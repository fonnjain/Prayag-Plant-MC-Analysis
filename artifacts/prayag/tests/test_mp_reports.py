"""
Tests for mp_reports.py — covers:
  FIX 1  Weight columns (production weight ≠ material req)
  FIX 2  Schedule-based rows (WEEK/SHIFT populated from ScheduleResult)
  FIX 3  Rate fallback tier populated in ItemResult
  FIX 4  Report-11A-D machine-group filter
  NEW    consolidated_plan_bytes() smoke test (7 tabs, no crash)
  NEW    _mp_schedule_from_session / _mp_fitting_schedule_from_session
         do NOT re-run engine when a pre-computed result is supplied
"""
import io
import pytest
from unittest.mock import patch, MagicMock

# ── Minimal stubs so the tests never need a live DB ──────────────────────────

def _make_item(
    item_code="CPVC-100",
    material="CPVC",
    qty_pcs=1000.0,
    wt_per_pc=0.3,
    material_kg=312.0,   # 1000 × 0.3 × 1.04
    rate_kg_per_hr=150.0,
    machine="M/C-1",
    rate_fallback_tier="item",
):
    from mp_engine import ItemResult, AssignedPortion
    assignment_hrs = material_kg / rate_kg_per_hr
    a = AssignedPortion(
        machine=machine,
        hrs=round(assignment_hrs, 3),
        material_kg=material_kg,
        qty_pcs=qty_pcs,
    )
    return ItemResult(
        item_code=item_code,
        raw_code=item_code,
        material=material,
        qty_pcs=qty_pcs,
        weight_per_pc_kg=wt_per_pc,
        material_kg=material_kg,
        fresh_compound_kg=material_kg * 0.75,
        pulverizer_kg=material_kg * 0.25,
        rate_kg_per_hr=rate_kg_per_hr,
        rate_estimated=(rate_fallback_tier != "item"),
        rate_fallback_tier=rate_fallback_tier,
        machine_hrs=round(assignment_hrs, 3),
        capable_machines=[machine],
        assignments=[a],
        has_weight=True,
        has_machine=True,
    )


def _make_engine_result(items=None):
    from mp_engine import EngineResult, CoverageGaps, PlanTotals, MachineLoad
    items = items or [_make_item()]
    ml = MachineLoad(
        machine="M/C-1", capacity_hrs=500.0,
        assigned_hrs=sum(it.machine_hrs for it in items),
        utilisation_pct=50.0, machine_days=13.0,
        material_kg=sum(it.material_kg for it in items),
        fresh_compound_kg=sum(it.fresh_compound_kg for it in items),
        pulverizer_kg=sum(it.pulverizer_kg for it in items),
        staffing_ok=True, operators_ot=2, support_w=1,
    )
    return EngineResult(
        segment="PIPE",
        effective_month="2026-07",
        items=items,
        machine_loads=[ml],
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]
        ),
        totals=PlanTotals(
            total_qty_pcs=sum(it.qty_pcs for it in items),
            total_material_kg=sum(it.material_kg for it in items),
            total_fresh_compound_kg=sum(it.fresh_compound_kg for it in items),
            total_pulverizer_kg=sum(it.pulverizer_kg for it in items),
            routable_material_kg=sum(it.material_kg for it in items),
            routable_fresh_compound_kg=sum(it.fresh_compound_kg for it in items),
            routable_pulverizer_kg=sum(it.pulverizer_kg for it in items),
        ),
        baseline_machine_loads=[ml],
        params_used={"waste_pct": 4.0, "pulverizer_pct": 25.0},
        effective_costs={"CPVC": 120.0},
        cost_by_material={"CPVC": 28080.0},
        n_unpriced=0,
    )


def _make_schedule_result(item_code="CPVC-100", machine="M/C-1"):
    from mp_scheduler import ScheduleResult, ShiftBlock, WeekFillRow
    blocks = [
        ShiftBlock(
            week=1, day=1, machine=machine, shift="DAY",
            item_code=item_code, raw_code=item_code, material="CPVC",
            planned_hours=10.0, excess_hours=0.0, origin_week=1, is_idle=False,
        ),
        ShiftBlock(
            week=1, day=1, machine=machine, shift="NIGHT",
            item_code=item_code, raw_code=item_code, material="CPVC",
            planned_hours=10.0, excess_hours=0.5, origin_week=1, is_idle=False,
        ),
        # Idle block — should be excluded from report rows
        ShiftBlock(
            week=1, day=2, machine=machine, shift="DAY",
            item_code="", raw_code="", material="",
            planned_hours=10.0, excess_hours=0.0, origin_week=0, is_idle=True,
        ),
    ]
    wf = WeekFillRow(
        week=1, machine=machine, capacity_hrs=125.0,
        scheduled_hrs=19.5, idle_hrs=10.0, utilisation_pct=15.6,
        changeovers=1, excess_kg=75.0, origin_breakdown={1: 19.5},
    )
    return ScheduleResult(
        segment="PIPE",
        effective_month="2026-07",
        blocks=blocks,
        weekly_fill=[wf],
        unfinished=[],
        total_capacity_hrs=500.0,
        total_scheduled_hrs=19.5,
        total_idle_hrs=10.0,
        total_excess_kg=75.0,
        total_changeovers=1,
        week_days=[6, 6, 6, 7],
        params_used={"min_run_block_hours": 5.0, "week_days": [6, 6, 6, 7]},
    )


# ── FIX 3: Weight column correctness ─────────────────────────────────────────

def test_production_weight_no_waste():
    """Production Wt (col 8) = qty_pcs × wt/pc; NOT equal to material_kg."""
    import mp_reports as r
    item = _make_item(qty_pcs=1000.0, wt_per_pc=0.3, material_kg=312.0)
    result = _make_engine_result([item])
    rows = r._build_rows_from_assignments(result)
    assert rows, "Expected at least one row"
    row = rows[0]
    prod_wt   = row[8]    # Production Wt (KG)
    mat_req   = row[9]    # Material Req (KG)
    expected_prod = round(1000.0 * 0.3, 1)    # 300.0
    expected_mat  = round(312.0, 1)            # 312.0  (from assignment.material_kg)
    assert abs(prod_wt - expected_prod) < 0.11, f"prod_wt={prod_wt}, expected≈{expected_prod}"
    assert abs(mat_req - expected_mat) < 0.11, f"mat_req={mat_req}, expected≈{expected_mat}"
    assert prod_wt != mat_req, "Production Wt and Material Req must differ (waste factor)"


