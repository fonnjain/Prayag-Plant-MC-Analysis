"""
Tests for the BREAKDOWN / MAINTENANCE downtime system.

Scope:
 - mp_model: MpMachineDowntime dataclass + CRUD helpers (pure / offline where possible)
 - mp_scheduler: downtime_records parameter reduces capacity, items cascade,
                 unfinished reason when ALL capable machines are down all month
 - mp_followup: IDLE_VS_PLAN / NOT_STARTED suppressed for down machines;
                DOWNTIME info entries; PROD_DURING_DOWNTIME integrity entries
 - No "/" route touched — all assertions are on /machine-planning/* objects only
"""
from __future__ import annotations

import datetime
import types
import sys
import unittest
from collections import defaultdict
from typing import Optional
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# mp_model helpers (pure, no DB)
# ---------------------------------------------------------------------------

import mp_model as _mm


class TestMpMachineDowntimeDataclass(unittest.TestCase):

    def test_fields(self):
        rec = _mm.MpMachineDowntime(
            segment="PLUMBING",
            machine="M/C-1",
            kind="breakdown",
            start_date=datetime.date(2026, 7, 1),
        )
        self.assertEqual(rec.segment, "PLUMBING")
        self.assertEqual(rec.machine, "M/C-1")
        self.assertEqual(rec.kind, "breakdown")
        self.assertIsNone(rec.end_date)
        self.assertIsNone(rec.id)

    def test_kind_values(self):
        for kind in ("breakdown", "maintenance"):
            rec = _mm.MpMachineDowntime(
                segment="PLUMBING",
                machine="M/C-1",
                kind=kind,
                start_date=datetime.date(2026, 7, 5),
                end_date=datetime.date(2026, 7, 10),
            )
            self.assertEqual(rec.kind, kind)


class TestInsertDowntimeValidation(unittest.TestCase):
    """Validation fires BEFORE any DB write (DowntimeValidationError)."""

    def _make_rec(self, kind="breakdown", start=datetime.date(2026, 7, 1), end=None):
        return _mm.MpMachineDowntime(
            segment="PLUMBING", machine="M/C-1", kind=kind,
            start_date=start, end_date=end,
        )

    def test_invalid_kind_raises(self):
        rec = self._make_rec(kind="scheduled")
        # validation happens before DB call
        with self.assertRaises(_mm.DowntimeValidationError):
            _mm.insert_downtime(rec)

    def test_end_before_start_raises(self):
        rec = self._make_rec(
            start=datetime.date(2026, 7, 10),
            end=datetime.date(2026, 7, 1),  # earlier!
        )
        with self.assertRaises(_mm.DowntimeValidationError):
            _mm.insert_downtime(rec)

    def test_end_equal_start_ok_or_db_unavailable(self):
        rec = self._make_rec(
            start=datetime.date(2026, 7, 5),
            end=datetime.date(2026, 7, 5),
        )
        # Should not raise DowntimeValidationError
        # (may raise MpModelError if DB unavailable, which is acceptable)
        try:
            _mm.insert_downtime(rec)
        except _mm.DowntimeValidationError:
            self.fail("end_date == start_date must NOT raise DowntimeValidationError")
        except Exception:
            pass  # DB not available is fine in unit tests


# ---------------------------------------------------------------------------
# mp_model.machine_down_on_date — pure helper, no DB
# ---------------------------------------------------------------------------

class TestMachineDownOnDate(unittest.TestCase):

    def _recs(self, sd_str, ed_str=None):
        return [{
            "machine": "M/C-1",
            "kind": "breakdown",
            "start_date": datetime.date.fromisoformat(sd_str),
            "end_date": (datetime.date.fromisoformat(ed_str) if ed_str else None),
            "reason": "",
        }]

    def test_open_record_machine_is_down_on_start(self):
        recs = self._recs("2026-07-05")
        self.assertTrue(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 5), recs))

    def test_open_record_machine_is_down_after_start(self):
        recs = self._recs("2026-07-05")
        self.assertTrue(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 8, 1), recs))

    def test_closed_record_machine_is_down_within_range(self):
        recs = self._recs("2026-07-05", "2026-07-12")
        self.assertTrue(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 8), recs))

    def test_closed_record_inclusive_end_date(self):
        recs = self._recs("2026-07-05", "2026-07-12")
        self.assertTrue(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 12), recs))

    def test_closed_record_machine_is_up_after_end(self):
        recs = self._recs("2026-07-05", "2026-07-12")
        self.assertFalse(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 13), recs))

    def test_before_start_is_up(self):
        recs = self._recs("2026-07-10", "2026-07-20")
        self.assertFalse(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 9), recs))

    def test_wrong_machine_not_down(self):
        recs = self._recs("2026-07-01")
        self.assertFalse(_mm.machine_down_on_date("M/C-2", datetime.date(2026, 7, 5), recs))

    def test_empty_records(self):
        self.assertFalse(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 5), []))

    def test_string_dates_still_parse(self):
        recs = [{"machine": "M/C-1", "kind": "breakdown",
                 "start_date": "2026-07-01", "end_date": "2026-07-31", "reason": ""}]
        self.assertTrue(_mm.machine_down_on_date("M/C-1", datetime.date(2026, 7, 15), recs))


