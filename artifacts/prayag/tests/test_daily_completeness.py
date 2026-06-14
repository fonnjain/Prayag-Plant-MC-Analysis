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


def test_ptmt_measured_against_authoritative_roster():
    """PTMT has no monthly grid, but its authoritative 55-machine register is
    merged into the master roster by build_masters. So completeness is held to
    all 55 — three reporting machines score 3/55, and the 52 non-reporters are
    surfaced (named) in a single summary line, not silently treated as complete.
    """
    master_rows = [_monthly("PIPE", "PIPE M/C - 1", "2026-06")]
    masters = build_masters(master_rows)
    assert "PTMT" in masters["machines"]
    assert len(masters["machines"]["PTMT"]) == 55, len(masters["machines"]["PTMT"])

    period_rows = [
        _daily("PTMT", "PTMT 110-1", "2026-06", 8),
        _daily("PTMT", "PTMT 110-2", "2026-06", 8),
        _daily("PTMT", "PTMT 125-1", "2026-06", 9),
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
    present, expected = score["machines"]
    assert (present, expected) == (3, 55), score["machines"]
    no_run = [i for i in issues if "no run in this window" in i["message"]]
    assert len(no_run) == 1, [i["message"] for i in issues]
    print("PASS: PTMT measured against authoritative 55-machine roster (3/55)")


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


def _tank_item(item: str, ym: str, day: int, pcs: float) -> Record:
    """A TANK row: per-item production, NO machine identity (machine="")."""
    return Record(
        grain="daily",
        plant="TANK",
        segment="Tanks",
        machine="",
        mould=item,
        unit="pcs",
        period=ym,
        date=f"{ym}-{day:02d}",
        total_count=pcs,
        reject_count=0.0,
        actual_hours=0.0,
        ideal_hours=0.0,
    )


def test_tank_no_fabricated_roster_gaps():
    """TANK logs per item with no machine/segment/mould roster. Tier-1 must NOT
    fabricate "appears in data but not in master roster" gaps for it — a plant
    whose source isn't per-machine simply scores plant-level 1/1 ("reporting")."""
    master_rows = [_monthly("PIPE", "PIPE M/C - 1", "2026-06")]
    masters = build_masters(master_rows)
    period_rows = [
        _tank_item("WT-3LL-05", "2026-06", 8, 120.0),
        _tank_item("WT-5LL-02", "2026-06", 9, 80.0),
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
    fabricated = [
        i for i in issues
        if "not in the master roster" in i["message"] and i.get("plant") == "TANK"
    ]
    assert not fabricated, [i["message"] for i in fabricated]
    assert score["machines"] == (1, 1), score["machines"]
    print("PASS: TANK (non-per-machine) gets no fabricated roster gaps, scores 1/1")


def test_ptmt_excluded_from_monthly_scope():
    """PTMT is injected into the master roster, but it has no monthly grid — so in
    a monthly/FY view it must be OUT of scope, never shown as 0-of-55 missing."""
    master_rows = [_monthly("PIPE", "PIPE M/C - 1", "2026-06")]
    masters = build_masters(master_rows)
    assert "PTMT" in masters["machines"]  # injected for daily completeness

    issues, score = tier1_completeness(
        period_months=["2026-06"],
        period_rows=[_monthly("PIPE", "PIPE M/C - 1", "2026-06")],
        source_reports=[],
        masters=masters,
        fy_months_with_data=["2026-06"],
        daily_used=False,
        as_of=datetime.date(2026, 7, 1),
    )
    ptmt_issues = [i for i in issues if i.get("plant") == "PTMT"]
    assert not ptmt_issues, [i["message"] for i in ptmt_issues]
    assert score["machines"] == (1, 1), score["machines"]
    print("PASS: PTMT excluded from monthly-grain scope (no 0/55 false gaps)")


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
    test_ptmt_measured_against_authoritative_roster()
    test_empty_daily_window_is_zero_zero_not_a_crash()
    test_tank_no_fabricated_roster_gaps()
    test_ptmt_excluded_from_monthly_scope()
    test_rostered_daily_plant_still_matches_master()
    print("\nAll daily completeness regression tests passed.")
