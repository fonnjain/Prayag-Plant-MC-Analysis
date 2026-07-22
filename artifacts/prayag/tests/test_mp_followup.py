"""
Tests for mp_followup — normalisation, plan-to-date proration,
RAG classification, and each warning type.

All tests run offline without DB or network access.
"""
from __future__ import annotations
import sys
import os
import datetime

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mp_followup as fu


# ---------------------------------------------------------------------------
# norm_machine
# ---------------------------------------------------------------------------

class TestNormMachine:
    def test_plan_mc1(self):
        assert fu.norm_machine("M/C-1") == "MC1"

    def test_actual_pipe_mc1(self):
        assert fu.norm_machine("PIPE M/C - 1") == "MC1"

    def test_actual_pipe_mc11(self):
        assert fu.norm_machine("PIPE M/C - 11") == "MC11"

    def test_spaces_only(self):
        assert fu.norm_machine("M/C - 3") == "MC3"

    def test_mixed_case(self):
        assert fu.norm_machine("pipe m/c - 2") == "MC2"

    def test_already_norm(self):
        assert fu.norm_machine("MC5") == "MC5"

    def test_moulding_prefix(self):
        assert fu.norm_machine("Moulding MC-3") == "MC3"

    def test_plan_mc_matches_actual_pipe_mc(self):
        """The critical join: 'M/C-1' must equal 'PIPE M/C - 1' after normalisation."""
        assert fu.norm_machine("M/C-1") == fu.norm_machine("PIPE M/C - 1")

    def test_m9_plan_vs_actual(self):
        assert fu.norm_machine("M/C-9") == fu.norm_machine("PIPE M/C - 9")

    def test_two_digit(self):
        assert fu.norm_machine("M/C-12") == "MC12"


# ---------------------------------------------------------------------------
# rag_status
# ---------------------------------------------------------------------------

class TestRagStatus:
    def test_exactly_on_plan_green(self):
        assert fu.rag_status(100.0, 100.0) == "GREEN"

    def test_zero_actual_zero_plan_green(self):
        assert fu.rag_status(0.0, 0.0) == "GREEN"

    def test_unplanned_actual_red(self):
        assert fu.rag_status(50.0, 0.0) == "RED"

    def test_within_amber_green(self):
        assert fu.rag_status(92.0, 100.0) == "GREEN"   # 8% deviation

    def test_borderline_amber(self):
        assert fu.rag_status(90.0, 100.0) == "AMBER"   # exactly 10%

    def test_within_red_amber(self):
        assert fu.rag_status(80.0, 100.0) == "AMBER"   # 20%

    def test_beyond_red(self):
        assert fu.rag_status(70.0, 100.0) == "RED"     # 30%

    def test_over_production_within_green(self):
        assert fu.rag_status(108.0, 100.0) == "GREEN"  # 8% over

    def test_over_production_amber(self):
        assert fu.rag_status(115.0, 100.0) == "AMBER"  # 15% over

    def test_over_production_red(self):
        assert fu.rag_status(130.0, 100.0) == "RED"    # 30% over

    def test_custom_thresholds(self):
        assert fu.rag_status(88.0, 100.0, amber_pct=5.0, red_pct=20.0) == "AMBER"


# ---------------------------------------------------------------------------
# _elapsed_plan_days
# ---------------------------------------------------------------------------

class TestElapsedPlanDays:
    def test_july_8_of_31_with_25_working(self):
        # 8/31 * 25 ≈ 6.45 → 6
        result = fu._elapsed_plan_days("2026-07", "2026-07-08", 25)
        assert result == 6

    def test_first_day(self):
        result = fu._elapsed_plan_days("2026-07", "2026-07-01", 25)
        assert result >= 1  # must return at least 1

    def test_last_day_july(self):
        result = fu._elapsed_plan_days("2026-07", "2026-07-31", 25)
        assert result == 25

    def test_mid_month(self):
        result = fu._elapsed_plan_days("2026-07", "2026-07-15", 25)
        # 15/31 * 25 ≈ 12.1 → 12
        assert result == 12


# ---------------------------------------------------------------------------
# parse_report11
# ---------------------------------------------------------------------------