# ---------------------------------------------------------------------------
# mp_scheduler._build_down_days — pure, no DB
# ---------------------------------------------------------------------------

import mp_scheduler as _sched


class TestBuildDownDays(unittest.TestCase):

    def _mc_params(self, machines=("M/C-1", "M/C-2")):
        return {mc: {"hours_per_shift": 10.0, "capacity_hrs_month": 500.0} for mc in machines}

    def _recs(self, machine, sd_str, ed_str=None):
        return [{
            "machine": machine,
            "kind": "breakdown",
            "start_date": datetime.date.fromisoformat(sd_str),
            "end_date": (datetime.date.fromisoformat(ed_str) if ed_str else None),
            "reason": "",
        }]

    def test_no_records_returns_empty(self):
        result = _sched._build_down_days([], self._mc_params(), "2026-07", 25)
        self.assertEqual(result, {})

    def test_open_record_marks_all_remaining_days(self):
        # Machine down from July 20 (day 20) — all of days 20..25 are down
        recs = self._recs("M/C-1", "2026-07-20")
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertIn("M/C-1", down)
        self.assertIn(20, down["M/C-1"])
        self.assertIn(25, down["M/C-1"])

    def test_closed_record_only_marks_covered_days(self):
        recs = self._recs("M/C-1", "2026-07-05", "2026-07-10")
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertIn(5, down["M/C-1"])
        self.assertIn(10, down["M/C-1"])   # inclusive
        self.assertNotIn(4, down["M/C-1"])
        self.assertNotIn(11, down["M/C-1"])

    def test_machine_not_in_mc_params_is_ignored(self):
        recs = self._recs("M/C-9", "2026-07-01")
        mc_params = self._mc_params(("M/C-1", "M/C-2"))  # M/C-9 not in it
        down = _sched._build_down_days(recs, mc_params, "2026-07", 25)
        self.assertNotIn("M/C-9", down)

    def test_other_machine_unaffected(self):
        recs = self._recs("M/C-1", "2026-07-01", "2026-07-31")
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertNotIn("M/C-2", down)

    def test_full_month_breakdown(self):
        recs = self._recs("M/C-1", "2026-07-01", "2026-07-31")
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertEqual(len(down["M/C-1"]), 25)  # all 25 working days covered


# ---------------------------------------------------------------------------
# mp_scheduler capacity-reduction math
# ---------------------------------------------------------------------------

