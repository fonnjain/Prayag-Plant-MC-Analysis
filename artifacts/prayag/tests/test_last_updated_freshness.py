"""Regression tests for the "Last Updated" period and per-plant data freshness.

"Last Updated" is a period-selection convenience that resolves PER PLANT: every
plant lands on its OWN most recent day with real daily production data (skipping
empty in-progress days), so each active plant shows real figures with its own
date — a laggard plant is never blocked or hidden behind the overall-max day, and
a plant with genuinely no daily data simply doesn't appear (nothing fabricated).
Resolution uses daily data ONLY. The completeness panel echoes the same per-plant
freshness ("Pipe to 04-Jun, PTMT to 12-Jun…").

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


def test_last_updated_resolves_per_plant_latest_day():
    """'Last Updated' resolves PER PLANT: each plant keeps its OWN most recent day
    even when plants log at different latencies, so a laggard plant (PIPE 04-Jun)
    still shows real figures and is never hidden behind the overall-max day."""
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
        # Per-plant: PTMT keeps 12-Jun and PIPE keeps its own 04-Jun row.
        assert {r.date for r in d["rows"]} == {"2026-06-12", "2026-06-04"}, [
            r.date for r in d["rows"]
        ]
        assert d["period_label"] == "Last updated", d["period_label"]
        print("PASS: Last Updated resolves per-plant (PTMT 12-Jun, PIPE 04-Jun)")
    finally:
        _restore(saved)


def test_last_updated_reads_plants_newest_months_beyond_window():
    """A plant whose latest real day is OLDER than the fixed recent window must
    still appear: get_data unions each daily plant's newest TWO available months
    from DAILY_SOURCES, so a plant resolves to its own freshest real day even when
    its newest configured month is an empty in-progress template. Without this the
    plant silently drops off "Last updated" (the original TANK/GARDEN-style bug)."""
    real_day = "2026-01-20"  # far outside the _today()=14-Jun recent window

    def daily(months):
        rows = []
        # Newest configured month (2026-06) is an empty in-progress template;
        # the real data lives in the second-newest month, beyond the window.
        if "2026-01" in months:
            rows.append(_daily("SYN", "SYN M/C - 1", "2026-01", 20, 90.0))
        return (rows, [{"file_id": "f", "record_count": len(rows), "title": "daily"}], [])

    saved = _install_stubs(daily)
    saved_ds = app.DAILY_SOURCES
    app.DAILY_SOURCES = {"SYN": {"files": {"2026-06": "emptyid", "2026-01": "dataid"}}}
    try:
        d = app.get_data({"period": "last_updated"})
        # The fix pulls 2026-01 into the read set, so SYN resolves to its real day.
        assert {r.date for r in d["rows"]} == {real_day}, [r.date for r in d["rows"]]
        print("PASS: last_updated reads each plant's newest months beyond the window")
    finally:
        app.DAILY_SOURCES = saved_ds
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
    test_last_updated_resolves_per_plant_latest_day()
    test_per_plant_freshness_sorted_desc_and_formatted()
    test_freshness_empty_on_daily_read_outage()
    print("\nAll Last-Updated / freshness regression tests passed.")
