"""Regression tests for manager ideal-hours overrides (run-hour gating + plant-level).

`_apply_ideal_overrides` applies a manager-entered planned-hours baseline on top of
each daily Record. Two honesty invariants must hold:

  1. Run-hour gating: for a run-hours-tracked machine, the override denominator is
     split ONLY across days that actually logged run hours. A day with output but
     no run hours must keep ``ideal_hours == 0`` (utilisation stays BLANK, never a
     fabricated 0%) while still being stamped ``ideal_source == "override"`` so the
     UI knows a baseline exists.
  2. Plant-level override: a plant with no machine identity (TANK, ``machine == ""``,
     ``runhours_tracked is False``) must still receive the override on every row.

Run: cd artifacts/prayag && python3 -m pytest tests/test_ideal_override_gating.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from metrics import Record


def _row(plant, machine, day, out, hours, tracked):
    return Record(
        grain="daily", plant=plant, segment=plant.title(), machine=machine,
        period="2026-06", date=f"2026-06-{day:02d}", total_count=out,
        reject_count=0.0, actual_hours=hours, ideal_hours=0.0, ideal_source="none",
        runhours_tracked=tracked,
    )


def _with_override(ov, fn):
    saved = app.store.ideal_overrides_for
    app.store.ideal_overrides_for = lambda m: ov if m == "2026-06" else {}
    try:
        return fn()
    finally:
        app.store.ideal_overrides_for = saved


def test_run_hour_gated_override_no_fake_zero():
    """A run-hours-tracked machine: 500h override splits only over run-hour days;
    a no-run-hour day stays ideal_hours=0 but is still flagged as overridden."""
    rows = [
        _row("GARDEN", "MACHINE-1", 1, 100.0, 8.0, True),   # has run hours
        _row("GARDEN", "MACHINE-1", 2, 120.0, 0.0, True),   # output, NO run hours
    ]
    ov = {("GARDEN", "MACHINE-1"): {"ideal_hours": 500.0}}
    out = _with_override(ov, lambda: app._apply_ideal_overrides(rows))

    by_day = {r.date: r for r in out}
    # The single run-hour day carries the FULL monthly override (denominator n=1).
    assert by_day["2026-06-01"].ideal_hours == 500.0, by_day["2026-06-01"].ideal_hours
    # The no-run-hour day must NOT be charged a baseline -> utilisation blank.
    assert by_day["2026-06-02"].ideal_hours == 0.0, by_day["2026-06-02"].ideal_hours
    # ...but BOTH are stamped 'override' so the UI shows a baseline exists.
    assert all(r.ideal_source == "override" for r in out), [r.ideal_source for r in out]


def test_plant_level_override_applies_to_output_only_plant():
    """TANK has no machine identity (machine=''), runhours_tracked=False: a
    plant-level override stamps every row even though utilisation stays suppressed."""
    rows = [
        _row("TANK", "", 1, 200.0, 0.0, False),
        _row("TANK", "", 2, 250.0, 0.0, False),
    ]
    ov = {("TANK", ""): {"ideal_hours": 500.0}}
    out = _with_override(ov, lambda: app._apply_ideal_overrides(rows))

    assert all(r.ideal_source == "override" for r in out), [r.ideal_source for r in out]
    # 500 split across the two rows (both eligible since the plant is output-only).
    assert sum(r.ideal_hours for r in out) == 500.0, [r.ideal_hours for r in out]


def test_override_does_not_mutate_cached_rows():
    """Overridden rows are copies (dataclasses.replace); originals keep their value."""
    rows = [_row("GARDEN", "MACHINE-1", 1, 100.0, 8.0, True)]
    ov = {("GARDEN", "MACHINE-1"): {"ideal_hours": 500.0}}
    out = _with_override(ov, lambda: app._apply_ideal_overrides(rows))
    assert out[0] is not rows[0]
    assert rows[0].ideal_hours == 0.0 and rows[0].ideal_source == "none"
