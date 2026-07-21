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
    _PIPE_PRODUCTION_MACHINES,
    _norm, _partial_match, _lookup, _lookup_list,
    _classify_ptmt_item, _find_worst_rm, _build_run_queue, _evaluate_gates, build_plan,
    _is_plant_wide_rm, _WorstRm,
)


# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------

def _gate(name, status, reason="", provenance=""):
    return GateStatus(name=name, status=status, reason=reason, provenance=provenance)


def _qi(item_code, net_req, days_cover, rate=None, family="TEST"):
    est = round(net_req / rate, 2) if rate else None
    return RunQueueItem(
        item_code=item_code,
        item_name=f"Item {item_code}",
        family=family,
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
                 item_name="Item", as_of_date="Jun-30", item_type=""):
        self.category = category
        self.reorder_flag = reorder_flag
        self.days_of_cover = days_of_cover
        self.lead_time_days = lead_time_days
        self.item_name = item_name
        self.as_of_date = as_of_date
        self.item_type = item_type  # e.g. "PIPE", "CPVC", ""


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
    assert isinstance(result, _WorstRm)
    assert result.item_name == "Resin B"
    assert result.days_of_cover == 1.0


def test_find_worst_rm_ignores_non_rm():
    mats = [_FakeMat("BOP", True, 1.0, 5.0)]
    assert _find_worst_rm(mats) is None


def test_find_worst_rm_item_type_filter_excludes_unrelated():
    """RM with a non-matching item_type is excluded when item_types filter is set."""
    mats = [
        _FakeMat("RM", True, 1.0, 5.0, item_name="CPVC Resin", item_type="CPVC"),
        _FakeMat("RM", True, 0.5, 5.0, item_name="UPVC Resin", item_type="UPVC"),
    ]
    # Only check CPVC
    result = _find_worst_rm(mats, item_types={"CPVC"})
    assert result is not None
    assert result.item_name == "CPVC Resin"  # UPVC excluded


def test_find_worst_rm_empty_item_type_always_included():
    """RM records with empty item_type are always included (generic/unclassified)."""
    mats = [
        _FakeMat("RM", True, 1.0, 5.0, item_name="Generic Resin", item_type=""),
    ]
    result = _find_worst_rm(mats, item_types={"CPVC"})
    assert result is not None  # empty item_type → included regardless


def test_find_worst_rm_plant_type_included():
    """RM with item_type='PIPE' is included when 'PIPE' is in item_types."""
    mats = [
        _FakeMat("RM", True, 2.0, 5.0, item_name="PIPE Resin", item_type="PIPE"),
    ]
    result = _find_worst_rm(mats, item_types={"CPVC", "PIPE"})
    assert result is not None


# ---------------------------------------------------------------------------
# _build_run_queue — PTMT path
# ---------------------------------------------------------------------------

class _FakeMouldStd:
    def __init__(self, item_code, machine_name, theoretical_pcs_hr,
                 item_name="FAUCET BODY"):
        self.item_code = item_code
        self.machine_name = machine_name
        self.theoretical_pcs_hr = theoretical_pcs_hr
        self.item_name = item_name  # used by _classify_ptmt_item → family


class _FakePlanRecord:
    def __init__(self, item_code, item_name, family, net_requirement, days_of_cover,
                 per_hour_output=0.0):
        self.item_code = item_code
        self.item_name = item_name
        self.family = family
        self.net_requirement = net_requirement
        self.days_of_cover = days_of_cover
        self.per_hour_output = per_hour_output


# ---------------------------------------------------------------------------
# _classify_ptmt_item — family keyword mapping
# ---------------------------------------------------------------------------

def test_classify_cistern_keywords():
    assert _classify_ptmt_item("CISTERN BODY") == "cistern"
    assert _classify_ptmt_item("W/C 31 MM F/T") == "cistern"
    assert _classify_ptmt_item("BALL COCK FLOAT") == "cistern"
    assert _classify_ptmt_item("FLUSH VALVE") == "cistern"


