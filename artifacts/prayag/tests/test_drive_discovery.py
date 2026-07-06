"""Offline regression for Google Drive folder auto-discovery, NO network.

Discovery must (1) map real workbook filenames to YYYY-MM deterministically,
(2) ADD only months not already pinned while NEVER overwriting a pinned id,
(3) skip plants without a daily layout (e.g. CP), and (4) be safe to run
concurrently with request handlers iterating sources.DAILY_SOURCES — the
copy-on-write swap must never raise "dictionary changed size during iteration".

Drive token + folder listing are stubbed so the test is deterministic/offline.

Run: cd artifacts/prayag && python3 -m pytest tests/test_drive_discovery.py -q
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
import sources


def test_parse_month_from_title():
    cases = {
        "1. Pipe & Fitting Plant Date Sheet & Monthly Report - Apr ' 2026": "2026-04",
        "3. TANK Date Sheet & Monthly Report - July ' 2026": "2026-07",
        "2. CP Date Sheet & Monthly Report - Nov ' 2025": "2025-11",
        "3. GARDEN PIPE Date Sheet & Monthly Report - June ' 2026": "2026-06",
        "Sept \u2019 2025 report": "2025-09",          # curly apostrophe + full form
        "some file with no month or year": None,
        "": None,
    }
    for title, want in cases.items():
        got = sheets.parse_month_from_title(title)
        assert got == want, (title, got, want)
    print("PASS: parse_month_from_title maps real titles and rejects the rest")


def _install_stub(monkeypatch, folder_files):
    """Stub the Drive token + folder listing. ``folder_files`` maps a folder id
    to a list of {id,name,modifiedTime} file dicts."""
    monkeypatch.setattr(sheets, "_get_drive_token", lambda: "stub-token")
    monkeypatch.setattr(sheets, "is_demo_mode", lambda: False)
    monkeypatch.setattr(
        sheets, "_list_drive_folder", lambda fid, tok: folder_files.get(fid, [])
    )


def test_adds_new_month_without_overwriting_pins(monkeypatch):
    # Pick a real daily-layout plant and its first folder.
    plant = "TANK"
    assert plant in sheets._DAILY_LAYOUTS
    folder = sources.DAILY_SOURCES[plant]["folder_ids"][0]
    before = dict(sources.DAILY_SOURCES[plant]["files"])
    assert before, "expected TANK to have at least one pinned month"
    existing_ym, existing_id = sorted(before.items())[0]

    # The folder reports the already-pinned month (with a DIFFERENT id — must be
    # ignored) plus a brand-new month that must be added.
    folder_files = {
        folder: [
            {"id": "DECOY_SHOULD_BE_IGNORED",
             "name": f"1. TANK Date Sheet & Monthly Report - "
                     f"{_month_word(existing_ym)} ' {existing_ym[:4]}",
             "modifiedTime": "2099-01-01T00:00:00Z"},
            {"id": "NEW_FILE_ID_2099_12",
             "name": "9. TANK Date Sheet & Monthly Report - Dec ' 2099",
             "modifiedTime": "2099-12-01T00:00:00Z"},
        ]
    }
    _install_stub(monkeypatch, folder_files)

    try:
        added = sheets.ensure_daily_discovery(force=True)
        files_after = sources.DAILY_SOURCES[plant]["files"]
        # Pinned id is untouched despite the decoy claiming the same month.
        assert files_after[existing_ym] == existing_id
        # The new month was added.
        assert files_after.get("2099-12") == "NEW_FILE_ID_2099_12"
        assert "2099-12" in (added.get(plant) or [])
    finally:
        # Restore the module-level map so other tests see the real pins.
        sources.DAILY_SOURCES[plant]["files"] = before
        sheets._discovery_state["last_scan_ts"] = 0.0
        sheets._discovery_state["added"] = {}
    print("PASS: discovery adds a new month and never overwrites a pinned id")


def test_skips_plants_without_layout(monkeypatch):
    # CP has folder_ids but no daily layout -> must NOT be auto-populated.
    assert "CP" in sources.DAILY_SOURCES
    assert "CP" not in sheets._DAILY_LAYOUTS
    folder = sources.DAILY_SOURCES["CP"]["folder_ids"][0]
    folder_files = {
        folder: [
            {"id": "CP_FILE", "name": "1. CP Date Sheet & Monthly Report - Oct ' 2025",
             "modifiedTime": "2025-10-01T00:00:00Z"},
        ]
    }
    _install_stub(monkeypatch, folder_files)
    before = dict(sources.DAILY_SOURCES["CP"].get("files") or {})
    try:
        sheets.ensure_daily_discovery(force=True)
        assert (sources.DAILY_SOURCES["CP"].get("files") or {}) == before, \
            "CP (no daily layout) must not be auto-populated"
    finally:
        sheets._discovery_state["last_scan_ts"] = 0.0
        sheets._discovery_state["added"] = {}
    print("PASS: discovery skips plants without a daily layout (CP)")


def test_concurrent_discovery_and_iteration_is_safe(monkeypatch):
    """Copy-on-write swap must not raise while another thread iterates the map."""
    plant = "GARDEN"
    folder = sources.DAILY_SOURCES[plant]["folder_ids"][0]
    before = dict(sources.DAILY_SOURCES[plant]["files"])
    # A rotating set of new months so each forced scan actually swaps the dict.
    folder_files = {
        folder: [
            {"id": f"G_{i}", "name": f"GARDEN PIPE Report - {m} ' 2099",
             "modifiedTime": f"2099-{i:02d}-01T00:00:00Z"}
            for i, m in enumerate(
                ["Jan", "Feb", "Mar", "Apr", "May", "Jun"], start=1)
        ]
    }
    _install_stub(monkeypatch, folder_files)

    stop = threading.Event()
    errors: list = []

    def _reader():
        while not stop.is_set():
            try:
                for _p, cfg in sources.DAILY_SOURCES.items():
                    for _ym in (cfg.get("files") or {}):
                        pass
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
                return

    def _writer():
        for _ in range(200):
            sheets._discovery_state["last_scan_ts"] = 0.0  # bypass TTL
            try:
                sheets.ensure_daily_discovery(force=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))
                return

    try:
        readers = [threading.Thread(target=_reader) for _ in range(4)]
        for r in readers:
            r.start()
        w = threading.Thread(target=_writer)
        w.start()
        w.join()
        stop.set()
        for r in readers:
            r.join()
        assert not errors, errors
    finally:
        sources.DAILY_SOURCES[plant]["files"] = before
        sheets._discovery_state["last_scan_ts"] = 0.0
        sheets._discovery_state["added"] = {}
    print("PASS: concurrent discovery + iteration raises no dict-mutation error")


def _month_word(ym: str) -> str:
    return ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(ym[5:7])]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
