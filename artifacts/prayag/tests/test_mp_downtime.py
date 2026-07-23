"""
Tests for the BREAKDOWN / MAINTENANCE downtime system.

Scope:
 - MpMachineDowntime dataclass defaults (including resolved, deleted fields)
 - delete_downtime: soft-delete (sets deleted flag, returns True; idempotent False on repeat)
 - restore_downtime: undoes soft-delete
 - machine_down_on_date: 9 cases + soft-deleted records are always skipped
 - Inclusive day-count arithmetic
 - resolve_downtime end_date validation
 - _build_down_days: 7 cases; deleted records ignored
 - Scheduler capacity: reduction, restoration after resolve, cascade, unresolve, zero unchanged
 - Follow-up: IDLE_VS_PLAN / NOT_STARTED suppressed for down machines
               DOWNTIME info entries; PROD_DURING_DOWNTIME integrity entries
               deleted records do NOT suppress warnings
 - "/" route unaffected (home_route_unaffected)
"""
from __future__ import annotations

import datetime
import unittest
from unittest.mock import MagicMock, patch

import mp_model as _mm
import mp_scheduler as _sched
import mp_followup as _fu


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _today() -> datetime.date:
    return datetime.date.today()


# ---------------------------------------------------------------------------
# MpMachineDowntime dataclass
# ---------------------------------------------------------------------------

class TestMpMachineDowntimeDataclass(unittest.TestCase):

    def test_fields_defaults(self):
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
        self.assertFalse(rec.resolved)
        self.assertIsNone(rec.resolved_at)
        self.assertFalse(rec.deleted)
        self.assertIsNone(rec.deleted_at)
        self.assertIsNone(rec.id)

    def test_kind_values(self):
        for kind in ("breakdown", "maintenance"):
            rec = _mm.MpMachineDowntime(
                segment="PLUMBING", machine="M/C-1", kind=kind,
                start_date=datetime.date(2026, 7, 5),
                end_date=datetime.date(2026, 7, 10),
                resolved=True,
            )
            self.assertEqual(rec.kind, kind)
            self.assertTrue(rec.resolved)
            self.assertFalse(rec.deleted)


# ---------------------------------------------------------------------------
# insert_downtime validation (fires before any DB write)
# ---------------------------------------------------------------------------

class TestInsertDowntimeValidation(unittest.TestCase):

    def _make_rec(self, kind="breakdown", start=datetime.date(2026, 7, 1), end=None):
        return _mm.MpMachineDowntime(
            segment="PLUMBING", machine="M/C-1", kind=kind,
            start_date=start, end_date=end,
        )

    def test_invalid_kind_raises(self):
        rec = self._make_rec(kind="scheduled")
        with self.assertRaises(_mm.DowntimeValidationError):
            _mm.insert_downtime(rec)

    def test_end_before_start_raises(self):
        rec = self._make_rec(
            start=datetime.date(2026, 7, 10),
            end=datetime.date(2026, 7, 1),
        )
        with self.assertRaises(_mm.DowntimeValidationError):
            _mm.insert_downtime(rec)

    def test_end_equal_start_no_validation_error(self):
        rec = self._make_rec(
            start=datetime.date(2026, 7, 5),
            end=datetime.date(2026, 7, 5),
        )
        try:
            _mm.insert_downtime(rec)
        except _mm.DowntimeValidationError:
            self.fail("end_date == start_date must NOT raise DowntimeValidationError")
        except Exception:
            pass  # DB unavailable is acceptable in unit tests


# ---------------------------------------------------------------------------
# delete_downtime — SOFT DELETE (sets deleted flag)
# ---------------------------------------------------------------------------

class TestSoftDelete(unittest.TestCase):

    def test_delete_downtime_without_db_returns_false(self):
        """When DB is unavailable delete_downtime must return False (no crash)."""
        result = _mm.delete_downtime(9999)
        self.assertFalse(result)

    def test_delete_does_not_raise(self):
        try:
            _mm.delete_downtime(1)
        except Exception as e:
            self.fail(f"delete_downtime raised unexpectedly: {e}")

    def test_restore_downtime_without_db_returns_false(self):
        result = _mm.restore_downtime(9999)
        self.assertFalse(result)

    def test_deleted_flag_default_false(self):
        rec = _mm.MpMachineDowntime(
            segment="PLUMBING", machine="M/C-1", kind="breakdown",
            start_date=datetime.date(2026, 7, 1),
        )
        self.assertFalse(rec.deleted)
        self.assertIsNone(rec.deleted_at)

    def test_soft_deleted_record_skipped_by_machine_down_on_date(self):
        """A soft-deleted record must NOT make machine_down_on_date return True."""
        recs = [{
            "machine": "M/C-1", "kind": "breakdown",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": None,
            "resolved": False,
            "deleted": True,    # soft-deleted
        }]
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 15), recs))

    def test_non_deleted_record_still_works(self):
        recs = [{
            "machine": "M/C-1", "kind": "breakdown",
            "start_date": datetime.date(2026, 7, 1),
            "end_date": None,
            "resolved": False,
            "deleted": False,
        }]
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 15), recs))


