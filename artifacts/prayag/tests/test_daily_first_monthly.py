"""Regression tests for DAILY-FIRST monthly / FY headline figures.

The daily files are the source of truth for EVERY period. A monthly or FY view
must compute its headline totals by summing the authoritative daily tabs, not the
monthly summary grid — the grid is only a reconciliation reference and is never
allowed to reduce the daily figures ("never reconcile down"). The grid is the
headline ONLY for a month that has no daily workbook at all, and a total daily
read outage degrades to the grid with an explicit "it undercounts" banner.

Run: cd artifacts/prayag && python3 -m tests.test_daily_first_monthly
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


def _grid(plant, machine, ym, out):
    return Record(
        grain="monthly", plant=plant, segment=plant.title(), machine=machine,
        period=ym, total_count=out, reject_count=0.0,
        actual_hours=160.0, ideal_hours=200.0,
    )


def _install_stubs(monkey_daily, monkey_grid):
    """Swap the live sheet readers + side-channels so get_data is hermetic."""
    saved = {
        "gd": app.get_daily_records, "gr": app.get_records,
        "ab": app._apply_baselines, "mw": app.months_with_data,
        "today": app._today, "eff": app.store.effective,
        "acks": app.store.acks_for, "key": os.environ.get("ANTHROPIC_API_KEY"),
    }
    app.get_daily_records = monkey_daily
    app.get_records = monkey_grid
    app._apply_baselines = lambda rows: None
    app.months_with_data = lambda: ["2026-05"]
    app._today = lambda: datetime.date(2026, 6, 14)
    app.store.effective = lambda *a, **k: None
    app.store.acks_for = lambda *a, **k: {}
    os.environ["ANTHROPIC_API_KEY"] = ""   # no fuzzy matcher / prose network
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


def test_monthly_view_sums_daily_not_grid():
    """A month view (May) publishes the DAILY sum, not the lower grid total."""
    def daily(months):
        return ([_daily("PIPE", "PIPE M/C - 1", "2026-05", 15, 999.0)],
                [{"file_id": "f", "record_count": 1, "title": "Pipe daily"}],
                ["Pipe & Fitting: daily rows sum to 999 but the monthly grid is "
                 "111 (8.9% off)"])

    def grid(months):
        # Grid is deliberately LOWER — if the view used it, the total would be 111.
        return ([_grid("PIPE", "PIPE M/C - 1", "2026-05", 111.0)], [], [])

    saved = _install_stubs(daily, grid)
    try:
        d = app.get_data({"period": "5"})
        assert d["daily_used"] is True, d["daily_used"]
        total = sum(r.total_count for r in d["rows"])
        assert total == 999.0, total                     # daily, never the 111 grid
        assert "summed from the daily" in d["grain_banner"], d["grain_banner"]
        # The daily-vs-grid gap is a NON-BLOCKING note: it must NOT gate the view
        # to "error" (which would hide the figures behind "needs review").
        assert d["confirmation"]["status"] != "error", d["confirmation"]["status"]
        print("PASS: monthly view sums daily (999), grid (111) stays a recon note")
    finally:
        _restore(saved)


def test_total_daily_outage_falls_back_to_grid_labelled():
    """If the daily read fails ENTIRELY, the view degrades to the grid but says so
    explicitly (it undercounts) — it never silently presents grid as daily."""
    def daily(months):
        raise SheetReadError("Google Sheets API error (429).")

    def grid(months):
        return ([_grid("PIPE", "PIPE M/C - 1", "2026-05", 111.0)], [], [])

    saved = _install_stubs(daily, grid)
    try:
        d = app.get_data({"period": "5"})
        assert d["daily_used"] is False, d["daily_used"]
        total = sum(r.total_count for r in d["rows"])
        assert total == 111.0, total
        assert "monthly summary sheet instead" in d["grain_banner"], d["grain_banner"]
        print("PASS: total daily outage degrades to grid with an explicit banner")
    finally:
        _restore(saved)


if __name__ == "__main__":
    test_monthly_view_sums_daily_not_grid()
    test_total_daily_outage_falls_back_to_grid_labelled()
    print("\nAll daily-first monthly/FY regression tests passed.")
