"""Tests for vanished-file discovery warning (Phase 2).

Validates the three states that must NOT be conflated:
  1. never had a file       → no warning, return [] (existing behaviour)
  2. genuinely zero output  → handled by EMPTY_SOURCES, no change
  3. had a discovered file, now unreadable → new vanished warning

All tests are offline — no real Google Sheets reads.

Run: cd artifacts/prayag && python3 -m pytest tests/test_vanished_discovery.py
"""
import os
import sys
import types
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
import sources


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_FID = "AbCdEfGhIjKlMnOpQrStUv"   # 22-char fake file ID


def _inject_vanished(plant, ym, fid=_FAKE_FID):
    """Force _discovery_state["vanished"] to contain one entry."""
    sheets._discovery_state["vanished"] = {f"{plant}:{ym}": fid}


def _clear_vanished():
    sheets._discovery_state["vanished"] = {}


# ---------------------------------------------------------------------------
# State 3 — had a discovered file, now unreadable: warning fires
# ---------------------------------------------------------------------------

class TestVanishedWarning:
    def setup_method(self):
        _inject_vanished("TANK", "2026-07", _FAKE_FID)

    def teardown_method(self):
        _clear_vanished()

    def test_get_vanished_file_id_returns_fid_when_vanished(self):
        fid = sheets._get_vanished_file_id("TANK", "2026-07")
        assert fid == _FAKE_FID, f"Expected {_FAKE_FID!r}, got {fid!r}"

    def test_get_vanished_file_id_returns_none_when_not_vanished(self):
        fid = sheets._get_vanished_file_id("TANK", "2026-05")
        assert fid is None, f"Expected None for a never-vanished month, got {fid!r}"

    def test_vanished_reports_returns_nonempty_list(self):
        result = sheets._vanished_reports("TANK", "2026-07", _FAKE_FID)
        assert result, "Expected at least one report tuple"

    def test_vanished_reports_have_warning_field(self):
        result = sheets._vanished_reports("TANK", "2026-07", _FAKE_FID)
        for recs, report in result:
            assert "warning" in report, "vanished report must have 'warning' key"
            w = report["warning"]
            assert "TANK" in w and "2026-07" in w, (
                f"Warning should mention plant and month: {w!r}")
            assert _FAKE_FID[:20] in w, (
                f"Warning should include file ID prefix: {w!r}")
            assert "no longer readable" in w or "deleted" in w, (
                f"Warning should explain the problem: {w!r}")

    def test_vanished_reports_return_empty_records(self):
        result = sheets._vanished_reports("TANK", "2026-07", _FAKE_FID)
        for recs, report in result:
            assert recs == [], f"Records must be [] for a vanished month, got {recs!r}"

    def test_vanished_reports_set_vanished_source_flag(self):
        result = sheets._vanished_reports("TANK", "2026-07", _FAKE_FID)
        for _, report in result:
            assert report.get("vanished_source") is True

    def test_load_daily_returns_vanished_report_when_file_missing(self):
        """_load_daily must return a warning report (not []) when the month is vanished."""
        _inject_vanished("TANK", "2026-07", _FAKE_FID)
        # TANK 2026-07 has no pinned file — _load_daily should hit the vanished path.
        token = "fake-token"
        result = sheets._load_daily("TANK", "2026-07", token)
        assert result, (
            "_load_daily returned [] for a vanished month — warning was swallowed")
        for recs, report in result:
            assert recs == [], "Records must be empty for a vanished month"
            assert report.get("warning"), "Warning must be set in report dict"
            assert "no longer readable" in report["warning"] or "deleted" in report["warning"]

    def test_warning_text_matches_spec(self):
        """Exact text must match the spec format."""
        result = sheets._vanished_reports("TANK", "2026-07", _FAKE_FID)
        _, report = result[0]
        w = report["warning"]
        # Spec text: "TANK 2026-07: a previously discovered source file is no
        # longer readable (was AbCdEfGhIjKlMnOpQrSt…). This month may show no
        # data. Source may have been deleted, moved, or had access revoked."
        assert w.startswith("TANK 2026-07:"), f"Wrong prefix: {w!r}"
        assert "previously discovered source file" in w
        assert "no longer readable" in w
        assert "This month may show no data" in w
        assert "deleted, moved, or had access revoked" in w


# ---------------------------------------------------------------------------
# State 1 — never had a file: no warning, existing behaviour preserved
# ---------------------------------------------------------------------------

class TestNeverHadAFile:
    def setup_method(self):
        # Vanished map is empty — no month was ever discovered
        sheets._discovery_state["vanished"] = {}

    def teardown_method(self):
        _clear_vanished()

    def test_get_vanished_returns_none_for_unknown_month(self):
        # A month that was never in sources.py and never discovered
        fid = sheets._get_vanished_file_id("TANK", "2026-01")
        assert fid is None

    def test_load_daily_returns_empty_list_when_no_file_ever(self):
        # TANK 2026-01 has no file and is not vanished — must return []
        result = sheets._load_daily("TANK", "2026-01", "fake-token")
        assert result == [], (
            f"_load_daily should return [] for a month with no file ever, got {result!r}")


