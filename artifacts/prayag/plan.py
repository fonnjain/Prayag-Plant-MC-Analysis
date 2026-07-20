"""
plan.py — Phase 3 Unified per-machine planning view.

build_plan(plant, month) joins on-demand loaders (Phases 2A–2C)
plus existing production/metrics into one MachinePlan per machine.

INVARIANTS
  * NEVER called on "/".  Triggered only from /plan routes.
  * Reuses existing L1/L2 cached loaders — no new sheet reads.
  * Recomputes every gate; trusts no stored flag.
  * Feed + Tooling gates are GREY until Phase 2D lands.
  * Grey gates NEVER become the bottleneck.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

#: Priority order used to pick the bottleneck (first RED wins; Grey is skipped).
GATE_PRIORITY: List[str] = [
    "Material", "Tooling", "Feed", "Machine health", "Manpower", "Capacity"
]


@dataclass
class GateStatus:
    """One gate in the machine readiness checklist."""
    name: str       # "Material" | "Tooling" | "Feed" | "Machine health" | "Manpower" | "Capacity"
    status: str     # "green" | "red" | "grey"
    reason: str     # short human-readable value ("Resin K-67 cover 2d < lead 5d")
    provenance: str # "Report-2/3/4 as of Jun-30"


@dataclass
class RunQueueItem:
    """One candidate production item for a machine's run queue."""
    item_code: str
    item_name: str
    family: str
    net_requirement: float          # pcs or kg depending on plant
    days_of_cover: Optional[float]  # None when no avg-sale data
    theoretical_rate: Optional[float]        # pcs/hr or kg/hr
    estimated_run_time_hrs: Optional[float]  # net_requirement / theoretical_rate
    unit: str                       # "pcs" | "kg"


@dataclass
class MachinePlan:
    """Unified planning snapshot for one machine for one month."""
    plant: str
    machine: str
    month: str
    run_queue: List[RunQueueItem] = field(default_factory=list)
    gates: List[GateStatus] = field(default_factory=list)
    # None = Ready; otherwise the highest-priority RED gate name
    bottleneck: Optional[str] = None
    bottleneck_reason: str = ""
    # Production context (from existing rollup — recomputed, never stored)
    actual_hours: float = 0.0
    ideal_hours: float = 0.0
    utilisation_pct: Optional[float] = None   # 0..100; None when not tracked
    idle_hours: float = 0.0                   # max(0, ideal - actual)
    total_output: float = 0.0
    output_unit: str = "pcs"
    # Sorting helper: idle capacity AND non-empty queue AND no red gate
    actionable: bool = False
    as_of_date: str = ""     # snapshot date from PlanRecord header


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(name: str) -> str:
    """Normalise: uppercase, collapse non-alphanumeric runs to single space."""
    if not name:
        return ""
    return re.sub(r"[^A-Z0-9]+", " ", name.upper().strip()).strip()


def _partial_match(a: str, b: str) -> bool:
    """True when normalised a and b share a meaningful numeric token."""
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    nums_a = set(re.findall(r"\d+", a))
    nums_b = set(re.findall(r"\d+", b))
    return bool(nums_a and nums_b and nums_a & nums_b)


def _lookup(idx: Dict[str, object], norm_key: str):
    """Exact-then-partial lookup in a norm-keyed index; returns None on miss."""
    v = idx.get(norm_key)
    if v is not None:
        return v
    for k, w in idx.items():
        if _partial_match(norm_key, k):
            return w
    return None


def _lookup_list(idx: Dict[str, list], norm_key: str) -> list:
    """Exact-then-partial lookup returning a list; empty list on miss."""
    v = idx.get(norm_key)
    if v is not None:
        return v
    for k, w in idx.items():
        if _partial_match(norm_key, k):
            return w
    return []


def _find_worst_rm(mat_recs: list):
    """Return (item_name, cover_days, lead_days, as_of) for the RM item
    with the lowest days_of_cover that is at or below reorder level.
    Returns None when all RM stock is healthy or no RM records are loaded."""
    worst = None
    worst_cover = float("inf")
    for r in mat_recs:
        if r.category != "RM":
            continue
        if r.reorder_flag and r.days_of_cover is not None:
            if r.days_of_cover < worst_cover:
                worst_cover = r.days_of_cover
                worst = (r.item_name, r.days_of_cover, r.lead_time_days,
                         r.as_of_date)
    return worst


