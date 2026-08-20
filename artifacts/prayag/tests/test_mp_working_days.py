"""Regression coverage for Plumbing's configurable working-day calendar."""
from __future__ import annotations

import dataclasses
import datetime
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mp_corrective_replan as corrective
import mp_model
import mp_scheduler


@dataclasses.dataclass
class _Item:
    item_code: str = "ITEM-1"
    raw_code: str = "ITEM-1"
    material: str = "CPVC"
    machine_hrs: float = 0.0
    rate_kg_per_hr: float = 100.0
    capable_machines: list[str] = dataclasses.field(default_factory=lambda: ["M/C-1"])
    has_weight: bool = True
    has_machine: bool = True


@dataclasses.dataclass
class _Demand:
    item_code: str = "ITEM-1"
    raw_code: str = "ITEM-1"
    material: str = "CPVC"
    qty_pcs: float = 1.0
    week_qty: dict = dataclasses.field(default_factory=dict)
    first_requested_week: int = 1


def _machine_rows():
    return [
        {"machine": "M/C-1", "capacity_hrs_month": 500, "hours_per_shift": 10},
        {"machine": "M/C-2", "capacity_hrs_month": 500, "hours_per_shift": 10},
    ]


def _schedule(monkeypatch, params, *, segment="PLUMBING", items=None, month="2026-07"):
    monkeypatch.setattr(mp_scheduler._mp, "get_params", lambda *_: params)
    monkeypatch.setattr(mp_scheduler._mp, "get_machines", lambda *_args, **_kwargs: _machine_rows())
    return mp_scheduler.run_shift_schedule(
        engine_items=items or [],
        demand_items=[_Demand()] if items else [],
        segment=segment,
        effective_month=month,
    )


def test_week_day_validation_requires_four_positive_days_within_month():
    assert mp_model.validate_week_days([7, 7, 7, 10], "2026-07") == [7, 7, 7, 10]
    with pytest.raises(mp_model.MpModelError, match="exactly four"):
        mp_model.validate_week_days([7, 7, 7], "2026-07")
    with pytest.raises(mp_model.MpModelError, match="positive whole"):
        mp_model.validate_week_days([7, 7, 7, 0], "2026-07")
    with pytest.raises(mp_model.MpModelError, match="cannot exceed"):
        mp_model.validate_week_days([8, 8, 8, 8], "2026-07")


def test_full_calendar_split_schedules_every_july_date(monkeypatch):
    params = SimpleNamespace(
        min_run_block_hours=2.0,
        week_days="[7,7,7,10]",
        week_days_configured=True,
    )
    result = _schedule(monkeypatch, params, month="2026-07")
    assert result.week_days == [7, 7, 7, 10]
    assert max(block.day for block in result.blocks) == 31
    assert len(result.blocks) == 2 * 31 * 2


def test_unset_plumbing_setting_keeps_existing_scheduler_default(monkeypatch):
    params = SimpleNamespace(
        min_run_block_hours=2.0,
        week_days="[7,7,7,10]",
        week_days_configured=False,
    )
    result = _schedule(monkeypatch, params, items=[_Item(machine_hrs=999.0)])
    assert result.week_days == [6, 6, 6, 7]
    assert max(block.day for block in result.blocks) == 25
    assert result.capacity_advisory is None
    assert "capacity_advisory" not in result.to_dict()


def test_non_plumbing_schedule_does_not_change_when_marker_is_false(monkeypatch):
    params = SimpleNamespace(
        min_run_block_hours=2.0,
        week_days="[7,7,7,10]",
        week_days_configured=False,
    )
    result = _schedule(monkeypatch, params, segment="PTMT")
    assert result.week_days == [7, 7, 7, 10]
    assert "capacity_advisory" not in result.to_dict()


def test_capacity_advisory_uses_only_capacity_limited_unfinished_kg(monkeypatch):
    params = SimpleNamespace(
        min_run_block_hours=2.0,
        week_days="[6,6,6,7]",
        week_days_configured=True,
    )
    result = _schedule(
        monkeypatch,
        params,
        items=[_Item(machine_hrs=999.0)],
        month="2026-07",
    )
    assert result.capacity_advisory == {
        "remaining_kg": 49900.0,
        "configured_days": 25,
        "calendar_days": 31,
        "additional_hours": 240.0,
    }
    restored = mp_scheduler.ScheduleResult.from_dict(result.to_dict())
    assert restored.capacity_advisory == result.capacity_advisory


def test_downtime_only_unfinished_does_not_raise_calendar_extension_advisory():
    unfinished = [
        mp_scheduler.UnfinishedItem(
            item_code="DOWN-ONLY",
            raw_code="DOWN-ONLY",
            material="CPVC",
            remaining_hours=5.0,
            remaining_kg=500.0,
            capable_machines=["M/C-1"],
            origin_week=1,
            downtime_reason="only capable machine(s) are down (breakdown/maintenance)",
        )
    ]
    assert mp_scheduler._capacity_advisory(
        unfinished, _machine_rows(), "2026-07", [6, 6, 6, 7], "PLUMBING", True
    ) is None


def test_corrective_replan_keeps_mon_sat_fallback_when_unconfigured():
    assert corrective._count_working_days(
        2026, 8, datetime.date(2026, 8, 8)
    ) == (26, 6, 20)


def test_corrective_replan_proportionally_rounds_configured_days():
    total, elapsed, remaining = corrective._count_working_days(
        2026, 7, datetime.date(2026, 7, 16), configured_week_days=[6, 6, 6, 7]
    )
    assert (total, elapsed, remaining) == (25, 12, 13)
    assert elapsed + remaining == total


def test_full_calendar_replan_counts_each_calendar_date():
    total, elapsed, remaining = corrective._count_working_days(
        2026, 7, datetime.date(2026, 7, 16), configured_week_days=[7, 7, 7, 10]
    )
    assert (total, elapsed, remaining) == (31, 15, 16)