# ---------------------------------------------------------------------------
# State 2 — genuinely zero output (EMPTY_SOURCES): existing behaviour unchanged
# ---------------------------------------------------------------------------

class TestGenuinelyEmpty:
    """EMPTY_SOURCES handling must not be affected by the vanished-file path."""

    def test_empty_sources_is_unaffected(self):
        # PIPE 2025-08 is in EMPTY_SOURCES — must still return the empty-source report
        result = sheets._load_daily("PIPE", "2025-08", "fake-token")
        assert result, "EMPTY_SOURCES entry must return a non-empty list of report tuples"
        for recs, report in result:
            assert recs == [], "Records for EMPTY_SOURCES must be []"
            assert report.get("empty_source") is True, (
                "empty_source flag must be True for EMPTY_SOURCES months")
            assert not report.get("vanished_source"), (
                "vanished_source must NOT be set for a genuinely-empty month")


# ---------------------------------------------------------------------------
# Genuinely zero production (KH April: registered file, zero rows) — unchanged
# ---------------------------------------------------------------------------

class TestGenuinelyZeroProduction:
    """A registered file with zero production rows must NOT trigger the vanished warning.

    KH April is registered (2026-04 in TANK.files) and its workbook has zero rows.
    The vanished path is only for months with NO file in sources.py.
    """

    def setup_method(self):
        # Simulate discovery vanished map being empty (no vanished months)
        sheets._discovery_state["vanished"] = {}

    def teardown_method(self):
        _clear_vanished()

    def test_registered_file_never_hits_vanished_path(self):
        # TANK 2026-04 IS in sources.py — _load_daily must never see file_id=None
        fid = sources.DAILY_SOURCES.get("TANK", {}).get("files", {}).get("2026-04")
        assert fid, "TANK 2026-04 must be registered in sources.py for this test to be valid"
        # _get_vanished_file_id must return None for a pinned month even if
        # it is somehow in the vanished map (e.g. from a stale entry).
        sheets._discovery_state["vanished"] = {"TANK:2026-04": "some-old-fid"}
        result = sheets._get_vanished_file_id("TANK", "2026-04")
        assert result is None, (
            "_get_vanished_file_id must return None for a pinned month — "
            f"got {result!r}")
        _clear_vanished()


# ---------------------------------------------------------------------------
# Discovery finds new months → they are not vanished
# ---------------------------------------------------------------------------

class TestDiscoveryAddedNotVanished:
    """Months added by discovery in the current scan must NOT appear in vanished."""

    def teardown_method(self):
        _clear_vanished()

    def test_freshly_added_month_not_vanished(self):
        # Simulate: discovery added TANK 2026-07 in this scan
        # → it should NOT appear in vanished (it was found)
        sheets._discovery_state["vanished"] = {}  # empty because it was found
        result = sheets._get_vanished_file_id("TANK", "2026-07")
        assert result is None, (
            "A month found in the current scan must not be flagged as vanished")


# ---------------------------------------------------------------------------
# August entries (current-month partial data) must not be flagged vanished
# ---------------------------------------------------------------------------

class TestCurrentMonthNotVanished:
    """Current-month partial data from discovery must not be flagged as vanished."""

    def setup_method(self):
        # discovery_seen has August entries but vanished is empty (they were found)
        sheets._discovery_state["vanished"] = {}

    def teardown_method(self):
        _clear_vanished()

    def test_current_month_august_not_vanished(self):
        for plant in ("TANK_VN", "TANK_WB"):
            result = sheets._get_vanished_file_id(plant, "2026-08")
            assert result is None, (
                f"{plant} 2026-08 current-month partial data must not be flagged vanished")


# ---------------------------------------------------------------------------
# discovery_status exposes vanished map
# ---------------------------------------------------------------------------

def test_discovery_status_includes_vanished_key():
    _inject_vanished("TANK", "2026-07", _FAKE_FID)
    status = sheets.discovery_status()
    assert "vanished" in status, "discovery_status must include 'vanished' key"
    assert "TANK:2026-07" in status["vanished"]
    _clear_vanished()


def test_discovery_status_vanished_empty_when_no_vanished():
    sheets._discovery_state["vanished"] = {}
    status = sheets.discovery_status()
    assert status["vanished"] == {}


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = []
    for cls in [TestVanishedWarning, TestNeverHadAFile, TestGenuinelyEmpty,
                TestGenuinelyZeroProduction, TestDiscoveryAddedNotVanished,
                TestCurrentMonthNotVanished]:
        obj = cls()
        for name in [n for n in dir(obj) if n.startswith("test_")]:
            tests.append((name, getattr(obj, name), obj))

    # Module-level tests
    for name, fn in [(k, v) for k, v in globals().items()
                     if k.startswith("test_") and callable(v)]:
        tests.append((name, fn, None))

    failures = []
    for name, fn, inst in tests:
        if inst and hasattr(inst, "setup_method"):
            inst.setup_method()
        try:
            fn()
            print(f"PASS: {name}")
        except Exception as e:
            failures.append((name, e))
            print(f"FAIL: {name}: {e}")
        finally:
            if inst and hasattr(inst, "teardown_method"):
                inst.teardown_method()

    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
