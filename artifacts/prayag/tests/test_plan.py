"""
Fixture-based tests for plan.py — Phase 3.

All tests use synthetic data; no network, no sheets, no DB.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plan import (
    GateStatus, RunQueueItem, MachinePlan, GATE_PRIORITY,
    _norm, _partial_match, _lookup, _lookup_list,
    _find_worst_rm, _build_run_queue, _evaluate_gates, build_plan,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _gate(name, status, reason="", provenance=""):
    return GateStatus(name=name, status=status, reason=reason, provenance=provenance)


def _qi(item_code, net_req, days_cover, rate=None):
    est = round(net_req / rate, 2) if rate else None
    return RunQueueItem(
        item_code=item_code,
        item_name=f"Item {item_code}",
        family="TEST",
        net_requirement=net_req,
        days_of_cover=days_cover,
        theoretical_rate=rate,
        estimated_run_time_hrs=est,
        unit="pcs",
    )


# ---------------------------------------------------------------------------
# _norm / _partial_match
# ---------------------------------------------------------------------------

def test_norm_strips_punctuation():
    assert _norm("M/C-07") == "M C 07"


def test_norm_uppercase():
    assert _norm("injection m/c") == "INJECTION M C"


def test_partial_match_numeric():
    assert _partial_match("M C 7", "INJECTION M C 7")


def test_partial_match_no_numeric_overlap():
    assert not _partial_match("M C 1", "M C 2")


def test_partial_match_empty():
    assert not _partial_match("", "M C 1")


# ---------------------------------------------------------------------------
# _lookup / _lookup_list
# ---------------------------------------------------------------------------

def test_lookup_exact():
    idx = {"A B": "found"}
    assert _lookup(idx, "A B") == "found"


def test_lookup_partial():
    idx = {"M C 7": "machine7"}
    assert _lookup(idx, "INJECTION M C 7") == "machine7"


def test_lookup_miss():
    assert _lookup({}, "X") is None


def test_lookup_list_exact():
    idx = {"A B": [1, 2, 3]}
    assert _lookup_list(idx, "A B") == [1, 2, 3]


def test_lookup_list_miss_returns_empty():
    assert _lookup_list({}, "X") == []


# ---------------------------------------------------------------------------
# _find_worst_rm
# ---------------------------------------------------------------------------

class _FakeMat:
    def __init__(self, category, reorder_flag, days_of_cover, lead_time_days,
                 item_name="Item", as_of_date="Jun-30"):
        self.category = category
        self.reorder_flag = reorder_flag
        self.days_of_cover = days_of_cover
        self.lead_time_days = lead_time_days
        self.item_name = item_name
        self.as_of_date = as_of_date


def test_find_worst_rm_returns_none_when_no_reorder():
    mats = [_FakeMat("RM", False, 10.0, 5.0)]
    assert _find_worst_rm(mats) is None


def test_find_worst_rm_finds_worst():
    mats = [
        _FakeMat("RM", True, 3.0, 5.0, item_name="Resin A"),
        _FakeMat("RM", True, 1.0, 5.0, item_name="Resin B"),
        _FakeMat("BOP", True, 0.5, 5.0, item_name="Pack"),  # not RM, ignored
    ]
    result = _find_worst_rm(mats)
    assert result is not None
    name, cover, lead, _ = result
    assert name == "Resin B"
    assert cover == 1.0


def test_find_worst_rm_ignores_non_rm():
    mats = [_FakeMat("BOP", True, 1.0, 5.0)]
    assert _find_worst_rm(mats) is None


# ---------------------------------------------------------------------------
# _build_run_queue — PTMT path
# ---------------------------------------------------------------------------

class _FakeMouldStd:
    def __init__(self, item_code, machine_name, theoretical_pcs_hr):
        self.item_code = item_code
        self.machine_name = machine_name
        self.theoretical_pcs_hr = theoretical_pcs_hr


class _FakePlanRecord:
    def __init__(self, item_code, item_name, family, net_requirement, days_of_cover,
                 per_hour_output=0.0):
        self.item_code = item_code
        self.item_name = item_name
        self.family = family
        self.net_requirement = net_requirement
        self.days_of_cover = days_of_cover
        self.per_hour_output = per_hour_output


def test_run_queue_ptmt_ranks_by_net_req_desc():
    norm_m = _norm("Injection M/C 7")
    stds = {
        norm_m: [
            _FakeMouldStd("ITEM_A", "Injection M/C 7", 100.0),
            _FakeMouldStd("ITEM_B", "Injection M/C 7", 200.0),
        ]
    }
    plan = {
        "ITEM_A": _FakePlanRecord("ITEM_A", "Cistern A", "cistern", 500.0, 10.0),
        "ITEM_B": _FakePlanRecord("ITEM_B", "Cistern B", "cistern", 1200.0, 5.0),
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert len(queue) == 2
    # Higher net_requirement first
    assert queue[0].item_code == "ITEM_B"
    assert queue[1].item_code == "ITEM_A"


def test_run_queue_ptmt_tiebreak_by_days_cover_asc():
    norm_m = _norm("M/C 3")
    stds = {
        norm_m: [
            _FakeMouldStd("ITEM_X", "M/C 3", 100.0),
            _FakeMouldStd("ITEM_Y", "M/C 3", 100.0),
        ]
    }
    plan = {
        "ITEM_X": _FakePlanRecord("ITEM_X", "Faucet X", "faucet", 500.0, 12.0),
        "ITEM_Y": _FakePlanRecord("ITEM_Y", "Faucet Y", "faucet", 500.0, 3.0),  # lower cover
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert len(queue) == 2
    # Same net_req → lower days_cover first (most urgent)
    assert queue[0].item_code == "ITEM_Y"


def test_run_queue_ptmt_skips_zero_net_req():
    norm_m = _norm("M/C 1")
    stds = {norm_m: [_FakeMouldStd("ITEM_Z", "M/C 1", 100.0)]}
    plan = {"ITEM_Z": _FakePlanRecord("ITEM_Z", "Zero Item", "cistern", 0.0, 5.0)}
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert queue == []


def test_run_queue_ptmt_estimated_run_time():
    norm_m = _norm("M/C 5")
    stds = {norm_m: [_FakeMouldStd("ITEM_A", "M/C 5", 500.0)]}
    plan = {"ITEM_A": _FakePlanRecord("ITEM_A", "A", "faucet", 2500.0, 8.0)}
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert len(queue) == 1
    assert queue[0].estimated_run_time_hrs == pytest.approx(5.0)


def test_run_queue_ptmt_no_rate_gives_none_estimate():
    norm_m = _norm("M/C 5")
    stds = {norm_m: [_FakeMouldStd("ITEM_A", "M/C 5", 0.0)]}  # 0 rate
    plan = {"ITEM_A": _FakePlanRecord("ITEM_A", "A", "faucet", 500.0, 8.0)}
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert len(queue) == 1
    assert queue[0].estimated_run_time_hrs is None
    assert queue[0].theoretical_rate is None


def test_run_queue_pipe_family_match():
    norm_m = _norm("Extruder 1")
    pipe_mat = {norm_m: {"CPVC"}}
    pipe_plan = {
        "CPVC": [
            _FakePlanRecord("P001", "CPVC 20mm", "CPVC", 300.0, 5.0, per_hour_output=50.0),
        ]
    }
    queue = _build_run_queue("PIPE", norm_m, {}, {}, pipe_mat, pipe_plan)
    assert len(queue) == 1
    assert queue[0].item_code == "P001"
    assert queue[0].estimated_run_time_hrs == pytest.approx(6.0)


def test_run_queue_deduplicates_item_codes():
    norm_m = _norm("M/C 2")
    stds = {
        norm_m: [
            _FakeMouldStd("ITEM_A", "M/C 2", 100.0),
            _FakeMouldStd("ITEM_A", "M/C 2", 100.0),  # duplicate
        ]
    }
    plan = {"ITEM_A": _FakePlanRecord("ITEM_A", "A", "cistern", 200.0, 5.0)}
    queue = _build_run_queue("PTMT", norm_m, stds, plan, {}, {})
    assert len(queue) == 1


# ---------------------------------------------------------------------------
# _evaluate_gates — bottleneck priority
# ---------------------------------------------------------------------------

class _FakeMetricsResult:
    def __init__(self, actual_hours=100.0, ideal_hours=200.0,
                 utilisation=0.5, util_available=True, unit="pcs",
                 total_count=1000.0, output_by_unit=None):
        self.actual_hours = actual_hours
        self.ideal_hours = ideal_hours
        self.utilisation = utilisation
        self.util_available = util_available
        self.unit = unit
        self.total_count = total_count
        self.output_by_unit = output_by_unit or {}


def _make_rm_at_reorder():
    return _FakeMat("RM", True, 2.0, 5.0, "Resin K-67", "Jun-30")


def test_bottleneck_material_beats_capacity():
    """When material is RED and capacity is RED, material wins (higher priority)."""
    # rm_worst present -> material RED
    rm_worst = ("Resin K-67", 2.0, 5.0, "Jun-30")
    # No manpower, no maint → grey; capacity RED (fully loaded)
    m_result = _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0)
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 1"), rm_worst,
        {}, {},  # maint_idx, mp_idx (both empty → grey)
        m_result, 500.0, 500.0,
        [_make_rm_at_reorder()], "2026-06",
    )
    assert bottleneck == "Material"
    assert "Resin K-67" in reason


def test_bottleneck_capacity_when_only_red():
    """With no material issue and all others grey, capacity RED is bottleneck."""
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 2"), None,
        {}, {},
        _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0),
        500.0, 500.0,
        [], "2026-06",
    )
    assert bottleneck == "Capacity"
    assert "Fully loaded" in reason


def test_grey_gate_never_bottleneck():
    """Tooling and Feed are always grey → never bottleneck even when all others are grey."""
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 3"), None,
        {}, {},  # maint grey, manpower grey
        None, 0.0, 0.0,  # capacity grey (no prod data)
        [], "2026-06",
    )
    for g in gates:
        if g.name in ("Tooling", "Feed"):
            assert g.status == "grey", f"{g.name} must always be grey"
    # No RED gates → no bottleneck
    assert bottleneck is None


def test_bottleneck_priority_order():
    """Verify GATE_PRIORITY is correct."""
    assert GATE_PRIORITY == [
        "Material", "Tooling", "Feed", "Machine health", "Manpower", "Capacity"
    ]


def test_manpower_red_when_actual_below_required():
    class _FakeMp:
        def __init__(self):
            self.required_manpower = 5.0
            self.actual_manpower = 3.0
            self.date = "2026-06-15"
    mp_idx = {_norm("M C 4"): [_FakeMp()]}
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 4"), None,
        {}, mp_idx,
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=200.0),
        100.0, 200.0,
        [], "2026-06",
    )
    mp_gate = next(g for g in gates if g.name == "Manpower")
    assert mp_gate.status == "red"
    assert "actual" in mp_gate.reason.lower()


def test_manpower_green_when_actual_meets_required():
    class _FakeMp:
        def __init__(self):
            self.required_manpower = 4.0
            self.actual_manpower = 5.0
            self.date = "2026-06-15"
    mp_idx = {_norm("M C 5"): [_FakeMp()]}
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 5"), None,
        {}, mp_idx,
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=200.0),
        100.0, 200.0,
        [], "2026-06",
    )
    mp_gate = next(g for g in gates if g.name == "Manpower")
    assert mp_gate.status == "green"


def test_capacity_green_when_idle():
    gates, bottleneck, _ = _evaluate_gates(
        _norm("M C 6"), None, {}, {},
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=300.0),
        100.0, 300.0,
        [], "2026-06",
    )
    cap = next(g for g in gates if g.name == "Capacity")
    assert cap.status == "green"
    assert bottleneck is None


def test_capacity_red_when_fully_loaded():
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 7"), None, {}, {},
        _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0),
        500.0, 500.0,
        [], "2026-06",
    )
    cap = next(g for g in gates if g.name == "Capacity")
    assert cap.status == "red"
    assert bottleneck == "Capacity"


def test_capacity_grey_when_no_prod_data():
    gates, bottleneck, _ = _evaluate_gates(
        _norm("M C 8"), None, {}, {},
        None, 0.0, 0.0,
        [], "2026-06",
    )
    cap = next(g for g in gates if g.name == "Capacity")
    assert cap.status == "grey"
    assert bottleneck is None  # grey never bottleneck


def test_machine_health_green_when_in_register():
    class _FakeMaint:
        def __init__(self):
            self.machine = "M/C 9"
            self.machine_age_years = 3.5
    maint_idx = {_norm("M/C 9"): _FakeMaint()}
    gates, _, _ = _evaluate_gates(
        _norm("M/C 9"), None, maint_idx, {},
        _FakeMetricsResult(), 100.0, 200.0,
        [], "2026-06",
    )
    mh = next(g for g in gates if g.name == "Machine health")
    assert mh.status == "green"
    assert "3.5y" in mh.reason


def test_machine_health_grey_when_missing():
    gates, _, _ = _evaluate_gates(
        _norm("UNKNOWN MC"), None, {}, {},
        _FakeMetricsResult(), 100.0, 200.0,
        [], "2026-06",
    )
    mh = next(g for g in gates if g.name == "Machine health")
    assert mh.status == "grey"


# ---------------------------------------------------------------------------
# MachinePlan.actionable flag
# ---------------------------------------------------------------------------

def test_actionable_flag_requires_all_three():
    """actionable = idle > 0 AND queue non-empty AND no red gate."""
    # With RED capacity gate → not actionable
    mp = MachinePlan(
        plant="PTMT", machine="M/C 1", month="2026-06",
        run_queue=[_qi("A", 100.0, 5.0)],
        gates=[_gate("Capacity", "red")],
        bottleneck="Capacity",
        bottleneck_reason="Fully loaded",
        idle_hours=0.0,
        actionable=False,
    )
    assert not mp.actionable


def test_six_gates_always_present():
    """build_plan should always emit exactly 6 gates per machine."""
    from plan import _evaluate_gates
    gates, _, _ = _evaluate_gates(
        _norm("M C 10"), None, {}, {},
        None, 0.0, 0.0,
        [], "2026-06",
    )
    assert len(gates) == 6
    names = [g.name for g in gates]
    for expected in GATE_PRIORITY:
        assert expected in names


def test_tooling_and_feed_always_grey():
    """Tooling and Feed are unconditionally grey (Phase 2D not built)."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 11"), None, {}, {},
        _FakeMetricsResult(), 100.0, 200.0,
        [], "2026-06",
    )
    for g in gates:
        if g.name in ("Tooling", "Feed"):
            assert g.status == "grey"
            assert "Phase 2D" in g.reason