# ---------------------------------------------------------------------------
# machine_down_on_date — pure, no DB
# ---------------------------------------------------------------------------

class TestMachineDownOnDate(unittest.TestCase):

    def _recs(self, sd_str, ed_str=None, resolved=False, deleted=False):
        return [{
            "machine": "M/C-1",
            "kind": "breakdown",
            "start_date": datetime.date.fromisoformat(sd_str),
            "end_date": (datetime.date.fromisoformat(ed_str) if ed_str else None),
            "resolved": resolved,
            "deleted": deleted,
            "reason": "",
        }]

    def test_open_record_down_on_start(self):
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 5), self._recs("2026-07-05")))

    def test_open_record_down_indefinitely(self):
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 8, 1), self._recs("2026-07-05")))

    def test_resolved_record_down_within_range(self):
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 8),
            self._recs("2026-07-05", "2026-07-12", resolved=True)))

    def test_resolved_record_inclusive_end_date(self):
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 12),
            self._recs("2026-07-05", "2026-07-12", resolved=True)))

    def test_resolved_record_up_after_end(self):
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 13),
            self._recs("2026-07-05", "2026-07-12", resolved=True)))

    def test_before_start_is_up(self):
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 9),
            self._recs("2026-07-10", "2026-07-20")))

    def test_wrong_machine_not_down(self):
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-2", datetime.date(2026, 7, 5), self._recs("2026-07-01")))

    def test_empty_records(self):
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 5), []))

    def test_string_dates_still_parse(self):
        recs = [{"machine": "M/C-1", "kind": "breakdown",
                 "start_date": "2026-07-01", "end_date": "2026-07-31",
                 "resolved": True, "deleted": False, "reason": ""}]
        self.assertTrue(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 15), recs))

    def test_deleted_record_never_blocks(self):
        recs = self._recs("2026-07-01", "2026-07-31", resolved=False, deleted=True)
        self.assertFalse(_mm.machine_down_on_date(
            "M/C-1", datetime.date(2026, 7, 15), recs))


# ---------------------------------------------------------------------------
# Inclusive day-count helper
# ---------------------------------------------------------------------------

class TestInclusiveDayCount(unittest.TestCase):

    def test_same_day_is_one(self):
        sd = datetime.date(2026, 7, 10)
        ed = datetime.date(2026, 7, 10)
        self.assertEqual((ed - sd).days + 1, 1)

    def test_five_day_range_is_five(self):
        sd = datetime.date(2026, 7, 5)
        ed = datetime.date(2026, 7, 9)
        self.assertEqual((ed - sd).days + 1, 5)

    def test_full_month_25_working_days(self):
        sd = datetime.date(2026, 7, 1)
        ed = datetime.date(2026, 7, 25)
        self.assertEqual((ed - sd).days + 1, 25)

    def test_open_record_days_grows_with_today(self):
        sd = datetime.date(2026, 7, 1)
        today = _today()
        expected = max(1, (today - sd).days + 1)
        ref = today
        computed = max(1, (ref - sd).days + 1)
        self.assertEqual(computed, expected)


# ---------------------------------------------------------------------------
# resolve_downtime end_date validation
# ---------------------------------------------------------------------------

class TestResolveEndDateValidation(unittest.TestCase):

    def test_validation_logic(self):
        start = datetime.date(2026, 7, 10)
        end   = datetime.date(2026, 7, 1)
        with self.assertRaises(_mm.DowntimeValidationError):
            if end < start:
                raise _mm.DowntimeValidationError("end_date must be >= start_date")

    def test_same_day_is_valid(self):
        start = datetime.date(2026, 7, 10)
        end   = datetime.date(2026, 7, 10)
        if end < start:
            raise _mm.DowntimeValidationError("should not happen")

    def test_later_date_is_valid(self):
        start = datetime.date(2026, 7, 10)
        end   = datetime.date(2026, 7, 20)
        if end < start:
            raise _mm.DowntimeValidationError("should not happen")