def _build_run_queue(
    plant: str,
    norm_m: str,
    ptmt_machine_stds: Dict[str, list],
    plan_by_code: Dict,
    pipe_machine_materials: Dict[str, set],
    pipe_plan_by_family: Dict[str, list],
) -> List[RunQueueItem]:
    """Build ranked run queue for one machine.

    PTMT  — MouldStd.machine_name → item_code → PlanRecord
    PIPE  — production Record.material → PlanRecord.family
    Others — no PlanRecord data available → empty queue
    """
    items: List[RunQueueItem] = []
    seen_codes: set = set()

    if plant == "PTMT":
        stds = _lookup_list(ptmt_machine_stds, norm_m)
        for std in stds:
            code = (std.item_code or "").strip().upper()
            plan_r = plan_by_code.get(code)
            if plan_r is None or plan_r.net_requirement <= 0:
                continue
            if code in seen_codes:
                continue
            seen_codes.add(code)
            rate = std.theoretical_pcs_hr if std.theoretical_pcs_hr > 0 else None
            est_h = round(plan_r.net_requirement / rate, 2) if rate else None
            items.append(RunQueueItem(
                item_code=plan_r.item_code,
                item_name=plan_r.item_name,
                family=plan_r.family,
                net_requirement=plan_r.net_requirement,
                days_of_cover=plan_r.days_of_cover,
                theoretical_rate=round(rate, 1) if rate else None,
                estimated_run_time_hrs=est_h,
                unit="pcs",
            ))

    elif plant in ("PIPE", "CP"):
        # Convert the set-valued dict to a plain list dict for _lookup_list
        mat_set_idx: Dict[str, list] = {
            k: list(v) for k, v in pipe_machine_materials.items()
        }
        materials = _lookup_list(mat_set_idx, norm_m)
        for mat in materials:
            for plan_r in pipe_plan_by_family.get(mat, []):
                if plan_r.net_requirement <= 0:
                    continue
                code = (plan_r.item_code or "").strip().upper()
                if code in seen_codes:
                    continue
                seen_codes.add(code)
                rate = plan_r.per_hour_output if plan_r.per_hour_output > 0 else None
                est_h = round(plan_r.net_requirement / rate, 2) if rate else None
                items.append(RunQueueItem(
                    item_code=plan_r.item_code,
                    item_name=plan_r.item_name,
                    family=plan_r.family,
                    net_requirement=plan_r.net_requirement,
                    days_of_cover=plan_r.days_of_cover,
                    theoretical_rate=round(rate, 1) if rate else None,
                    estimated_run_time_hrs=est_h,
                    unit="pcs",
                ))

    # Rank: net_requirement DESC, then days_of_cover ASC (most-urgent stock first)
    items.sort(key=lambda x: (
        -x.net_requirement,
        x.days_of_cover if x.days_of_cover is not None else float("inf"),
    ))
    return items


