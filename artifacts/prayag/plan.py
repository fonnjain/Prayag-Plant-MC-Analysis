"""
plan.py — Phase 3 Unified per-machine planning view.

build_plan(plant, month) joins on-demand loaders (Phases 2A–2D)
plus existing production/metrics into one MachinePlan per machine.
Returns (plans, plant_alerts) — a list of MachinePlan and a list of
plant-wide material alert dicts for the banner on /plan.

INVARIANTS
  * NEVER called on "/".  Triggered only from /plan routes.
  * Reuses existing L1/L2 cached loaders — no new sheet reads.
  * Recomputes every gate; trusts no stored flag.
  * Feed gate wired from compound mixer-batch log (Report-5 A-D).
  * Tooling gate wired from toolroom job log (Report-21).
  * Grey and plant-wide gates NEVER become the bottleneck.
  * Machine roster = PRODUCTION sources only (daily prod_recs + PIPE fixed list).
    Maintenance records (Report-16/8) are a LEFT JOIN — NEVER added to the roster.
    MouldStd names are a lookup index for PTMT — NEVER added to the roster.
  * Material gate is PER-MACHINE, filtered to the machine's run-queue types.
    A machine with an empty queue gets Material=GREY ("no mapped demand").
    Generic plant-level RM (item_type == plant name) is surfaced as "plant-wide"
    status (not "red") — the plant-level banner carries the risk signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

#: Priority order used to pick the bottleneck (first RED wins; Grey is skipped).
GATE_PRIORITY: List[str] = [
    "Material", "Tooling", "Feed", "Machine health", "Manpower", "Capacity"
]

#: Canonical PIPE production machine roster — Report-5 machine column format.
#: Matches the "PIPE Pipe M/C-N" naming stored by the daily Record parser.
#: Seeded into the roster so idle machines (no prod_recs this month) still appear.
_PIPE_PRODUCTION_MACHINES: List[str] = [
    f"PIPE Pipe M/C-{i}" for i in range(1, 10)
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


class _WorstRm(NamedTuple):
    """Return type of _find_worst_rm: worst reorder-triggered RM item."""
    item_name: str
    days_of_cover: Optional[float]    # recomputed; used as sort key
    cover_display: Optional[float]    # stock_days_sheet when available, else days_of_cover
    lead_time_days: float
    as_of_date: str
    item_type: str                    # uppercased; "" = unclassified / generic


def _is_plant_wide_rm(item_type: str, plant: str) -> bool:
    """True when an RM item has no machine/type-specific attribution.

    Records with item_type equal to the plant name (e.g. 'PIPE') or empty string
    are generic plant-level inputs (e.g. GRANUALS-CG122 for PIPE).  They are surfaced
    as a shared plant-level alert banner rather than identical red on every card.
    """
    t = (item_type or "").strip().upper()
    p = (plant or "").strip().upper()
    return not t or t == p


def _classify_ptmt_item(item_name: str) -> str:
    """Map a MouldStd item_name to a PlanRecord family ('cistern'|'seatcover'|'faucet').

    Keywords are matched against the uppercased item_name.  The three families
    correspond to the finished-product planning families in PTMT Report-1.
    Moulds that don't contain cistern- or seatcover-specific keywords are
    classified as 'faucet' (the dominant PTMT product line).
    """
    nm = item_name.strip().upper()
    cistern_kw = ("CISTERN", "W/C", "BALL COCK", "FLOAT BALL", "FLUSH", "BALLCOCK",
                  "SIDE INLET", "BALL VALVE CISTERN")
    seat_kw = ("SEAT ", "SEAT-", " SEAT", "TOILET SEAT", "COVER SEAT")
    for kw in cistern_kw:
        if kw in nm:
            return "cistern"
    for kw in seat_kw:
        if kw in nm:
            return "seatcover"
    return "faucet"


def _find_worst_rm(mat_recs: list, item_types: Optional[Set[str]] = None) -> Optional[_WorstRm]:
    """Return _WorstRm for the RM item with the lowest days_of_cover at reorder level.

    If item_types is provided, only RM records whose item_type (uppercased)
    is in item_types, or whose item_type is empty/unknown, are considered.
    Returns None when all RM stock is healthy or no matching RM records exist.

    cover_display uses stock_days_sheet (the sheet's own pre-computed "Stock Days"
    column) when available, falling back to the recomputed days_of_cover.  For PIPE
    Report-2 the two can diverge because the sheet uses actual-month consumption while
    the app computes from a rolling average.  The sheet value matches what Prayag staff
    read from the report.
    """
    worst: Optional[_WorstRm] = None
    worst_cover = float("inf")
    for r in mat_recs:
        if r.category != "RM":
            continue
        if item_types is not None:
            rt = (getattr(r, "item_type", "") or "").strip().upper()
            if rt and rt not in item_types:
                continue
        if r.reorder_flag and r.days_of_cover is not None:
            if r.days_of_cover < worst_cover:
                worst_cover = r.days_of_cover
                sds = getattr(r, "stock_days_sheet", None)
                worst = _WorstRm(
                    item_name=r.item_name,
                    days_of_cover=r.days_of_cover,
                    cover_display=sds if sds is not None else r.days_of_cover,
                    lead_time_days=r.lead_time_days,
                    as_of_date=r.as_of_date,
                    item_type=(getattr(r, "item_type", "") or "").strip().upper(),
                )
    return worst


def _build_run_queue(
    plant: str,
    norm_m: str,
    ptmt_machine_stds: Dict[str, list],
    ptmt_plan_by_family: Dict[str, list],
    pipe_machine_materials: Dict[str, set],
    pipe_plan_by_family: Dict[str, list],
    pipe_all_plan_recs: list = (),
) -> List[RunQueueItem]:
    """Build ranked run queue for one machine.

    PTMT  — MouldStd.machine_name → classify item_name → family
             → PlanRecord for that family (finished-product demand).
             MouldStd item_codes are mould part numbers (PSF-xxx) which are
             a different code system from the product item codes in PlanRecord
             — they MUST NOT be used as a join key.
    PIPE  — production Record.material → PlanRecord.family;
             falls back to ALL open pipe plan records for idle machines
             (machines with no prod_recs this month).
    Others — no PlanRecord data available → empty queue
    """
    items: List[RunQueueItem] = []
    seen_codes: set = set()

    if plant == "PTMT":
        stds = _lookup_list(ptmt_machine_stds, norm_m)
        # Determine which finished-product families this machine's moulds serve.
        # If no mould stds are registered, assume the machine can run all families.
        machine_fams: Set[str] = {_classify_ptmt_item(s.item_name) for s in stds}
        if not machine_fams:
            machine_fams = {"faucet", "cistern", "seatcover"}
        for fam in sorted(machine_fams):  # sorted for determinism
            for plan_r in (ptmt_plan_by_family or {}).get(fam, []):
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
                    family=fam,
                    net_requirement=plan_r.net_requirement,
                    days_of_cover=plan_r.days_of_cover,
                    theoretical_rate=round(rate, 1) if rate else None,
                    estimated_run_time_hrs=est_h,
                    unit="pcs",
                ))

    elif plant in ("PIPE", "CP"):
        # Convert set-valued dict to list dict for _lookup_list
        mat_set_idx: Dict[str, list] = {
            k: list(v) for k, v in pipe_machine_materials.items()
        }
        materials = _lookup_list(mat_set_idx, norm_m)

        if materials:
            # Machine ran in this period — use its material history
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
        else:
            # Idle machine (no production this month) — offer ALL open pipe jobs
            # Any pipe machine can run any product type when set up for it.
            for plan_r in pipe_all_plan_recs:
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
    plant: str,
    run_queue: List[RunQueueItem],
    mat_recs: list,
    maint_idx: Dict,
    mp_idx: Dict,
    m_result,
    actual_h: float,
    ideal_h: float,
    month: str,
    *,
    mixer_recs_for_type: Optional[list] = None,
    toolroom_items: Optional[frozenset] = None,
) -> Tuple[List[GateStatus], Optional[str], str]:
    """Evaluate all six readiness gates; return (gates, bottleneck_name, reason).

    Material gate is PER-MACHINE — filtered to the types in this machine's run queue.
    A machine with an empty queue gets Material=GREY (no mapped demand).
    Generic plant-level RM (item_type == plant name) is surfaced as "plant-wide" status
    (not "red") — machine is NOT individually bottlenecked; the banner carries the risk.
    Grey and plant-wide gates NEVER become the bottleneck.

    mixer_recs_for_type — CompoundBatchRecord list for this machine's compound types;
                          None = no data (GREY); [] = data but no records for type (GREY);
                          non-empty: breakdown_hours>0 + total_compound_kg==0 → RED, else GREEN.
    toolroom_items      — frozenset of item names in active toolroom jobs for this machine;
                          None = no data (GREY); frozenset() = loaded, no match (GREEN);
                          non-empty = active job blocking the machine (RED).
    """
    gates: List[GateStatus] = []

    # ── 1. Material (per-machine RM check) ─────────────────────────────
    if not run_queue:
        gates.append(GateStatus(
            name="Material", status="grey",
            reason="No mapped demand — no job assigned",
            provenance="",
        ))
    else:
        # item_types: queue families + plant name (covers generic plant-level RM)
        item_types: Set[str] = {qi.family.strip().upper() for qi in run_queue if qi.family}
        if plant:
            item_types.add(plant.strip().upper())

        rm_worst = _find_worst_rm(mat_recs, item_types=item_types)
        rm_recs = [
            r for r in mat_recs if r.category == "RM" and (
                not item_types
                or not (getattr(r, "item_type", "") or "").strip().upper()
                or (getattr(r, "item_type", "") or "").strip().upper() in item_types
            )
        ]
        mat_as_of = rm_recs[0].as_of_date if rm_recs else ""

        if rm_worst:
            cd = (rm_worst.cover_display
                  if rm_worst.cover_display is not None else rm_worst.days_of_cover)
            if _is_plant_wide_rm(rm_worst.item_type, plant):
                gates.append(GateStatus(
                    name="Material", status="plant-wide",
                    reason=(f"plant-wide: {rm_worst.item_name} cover "
                            f"{cd:.0f}d < lead {rm_worst.lead_time_days:.0f}d"),
                    provenance=f"Report-2/3/4 as of {rm_worst.as_of_date or month}",
                ))
            else:
                gates.append(GateStatus(
                    name="Material", status="red",
                    reason=(f"{rm_worst.item_name} cover "
                            f"{cd:.0f}d < lead {rm_worst.lead_time_days:.0f}d"),
                    provenance=f"Report-2/3/4 as of {rm_worst.as_of_date or month}",
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
                reason="No RM data for this machine type",
                provenance="",
            ))

    # ── 2. Tooling (Report-21) ──────────────────────────────────────────
    if toolroom_items is None:
        gates.append(GateStatus(
            name="Tooling", status="grey",
            reason="No toolroom data for this machine",
            provenance="",
        ))
    elif toolroom_items:
        items_str = ", ".join(sorted(toolroom_items)[:2])
        if len(toolroom_items) > 2:
            items_str += f" (+{len(toolroom_items) - 2} more)"
        gates.append(GateStatus(
            name="Tooling", status="red",
            reason=f"Active toolroom job: {items_str}",
            provenance=f"Report-21 as of {month}",
        ))
    else:
        gates.append(GateStatus(
            name="Tooling", status="green",
            reason="No active toolroom job this period",
            provenance=f"Report-21 as of {month}",
        ))

    # ── 3. Feed / compound (Report-5 A-D) ──────────────────────────────
    if mixer_recs_for_type is None or not mixer_recs_for_type:
        reason = ("No compound/mixer data for this type"
                  if mixer_recs_for_type is not None
                  else "No compound/mixer data available")
        gates.append(GateStatus(
            name="Feed", status="grey",
            reason=reason,
            provenance="",
        ))
    else:
        has_breakdown = any(
            getattr(r, "breakdown_hours", 0) > 0
            and getattr(r, "total_compound_kg", 0) == 0
            for r in mixer_recs_for_type
        )
        if has_breakdown:
            gates.append(GateStatus(
                name="Feed", status="red",
                reason="Mixer breakdown — no compound produced for this type",
                provenance=f"Report-5(A-D) as of {month}",
            ))
        else:
            total_kg = sum(getattr(r, "total_compound_kg", 0) for r in mixer_recs_for_type)
            gates.append(GateStatus(
                name="Feed", status="green",
                reason=f"Compound available ({total_kg:,.0f} kg produced)",
                provenance=f"Report-5(A-D) as of {month}",
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
            avg_req = pipe_recs[0].required_manpower
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
                    reason=f"Avg actual {avg_actual:.1f} \u2265 required {avg_req:.0f}",
                    provenance=f"Report-22 as of {last_date}",
                ))
        else:
            # PTMT — no required_manpower col; confirm any was logged
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

def build_plan(plant: str, month: str) -> Tuple[List[MachinePlan], List[dict]]:
    """Build (MachinePlan list, plant_alerts list) for plant + month.

    plant_alerts — list of dicts for plant-wide RM items at reorder:
      {"item_name", "cover_display", "lead_time_days", "as_of"}
    Machine roster = production sources only; maintenance is a LEFT JOIN.
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

    # Phase 2D: compound/feed (Report-5 A-D) and toolroom (Report-21)
    try:
        mixer_recs_raw: Optional[list] = sheets.load_mixer_records(plant, month)
    except Exception:
        mixer_recs_raw = None  # None → GREY per machine

    try:
        toolroom_recs_raw: Optional[list] = sheets.load_toolroom_records(plant, month)
    except Exception:
        toolroom_recs_raw = None  # None → GREY per machine

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
    # Maintenance is a LEFT JOIN index — never used to build the roster.
    maint_idx: Dict[str, object] = {_norm(r.machine): r for r in maint_recs}

    mp_idx: Dict[str, list] = {}
    for r in mp_recs:
        mp_idx.setdefault(_norm(r.machine), []).append(r)

    mach_metrics: Dict[str, object] = {}
    if prod_recs:
        mach_metrics = _met.rollup_by_machine(prod_recs)

    # PTMT MASTER: lookup index for run-queue joins; NOT added to the roster.
    ptmt_machine_stds: Dict[str, list] = {}
    for std in mould_stds:
        ptmt_machine_stds.setdefault(_norm(std.machine_name), []).append(std)

    # PTMT: plan records keyed by family (faucet/cistern/seatcover).
    # NOTE: MouldStd item_codes are mould part numbers (PSF-xxx), NOT product
    # item codes — the two code systems never overlap, so a code-based join is
    # impossible.  Family-based join is the correct approach.
    ptmt_plan_by_family: Dict[str, list] = {}
    if plant == "PTMT":
        for r in plan_recs:
            fam = (r.family or "").strip().lower()
            if fam:
                ptmt_plan_by_family.setdefault(fam, []).append(r)

    pipe_machine_materials: Dict[str, set] = {}
    for r in prod_recs:
        if r.machine and r.material:
            pipe_machine_materials.setdefault(_norm(r.machine), set()).add(
                r.material.upper().strip()
            )

    pipe_plan_by_family: Dict[str, list] = {}
    for r in plan_recs:
        pipe_plan_by_family.setdefault(r.family.upper().strip(), []).append(r)

    # ── 2b. Plant-wide material alerts (shared banner on /plan) ─────────
    _best_pa: Dict[str, dict] = {}
    for _r in mat_recs:
        if _r.category != "RM" or not _r.reorder_flag:
            continue
        _itype = (getattr(_r, "item_type", "") or "").strip().upper()
        if not _is_plant_wide_rm(_itype, plant):
            continue
        _sds = getattr(_r, "stock_days_sheet", None)
        _cd = _sds if _sds is not None else _r.days_of_cover
        _nm = _r.item_name
        _existing = _best_pa.get(_nm)
        if _existing is None or (_cd or 999) < (_existing["cover_display"] or 999):
            _best_pa[_nm] = {
                "item_name": _nm, "cover_display": _cd,
                "lead_time_days": _r.lead_time_days, "as_of": _r.as_of_date,
            }
    plant_alerts: List[dict] = sorted(
        _best_pa.values(), key=lambda a: (a["cover_display"] or 999)
    )

    # Per-machine Phase 2D helpers (closures over *_recs_raw locals above)
    def _mixer_for(families: Set[str]) -> Optional[list]:
        """Mixer records for these compound families; None → no data → GREY."""
        if mixer_recs_raw is None or not families:
            return None
        fam_upper = {f.strip().upper() for f in families if f}
        matched = [
            r for r in mixer_recs_raw
            if (getattr(r, "batch_type", "") or "").strip().upper() in fam_upper
        ]
        return matched  # [] = data loaded but 0 records for type → GREY per spec

    def _toolroom_for(norm_m_: str, stds_: list) -> Optional[frozenset]:
        """Toolroom items for this machine's moulds; None → no data → GREY."""
        if toolroom_recs_raw is None:
            return None
        if plant == "PTMT":
            if not stds_:
                return None  # no mould data for machine → GREY
            mould_norms = {_norm(s.item_name) for s in stds_}
            return frozenset(
                r.item for r in toolroom_recs_raw
                if _norm(r.item) in mould_norms
                or any(_partial_match(_norm(r.item), mn) for mn in mould_norms)
            )
        # PIPE/CP: Report-21 tracks moulds/lathe ops, not extrusion dies
        # → no reliable per-machine mapping → GREY
        return None

    # ── 3. Collect machine names from PRODUCTION sources only ────────────
    #    maint_recs → LEFT JOIN, never roster source.
    #    mould_stds → run-queue lookup only, never roster source.
    #    mp_recs    → lookup index only, never roster source.
    machines: set = set()

    # PIPE: seed with canonical 9-machine list so idle machines still appear.
    if plant in ("PIPE", "CP"):
        for m in _PIPE_PRODUCTION_MACHINES:
            machines.add(m)

    # Add any machine that actually appeared in daily production this month.
    for r in prod_recs:
        if r.machine and not r.is_finishing:
            machines.add(r.machine)

    # PTMT: prod_recs carries authoritative names (with "PTMT " prefix).
    # mould_stds names lack the prefix and cause duplicates — excluded.

    if not machines:
        return []

    # ── 4. MachinePlan per machine ───────────────────────────────────────
    as_of_global = plan_recs[0].as_of_date if plan_recs else ""
    plans: List[MachinePlan] = []

    for machine in sorted(machines):
        norm_m = _norm(machine)

        # Production context — exact name then normalised fallback
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
            ptmt_machine_stds, ptmt_plan_by_family,
            pipe_machine_materials, pipe_plan_by_family,
            pipe_all_plan_recs=plan_recs,
        )

        # Phase 2D: compound families and mould stds for this machine
        if plant in ("PIPE", "CP"):
            _m_fams: Set[str] = (pipe_machine_materials.get(norm_m)
                                  or set(pipe_plan_by_family.keys()))
        elif plant == "PTMT":
            _m_stds_loc = _lookup_list(ptmt_machine_stds, norm_m)
            _m_fams = ({_classify_ptmt_item(s.item_name).upper() for s in _m_stds_loc}
                       or {"FAUCET", "CISTERN", "SEATCOVER"})
        else:
            _m_fams = set()
        _toolroom_stds = (_lookup_list(ptmt_machine_stds, norm_m)
                          if plant == "PTMT" else [])

        gates, bottleneck, bn_reason = _evaluate_gates(
            norm_m=norm_m,
            plant=plant,
            run_queue=run_queue,
            mat_recs=mat_recs,
            maint_idx=maint_idx,
            mp_idx=mp_idx,
            m_result=m_result,
            actual_h=actual_h,
            ideal_h=ideal_h,
            month=month,
            mixer_recs_for_type=_mixer_for(_m_fams),
            toolroom_items=_toolroom_for(norm_m, _toolroom_stds),
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
    return plans, plant_alerts