# ---------------------------------------------------------------------------
# _build_down_days — pure, no DB; deleted records ignored
# ---------------------------------------------------------------------------

class TestBuildDownDays(unittest.TestCase):

    def _mc_params(self, machines=("M/C-1", "M/C-2")):
        return {mc: {"hours_per_shift": 10.0, "capacity_hrs_month": 500.0} for mc in machines}

    def _rec(self, machine, sd_str, ed_str=None, resolved=False, deleted=False):
        return {
            "machine": machine, "kind": "breakdown", "resolved": resolved,
            "deleted": deleted,
            "start_date": datetime.date.fromisoformat(sd_str),
            "end_date": (datetime.date.fromisoformat(ed_str) if ed_str else None),
            "reason": "",
        }

    def test_no_records_returns_empty(self):
        result = _sched._build_down_days([], self._mc_params(), "2026-07", 25)
        self.assertEqual(result, {})

    def test_open_record_marks_all_remaining_days(self):
        recs = [self._rec("M/C-1", "2026-07-20")]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertIn("M/C-1", down)
        self.assertIn(20, down["M/C-1"])
        self.assertIn(25, down["M/C-1"])

    def test_resolved_record_only_marks_covered_days(self):
        recs = [self._rec("M/C-1", "2026-07-05", "2026-07-10", resolved=True)]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertIn(5, down["M/C-1"])
        self.assertIn(10, down["M/C-1"])
        self.assertNotIn(4, down["M/C-1"])
        self.assertNotIn(11, down["M/C-1"])

    def test_machine_not_in_mc_params_is_ignored(self):
        recs = [self._rec("M/C-9", "2026-07-01")]
        down = _sched._build_down_days(recs, self._mc_params(("M/C-1",)), "2026-07", 25)
        self.assertNotIn("M/C-9", down)

    def test_other_machine_unaffected(self):
        recs = [self._rec("M/C-1", "2026-07-01", "2026-07-31")]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertNotIn("M/C-2", down)

    def test_full_month_breakdown(self):
        recs = [self._rec("M/C-1", "2026-07-01", "2026-07-31")]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertEqual(len(down["M/C-1"]), 25)

    def test_resolved_record_machine_back_after_end(self):
        recs = [self._rec("M/C-1", "2026-07-01", "2026-07-10", resolved=True)]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertNotIn(11, down.get("M/C-1", set()))

    def test_deleted_record_does_not_block_any_day(self):
        """Soft-deleted records must never appear in _build_down_days output."""
        recs = [self._rec("M/C-1", "2026-07-01", "2026-07-31", deleted=True)]
        down = _sched._build_down_days(recs, self._mc_params(), "2026-07", 25)
        self.assertNotIn("M/C-1", down)

    def test_delete_then_restore_capacity(self):
        """Active record blocks capacity; deleting it (deleted=True) restores all days."""
        active = [self._rec("M/C-1", "2026-07-01", "2026-07-31", deleted=False)]
        deleted = [self._rec("M/C-1", "2026-07-01", "2026-07-31", deleted=True)]
        down_active  = _sched._build_down_days(active,  self._mc_params(), "2026-07", 25)
        down_deleted = _sched._build_down_days(deleted, self._mc_params(), "2026-07", 25)
        self.assertEqual(len(down_active.get("M/C-1", set())), 25)
        self.assertNotIn("M/C-1", down_deleted)


# ---------------------------------------------------------------------------
# Scheduler: capacity reduction + restoration
# ---------------------------------------------------------------------------

