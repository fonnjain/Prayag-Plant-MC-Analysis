"""
Deterministic metrics engine — all arithmetic is pure Python.
No AI model touches these numbers.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import math


@dataclass
class ShiftRow:
    date: str          # ISO yyyy-mm-dd
    plant: str
    machine: str
    mould: str
    segment: str
    product: str
    shift: str
    ideal_rate: float  # pcs/hr (or kg/hr, ltr/hr)
    shift_len_min: float
    planned_stops_min: float
    downtime_min: float
    downtime_reason: str
    total_count: float
    reject_count: float
    planned_output: float
    unit: str          # pcs / kg / ltr
    labour_cost: float = 0.0
    power_cost: float = 0.0
    solar_cost: float = 0.0
    runner_lumps: float = 0.0
    compound_type: str = ""


@dataclass
class MetricsResult:
    # Time components
    shift_len_min: float = 0.0
    planned_stops_min: float = 0.0
    ppt: float = 0.0          # Planned Production Time
    downtime_min: float = 0.0
    run_time: float = 0.0

    # Counts
    total_count: float = 0.0
    reject_count: float = 0.0
    good_count: float = 0.0
    runner_lumps: float = 0.0
    planned_output: float = 0.0

    # Rates
    availability: float = 0.0
    performance: float = 0.0
    quality: float = 0.0
    oee: float = 0.0
    rejection_pct: float = 0.0
    runner_pct: float = 0.0
    attainment: float = 0.0
    utilisation: float = 0.0

    # Costs
    labour_cost: float = 0.0
    power_cost: float = 0.0
    solar_cost: float = 0.0

    # Row count
    row_count: int = 0

    # Warnings from validation
    warnings: List[str] = field(default_factory=list)

    @property
    def oee_rating(self) -> str:
        if self.oee >= 0.85:
            return "green"
        elif self.oee >= 0.60:
            return "amber"
        return "red"

    @property
    def oee_pct(self) -> float:
        return round(self.oee * 100, 1)

    @property
    def availability_pct(self) -> float:
        return round(self.availability * 100, 1)

    @property
    def performance_pct(self) -> float:
        return round(self.performance * 100, 1)

    @property
    def quality_pct(self) -> float:
        return round(self.quality * 100, 1)

    @property
    def attainment_pct(self) -> float:
        return round(self.attainment * 100, 1)

    @property
    def utilisation_pct(self) -> float:
        return round(self.utilisation * 100, 1)

    @property
    def rejection_pct_display(self) -> float:
        return round(self.rejection_pct * 100, 2)

    def to_dict(self) -> dict:
        return {
            "shift_len_min": round(self.shift_len_min, 1),
            "planned_stops_min": round(self.planned_stops_min, 1),
            "ppt": round(self.ppt, 1),
            "downtime_min": round(self.downtime_min, 1),
            "run_time": round(self.run_time, 1),
            "total_count": round(self.total_count, 2),
            "reject_count": round(self.reject_count, 2),
            "good_count": round(self.good_count, 2),
            "runner_lumps": round(self.runner_lumps, 2),
            "planned_output": round(self.planned_output, 2),
            "availability": self.availability_pct,
            "performance": self.performance_pct,
            "quality": self.quality_pct,
            "oee": self.oee_pct,
            "rejection_pct": self.rejection_pct_display,
            "runner_pct": round(self.runner_pct * 100, 2),
            "attainment": self.attainment_pct,
            "utilisation": self.utilisation_pct,
            "labour_cost": round(self.labour_cost, 2),
            "power_cost": round(self.power_cost, 2),
            "solar_cost": round(self.solar_cost, 2),
            "row_count": self.row_count,
            "oee_rating": self.oee_rating,
            "warnings": self.warnings,
        }


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or math.isnan(denominator) or math.isinf(denominator):
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def compute_metrics(rows: List[ShiftRow]) -> MetricsResult:
    """Aggregate a list of ShiftRow into a MetricsResult. Pure arithmetic."""
    m = MetricsResult()
    m.row_count = len(rows)
    if not rows:
        return m

    for r in rows:
        m.shift_len_min += r.shift_len_min
        m.planned_stops_min += r.planned_stops_min
        m.downtime_min += r.downtime_min
        m.total_count += r.total_count
        m.reject_count += r.reject_count
        m.runner_lumps += r.runner_lumps
        m.planned_output += r.planned_output
        m.labour_cost += r.labour_cost
        m.power_cost += r.power_cost
        m.solar_cost += r.solar_cost

    m.ppt = m.shift_len_min - m.planned_stops_min
    m.run_time = m.ppt - m.downtime_min
    m.good_count = m.total_count - m.reject_count

    # Guard run_time — downtime cannot exceed PPT (validation may flag this)
    effective_run_time = max(m.run_time, 0.0)

    # OEE components
    m.availability = _safe_div(effective_run_time, m.ppt)

    # Performance: total produced / (run_time_hrs * ideal_rate_weighted)
    # Weight ideal_rate by run_time contribution per row
    weighted_ideal = 0.0
    for r in rows:
        row_ppt = r.shift_len_min - r.planned_stops_min
        row_run = max(row_ppt - r.downtime_min, 0.0)
        weighted_ideal += row_run * r.ideal_rate
    ideal_theoretical = weighted_ideal / 60.0  # convert to hours → units
    m.performance = _safe_div(m.total_count, ideal_theoretical)
    m.performance = min(m.performance, 1.0)   # cap at 100% for display; >100% flagged separately

    m.quality = _safe_div(m.good_count, m.total_count)

    m.oee = m.availability * m.performance * m.quality

    m.rejection_pct = _safe_div(m.reject_count, m.total_count)
    m.runner_pct = _safe_div(m.runner_lumps, m.total_count)
    m.attainment = _safe_div(m.total_count, m.planned_output)

    # Utilisation = run_time / shift_len (actual vs ideal capacity)
    m.utilisation = _safe_div(effective_run_time, m.shift_len_min)

    return m


def rollup(rows: List[ShiftRow], group_by: str) -> Dict[str, MetricsResult]:
    """Group rows by a field and compute metrics per group."""
    groups: Dict[str, List[ShiftRow]] = {}
    for r in rows:
        key = getattr(r, group_by, "Unknown")
        groups.setdefault(key, []).append(r)
    return {k: compute_metrics(v) for k, v in groups.items()}


def rollup_by_date(rows: List[ShiftRow]) -> Dict[str, MetricsResult]:
    return rollup(rows, "date")


def rollup_by_plant(rows: List[ShiftRow]) -> Dict[str, MetricsResult]:
    return rollup(rows, "plant")


def rollup_by_machine(rows: List[ShiftRow]) -> Dict[str, MetricsResult]:
    return rollup(rows, "machine")


def rollup_by_mould(rows: List[ShiftRow]) -> Dict[str, MetricsResult]:
    return rollup(rows, "mould")


def rollup_by_segment(rows: List[ShiftRow]) -> Dict[str, MetricsResult]:
    return rollup(rows, "segment")


def downtime_pareto(rows: List[ShiftRow]) -> List[Dict]:
    """Return downtime minutes by reason code, sorted desc, with cumulative %."""
    reason_map: Dict[str, float] = {}
    for r in rows:
        if r.downtime_min > 0 and r.downtime_reason:
            reason_map[r.downtime_reason] = reason_map.get(r.downtime_reason, 0) + r.downtime_min

    total = sum(reason_map.values())
    if total == 0:
        return []

    sorted_reasons = sorted(reason_map.items(), key=lambda x: x[1], reverse=True)
    cumulative = 0.0
    result = []
    for reason, mins in sorted_reasons:
        cumulative += mins
        result.append({
            "reason": reason,
            "minutes": round(mins, 1),
            "pct": round(mins / total * 100, 1),
            "cumulative_pct": round(cumulative / total * 100, 1),
        })
    return result
