"""
Deterministic metrics engine — all arithmetic is pure Python.
No AI model touches these numbers.

A single grain-agnostic ``Record`` carries both:
  * monthly-grain rows (annual summary grids): actual/ideal HOURS and OUTPUT.
  * daily-grain rows (per-day mixer/shift logs): shift length, downtime,
    ideal rate — enough to compute true OEE (A x P x Q).

``compute_metrics`` recomputes every ratio from raw values (never trusts a
stored % cell). OEE (Availability x Performance x Quality) is only produced
when OEE-capable daily rows are present; for hours-only monthly data we report
Utilisation and Output Efficiency instead and mark ``oee_available = False``.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Dict
import math


@dataclass
class Record:
    # --- identity / dimensions ---
    grain: str = "monthly"        # "monthly" | "daily"
    period: str = ""              # "2026-04" (monthly) or "2026-04-07" (daily)
    date: str = ""                # ISO date used for trend bucketing
    plant: str = ""
    segment: str = ""
    machine: str = ""
    mould: str = ""
    product: str = ""
    material: str = ""            # CPVC / UPVC / AGRI / SWR ...
    unit: str = "kg"             # kg / pcs / ltr

    # --- production ---
    total_count: float = 0.0      # actual output (kg / pcs / ltr)
    reject_count: float = 0.0
    runner_lumps: float = 0.0
    planned_output: float = 0.0
    ideal_output: float = 0.0     # monthly: ideal output for the period

    # --- hours (monthly grain) ---
    actual_hours: float = 0.0
    ideal_hours: float = 0.0       # the value USED as the utilisation/efficiency
                                   # denominator (a config baseline when set,
                                   # otherwise the sheet placeholder)
    ideal_hours_sheet: float = 0.0  # raw planned hours as read from the sheet
    ideal_source: str = "sheet"     # "config" baseline | "sheet" placeholder

    # --- classification ---
    is_finishing: bool = False    # finishing/regrind line (e.g. PTMT Grinders).
                                  # Its throughput is regrind, not new production,
                                  # so it is excluded from a MIXED rollup's output
                                  # totals (its own segment still shows itself).

    # --- daily-grain OEE inputs ---
    has_oee: bool = False
    shift: str = ""
    shift_len_min: float = 0.0
    planned_stops_min: float = 0.0
    downtime_min: float = 0.0
    downtime_reason: str = ""
    ideal_rate: float = 0.0       # pcs/hr | kg/hr | ltr/hr

    # --- costs ---
    labour_cost: float = 0.0
    power_cost: float = 0.0
    solar_cost: float = 0.0
    compound_type: str = ""

    # --- provenance ---
    source_family: str = ""
    source_file: str = ""
    source_tab: str = ""


# Backwards-compatible alias for older call sites / demo data.
ShiftRow = Record


def _rate_band(x: float) -> str:
    if x >= 0.85:
        return "green"
    if x >= 0.60:
        return "amber"
    return "red"


@dataclass
class MetricsResult:
    # Time components (minutes)
    shift_len_min: float = 0.0
    planned_stops_min: float = 0.0
    ppt: float = 0.0
    downtime_min: float = 0.0
    run_time: float = 0.0

    # Hours (for monthly utilisation/efficiency)
    actual_hours: float = 0.0
    ideal_hours: float = 0.0
    ideal_output: float = 0.0

    # Counts
    total_count: float = 0.0
    reject_count: float = 0.0
    good_count: float = 0.0
    runner_lumps: float = 0.0
    planned_output: float = 0.0

    # Rates (0..1)
    availability: float = 0.0
    performance: float = 0.0
    performance_raw: float = 0.0  # unclamped — for validity checks (>100% = invalid)
    quality: float = 0.0
    oee: float = 0.0
    rejection_pct: float = 0.0
    runner_pct: float = 0.0
    attainment: float = 0.0
    utilisation: float = 0.0
    output_efficiency: float = 0.0

    oee_available: bool = False
    # Whether a real ideal/planned baseline backs the ratio. False for plants
    # with no monthly grid, no in-sheet ideal column and no config baseline
    # (e.g. TANK, Moulding daily): we still report run hours + output, but the
    # ratio is suppressed in the UI rather than shown as a misleading 0%.
    util_available: bool = False
    eff_available: bool = False

    # Costs
    labour_cost: float = 0.0
    power_cost: float = 0.0
    solar_cost: float = 0.0

    row_count: int = 0
    warnings: List[str] = field(default_factory=list)

    # ---- ratings ----
    @property
    def oee_rating(self) -> str:
        return _rate_band(self.oee)

    @property
    def util_rating(self) -> str:
        return _rate_band(self.utilisation)

    @property
    def eff_rating(self) -> str:
        return _rate_band(self.output_efficiency)

    @property
    def headline_available(self) -> bool:
        """True when some baseline-backed headline KPI can be shown."""
        return self.oee_available or self.eff_available or self.util_available

    @property
    def headline(self) -> float:
        """Primary KPI: OEE, else Output Efficiency, else Utilisation.

        Falls back through the ratios that actually have a baseline behind them
        so a utilisation-only plant (e.g. PTMT, with in-sheet ideal hours but no
        output rate) still shows a real figure instead of a misleading 0%.
        """
        if self.oee_available:
            return self.oee
        if self.eff_available:
            return self.output_efficiency
        if self.util_available:
            return self.utilisation
        return 0.0

    @property
    def headline_label(self) -> str:
        if self.oee_available:
            return "OEE"
        if self.eff_available:
            return "Output Efficiency"
        if self.util_available:
            return "Utilisation"
        return "No baseline set"

    @property
    def headline_rating(self) -> str:
        if self.oee_available:
            return self.oee_rating
        if self.eff_available:
            return self.eff_rating
        if self.util_available:
            return self.util_rating
        return "red"

    # ---- percentage helpers ----
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
    def output_efficiency_pct(self) -> float:
        return round(self.output_efficiency * 100, 1)

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
            "actual_hours": round(self.actual_hours, 1),
            "ideal_hours": round(self.ideal_hours, 1),
            "ideal_output": round(self.ideal_output, 2),
            "total_count": round(self.total_count, 2),
            "reject_count": round(self.reject_count, 2),
            "good_count": round(self.good_count, 2),
            "runner_lumps": round(self.runner_lumps, 2),
            "planned_output": round(self.planned_output, 2),
            "availability": self.availability_pct,
            "performance": self.performance_pct,
            "quality": self.quality_pct,
            "oee": self.oee_pct,
            "oee_available": self.oee_available,
            "util_available": self.util_available,
            "eff_available": self.eff_available,
            "headline_available": self.headline_available,
            "rejection_pct": self.rejection_pct_display,
            "runner_pct": round(self.runner_pct * 100, 2),
            "attainment": self.attainment_pct,
            "utilisation": self.utilisation_pct,
            "output_efficiency": self.output_efficiency_pct,
            "labour_cost": round(self.labour_cost, 2),
            "power_cost": round(self.power_cost, 2),
            "solar_cost": round(self.solar_cost, 2),
            "row_count": self.row_count,
            "oee_rating": self.oee_rating,
            "util_rating": self.util_rating,
            "eff_rating": self.eff_rating,
            "headline": round(self.headline * 100, 1),
            "headline_label": self.headline_label,
            "headline_rating": self.headline_rating,
            "warnings": self.warnings,
        }


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0 or math.isnan(denominator) or math.isinf(denominator):
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def compute_metrics(rows: List[Record]) -> MetricsResult:
    """Aggregate a list of Record into a MetricsResult. Pure arithmetic."""
    m = MetricsResult()
    m.row_count = len(rows)
    if not rows:
        return m

    # Finishing/regrind lines (e.g. PTMT Grinders) reprocess scrap — their
    # throughput is not new production. In a MIXED rollup (plant/overall/period)
    # they are excluded so regrind KG is never added to plant output, and their
    # run hours never enter the utilisation numerator. A rollup that is ENTIRELY
    # finishing (the Grinding segment viewed on its own) still shows itself.
    non_fin = [r for r in rows if not r.is_finishing]
    prod_rows = non_fin if non_fin else rows

    # Utilisation/efficiency numerators only accumulate hours/output that have a
    # real baseline behind them (ideal_hours / ideal_output > 0). A no-baseline
    # plant (TANK, Moulding daily) still contributes to the output and run-hour
    # TOTALS below, but must NOT pollute the ratio — otherwise its run hours land
    # in the numerator with a zero denominator and silently inflate the figure.
    util_run = 0.0
    eff_out = 0.0
    for r in prod_rows:
        m.total_count += r.total_count
        m.reject_count += r.reject_count
        m.runner_lumps += r.runner_lumps
        m.planned_output += r.planned_output
        m.labour_cost += r.labour_cost
        m.power_cost += r.power_cost
        m.solar_cost += r.solar_cost

        if r.grain == "daily" and r.shift_len_min > 0:
            # True shift-log row (mixer/shift log): derive worked vs available
            # hours from the time model. These rows also feed OEE below.
            row_ppt = r.shift_len_min - r.planned_stops_min
            row_run = max(row_ppt - r.downtime_min, 0.0)
            m.actual_hours += row_run / 60.0
            m.ideal_hours += r.shift_len_min / 60.0
            m.ideal_output += (row_run / 60.0) * r.ideal_rate
            m.downtime_min += r.downtime_min
            util_run += row_run / 60.0
            if r.ideal_rate > 0:
                eff_out += r.total_count
        else:
            # Monthly-grain rows AND daily-matrix rows (per-date production
            # grids) both carry hours/output directly — no shift timing to model.
            m.actual_hours += r.actual_hours
            m.ideal_hours += r.ideal_hours
            m.ideal_output += r.ideal_output
            if r.ideal_hours > 0:
                util_run += r.actual_hours
            if r.ideal_output > 0:
                eff_out += r.total_count

    m.good_count = m.total_count - m.reject_count
    m.run_time = m.actual_hours * 60.0
    m.shift_len_min = m.ideal_hours * 60.0

    m.utilisation = _safe_div(util_run, m.ideal_hours)
    m.output_efficiency = _safe_div(eff_out, m.ideal_output)
    m.util_available = m.ideal_hours > 0
    m.eff_available = m.ideal_output > 0
    m.rejection_pct = _safe_div(m.reject_count, m.total_count)
    m.runner_pct = _safe_div(m.runner_lumps, m.total_count)
    m.attainment = _safe_div(m.total_count, m.planned_output)

    # OEE only from OEE-capable daily rows (finishing lines excluded from a
    # mixed rollup, same as the totals above).
    oee_rows = [r for r in prod_rows if r.has_oee]
    if oee_rows:
        ppt = sum(r.shift_len_min - r.planned_stops_min for r in oee_rows)
        dt = sum(r.downtime_min for r in oee_rows)
        run = max(ppt - dt, 0.0)
        m.ppt = ppt
        m.availability = _safe_div(run, ppt)

        weighted_ideal = 0.0
        oee_total = 0.0
        oee_good = 0.0
        for r in oee_rows:
            row_ppt = r.shift_len_min - r.planned_stops_min
            row_run = max(row_ppt - r.downtime_min, 0.0)
            weighted_ideal += row_run * r.ideal_rate
            oee_total += r.total_count
            oee_good += (r.total_count - r.reject_count)
        ideal_theoretical = weighted_ideal / 60.0
        m.performance_raw = _safe_div(oee_total, ideal_theoretical)
        m.performance = min(m.performance_raw, 1.0)
        m.quality = _safe_div(oee_good, oee_total)
        m.oee = m.availability * m.performance * m.quality
        m.oee_available = True
    else:
        m.oee_available = False

    return m


def rollup(rows: List[Record], group_by: str) -> Dict[str, MetricsResult]:
    groups: Dict[str, List[Record]] = {}
    for r in rows:
        key = getattr(r, group_by, "Unknown")
        groups.setdefault(key, []).append(r)
    return {k: compute_metrics(v) for k, v in groups.items()}


def rollup_by_date(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "date")


def rollup_by_period(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "period")


def rollup_by_plant(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "plant")


def rollup_by_machine(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "machine")


def rollup_by_mould(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "mould")


def rollup_by_segment(rows: List[Record]) -> Dict[str, MetricsResult]:
    return rollup(rows, "segment")


def downtime_pareto(rows: List[Record]) -> List[Dict]:
    """Downtime minutes by reason (daily grain only), sorted desc, cumulative %."""
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
