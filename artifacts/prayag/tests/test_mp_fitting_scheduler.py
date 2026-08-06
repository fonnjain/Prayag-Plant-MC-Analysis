"""
Tests for mp_scheduler.run_fitting_schedule — capacity enforcement for fitting (moulding) machines.

All tests run offline without DB access: synthetic FittingItemResult and
FittingDemandItem objects are passed directly, with mp_model stubbed out.
"""
from __future__ import annotations

import dataclasses
import sys
import os
import types
from typing import List, Optional

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Minimal stubs for offline use ────────────────────────────────────────────

def _make_mp_model_stub(moulding_machines=None):
    """Return a minimal mp_model stub for fitting scheduler tests."""
    stub = types.ModuleType("mp_model")
    stub.AVAILABLE = False

    @dataclasses.dataclass
    class MpParams:
        segment: str = ""
        waste_pct: float = 4.0
        pulverizer_pct: float = 25.0
        effective_month: str = ""
        min_run_block_hours: float = 2.0
        night_changeover_allowed: bool = False
        week_days: str = "[6,6,6,7]"

    default_machines = moulding_machines or [
        {"machine": "MOD-1", "capacity_hrs_month": 250, "shifts_per_day": 2,
         "hours_per_shift": 10, "working_days_month": 25, "kind": "moulding"},
        {"machine": "MOD-2", "capacity_hrs_month": 250, "shifts_per_day": 2,
         "hours_per_shift": 10, "working_days_month": 25, "kind": "moulding"},
    ]

    def get_params(segment, effective_month):
        return MpParams()

    def get_machines(segment, effective_month, kind=None):
        if kind == "moulding":
            return default_machines
        return []

    stub.get_params = get_params
    stub.get_machines = get_machines
    return stub


@pytest.fixture(autouse=True)
def patch_mp_model(monkeypatch):
    """Inject the mp_model stub and force a fresh import of mp_scheduler."""
    stub = _make_mp_model_stub()
    real_scheduler = sys.modules.get("mp_scheduler")
    monkeypatch.setitem(sys.modules, "mp_model", stub)
    sys.modules.pop("mp_scheduler", None)
    yield
    sys.modules.pop("mp_scheduler", None)
    if real_scheduler is not None:
        sys.modules["mp_scheduler"] = real_scheduler


# ── Minimal FittingItemResult / FittingDemandItem stubs ──────────────────────

@dataclasses.dataclass
class _FittingItem:
    item_code: str
    raw_code: str
    material: str
    qty_pcs: float = 0.0
    weight_per_pc_kg: float = 0.0
    material_kg: float = 0.0
    fresh_compound_kg: float = 0.0
    pulverizer_kg: float = 0.0
    pcs_per_hr: float = 100.0
    rate_estimated: bool = False
    machine_hrs: float = 0.0
    cavity: Optional[float] = None
    cycle_time_sec: Optional[float] = None
    num_cycles: Optional[float] = None
    capable_machines: List[str] = dataclasses.field(default_factory=list)
    route_estimated: bool = False
    assignments: list = dataclasses.field(default_factory=list)
    has_weight: bool = True
    has_machine: bool = True
    gross_qty_pcs: float = 0.0
    rej_rate: float = 0.0
    rej_basis: str = "none"
    rej_capped: bool = False
    waste_pct_used: float = 0.0
    waste_basis: str = "default"


@dataclasses.dataclass
class _FittingDemand:
    item_code: str
    raw_code: str
    material: str
    qty_pcs: float


# ── Helper ───────────────────────────────────────────────────────────────────

