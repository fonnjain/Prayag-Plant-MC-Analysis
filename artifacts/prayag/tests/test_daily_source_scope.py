"""Regression coverage for narrowing a report to its physical source workbook."""
from __future__ import annotations

import os
import sys


PRAYAG_DIR = os.path.join(os.path.dirname(__file__), "..")
if PRAYAG_DIR not in sys.path:
    sys.path.insert(0, PRAYAG_DIR)

import sheets


def test_daily_records_can_limit_reads_to_requested_source_workbooks(monkeypatch):
    calls = []
    monkeypatch.setattr(sheets, "is_demo_mode", lambda: False)
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "test-token")
    monkeypatch.setattr(sheets, "_daily_plants", lambda: ["PIPE", "PTMT"])
    monkeypatch.setattr(
        sheets.sources,
        "DAILY_SOURCES",
        {
            "PIPE": {"files": {"2026-08": "pipe-file"}},
            "PTMT": {"files": {"2026-08": "ptmt-file"}},
        },
    )

    def fake_load(plant, ym, token):
        calls.append((plant, ym, token))
        return []

    monkeypatch.setattr(sheets, "_load_daily_cached", fake_load)

    records, reports, warnings = sheets.get_daily_records(
        ["2026-08"], source_plants={"PIPE"}
    )

    assert calls == [("PIPE", "2026-08", "test-token")]
    assert records == []
    assert reports == []
    assert warnings == []