def test_weight_columns_differ_when_waste_applied():
    """Verify the two weight columns differ by exactly the waste factor."""
    import mp_reports as r
    item = _make_item(qty_pcs=500.0, wt_per_pc=0.5, material_kg=260.0)  # 4% waste
    result = _make_engine_result([item])
    rows = r._build_rows_from_assignments(result)
    row = rows[0]
    prod_wt = row[8]
    mat_req = row[9]
    ratio = mat_req / prod_wt if prod_wt > 0 else 0
    # ratio should be ≈ 1.04 (waste factor)
    assert abs(ratio - 1.04) < 0.01, f"mat_req/prod_wt ratio={ratio:.4f}, expected≈1.04"


# ── FIX 1: Schedule-based rows ────────────────────────────────────────────────

def test_schedule_rows_have_week_and_shift():
    """When ScheduleResult is provided, WEEK and SHIFT columns must be populated."""
    import mp_reports as r
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    rows = r._build_rows_from_schedule(result, schedule)
    assert rows, "Expected at least one scheduled row"
    for row in rows:
        week_val  = row[1]   # WEEK
        shift_val = row[2]   # SHIFT
        assert week_val.startswith("W"), f"WEEK should start with 'W', got {week_val!r}"
        assert shift_val in ("DAY", "NIGHT"), f"SHIFT should be DAY/NIGHT, got {shift_val!r}"


def test_schedule_rows_exclude_idle():
    """Idle blocks must not appear as data rows."""
    import mp_reports as r
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    rows = r._build_rows_from_schedule(result, schedule)
    # Our stub schedule has 2 non-idle blocks (day1 DAY + day1 NIGHT) and 1 idle
    assert len(rows) == 2, f"Expected 2 non-idle rows, got {len(rows)}"


def test_schedule_rows_production_hours_correct():
    """Running Hours in schedule row = planned_hours - excess_hours."""
    import mp_reports as r
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    rows = r._build_rows_from_schedule(result, schedule)
    # Night block: planned=10.0, excess=0.5 → prod_hrs=9.5
    # Day block: planned=10.0, excess=0.0 → prod_hrs=10.0
    hrs_values = {row[7] for row in rows}   # Running Hours col
    assert 9.5 in hrs_values, f"Expected 9.5 hr (night block prod hrs), got {hrs_values}"
    assert 10.0 in hrs_values, f"Expected 10.0 hr (day block prod hrs), got {hrs_values}"


def test_dispatch_uses_schedule_when_provided():
    """_build_rows dispatches to schedule path when schedule arg given."""
    import mp_reports as r
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    rows_scheduled  = r._build_rows(result, schedule=schedule)
    rows_assignment = r._build_rows(result, schedule=None)
    # With schedule: DATE = day number (int); without: DATE = month string
    assert rows_scheduled[0][0] == 1, "Scheduled DATE should be day number 1"
    assert isinstance(rows_assignment[0][0], str), "Assignment DATE should be string"


# ── FIX 2 (11A-D) machine filter ─────────────────────────────────────────────

def test_machine_filter_excludes_other_machines():
    """report_11x_bytes filters to only the specified machine group."""
    import mp_reports as r
    items = [
        _make_item("CPVC-100", machine="M/C-1", qty_pcs=1000.0),
        _make_item("UPVC-200", material="UPVC", machine="M/C-3",
                   qty_pcs=500.0, wt_per_pc=0.4, material_kg=208.0, rate_kg_per_hr=140.0),
    ]
    result = _make_engine_result(items)
    rows_A = r._build_rows_from_assignments(result, machine_filter={"M/C-1", "M/C-2"})
    rows_B = r._build_rows_from_assignments(result, machine_filter={"M/C-3", "M/C-4"})
    mc_in_A = {row[3] for row in rows_A}   # MACHINE NAME col
    mc_in_B = {row[3] for row in rows_B}
    assert "M/C-1" in mc_in_A and "M/C-3" not in mc_in_A
    assert "M/C-3" in mc_in_B and "M/C-1" not in mc_in_B


# ── FIX 4: rate_fallback_tier ────────────────────────────────────────────────

def test_rate_fallback_tier_seeded():
    from mp_engine import _get_rate
    ph = {"CPVC-100": 150.0}
    mat_avg = {"CPVC": 140.0}
    rate, estimated, tier = _get_rate("CPVC-100", "CPVC", ph, mat_avg, 120.0)
    assert tier == "item"
    assert not estimated
    assert rate == 150.0


def test_rate_fallback_tier_mat_avg():
    from mp_engine import _get_rate
    ph = {}
    mat_avg = {"CPVC": 140.0}
    rate, estimated, tier = _get_rate("CPVC-999", "CPVC", ph, mat_avg, 120.0)
    assert tier == "mat_avg"
    assert estimated
    assert rate == 140.0


def test_rate_fallback_tier_overall_avg():
    from mp_engine import _get_rate
    ph = {}
    mat_avg = {}
    rate, estimated, tier = _get_rate("SWR-999", "SWR", ph, mat_avg, 120.0)
    assert tier == "overall_avg"
    assert estimated
    assert rate == 120.0


# ── Report generation smoke tests ─────────────────────────────────────────────

def test_report_11_bytes_no_schedule():
    """report_11_bytes without schedule runs and returns valid xlsx bytes."""
    import mp_reports as r
    result = _make_engine_result()
    data = r.report_11_bytes(result, schedule=None)
    assert data[:4] == b"PK\x03\x04", "xlsx should start with PK header (ZIP)"
    assert len(data) > 3000


def test_report_11_bytes_with_schedule():
    """report_11_bytes with schedule returns valid xlsx (schedule-mode rows)."""
    import mp_reports as r
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    data = r.report_11_bytes(result, schedule=schedule)
    assert data[:4] == b"PK\x03\x04"
    assert len(data) > 3000


def test_report_11x_bytes_group_filter():
    """report_11x_bytes with group A returns valid xlsx."""
    import mp_reports as r
    result = _make_engine_result()
    data = r.report_11x_bytes(result, "A")
    assert data[:4] == b"PK\x03\x04"


def test_consolidated_plan_bytes_all_tabs():
    """consolidated_plan_bytes returns a workbook with all 7 tabs, no crash."""
    import mp_reports as r
    from openpyxl import load_workbook
    result   = _make_engine_result([
        _make_item("CPVC-100", rate_fallback_tier="item"),
        _make_item("SWR-200", material="SWR", rate_fallback_tier="mat_avg",
                   qty_pcs=800.0, wt_per_pc=0.25, material_kg=208.0,
                   rate_kg_per_hr=130.0, machine="M/C-2"),
        _make_item("AGRI-300", material="AGRI", rate_fallback_tier="overall_avg",
                   qty_pcs=600.0, wt_per_pc=0.2, material_kg=124.8,
                   rate_kg_per_hr=110.0, machine="M/C-3"),
    ])
    schedule = _make_schedule_result()
    data = r.consolidated_plan_bytes(
        engine_result=result,
        fitting_result=None,
        schedule_result=schedule,
    )
    assert data[:4] == b"PK\x03\x04"
    wb = load_workbook(io.BytesIO(data))
    assert len(wb.sheetnames) == 7, f"Expected 7 tabs, got {wb.sheetnames}"
    expected_prefixes = ["1.", "2.", "3.", "4.", "5.", "6.", "7."]
    for prefix, name in zip(expected_prefixes, wb.sheetnames):
        assert name.startswith(prefix), f"Tab {name!r} should start with {prefix!r}"


