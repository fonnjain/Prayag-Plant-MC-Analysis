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

import ideal_hours


OUTPUT_BASIS_NET = "net"
OUTPUT_BASIS_GROSS = "gross"
OUTPUT_BASIS_UNKNOWN = "unknown"

# Every listed plant's live source contract has been verified against its parser
# and management report.  Unknown plants remain unknown rather than silently
# receiving a net/gross transform.
_OUTPUT_BASIS_BY_PLANT = {
    "GARDEN": OUTPUT_BASIS_NET,
    "GARDEN_WB": OUTPUT_BASIS_NET,
    "HDPE": OUTPUT_BASIS_NET,
    "MOULDING": OUTPUT_BASIS_NET,
    "PIPE": OUTPUT_BASIS_NET,
    "PTMT": OUTPUT_BASIS_GROSS,
    "TANK": OUTPUT_BASIS_NET,
    "TANK_VN": OUTPUT_BASIS_NET,
    "TANK_WB": OUTPUT_BASIS_NET,
}


def _plant_output_basis(plant: str) -> str:
    """Return the documented output basis without guessing for unknown plants."""
    return _OUTPUT_BASIS_BY_PLANT.get(str(plant or "").strip().upper(), OUTPUT_BASIS_UNKNOWN)


def output_basis(record) -> str:
    """Return a Record's output basis, safely supporting pre-field L2 pickles."""
    basis = str(getattr(record, "output_basis", "") or "").strip().lower()
    if basis in (OUTPUT_BASIS_NET, OUTPUT_BASIS_GROSS):
        return basis
    return _plant_output_basis(getattr(record, "plant", ""))


def _can_combine_rejection(record) -> bool:
    """True only when production and rejection are in the same known unit."""
    output_unit = str(getattr(record, "unit", "") or "").strip().lower()
    reject_unit = str(getattr(record, "reject_unit", "") or "").strip().lower()
    return not output_unit or not reject_unit or output_unit == reject_unit


def net_output(record) -> float:
    """Return good/net output without subtracting a rejection in another unit."""
    total = float(getattr(record, "total_count", 0.0) or 0.0)
    if output_basis(record) == OUTPUT_BASIS_GROSS and _can_combine_rejection(record):
        return max(0.0, total - float(getattr(record, "reject_count", 0.0) or 0.0))
    return total