class TestSchedulerDowntimeCapacityReduction(unittest.TestCase):
    """When a machine is down, capacity is reduced and DOWN blocks are recorded."""

    def _make_minimal_engine_item(self, item_code, machine, hrs=100.0, rate=200.0):
        """Build a minimal EngineItem-like object for the scheduler."""
        item = MagicMock()
        item.item_code = item_code
        item.raw_code = item_code
        item.material = "UPVC"
        item.has_weight = True
        item.has_machine = True
        item.machine_hrs = hrs
        item.rate_kg_per_hr = rate
        item.capable_machines = [machine]
        return item

    def _mc_params_rows(self, machines=("M/C-1",)):
        return [{"machine": mc, "kind": "extrusion",
                 "hours_per_shift": 10.0, "capacity_hrs_month": 500.0,
                 "effective_month": "2026-07", "segment": "PLUMBING"}
                for mc in machines]

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_no_downtime_plan_unchanged(self, mock_get_machines, mock_get_params):
        """With no downtime records the schedule should be identical."""
        mock_get_params.return_value = None
        mock_get_machines.return_value = self._mc_params_rows()

        item = self._make_minimal_engine_item("PW11", "M/C-1")
        result_no_dt = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07",
                                                 downtime_records=None)
        result_empty_dt = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07",
                                                    downtime_records=[])
        self.assertEqual(result_no_dt.downtime_machine_days, 0)
        self.assertEqual(result_empty_dt.downtime_machine_days, 0)
        # Both should produce same scheduled_hrs
        self.assertEqual(result_no_dt.total_scheduled_hrs,
                         result_empty_dt.total_scheduled_hrs)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_downtime_reduces_available_capacity(self, mock_get_machines, mock_get_params):
        """Machine down for 5 days should lose 5×2×10 = 100 h."""
        mock_get_params.return_value = None
        mock_get_machines.return_value = self._mc_params_rows()

        dt_recs = [{
            "machine": "M/C-1", "kind": "breakdown",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": datetime.date(2026, 7, 5),   # 5 days
            "reason": "",
        }]
        item = self._make_minimal_engine_item("PW11", "M/C-1", hrs=200.0)
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07",
                                           downtime_records=dt_recs)
        self.assertEqual(result.downtime_machine_days, 5)
        self.assertAlmostEqual(result.downtime_hours_lost, 100.0, places=0)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_down_machine_blocks_are_marked_idle(self, mock_get_machines, mock_get_params):
        """Blocks on down machine-days must be is_idle=True with item_code='DOWN'."""
        mock_get_params.return_value = None
        mock_get_machines.return_value = self._mc_params_rows()

        dt_recs = [{
            "machine": "M/C-1", "kind": "breakdown",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": datetime.date(2026, 7, 3),
            "reason": "",
        }]
        result = _sched.run_shift_schedule([], [], "PLUMBING", "2026-07",
                                           downtime_records=dt_recs)
        down_blocks = [b for b in result.blocks
                       if b.is_idle and b.item_code == "DOWN" and b.machine == "M/C-1"]
        # 3 days × 2 shifts = 6 DOWN blocks
        self.assertEqual(len(down_blocks), 6)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_item_cascades_to_other_machine(self, mock_get_machines, mock_get_params):
        """When M/C-1 is down half the month, item cascades to M/C-2."""
        mock_get_params.return_value = None
        mock_get_machines.return_value = self._mc_params_rows(("M/C-1", "M/C-2"))

        dt_recs = [{
            "machine": "M/C-1", "kind": "breakdown",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": datetime.date(2026, 7, 25),  # full month
            "reason": "",
        }]
        # Item capable on both M/C-1 and M/C-2
        item = MagicMock()
        item.item_code = "PW11"
        item.raw_code = "PW11"
        item.material = "UPVC"
        item.has_weight = True
        item.has_machine = True
        item.machine_hrs = 50.0
        item.rate_kg_per_hr = 200.0
        item.capable_machines = ["M/C-1", "M/C-2"]

        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07",
                                           downtime_records=dt_recs)
        # Item should be scheduled on M/C-2 (M/C-1 is fully down)
        mc2_blocks = [b for b in result.blocks
                      if b.machine == "M/C-2" and b.item_code == "PW11" and not b.is_idle]
        self.assertGreater(len(mc2_blocks), 0, "Item must cascade to M/C-2")

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_unfinished_downtime_reason_when_only_machine_down(
            self, mock_get_machines, mock_get_params):
        """Item routed only to a fully-down machine gets a downtime_reason."""
        mock_get_params.return_value = None
        mock_get_machines.return_value = self._mc_params_rows(("M/C-1",))

        # Full-month downtime on the ONLY capable machine
        dt_recs = [{
            "machine": "M/C-1", "kind": "maintenance",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": datetime.date(2026, 7, 31),
            "reason": "scheduled overhaul",
        }]
        item = self._make_minimal_engine_item("PW11", "M/C-1", hrs=200.0)
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07",
                                           downtime_records=dt_recs)
        self.assertEqual(len(result.unfinished), 1)
        self.assertIn("breakdown", result.unfinished[0].downtime_reason.lower() + "maintenance")
        self.assertIn("capable", result.unfinished[0].downtime_reason.lower())


# ---------------------------------------------------------------------------
# mp_followup warning suppression + new warning types
# ---------------------------------------------------------------------------

import mp_followup as _fu