def _run(fitting_items, fitting_demand=None, segment="PLUMBING", month="2026-05"):
    import mp_scheduler as sched
    return sched.run_fitting_schedule(
        fitting_items=fitting_items,
        fitting_demand=fitting_demand or [],
        segment=segment,
        effective_month=month,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEmptyDemand:
    def test_no_items_all_idle(self):
        result = _run([])
        assert all(b.is_idle for b in result.blocks)
        assert result.total_scheduled_hrs == 0.0
        assert result.unfinished == []

    def test_total_capacity_is_sum_of_moulding_machines(self):
        result = _run([])
        # 2 machines × 250h = 500h
        assert result.total_capacity_hrs == 500.0

    def test_segment_and_month_stored(self):
        result = _run([], segment="PLUMBING", month="2026-07")
        assert result.segment == "PLUMBING"
        assert result.effective_month == "2026-07"

    def test_weekly_fill_rows_produced(self):
        result = _run([])
        # 2 machines × 4 weeks = 8 rows
        assert len(result.weekly_fill) == 8


class TestFitsInCapacity:
    """Items that collectively fit within moulding machine capacity."""

    def _item(self, code="FIT-A", hrs=10.0, mat_kg=50.0, machine="MOD-1"):
        return _FittingItem(
            item_code=code, raw_code=code, material="CPVC",
            machine_hrs=hrs, material_kg=mat_kg, gross_qty_pcs=200.0,
            pcs_per_hr=20.0, capable_machines=[machine],
        )

    def test_item_fully_scheduled(self):
        """A small item that fits in one day should have no unfinished remainder."""
        result = _run([self._item(hrs=10.0)])
        assert result.unfinished == []

    def test_scheduled_hours_positive(self):
        result = _run([self._item(hrs=10.0)])
        assert result.total_scheduled_hrs > 0

    def test_blocks_contain_item(self):
        result = _run([self._item(hrs=10.0)])
        non_idle = [b for b in result.blocks if not b.is_idle]
        assert any(b.item_code == "FIT-A" for b in non_idle)

    def test_machine_respected(self):
        """Item routed only to MOD-1 must never appear on MOD-2."""
        result = _run([self._item(hrs=10.0, machine="MOD-1")])
        mod2_items = {b.item_code for b in result.blocks
                      if b.machine == "MOD-2" and not b.is_idle}
        assert "FIT-A" not in mod2_items

    def test_two_items_separate_machines(self):
        """Items each routed to their own machine run independently."""
        item1 = _FittingItem(
            item_code="FIT-A", raw_code="A", material="CPVC",
            machine_hrs=10.0, material_kg=50.0, gross_qty_pcs=100.0,
            capable_machines=["MOD-1"],
        )
        item2 = _FittingItem(
            item_code="FIT-B", raw_code="B", material="UPVC",
            machine_hrs=10.0, material_kg=60.0, gross_qty_pcs=120.0,
            capable_machines=["MOD-2"],
        )
        result = _run([item1, item2])
        assert result.unfinished == []
        mod1_items = {b.item_code for b in result.blocks
                      if b.machine == "MOD-1" and not b.is_idle}
        mod2_items = {b.item_code for b in result.blocks
                      if b.machine == "MOD-2" and not b.is_idle}
        assert "FIT-A" in mod1_items
        assert "FIT-B" in mod2_items
        assert "FIT-B" not in mod1_items
        assert "FIT-A" not in mod2_items


class TestOverCapacity:
    """Items whose total machine hours exceed moulding capacity."""

    def _heavy_item(self, code="FIT-X", hrs=9999.0, machine="MOD-1"):
        return _FittingItem(
            item_code=code, raw_code=code, material="CPVC",
            machine_hrs=hrs, material_kg=hrs * 5.0, gross_qty_pcs=1000.0,
            capable_machines=[machine],
        )

    def test_over_capacity_item_becomes_unfinished(self):
        """Item needing more hours than MOD-1's monthly capacity is partially deferred."""
        result = _run([self._heavy_item(hrs=9999.0)])
        assert len(result.unfinished) == 1
        assert result.unfinished[0].item_code == "FIT-X"

    def test_unfinished_remaining_hours_positive(self):
        result = _run([self._heavy_item(hrs=9999.0)])
        assert result.unfinished[0].remaining_hours > 0

    def test_unfinished_remaining_kg_proportional(self):
        """remaining_kg must equal remaining_hours × (material_kg / machine_hrs)."""
        hrs = 9999.0
        mat_kg = hrs * 5.0
        item = self._heavy_item(hrs=hrs)
        item.material_kg = mat_kg
        result = _run([item])
        u = result.unfinished[0]
        # rate_kg_per_hr = mat_kg / hrs = 5.0; remaining_kg = u.remaining_hours * 5
        expected_kg = round(u.remaining_hours * 5.0, 1)
        assert abs(u.remaining_kg - expected_kg) < 1.0

    def test_scheduler_never_exceeds_declared_capacity(self):
        """Total scheduled hours per machine must never exceed capacity_hrs_month.

        The fitting scheduler enforces capacity_hrs_month as a hard budget:
        once a machine exhausts its declared monthly capacity it idles for the
        rest of the month regardless of remaining working days.  This guarantees
        weekly_fill.scheduled_hrs ≤ weekly_fill.capacity_hrs for every row,
        which is required for the Machine Load tab assertion to hold.

        Stub declares capacity_hrs_month=250 but has 25 days × 2 shifts × 10 hps
        = 500 physical hours — so the declared capacity is the binding constraint.
        """
        result = _run([self._heavy_item(hrs=9999.0)])
        # declared capacity per machine: 250 hrs (from stub)
        declared_capacity = 250.0
        sched_by_mc: dict = {}
        for wf in result.weekly_fill:
            sched_by_mc.setdefault(wf.machine, 0.0)
            sched_by_mc[wf.machine] += wf.scheduled_hrs
        for mc, total_sched in sched_by_mc.items():
            assert total_sched <= declared_capacity + 0.1, (
                f"{mc}: total scheduled {total_sched} > declared capacity {declared_capacity}"
            )

    def test_monthly_scheduled_never_exceeds_monthly_capacity(self):
        """Per-machine MONTHLY total scheduled ≤ MONTHLY total capacity.

        The Machine Load tab assertion aggregates weekly_fill rows into monthly
        totals per machine, then checks utilisation.  Weekly rows carry a
        pro-rated share of declared capacity for display only — the enforced
        constraint is at the monthly level, not per week.
        """
        result = _run([self._heavy_item(hrs=9999.0)])
        by_mc: dict = {}
        for wf in result.weekly_fill:
            by_mc.setdefault(wf.machine, {"sched": 0.0, "cap": 0.0})
            by_mc[wf.machine]["sched"] += wf.scheduled_hrs
            by_mc[wf.machine]["cap"]   += wf.capacity_hrs
        for mc, d in by_mc.items():
            assert d["sched"] <= d["cap"] + 0.01, (
                f"{mc}: monthly scheduled {d['sched']} > monthly capacity {d['cap']}"
            )

    def test_two_heavy_items_both_partially_deferred(self):
        """When two large items compete for one machine both must appear in unfinished."""
        item_a = _FittingItem(
            item_code="HEAVY-A", raw_code="A", material="CPVC",
            machine_hrs=5000.0, material_kg=25000.0, gross_qty_pcs=500.0,
            capable_machines=["MOD-1"],
        )
        item_b = _FittingItem(
            item_code="HEAVY-B", raw_code="B", material="CPVC",
            machine_hrs=5000.0, material_kg=25000.0, gross_qty_pcs=500.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item_a, item_b])
        unfinished_codes = {u.item_code for u in result.unfinished}
        # At least one (likely both) must be deferred
        assert unfinished_codes & {"HEAVY-A", "HEAVY-B"}