def gross_output(record) -> float:
    """Return gross output without combining a rejection in another unit."""
    total = float(getattr(record, "total_count", 0.0) or 0.0)
    if output_basis(record) == OUTPUT_BASIS_NET and _can_combine_rejection(record):
        return total + float(getattr(record, "reject_count", 0.0) or 0.0)
    return total


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
    unit: str = "kg"             # the figure's OWN unit: kg / pcs / Ltr. Read per
                                  # plant from its sheet header; never assumed global.
    secondary_counts: dict = field(default_factory=dict)
    # Alternative-unit measures of the SAME production (e.g. TANK records litres as
    # the primary unit but the sheet also gives the run in pcs and kg → {"pcs":…,
    # "kg":…}). Display-only: never summed into a rollup total and never used in a
    # ratio, so cross-unit contamination is impossible.

    # --- production ---
    total_count: float = 0.0      # actual output (kg / pcs / ltr)
    reject_count: float = 0.0
    reject_unit: str = ""          # if set, reject_count is in this unit (not ``unit``)
    reject_denominator: float = 0.0  # denominator for rejection_pct when reject_unit ≠ unit
    # Contract for ``total_count``: net/good output or gross output before
    # rejection.  Set at Record construction from the plant contract unless an
    # ingestion source supplies it explicitly.  ``output_basis()`` below still
    # handles old L2-cached Records that predate this field.
    output_basis: str = ""
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
    runhours_tracked: bool = True   # False for output-only plants (GARDEN/TANK):
                                    # an ideal denominator may exist, but run hours
                                    # are not recorded, so utilisation stays
                                    # suppressed (never a fake 0%) until they are.
    ideal_month_hours: float = 0.0  # Report-5 col M: full-month ideal machine hours
                                    # (500 for pipe/moulding, 300 for grinders).
                                    # Spread across daily rows (÷ nrows) so a
                                    # monthly rollup reconstructs the exact col-M
                                    # value. Denominator for M/C Efficiency only —
                                    # NEVER used for utilisation (which is run-day
                                    # based: col F ÷ (col D × col E)).

    # --- location / geography ---
    location: str = ""            # KH | Bhiwari | VN | WB (empty = legacy / unknown)

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

    # --- tonnage band (Group-of-Moulding) ---
    tonnage_band: str = ""        # "150" | "200" | "250" | "275" | "350" | "450"

    # --- rejection tracking ---
    rejection_tracked: bool = True  # False when the source tab has no rejection column
                                    # (e.g. GARDEN MACHINE n block tabs). Causes
                                    # rejection_pct to be suppressed rather than shown
                                    # as a false 0% ("not captured" ≠ "no rejection").

    def __post_init__(self):
        if str(self.output_basis or "").strip().lower() not in (
            OUTPUT_BASIS_NET, OUTPUT_BASIS_GROSS,
        ):
            self.output_basis = _plant_output_basis(self.plant)


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
    mc_efficiency: float = 0.0    # actual run hours ÷ ideal machine hours (col M).
                                   # NOT capped at 1.0 — >100% is valid and shown.

    oee_available: bool = False
    # Whether a real ideal/planned baseline backs the ratio. False for plants
    # with no monthly grid, no in-sheet ideal column and no config baseline
    # (e.g. TANK, Moulding daily): we still report run hours + output, but the
    # ratio is suppressed in the UI rather than shown as a misleading 0%.
    util_available: bool = False
    eff_available: bool = False
    mc_eff_available: bool = False  # True when at least one row has ideal_month_hours > 0

    # Sum of ideal_month_hours across all contributing rows; used by generators
    # to compute a correct TOTAL row denominator (e.g. 9 × 500 = 4 500 for PIPE)
    # without relying on the TOTAL row stored in the sheet (which mis-counts).
    mc_eff_hours_ideal: float = 0.0

    # True when a planned-hours baseline EXISTS for this rollup (sheet / derived /
    # config / app-default / manager override) even when the utilisation
    # denominator is currently gated to 0 for want of reported run hours. Lets the
    # UI say "run hours not recorded" instead of the misleading "no baseline set".
    baseline_set: bool = False

    # False when NONE of the contributing records have a rejection column in their
    # source tab (see Record.rejection_tracked). Suppresses rejection_pct in the
    # UI so a missing numerator column never reads as "no rejection occurred".
    rejection_available: bool = True

    # Costs
    labour_cost: float = 0.0
    power_cost: float = 0.0
    solar_cost: float = 0.0

    row_count: int = 0
    warnings: List[str] = field(default_factory=list)

    # ---- units ----
    # ``unit`` is the SOLE unit of the contributing rows (e.g. "kg" for a single
    # kg plant, "Ltr" for TANK) or "" when the rollup mixes units (the cross-plant
    # overall). ``output_by_unit`` always carries the per-unit output split so a
    # mixed rollup is shown per unit, never as a meaningless single number — output
    # is NEVER summed across units. ``secondary_counts`` are alt-unit views of the
    # same production (TANK pcs/kg), display-only.
    unit: str = ""
    reject_unit: str = ""  # unit of reject_count ("kg" for Tank, same as unit otherwise)
    output_by_unit: Dict[str, float] = field(default_factory=dict)
    reject_by_unit: Dict[str, float] = field(default_factory=dict)
    secondary_counts: Dict[str, float] = field(default_factory=dict)

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
    def mc_efficiency_pct(self) -> float:
        return round(self.mc_efficiency * 100, 1)

    @property
    def mc_eff_rating(self) -> str:
        """Purple when >100% (exceeded ideal — valid and expected for some machines);
        otherwise the standard green/amber/red band."""
        if self.mc_efficiency > 1.0:
            return "purple"
        return _rate_band(self.mc_efficiency)

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
        if self.baseline_set:
            return "Run hours not recorded"
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
            "unit": self.unit,
            "reject_unit": self.reject_unit,
            "output_by_unit": {k: round(v, 2) for k, v in self.output_by_unit.items()},
            "reject_by_unit": {k: round(v, 2) for k, v in self.reject_by_unit.items()},
            "rejection_pct_by_unit": {
                k: round(_safe_div(self.reject_by_unit.get(k, 0.0), v) * 100, 1)
                for k, v in self.output_by_unit.items()
            },
            "is_mixed_unit": len(self.output_by_unit) > 1,
            "secondary_counts": {k: round(v, 2) for k, v in self.secondary_counts.items()},
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
            "mc_efficiency": self.mc_efficiency_pct,
            "mc_eff_available": self.mc_eff_available,
            "mc_eff_rating": self.mc_eff_rating,
            "baseline_set": self.baseline_set,
            "headline_available": self.headline_available,
            "rejection_pct": self.rejection_pct_display if self.rejection_available else None,
            "rejection_available": self.rejection_available,
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
    _reject_denom_sum: float = 0.0  # summed reject_denominator (non-zero for Tank)
    util_ideal = 0.0   # utilisation denominator: only hours from rows that ACTUALLY
                       # track run time (excludes output-only plants), so an
                       # app-default denominator on GARDEN/TANK neither fabricates a
                       # 0% nor dilutes a mixed rollup's utilisation.
    eff_out = 0.0
    mc_eff_run = 0.0   # M/C Efficiency numerator: run hours for rows with an
    mc_eff_den = 0.0   # ideal_month_hours > 0 (col M present in Report-5).
    for r in prod_rows:
        m.total_count += r.total_count
        m.reject_count += r.reject_count
        _reject_denom_sum += getattr(r, "reject_denominator", 0.0)
        m.runner_lumps += r.runner_lumps
        m.planned_output += r.planned_output
        m.labour_cost += r.labour_cost
        m.power_cost += r.power_cost
        m.solar_cost += r.solar_cost

        # Output is bucketed by the row's OWN unit so a mixed rollup never collapses
        # into a meaningless single number. total_count above keeps the raw sum for
        # ratio backward-compatibility, but the UI reads output_by_unit / unit.
        # ``getattr`` guards against a stale Record deserialized from the Postgres
        # L2 cache that predates the secondary_counts field (an old pickle lacks the
        # dataclass default, so a bare attribute access would raise on deploy).
        _u = (getattr(r, "unit", "") or "").strip()
        if _u:
            m.output_by_unit[_u] = m.output_by_unit.get(_u, 0.0) + r.total_count
            # Key rejection by its OWN unit (reject_unit when set, else production unit).
            # This prevents Tank kg rejection from landing under the "Ltr" key.
            _rj_key = getattr(r, "reject_unit", "") or _u
            m.reject_by_unit[_rj_key] = m.reject_by_unit.get(_rj_key, 0.0) + r.reject_count
        for _su, _sv in (getattr(r, "secondary_counts", None) or {}).items():
            m.secondary_counts[_su] = m.secondary_counts.get(_su, 0.0) + _sv

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
            util_ideal += r.shift_len_min / 60.0
            if r.ideal_rate > 0:
                eff_out += r.total_count
        else:
            # Monthly-grain rows AND daily-matrix rows (per-date production
            # grids) both carry hours/output directly — no shift timing to model.
            m.actual_hours += r.actual_hours
            m.ideal_hours += r.ideal_hours
            m.ideal_output += r.ideal_output
            if r.ideal_hours > 0 and r.runhours_tracked:
                util_run += r.actual_hours
                util_ideal += r.ideal_hours
            if r.ideal_output > 0:
                eff_out += r.total_count
            # M/C Efficiency: accumulate from rows that have a col-M denominator.
            # getattr guard: old pickles from L2 cache lack ideal_month_hours.
            _imh = getattr(r, "ideal_month_hours", 0.0)
            if _imh > 0:
                mc_eff_run += r.actual_hours
                mc_eff_den += _imh

    m.good_count = sum(net_output(r) for r in prod_rows)
    m.run_time = m.actual_hours * 60.0
    m.shift_len_min = m.ideal_hours * 60.0

    # Sole unit when every contributing row shares one (a single plant/machine);
    # "" when the rollup mixes units (the cross-plant overall) so the UI shows the
    # per-unit split instead of one number.
    _units = {u for u in m.output_by_unit}
    m.unit = next(iter(_units)) if len(_units) == 1 else ""

    m.utilisation = _safe_div(util_run, util_ideal)
    m.output_efficiency = _safe_div(eff_out, m.ideal_output)
    m.util_available = util_ideal > 0
    m.eff_available = m.ideal_output > 0
    # M/C Efficiency: NOT capped at 1.0 (>100% is valid — e.g. a grinder
    # running overtime). The denominator comes from col M per machine row,
    # summed from the per-row spread so the monthly total reconstructs exactly.
    m.mc_efficiency = _safe_div(mc_eff_run, mc_eff_den)
    m.mc_eff_available = mc_eff_den > 0
    m.mc_eff_hours_ideal = mc_eff_den
    # A planned-hours baseline EXISTS (independent of run-hour gating) when any row
    # carries a resolved ideal source, OR its plant has an app-default baseline.
    m.baseline_set = any(
        (getattr(r, "ideal_source", "") not in ("none", ""))
        or (getattr(r, "plant", "") in ideal_hours.APP_DEFAULT_IDEAL_HOURS)
        for r in prod_rows
    )
    # Rejection %: Tank records carry reject_denominator = prod_ltr (same unit as
    # reject_count after Phase-1 fix).  For all other plants reject_denominator is 0
    # and total_count serves as the denominator.
    _rej_denom = _reject_denom_sum if _reject_denom_sum > 0 else m.total_count
    m.rejection_pct = _safe_div(m.reject_count, _rej_denom)
    # Rejection is available only when at least one contributing record actually
    # tracked a rejection column. Old pickles from L2 cache lack the field; default
    # True (conservative — don't suppress data we can't confirm is absent).
    m.rejection_available = any(
        getattr(r, "rejection_tracked", True) for r in prod_rows
    )
    # Derive a single reject_unit for the rollup (set when all rows agree on one unit).
    _rej_units = [u for u, v in m.reject_by_unit.items() if v > 0]
    m.reject_unit = _rej_units[0] if len(_rej_units) == 1 else ""
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
            oee_total += gross_output(r)
            oee_good += net_output(r)
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


def rollup_by_tonnage_band(rows: List[Record]) -> Dict[str, MetricsResult]:
    """Group-of-Moulding: aggregate by tonnage band (150/200/250/275/350/450)."""
    groups: Dict[str, List[Record]] = {}
    for r in rows:
        key = r.tonnage_band or "Other"
        groups.setdefault(key, []).append(r)
    BAND_ORDER = ["150", "200", "250", "275", "350", "450"]
    sorted_keys = sorted(groups.keys(),
        key=lambda k: BAND_ORDER.index(k) if k in BAND_ORDER else 999)
    return {k: compute_metrics(groups[k]) for k in sorted_keys}


def rollup_by_location(rows: List[Record]) -> Dict[str, MetricsResult]:
    """Aggregate by location (KH / Bhiwari / VN / WB)."""
    return rollup(rows, "location")


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