def test_consolidated_machine_load_tab_has_demand_and_scheduled_columns():
    """Tab '2. Machine Load' must have both Demand and Scheduled column headers."""
    import mp_reports as r
    from openpyxl import load_workbook
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    data = r.consolidated_plan_bytes(
        engine_result=result,
        fitting_result=None,
        schedule_result=schedule,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]
    # Row 4 is the column-header row; collect all header text
    headers = {
        ws.cell(row=4, column=c).value
        for c in range(1, 20)
        if ws.cell(row=4, column=c).value
    }
    assert any("Demand" in (h or "") for h in headers), (
        f"Expected a 'Demand' column in tab 2 headers; got: {headers}"
    )
    assert any("Scheduled" in (h or "") for h in headers), (
        f"Expected a 'Scheduled' column in tab 2 headers; got: {headers}"
    )
    # Old label must be gone — no plain "Assigned (hrs)" or "Utilisation %"
    assert "Assigned (hrs)" not in headers, "Old 'Assigned (hrs)' label must be renamed to 'Demand (hrs)'"
    assert "Utilisation %" not in headers,  "Old 'Utilisation %' label must be renamed to 'Demand util %'"


def test_consolidated_machine_load_scheduled_util_never_exceeds_100():
    """Scheduled util % must be ≤ 100% for every machine row in tab '2. Machine Load'.

    The scheduler enforces a capacity ceiling so this must always hold.  A value
    > 100 here would contradict the Capacity-Feasible Plan — the whole point of
    adding the scheduled column.
    """
    import mp_reports as r
    from openpyxl import load_workbook
    from mp_scheduler import ScheduleResult, WeekFillRow, ShiftBlock

    # Build a schedule where one machine runs at exactly full capacity (100%)
    # and another is partially used (50%).
    wf1 = WeekFillRow(
        week=1, machine="M/C-1", capacity_hrs=500.0,
        scheduled_hrs=500.0, idle_hrs=0.0, utilisation_pct=100.0,
        changeovers=0, excess_kg=0.0, origin_breakdown={1: 500.0},
    )
    wf2 = WeekFillRow(
        week=1, machine="M/C-2", capacity_hrs=400.0,
        scheduled_hrs=200.0, idle_hrs=200.0, utilisation_pct=50.0,
        changeovers=0, excess_kg=0.0, origin_breakdown={1: 200.0},
    )
    schedule = ScheduleResult(
        segment="PIPE", effective_month="2026-08",
        blocks=[], weekly_fill=[wf1, wf2], unfinished=[],
        total_capacity_hrs=900.0, total_scheduled_hrs=700.0,
        total_idle_hrs=200.0, total_excess_kg=0.0,
        total_changeovers=0, week_days=[6, 6, 6, 7], params_used={},
    )
    from mp_engine import EngineResult, MachineLoad, CoverageGaps, PlanTotals
    ml1 = MachineLoad(
        machine="M/C-1", capacity_hrs=500.0,
        assigned_hrs=750.0,  # demand > capacity (150%)
        utilisation_pct=150.0, machine_days=25.0,
        material_kg=10000.0, fresh_compound_kg=7500.0, pulverizer_kg=2500.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    ml2 = MachineLoad(
        machine="M/C-2", capacity_hrs=400.0,
        assigned_hrs=590.0,  # demand > capacity (147.5%)
        utilisation_pct=147.5, machine_days=20.0,
        material_kg=8000.0, fresh_compound_kg=6000.0, pulverizer_kg=2000.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    item = _make_item()
    result = EngineResult(
        segment="PIPE", effective_month="2026-08",
        items=[item], machine_loads=[ml1, ml2],
        coverage_gaps=CoverageGaps(no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]),
        totals=PlanTotals(
            total_qty_pcs=1000.0, total_material_kg=18000.0,
            total_fresh_compound_kg=13500.0, total_pulverizer_kg=4500.0,
            routable_material_kg=18000.0, routable_fresh_compound_kg=13500.0,
            routable_pulverizer_kg=4500.0,
        ),
        baseline_machine_loads=[ml1, ml2],
        params_used={}, effective_costs={}, cost_by_material={}, n_unpriced=0,
    )

    data = r.consolidated_plan_bytes(
        engine_result=result, fitting_result=None, schedule_result=schedule,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]

    # Locate "Scheduled util %" column
    hdr_row = 4
    sched_util_col = None
    for c in range(1, 20):
        v = ws.cell(row=hdr_row, column=c).value or ""
        if "Scheduled util" in v:
            sched_util_col = c
            break
    assert sched_util_col is not None, "Could not find 'Scheduled util %' column"

    # Collect scheduled util values from data rows (row 5 onwards, stop at TOTAL)
    sched_utils = []
    for row in range(hdr_row + 1, hdr_row + 20):
        machine_val = ws.cell(row=row, column=1).value
        if machine_val == "TOTAL" or machine_val is None:
            break
        util_val = ws.cell(row=row, column=sched_util_col).value
        if util_val is not None:
            sched_utils.append(float(util_val))

    assert sched_utils, "No scheduled-util data rows found"
    for u in sched_utils:
        assert u <= 100.0 + 0.01, (
            f"Scheduled util % {u} exceeds 100% — breaks capacity guarantee"
        )

    # Also verify demand util IS above 100% for M/C-1 (so the two columns are distinct)
    demand_util_col = None
    for c in range(1, 20):
        v = ws.cell(row=hdr_row, column=c).value or ""
        if "Demand util" in v:
            demand_util_col = c
            break
    assert demand_util_col is not None
    demand_utils = []
    for row in range(hdr_row + 1, hdr_row + 20):
        machine_val = ws.cell(row=row, column=1).value
        if machine_val == "TOTAL" or machine_val is None:
            break
        v = ws.cell(row=row, column=demand_util_col).value
        if v is not None:
            demand_utils.append(float(v))
    assert any(u > 100.0 for u in demand_utils), (
        "Expected at least one machine with demand util > 100% in this test fixture"
    )


def test_consolidated_machine_load_scheduled_values_match_schedule_result():
    """Scheduled hrs in tab 2 must equal the sum of weekly_fill.scheduled_hrs per machine.

    This pins the exact numbers so that the consolidated report and the
    Capacity-Feasible Plan (which derives sched_load_by_mc the same way) are
    guaranteed to agree.
    """
    import mp_reports as r
    from openpyxl import load_workbook
    from mp_scheduler import ScheduleResult, WeekFillRow

    # Two machines, two weeks of weekly_fill each
    # M/C-1: W1=120.0 + W2=130.0 = 250.0 total scheduled hrs
    # M/C-2: W1=80.0  + W2=70.0  = 150.0 total scheduled hrs
    wf_rows = [
        WeekFillRow(week=1, machine="M/C-1", capacity_hrs=250.0,
                    scheduled_hrs=120.0, idle_hrs=130.0, utilisation_pct=48.0,
                    changeovers=0, excess_kg=0.0, origin_breakdown={1: 120.0}),
        WeekFillRow(week=2, machine="M/C-1", capacity_hrs=250.0,
                    scheduled_hrs=130.0, idle_hrs=120.0, utilisation_pct=52.0,
                    changeovers=0, excess_kg=0.0, origin_breakdown={2: 130.0}),
        WeekFillRow(week=1, machine="M/C-2", capacity_hrs=200.0,
                    scheduled_hrs=80.0, idle_hrs=120.0, utilisation_pct=40.0,
                    changeovers=0, excess_kg=0.0, origin_breakdown={1: 80.0}),
        WeekFillRow(week=2, machine="M/C-2", capacity_hrs=200.0,
                    scheduled_hrs=70.0, idle_hrs=130.0, utilisation_pct=35.0,
                    changeovers=0, excess_kg=0.0, origin_breakdown={2: 70.0}),
    ]
    schedule = ScheduleResult(
        segment="PIPE", effective_month="2026-08",
        blocks=[], weekly_fill=wf_rows, unfinished=[],
        total_capacity_hrs=900.0, total_scheduled_hrs=400.0,
        total_idle_hrs=500.0, total_excess_kg=0.0, total_changeovers=0,
        week_days=[6, 6, 6, 7], params_used={},
    )
    from mp_engine import EngineResult, MachineLoad, CoverageGaps, PlanTotals
    ml1 = MachineLoad(
        machine="M/C-1", capacity_hrs=500.0, assigned_hrs=300.0,
        utilisation_pct=60.0, machine_days=25.0,
        material_kg=5000.0, fresh_compound_kg=3750.0, pulverizer_kg=1250.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    ml2 = MachineLoad(
        machine="M/C-2", capacity_hrs=400.0, assigned_hrs=200.0,
        utilisation_pct=50.0, machine_days=20.0,
        material_kg=4000.0, fresh_compound_kg=3000.0, pulverizer_kg=1000.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    item = _make_item()
    result = EngineResult(
        segment="PIPE", effective_month="2026-08",
        items=[item], machine_loads=[ml1, ml2],
        coverage_gaps=CoverageGaps(no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]),
        totals=PlanTotals(
            total_qty_pcs=1000.0, total_material_kg=9000.0,
            total_fresh_compound_kg=6750.0, total_pulverizer_kg=2250.0,
            routable_material_kg=9000.0, routable_fresh_compound_kg=6750.0,
            routable_pulverizer_kg=2250.0,
        ),
        baseline_machine_loads=[ml1, ml2],
        params_used={}, effective_costs={}, cost_by_material={}, n_unpriced=0,
    )
    data = r.consolidated_plan_bytes(
        engine_result=result, fitting_result=None, schedule_result=schedule,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]

    # Locate Scheduled (hrs) column
    hdr_row = 4
    sched_col = None
    for c in range(1, 20):
        v = ws.cell(row=hdr_row, column=c).value or ""
        if v == "Scheduled (hrs)":
            sched_col = c
            break
    assert sched_col is not None, "Could not find 'Scheduled (hrs)' column"

    # Read machine → scheduled hrs from the report
    sched_in_report = {}
    for row in range(hdr_row + 1, hdr_row + 20):
        mc = ws.cell(row=row, column=1).value
        if mc == "TOTAL" or mc is None:
            break
        sched_in_report[mc] = float(ws.cell(row=row, column=sched_col).value or 0)

    assert abs(sched_in_report.get("M/C-1", -1) - 250.0) < 0.1, (
        f"M/C-1 scheduled hrs should be 250.0 (120+130), got {sched_in_report.get('M/C-1')}"
    )
    assert abs(sched_in_report.get("M/C-2", -1) - 150.0) < 0.1, (
        f"M/C-2 scheduled hrs should be 150.0 (80+70), got {sched_in_report.get('M/C-2')}"
    )


def test_consolidated_machine_load_note_present():
    """Tab '2. Machine Load' row 3 must contain the demand-vs-scheduled clarifying note."""
    import mp_reports as r
    from openpyxl import load_workbook
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    data = r.consolidated_plan_bytes(
        engine_result=result, fitting_result=None, schedule_result=schedule,
    )
    wb   = load_workbook(io.BytesIO(data))
    ws   = wb["2. Machine Load"]
    note = ws["A3"].value or ""
    assert "100%" in note, f"Note should mention 100%; got: {note!r}"
    assert "Demand" in note or "demand" in note, f"Note should mention demand; got: {note!r}"
    assert "Scheduled" in note or "scheduled" in note, f"Note should mention scheduled; got: {note!r}"
    assert "Capacity-Feasible" in note or "capacity" in note.lower(), (
        f"Note should reference the Capacity-Feasible Plan; got: {note!r}"
    )


def test_consolidated_other_tabs_unchanged():
    """The six tabs other than '2. Machine Load' must still be present and named correctly."""
    import mp_reports as r
    from openpyxl import load_workbook
    result   = _make_engine_result()
    schedule = _make_schedule_result()
    data = r.consolidated_plan_bytes(
        engine_result=result, fitting_result=None, schedule_result=schedule,
    )
    wb = load_workbook(io.BytesIO(data))
    assert len(wb.sheetnames) == 7, f"Still expect 7 tabs; got {wb.sheetnames}"
    for prefix in ["1.", "3.", "4.", "5.", "6.", "7."]:
        assert any(n.startswith(prefix) for n in wb.sheetnames), (
            f"Tab starting with {prefix!r} missing; sheets: {wb.sheetnames}"
        )


def test_consolidated_plan_bytes_no_data_graceful():
    """consolidated_plan_bytes handles all-None inputs without crashing."""
    import mp_reports as r
    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None, schedule_result=None
    )
    assert data[:4] == b"PK\x03\x04"


def test_rate_fallback_note_only_when_estimated():
    """Rate fallback note section should NOT appear when all items have seeded rates."""
    import mp_reports as r
    from openpyxl import load_workbook
    result = _make_engine_result([
        _make_item("CPVC-100", rate_fallback_tier="item"),
    ])
    data = r.report_11_bytes(result, schedule=None)
    wb   = load_workbook(io.BytesIO(data))
    ws   = wb.active
    # Look for the fallback note title text anywhere in the sheet
    texts = [
        str(ws.cell(row=r, column=1).value or "")
        for r in range(1, ws.max_row + 1)
    ]
    fallback_found = any("Rate Fallback" in t for t in texts)
    assert not fallback_found, "No fallback note expected when all items have seeded rates"


def test_rate_fallback_note_present_when_estimated():
    """Rate fallback note section SHOULD appear when some items use estimated rates."""
    import mp_reports as r
    from openpyxl import load_workbook
    result = _make_engine_result([
        _make_item("SWR-999", material="SWR", rate_fallback_tier="overall_avg",
                   qty_pcs=500.0, wt_per_pc=0.3, material_kg=156.0,
                   rate_kg_per_hr=120.0, machine="M/C-1"),
    ])
    data = r.report_11_bytes(result, schedule=None)
    wb   = load_workbook(io.BytesIO(data))
    ws   = wb.active
    texts = [
        str(ws.cell(row=r_i, column=1).value or "")
        for r_i in range(1, ws.max_row + 1)
    ]
    fallback_found = any("Rate Fallback" in t for t in texts)
    assert fallback_found, "Fallback note should appear when estimated items exist"


# ── Fitting machine section in tab '2. Machine Load' ─────────────────────────

def _make_fitting_result(machines=None):
    """Minimal FittingEngineResult with the given machine load list."""
    from mp_engine import (
        FittingEngineResult, FittingItemResult, FittingAssignedPortion,
        MachineLoad, CoverageGaps, PlanTotals,
    )
    machines = machines or [
        MachineLoad(
            machine="FM-1", capacity_hrs=300.0,
            assigned_hrs=180.0, utilisation_pct=60.0, machine_days=15.0,
            material_kg=3000.0, fresh_compound_kg=2250.0, pulverizer_kg=750.0,
            staffing_ok=True, operators_ot=0, support_w=1,
        )
    ]
    item = FittingItemResult(
        item_code="CPVC-F-100", raw_code="CPVC-F-100",
        material="CPVC", qty_pcs=500.0, weight_per_pc_kg=0.15,
        material_kg=78.0, fresh_compound_kg=58.5, pulverizer_kg=19.5,
        pcs_per_hr=120.0, rate_estimated=False, machine_hrs=4.17,
        cavity=4.0, cycle_time_sec=120.0, num_cycles=125.0,
        capable_machines=[machines[0].machine],
        route_estimated=False,
        assignments=[FittingAssignedPortion(
            machine=machines[0].machine, hrs=4.17, qty_pcs=500.0, material_kg=78.0
        )],
        has_weight=True, has_machine=True,
    )
    return FittingEngineResult(
        segment="FITTING", effective_month="2026-08",
        items=[item],
        machine_loads=machines,
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]
        ),
        totals=PlanTotals(
            total_qty_pcs=500.0, total_material_kg=78.0,
            total_fresh_compound_kg=58.5, total_pulverizer_kg=19.5,
            routable_material_kg=78.0, routable_fresh_compound_kg=58.5,
            routable_pulverizer_kg=19.5,
        ),
        baseline_machine_loads=machines,
        params_used={}, n_route_estimated=0, n_unroutable=0,
    )


