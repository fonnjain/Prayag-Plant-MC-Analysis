"""
Tests for mp_scheduler — shift-based day scheduler.

All tests run offline without DB access: we pass synthetic engine items and
demand items directly to run_shift_schedule via monkeypatching.
"""
from __future__ import annotations

import dataclasses
import sys
import os
import types
from typing import Dict, List

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Minimal stubs for offline use ────────────────────────────────────────────

def _make_mp_model_stub():
    """Return a minimal mp_model stub with get_params/get_machines returning defaults."""
    stub = types.ModuleType("mp_model")
    stub.AVAILABLE = False

    @dataclasses.dataclass
    class MpParams:
        segment: str = ""
        waste_pct: float = 4.0
        pulverizer_pct: float = 25.0
        effective_month: str = ""
        min_run_block_hours: float = 5.0
        night_changeover_allowed: bool = False
        week_days: str = "[6,6,6,7]"

    def get_params(segment, effective_month):
        return MpParams()

    def get_machines(segment, effective_month, kind=None):
        return [
            {"machine": "M/C-1", "capacity_hrs_month": 500, "shifts_per_day": 2, "hours_per_shift": 10, "working_days_month": 25},
            {"machine": "M/C-2", "capacity_hrs_month": 500, "shifts_per_day": 2, "hours_per_shift": 10, "working_days_month": 25},
        ]

    stub.get_params = get_params
    stub.get_machines = get_machines
    return stub


@pytest.fixture(autouse=True)
def patch_mp_model(monkeypatch):
    """Inject the mp_model stub before importing mp_scheduler."""
    stub = _make_mp_model_stub()
    monkeypatch.setitem(sys.modules, "mp_model", stub)
    # Force re-import of mp_scheduler with stub
    if "mp_scheduler" in sys.modules:
        del sys.modules["mp_scheduler"]
    yield


# ── Minimal ItemResult / DemandItem stubs ─────────────────────────────────

@dataclasses.dataclass
class _ItemResult:
    item_code: str
    raw_code: str
    material: str
    qty_pcs: float = 0.0
    weight_per_pc_kg: float = 0.0
    material_kg: float = 0.0
    fresh_compound_kg: float = 0.0
    pulverizer_kg: float = 0.0
    rate_kg_per_hr: float = 100.0
    rate_estimated: bool = False
    machine_hrs: float = 0.0
    capable_machines: list = dataclasses.field(default_factory=list)
    assignments: list = dataclasses.field(default_factory=list)
    has_weight: bool = True
    has_machine: bool = True


@dataclasses.dataclass
class _DemandItem:
    item_code: str
    raw_code: str
    material: str
    qty_pcs: float
    week_qty: Dict[int, float] = dataclasses.field(default_factory=dict)
    first_requested_week: int = 0


# ── Helper ───────────────────────────────────────────────────────────────────

def _run(engine_items, demand_items, segment="PLUMBING", month="2026-05"):
    import mp_scheduler as sched
    return sched.run_shift_schedule(engine_items, demand_items, segment, month)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestEmptyDemand:
    def test_no_items_returns_all_idle(self):
        result = _run([], [])
        # 2 machines × 25 days × 2 shifts = 100 blocks all idle
        assert all(b.is_idle for b in result.blocks)
        assert result.total_scheduled_hrs == 0.0
        assert result.total_idle_hrs > 0
        assert result.unfinished == []
        assert result.total_changeovers == 0

    def test_capacity_is_sum_of_machines(self):
        result = _run([], [])
        # 2 machines × 500h = 1000h
        assert result.total_capacity_hrs == 1000.0