def _r11_fixture():
    """Minimal Report-11 values matrix: 5 header rows + 3 data rows."""
    hdr = ["SR", "DATE", "SR", "MACHINE NO.", "TYPES", "ITEM CODE",
           "Running Hour", "", "Actual Output/Pcs", "Weight", "", "", "", "Actual rejection weight"]
    return [
        ["", "", "", "", ""],       # row 0 (blank)
        ["", "", "", "", ""],       # row 1
        ["", "", "", "", ""],       # row 2
        ["", "", "", "", ""],       # row 3
        hdr,                         # row 4 (header)
        ["", "1", "", "PIPE M/C - 1", "CPVC", "CPVC-110-A", "8", "", "500", "1200", "", "", "", "12"],
        ["", "2", "", "PIPE M/C - 1", "CPVC", "CPVC-90-B",  "2", "", "200", "450",  "", "", "", "5"],
        ["", "3", "", "PIPE M/C - 2", "SWR",  "SWR-160-A",  "6", "", "300", "890",  "", "", "", "9"],
    ]


class TestParseReport11:
    def test_returns_3_rows(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert len(rows) == 3

    def test_machine_preserved(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert rows[0]["machine"] == "PIPE M/C - 1"

    def test_item_code_preserved(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert rows[0]["item_code"] == "CPVC-110-A"

    def test_weight_parsed(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert rows[0]["weight_kg"] == 1200.0

    def test_pcs_parsed(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert rows[0]["pcs"] == 500.0

    def test_rejection_parsed(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        assert rows[0]["rejection_kg"] == 12.0

    def test_date_resolved(self):
        rows = fu.parse_report11(_r11_fixture(), "2026-07")
        # Day 1 → 2026-07-01
        assert rows[0]["date"] == "2026-07-01"

    def test_empty_values_returns_empty(self):
        assert fu.parse_report11([], "2026-07") == []

    def test_zero_output_row_skipped(self):
        vals = _r11_fixture()
        # Add a row with 0 pcs and 0 kg
        vals.append(["", "4", "", "PIPE M/C - 3", "AGRI", "AGRI-X", "0", "", "0", "0", "", "", "", "0"])
        rows = fu.parse_report11(vals, "2026-07")
        assert len(rows) == 3  # zero row not included


# ---------------------------------------------------------------------------
# build_plan_lines_from_schedule (offline, using stubs)
# ---------------------------------------------------------------------------

import dataclasses
from typing import List


@dataclasses.dataclass
class _FakeBlock:
    week: int = 1
    day: int = 1
    machine: str = "M/C-1"
    shift: str = "DAY"
    item_code: str = "CPVCA"
    raw_code: str = "CPVC-A"
    material: str = "CPVC"
    planned_hours: float = 10.0
    excess_hours: float = 0.0
    origin_week: int = 1
    is_idle: bool = False


@dataclasses.dataclass
class _FakeSched:
    blocks: List[_FakeBlock]
    segment: str = "PLUMBING"
    effective_month: str = "2026-07"


@dataclasses.dataclass
class _FakeItem:
    item_code: str
    rate_kg_per_hr: float
    rate_estimated: bool
    weight_per_pc_kg: float = 0.5
    material_kg: float = 0.0
    material: str = ""
    qty_pcs: float = 0.0
    fresh_compound_kg: float = 0.0
    pulverizer_kg: float = 0.0
    machine_hrs: float = 0.0
    capable_machines: list = dataclasses.field(default_factory=list)
    assignments: list = dataclasses.field(default_factory=list)
    has_weight: bool = True
    has_machine: bool = True
    raw_code: str = ""
    rate_fallback_tier: str = "item"


@dataclasses.dataclass
class _FakeEngineResult:
    items: list


class TestBuildPlanLines:
    def test_basic_line_counts(self):
        sched = _FakeSched(blocks=[_FakeBlock(), _FakeBlock(day=2)])
        eng = _FakeEngineResult(items=[_FakeItem("CPVCA", 100.0, False)])
        lines = fu.build_plan_lines_from_schedule(sched, eng, run_id=1,
                                                   segment="PLUMBING", month="2026-07")
        assert len(lines) == 2

    def test_planned_kg_calculation(self):
        sched = _FakeSched(blocks=[_FakeBlock(planned_hours=10.0, excess_hours=0.0)])
        eng = _FakeEngineResult(items=[_FakeItem("CPVCA", 100.0, False)])
        lines = fu.build_plan_lines_from_schedule(sched, eng, 1, "PLUMBING", "2026-07")
        # net_hours=10, rate=100 → planned_kg=1000
        assert lines[0].planned_kg == 1000.0

    def test_excess_subtracted(self):
        sched = _FakeSched(blocks=[_FakeBlock(planned_hours=10.0, excess_hours=2.0)])
        eng = _FakeEngineResult(items=[_FakeItem("CPVCA", 100.0, False)])
        lines = fu.build_plan_lines_from_schedule(sched, eng, 1, "PLUMBING", "2026-07")
        # net_hours=8, rate=100 → 800 kg
        assert lines[0].planned_kg == 800.0

    def test_machine_norm_applied(self):
        sched = _FakeSched(blocks=[_FakeBlock(machine="M/C-1")])
        eng = _FakeEngineResult(items=[_FakeItem("CPVCA", 100.0, False)])
        lines = fu.build_plan_lines_from_schedule(sched, eng, 1, "PLUMBING", "2026-07")
        assert lines[0].machine_norm == "MC1"

    def test_idle_block_included_as_idle(self):
        sched = _FakeSched(blocks=[_FakeBlock(is_idle=True, item_code="")])
        eng = _FakeEngineResult(items=[])
        lines = fu.build_plan_lines_from_schedule(sched, eng, 1, "PLUMBING", "2026-07")
        assert lines[0].is_idle is True

    def test_is_excess_flag(self):
        sched = _FakeSched(blocks=[_FakeBlock(excess_hours=1.0)])
        eng = _FakeEngineResult(items=[_FakeItem("CPVCA", 100.0, False)])
        lines = fu.build_plan_lines_from_schedule(sched, eng, 1, "PLUMBING", "2026-07")
        assert lines[0].is_excess is True


# ---------------------------------------------------------------------------
# compute_followup (offline, pure logic — no DB)
# ---------------------------------------------------------------------------

def _make_plan_line(machine="M/C-1", item="CPVCA", day=1, week=1,
                    planned_kg=100.0, planned_hrs=5.0, shift="DAY"):
    return {
        "plan_run_id": 1, "segment": "PLUMBING", "month": "2026-07",
        "week": week, "day": day, "shift": shift,
        "machine": machine, "machine_norm": fu.norm_machine(machine),
        "item_code": item, "item_norm": fu.norm_item(item),
        "material": "CPVC",
        "planned_pcs": 500.0, "planned_kg": planned_kg,
        "planned_hours": planned_hrs, "net_hours": planned_hrs,
        "rate_used": 100.0, "rate_estimated": False,
        "is_excess": False, "is_idle": False,
    }


def _make_actual_line(machine="PIPE M/C - 1", item="CPVC-A", date="2026-07-06",
                      actual_kg=85.0, actual_hrs=4.5, actual_pcs=400.0):
    return {
        "segment": "PLUMBING", "month": "2026-07",
        "date": date,
        "machine": machine, "machine_norm": fu.norm_machine(machine),
        "item_code": item, "item_norm": fu.norm_item(item),
        "material": "CPVC",
        "actual_pcs": actual_pcs, "actual_kg": actual_kg,
        "actual_hours": actual_hrs, "rejection_kg": 2.0,
        "source_tab": "Report-11",
    }


class TestComputeFollowupPlanToDate:
    """compute_followup uses elapsed_plan_days to restrict plan-to-date comparison."""

    def _run(self, plan_lines, actual_lines, month="2026-07",
             amber=10.0, red=25.0, hours_dev=15.0):
        import mp_model as _mm
        import unittest.mock as mock

        with mock.patch.object(_mm, "get_plan_lines", return_value=plan_lines), \
             mock.patch.object(_mm, "get_actual_lines", return_value=actual_lines):
            return fu.compute_followup(
                plan_run_id=1, segment="PLUMBING", month=month,
                amber_pct=amber, red_pct=red, hours_dev_pct=hours_dev,
            )

    def test_returns_result(self):
        plan = [_make_plan_line(day=1, planned_kg=100.0)]
        actual = [_make_actual_line(actual_kg=95.0, date="2026-07-06")]
        res = self._run(plan, actual)
        assert res is not None

    def test_as_of_date_is_max_actual_date(self):
        plan = [_make_plan_line(day=1, planned_kg=200.0)]
        actual = [
            _make_actual_line(actual_kg=50.0, date="2026-07-05"),
            _make_actual_line(item="SWR-A", actual_kg=60.0, date="2026-07-08"),
        ]
        res = self._run(plan, actual)
        assert res.as_of_date == "2026-07-08"

    def test_plan_day_7_beyond_elapsed_not_included(self):
        # As-of date = July 8 → elapsed_plan_days ≈ 6
        # Plan has a day-7 block → must NOT be included in plan-to-date
        plan = [
            _make_plan_line(day=1, planned_kg=100.0),
            _make_plan_line(day=7, planned_kg=300.0),  # beyond as-of
        ]
        actual = [_make_actual_line(actual_kg=95.0, date="2026-07-08")]
        res = self._run(plan, actual)
        # plan_todate should include only day-1 line (day-7 is beyond elapsed ≈ 6)
        iv = res.item_rows[0]
        assert iv.planned_kg_todate == 100.0  # day-1 only

    def test_no_actuals_returns_result_with_zero_actual(self):
        plan = [_make_plan_line()]
        res = self._run(plan, [])
        assert res is not None
        assert res.total_actual_kg == 0.0

    def test_returns_none_with_empty_both(self):
        res = self._run([], [])
        assert res is None


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

class TestWarnings:
    def _run(self, plan_lines, actual_lines, min_block=2.0, amber=10.0, red=25.0):
        import mp_model as _mm
        import unittest.mock as mock
        with mock.patch.object(_mm, "get_plan_lines", return_value=plan_lines), \
             mock.patch.object(_mm, "get_actual_lines", return_value=actual_lines):
            return fu.compute_followup(
                plan_run_id=1, segment="PLUMBING", month="2026-07",
                amber_pct=amber, red_pct=red, hours_dev_pct=15.0,
                min_run_block_hours=min_block,
            )

    def test_wrong_machine_warning_raised(self):
        """An item produced on a different machine than planned triggers WRONG_MACHINE."""
        plan = [_make_plan_line(machine="M/C-1", item="CPVCA", day=1, planned_kg=200.0)]
        # Actual on M/C-2 — not the planned machine
        actual = [_make_actual_line(machine="PIPE M/C - 2", item="CPVC-A", actual_kg=100.0)]
        res = self._run(plan, actual)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_WRONG_MACHINE in types

    def test_unplanned_item_warning(self):
        """An item in actuals but not in plan triggers UNPLANNED."""
        plan = [_make_plan_line(item="CPVCA", day=1)]
        actual = [_make_actual_line(item="BRAND-NEW-X", actual_kg=50.0)]
        res = self._run(plan, actual)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_UNPLANNED in types

    def test_not_started_warning(self):
        """A planned item with zero actual by elapsed day triggers NOT_STARTED."""
        plan = [_make_plan_line(item="CPVCA", day=1, planned_kg=100.0)]
        # No actuals for this item
        actual = [_make_actual_line(item="OTHER-ITEM", actual_kg=50.0, date="2026-07-06")]
        res = self._run(plan, actual)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_NOT_STARTED in types

    def test_qty_shortfall_warning(self):
        """Actual 40% below plan-to-date triggers QTY_SHORTFALL."""
        plan = [_make_plan_line(day=1, planned_kg=100.0)]
        actual = [_make_actual_line(actual_kg=50.0, date="2026-07-06")]
        res = self._run(plan, actual, amber=10.0, red=25.0)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_QTY_SHORT in types

    def test_qty_overrun_warning(self):
        """Actual 50% above plan-to-date triggers QTY_OVERRUN."""
        plan = [_make_plan_line(day=1, planned_kg=100.0)]
        actual = [_make_actual_line(actual_kg=160.0, date="2026-07-06")]
        res = self._run(plan, actual, amber=10.0, red=25.0)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_QTY_OVER in types

    def test_green_adherence_no_qty_warning(self):
        """Actual within GREEN threshold → no QTY warning."""
        plan = [_make_plan_line(day=1, planned_kg=100.0)]
        actual = [_make_actual_line(actual_kg=96.0, date="2026-07-06")]
        res = self._run(plan, actual, amber=10.0, red=25.0)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_QTY_SHORT not in types
        assert fu.WTYPE_QTY_OVER not in types

    def test_short_block_warning(self):
        """Actual hours per machine-day < min_run_block triggers SHORT_BLOCK."""
        plan = [_make_plan_line(day=1, planned_kg=100.0, planned_hrs=8.0)]
        actual = [_make_actual_line(actual_kg=50.0, actual_hrs=1.0, date="2026-07-06")]
        res = self._run(plan, actual, min_block=2.0)
        types = [w.warning_type for w in res.warnings]
        assert fu.WTYPE_SHORT_BLOCK in types

    def test_warnings_sorted_by_severity(self):
        """Warnings must be sorted severity ascending (1=critical first)."""
        plan = [_make_plan_line(day=1, planned_kg=100.0)]
        actual = [
            _make_actual_line(machine="PIPE M/C - 2", actual_kg=160.0, date="2026-07-06"),
        ]
        res = self._run(plan, actual, amber=10.0, red=25.0)
        severities = [w.severity for w in res.warnings]
        assert severities == sorted(severities)