def _make_fitting_schedule(machine="FM-1", scheduled_hrs=200.0, capacity_hrs=300.0):
    """Minimal fitting ScheduleResult with one WeekFillRow."""
    from mp_scheduler import ScheduleResult, WeekFillRow
    util = round(scheduled_hrs / capacity_hrs * 100, 1) if capacity_hrs > 0 else 0.0
    wf = WeekFillRow(
        week=1, machine=machine, capacity_hrs=capacity_hrs,
        scheduled_hrs=scheduled_hrs, idle_hrs=capacity_hrs - scheduled_hrs,
        utilisation_pct=util, changeovers=0, excess_kg=0.0,
        origin_breakdown={1: scheduled_hrs},
    )
    return ScheduleResult(
        segment="FITTING", effective_month="2026-08",
        blocks=[], weekly_fill=[wf], unfinished=[],
        total_capacity_hrs=capacity_hrs, total_scheduled_hrs=scheduled_hrs,
        total_idle_hrs=capacity_hrs - scheduled_hrs, total_excess_kg=0.0,
        total_changeovers=0, week_days=[6, 6, 6, 7], params_used={},
    )


def test_consolidated_tab2_has_fitting_section_when_fitting_result_present():
    """Tab '2. Machine Load' must contain a 'FITTING MACHINES' section header
    when a fitting_result is provided."""
    import mp_reports as r
    from openpyxl import load_workbook

    fitting = _make_fitting_result()
    data = r.consolidated_plan_bytes(
        engine_result=None,
        fitting_result=fitting,
        schedule_result=None,
        fitting_schedule=None,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]

    # Scan all cells for the section header text
    found = False
    for row in ws.iter_rows():
        for cell in row:
            if "FITTING MACHINES" in str(cell.value or ""):
                found = True
                break
    assert found, "Expected 'FITTING MACHINES' section header in tab '2. Machine Load'"