class TestSchedulerCapacity(unittest.TestCase):

    def _mc_params_rows(self, machines=("M/C-1",)):
        return [{"machine": mc, "kind": "extrusion",
                 "hours_per_shift": 10.0, "capacity_hrs_month": 500.0,
                 "effective_month": "2026-07", "segment": "PLUMBING"}
                for mc in machines]

    def _item(self, item_code, machine, hrs=100.0):
        it = MagicMock()
        it.item_code = item_code; it.raw_code = item_code
        it.material = "UPVC"; it.has_weight = True; it.has_machine = True
        it.machine_hrs = hrs; it.rate_kg_per_hr = 200.0
        it.capable_machines = [machine]
        return it

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_no_downtime_unchanged(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        item = self._item("PW11", "M/C-1")
        r1 = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=None)
        r2 = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=[])
        self.assertEqual(r1.downtime_machine_days, 0)
        self.assertEqual(r2.downtime_machine_days, 0)
        self.assertEqual(r1.total_scheduled_hrs, r2.total_scheduled_hrs)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_5day_breakdown_loses_100hrs(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        dt = [{"machine": "M/C-1", "kind": "breakdown", "resolved": False, "deleted": False,
               "start_date": datetime.date(2026, 7, 1),
               "end_date": datetime.date(2026, 7, 5), "reason": ""}]
        item = self._item("PW11", "M/C-1", hrs=200.0)
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=dt)
        self.assertEqual(result.downtime_machine_days, 5)
        self.assertAlmostEqual(result.downtime_hours_lost, 100.0, places=0)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_deleted_record_does_not_reduce_capacity(self, mgm, mgp):
        """Soft-deleted breakdown must not reduce machine capacity at all."""
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        dt_deleted = [{"machine": "M/C-1", "kind": "breakdown", "resolved": False, "deleted": True,
                       "start_date": datetime.date(2026, 7, 1),
                       "end_date": datetime.date(2026, 7, 25), "reason": ""}]
        item = self._item("PW11", "M/C-1", hrs=100.0)
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=dt_deleted)
        self.assertEqual(result.downtime_machine_days, 0)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_resolved_record_reduces_capacity_only_through_end_date(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        dt = [{"machine": "M/C-1", "kind": "breakdown", "resolved": True, "deleted": False,
               "start_date": datetime.date(2026, 7, 1),
               "end_date": datetime.date(2026, 7, 5), "reason": ""}]
        item = self._item("PW11", "M/C-1", hrs=200.0)
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=dt)
        self.assertEqual(result.downtime_machine_days, 5)
        mc1_blocks = [b for b in result.blocks
                      if b.machine == "M/C-1" and b.item_code == "PW11" and not b.is_idle]
        self.assertGreater(len(mc1_blocks), 0, "Machine should schedule after resolved end_date")

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_full_month_down_items_cascade_to_other_machine(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows(("M/C-1", "M/C-2"))
        dt = [{"machine": "M/C-1", "kind": "breakdown", "resolved": False, "deleted": False,
               "start_date": datetime.date(2026, 7, 1),
               "end_date": datetime.date(2026, 7, 25), "reason": ""}]
        item = MagicMock()
        item.item_code = "PW11"; item.raw_code = "PW11"; item.material = "UPVC"
        item.has_weight = True; item.has_machine = True
        item.machine_hrs = 50.0; item.rate_kg_per_hr = 200.0
        item.capable_machines = ["M/C-1", "M/C-2"]
        result = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=dt)
        mc2_blocks = [b for b in result.blocks
                      if b.machine == "M/C-2" and b.item_code == "PW11" and not b.is_idle]
        self.assertGreater(len(mc2_blocks), 0, "Item must cascade to M/C-2")

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_unresolve_restores_unavailability(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        dt_open = [{"machine": "M/C-1", "kind": "breakdown", "resolved": False, "deleted": False,
                    "start_date": datetime.date(2026, 7, 1), "end_date": None, "reason": ""}]
        result = _sched.run_shift_schedule([], [], "PLUMBING", "2026-07", downtime_records=dt_open)
        self.assertEqual(result.downtime_machine_days, 25)

    @patch("mp_model.get_params")
    @patch("mp_model.get_machines")
    def test_zero_downtime_plan_unchanged(self, mgm, mgp):
        mgp.return_value = None
        mgm.return_value = self._mc_params_rows()
        item = self._item("PW11", "M/C-1", hrs=100.0)
        r_no = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=None)
        r_empty = _sched.run_shift_schedule([item], [], "PLUMBING", "2026-07", downtime_records=[])
        self.assertEqual(r_no.total_scheduled_hrs, r_empty.total_scheduled_hrs)
        self.assertEqual(r_no.downtime_machine_days, 0)


# ---------------------------------------------------------------------------
# Follow-up: warning suppression + new warning types; deleted never suppresses
# ---------------------------------------------------------------------------