class TestUnfinishedDetails:
    def test_unfinished_has_capable_machines(self):
        item = _FittingItem(
            item_code="DEF-1", raw_code="D", material="SWR",
            machine_hrs=9999.0, material_kg=9999.0, gross_qty_pcs=100.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item])
        assert result.unfinished[0].capable_machines == ["MOD-1"]

    def test_unfinished_sorted_by_remaining_hours_desc(self):
        """Largest remaining-hours items come first in the unfinished list."""
        big = _FittingItem(
            item_code="BIG", raw_code="B", material="CPVC",
            machine_hrs=8000.0, material_kg=8000.0, gross_qty_pcs=800.0,
            capable_machines=["MOD-1"],
        )
        small = _FittingItem(
            item_code="SMALL", raw_code="S", material="CPVC",
            machine_hrs=4000.0, material_kg=4000.0, gross_qty_pcs=400.0,
            capable_machines=["MOD-1"],
        )
        result = _run([big, small])
        if len(result.unfinished) >= 2:
            assert result.unfinished[0].remaining_hours >= result.unfinished[1].remaining_hours


class TestCapacityBudgetEnforcement:
    """Verify capacity_hrs_month is the binding constraint, not physical days."""

    def test_declared_capacity_250_limits_scheduling(self):
        """With capacity_hrs_month=250 and 9999h demand, scheduled ≤ 250h."""
        item = _FittingItem(
            item_code="CAP-TEST", raw_code="C", material="CPVC",
            machine_hrs=9999.0, material_kg=9999.0, gross_qty_pcs=1000.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item])
        total_sched = sum(
            wf.scheduled_hrs for wf in result.weekly_fill if wf.machine == "MOD-1"
        )
        assert total_sched <= 250.0 + 0.1, (
            f"Expected ≤ 250h but got {total_sched}h scheduled on MOD-1"
        )

    def test_item_is_partially_deferred(self):
        """Most of the 9999h item must remain unfinished (deferred)."""
        item = _FittingItem(
            item_code="CAP-TEST", raw_code="C", material="CPVC",
            machine_hrs=9999.0, material_kg=9999.0, gross_qty_pcs=1000.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item])
        assert len(result.unfinished) == 1
        assert result.unfinished[0].remaining_hours > 0

    def test_remaining_kg_proportional_to_remaining_hours(self):
        """remaining_kg = remaining_hrs × (material_kg / machine_hrs)."""
        hrs = 9999.0
        mat_kg = 5000.0
        item = _FittingItem(
            item_code="PROP-TEST", raw_code="P", material="CPVC",
            machine_hrs=hrs, material_kg=mat_kg, gross_qty_pcs=500.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item])
        u = result.unfinished[0]
        rate = mat_kg / hrs   # effective kg/hr
        expected_kg = round(u.remaining_hours * rate, 1)
        assert abs(u.remaining_kg - expected_kg) < 1.0, (
            f"remaining_kg {u.remaining_kg} != expected {expected_kg} "
            f"(remaining_hrs={u.remaining_hours}, rate={rate})"
        )

    def test_report_machine_load_assertion_does_not_fire(self):
        """capacity_feasible_plan_bytes must not raise AssertionError.

        Specifically: when a moulding machine has capacity_hrs_month=250 but
        physical days × 2 × hps = 500, the fitting scheduler must cap scheduled
        hours at 250 so the Machine Load tab invariant holds.
        """
        import sys, types

        # Stub mp_engine imports that mp_reports needs
        if "mp_engine" not in sys.modules:
            stub_engine = types.ModuleType("mp_engine")
            stub_engine.AssignedPortion = None
            stub_engine.EngineResult = None
            stub_engine.ItemResult = None
            stub_engine.REPORT_11_GROUPS = {"A": [], "B": [], "C": [], "D": []}
            sys.modules["mp_engine"] = stub_engine

        import mp_scheduler as sched
        import mp_reports as rpts

        item = _FittingItem(
            item_code="RPT-TEST", raw_code="R", material="CPVC",
            machine_hrs=9999.0, material_kg=9999.0, gross_qty_pcs=1000.0,
            capable_machines=["MOD-1"],
        )
        fitting_schedule = sched.run_fitting_schedule(
            fitting_items=[item],
            fitting_demand=[],
            segment="PLUMBING",
            effective_month="2026-05",
        )

        # Verify no assertion error — passes fitting_schedule, None for pipe
        try:
            data = rpts.capacity_feasible_plan_bytes(
                pipe_result=None,
                fitting_result=None,
                schedule=None,
                month="May-2026",
                fitting_schedule=fitting_schedule,
            )
            assert isinstance(data, bytes) and len(data) > 0
        except AssertionError as exc:
            pytest.fail(
                f"capacity_feasible_plan_bytes raised AssertionError (util > 100%): {exc}"
            )


