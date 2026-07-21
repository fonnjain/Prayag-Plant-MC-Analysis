"""
Machine Planning Optimiser — Phase MP-2.

Parses a weekly release plan Excel, runs per-item chain math from the
mp_* tables, then LPT + parallel-split load balancing across extrusion
machines.

ADDITIVE / ISOLATED: reads only mp_* tables. Never touches the existing
production pipeline (/, /data, /reports, /plan).
"""
from __future__ import annotations

import dataclasses
import io
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

# ── openpyxl (for demand Excel parsing) ─────────────────────────────────────
try:
    import openpyxl
    _OPENPYXL = True
except ImportError:
    _OPENPYXL = False

# ── local imports (all mp_* — never production pipeline) ────────────────────
from mp_seed import norm_code as _norm_code, SEGMENT as _DEFAULT_SEGMENT
import mp_model as _mp

# ── Constants ────────────────────────────────────────────────────────────────
PIPE_TABS: Dict[str, str] = {
    "CPVC Pipe": "CPVC",
    "UPVC Pipe": "UPVC",
    "SWR Pipe":  "SWR",
    "AGRI Pipe": "AGRI",
}
_COL_ITEM = 0   # Col A — Item Code (0-indexed)
_COL_QTY  = 3   # Col D — Production Plan pcs (0-indexed)

# Default machine-group config for Report-11A–D (overridable via DB later)
REPORT_11_GROUPS: Dict[str, List[str]] = {
    "A": ["M/C-1", "M/C-2"],
    "B": ["M/C-3", "M/C-4"],
    "C": ["M/C-5", "M/C-6"],
    "D": ["M/C-7", "M/C-8", "M/C-9"],
}


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclasses.dataclass
class DemandItem:
    item_code: str   # normalised
    raw_code: str    # original text from Excel
    material: str    # CPVC / UPVC / SWR / AGRI
    qty_pcs: float


@dataclasses.dataclass
class AssignedPortion:
    machine: str
    hrs: float
    material_kg: float
    qty_pcs: float


@dataclasses.dataclass
class ItemResult:
    item_code: str
    raw_code: str
    material: str
    qty_pcs: float
    weight_per_pc_kg: Optional[float]
    material_kg: float
    fresh_compound_kg: float
    pulverizer_kg: float
    rate_kg_per_hr: float
    rate_estimated: bool
    machine_hrs: float
    capable_machines: List[str]
    assignments: List[AssignedPortion]
    has_weight: bool
    has_machine: bool


@dataclasses.dataclass
class MachineLoad:
    machine: str
    capacity_hrs: float
    assigned_hrs: float
    utilisation_pct: float
    machine_days: float
    material_kg: float
    fresh_compound_kg: float
    pulverizer_kg: float
    staffing_ok: bool
    operators_ot: int
    support_w: int


@dataclasses.dataclass
class CoverageGaps:
    no_weight: List[str]           # item codes with no BOM weight
    no_machine: List[str]          # item codes with no capable extrusion machine
    idle_machines: List[str]       # machines in routing but got no load this plan
    locked_out_machines: List[str] # extrusion machines with no routing rows at all


@dataclasses.dataclass
class PlanTotals:
    total_qty_pcs: float
    total_material_kg: float
    total_fresh_compound_kg: float
    total_pulverizer_kg: float
    routable_material_kg: float
    routable_fresh_compound_kg: float
    routable_pulverizer_kg: float


