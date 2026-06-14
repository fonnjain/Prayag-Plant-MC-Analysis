"""Regression tests for the "Last Updated" period and per-plant data freshness.

"Last Updated" is a period-selection convenience: it resolves to the most recent
date that actually has daily production data (a single-day snapshot), so the user
lands on real figures instead of an empty in-progress day. Resolution uses daily
data ONLY. The completeness panel then shows per-plant freshness ("Pipe to
04-Jun, PTMT to 12-Jun…") using the overall max as the resolved date, without
blocking on laggard plants.

Run: cd artifacts/prayag && python3 -m tests.test_last_updated_freshness
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from metrics import Record
from sheets import SheetReadError


def _daily(plant, machine, ym, day, out):
    return Record(
        grain="daily", plant=plant, segment=plant.title(), machine=machine,
        period=ym, date=f"{ym}-{day:02d}", total_count=out, reject_count=0.0,
        actual_hours=8.0, ideal_hours=8.0,
    )


def _install_stubs(monkey_daily):
    saved = {
        "gd": app.get_daily_records, "gr": app.get_records,
        "ab": app._apply_baselines, "mw": app.months_with_data,
        "today": app._today, "eff": app.store.effective,
        "acks": app.store.acks_for, "key": os.environ.get("ANTHROPIC_API_KEY"),
    }
    app.get_daily_records = monkey_daily
    app.get_records = lambda months: ([], [], [])
    app._apply_baselines = lambda rows: None
    app.months_with_data = lambda: ["2026-05", "2026-06"]
    app._today = lambda: datetime.date(2026, 6, 14)
    app.store.effective = lambda *a, **k: None
    app.store.acks_for = lambda *a, **k: {}
    os.environ["ANTHROPIC_API_KEY"] = ""
    return saved


def _restore(saved):
    app.get_daily_records = saved["gd"]
    app.get_records = saved["gr"]
    app._apply_baselines = saved["ab"]
    app.months_with_data = saved["mw"]
    app._today = saved["today"]
    app.store.effective = saved["eff"]
    app.store.acks_for = saved["acks"]
    if saved["key"] is None:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = saved["key"]


def test_last_updated_resolves_to_overall_max_date():
    """'Last Updated' narrows to the single latest date any plant reported, even
    when plants log at different latencies — and skips empty trailing days."""
    def daily(months):
        rows = [
            _daily("PTMT", "PTMT 80-1", "2026-06", 12, 50.0),   # freshest: 12-Jun
            _daily("PIPE", "PIPE M/C - 1", "2026-06", 4, 70.0),  # laggard: 04-Jun
        ]
        return (rows, [{"file_id": "f", "record_count": 2, "title": "daily"}], [])

    saved = _install_stubs(daily)
    try:
        d = app.get_data({"period": "last_updated"})
        assert d["daily_used"] is True, d["daily_used"]
        # Narrowed to the single overall-max day (12-Jun) — only PTMT's row.
        assert {r.date for r in d["rows"]} == {"2026-06-12"}, [r.date for r in d["rows"]]
        assert d["period_label"] == "Last updated: 12-06-2026", d["period_label"]
        print("PASS: Last Updated resolves to the overall max date (12-06-2026)")
    finally:
        _restore(saved)


def test_per_plant_freshness_sorted_desc_and_formatted():
    """The completeness panel gets per-plant freshness: each plant's own latest
    date, dd-mm-yyyy, sorted newest first — laggards visible, never blocking."""
    def daily(months):
        rows = [
            _daily("PTMT", "PTMT 80-1", "2026-06", 12, 50.0),
            _daily("PIPE", "PIPE M/C - 1", "2026-06", 4, 70.0),
            _daily("PIPE", "PIPE M/C - 1", "2026-06", 1, 60.0),   # older PIPE row
        ]
        return (rows, [{"file_id": "f", "record_count": 3, "title": "daily"}], [])

    saved = _install_stubs(daily)
    try:
        d = app.get_data({"period": "last_updated"})
        fresh = d["confirmation"]["freshness"]
        plants = [(f["plant"], f["disp"]) for f in fresh]
        # Newest first; PIPE collapses to its own max (04-Jun), not 01-Jun.
        assert plants == [("PTMT", "12-06-2026"), ("PIPE", "04-06-2026")], plants
        assert all(f.get("name") for f in fresh), fresh
        print("PASS: per-plant freshness sorted desc, dd-mm-yyyy, per-plant max")
    finally:
        _restore(saved)


def test_freshness_empty_on_daily_read_outage():
    """If the daily read fails entirely, freshness is empty (the panel hides it) —
    no fabricated dates, consistent with the daily-only guardrail."""
    def daily(months):
        raise SheetReadError("Google Sheets API error (429).")

    saved = _install_stubs(daily)
    try:
        d = app.get_data({"period": "last_updated"})
        assert d["confirmation"]["freshness"] == [], d["confirmation"]["freshness"]
        print("PASS: freshness empty on a total daily read outage")
    finally:
        _restore(saved)


if __name__ == "__main__":
    test_last_updated_resolves_to_overall_max_date()
    test_per_plant_freshness_sorted_desc_and_formatted()
    test_freshness_empty_on_daily_read_outage()
    print("\nAll Last-Updated / freshness regression tests passed.")
