"""costing_analysis.py — Cross-pillar costing analysis: Labour + Power + RM.

All computations are deterministic Python from DB-stored figures.
Claude is used nowhere in this module — it only receives already-computed
numbers from the route (for narrative prose, not for data computation).

LABOUR denominator  : Plumbing production (pipe_kg + fitting_kg) from
                      costing_labour_monthly.
POWER denominator   : "Ideal Power Cost" tab uses ALL-PLANTS denominator
                      (Plumbing + Garden + HDPE + Tank).  Both denominators
                      are labelled in every output dict.
CONTRACTOR TOGGLE   : contractor_wages come from UNIT-2 col 8 stored in
                      costing_power_monthly.contractor_wages_u2.
IDEAL RATES         : pipe_labour=2.50, fitting_labour=6.50 (from Ideal
                      Labour Cost TOTAL row); pipe_power=4.00, fitting_power=8.00
                      (from Ideal Power Cost TOTAL row).

ACCEPTED FIGURES (FY2026-27 Q1):
  Labour excl contractor = Rs 6.12/kg | incl = Rs 6.43/kg | ideal = Rs 3.67/kg
  Power actual = Rs 8.24/kg (all-plants) | ideal = Rs 4.86/kg
  Combined actual = Rs 14.67/kg | ideal = Rs 8.53/kg | delta = +72%
  Hours paid vs actual: 93,443 vs 82,415 → 11,028 unpaid (11.8%)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]
_MONTH_NUM = {lbl: i + 1 for i, lbl in enumerate(MONTH_LABELS)}

_HOURS_WARNING_THRESHOLD = 0.10   # 10 % paid-not-worked → warn
_LABOUR_OVER_IDEAL_WARN  = 0.20   # 20 % over ideal → warn
_LABOUR_OVER_IDEAL_ALERT = 0.50   # 50 % over ideal → red
_POWER_OVER_IDEAL_WARN   = 0.25   # 25 % over ideal → warn
_POWER_OVER_IDEAL_ALERT  = 0.60   # 60 % over ideal → red
_COMBINED_WARN           = 0.30   # 30 % over ideal → warn

# Volume sensitivity scenarios: production multipliers
_SENSITIVITY_STEPS = [1.0, 1.10, 1.20, 1.30]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _flt(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _pct(actual, ideal) -> Optional[float]:
    if ideal and ideal != 0:
        return round((actual - ideal) / abs(ideal) * 100, 1)
    return None


def _safe_div(num, den, decimals=2) -> Optional[float]:
    if den and den != 0:
        return round(num / den, decimals)
    return None


# ── Monthly stack builder ─────────────────────────────────────────────────────

def build_cost_stack(
    labour_rows: list,
    power_rows: list,
    *,
    incl_contractor: bool = True,
) -> list:
    """Build per-month cost stack dict for months with real production data.

    Each output dict contains:
      Labour actual/ideal/variance (Plumbing denominator).
      Power actual/ideal/variance  (all-plants denominator, from Ideal Power Cost tab).
      Combined = Labour + Power (mixed denominators; labelled).
    """
    power_by_month = {r["month_label"]: r for r in power_rows}
    stack = []

    for lr in labour_rows:
        month = lr["month_label"]
        pr    = power_by_month.get(month, {})

        paid_wages    = _flt(lr.get("paid_wages"))
        pipe_kg       = _flt(lr.get("pipe_prod_kg"))
        fitting_kg    = _flt(lr.get("fitting_prod_kg"))
        total_kg      = pipe_kg + fitting_kg

        # Skip months with no real data; surface as "awaiting" if partial data exists
        if total_kg <= 0 and paid_wages <= 0:
            has_partial = (
                _flt(lr.get("paid_hours")) > 0
                or _flt(lr.get("no_of_labour")) > 0
                or _flt(lr.get("contractor_labour")) > 0
            )
            if has_partial:
                stack.append({
                    "month":      month,
                    "month_num":  _MONTH_NUM.get(month, 0),
                    "awaiting":   True,
                })
            continue

        # Contractor wages: prefer UNIT-2 source
        contractor_wages = _flt(pr.get("contractor_wages_u2")) if pr else 0.0

        total_wages_incl = paid_wages + contractor_wages
        total_wages_excl = paid_wages

        total_wages = total_wages_incl if incl_contractor else total_wages_excl

        # Labour ideal (weighted by pipe/fitting split)
        pipe_rate    = _flt(pr.get("pipe_ideal_labour_rate"), 2.50) if pr else 2.50
        fitting_rate = _flt(pr.get("fitting_ideal_labour_rate"), 6.50) if pr else 6.50
        if not pipe_rate:
            pipe_rate = 2.50
        if not fitting_rate:
            fitting_rate = 6.50

        if total_kg > 0:
            labour_actual_kg = round(total_wages / total_kg, 4)
            labour_excl_kg   = round(total_wages_excl / total_kg, 4)
            labour_ideal_kg  = round((pipe_kg * pipe_rate + fitting_kg * fitting_rate) / total_kg, 4)
        else:
            labour_actual_kg = None
            labour_excl_kg   = None
            labour_ideal_kg  = None

        # Power — from Ideal Power Cost tab (all-plants denominator)
        power_actual_kg = _flt(pr.get("actual_kg_power")) or None if pr else None
        power_ideal_kg  = _flt(pr.get("ideal_kg_power"))  or None if pr else None
        if power_actual_kg == 0.0:
            power_actual_kg = None
        if power_ideal_kg == 0.0:
            power_ideal_kg = None

        # Combined (note: mixed denominators)
        combined_actual = None
        combined_ideal  = None
        if labour_actual_kg is not None and power_actual_kg is not None:
            combined_actual = round(labour_actual_kg + power_actual_kg, 4)
        if labour_ideal_kg is not None and power_ideal_kg is not None:
            combined_ideal = round(labour_ideal_kg + power_ideal_kg, 4)

        entry = {
            "month":              month,
            "month_num":          _MONTH_NUM.get(month, 0),
            "pipe_kg":            pipe_kg,
            "fitting_kg":         fitting_kg,
            "total_kg":           total_kg,
            "paid_wages":         paid_wages,
            "contractor_wages":   contractor_wages,
            "total_wages":        total_wages,
            "labour_actual_kg":   labour_actual_kg,
            "labour_excl_kg":     labour_excl_kg,
            "labour_ideal_kg":    labour_ideal_kg,
            "labour_variance_kg": _safe_div(labour_actual_kg - labour_ideal_kg, 1, 4)
                                  if labour_actual_kg is not None and labour_ideal_kg is not None
                                  else None,
            "labour_variance_pct": _pct(labour_actual_kg, labour_ideal_kg)
                                   if labour_actual_kg is not None and labour_ideal_kg is not None
                                   else None,
            "labour_denom_label": "Plumbing prod (pipe + fitting)",
            # Power
            "power_actual_kg":   power_actual_kg,
            "power_ideal_kg":    power_ideal_kg,
            "power_variance_kg": round(power_actual_kg - power_ideal_kg, 4)
                                 if power_actual_kg is not None and power_ideal_kg is not None
                                 else None,
            "power_variance_pct": _pct(power_actual_kg, power_ideal_kg)
                                  if power_actual_kg is not None and power_ideal_kg is not None
                                  else None,
            "power_denom_label": "All-plant prod (Plumbing+Garden+HDPE+Tank)",
            # Power detail (UNIT-2)
            "jvvl_amount":        _flt(pr.get("jvvl_amount")) or None if pr else None,
            "total_kwh":          _flt(pr.get("total_kwh")) or None if pr else None,
            "kwh_per_kg":         _flt(pr.get("kwh_per_kg")) or None if pr else None,
            "per_unit_cost":      _flt(pr.get("per_unit_cost")) or None if pr else None,
            "solar1_kwh":         _flt(pr.get("solar1_kwh")) or None if pr else None,
            "solar2_kwh":         _flt(pr.get("solar2_kwh")) or None if pr else None,
            "rate_708_rs":        _flt(pr.get("rate_708_rs")) or None if pr else None,
            "rate_1150_rs":       _flt(pr.get("rate_1150_rs")) or None if pr else None,
            "total_power_708":    _flt(pr.get("total_power_708")) or None if pr else None,
            "total_power_1150":   _flt(pr.get("total_power_1150")) or None if pr else None,
            "per_kg_power_708":   _flt(pr.get("per_kg_power_708")) or None if pr else None,
            "per_kg_power_1150":  _flt(pr.get("per_kg_power_1150")) or None if pr else None,
            "ideal_power_total":  _flt(pr.get("ideal_power_total")) or None if pr else None,
            "actual_power_total": _flt(pr.get("actual_power_total")) or None if pr else None,
            # Combined
            "combined_actual_kg":   combined_actual,
            "combined_ideal_kg":    combined_ideal,
            "combined_variance_kg": round(combined_actual - combined_ideal, 4)
                                    if combined_actual is not None and combined_ideal is not None
                                    else None,
            "combined_variance_pct": _pct(combined_actual, combined_ideal)
                                     if combined_actual is not None and combined_ideal is not None
                                     else None,
        }
        stack.append(entry)

    return sorted(stack, key=lambda r: r["month_num"])


def build_fy_total(stack: list, fy_power_total_row: Optional[dict] = None) -> dict:
    """Aggregate stack rows into a single FY total dict."""
    if not stack:
        return {}

    pipe_kg = sum(_flt(r.get("pipe_kg")) for r in stack)
    fitting_kg = sum(_flt(r.get("fitting_kg")) for r in stack)
    total_kg = pipe_kg + fitting_kg
    paid_wages = sum(_flt(r.get("paid_wages")) for r in stack)
    contractor_wages = sum(_flt(r.get("contractor_wages")) for r in stack)
    total_wages = paid_wages + contractor_wages
    total_wages_excl = paid_wages

    # Labour ideal (weighted from aggregated volumes)
    # Rates are per-month consistent; use first-row rates
    first = stack[0]
    pipe_rate = _flt(first.get("pipe_ideal_labour_rate_used"), 2.50) or 2.50
    fitting_rate = _flt(first.get("fitting_ideal_labour_rate_used"), 6.50) or 6.50

    if total_kg > 0:
        labour_actual_kg = round(total_wages / total_kg, 4)
        labour_excl_kg   = round(total_wages_excl / total_kg, 4)
        labour_ideal_kg  = round((pipe_kg * pipe_rate + fitting_kg * fitting_rate) / total_kg, 4)
    else:
        labour_actual_kg = None
        labour_excl_kg   = None
        labour_ideal_kg  = None

    # Power: use fy_power_total_row (from Ideal Power Cost TOTAL row) if available
    # or sum monthly actuals/ideals
    if fy_power_total_row:
        power_actual_kg = _flt(fy_power_total_row.get("actual_kg_power")) or None
        power_ideal_kg  = _flt(fy_power_total_row.get("ideal_kg_power")) or None
        total_ideal_power  = _flt(fy_power_total_row.get("ideal_power_total")) or None
        total_actual_power = _flt(fy_power_total_row.get("actual_power_total")) or None
    else:
        # Fall back to summing monthly (less accurate for all-plants denominator)
        power_actual_kg = None
        power_ideal_kg  = None
        total_ideal_power  = sum(_flt(r.get("ideal_power_total")) for r in stack) or None
        total_actual_power = sum(_flt(r.get("actual_power_total")) for r in stack) or None

    combined_actual = None
    combined_ideal  = None
    if labour_actual_kg is not None and power_actual_kg is not None:
        combined_actual = round(labour_actual_kg + power_actual_kg, 4)
    if labour_ideal_kg is not None and power_ideal_kg is not None:
        combined_ideal = round(labour_ideal_kg + power_ideal_kg, 4)

    jvvl_total = sum(_flt(r.get("jvvl_amount")) for r in stack if r.get("jvvl_amount"))
    kwh_total  = sum(_flt(r.get("total_kwh")) for r in stack if r.get("total_kwh"))
    power708_total = sum(_flt(r.get("total_power_708")) for r in stack if r.get("total_power_708"))
    power1150_total = sum(_flt(r.get("total_power_1150")) for r in stack if r.get("total_power_1150"))

    return {
        "pipe_kg":            round(pipe_kg),
        "fitting_kg":         round(fitting_kg),
        "total_kg":           round(total_kg),
        "paid_wages":         round(paid_wages),
        "contractor_wages":   round(contractor_wages),
        "total_wages":        round(total_wages),
        "total_wages_excl":   round(total_wages_excl),
        "labour_actual_kg":   labour_actual_kg,
        "labour_excl_kg":     labour_excl_kg,
        "labour_ideal_kg":    labour_ideal_kg,
        "labour_variance_kg": round(labour_actual_kg - labour_ideal_kg, 4)
                              if labour_actual_kg is not None and labour_ideal_kg is not None
                              else None,
        "labour_variance_pct": _pct(labour_actual_kg, labour_ideal_kg)
                               if labour_actual_kg is not None and labour_ideal_kg is not None
                               else None,
        "power_actual_kg":    power_actual_kg,
        "power_ideal_kg":     power_ideal_kg,
        "power_variance_kg":  round(power_actual_kg - power_ideal_kg, 4)
                              if power_actual_kg is not None and power_ideal_kg is not None
                              else None,
        "power_variance_pct": _pct(power_actual_kg, power_ideal_kg)
                              if power_actual_kg is not None and power_ideal_kg is not None
                              else None,
        "combined_actual_kg":    combined_actual,
        "combined_ideal_kg":     combined_ideal,
        "combined_variance_kg":  round(combined_actual - combined_ideal, 4)
                                 if combined_actual is not None and combined_ideal is not None
                                 else None,
        "combined_variance_pct": _pct(combined_actual, combined_ideal)
                                 if combined_actual is not None and combined_ideal is not None
                                 else None,
        "jvvl_total":       round(jvvl_total) if jvvl_total else None,
        "kwh_total":        round(kwh_total)  if kwh_total  else None,
        "power708_total":   round(power708_total)  if power708_total  else None,
        "power1150_total":  round(power1150_total) if power1150_total else None,
        "total_ideal_power":  round(total_ideal_power)  if total_ideal_power  else None,
        "total_actual_power": round(total_actual_power) if total_actual_power else None,
        "n_months":          len(stack),
    }


# ── Hours analysis ────────────────────────────────────────────────────────────

def build_hours_analysis(labour_rows: list) -> dict:
    """Paid vs actual hours gap analysis."""
    paid_total   = sum(_flt(r.get("paid_hours")) for r in labour_rows)
    actual_total = sum(_flt(r.get("actual_hours")) for r in labour_rows)
    diff         = paid_total - actual_total
    diff_pct     = round(diff / paid_total * 100, 1) if paid_total > 0 else 0.0

    monthly = []
    for r in labour_rows:
        ph = _flt(r.get("paid_hours"))
        ah = _flt(r.get("actual_hours"))
        if ph > 0 or ah > 0:
            d = ph - ah
            monthly.append({
                "month":          r["month_label"],
                "paid_hours":     ph,
                "actual_hours":   ah,
                "diff":           d,
                "diff_pct":       round(d / ph * 100, 1) if ph > 0 else 0.0,
            })

    return {
        "paid_hours_fy":   round(paid_total),
        "actual_hours_fy": round(actual_total),
        "hours_diff":      round(diff),
        "hours_diff_pct":  diff_pct,
        "warning":         diff_pct > _HOURS_WARNING_THRESHOLD * 100,
        "monthly":         monthly,
    }


# ── YoY / MoM trends ─────────────────────────────────────────────────────────

def build_mom_trends(stack: list) -> list:
    """Month-over-month change in Rs/kg figures within current FY."""
    trends = []
    for i, curr in enumerate(stack):
        prev = stack[i - 1] if i > 0 else None
        entry = {
            "month":              curr["month"],
            "labour_actual_kg":   curr.get("labour_actual_kg"),
            "labour_ideal_kg":    curr.get("labour_ideal_kg"),
            "power_actual_kg":    curr.get("power_actual_kg"),
            "power_ideal_kg":     curr.get("power_ideal_kg"),
            "combined_actual_kg": curr.get("combined_actual_kg"),
            "combined_ideal_kg":  curr.get("combined_ideal_kg"),
        }
        if prev:
            def _delta(key):
                a = curr.get(key)
                b = prev.get(key)
                if a is not None and b is not None and b != 0:
                    return round((a - b) / abs(b) * 100, 1)
                return None
            entry["mom_labour_pct"]   = _delta("labour_actual_kg")
            entry["mom_power_pct"]    = _delta("power_actual_kg")
            entry["mom_combined_pct"] = _delta("combined_actual_kg")
        trends.append(entry)
    return trends


def build_yoy(
    curr_stack: list,
    prev_labour_rows: list,
    prev_power_rows: list,
    *,
    incl_contractor: bool = True,
) -> list:
    """Year-over-year comparison for common months (APR, MAY, JUN, …)."""
    if not curr_stack or not prev_labour_rows:
        return []

    # Build prev-FY stack (same logic as current, using same incl_contractor flag)
    prev_stack = build_cost_stack(prev_labour_rows, prev_power_rows, incl_contractor=incl_contractor)
    prev_by_month = {r["month"]: r for r in prev_stack}

    yoy = []
    for cr in curr_stack:
        month = cr["month"]
        pr    = prev_by_month.get(month)
        if not pr:
            continue

        def _row(label, curr_val, prev_val):
            if curr_val is not None and prev_val is not None and prev_val != 0:
                chg = round((curr_val - prev_val) / abs(prev_val) * 100, 1)
                direction = "up" if curr_val > prev_val else "down"
            else:
                chg = None
                direction = None
            return {
                "month":     month,
                "metric":    label,
                "curr_val":  curr_val,
                "prev_val":  prev_val,
                "change_pct": chg,
                "direction": direction,
            }

        yoy.append(_row("Labour Rs/kg (actual)", cr.get("labour_actual_kg"), pr.get("labour_actual_kg")))
        yoy.append(_row("Labour Rs/kg (ideal)",  cr.get("labour_ideal_kg"),  pr.get("labour_ideal_kg")))
        yoy.append(_row("Power Rs/kg (actual)",  cr.get("power_actual_kg"),  pr.get("power_actual_kg")))
        yoy.append(_row("Combined Rs/kg",        cr.get("combined_actual_kg"), pr.get("combined_actual_kg")))

    return yoy


# ── Cost bridge ───────────────────────────────────────────────────────────────

def build_cost_bridge(
    curr_labour_rows: list,
    curr_power_rows: list,
    prev_labour_rows: list,
    prev_power_rows: list,
    *,
    incl_contractor: bool = True,
) -> dict:
    """Decompose change in labour Rs/kg into RATE + VOLUME + MIX effects.

    Rate effect  = (curr_wages_total - prev_wages_total) / curr_vol
    Volume effect = prev_wages_total × (1/curr_vol - 1/prev_vol)
    Mix effect    = ideal_kg_curr - ideal_kg_prev  (structural, not controllable)
    Residual      = actual Δ − Rate − Volume − Mix  (should be ≈ 0)
    """
    if not curr_labour_rows or not prev_labour_rows:
        return {"available": False}

    def _totals(labour_rows, power_rows, incl_contractor):
        pwr_by_month = {r["month_label"]: r for r in power_rows}
        wages = vol = pipe = fitting = 0.0
        pipe_rate = fitting_rate = None
        for lr in labour_rows:
            pr = pwr_by_month.get(lr["month_label"], {})
            if not pipe_rate:
                pipe_rate    = _flt(pr.get("pipe_ideal_labour_rate"), 2.50) or 2.50
                fitting_rate = _flt(pr.get("fitting_ideal_labour_rate"), 6.50) or 6.50
            pw = _flt(lr.get("paid_wages"))
            cw = _flt(pr.get("contractor_wages_u2")) if incl_contractor else 0.0
            wages   += pw + cw
            pipe    += _flt(lr.get("pipe_prod_kg"))
            fitting += _flt(lr.get("fitting_prod_kg"))
        vol = pipe + fitting
        ideal_kg = round((pipe * (pipe_rate or 2.50) + fitting * (fitting_rate or 6.50)) / vol, 4) if vol > 0 else None
        cost_kg  = round(wages / vol, 4) if vol > 0 else None
        return {"wages": wages, "vol": vol, "pipe": pipe, "fitting": fitting,
                "cost_kg": cost_kg, "ideal_kg": ideal_kg,
                "pipe_rate": pipe_rate, "fitting_rate": fitting_rate}

    curr = _totals(curr_labour_rows, curr_power_rows, incl_contractor)
    prev = _totals(prev_labour_rows, prev_power_rows, incl_contractor)

    if not curr["vol"] or not prev["vol"]:
        return {"available": False, "reason": "Zero production volume in one period."}

    total_change = round(curr["cost_kg"] - prev["cost_kg"], 4)

    # Rate effect: wage change attributed to rate (not volume)
    rate_effect = _safe_div(curr["wages"] - prev["wages"], curr["vol"], 4)

    # Volume effect: same prev wages spread over different volume
    volume_effect = round(prev["wages"] * (1.0 / curr["vol"] - 1.0 / prev["vol"]), 4) if rate_effect is not None else None

    # Mix effect: structural change in ideal Rs/kg due to product mix
    mix_effect = round(curr["ideal_kg"] - prev["ideal_kg"], 4) \
                 if curr["ideal_kg"] is not None and prev["ideal_kg"] is not None \
                 else None

    # Residual
    residual = None
    if rate_effect is not None and volume_effect is not None:
        residual = round(total_change - rate_effect - volume_effect, 4)
        if mix_effect is not None:
            residual = round(residual - mix_effect, 4)

    return {
        "available":      True,
        "total_change":   total_change,
        "curr_cost_kg":   curr["cost_kg"],
        "prev_cost_kg":   prev["cost_kg"],
        "curr_vol":       round(curr["vol"]),
        "prev_vol":       round(prev["vol"]),
        "curr_wages":     round(curr["wages"]),
        "prev_wages":     round(prev["wages"]),
        "rate_effect":    rate_effect,
        "volume_effect":  volume_effect,
        "mix_effect":     mix_effect,
        "residual":       residual,
        "curr_ideal_kg":  curr["ideal_kg"],
        "prev_ideal_kg":  prev["ideal_kg"],
    }


# ── Volume sensitivity ────────────────────────────────────────────────────────

def build_volume_sensitivity(
    stack: list,
    *,
    labour_fixed_pct: float = 0.90,
    power_fixed_pct:  float = 0.35,
) -> dict:
    """What-if Rs/kg at +10/+20/+30% production volume.

    Labour is modelled as 90% fixed (salaried workforce), 10% variable.
    Power fixed portion = JVVL demand charges (rate_708_rs),  variable = JVVL.
    """
    if not stack:
        return {}

    total_wages  = sum(_flt(r.get("total_wages")) for r in stack)
    total_vol    = sum(_flt(r.get("total_kg")) for r in stack)
    power_actual = sum(_flt(r.get("actual_power_total")) for r in stack if r.get("actual_power_total"))
    jvvl_total   = sum(_flt(r.get("jvvl_amount")) for r in stack if r.get("jvvl_amount"))
    demand_charges = sum(_flt(r.get("rate_708_rs")) for r in stack if r.get("rate_708_rs"))

    # Fallback power if actual_power_total not available
    if not power_actual:
        power_actual = sum(_flt(r.get("total_power_708")) for r in stack if r.get("total_power_708"))

    fixed_wages    = total_wages   * labour_fixed_pct
    variable_wages = total_wages   * (1 - labour_fixed_pct)
    fixed_power    = demand_charges if demand_charges else (power_actual * power_fixed_pct)
    variable_power = power_actual - fixed_power

    scenarios = []
    for mult in _SENSITIVITY_STEPS:
        new_vol  = total_vol * mult
        new_wages = fixed_wages + variable_wages * mult
        new_power = fixed_power + variable_power * mult
        scenarios.append({
            "label":        f"+{round((mult-1)*100)}%" if mult > 1 else "Current",
            "mult":          round(mult, 2),
            "volume_kg":     round(new_vol),
            "labour_kg":     _safe_div(new_wages, new_vol, 2),
            "power_kg":      _safe_div(new_power, new_vol, 2),
            "combined_kg":   _safe_div(new_wages + new_power, new_vol, 2),
        })

    return {
        "base_vol_kg":     round(total_vol),
        "base_wages":      round(total_wages),
        "base_power":      round(power_actual),
        "labour_fixed_pct": round(labour_fixed_pct * 100),
        "power_fixed_pct":  round(power_fixed_pct * 100),
        "scenarios":       scenarios,
    }


# ── Warnings engine ───────────────────────────────────────────────────────────

def build_warnings(
    stack: list,
    fy_total: dict,
    hours_analysis: dict,
) -> list:
    """Generate ranked list of warnings/alerts from computed data.

    Returns list of {"severity": "red"|"amber"|"info", "title": ..., "detail": ..., "rec": ...}
    """
    warnings = []

    def _w(sev, title, detail, rec=None):
        warnings.append({"severity": sev, "title": title, "detail": detail, "rec": rec})

    # 1. Combined conversion cost over ideal
    comb_pct = fy_total.get("combined_variance_pct")
    if comb_pct is not None:
        if comb_pct > _COMBINED_WARN * 100:
            sev = "red" if comb_pct > 60 else "amber"
            _w(sev,
               f"Combined conversion cost {comb_pct:+.0f}% above ideal",
               f"Actual Rs {fy_total.get('combined_actual_kg'):.2f}/kg vs ideal "
               f"Rs {fy_total.get('combined_ideal_kg'):.2f}/kg.",
               "Analyse labour rate/volume drivers and negotiate JVVL rate review.")

    # 2. Labour cost over ideal
    lab_pct = fy_total.get("labour_variance_pct")
    if lab_pct is not None:
        if lab_pct > _LABOUR_OVER_IDEAL_WARN * 100:
            sev = "red" if lab_pct > _LABOUR_OVER_IDEAL_ALERT * 100 else "amber"
            _w(sev,
               f"Labour Rs/kg {lab_pct:+.0f}% above ideal",
               f"Actual Rs {fy_total.get('labour_actual_kg'):.2f}/kg vs ideal "
               f"Rs {fy_total.get('labour_ideal_kg'):.2f}/kg.",
               "Review headcount vs production volume; consider contractor rationalisation.")

    # 3. Power cost over ideal
    pwr_pct = fy_total.get("power_variance_pct")
    if pwr_pct is not None:
        if pwr_pct > _POWER_OVER_IDEAL_WARN * 100:
            sev = "red" if pwr_pct > _POWER_OVER_IDEAL_ALERT * 100 else "amber"
            _w(sev,
               f"Power Rs/kg {pwr_pct:+.0f}% above ideal",
               f"Actual Rs {fy_total.get('power_actual_kg'):.2f}/kg vs ideal "
               f"Rs {fy_total.get('power_ideal_kg'):.2f}/kg (all-plant denominator).",
               "Increase solar utilisation and reduce grid draw during peak tariff hours.")

    # 4. Paid hours vs actual hours
    hours_diff_pct = hours_analysis.get("hours_diff_pct", 0.0)
    if hours_diff_pct > _HOURS_WARNING_THRESHOLD * 100:
        sev = "red" if hours_diff_pct > 20 else "amber"
        _w(sev,
           f"Paid hours exceed actual by {hours_diff_pct:.1f}%",
           f"Paid: {hours_analysis.get('paid_hours_fy'):,} h | Actual: "
           f"{hours_analysis.get('actual_hours_fy'):,} h | Gap: "
           f"{hours_analysis.get('hours_diff'):,} h",
           "Audit attendance records; review shift scheduling efficiency.")

    # 5. Per-month power spikes
    for r in stack:
        pwr_kg = r.get("power_actual_kg")
        pwr_ideal_kg = r.get("power_ideal_kg")
        if pwr_kg and pwr_ideal_kg and pwr_ideal_kg > 0:
            m_pct = (pwr_kg - pwr_ideal_kg) / pwr_ideal_kg * 100
            if m_pct > 100:   # > 100% over ideal in a single month
                _w("red",
                   f"{r['month']}: Power spike — {m_pct:+.0f}% above ideal",
                   f"Rs {pwr_kg:.2f}/kg actual vs Rs {pwr_ideal_kg:.2f}/kg ideal "
                   f"(low production volume {r.get('total_kg',0):,.0f} kg in this month).",
                   "Review production calendar — spreading fixed power charges over thin volume.")

    # 6. Ideal rates static (still at launch-day defaults)
    pipe_rate = stack[0].get("pipe_ideal_labour_rate_used") if stack else None
    # Note: these are stored in power rows; default 2.50/6.50
    # Fire warning if rates haven't been updated in > 12 months (proxy: if still default)
    # This is an advisory, not an error
    _w("info",
       "Ideal rates advisory",
       "Pipe labour ideal=Rs 2.50/kg, Fitting=Rs 6.50/kg, Pipe power=Rs 4.00/kg, "
       "Fitting power=Rs 8.00/kg. Verify these reflect current capacity norms.",
       "Review with engineering if capacity/staffing norms have changed since baseline.")

    # Sort by severity
    _order = {"red": 0, "amber": 1, "info": 2}
    warnings.sort(key=lambda w: _order.get(w["severity"], 3))
    return warnings


# ── Main view assembler ───────────────────────────────────────────────────────

def get_analysis_view(
    segment: str,
    fy: str,
    *,
    incl_contractor: bool = True,
    prev_fy: Optional[str] = None,
) -> dict:
    """Assemble the full Costing Analysis view dict.

    Loads data from DB (costing_labour_monthly + costing_power_monthly).
    Returns a dict ready for template rendering.
    """
    import costing_model as cm
    import costing_power as cp

    fy_cfg   = cm.FY_CONFIG.get(fy, {})
    fy_label = fy_cfg.get("label", f"FY{fy}")

    # Load current FY data
    labour_rows = cm.get_labour_monthly(segment, fy)
    power_rows  = cp.get_power_monthly(segment, fy)

    # Ideal rates (from first power row that has them)
    pipe_labour_rate    = 2.50
    fitting_labour_rate = 6.50
    pipe_power_rate     = 4.00
    fitting_power_rate  = 8.00
    for pr in power_rows:
        r = pr.get("pipe_ideal_labour_rate")
        if r:
            pipe_labour_rate = float(r)
        r = pr.get("fitting_ideal_labour_rate")
        if r:
            fitting_labour_rate = float(r)
        r = pr.get("pipe_ideal_power_rate")
        if r:
            pipe_power_rate = float(r)
        r = pr.get("fitting_ideal_power_rate")
        if r:
            fitting_power_rate = float(r)
        break

    # Build stacks
    stack = build_cost_stack(labour_rows, power_rows, incl_contractor=incl_contractor)

    # Separate real-data months from "awaiting source data" placeholders.
    # Computations (FY total, MoM, YoY, warnings) use only active months;
    # the template receives the full stack so awaiting months are visible.
    active_stack = [r for r in stack if not r.get("awaiting")]

    # FY total — use last power row's FY-aggregate ideal figures if available
    # (Ideal Power Cost tab TOTAL row is stored in all monthly rows identically)
    # Build from summed stack; power totals from UNIT-2 rows
    fy_power_agg = {
        "ideal_kg_power":     power_rows[0].get("ideal_kg_power")     if power_rows else None,
        "actual_kg_power":    power_rows[0].get("actual_kg_power")    if power_rows else None,
        "ideal_power_total":  None,
        "actual_power_total": None,
    } if power_rows else None

    # Use the TOTAL from the Ideal Power Cost tab by looking at the sum of all months
    # (the TOTAL row values are not stored per se; we use the sum approach)
    if power_rows and stack:
        # Use all-months ideal/actual power totals from summed monthly rows
        total_ideal_pwr  = sum(_flt(pr.get("ideal_power_total"))  for pr in power_rows if pr.get("ideal_power_total"))
        total_actual_pwr = sum(_flt(pr.get("actual_power_total")) for pr in power_rows if pr.get("actual_power_total"))
        # per-kg from summed total / all-plants vol (stored in power rows as ideal_kg / actual_kg)
        # Use TOTAL row from Ideal Power Cost tab:
        # The monthly per-month ideal_kg_power and actual_kg_power use the per-month all-plants vol.
        # For the FY total, the TOTAL row ideal_kg_power / actual_kg_power is the correct figure.
        # It's stored in every monthly row (same value) — take from first non-null row.
        # However, the "TOTAL row" figures are NOT stored in the monthly table (we store per-month).
        # The FY-level all-plants ideal/actual per-kg must be recomputed from the summed ideal/actual totals
        # and the summed all-plants production (not directly available in DB).
        # As an approximation: use the per-month figures and compute weighted average.
        # Better: the route should call parse_ideal_power_tab and extract the TOTAL row separately.
        # For now, compute weighted average from monthly stacks.
        if fy_power_agg:
            fy_power_agg["ideal_power_total"]  = total_ideal_pwr  or None
            fy_power_agg["actual_power_total"] = total_actual_pwr or None
            # Re-estimate all-plants per-kg from sums (adequate for display)
            all_plants_vol_est = sum(
                _flt(pr.get("ideal_power_total", 0)) / _flt(pr.get("ideal_kg_power", 1))
                for pr in power_rows
                if pr.get("ideal_power_total") and pr.get("ideal_kg_power")
            ) or None
            if all_plants_vol_est and total_ideal_pwr:
                fy_power_agg["ideal_kg_power"]  = round(total_ideal_pwr  / all_plants_vol_est, 2)
            if all_plants_vol_est and total_actual_pwr:
                fy_power_agg["actual_kg_power"] = round(total_actual_pwr / all_plants_vol_est, 2)

    fy_total = build_fy_total(active_stack, fy_power_agg)

    # Hours analysis
    hours = build_hours_analysis(labour_rows)

    # MoM trends
    mom = build_mom_trends(active_stack)

    # Prev FY data for YoY + cost bridge
    prev_fy_label = None
    prev_labour_rows = []
    prev_power_rows  = []
    if not prev_fy:
        # Default to previous FY in order
        fy_order = cm.FY_ORDER
        idx = fy_order.index(fy) + 1 if fy in fy_order else len(fy_order)
        if idx < len(fy_order):
            prev_fy = fy_order[idx]
    if prev_fy:
        prev_fy_label    = cm.FY_CONFIG.get(prev_fy, {}).get("label", f"FY{prev_fy}")
        prev_labour_rows = cm.get_labour_monthly(segment, prev_fy)
        prev_power_rows  = cp.get_power_monthly(segment, prev_fy)

    yoy          = build_yoy(active_stack, prev_labour_rows, prev_power_rows, incl_contractor=incl_contractor)
    cost_bridge  = build_cost_bridge(
        labour_rows, power_rows, prev_labour_rows, prev_power_rows, incl_contractor=incl_contractor
    )
    vol_sensitivity = build_volume_sensitivity(active_stack)
    warnings_list   = build_warnings(active_stack, fy_total, hours)

    return {
        "segment":       segment,
        "fy":            fy,
        "fy_label":      fy_label,
        "prev_fy":       prev_fy,
        "prev_fy_label": prev_fy_label,
        "incl_contractor": incl_contractor,
        "power_loaded":  bool(power_rows),
        "n_data_months": len(active_stack),
        "pipe_labour_rate":    pipe_labour_rate,
        "fitting_labour_rate": fitting_labour_rate,
        "pipe_power_rate":     pipe_power_rate,
        "fitting_power_rate":  fitting_power_rate,
        "stack":         stack,
        "fy_total":      fy_total,
        "hours":         hours,
        "mom":           mom,
        "yoy":           yoy,
        "cost_bridge":   cost_bridge,
        "vol_sensitivity": vol_sensitivity,
        "warnings":      warnings_list,
    }