@dataclasses.dataclass
class EngineResult:
    segment: str
    effective_month: str
    items: List[ItemResult]
    machine_loads: List[MachineLoad]          # optimised (LPT + split)
    coverage_gaps: CoverageGaps
    totals: PlanTotals
    baseline_machine_loads: List[MachineLoad] # unbalanced (first capable machine)
    params_used: Dict[str, float]

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict (via dataclasses.asdict)."""
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "EngineResult":
        """Reconstruct from a dict stored in Postgres / session."""
        def _ap(x: dict) -> AssignedPortion:
            return AssignedPortion(**x)

        def _item(x: dict) -> ItemResult:
            assignments = [_ap(a) for a in x.pop("assignments", [])]
            return ItemResult(**x, assignments=assignments)

        def _ml(x: dict) -> MachineLoad:
            return MachineLoad(**x)

        gaps = CoverageGaps(**d["coverage_gaps"])
        totals = PlanTotals(**d["totals"])
        return EngineResult(
            segment=d["segment"],
            effective_month=d["effective_month"],
            items=[_item(dict(r)) for r in d.get("items", [])],
            machine_loads=[_ml(r) for r in d.get("machine_loads", [])],
            coverage_gaps=gaps,
            totals=totals,
            baseline_machine_loads=[_ml(r) for r in d.get("baseline_machine_loads", [])],
            params_used=d.get("params_used", {}),
        )


# ── Demand parsing ───────────────────────────────────────────────────────────

def _is_total_row(cell_a: str) -> bool:
    return cell_a.upper().strip() in ("TOTAL", "TOTALS", "GRAND TOTAL")


def _is_skip_row(cell_a: str, cell_d: str) -> bool:
    """True for blank, header, or TOTAL rows."""
    if not cell_a:
        return True
    if _is_total_row(cell_a):
        return True
    # Skip pure numeric codes (row numbers, blank separators)
    nc = _norm_code(cell_a)
    if not nc or nc.isdigit():
        return True
    # Skip header-like rows ("ITEM CODE", "SR.", etc.)
    if re.match(r"^(ITEM|SR|S\.?\s*NO|SERIAL|DESCRIPTION|PRODUCT)", nc, re.I):
        return True
    return False


def parse_demand_excel(file_bytes: bytes) -> List[DemandItem]:
    """Parse the weekly release plan Excel and return DemandItem list.

    Reads only the four Pipe tabs (CPVC Pipe / UPVC Pipe / SWR Pipe / AGRI Pipe).
    Col A = Item Code, Col D = Production Plan pcs. Normalises all codes.
    Skips TOTAL rows, blank rows, and non-item rows.
    """
    if not _OPENPYXL:
        raise RuntimeError("openpyxl is required for demand upload.")

    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    items: List[DemandItem] = []

    for tab_name, material in PIPE_TABS.items():
        # Case-insensitive tab lookup
        ws = None
        for sn in wb.sheetnames:
            if sn.strip().lower() == tab_name.lower():
                ws = wb[sn]
                break
        if ws is None:
            continue  # tab absent — skip gracefully

        for row in ws.iter_rows(values_only=True):
            raw_a = str(row[_COL_ITEM]).strip() if row[_COL_ITEM] is not None else ""
            raw_d = row[_COL_QTY] if len(row) > _COL_QTY else None

            if _is_skip_row(raw_a, str(raw_d or "")):
                continue

            try:
                qty = float(str(raw_d).replace(",", "").strip())
            except (ValueError, TypeError):
                continue
            if qty <= 0:
                continue

            items.append(DemandItem(
                item_code=_norm_code(raw_a),
                raw_code=raw_a,
                material=material,
                qty_pcs=qty,
            ))

    return items


# ── Chain math helpers ────────────────────────────────────────────────────────

def _build_rate_lookups(
    per_hour_rows: List[dict],
    routing_rows: List[dict],
) -> Tuple[Dict[str, float], Dict[str, float], float]:
    """
    Returns:
        ph_dict      — {item_code: kg_per_hr}   (only basis='kg_per_hr')
        mat_avg      — {material: avg kg_per_hr}  (for fallback)
        overall_avg  — avg across all kg_per_hr pipe items (final fallback)
    """
    # material of each pipe item (from routing)
    routing_material: Dict[str, str] = {}
    for r in routing_rows:
        if r["machine"].startswith("M/C-"):
            routing_material.setdefault(r["item_code"], r.get("material", ""))

    ph_dict: Dict[str, float] = {}
    ph_by_mat: Dict[str, List[float]] = defaultdict(list)
    for r in per_hour_rows:
        if r["basis"] != "kg_per_hr":
            continue
        ic  = r["item_code"]
        val = float(r["value"])
        ph_dict[ic] = val
        mat = routing_material.get(ic, "")
        if mat:
            ph_by_mat[mat].append(val)

    mat_avg: Dict[str, float] = {
        m: sum(vs) / len(vs) for m, vs in ph_by_mat.items() if vs
    }
    all_vals = list(ph_dict.values())
    overall_avg = sum(all_vals) / len(all_vals) if all_vals else 1.0
    return ph_dict, mat_avg, overall_avg


def _get_rate(
    item_code: str,
    material: str,
    ph_dict: Dict[str, float],
    mat_avg: Dict[str, float],
    overall_avg: float,
) -> Tuple[float, bool]:
    """Return (rate_kg_per_hr, rate_estimated)."""
    if item_code in ph_dict:
        return ph_dict[item_code], False
    if material in mat_avg:
        return mat_avg[material], True
    return overall_avg, True


# ── Optimiser ────────────────────────────────────────────────────────────────

def _compute_machine_loads(
    items: List[ItemResult],
    machine_params: Dict[str, dict],   # {machine: {capacity_hrs_month, operators_ot, support_w}}
    use_assignments_attr: str = "assignments",
) -> List[MachineLoad]:
    """Aggregate per-machine totals from item.assignments (or item.baseline_assignments)."""
    acc: Dict[str, Dict] = {}
    for mc, p in machine_params.items():
        cap = float(p.get("capacity_hrs_month") or 500.0)
        acc[mc] = {
            "capacity_hrs": cap,
            "assigned_hrs": 0.0,
            "material_kg": 0.0,
            "fresh_compound_kg": 0.0,
            "pulverizer_kg": 0.0,
            "operators_ot": int(p.get("operators_ot") or 0),
            "support_w": int(p.get("support_w") or 0),
        }

    for item in items:
        asgns: List[AssignedPortion] = getattr(item, use_assignments_attr, [])
        for a in asgns:
            if a.machine in acc:
                mat_frac = a.material_kg
                fresh_frac = mat_frac * (1 - item.pulverizer_kg / item.material_kg) if item.material_kg > 0 else 0.0
                pulv_frac  = mat_frac - fresh_frac
                acc[a.machine]["assigned_hrs"]    += a.hrs
                acc[a.machine]["material_kg"]     += mat_frac
                acc[a.machine]["fresh_compound_kg"] += fresh_frac
                acc[a.machine]["pulverizer_kg"]   += pulv_frac

    loads = []
    for mc in sorted(acc.keys()):
        d = acc[mc]
        cap = d["capacity_hrs"]
        hrs = d["assigned_hrs"]
        util = hrs / cap * 100.0 if cap > 0 else 0.0
        days = hrs * 26.0 / cap if cap > 0 else 0.0
        ok   = (d["operators_ot"] > 0 or d["support_w"] > 0)
        loads.append(MachineLoad(
            machine=mc,
            capacity_hrs=cap,
            assigned_hrs=round(hrs, 3),
            utilisation_pct=round(util, 2),
            machine_days=round(days, 2),
            material_kg=round(d["material_kg"], 2),
            fresh_compound_kg=round(d["fresh_compound_kg"], 2),
            pulverizer_kg=round(d["pulverizer_kg"], 2),
            staffing_ok=ok,
            operators_ot=d["operators_ot"],
            support_w=d["support_w"],
        ))
    return loads


def _lpt_optimise(
    items: List[ItemResult],
    machine_caps: Dict[str, float],   # {machine: capacity_hrs_month}
) -> None:
    """
    LPT + parallel-split balancer. Mutates item.assignments in-place.

    Process items largest-material_kg first. For each item:
    - 1 capable machine: assign all.
    - 2+ capable machines:
        - If fits on least-loaded: assign all there.
        - Otherwise: split proportional to remaining capacity across ALL capable.
        - If ALL are full (0 remaining): assign to least-over (overflow).
    """
    # Sort descending by material_kg (LPT key)
    sorted_items = sorted(items, key=lambda x: x.material_kg, reverse=True)

    assigned: Dict[str, float] = {mc: 0.0 for mc in machine_caps}

    for item in sorted_items:
        item.assignments = []
        if not item.has_machine or not item.capable_machines:
            continue

        caps = [mc for mc in item.capable_machines if mc in machine_caps]
        if not caps:
            item.has_machine = False
            continue

        item_hrs = item.machine_hrs
        frac_per_hr = item.material_kg / item_hrs if item_hrs > 0 else 0.0

        if len(caps) == 1:
            mc = caps[0]
            item.assignments = [AssignedPortion(
                machine=mc, hrs=item_hrs,
                material_kg=item.material_kg, qty_pcs=item.qty_pcs,
            )]
            assigned[mc] += item_hrs
            continue

        # Find least-loaded by utilisation%
        least = min(caps, key=lambda mc: assigned[mc] / machine_caps[mc])
        remaining_least = max(0.0, machine_caps[least] - assigned[least])

        if item_hrs <= remaining_least + 1e-6:
            # Fits on least-loaded machine
            item.assignments = [AssignedPortion(
                machine=least, hrs=item_hrs,
                material_kg=item.material_kg, qty_pcs=item.qty_pcs,
            )]
            assigned[least] += item_hrs
        else:
            # Parallel split proportional to remaining capacity
            remainders = {mc: max(0.0, machine_caps[mc] - assigned[mc]) for mc in caps}
            total_rem = sum(remainders.values())

            if total_rem <= 1e-6:
                # All machines at/over capacity — assign overflow to least over
                mc = min(caps, key=lambda mc: assigned[mc] / machine_caps[mc])
                item.assignments = [AssignedPortion(
                    machine=mc, hrs=item_hrs,
                    material_kg=item.material_kg, qty_pcs=item.qty_pcs,
                )]
                assigned[mc] += item_hrs
            else:
                for mc in caps:
                    frac = remainders[mc] / total_rem
                    hrs_frac = item_hrs * frac
                    if hrs_frac < 1e-4:
                        continue
                    item.assignments.append(AssignedPortion(
                        machine=mc,
                        hrs=round(hrs_frac, 4),
                        material_kg=round(item.material_kg * frac, 4),
                        qty_pcs=round(item.qty_pcs * frac, 2),
                    ))
                    assigned[mc] += hrs_frac


def _baseline_assign(items: List[ItemResult]) -> None:
    """
    Unbalanced baseline: each item → first capable machine (sorted), no split.
    Stores result in item.assignments (baseline overwrites; caller saves optimised
    first and restores after).
    """
    for item in items:
        item.assignments = []
        if not item.has_machine or not item.capable_machines:
            continue
        mc = sorted(item.capable_machines)[0]
        item.assignments = [AssignedPortion(
            machine=mc, hrs=item.machine_hrs,
            material_kg=item.material_kg, qty_pcs=item.qty_pcs,
        )]


# ── Main entry point ─────────────────────────────────────────────────────────

def run_engine(
    demand: List[DemandItem],
    effective_month: str,
    segment: str = _DEFAULT_SEGMENT,
) -> EngineResult:
    """
    Run the full MP-2 engine:
    1. Load mp_* inputs from DB for (segment, effective_month).
    2. Apply per-item chain math.
    3. LPT + parallel-split optimisation.
    4. Unbalanced baseline.
    5. Return EngineResult with all outputs.
    """
    # ── Load all inputs from DB ──────────────────────────────────────────────
    params_row = _mp.get_params(segment, effective_month)
    waste_pct     = float(params_row.waste_pct)     if params_row else 4.0
    pulv_pct      = float(params_row.pulverizer_pct) if params_row else 25.0

    bom_rows   = _mp.get_bom_weight_rows(segment, effective_month)
    ph_rows    = _mp.get_per_hour(segment, effective_month)
    routing    = _mp.get_routing(segment, effective_month)
    machines   = _mp.get_machines(segment, effective_month, kind="extrusion")

    bom: Dict[str, float] = {r["item_code"]: float(r["weight_per_pc_kg"]) for r in bom_rows}

    # Per-machine params
    mc_params: Dict[str, dict] = {m["machine"]: m for m in machines}
    mc_caps: Dict[str, float] = {
        mc: float(p.get("capacity_hrs_month") or 500.0)
        for mc, p in mc_params.items()
    }

    # Routing: pipe items → capable machines
    pipe_caps: Dict[str, List[str]] = defaultdict(list)  # {item_code: [machines]}
    routed_machines: set = set()
    for r in routing:
        mc = r["machine"]
        if mc.startswith("M/C-") and r.get("capable", True):
            ic = r["item_code"]
            if mc not in pipe_caps[ic]:
                pipe_caps[ic].append(mc)
            routed_machines.add(mc)

    # Rate lookups
    ph_dict, mat_avg, overall_avg = _build_rate_lookups(ph_rows, routing)

    # ── Per-item chain math ──────────────────────────────────────────────────
    items: List[ItemResult] = []
    no_weight: List[str] = []
    no_machine: List[str] = []

    for d in demand:
        ic  = d.item_code
        mat = d.material
        qty = d.qty_pcs

        wt = bom.get(ic)
        has_weight = wt is not None
        if not has_weight:
            no_weight.append(ic)
            # Still record the item but with zero chain math
            items.append(ItemResult(
                item_code=ic, raw_code=d.raw_code,
                material=mat, qty_pcs=qty,
                weight_per_pc_kg=None,
                material_kg=0.0, fresh_compound_kg=0.0, pulverizer_kg=0.0,
                rate_kg_per_hr=0.0, rate_estimated=False,
                machine_hrs=0.0,
                capable_machines=pipe_caps.get(ic, []),
                assignments=[],
                has_weight=False, has_machine=bool(pipe_caps.get(ic)),
            ))
            continue

        material_kg = qty * wt * (1.0 + waste_pct / 100.0)
        fresh       = material_kg * (1.0 - pulv_pct / 100.0)
        pulv        = material_kg - fresh

        rate, estimated = _get_rate(ic, mat, ph_dict, mat_avg, overall_avg)
        if rate <= 0:
            rate = overall_avg
            estimated = True
        machine_hrs = material_kg / rate if rate > 0 else 0.0

        caps = pipe_caps.get(ic, [])
        has_mc = bool(caps)
        if not has_mc:
            no_machine.append(ic)

        items.append(ItemResult(
            item_code=ic, raw_code=d.raw_code,
            material=mat, qty_pcs=qty,
            weight_per_pc_kg=wt,
            material_kg=round(material_kg, 4),
            fresh_compound_kg=round(fresh, 4),
            pulverizer_kg=round(pulv, 4),
            rate_kg_per_hr=round(rate, 4),
            rate_estimated=estimated,
            machine_hrs=round(machine_hrs, 4),
            capable_machines=sorted(caps),
            assignments=[],
            has_weight=True, has_machine=has_mc,
        ))

    # Routable items only (has_weight=True AND has_machine=True)
    routable = [it for it in items if it.has_weight and it.has_machine]

    # ── LPT optimisation ────────────────────────────────────────────────────
    _lpt_optimise(routable, mc_caps)

    # Save optimised assignments
    opt_assignments: Dict[str, List[AssignedPortion]] = {
        it.item_code + "|" + it.raw_code: list(it.assignments) for it in routable
    }

    opt_loads = _compute_machine_loads(routable, mc_params)

    # ── Baseline (unbalanced) ────────────────────────────────────────────────
    _baseline_assign(routable)
    base_loads = _compute_machine_loads(routable, mc_params)

    # Restore optimised assignments on items
    for it in routable:
        key = it.item_code + "|" + it.raw_code
        it.assignments = opt_assignments.get(key, [])

    # ── Coverage gaps ────────────────────────────────────────────────────────
    assigned_machines = {a.machine for it in routable for a in it.assignments}
    idle = sorted(routed_machines - assigned_machines - set(no_machine))
    locked_out = sorted(set(mc_params.keys()) - routed_machines)

    gaps = CoverageGaps(
        no_weight=sorted(set(no_weight)),
        no_machine=sorted(set(no_machine)),
        idle_machines=idle,
        locked_out_machines=locked_out,
    )

    # ── Totals ───────────────────────────────────────────────────────────────
    total_qty     = sum(it.qty_pcs       for it in items)
    total_mat_kg  = sum(it.material_kg   for it in items if it.has_weight)
    total_fresh   = sum(it.fresh_compound_kg for it in items if it.has_weight)
    total_pulv    = sum(it.pulverizer_kg for it in items if it.has_weight)

    rout_mat_kg   = sum(it.material_kg   for it in routable)
    rout_fresh    = sum(it.fresh_compound_kg for it in routable)
    rout_pulv     = sum(it.pulverizer_kg for it in routable)

    totals = PlanTotals(
        total_qty_pcs=round(total_qty, 0),
        total_material_kg=round(total_mat_kg, 2),
        total_fresh_compound_kg=round(total_fresh, 2),
        total_pulverizer_kg=round(total_pulv, 2),
        routable_material_kg=round(rout_mat_kg, 2),
        routable_fresh_compound_kg=round(rout_fresh, 2),
        routable_pulverizer_kg=round(rout_pulv, 2),
    )

    return EngineResult(
        segment=segment,
        effective_month=effective_month,
        items=items,
        machine_loads=opt_loads,
        coverage_gaps=gaps,
        totals=totals,
        baseline_machine_loads=base_loads,
        params_used={"waste_pct": waste_pct, "pulverizer_pct": pulv_pct},
    )