def test_consolidated_tab2_fitting_machine_row_present():
    """Tab '2. Machine Load' must show the fitting machine name in a data row."""
    import mp_reports as r
    from openpyxl import load_workbook

    fitting = _make_fitting_result()
    data = r.consolidated_plan_bytes(
        engine_result=None,
        fitting_result=fitting,
        schedule_result=None,
        fitting_schedule=None,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]

    col1_values = [
        ws.cell(row=row, column=1).value
        for row in range(1, ws.max_row + 1)
    ]
    assert "FM-1" in col1_values, (
        f"Expected fitting machine 'FM-1' in col-1 of tab 2; got: {col1_values}"
    )


def test_consolidated_tab2_fitting_scheduled_util_never_exceeds_100():
    """Scheduled util % for fitting machines must be ≤ 100% in tab '2. Machine Load'.

    The fitting scheduler enforces a capacity ceiling, so this invariant must hold
    regardless of how high the demand util is.
    """
    import mp_reports as r
    from openpyxl import load_workbook
    from mp_engine import MachineLoad

    # Fitting machine with demand > capacity (150%) but schedule capped at 100%
    fm = MachineLoad(
        machine="FM-1", capacity_hrs=300.0,
        assigned_hrs=450.0, utilisation_pct=150.0, machine_days=20.0,
        material_kg=4000.0, fresh_compound_kg=3000.0, pulverizer_kg=1000.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    fitting = _make_fitting_result(machines=[fm])
    # Fitting schedule: scheduler caps at capacity (300 hrs = 100%)
    fit_sched = _make_fitting_schedule(machine="FM-1",
                                       scheduled_hrs=300.0, capacity_hrs=300.0)

    data = r.consolidated_plan_bytes(
        engine_result=None,
        fitting_result=fitting,
        schedule_result=None,
        fitting_schedule=fit_sched,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]

    # Locate "Scheduled util %" column (header row 4)
    hdr_row = 4
    sched_util_col = None
    for c in range(1, 20):
        v = ws.cell(row=hdr_row, column=c).value or ""
        if "Scheduled util" in v:
            sched_util_col = c
            break
    assert sched_util_col is not None, "Could not find 'Scheduled util %' column"

    # Collect scheduled util values from ALL data rows (skip header/section rows)
    sched_utils = []
    for row in range(hdr_row + 1, hdr_row + 40):
        machine_val = ws.cell(row=row, column=1).value
        if machine_val is None:
            continue
        if machine_val in ("TOTAL", "FITTING TOTAL", "FITTING MACHINES",
                           "(pipe engine result not available)"):
            continue
        util_val = ws.cell(row=row, column=sched_util_col).value
        if util_val is not None:
            try:
                sched_utils.append(float(util_val))
            except (TypeError, ValueError):
                pass

    assert sched_utils, "No fitting-machine scheduled-util data rows found"
    for u in sched_utils:
        assert u <= 100.0 + 0.01, (
            f"Fitting scheduled util % {u} exceeds 100% — breaks capacity guarantee"
        )

    # Also confirm demand util IS above 100% (columns are genuinely distinct)
    demand_util_col = None
    for c in range(1, 20):
        v = ws.cell(row=hdr_row, column=c).value or ""
        if "Demand util" in v:
            demand_util_col = c
            break
    assert demand_util_col is not None, "Could not find 'Demand util %' column"
    demand_utils = []
    for row in range(hdr_row + 1, hdr_row + 40):
        machine_val = ws.cell(row=row, column=1).value
        if machine_val in (None, "TOTAL", "FITTING TOTAL", "FITTING MACHINES"):
            continue
        v = ws.cell(row=row, column=demand_util_col).value
        if v is not None:
            try:
                demand_utils.append(float(v))
            except (TypeError, ValueError):
                pass
    assert any(u > 100.0 for u in demand_utils), (
        "Expected demand util > 100% for FM-1 (150% fixture) — columns must be distinct"
    )


def test_consolidated_tab2_fitting_no_data_graceful():
    """Tab '2. Machine Load' shows a graceful message when fitting_result is None."""
    import mp_reports as r
    from openpyxl import load_workbook

    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None,
        schedule_result=None, fitting_schedule=None,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["2. Machine Load"]
    # Should still have the FITTING MACHINES section header
    found_header = False
    for row in ws.iter_rows():
        for cell in row:
            if "FITTING MACHINES" in str(cell.value or ""):
                found_header = True
                break
    assert found_header, "Section header 'FITTING MACHINES' should always be present"


# ── Fitting machines in Tab 3: Weekly Fill ───────────────────────────────────

def test_consolidated_tab3_fitting_section_header_always_present():
    """Tab '3. Weekly Fill' must always contain a 'FITTING MACHINES' section header."""
    import mp_reports as r
    from openpyxl import load_workbook

    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None,
        schedule_result=None, fitting_schedule=None,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["3. Weekly Fill"]
    found = any(
        "FITTING MACHINES" in str(cell.value or "")
        for row in ws.iter_rows()
        for cell in row
    )
    assert found, "Expected 'FITTING MACHINES' section header in tab '3. Weekly Fill'"


def test_consolidated_tab3_fitting_rows_appear_when_fitting_schedule_supplied():
    """Tab '3. Weekly Fill' must show fitting machine rows when fitting_schedule is given."""
    import mp_reports as r
    from openpyxl import load_workbook

    fit_sched = _make_fitting_schedule(machine="FM-1", scheduled_hrs=200.0, capacity_hrs=300.0)
    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None,
        schedule_result=None, fitting_schedule=fit_sched,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["3. Weekly Fill"]

    # The machine name "FM-1" must appear in column 2 (Machine col) somewhere
    col2_values = [ws.cell(row=row, column=2).value for row in range(1, ws.max_row + 1)]
    assert "FM-1" in col2_values, (
        f"Expected fitting machine 'FM-1' in column 2 of tab '3. Weekly Fill'; "
        f"got: {col2_values}"
    )


def test_consolidated_tab3_fitting_scheduled_hrs_correct():
    """Scheduled hrs in fitting row must match fitting_schedule.weekly_fill.scheduled_hrs."""
    import mp_reports as r
    from openpyxl import load_workbook

    fit_sched = _make_fitting_schedule(machine="FM-2", scheduled_hrs=175.0, capacity_hrs=300.0)
    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None,
        schedule_result=None, fitting_schedule=fit_sched,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["3. Weekly Fill"]

    # Find the row where Machine col == "FM-2" and read Scheduled (hrs) (col 4)
    sched_val = None
    for row in range(1, ws.max_row + 1):
        if ws.cell(row=row, column=2).value == "FM-2":
            sched_val = ws.cell(row=row, column=4).value
            break
    assert sched_val is not None, "Could not find FM-2 row in tab '3. Weekly Fill'"
    assert abs(float(sched_val) - 175.0) < 0.1, (
        f"Scheduled hrs for FM-2 should be 175.0; got {sched_val}"
    )


def test_consolidated_tab3_no_fitting_schedule_graceful():
    """Tab '3. Weekly Fill' shows a graceful message when fitting_schedule is None."""
    import mp_reports as r
    from openpyxl import load_workbook

    data = r.consolidated_plan_bytes(
        engine_result=None, fitting_result=None,
        schedule_result=None, fitting_schedule=None,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["3. Weekly Fill"]
    all_values = [
        str(cell.value or "")
        for row in ws.iter_rows()
        for cell in row
    ]
    assert any("no fitting schedule available" in v for v in all_values), (
        "Expected '(no fitting schedule available)' message in tab '3. Weekly Fill' "
        f"when fitting_schedule is None; cells: {[v for v in all_values if v]}"
    )


def test_consolidated_tab3_both_pipe_and_fitting_sections():
    """Tab '3. Weekly Fill' shows both pipe rows and fitting rows when both schedules present."""
    import mp_reports as r
    from openpyxl import load_workbook

    pipe_sched = _make_schedule_result(machine="M/C-1")
    fit_sched  = _make_fitting_schedule(machine="FM-1", scheduled_hrs=150.0, capacity_hrs=300.0)
    data = r.consolidated_plan_bytes(
        engine_result=_make_engine_result(),
        fitting_result=None,
        schedule_result=pipe_sched,
        fitting_schedule=fit_sched,
    )
    wb = load_workbook(io.BytesIO(data))
    ws = wb["3. Weekly Fill"]

    col2_values = [ws.cell(row=row, column=2).value for row in range(1, ws.max_row + 1)]
    assert "M/C-1" in col2_values, "Pipe machine 'M/C-1' should appear in tab 3"
    assert "FM-1" in col2_values,  "Fitting machine 'FM-1' should appear in tab 3"


# ── No-double-engine-run regression ──────────────────────────────────────────

def _minimal_run_payload():
    """Minimal payload dict matching the DemandItem / FittingDemandItem field sets
    that _mp_schedule_from_session / _mp_fitting_schedule_from_session expect."""
    return {
        "effective_month": "2026-08",
        "segment": "PIPE",
        # Only DemandItem fields: item_code, raw_code, material, qty_pcs, week_qty,
        # first_requested_week
        "demand": [
            {
                "item_code": "PIPE-001", "raw_code": "PIPE-001",
                "material": "CPVC", "qty_pcs": 500.0,
                "week_qty": {"1": 500.0},
                "first_requested_week": 0,
            }
        ],
        # Only FittingDemandItem fields: item_code, raw_code, material, qty_pcs
        "fitting_demand": [
            {
                "item_code": "FIT-001", "raw_code": "FIT-001",
                "material": "CPVC", "qty_pcs": 200.0,
            }
        ],
    }


def _mock_session(run_id="test-run-42"):
    """Return a MagicMock that behaves like a Flask session dict with mp2_run_id set."""
    s = MagicMock()
    s.get.side_effect = lambda key, default=None: run_id if key == "mp2_run_id" else default
    return s


def test_schedule_helper_does_not_rerun_pipe_engine_when_result_supplied():
    """_mp_schedule_from_session must NOT invoke run_engine when engine_result is given.

    This prevents a double engine-run (and associated latency) on every
    Consolidated Plan / ZIP report download that has already computed the pipe result.
    The fix: passing engine_result to the helper skips the internal
    _mp2_result_from_session() call that would otherwise re-run run_engine.
    """
    import app as appmod

    precomputed = _make_engine_result()

    with patch("app.session", _mock_session()), \
         patch.object(appmod, "_mp2_load_run",
                      return_value=_minimal_run_payload()), \
         patch("mp_engine.run_engine") as mock_run_engine, \
         patch("mp_model.get_downtime_affecting_month", return_value=[]), \
         patch("mp_scheduler.run_shift_schedule", return_value=None):

        appmod._mp_schedule_from_session(engine_result=precomputed)

        assert mock_run_engine.call_count == 0, (
            f"run_engine must NOT be called when engine_result is pre-supplied; "
            f"got {mock_run_engine.call_count} call(s)"
        )


def test_fitting_schedule_helper_does_not_rerun_fitting_engine_when_result_supplied():
    """_mp_fitting_schedule_from_session must NOT invoke run_fitting_engine when
    fitting_result is given.

    This prevents a double fitting-engine-run on every Consolidated Plan / ZIP
    report download that has already computed the fitting result.
    The fix: passing fitting_result to the helper skips the internal
    _mp3_fitting_result_from_session() call that would otherwise re-run run_fitting_engine.
    """
    import app as appmod

    precomputed = _make_fitting_result()

    with patch("app.session", _mock_session()), \
         patch.object(appmod, "_mp2_load_run",
                      return_value=_minimal_run_payload()), \
         patch("mp_engine.run_fitting_engine") as mock_run_fitting, \
         patch("mp_model.get_downtime_affecting_month", return_value=[]), \
         patch("mp_scheduler.run_fitting_schedule", return_value=None):

        appmod._mp_fitting_schedule_from_session(fitting_result=precomputed)

        assert mock_run_fitting.call_count == 0, (
            f"run_fitting_engine must NOT be called when fitting_result is "
            f"pre-supplied; got {mock_run_fitting.call_count} call(s)"
        )


# ── Flask route integration tests ────────────────────────────────────────────

def _make_both_results():
    """Return (engine_result, fitting_result, pipe_schedule, fitting_schedule)
    with a fitting machine that is distinct from the pipe machine."""
    from mp_engine import (
        EngineResult, FittingEngineResult, FittingItemResult,
        FittingAssignedPortion, MachineLoad, CoverageGaps, PlanTotals,
    )
    from mp_scheduler import ScheduleResult, WeekFillRow

    # ── Pipe side ──
    pipe_ml = MachineLoad(
        machine="M/C-1", capacity_hrs=500.0,
        assigned_hrs=250.0, utilisation_pct=50.0, machine_days=25.0,
        material_kg=5000.0, fresh_compound_kg=3750.0, pulverizer_kg=1250.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    item = _make_item(machine="M/C-1")
    pipe_result = EngineResult(
        segment="PIPE", effective_month="2026-07",
        items=[item], machine_loads=[pipe_ml],
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]
        ),
        totals=PlanTotals(
            total_qty_pcs=item.qty_pcs, total_material_kg=item.material_kg,
            total_fresh_compound_kg=item.fresh_compound_kg,
            total_pulverizer_kg=item.pulverizer_kg,
            routable_material_kg=item.material_kg,
            routable_fresh_compound_kg=item.fresh_compound_kg,
            routable_pulverizer_kg=item.pulverizer_kg,
        ),
        baseline_machine_loads=[pipe_ml],
        params_used={}, effective_costs={}, cost_by_material={}, n_unpriced=0,
    )
    pipe_wf = WeekFillRow(
        week=1, machine="M/C-1", capacity_hrs=500.0,
        scheduled_hrs=250.0, idle_hrs=250.0, utilisation_pct=50.0,
        changeovers=0, excess_kg=0.0, origin_breakdown={1: 250.0},
    )
    pipe_schedule = ScheduleResult(
        segment="PIPE", effective_month="2026-07",
        blocks=[], weekly_fill=[pipe_wf], unfinished=[],
        total_capacity_hrs=500.0, total_scheduled_hrs=250.0,
        total_idle_hrs=250.0, total_excess_kg=0.0, total_changeovers=0,
        week_days=[6, 6, 6, 7], params_used={},
    )

    # ── Fitting side ──
    fit_ml = MachineLoad(
        machine="FM-ROUTE-1", capacity_hrs=300.0,
        assigned_hrs=180.0, utilisation_pct=60.0, machine_days=15.0,
        material_kg=3000.0, fresh_compound_kg=2250.0, pulverizer_kg=750.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    fit_item = FittingItemResult(
        item_code="CPVC-F-100", raw_code="CPVC-F-100",
        material="CPVC", qty_pcs=500.0, weight_per_pc_kg=0.15,
        material_kg=78.0, fresh_compound_kg=58.5, pulverizer_kg=19.5,
        pcs_per_hr=120.0, rate_estimated=False, machine_hrs=4.17,
        cavity=4.0, cycle_time_sec=120.0, num_cycles=125.0,
        capable_machines=["FM-ROUTE-1"], route_estimated=False,
        assignments=[FittingAssignedPortion(
            machine="FM-ROUTE-1", hrs=4.17, qty_pcs=500.0, material_kg=78.0,
        )],
        has_weight=True, has_machine=True,
    )
    fitting_result = FittingEngineResult(
        segment="FITTING", effective_month="2026-07",
        items=[fit_item], machine_loads=[fit_ml],
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[]
        ),
        totals=PlanTotals(
            total_qty_pcs=500.0, total_material_kg=78.0,
            total_fresh_compound_kg=58.5, total_pulverizer_kg=19.5,
            routable_material_kg=78.0, routable_fresh_compound_kg=58.5,
            routable_pulverizer_kg=19.5,
        ),
        baseline_machine_loads=[fit_ml],
        params_used={}, n_route_estimated=0, n_unroutable=0,
    )
    fit_wf = WeekFillRow(
        week=1, machine="FM-ROUTE-1", capacity_hrs=300.0,
        scheduled_hrs=180.0, idle_hrs=120.0, utilisation_pct=60.0,
        changeovers=0, excess_kg=0.0, origin_breakdown={1: 180.0},
    )
    fitting_schedule = ScheduleResult(
        segment="FITTING", effective_month="2026-07",
        blocks=[], weekly_fill=[fit_wf], unfinished=[],
        total_capacity_hrs=300.0, total_scheduled_hrs=180.0,
        total_idle_hrs=120.0, total_excess_kg=0.0, total_changeovers=0,
        week_days=[6, 6, 6, 7], params_used={},
    )

    return pipe_result, fitting_result, pipe_schedule, fitting_schedule


def test_route_consolidated_with_both_schedules_returns_xlsx():
    """GET /machine-planning/report/consolidated returns a valid .xlsx with 7 tabs
    and the fitting machine row in tab '2. Machine Load' when both pipe and fitting
    plans are present in the session."""
    import app as appmod
    from openpyxl import load_workbook

    pipe_r, fit_r, pipe_s, fit_s = _make_both_results()

    with patch.object(appmod, "_ensure_session_run_id", return_value=None), \
         patch.object(appmod, "_mp2_result_from_session", return_value=pipe_r), \
         patch.object(appmod, "_mp3_fitting_result_from_session", return_value=fit_r), \
         patch.object(appmod, "_mp_schedule_from_session", return_value=pipe_s), \
         patch.object(appmod, "_mp_fitting_schedule_from_session", return_value=fit_s):

        client = appmod.app.test_client()
        resp = client.get("/machine-planning/report/consolidated")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert "spreadsheet" in resp.content_type or "octet" in resp.content_type or \
           resp.data[:4] == b"PK\x03\x04", (
        f"Response is not xlsx; content_type={resp.content_type}"
    )

    wb = load_workbook(io.BytesIO(resp.data))
    assert len(wb.sheetnames) == 7, f"Expected 7 tabs; got {wb.sheetnames}"
    for prefix in ["1.", "2.", "3.", "4.", "5.", "6.", "7."]:
        assert any(n.startswith(prefix) for n in wb.sheetnames), (
            f"Tab starting with {prefix!r} missing; sheets={wb.sheetnames}"
        )

    # Tab '2. Machine Load' must show the fitting machine name
    ws2 = wb["2. Machine Load"]
    col1 = [ws2.cell(row=r, column=1).value for r in range(1, ws2.max_row + 1)]
    assert "FM-ROUTE-1" in col1, (
        f"Fitting machine 'FM-ROUTE-1' not found in tab '2. Machine Load' col-1; "
        f"values: {[v for v in col1 if v]}"
    )

    # The section header must also be present
    found_header = any("FITTING MACHINES" in str(v or "") for v in col1)
    assert found_header, "Expected 'FITTING MACHINES' section header in tab '2. Machine Load'"


def test_route_zip_with_both_schedules_returns_zip_with_consolidated():
    """GET /machine-planning/report/zip returns a ZIP that contains the consolidated
    sheet and the fitting-machine report (report-12) when both plans are present."""
    import app as appmod

    pipe_r, fit_r, pipe_s, fit_s = _make_both_results()

    with patch.object(appmod, "_ensure_session_run_id", return_value=None), \
         patch.object(appmod, "_mp2_result_from_session", return_value=pipe_r), \
         patch.object(appmod, "_mp3_fitting_result_from_session", return_value=fit_r), \
         patch.object(appmod, "_mp_schedule_from_session", return_value=pipe_s), \
         patch.object(appmod, "_mp_fitting_schedule_from_session", return_value=fit_s):

        client = appmod.app.test_client()
        resp = client.get("/machine-planning/report/zip")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    assert resp.data[:4] == b"PK\x03\x04", (
        f"Response is not a ZIP; first bytes={resp.data[:4]!r}"
    )

    import zipfile as _zipfile
    with _zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        names = zf.namelist()

    # Must contain a consolidated plan entry
    consolidated_entries = [n for n in names if "Consolidated_Plan" in n]
    assert consolidated_entries, (
        f"ZIP must contain a Consolidated_Plan file; got: {names}"
    )

    # Must contain report-12 (fittings)
    report12_entries = [n for n in names if "Report-12" in n or "Fitting" in n]
    assert report12_entries, (
        f"ZIP must contain a Report-12/Fitting file; got: {names}"
    )

    # Consolidated entry inside the ZIP must be a valid .xlsx with fitting data
    from openpyxl import load_workbook
    with _zipfile.ZipFile(io.BytesIO(resp.data)) as zf:
        xlsx_bytes = zf.read(consolidated_entries[0])
    wb = load_workbook(io.BytesIO(xlsx_bytes))
    assert len(wb.sheetnames) == 7, (
        f"Consolidated sheet inside ZIP should have 7 tabs; got {wb.sheetnames}"
    )
    ws2 = wb["2. Machine Load"]
    col1 = [ws2.cell(row=r, column=1).value for r in range(1, ws2.max_row + 1)]
    assert "FM-ROUTE-1" in col1, (
        f"Fitting machine 'FM-ROUTE-1' not found in consolidated sheet inside ZIP; "
        f"values: {[v for v in col1 if v]}"
    )