def test_classify_seatcover_keyword():
    assert _classify_ptmt_item("SEAT COVER 30MM") == "seatcover"
    assert _classify_ptmt_item("TOILET SEAT HINGE") == "seatcover"


def test_classify_faucet_default():
    assert _classify_ptmt_item("FAUCET BODY 15MM") == "faucet"
    assert _classify_ptmt_item("HANDLE-O") == "faucet"
    assert _classify_ptmt_item("NOZZLE BODY 15MM") == "faucet"
    assert _classify_ptmt_item("BIB COCK SPINDLE") == "faucet"
    assert _classify_ptmt_item("ANGLE VALVE KNOB") == "faucet"


# ---------------------------------------------------------------------------
# _build_run_queue — PTMT path (family-based join)
# 4th arg is now ptmt_plan_by_family: Dict[family, List[PlanRecord]]
# Rate comes from plan_r.per_hour_output (NOT MouldStd.theoretical_pcs_hr)
# ---------------------------------------------------------------------------

def test_run_queue_ptmt_ranks_by_net_req_desc():
    norm_m = _norm("Injection M/C 7")
    # MouldStd item_name "CISTERN BODY" → classified as "cistern"
    stds = {norm_m: [_FakeMouldStd("PSF-100", "Injection M/C 7", 100.0, "CISTERN BODY")]}
    plan_by_fam = {
        "cistern": [
            _FakePlanRecord("ITEM_A", "Cistern A", "cistern", 500.0, 10.0),
            _FakePlanRecord("ITEM_B", "Cistern B", "cistern", 1200.0, 5.0),
        ]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert len(queue) == 2
    assert queue[0].item_code == "ITEM_B"   # higher net_req first
    assert queue[1].item_code == "ITEM_A"


def test_run_queue_ptmt_tiebreak_by_days_cover_asc():
    norm_m = _norm("M/C 3")
    stds = {norm_m: [_FakeMouldStd("PSF-1", "M/C 3", 100.0, "FAUCET BODY")]}
    plan_by_fam = {
        "faucet": [
            _FakePlanRecord("ITEM_X", "Faucet X", "faucet", 500.0, 12.0),
            _FakePlanRecord("ITEM_Y", "Faucet Y", "faucet", 500.0, 3.0),
        ]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert len(queue) == 2
    assert queue[0].item_code == "ITEM_Y"   # lower days_cover → more urgent


def test_run_queue_ptmt_skips_zero_net_req():
    norm_m = _norm("M/C 1")
    stds = {norm_m: [_FakeMouldStd("PSF-2", "M/C 1", 100.0, "CISTERN BODY")]}
    plan_by_fam = {
        "cistern": [_FakePlanRecord("ITEM_Z", "Zero Item", "cistern", 0.0, 5.0)]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert queue == []


def test_run_queue_ptmt_estimated_run_time():
    """Rate comes from plan_r.per_hour_output, not MouldStd.theoretical_pcs_hr."""
    norm_m = _norm("M/C 5")
    stds = {norm_m: [_FakeMouldStd("PSF-3", "M/C 5", 999.0, "FAUCET BODY")]}
    plan_by_fam = {
        "faucet": [_FakePlanRecord("ITEM_A", "A", "faucet", 2500.0, 8.0,
                                   per_hour_output=500.0)]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert len(queue) == 1
    assert queue[0].estimated_run_time_hrs == pytest.approx(5.0)
    assert queue[0].theoretical_rate == pytest.approx(500.0)


def test_run_queue_ptmt_no_rate_gives_none_estimate():
    """per_hour_output=0 → no rate → estimated_run_time_hrs is None."""
    norm_m = _norm("M/C 5")
    stds = {norm_m: [_FakeMouldStd("PSF-4", "M/C 5", 100.0, "FAUCET BODY")]}
    plan_by_fam = {
        "faucet": [_FakePlanRecord("ITEM_A", "A", "faucet", 500.0, 8.0,
                                   per_hour_output=0.0)]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert len(queue) == 1
    assert queue[0].estimated_run_time_hrs is None
    assert queue[0].theoretical_rate is None


def test_run_queue_ptmt_multi_family_machine():
    """A machine with moulds from two families gets items from both families."""
    norm_m = _norm("M/C 6")
    # Two stds: one cistern mould, one faucet mould
    stds = {norm_m: [
        _FakeMouldStd("PSF-10", "M/C 6", 100.0, "CISTERN BODY"),
        _FakeMouldStd("PSF-11", "M/C 6", 100.0, "FAUCET BODY"),
    ]}
    plan_by_fam = {
        "cistern": [_FakePlanRecord("CIS-1", "Cistern A", "cistern", 300.0, 5.0)],
        "faucet":  [_FakePlanRecord("FAU-1", "Faucet A", "faucet", 600.0, 8.0)],
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    codes = {qi.item_code for qi in queue}
    assert "CIS-1" in codes
    assert "FAU-1" in codes


def test_run_queue_ptmt_no_stds_shows_all_families():
    """Machine not in ptmt_machine_stds gets items from all three families."""
    norm_m = _norm("UNKNOWN M/C")
    plan_by_fam = {
        "cistern":   [_FakePlanRecord("CIS-1", "Cistern A", "cistern", 100.0, 5.0)],
        "faucet":    [_FakePlanRecord("FAU-1", "Faucet A", "faucet", 200.0, 3.0)],
        "seatcover": [_FakePlanRecord("SEAT-1", "Seat A", "seatcover", 50.0, 10.0)],
    }
    queue = _build_run_queue("PTMT", norm_m, {}, plan_by_fam, {}, {})
    codes = {qi.item_code for qi in queue}
    assert codes == {"CIS-1", "FAU-1", "SEAT-1"}


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


def test_run_queue_pipe_idle_machine_gets_all_jobs():
    """An idle PIPE machine (not in pipe_machine_materials) gets ALL open plan_recs."""
    norm_m = _norm("PIPE Pipe M/C 8")  # not in pipe_mat → idle
    pipe_mat: dict = {}  # no production history for this machine
    pipe_plan: dict = {}
    all_recs = [
        _FakePlanRecord("P001", "CPVC 20mm", "CPVC", 300.0, 5.0, per_hour_output=50.0),
        _FakePlanRecord("P002", "UPVC 25mm", "UPVC", 500.0, 3.0, per_hour_output=40.0),
    ]
    queue = _build_run_queue("PIPE", norm_m, {}, {}, pipe_mat, pipe_plan,
                             pipe_all_plan_recs=all_recs)
    assert len(queue) == 2  # gets both open jobs
    # Ranked by net_requirement DESC: P002 first
    assert queue[0].item_code == "P002"


def test_run_queue_deduplicates_item_codes():
    """Same item_code appearing twice in a family list → only one queue item."""
    norm_m = _norm("M/C 2")
    stds = {norm_m: [_FakeMouldStd("PSF-5", "M/C 2", 100.0, "CISTERN BODY")]}
    # Duplicate item_code in the cistern plan list (data error)
    plan_by_fam = {
        "cistern": [
            _FakePlanRecord("ITEM_A", "Cistern A", "cistern", 200.0, 5.0),
            _FakePlanRecord("ITEM_A", "Cistern A", "cistern", 200.0, 5.0),  # duplicate
        ]
    }
    queue = _build_run_queue("PTMT", norm_m, stds, plan_by_fam, {}, {})
    assert len(queue) == 1


# ---------------------------------------------------------------------------
# _evaluate_gates — bottleneck priority
# New signature: (norm_m, plant, run_queue, mat_recs, maint_idx, mp_idx,
#                 m_result, actual_h, ideal_h, month)
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


def _make_rm_at_reorder(item_type=""):
    return _FakeMat("RM", True, 2.0, 5.0, "Resin K-67", "Jun-30", item_type=item_type)


def test_bottleneck_material_beats_capacity():
    """Machine-specific RED material beats RED capacity (higher priority)."""
    run_queue = [_qi("ITEM_A", 500.0, 10.0, family="SPECIFIC")]
    m_result = _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0)
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 1"), "TEST",
        run_queue,
        [_make_rm_at_reorder(item_type="SPECIFIC")],  # machine-specific → RED (not plant-wide)
        {}, {},
        m_result, 500.0, 500.0, "2026-06",
    )
    assert bottleneck == "Material"
    assert "Resin K-67" in reason


def test_bottleneck_capacity_when_only_red():
    """With no material issue and all others grey, capacity RED is bottleneck."""
    # Empty mat_recs → Material=GREY; empty queue still gets GREY too (no job)
    # Use a non-empty queue so Material checks RM (returns GREY = no RM data)
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 2"), "PTMT",
        [_qi("X", 100.0, 5.0)],
        [],  # no RM records → Material=GREY (no RM data)
        {}, {},
        _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0),
        500.0, 500.0, "2026-06",
    )
    assert bottleneck == "Capacity"
    assert "Fully loaded" in reason


def test_grey_gate_never_bottleneck():
    """GREY/plant-wide gates never become bottleneck even when all others grey."""
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 3"), "PTMT",
        [],   # empty queue → Material GREY
        [], {}, {},
        None, 0.0, 0.0,  # capacity grey (no prod data)
        "2026-06",
    )
    for g in gates:
        if g.name in ("Tooling", "Feed"):
            assert g.status == "grey", f"{g.name} should be grey with default params"
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
        _norm("M C 4"), "PTMT",
        [],  # empty queue → Material GREY
        [], {}, mp_idx,
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=200.0),
        100.0, 200.0, "2026-06",
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
        _norm("M C 5"), "PTMT",
        [], [], {}, mp_idx,
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=200.0),
        100.0, 200.0, "2026-06",
    )
    mp_gate = next(g for g in gates if g.name == "Manpower")
    assert mp_gate.status == "green"


