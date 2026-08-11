"""Tests for BoundedLRUCache (mp_lru_cache) — capacity, LRU eviction, thread safety,
and correct wiring in app.py (_MP2_ENGINE_CACHE / _MP2_RUN_CACHE).
"""
import re
import threading
import pathlib
import pytest

# Import the production class directly — no Flask app startup needed.
from mp_lru_cache import BoundedLRUCache


# ---------------------------------------------------------------------------
# Basic get / set / contains / len
# ---------------------------------------------------------------------------

def test_basic_set_and_get():
    c = BoundedLRUCache(10)
    c["a"] = 1
    assert c.get("a") == 1
    assert c["a"] == 1


def test_missing_key_returns_default():
    c = BoundedLRUCache(10)
    assert c.get("nope") is None
    assert c.get("nope", 42) == 42


def test_contains():
    c = BoundedLRUCache(10)
    c["x"] = "hello"
    assert "x" in c
    assert "y" not in c


def test_len():
    c = BoundedLRUCache(10)
    assert len(c) == 0
    c["a"] = 1
    c["b"] = 2
    assert len(c) == 2


def test_maxsize_property():
    c = BoundedLRUCache(17)
    assert c.maxsize == 17


def test_invalid_maxsize_raises():
    with pytest.raises(ValueError):
        BoundedLRUCache(0)


def test_clear_empties_cache():
    c = BoundedLRUCache(10)
    c["a"] = 1
    c["b"] = 2
    c.clear()
    assert len(c) == 0
    assert "a" not in c
    assert c.get("b") is None


# ---------------------------------------------------------------------------
# Capacity enforcement
# ---------------------------------------------------------------------------

def test_does_not_exceed_maxsize():
    cap = 5
    c = BoundedLRUCache(cap)
    for i in range(cap + 10):
        c[i] = i * 10
    assert len(c) == cap


def test_evicts_oldest_entry_first():
    """Without any reads the first-written key is the LRU and evicted first."""
    c = BoundedLRUCache(3)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    # 4th entry evicts "a"
    c["d"] = 4
    assert "a" not in c
    assert "b" in c
    assert "c" in c
    assert "d" in c


def test_overwrite_same_key_does_not_grow():
    c = BoundedLRUCache(3)
    for v in range(10):
        c["same"] = v
    assert len(c) == 1
    assert c["same"] == 9


# ---------------------------------------------------------------------------
# LRU promotion
# ---------------------------------------------------------------------------

def test_getitem_promotes_to_mru():
    """A key accessed via [] should survive the next eviction."""
    c = BoundedLRUCache(3)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    _ = c["a"]          # promotes "a" → MRU; "b" becomes LRU
    c["d"] = 4
    assert "b" not in c
    assert "a" in c
    assert "c" in c
    assert "d" in c


def test_get_promotes_to_mru():
    """A key accessed via .get() should also be protected from the next eviction."""
    c = BoundedLRUCache(3)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    c.get("a")          # promotes "a"; "b" becomes LRU
    c["d"] = 4
    assert "b" not in c
    assert "a" in c


def test_overwrite_promotes_to_mru():
    """Re-writing an existing key should move it to MRU."""
    c = BoundedLRUCache(3)
    c["a"] = 1
    c["b"] = 2
    c["c"] = 3
    c["a"] = 99         # re-write → MRU; "b" is now LRU
    c["d"] = 4
    assert "b" not in c
    assert c["a"] == 99


# ---------------------------------------------------------------------------
# Atomic get — no TOCTOU window
# ---------------------------------------------------------------------------

def test_get_is_atomic_under_eviction_pressure():
    """Using .get() must never raise even when concurrent writers cause eviction.

    This is the production pattern for _mp2_load_run: a single .get() call
    (not `if key in cache: return cache[key]`) must always return a value or
    None — never KeyError.
    """
    cap = 3
    c = BoundedLRUCache(cap)
    errors = []

    stop = threading.Event()

    def evict_continuously():
        i = 0
        while not stop.is_set():
            c[f"evict-{i}"] = i
            i += 1

    def read_continuously():
        for _ in range(500):
            try:
                c.get("target")   # must never raise
            except Exception as exc:
                errors.append(exc)

    c["target"] = "value"
    evictor = threading.Thread(target=evict_continuously, daemon=True)
    reader  = threading.Thread(target=read_continuously)
    evictor.start()
    reader.start()
    reader.join()
    stop.set()
    evictor.join(timeout=1)

    assert not errors, f"get() raised under eviction pressure: {errors}"


# ---------------------------------------------------------------------------
# Thread safety — concurrent writers stay within cap
# ---------------------------------------------------------------------------

def test_concurrent_writes_stay_within_cap():
    cap = 10
    c = BoundedLRUCache(cap)
    violations = []

    def writer(start):
        for i in range(start, start + 80):
            c[i] = i
            current = len(c)
            if current > cap:
                violations.append(current)

    threads = [threading.Thread(target=writer, args=(t * 80,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not violations, f"cap exceeded: {violations}"
    assert len(c) <= cap


def test_concurrent_reads_and_writes_no_exception():
    c = BoundedLRUCache(20)
    for i in range(20):
        c[i] = i

    errors = []

    def reader():
        try:
            for i in range(300):
                c.get(i % 30)
        except Exception as exc:
            errors.append(("reader", exc))

    def writer():
        try:
            for i in range(300):
                c[i % 40] = i
        except Exception as exc:
            errors.append(("writer", exc))

    threads = (
        [threading.Thread(target=reader) for _ in range(4)]
        + [threading.Thread(target=writer) for _ in range(4)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions in concurrent access: {errors}"


# ---------------------------------------------------------------------------
# Production wiring — correct maxsizes configured in app.py
# ---------------------------------------------------------------------------

APP_SRC = pathlib.Path(__file__).parent.parent.joinpath("app.py").read_text()


def test_engine_cache_maxsize_is_50():
    m = re.search(r"_MP2_ENGINE_CACHE\s*(?::\s*\S+)?\s*=\s*_BoundedCache\(maxsize=(\d+)\)", APP_SRC)
    assert m is not None, "_MP2_ENGINE_CACHE = _BoundedCache(maxsize=...) not found in app.py"
    assert int(m.group(1)) == 50


def test_run_cache_maxsize_is_100():
    m = re.search(r"_MP2_RUN_CACHE\s*(?::\s*\S+)?\s*=\s*_BoundedCache\(maxsize=(\d+)\)", APP_SRC)
    assert m is not None, "_MP2_RUN_CACHE = _BoundedCache(maxsize=...) not found in app.py"
    assert int(m.group(1)) == 100


def test_mp2_load_run_uses_single_get():
    """_mp2_load_run must use a single .get() call, not a membership-test + index lookup."""
    # Find _mp2_load_run body in app.py source
    m = re.search(
        r"def _mp2_load_run.*?(?=\ndef |\Z)",
        APP_SRC,
        re.DOTALL,
    )
    assert m, "_mp2_load_run not found"
    body = m.group(0)
    # Must NOT contain the racy two-step pattern
    assert "if run_id in _MP2_RUN_CACHE" not in body, (
        "_mp2_load_run still uses racy `if run_id in cache: return cache[run_id]` pattern"
    )
    # Must use a single .get() call
    assert "_MP2_RUN_CACHE.get(run_id)" in body, (
        "_mp2_load_run must use _MP2_RUN_CACHE.get(run_id) for atomic access"
    )