class TestSingleItem:
    def _item(self, machine_hrs=20.0, capable=("M/C-1",)):
        return _ItemResult(
            item_code="CPVCA", raw_code="CPVC-A", material="CPVC",
            machine_hrs=machine_hrs, rate_kg_per_hr=100.0,
            capable_machines=list(capable),
        )

    def _demand(self, first_week=1):
        return _DemandItem(
            item_code="CPVCA", raw_code="CPVC-A", material="CPVC",
            qty_pcs=100, first_requested_week=first_week,
        )

    def test_single_item_scheduled_day1(self):
        """A 20h item on a 10h/shift machine fills exactly day 1 (DAY+NIGHT)."""
        result = _run([self._item(20.0)], [self._demand()])
        mc1_blocks = [b for b in result.blocks if b.machine == "M/C-1" and b.day == 1]
        assert len(mc1_blocks) == 2
        day_b  = next(b for b in mc1_blocks if b.shift == "DAY")
        night_b = next(b for b in mc1_blocks if b.shift == "NIGHT")
        assert day_b.item_code == "CPVCA"
        assert night_b.item_code == "CPVCA"
        assert day_b.planned_hours == 10.0
        assert night_b.planned_hours == 10.0
        assert night_b.excess_hours == pytest.approx(0.0, abs=0.01)

    def test_item_finished_no_unfinished(self):
        result = _run([self._item(20.0)], [self._demand()])
        assert result.unfinished == []

    def test_no_changeover_single_item(self):
        result = _run([self._item(20.0)], [self._demand()])
        assert result.total_changeovers == 0

    def test_excess_when_item_smaller_than_block(self):
        """Item needing 7h is scheduled in 20h machine-day → 13h excess."""
        result = _run([self._item(7.0)], [self._demand()])
        mc1_blocks = [b for b in result.blocks if b.machine == "M/C-1" and b.day == 1 and not b.is_idle]
        total_excess = sum(b.excess_hours for b in mc1_blocks)
        assert total_excess == pytest.approx(13.0, abs=0.1)

    def test_large_item_becomes_unfinished(self):
        """Item needing more hours than total month capacity stays unfinished."""
        # 2 machines × 25 days × 20h = 1000h capacity
        # Single item with only M/C-1 capable: 250h capacity on M/C-1
        result = _run([self._item(999.0, capable=("M/C-1",))], [self._demand()])
        assert len(result.unfinished) == 1
        assert result.unfinished[0].item_code == "CPVCA"

    def test_first_week_affects_priority(self):
        """W2 item should have lower priority than W1 item."""
        item_w1 = _ItemResult(
            item_code="W1ITEM", raw_code="W1", material="CPVC",
            machine_hrs=100.0, rate_kg_per_hr=100.0,
            capable_machines=["M/C-1"],
        )
        item_w2 = _ItemResult(
            item_code="W2ITEM", raw_code="W2", material="CPVC",
            machine_hrs=100.0, rate_kg_per_hr=100.0,
            capable_machines=["M/C-1"],
        )
        d_w1 = _DemandItem("W1ITEM", "W1", "CPVC", 100, first_requested_week=1)
        d_w2 = _DemandItem("W2ITEM", "W2", "CPVC", 100, first_requested_week=2)
        result = _run([item_w1, item_w2], [d_w1, d_w2])
        # W1 days (days 1-6): M/C-1 should schedule W1ITEM first (highest priority)
        w1_day1_mc1 = [b for b in result.blocks if b.machine == "M/C-1" and b.day == 1 and not b.is_idle]
        assert any(b.item_code == "W1ITEM" for b in w1_day1_mc1)


