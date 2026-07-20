"""
Dataclasses for the planning / demand domain.

These live OUTSIDE the Record/metrics pipeline — planning data is a separate
domain loaded on-demand (never on the "/" critical path) via
sheets.load_planning / load_ptmt_pieces / load_ptmt_master.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PlanRecord:
    """One item line from a PIPE Report-1 or PTMT Report-1 demand snapshot."""
    plant: str              # "PIPE" | "PTMT"
    family: str             # "CPVC" | "UPVC" | "AGRI" | "SWR" | "faucet" | "cistern" | "seatcover"
    category: str           # sub-section label within the tab ("CPVC PIPE", "UPVC FITTING", …)
    item_code: str
    item_name: str
    wt_kg: float            # weight per piece in kg (PIPE); 0 for PTMT (no wt col)
    ideal_qty: float        # SET IDEAL QTY / SET IDEAL STOCK
    avg_sale_90d: float     # "Last 90 Days" for PIPE; "Last 30 Days" for PTMT
    per_hour_output: float  # 0 for PTMT Report-1 (not present in that tab)
    produce_required: float # Production Require in the Month; 0 for PTMT (no col)
    produced: float         # Production in the Month
    closing_stock: float
    opening_stock: float    # PIPE only; 0 for PTMT
    as_of_date: str         # date label from closing-stock sub-header, e.g. "Jun, 30"
    # Derived — set by compute_plan_metrics()
    net_requirement: float = 0.0
    days_of_cover: Optional[float] = None


@dataclass
class MouldStd:
    """One row from PTMT MASTER — mould + machine standard specification."""
    item_code: str
    item_name: str
    mould_cavity: int       # Mould Cavity (F)
    cycle_time_secs: float  # Cycle Time (F) — raw seconds per full cycle
    cycle_time_per_pcs: float  # Cycle Time Per Pcs Standard (F) = cycle_time / cavities
    wt_per_pc_gms: float    # Wt per Pc in Gms (F)
    runner_weight_gms: float
    runner_wt_per_pcs_gms: float
    machine_name: str
    # Derived
    theoretical_pcs_hr: float = 0.0  # 3 600 / cycle_time_per_pcs (when > 0)


def compute_plan_metrics(rec: PlanRecord) -> PlanRecord:
    """Compute net_requirement and days_of_cover in-place, return same object."""
    gap1 = max(rec.produce_required - rec.produced, 0.0)
    gap2 = max(rec.ideal_qty - rec.closing_stock, 0.0)
    rec.net_requirement = max(gap1, gap2)
    if rec.avg_sale_90d > 0:
        daily_sale = rec.avg_sale_90d / 90.0
        rec.days_of_cover = rec.closing_stock / daily_sale if daily_sale > 0 else None
    else:
        rec.days_of_cover = None
    return rec


def compute_mould_std(std: MouldStd) -> MouldStd:
    """Fill theoretical_pcs_hr; return same object."""
    if std.cycle_time_per_pcs > 0:
        std.theoretical_pcs_hr = 3600.0 / std.cycle_time_per_pcs
    return std


@dataclass
class MaterialRecord:
    """One item from a PIPE/PTMT Report-2/3/4 material-stock snapshot.

    days_of_cover and reorder metrics are recomputed by
    compute_material_metrics(); never trust the sheet-supplied 'Stock Days'
    as the headline (stored as stock_days_sheet for cross-check only).
    """
    plant: str                          # "PIPE" | "PTMT"
    category: str                       # "RM" | "BOP" | "PACK"
    item_code: str
    item_name: str
    item_type: str                      # TYPES column (may be empty)
    avg_price: float                    # Av. Purchase Price of Last N Days
    avg_consumption_month: float        # Consumption / Avg Consumption of Last month
    ideal_stock: float                  # SET IDEAL QTY or equivalent (units)
    min_batch: float                    # Min. Batch Size
    lead_time_days: float               # LEAD TIME (days; stripped of any text suffix)
    opening_stock: float                # Opening Stock (PIPE only; 0 for PTMT)
    closing_stock: float                # Closing Stock
    purchase_till: float                # Purchase Till (cumulative purchases this month)
    consumption_till: float             # Consumption till Date (cumulative this month)
    stock_days_sheet: Optional[float]   # Pre-computed "Stock Days" from PIPE sheet; None for PTMT
    days_of_cover: Optional[float]      # Recomputed: closing / (avg_consumption / 30)
    as_of_date: str                     # Snapshot date label from sheet header
    reorder_flag: bool = False          # cover <= lead_time_days
    suggested_purchase: float = 0.0    # max(ideal_stock − closing, min_batch) when reorder


def compute_material_metrics(rec: "MaterialRecord") -> "MaterialRecord":
    """Compute days_of_cover, reorder_flag, and suggested_purchase in-place."""
    daily = rec.avg_consumption_month / 30.0 if rec.avg_consumption_month > 0 else 0.0
    if daily > 0 and rec.closing_stock >= 0:
        rec.days_of_cover = rec.closing_stock / daily
    else:
        rec.days_of_cover = None

    rec.reorder_flag = bool(
        rec.days_of_cover is not None
        and rec.lead_time_days > 0
        and rec.days_of_cover <= rec.lead_time_days
    )
    if rec.reorder_flag and (rec.ideal_stock > 0 or rec.min_batch > 0):
        shortfall = rec.ideal_stock - rec.closing_stock
        rec.suggested_purchase = max(shortfall, rec.min_batch)
    else:
        rec.suggested_purchase = 0.0
    return rec


# ---------------------------------------------------------------------------
# Phase 2C — Maintenance master
# ---------------------------------------------------------------------------

@dataclass
class MaintenanceRecord:
    """One machine row from PIPE Report-16 or PTMT Report-8 maintenance master.

    Phase 2C — loaded on-demand from /maintenance, never on '/'.
    purchase_date is the raw sheet text; machine_age_years is derived
    by compute_maintenance_metrics() using today's date.
    """
    plant: str
    machine: str
    make: str
    purchase_date: str          # raw: "Jan-20", "01-06-2018", etc.
    cost: float
    amc_applicable: str         # raw: "YES" / "NO" / "NA" / ""
    pm_required: str            # Monthly Preventive Maintenance Required schedule text
    check_points: str           # Monthly Check Points description
    spares: str                 # Spare to be kept in stock
    service_engineer: str
    service_mobile: str
    service_location: str
    service_lead_time_days: float   # Lead Time to Reach factory (numeric days)
    # Derived
    machine_age_years: Optional[float] = None


def compute_maintenance_metrics(rec: "MaintenanceRecord") -> "MaintenanceRecord":
    """Derive machine_age_years from purchase_date text; return same object."""
    import datetime
    pd_str = rec.purchase_date.strip()
    if not pd_str:
        return rec
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d-%b-%y",
                "%b-%Y", "%b-%y", "%B-%Y", "%Y-%m-%d", "%m/%Y"):
        try:
            dt = datetime.datetime.strptime(pd_str, fmt).date()
            delta_days = (datetime.date.today() - dt).days
            if delta_days > 0:
                rec.machine_age_years = round(delta_days / 365.25, 1)
            break
        except ValueError:
            continue
    return rec


# ---------------------------------------------------------------------------
# Phase 2C — Manpower roster
# ---------------------------------------------------------------------------

@dataclass
class ManpowerRecord:
    """One machine × date × shift manpower entry.

    Phase 2C — loaded on-demand from /manpower, never on '/'.

    PIPE Report-22 (A/B): shift='all', man_hours populated, type_flag=''.
    PTMT Report-6 (A/B/C): shift='1st'/'2nd'/'3rd', man_hours=0.0,
    type_flag = P/C roster flag from the sheet.

    INVARIANT: This record NEVER represents production output.
    """
    plant: str
    machine: str
    date: str               # ISO "2026-06-01"
    shift: str              # "all" | "1st" | "2nd" | "3rd"
    required_manpower: float   # static "REQUIREMENT OF MANPOWER" col (PIPE); 0 for PTMT
    actual_manpower: float     # per-date TOTAL MANPOWER (PIPE) or shift count (PTMT)
    man_hours: float           # per-date TOTAL HOURS (PIPE); 0.0 for PTMT
    type_flag: str             # "" (PIPE) | "P"/"C" (PTMT shift-roster type flag)


# ---------------------------------------------------------------------------
# Phase 2D — Yield / daily production pivot (PIPE Report-15 / 13 / 14)
# ---------------------------------------------------------------------------

@dataclass
class YieldRecord:
    """One type × date production/wastage row from PIPE yield reports.

    Phase 2D — loaded on-demand from /yield, NEVER on '/'.

    source="R15_kg"  → production_kg / wastage_kg / pulverizer_consumed_kg populated
                       from Report-15 (weight-based).
    source="R13_pcs" → production_pcs / target_pcs populated from Report-13 (pipe daily pcs).
    source="R14_pcs" → production_pcs / target_pcs populated from Report-14 (fittings daily pcs).

    yield_pct is computed only for R15_kg rows (production_kg / (production_kg + wastage_kg) × 100).
    """
    plant: str
    date: str                       # ISO "2026-06-01"
    type: str                       # "CPVC" | "UPVC" | "SWR" | "AGRI" | "UPVC_F" | "SWR_F"
    production_pcs: float = 0.0
    production_kg: float = 0.0
    wastage_kg: float = 0.0
    pulverizer_consumed_kg: float = 0.0
    target_pcs: float = 0.0
    yield_pct: Optional[float] = None
    source: str = ""


def compute_yield_metrics(rec: "YieldRecord") -> "YieldRecord":
    """yield_pct = production_kg / (production_kg + wastage_kg) × 100."""
    if rec.production_kg > 0:
        total = rec.production_kg + rec.wastage_kg
        if total > 0:
            rec.yield_pct = round(rec.production_kg / total * 100.0, 2)
    return rec


# ---------------------------------------------------------------------------
# Phase 2D — Compound mixer batch log (PIPE Report-5(A/B/C/D))
# ---------------------------------------------------------------------------

@dataclass
class CompoundBatchRecord:
    """One mixer batch log row from PIPE Report-5(A/B/C/D).

    Phase 2D — loaded on-demand from /mixer, NEVER on '/'.
    DISTINCT from compound.py CP-fittings mass-balance (existing /compound route).

    mixer_availability = running_hours / (running_hours + breakdown_hours).
    """
    plant: str
    date: str           # ISO
    mixer_id: str       # "A" | "B" | "C" | "D"
    batch_type: str
    batch_size: float
    num_batches: float
    total_compound_kg: float
    running_hours: float
    breakdown_hours: float
    shift: str
    mixer_availability: Optional[float] = None


def compute_mixer_metrics(rec: "CompoundBatchRecord") -> "CompoundBatchRecord":
    """mixer_availability = running_hours / (running_hours + breakdown_hours)."""
    total = rec.running_hours + rec.breakdown_hours
    if total > 0:
        rec.mixer_availability = round(rec.running_hours / total, 4)
    return rec


# ---------------------------------------------------------------------------
# Phase 2D — Toolroom job log (PIPE Report-21)
# ---------------------------------------------------------------------------

@dataclass
class ToolroomRecord:
    """One toolroom job row from PIPE Report-21.

    Phase 2D — loaded on-demand from /toolroom, NEVER on '/'.
    ~24 job rows expected per month.
    """
    plant: str
    date: str
    machine: str
    item: str
    work_detail: str
    remarks: str
    manpower: float
    working_hours: float


# ---------------------------------------------------------------------------
# Phase 2D — Scrap / wastage master (PTMT Report-10)
# ---------------------------------------------------------------------------

@dataclass
class WastageRecord:
    """One scrap/wastage row from PTMT Report-10.

    Phase 2D — loaded on-demand from /wastage, NEVER on '/'.
    ~33 rows expected.

    INVARIANT: unit varies (KG / PCS / LTR) — NEVER sum across units.
    """
    plant: str
    department: str
    waste_item: str
    unit: str               # "KG" | "PCS" | "LTR"  — never sum across
    avg_waste_per_week: float
    dispose_cycle: str
    responsible_person: str  # RESPONSIBLE PERSON FOR DISPOSE
    approx_sale_value: float
