"""
Integration-style regression for plan.build_plan partial daily-read handling.

When the requested plant+month daily workbook is withheld or fails to load, its
(plant, ym) is surfaced by sheets.get_daily_records in the reports payload under
``_failed_pairs``.  build_plan MUST NOT then produce clean, complete-looking
machine production metrics from the partial/withheld record set — it must flag
the plan as partial and withhold hours/utilisation/output.

All data here is synthetic; no network, no sheets, no DB.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import sheets
import metrics as _met
from plan import build_plan


PLANT = "PIPE"
MONTH = "2026-06"


def _prod_record(machine, hours=100.0, ideal=200.0, output=5000.0):
    """A daily-grain production Record that WOULD yield rich metrics if used."""
    return _met.Record(
        grain="daily",
        period=f"{MONTH}-15",
        date=f"{MONTH}-15",
        plant=PLANT,
        machine=machine,
        material="UPVC",
        unit="kg",
        total_count=output,
        actual_hours=hours,
        ideal_hours=ideal,
        ideal_month_hours=500.0,
    )


def _stub_empty_loaders(monkeypatch):
    """Neutralise every non-daily loader so the test isolates the daily path."""
    monkeypatch.setattr(sheets, "load_planning", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(sheets, "load_material_records", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(sheets, "load_maintenance_records", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(sheets, "load_manpower_records", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(sheets, "load_mixer_records", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(sheets, "load_toolroom_records", lambda *_a, **_k: None, raising=False)
    monkeypatch.setattr(sheets, "load_ptmt_master", lambda *_a, **_k: [], raising=False)


def test_clean_daily_read_produces_production_metrics(monkeypatch):
    """Baseline: with a healthy daily read, metrics ARE produced (not withheld)."""
    _stub_empty_loaders(monkeypatch)
    recs = [_prod_record("PIPE Pipe M/C-1")]
    monkeypatch.setattr(
        sheets, "get_daily_records",
        lambda _months: (recs, [], []),
        raising=False,
    )

    plans, _alerts = build_plan(PLANT, MONTH)
    assert plans, "expected a machine roster for PIPE"
    m1 = next(p for p in plans if p.machine == "PIPE Pipe M/C-1")

    # Sanity: the healthy path is NOT flagged partial and DOES carry metrics.
    assert m1.production_partial is False
    assert m1.production_partial_reason == ""
    assert m1.actual_hours > 0
    assert m1.total_output > 0


def test_failed_target_plant_month_withholds_production_metrics(monkeypatch):
    """A failed target plant+month must NOT yield clean production metrics."""
    _stub_empty_loaders(monkeypatch)

    # Records exist in the payload, but the target pair failed to fully load —
    # they are PARTIAL and must not drive machine metrics.
    recs = [_prod_record("PIPE Pipe M/C-1")]
    reason = f"{PLANT} daily ({MONTH}) is incomplete: only 3 of 30 days present"
    reports = [{
        "_failed_pairs": [(PLANT, MONTH)],
        "_failed_pair_reasons": {f"{PLANT}:{MONTH}": reason},
    }]
    monkeypatch.setattr(
        sheets, "get_daily_records",
        lambda _months: (recs, reports, []),
        raising=False,
    )

    plans, _alerts = build_plan(PLANT, MONTH)
    assert plans, "roster should still seed from the canonical PIPE list"

    # Every machine plan must carry the serializable partial-source flag...
    assert all(p.production_partial is True for p in plans)
    assert all(p.production_partial_reason for p in plans)
    assert all(reason in p.production_partial_reason for p in plans)

    # ...and NO production metric may be derived from the partial records.
    for p in plans:
        assert p.actual_hours == 0.0
        assert p.ideal_hours == 0.0
        assert p.idle_hours == 0.0
        assert p.total_output == 0.0
        assert p.utilisation_pct is None

    # The plan can never look "complete": a partial machine is never actionable,
    # and its Capacity gate cannot present as a clean green baseline.
    assert not any(p.actionable for p in plans)
    for p in plans:
        cap = next((g for g in p.gates if g.name == "Capacity"), None)
        assert cap is not None
        assert cap.status != "green"


def test_other_plant_month_failure_does_not_flag_this_plant(monkeypatch):
    """A failed pair for a DIFFERENT plant/month must not withhold our metrics."""
    _stub_empty_loaders(monkeypatch)
    recs = [_prod_record("PIPE Pipe M/C-1")]
    reports = [{
        "_failed_pairs": [("PTMT", MONTH), (PLANT, "2026-05")],
        "_failed_pair_reasons": {
            f"PTMT:{MONTH}": "PTMT throttled",
            f"{PLANT}:2026-05": "PIPE May throttled",
        },
    }]
    monkeypatch.setattr(
        sheets, "get_daily_records",
        lambda _months: (recs, reports, []),
        raising=False,
    )

    plans, _alerts = build_plan(PLANT, MONTH)
    m1 = next(p for p in plans if p.machine == "PIPE Pipe M/C-1")
    assert m1.production_partial is False
    assert m1.actual_hours > 0
    assert m1.total_output > 0