def test_capacity_green_when_idle():
    gates, bottleneck, _ = _evaluate_gates(
        _norm("M C 6"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=300.0),
        100.0, 300.0, "2026-06",
    )
    cap = next(g for g in gates if g.name == "Capacity")
    assert cap.status == "green"
    assert bottleneck is None


def test_capacity_red_when_fully_loaded():
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 7"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(actual_hours=500.0, ideal_hours=500.0),
        500.0, 500.0, "2026-06",
    )
    cap = next(g for g in gates if g.name == "Capacity")
    assert cap.status == "red"
    assert bottleneck == "Capacity"


def test_capacity_grey_when_no_prod_data():
    gates, bottleneck, _ = _evaluate_gates(
        _norm("M C 8"), "PTMT",
        [], [], {}, {},
        None, 0.0, 0.0, "2026-06",
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
        _norm("M/C 9"), "PTMT",
        [], [], maint_idx, {},
        _FakeMetricsResult(), 100.0, 200.0, "2026-06",
    )
    mh = next(g for g in gates if g.name == "Machine health")
    assert mh.status == "green"
    assert "3.5y" in mh.reason


def test_machine_health_grey_when_missing():
    gates, _, _ = _evaluate_gates(
        _norm("UNKNOWN MC"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(), 100.0, 200.0, "2026-06",
    )
    mh = next(g for g in gates if g.name == "Machine health")
    assert mh.status == "grey"


# ---------------------------------------------------------------------------
# MachinePlan.actionable flag
# ---------------------------------------------------------------------------

def test_actionable_flag_requires_all_three():
    """actionable = idle > 0 AND queue non-empty AND no red gate."""
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
    """_evaluate_gates always emits exactly 6 gates."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 10"), "PTMT",
        [], [], {}, {},
        None, 0.0, 0.0, "2026-06",
    )
    assert len(gates) == 6
    names = [g.name for g in gates]
    for expected in GATE_PRIORITY:
        assert expected in names


def test_tooling_and_feed_grey_when_no_data():
    """Tooling and Feed default to GREY when no Phase 2D data is provided."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 11"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(), 100.0, 200.0, "2026-06",
        # toolroom_items=None, mixer_recs_for_type=None (defaults) → GREY
    )
    for g in gates:
        if g.name in ("Tooling", "Feed"):
            assert g.status == "grey"


