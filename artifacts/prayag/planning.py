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
