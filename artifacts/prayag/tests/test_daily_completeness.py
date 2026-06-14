"""Regression tests for sub-monthly daily completeness counting (Tier 1).

Covers the fix where a plant that has a daily workbook but NO monthly-grid
roster (e.g. PTMT/TANK) reported "0/0 machines" in a daily view. In daily grain
the daily file is the only roster we hold, so its reporting machines must be
counted as both expected and present — honest coverage, never 0/0. Rostered
plants must keep matching against the master roster as before.

Run: cd artifacts/prayag && python3 -m tests.test_daily_completeness
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import Record
from confirm import build_masters, tier1_completeness


def _daily(plant: str, machine: str, ym: str, day: int) -> Record:
    return Record(
        grain="daily",
        plant=plant,
        segment=plant.title(),
        machine=machine,
        period=ym,
        date=f"{ym}-{day:02d}",
        total_count=100.0,
        reject_count=0.0,
        actual_hours=8.0,
        ideal_hours=8.0,
    )


def _monthly(plant: str, machine: str, ym: str) -> Record:
    return Record(
        grain="monthly",
        plant=plant,
        segment=plant.title(),
        machine=machine,
        period=ym,
        total_count=1000.0,
        reject_count=0.0,
        actual_hours=160.0,
        ideal_hours=200.0,
    )


def test_no_roster_daily_plant_counts_reporting_machines():
    """PTMT has a daily file but no monthly grid → its reporting machines are
    counted as both present and expected (not 0/0)."""
    # Master roster built only from rostered plants (no PTMT).
    master_rows = [_monthly("PIPE", "PIPE M/C - 1", "2026-06")]
    masters = build_masters(master_rows)
    assert "PTMT" not in masters["machines"]

    period_rows = [
        _daily("PTMT", "PTMT 110-1", "2026-06", 8),
        _daily("PTMT", "PTMT 110-2", "2026-06", 8),
        _daily("PTMT", "PTMT 125-1", "2026-06", 9),
    ]
    _issues, score = tier1_completeness(
        period_months=["2026-06"],
        period_rows=period_rows,
        source_reports=[],
        masters=masters,
        fy_months_with_data=["2026-06"],
        daily_used=True,
        as_of=datetime.date(2026, 6, 10),
    )
    present, expected = score["machines"]
    assert (present, expected) == (3, 3), score["machines"]
    print("PASS: no-roster daily plant counts reporting machines (3/3, not 0/0)")


def test_empty_daily_window_is_zero_zero_not_a_crash():
    """An empty daily window (e.g. 'Yesterday' with no rows) yields 0/0 cleanly,
    not a crash and not fabricated coverage."""
    master_rows = [_monthly("PIPE", "PIPE M/C - 1", "2026-06")]
    masters = build_masters(master_rows)
    _issues, score = tier1_completeness(
        period_months=["2026-06"],
        period_rows=[],
        source_reports=[],
        masters=masters,
        fy_months_with_data=["2026-06"],
        daily_used=True,
        as_of=datetime.date(2026, 6, 14),
    )
    assert score["machines"] == (0, 0), score["machines"]
    print("PASS: empty daily window reports 0/0 cleanly")


def test_rostered_daily_plant_still_matches_master():
    """A rostered plant (PIPE) in daily grain matches its master roster and a
    machine with no run collapses to a single summary issue, not per-machine."""
    master_rows = [
        _monthly("PIPE", "PIPE M/C - 1", "2026-06"),
        _monthly("PIPE", "PIPE M/C - 2", "2026-06"),
        _monthly("PIPE", "PIPE M/C - 3", "2026-06"),
    ]
    masters = build_masters(master_rows)
    period_rows = [
        _daily("PIPE", "PIPE M/C - 1", "2026-06", 8),
        _daily("PIPE", "PIPE M/C - 2", "2026-06", 8),
    ]
    issues, score = tier1_completeness(
        period_months=["2026-06"],
        period_rows=period_rows,
        source_reports=[],
        masters=masters,
        fy_months_with_data=["2026-06"],
        daily_used=True,
        as_of=datetime.date(2026, 6, 10),
    )
    assert score["machines"] == (2, 3), score["machines"]
    no_run = [i for i in issues if "no run in this window" in i["message"]]
    assert len(no_run) == 1, [i["message"] for i in issues]
    print("PASS: rostered daily plant matches roster, one 'no run' summary line")


if __name__ == "__main__":
    test_no_roster_daily_plant_counts_reporting_machines()
    test_empty_daily_window_is_zero_zero_not_a_crash()
    test_rostered_daily_plant_still_matches_master()
    print("\nAll daily completeness regression tests passed.")