class TestTwoItems:
    def test_changeover_with_two_small_items(self):
        """Two items both capable of M/C-1, both large enough for blocks → changeover."""
        item_a = _ItemResult(
            item_code="ITEMA", raw_code="A", material="CPVC",
            machine_hrs=200.0, rate_kg_per_hr=100.0, capable_machines=["M/C-1"],
        )
        item_b = _ItemResult(
            item_code="ITEMB", raw_code="B", material="CPVC",
            machine_hrs=200.0, rate_kg_per_hr=100.0, capable_machines=["M/C-1"],
        )
        da = _DemandItem("ITEMA", "A", "CPVC", 100, first_requested_week=1)
        db = _DemandItem("ITEMB", "B", "CPVC", 100, first_requested_week=1)
        result = _run([item_a, item_b], [da, db])
        # At least one day should have 3 non-idle blocks (A in DAY, B-day, B-night)
        days_with_3 = {}
        for b in result.blocks:
            if b.machine == "M/C-1" and not b.is_idle:
                key = (b.machine, b.day)
                days_with_3[key] = days_with_3.get(key, 0) + 1
        assert any(v >= 3 for v in days_with_3.values()), "Expected ≥1 day with 3 blocks (two items)"
        assert result.total_changeovers >= 1

    def test_items_split_across_machines(self):
        """Item capable only of M/C-1, another only of M/C-2 → each goes to own machine."""
        item1 = _ItemResult(
            item_code="ITEM1", raw_code="1", material="CPVC",
            machine_hrs=10.0, rate_kg_per_hr=50.0, capable_machines=["M/C-1"],
        )
        item2 = _ItemResult(
            item_code="ITEM2", raw_code="2", material="UPVC",
            machine_hrs=10.0, rate_kg_per_hr=80.0, capable_machines=["M/C-2"],
        )
        result = _run([item1, item2], [
            _DemandItem("ITEM1", "1", "CPVC", 10),
            _DemandItem("ITEM2", "2", "UPVC", 10),
        ])
        mc1_items = {b.item_code for b in result.blocks if b.machine == "M/C-1" and not b.is_idle}
        mc2_items = {b.item_code for b in result.blocks if b.machine == "M/C-2" and not b.is_idle}
        assert "ITEM1" in mc1_items
        assert "ITEM2" in mc2_items
        assert "ITEM1" not in mc2_items
        assert "ITEM2" not in mc1_items


class TestWeeklyFill:
    def test_weekly_fill_rows_produced(self):
        result = _run([], [])
        # 2 machines × 4 weeks = 8 rows
        assert len(result.weekly_fill) == 8

    def test_week_capacity_proportional_to_days(self):
        """W4 (7 days) should have more capacity than W1 (6 days)."""
        result = _run([], [])
        mc1_fill = {r.week: r for r in result.weekly_fill if r.machine == "M/C-1"}
        assert mc1_fill[4].capacity_hrs > mc1_fill[1].capacity_hrs

    def test_total_week_capacity_equals_month_capacity(self):
        result = _run([], [])
        mc1_total = sum(r.capacity_hrs for r in result.weekly_fill if r.machine == "M/C-1")
        assert mc1_total == pytest.approx(500.0, abs=1.0)


class TestScheduleResult:
    def test_to_dict_from_dict_roundtrip(self):
        result = _run([], [])
        d = result.to_dict()
        import mp_scheduler as sched
        restored = sched.ScheduleResult.from_dict(d)
        assert restored.segment == result.segment
        assert restored.total_capacity_hrs == result.total_capacity_hrs
        assert len(restored.weekly_fill) == len(result.weekly_fill)
        assert len(restored.blocks) == len(result.blocks)

    def test_week_days_in_result(self):
        result = _run([], [])
        assert result.week_days == [6, 6, 6, 7]

    def test_params_used_present(self):
        result = _run([], [])
        assert "min_run_block_hours" in result.params_used
        assert result.params_used["min_run_block_hours"] == 5.0


class TestMinBlock:
    def test_tiny_item_padded_to_min_block(self):
        """Item with 2h need → padded to 5h min block → 3h excess."""
        item = _ItemResult(
            item_code="TINY", raw_code="T", material="CPVC",
            machine_hrs=2.0, rate_kg_per_hr=100.0, capable_machines=["M/C-1"],
        )
        result = _run([item], [_DemandItem("TINY", "T", "CPVC", 10)])
        mc1_non_idle = [b for b in result.blocks if b.machine == "M/C-1" and not b.is_idle]
        assert len(mc1_non_idle) == 1, "Tiny item should produce exactly 1 day-only block"
        assert mc1_non_idle[0].planned_hours == pytest.approx(5.0, abs=0.01)
        assert mc1_non_idle[0].excess_hours == pytest.approx(3.0, abs=0.01)
        assert result.total_excess_kg == pytest.approx(3.0 * 100.0, abs=1.0)