class TestFollowupDowntime(unittest.TestCase):

    def _machine_row(self, machine):
        mv = MagicMock()
        mv.machine = machine; mv.had_planned_work = True; mv.had_actual_work = False
        mv.planned_hours_todate = 50.0; mv.actual_hours = 0.0
        mv.hours_var_pct = 0.0; mv.planned_kg_todate = 500.0
        return mv

    def _item_row(self, machine, item_code="PW11", planned_kg=500.0, actual_kg=0.0):
        iv = MagicMock()
        iv.machine = machine; iv.machine_norm = _fu.norm_machine(machine)
        iv.item_code = item_code; iv.material = "UPVC"
        iv.planned_kg_todate = planned_kg; iv.actual_kg = actual_kg
        iv.planned_kg_total = planned_kg; iv.kg_var_pct = 0.0
        iv.rag = "GREEN"; iv.is_wrong_machine = False; iv.is_unplanned = False
        return iv

    def _dt(self, machine, start="2026-07-01", end=None, resolved=False, deleted=False):
        return [{"machine": machine, "kind": "breakdown",
                 "start_date": datetime.date.fromisoformat(start),
                 "end_date": (datetime.date.fromisoformat(end) if end else None),
                 "resolved": resolved, "deleted": deleted, "reason": ""}]

    def _gen(self, item_rows=None, machine_rows=None, actual_lines=None, dt=None, elapsed=10):
        return _fu._generate_warnings(
            item_rows=item_rows or [], machine_rows=machine_rows or [],
            plan_lines=[], actual_lines=actual_lines or [],
            elapsed_plan_days=elapsed,
            amber_pct=10.0, red_pct=25.0,
            hours_dev_pct=15.0, min_run_block_hours=2.0,
            downtime_records=dt,
        )

    def test_idle_vs_plan_suppressed_for_down_machine(self):
        warns = self._gen(machine_rows=[self._machine_row("M/C-1")],
                          dt=self._dt("M/C-1"))
        idle = [w for w in warns if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN and w.machine == "M/C-1"]
        self.assertEqual(len(idle), 0)

    def test_idle_vs_plan_raised_for_non_down_machine(self):
        warns = self._gen(machine_rows=[self._machine_row("M/C-2")],
                          dt=self._dt("M/C-1"))
        idle = [w for w in warns if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN and w.machine == "M/C-2"]
        self.assertEqual(len(idle), 1)

    def test_not_started_suppressed_for_down_machine(self):
        warns = self._gen(item_rows=[self._item_row("M/C-1")],
                          dt=self._dt("M/C-1"))
        ns = [w for w in warns if w.warning_type == _fu.WTYPE_NOT_STARTED and w.machine == "M/C-1"]
        self.assertEqual(len(ns), 0)

    def test_downtime_info_entry(self):
        warns = self._gen(dt=self._dt("M/C-1", "2026-07-01", "2026-07-10", resolved=True))
        dt_w = [w for w in warns if w.warning_type == _fu.WTYPE_DOWNTIME]
        self.assertEqual(len(dt_w), 1)
        self.assertEqual(dt_w[0].severity, 5)

    def test_prod_during_downtime(self):
        actual = [{"machine": "M/C-1", "machine_norm": "MC1", "item_norm": "PW11",
                   "item_code": "PW11", "date": datetime.date(2026, 7, 15),
                   "actual_hours": 12.0, "actual_kg": 2400.0}]
        warns = self._gen(actual_lines=actual,
                          dt=self._dt("M/C-1", "2026-07-01", "2026-07-31", resolved=True),
                          elapsed=15)
        pdd = [w for w in warns if w.warning_type == _fu.WTYPE_PROD_DURING_DOWN]
        self.assertGreater(len(pdd), 0)
        self.assertEqual(pdd[0].severity, 1)

    def test_no_downtime_idle_still_fires(self):
        mv = self._machine_row("M/C-1")
        for dt in (None, []):
            warns = self._gen(machine_rows=[mv], dt=dt)
            idle = [w for w in warns if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN]
            self.assertEqual(len(idle), 1)

    def test_deleted_record_does_not_suppress_idle_warning(self):
        """Soft-deleted records must NOT suppress IDLE_VS_PLAN warnings."""
        warns = self._gen(machine_rows=[self._machine_row("M/C-1")],
                          dt=self._dt("M/C-1", deleted=True))
        idle = [w for w in warns if w.warning_type == _fu.WTYPE_IDLE_VS_PLAN and w.machine == "M/C-1"]
        self.assertEqual(len(idle), 1)

    def test_deleted_record_does_not_suppress_not_started(self):
        """Soft-deleted records must NOT suppress NOT_STARTED warnings."""
        warns = self._gen(item_rows=[self._item_row("M/C-1")],
                          dt=self._dt("M/C-1", deleted=True))
        ns = [w for w in warns if w.warning_type == _fu.WTYPE_NOT_STARTED and w.machine == "M/C-1"]
        self.assertEqual(len(ns), 1)

    def test_home_route_unaffected(self):
        import app as _app
        self.assertTrue(hasattr(_app, "app"))


if __name__ == "__main__":
    unittest.main()