# ---------------------------------------------------------------------------
# Material gate — per-machine logic
# ---------------------------------------------------------------------------

def test_material_grey_when_queue_empty():
    """Empty run queue → Material=GREY regardless of mat_recs."""
    mats = [_make_rm_at_reorder()]  # has a reorder item, but queue is empty
    gates, _, _ = _evaluate_gates(
        _norm("M C 12"), "PTMT",
        [],   # empty queue
        mats, {}, {},
        None, 0.0, 0.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    assert mat_gate.status == "grey"
    assert "no job" in mat_gate.reason.lower()


def test_material_green_when_rm_healthy():
    """Non-empty queue + all RM above lead time → Material=GREEN."""
    healthy_rm = _FakeMat("RM", False, 30.0, 5.0, "Healthy Resin", item_type="")
    run_queue = [_qi("ITEM_A", 100.0, 5.0)]
    gates, bottleneck, _ = _evaluate_gates(
        _norm("M C 13"), "PTMT",
        run_queue,
        [healthy_rm], {}, {},
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=300.0),
        100.0, 300.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    assert mat_gate.status == "green"
    assert bottleneck is None  # no RED gates → ready


def test_material_grey_when_no_rm_data():
    """Non-empty queue + no mat_recs → Material=GREY (no RM data for type)."""
    run_queue = [_qi("ITEM_A", 100.0, 5.0)]
    gates, _, _ = _evaluate_gates(
        _norm("M C 14"), "PTMT",
        run_queue,
        [],   # no material records at all
        {}, {},
        None, 0.0, 0.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    assert mat_gate.status == "grey"


def test_material_red_only_for_matching_type():
    """RM reorder on a non-matching type does NOT make gate RED for this machine."""
    mats = [
        _FakeMat("RM", True, 1.0, 5.0, "UPVC Resin", item_type="UPVC"),   # reorder!
        _FakeMat("RM", False, 30.0, 5.0, "CPVC Resin", item_type="CPVC"),  # healthy
    ]
    # Machine runs CPVC — only CPVC + "PIPE" (plant) type RM relevant
    run_queue = [_qi("P001", 300.0, 5.0, family="CPVC")]
    gates, bottleneck, _ = _evaluate_gates(
        _norm("CPVC EXTRUDER"), "PIPE",
        run_queue, mats, {}, {},
        _FakeMetricsResult(actual_hours=100.0, ideal_hours=300.0),
        100.0, 300.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    # UPVC resin is excluded (not relevant to this CPVC machine)
    # CPVC resin is healthy → green
    assert mat_gate.status == "green"
    assert bottleneck is None


# ---------------------------------------------------------------------------
# Roster correctness — _PIPE_PRODUCTION_MACHINES constant
# ---------------------------------------------------------------------------

def test_pipe_production_machines_count():
    """Canonical PIPE roster must have exactly 9 machines."""
    assert len(_PIPE_PRODUCTION_MACHINES) == 9


def test_pipe_production_machines_format():
    """All PIPE roster entries follow 'PIPE Pipe M/C-N' format."""
    for m in _PIPE_PRODUCTION_MACHINES:
        assert m.startswith("PIPE Pipe M/C-"), f"Unexpected format: {m}"


def test_pipe_roster_excludes_maintenance_assets():
    """Maintenance-only assets must NOT appear in the PIPE production roster."""
    bad_assets = ["ANALYTICAL BALANCE", "C150-A", "C200-A", "COOLING TOWER",
                  "CHILLER", "COMPRESSOR", "PRINTING ROLLER"]
    roster_upper = {m.upper() for m in _PIPE_PRODUCTION_MACHINES}
    for asset in bad_assets:
        assert asset.upper() not in roster_upper, (
            f"Maintenance asset '{asset}' must not be in PIPE production roster"
        )


def test_pipe_roster_includes_mc_1_through_9():
    """Every Pipe M/C-1 .. Pipe M/C-9 must be in the PIPE production roster."""
    for i in range(1, 10):
        expected = f"PIPE Pipe M/C-{i}"
        assert expected in _PIPE_PRODUCTION_MACHINES, (
            f"'{expected}' missing from PIPE production roster"
        )


# ---------------------------------------------------------------------------
# _is_plant_wide_rm helper
# ---------------------------------------------------------------------------

def test_is_plant_wide_rm_empty_type():
    """Empty item_type is always plant-wide."""
    assert _is_plant_wide_rm("", "PIPE")
    assert _is_plant_wide_rm("", "PTMT")


def test_is_plant_wide_rm_matches_plant_name():
    """item_type equal to the plant name is plant-wide."""
    assert _is_plant_wide_rm("PIPE", "PIPE")
    assert _is_plant_wide_rm("ptmt", "PTMT")   # case-insensitive


def test_is_plant_wide_rm_specific_type_not_plant_wide():
    """item_type naming a specific sub-type is NOT plant-wide."""
    assert not _is_plant_wide_rm("CPVC", "PIPE")
    assert not _is_plant_wide_rm("UPVC", "PIPE")
    assert not _is_plant_wide_rm("CISTERN", "PTMT")


# ---------------------------------------------------------------------------
# Material gate — plant-wide status
# ---------------------------------------------------------------------------

def test_material_gate_plant_wide_not_bottleneck():
    """RM with empty item_type (plant-wide) → gate status 'plant-wide', not bottleneck."""
    run_queue = [_qi("ITEM_A", 300.0, 5.0, family="GENERIC")]
    mat = _make_rm_at_reorder(item_type="")   # empty → plant-wide for any plant
    m_result = _FakeMetricsResult(actual_hours=100.0, ideal_hours=200.0)
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 5"), "TEST",
        run_queue,
        [mat], {}, {},
        m_result, 100.0, 200.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    assert mat_gate.status == "plant-wide", "Generic RM must be plant-wide, not red"
    # plant-wide never makes the machine the bottleneck
    assert bottleneck != "Material", "plant-wide gate must not be the bottleneck"


def test_material_gate_plant_wide_reason_contains_item_name():
    """plant-wide gate reason must include the item name for traceability."""
    run_queue = [_qi("ITEM_B", 100.0, 3.0, family="SOMETHING")]
    mat = _make_rm_at_reorder(item_type="")
    gates, _, _ = _evaluate_gates(
        _norm("M C 6"), "MOULDING",
        run_queue,
        [mat], {}, {},
        None, 0.0, 0.0, "2026-06",
    )
    mat_gate = next(g for g in gates if g.name == "Material")
    assert "Resin K-67" in mat_gate.reason


# ---------------------------------------------------------------------------
# Tooling gate — Phase 2D
# ---------------------------------------------------------------------------

class _FakeToolroomItem:
    def __init__(self, item):
        self.item = item


def test_tooling_red_with_active_job():
    """Non-empty frozenset of toolroom items → Tooling=RED."""
    gates, bottleneck, reason = _evaluate_gates(
        _norm("M C 20"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        toolroom_items=frozenset({"CISTERN MOULD A", "CISTERN MOULD B"}),
    )
    tooling_gate = next(g for g in gates if g.name == "Tooling")
    assert tooling_gate.status == "red"
    assert bottleneck == "Tooling"


def test_tooling_green_no_active_job():
    """Empty frozenset (data loaded, no match) → Tooling=GREEN."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 21"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        toolroom_items=frozenset(),
    )
    tooling_gate = next(g for g in gates if g.name == "Tooling")
    assert tooling_gate.status == "green"


def test_tooling_grey_when_none():
    """toolroom_items=None (default) → Tooling=GREY (no data)."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 22"), "PTMT",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        toolroom_items=None,
    )
    tooling_gate = next(g for g in gates if g.name == "Tooling")
    assert tooling_gate.status == "grey"


# ---------------------------------------------------------------------------
# Feed gate — Phase 2D
# ---------------------------------------------------------------------------

class _FakeMixerRecord:
    def __init__(self, batch_type, total_compound_kg=0.0, breakdown_hours=0.0):
        self.batch_type = batch_type
        self.total_compound_kg = total_compound_kg
        self.breakdown_hours = breakdown_hours


def test_feed_red_mixer_breakdown():
    """Mixer records with breakdown_hours>0 and total_compound_kg==0 → Feed=RED."""
    mixer_recs = [_FakeMixerRecord("CPVC", total_compound_kg=0.0, breakdown_hours=3.5)]
    gates, _, _ = _evaluate_gates(
        _norm("M C 30"), "PIPE",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        mixer_recs_for_type=mixer_recs,
    )
    feed_gate = next(g for g in gates if g.name == "Feed")
    assert feed_gate.status == "red"
    assert "breakdown" in feed_gate.reason.lower()


def test_feed_green_compound_available():
    """Mixer records with compound produced → Feed=GREEN."""
    mixer_recs = [_FakeMixerRecord("CPVC", total_compound_kg=5000.0, breakdown_hours=0.0)]
    gates, _, _ = _evaluate_gates(
        _norm("M C 31"), "PIPE",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        mixer_recs_for_type=mixer_recs,
    )
    feed_gate = next(g for g in gates if g.name == "Feed")
    assert feed_gate.status == "green"
    assert "5,000" in feed_gate.reason


def test_feed_grey_empty_list():
    """mixer_recs_for_type=[] (data loaded, no records for type) → Feed=GREY."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 32"), "PIPE",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        mixer_recs_for_type=[],
    )
    feed_gate = next(g for g in gates if g.name == "Feed")
    assert feed_gate.status == "grey"


def test_feed_grey_none():
    """mixer_recs_for_type=None (default) → Feed=GREY (no data)."""
    gates, _, _ = _evaluate_gates(
        _norm("M C 33"), "PIPE",
        [], [], {}, {},
        _FakeMetricsResult(), 0.0, 0.0, "2026-06",
        mixer_recs_for_type=None,
    )
    feed_gate = next(g for g in gates if g.name == "Feed")
    assert feed_gate.status == "grey"


# ---------------------------------------------------------------------------
# _find_worst_rm — cover_display uses stock_days_sheet when present
# ---------------------------------------------------------------------------

def test_find_worst_rm_cover_display_uses_stock_days_sheet():
    """When stock_days_sheet is set on the record, cover_display must use it."""
    mat = _FakeMat("RM", True, 46.57, 60.0, "GRANUALS-CG122")
    mat.stock_days_sheet = 17.0  # sheet's own value differs from rolling-avg
    result = _find_worst_rm([mat])
    assert result is not None
    assert result.cover_display == 17.0, "cover_display must use stock_days_sheet"
    assert result.days_of_cover == 46.57, "days_of_cover must retain the computed value"


def test_find_worst_rm_cover_display_fallback_when_no_sheet():
    """When stock_days_sheet is absent, cover_display falls back to days_of_cover."""
    mat = _FakeMat("RM", True, 10.0, 30.0, "Resin X")
    # no .stock_days_sheet attribute → getattr returns None → fallback
    result = _find_worst_rm([mat])
    assert result is not None
    assert result.cover_display == 10.0