class TestScheduleResultStructure:
    def test_to_dict_from_dict_roundtrip(self):
        """ScheduleResult serialises and deserialises correctly for fitting results."""
        item = _FittingItem(
            item_code="RT-1", raw_code="R", material="CPVC",
            machine_hrs=20.0, material_kg=100.0, gross_qty_pcs=200.0,
            capable_machines=["MOD-1"],
        )
        result = _run([item])
        d = result.to_dict()
        import mp_scheduler as sched
        restored = sched.ScheduleResult.from_dict(d)
        assert restored.segment == result.segment
        assert restored.total_capacity_hrs == result.total_capacity_hrs
        assert len(restored.weekly_fill) == len(result.weekly_fill)
        assert len(restored.unfinished) == len(result.unfinished)

    def test_week_days_present(self):
        result = _run([])
        assert result.week_days == [6, 6, 6, 7]

    def test_params_used_present(self):
        result = _run([])
        assert "min_run_block_hours" in result.params_used

    def test_has_no_weight_item_skipped(self):
        """Items with has_weight=False must be silently skipped."""
        bad = _FittingItem(
            item_code="NO-WT", raw_code="X", material="CPVC",
            machine_hrs=100.0, material_kg=0.0, gross_qty_pcs=0.0,
            capable_machines=["MOD-1"], has_weight=False,
        )
        result = _run([bad])
        non_idle = [b for b in result.blocks if not b.is_idle]
        assert not any(b.item_code == "NO-WT" for b in non_idle)
        assert result.unfinished == []

    def test_has_machine_false_item_skipped(self):
        """Items with has_machine=False must be silently skipped."""
        bad = _FittingItem(
            item_code="NO-MC", raw_code="Y", material="CPVC",
            machine_hrs=100.0, material_kg=500.0, gross_qty_pcs=100.0,
            capable_machines=["MOD-1"], has_machine=False,
        )
        result = _run([bad])
        non_idle = [b for b in result.blocks if not b.is_idle]
        assert not any(b.item_code == "NO-MC" for b in non_idle)

    def test_item_with_no_capable_moulding_machine_skipped(self):
        """Item whose capable_machines contains only extrusion (M/C-*) names is skipped."""
        bad = _FittingItem(
            item_code="PIPE-ITEM", raw_code="P", material="CPVC",
            machine_hrs=50.0, material_kg=250.0, gross_qty_pcs=100.0,
            capable_machines=["M/C-1", "M/C-2"],   # extrusion machines, not in mc_params
        )
        result = _run([bad])
        assert result.unfinished == []
        non_idle = [b for b in result.blocks if not b.is_idle]
        assert "PIPE-ITEM" not in {b.item_code for b in non_idle}