def _evaluate_gates(
    norm_m: str,
    rm_worst,
    maint_idx: Dict,
    mp_idx: Dict,
    m_result,
    actual_h: float,
    ideal_h: float,
    mat_recs: list,
    month: str,
) -> Tuple[List[GateStatus], Optional[str], str]:
    """Evaluate all six readiness gates; return (gates, bottleneck_name, reason)."""
    gates: List[GateStatus] = []

    # ── 1. Material (plant-level RM reorder check) ─────────────────────
    rm_recs = [r for r in mat_recs if r.category == "RM"]
    mat_as_of = rm_recs[0].as_of_date if rm_recs else ""
    if rm_worst:
        name, cover, lead, as_of = rm_worst
        gates.append(GateStatus(
            name="Material", status="red",
            reason=f"{name} cover {cover:.0f}d < lead {lead:.0f}d",
            provenance=f"Report-2/3/4 as of {as_of or month}",
        ))
    elif rm_recs:
        gates.append(GateStatus(
            name="Material", status="green",
            reason="All RM stock above lead time",
            provenance=f"Report-2/3/4 as of {mat_as_of or month}",
        ))
    else:
        gates.append(GateStatus(
            name="Material", status="grey",
            reason="No material data loaded",
            provenance="",
        ))

    # ── 2. Tooling (always GREY — Phase 2D not yet built) ──────────────
    gates.append(GateStatus(
        name="Tooling", status="grey",
        reason="not yet tracked (Phase 2D)",
        provenance="",
    ))

    # ── 3. Feed (always GREY — Phase 2D not yet built) ─────────────────
    gates.append(GateStatus(
        name="Feed", status="grey",
        reason="not yet tracked (Phase 2D)",
        provenance="",
    ))

    # ── 4. Machine health (maintenance register) ───────────────────────
    maint_r = _lookup(maint_idx, norm_m)
    if maint_r is not None:
        age_str = (f"{maint_r.machine_age_years:.1f}y"
                   if maint_r.machine_age_years else "age unknown")
        gates.append(GateStatus(
            name="Machine health", status="green",
            reason=f"In maintenance register ({age_str})",
            provenance=f"Report-16/Report-8 as of {month}",
        ))
    else:
        gates.append(GateStatus(
            name="Machine health", status="grey",
            reason="Not in maintenance register",
            provenance="",
        ))

    # ── 5. Manpower ────────────────────────────────────────────────────
    mp_list = _lookup_list(mp_idx, norm_m)
    if mp_list:
        pipe_recs = [r for r in mp_list if r.required_manpower > 0]
        if pipe_recs:
            avg_actual = sum(r.actual_manpower for r in pipe_recs) / len(pipe_recs)
            avg_req = pipe_recs[0].required_manpower  # static "REQUIREMENT" col
            last_date = max(
                (r.date for r in pipe_recs if r.date), default=month
            )
            if avg_actual < avg_req:
                gates.append(GateStatus(
                    name="Manpower", status="red",
                    reason=f"Avg actual {avg_actual:.1f} < required {avg_req:.0f}",
                    provenance=f"Report-22 as of {last_date}",
                ))
            else:
                gates.append(GateStatus(
                    name="Manpower", status="green",
                    reason=f"Avg actual {avg_actual:.1f} ≥ required {avg_req:.0f}",
                    provenance=f"Report-22 as of {last_date}",
                ))
        else:
            # PTMT — no required_manpower col; just confirm any was logged
            logged = sum(1 for r in mp_list if r.actual_manpower > 0)
            last_date = max((r.date for r in mp_list if r.date), default=month)
            if logged > 0:
                gates.append(GateStatus(
                    name="Manpower", status="green",
                    reason=f"Logged on {logged} shift-day(s)",
                    provenance=f"Report-6 as of {last_date}",
                ))
            else:
                gates.append(GateStatus(
                    name="Manpower", status="red",
                    reason="No manpower logged this period",
                    provenance=f"Report-6 as of {month}",
                ))
    else:
        gates.append(GateStatus(
            name="Manpower", status="grey",
            reason="No manpower data for this machine",
            provenance="",
        ))

    # ── 6. Capacity (idle hours from production rollup) ────────────────
    if m_result is not None and ideal_h > 0:
        idle_h = max(0.0, ideal_h - actual_h)
        if idle_h > 0:
            gates.append(GateStatus(
                name="Capacity", status="green",
                reason=f"{idle_h:.1f}h idle available",
                provenance=f"Production records {month}",
            ))
        else:
            gates.append(GateStatus(
                name="Capacity", status="red",
                reason=f"Fully loaded ({actual_h:.1f}h / {ideal_h:.1f}h ideal)",
                provenance=f"Production records {month}",
            ))
    else:
        gates.append(GateStatus(
            name="Capacity", status="grey",
            reason="No production baseline for this period",
            provenance="",
        ))

    # ── Bottleneck: first RED in GATE_PRIORITY; Grey never a bottleneck ─
    gate_by_name = {g.name: g for g in gates}
    bottleneck: Optional[str] = None
    bn_reason = ""
    for gname in GATE_PRIORITY:
        g = gate_by_name.get(gname)
        if g and g.status == "red":
            bottleneck = gname
            bn_reason = g.reason
            break

    return gates, bottleneck, bn_reason


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_plan(plant: str, month: str) -> List[MachinePlan]:
    """Build MachinePlan list for all machines in plant for the given month.

    Joins existing on-demand loaders (no new sheet reads).
    MUST NOT be called from the "/" critical path.
    """
    import sheets
    import metrics as _met

    # ── 1. Load all domains via cached loaders ───────────────────────────
    try:
        plan_recs = sheets.load_planning(plant, month)
    except Exception:
        plan_recs = []

    try:
        mat_recs = sheets.load_material_records(plant, month)
    except Exception:
        mat_recs = []

    try:
        maint_recs = sheets.load_maintenance_records(plant, month)
    except Exception:
        maint_recs = []

    try:
        mp_recs = sheets.load_manpower_records(plant, month)
    except Exception:
        mp_recs = []

    mould_stds: list = []
    if plant == "PTMT":
        try:
            mould_stds = sheets.load_ptmt_master(month)
        except Exception:
            mould_stds = []

    try:
        prod_recs_all, _, _ = sheets.get_daily_records([month])
        prod_recs = [r for r in prod_recs_all if r.plant == plant]
    except Exception:
        prod_recs = []

    # ── 2. Build indexes ─────────────────────────────────────────────────
    maint_idx: Dict[str, object] = {_norm(r.machine): r for r in maint_recs}

    mp_idx: Dict[str, list] = {}
    for r in mp_recs:
        mp_idx.setdefault(_norm(r.machine), []).append(r)

    mach_metrics: Dict[str, object] = {}
    if prod_recs:
        mach_metrics = _met.rollup_by_machine(prod_recs)

    rm_worst = _find_worst_rm(mat_recs)

    ptmt_machine_stds: Dict[str, list] = {}
    for std in mould_stds:
        ptmt_machine_stds.setdefault(_norm(std.machine_name), []).append(std)

    plan_by_code: Dict[str, object] = {
        (r.item_code or "").strip().upper(): r for r in plan_recs
    }

    pipe_machine_materials: Dict[str, set] = {}
    for r in prod_recs:
        if r.machine and r.material:
            pipe_machine_materials.setdefault(_norm(r.machine), set()).add(
                r.material.upper().strip()
            )

    pipe_plan_by_family: Dict[str, list] = {}
    for r in plan_recs:
        pipe_plan_by_family.setdefault(r.family.upper().strip(), []).append(r)

    # ── 3. Collect machine names from all sources ────────────────────────
    machines: set = set()
    for r in prod_recs:
        if r.machine and not r.is_finishing:
            machines.add(r.machine)
    for r in mp_recs:
        if r.machine:
            machines.add(r.machine)
    for r in maint_recs:
        if r.machine:
            machines.add(r.machine)
    for std in mould_stds:
        if std.machine_name:
            machines.add(std.machine_name)

    if not machines:
        return []

    # ── 4. MachinePlan per machine ───────────────────────────────────────
    as_of_global = plan_recs[0].as_of_date if plan_recs else ""
    plans: List[MachinePlan] = []

    for machine in sorted(machines):
        norm_m = _norm(machine)

        # Production context — try exact name then normalised fallback
        m_result = mach_metrics.get(machine)
        if m_result is None:
            for k, v in mach_metrics.items():
                if _norm(k) == norm_m:
                    m_result = v
                    break

        actual_h = m_result.actual_hours if m_result else 0.0
        ideal_h  = m_result.ideal_hours  if m_result else 0.0
        idle_h   = max(0.0, ideal_h - actual_h)

        util_pct: Optional[float] = None
        if m_result and m_result.utilisation > 0 and m_result.util_available:
            util_pct = round(m_result.utilisation * 100.0, 1)

        total_out = m_result.total_count if m_result else 0.0
        out_unit  = m_result.unit if (m_result and m_result.unit) else ""
        if not out_unit and m_result and m_result.output_by_unit:
            out_unit = next(iter(m_result.output_by_unit), "pcs")
        if not out_unit:
            out_unit = "pcs"

        run_queue = _build_run_queue(
            plant, norm_m,
            ptmt_machine_stds, plan_by_code,
            pipe_machine_materials, pipe_plan_by_family,
        )

        gates, bottleneck, bn_reason = _evaluate_gates(
            norm_m, rm_worst, maint_idx, mp_idx,
            m_result, actual_h, ideal_h, mat_recs, month,
        )

        actionable = (
            idle_h > 0
            and len(run_queue) > 0
            and bottleneck is None
        )

        plans.append(MachinePlan(
            plant=plant,
            machine=machine,
            month=month,
            run_queue=run_queue,
            gates=gates,
            bottleneck=bottleneck,
            bottleneck_reason=bn_reason,
            actual_hours=actual_h,
            ideal_hours=ideal_h,
            utilisation_pct=util_pct,
            idle_hours=idle_h,
            total_output=total_out,
            output_unit=out_unit,
            actionable=actionable,
            as_of_date=as_of_global,
        ))

    # Sort: actionable first, then group blocked by bottleneck name, then alpha
    plans.sort(key=lambda p: (
        0 if p.actionable else 1,
        p.bottleneck or "",
        p.machine,
    ))
    return plans