class TestFollowupDowntimeSuppression(unittest.TestCase):
    """_generate_warnings must suppress IDLE_VS_PLAN + NOT_STARTED for down machines."""

    def _machine_row(self, machine, had_planned=True, had_actual=False, kg_todate=500.0):
        mv = MagicMock()
        mv.machine = machine
        mv.had_planned_work = had_planned
        mv.had_actual_work  = had_actual
        mv.planned_hours_todate = 50.0
        mv.actual_hours         = 0.0
        mv.hours_var_pct        = 0.0
        mv.planned_kg_todate    = kg_todate
        return mv

    def _item_row(self, machine, item_code="PW11", planned_kg=500.0, actual_kg=0.0):
        iv = MagicMock()
        iv.machine          = machine
        iv.machine_norm     = _fu.norm_machine(machine)
        iv.item_code        = item_code
        iv.material         = "UPVC"
        iv.planned_kg_todate = planned_kg
        iv.actual_kg        = actual_kg
        iv.planned_kg_total = planned_kg
        iv.kg_var_pct       = 0.0
        iv.rag              = "GREEN"
        iv.is_wrong_machine = False
        iv.is_unplanned     = False
        return iv

    def _dt_recs(self, machine, start="2026-07-01", end=None):
        return [{
            "machine": machine,
            "kind": "breakdown",
            "start_date": datetime.date.fromisoformat(start),
            "end_date": (datetime.date.fromisoformat(end) if end else None),
            "reason": "",
        }]

    def test_idle_vs_plan_suppressed_for_down_machine(self):
        mv = self._machine_row("M/C-1")
        dt_recs = self._dt_recs("M/C-1", "2026-07-01")
        warnings = _fu._generate_warnings(
            item_rows=[], machine_rows=[mv],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=10,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt_recs,
        )
        idle_warns = [w for w in warnings if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN
                      and w.machine == "M/C-1"]
        self.assertEqual(len(idle_warns), 0, "IDLE_VS_PLAN must be suppressed for down machine")

    def test_idle_vs_plan_raised_for_non_down_machine(self):
        mv = self._machine_row("M/C-2")
        dt_recs = self._dt_recs("M/C-1", "2026-07-01")  # M/C-1 is down, not M/C-2
        warnings = _fu._generate_warnings(
            item_rows=[], machine_rows=[mv],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=10,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt_recs,
        )
        idle_warns = [w for w in warnings if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN
                      and w.machine == "M/C-2"]
        self.assertEqual(len(idle_warns), 1, "IDLE_VS_PLAN must still fire for M/C-2")

    def test_not_started_suppressed_for_down_machine(self):
        iv = self._item_row("M/C-1", planned_kg=300.0, actual_kg=0.0)
        dt_recs = self._dt_recs("M/C-1", "2026-07-01")
        warnings = _fu._generate_warnings(
            item_rows=[iv], machine_rows=[],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=10,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt_recs,
        )
        ns_warns = [w for w in warnings if w.warning_type == _fu.WTYPE_NOT_STARTED
                    and w.machine == "M/C-1"]
        self.assertEqual(len(ns_warns), 0, "NOT_STARTED must be suppressed for down machine")

    def test_downtime_info_entry_present(self):
        """WTYPE_DOWNTIME informational entry must appear for each downtime record."""
        dt_recs = self._dt_recs("M/C-1", "2026-07-01", "2026-07-10")
        warnings = _fu._generate_warnings(
            item_rows=[], machine_rows=[],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=0,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt_recs,
        )
        dt_warns = [w for w in warnings if w.warning_type == _fu.WTYPE_DOWNTIME]
        self.assertEqual(len(dt_warns), 1)
        self.assertEqual(dt_warns[0].severity, 5)  # informational

    def test_prod_during_downtime_integrity_warning(self):
        """Actual production on a down machine raises PROD_DURING_DOWNTIME."""
        dt_recs = self._dt_recs("M/C-1", "2026-07-01", "2026-07-31")
        actual_lines = [{
            "machine": "M/C-1",
            "machine_norm": "MC1",
            "item_norm": "PW11",
            "item_code": "PW11",
            "date": datetime.date(2026, 7, 15),
            "actual_hours": 12.0,
            "actual_kg": 2400.0,
        }]
        warnings = _fu._generate_warnings(
            item_rows=[], machine_rows=[],
            plan_lines=[], actual_lines=actual_lines,
            elapsed_plan_days=15,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt_recs,
        )
        pdd_warns = [w for w in warnings if w.warning_type == _fu.WTYPE_PROD_DURING_DOWN]
        self.assertGreater(len(pdd_warns), 0, "PROD_DURING_DOWNTIME must fire")
        self.assertEqual(pdd_warns[0].severity, 1)  # critical

    def test_no_downtime_plan_unchanged(self):
        """With no downtime_records the function should behave exactly as before."""
        mv = self._machine_row("M/C-1")
        warnings_without = _fu._generate_warnings(
            item_rows=[], machine_rows=[mv],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=10,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
        )
        warnings_empty = _fu._generate_warnings(
            item_rows=[], machine_rows=[mv],
            plan_lines=[], actual_lines=[],
            elapsed_plan_days=10,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=[],
        )
        # Both should raise IDLE_VS_PLAN (no suppression)
        for warns in (warnings_without, warnings_empty):
            self.assertEqual(
                sum(1 for w in warns if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN), 1
            )

    def test_home_route_unaffected(self):
        """The downtime system must not touch any non-machine-planning code path.

        We verify that the "/" route is not accidentally importing from mp_model
        or mp_followup in a way that breaks it.  We simply check the module
        exists and still imports cleanly.
        """
        import app as _app
        self.assertTrue(hasattr(_app, "app"))


if __name__ == "__main__":
    unittest.main()
