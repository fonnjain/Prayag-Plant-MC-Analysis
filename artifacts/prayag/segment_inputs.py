"""Group B — Segment Labour / Solar / Power **manual monthly inputs**.

Everything in the management reports recomputes from daily production data and the
employee-cost sheets EXCEPT the fields below: grid-power amount, electricity and
solar generation, the tariff basic rates, and contractor head-count/wages. These
do NOT exist in any production workbook, so they must be captured by a person each
month (source: JVVL electricity bill, Solar Reading Details, contractor invoices).

This module is **pure** — no network, no DB, no Flask. It defines the capture
schema (``UNITS``/``FIELDS``) and ``build_segment_inputs`` which merges whatever
manual values have been entered with the recomputed per-unit production to produce
a template-ready view. A field that has not been entered stays ``None`` and the UI
renders it as "awaiting input"; nothing here ever fabricates a value. Per-kg power
cost is computed only from the unit's own kg production (never across units of
measure) and only once the grid-power amount has been entered.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


# Three billing units, each spanning a set of production plants. The plant set is
# used only to recompute the production denominator for per-kg cost; the manual
# values themselves are captured per unit.
UNITS: List[dict] = [
    {
        "key": "UNIT-1",
        "label": "UNIT-1 (CP / PTMT / Hardware / Sink)",
        "plants": ["CP", "PTMT"],
    },
    {
        "key": "UNIT-2",
        "label": "UNIT-2 (Plumbing / Pipe + Tank)",
        "plants": ["PIPE", "MOULDING", "TANK"],
    },
    {
        "key": "UNIT-3",
        "label": "UNIT-3 (Garden + HDPE)",
        "plants": ["GARDEN", "HDPE"],
    },
]

UNIT_KEYS: List[str] = [u["key"] for u in UNITS]
UNIT_LABELS: Dict[str, str] = {u["key"]: u["label"] for u in UNITS}
UNIT_PLANTS: Dict[str, List[str]] = {u["key"]: list(u["plants"]) for u in UNITS}


# Every manual field. ``units`` restricts a field to specific billing units (e.g. a
# second solar source only exists for Unit-2). ``money`` fields are ₹ amounts.
FIELDS: List[dict] = [
    {"key": "jvvl_power", "label": "JVVL Power Amount", "unit": "₹",
     "units": UNIT_KEYS, "money": True,
     "help": "Grid electricity bill amount for the month (JVVL bill)."},
    {"key": "elec_gen", "label": "Electricity Generation", "unit": "kWh",
     "units": UNIT_KEYS, "money": False,
     "help": "Grid units consumed (kWh)."},
    {"key": "solar_gen", "label": "Solar Generation", "unit": "kWh",
     "units": UNIT_KEYS, "money": False,
     "help": "Solar units generated / consumed (kWh)."},
    {"key": "solar_gen2", "label": "2nd Solar Generation", "unit": "kWh",
     "units": ["UNIT-2"], "money": False,
     "help": "Second solar source — Unit-2 only."},
    {"key": "rate_708", "label": "Basic Rate (7.08)", "unit": "₹/unit",
     "units": UNIT_KEYS, "money": False,
     "help": "Tariff basic rate; changes rarely — confirm each FY."},
    {"key": "rate_115", "label": "Basic Rate (11.5)", "unit": "₹/unit",
     "units": ["UNIT-2", "UNIT-3"], "money": False,
     "help": "Second tariff band used in Unit-2/3."},
    {"key": "contractor_count", "label": "Contractor Labour", "unit": "persons",
     "units": UNIT_KEYS, "money": False,
     "help": "Contractors are NOT in the payroll CTC sheet — enter the head-count."},
    {"key": "contractor_wages", "label": "Contractor Wages", "unit": "₹",
     "units": UNIT_KEYS, "money": True,
     "help": "Contractor wages paid for the month."},
]

FIELD_KEYS: List[str] = [f["key"] for f in FIELDS]
FIELD_BY_KEY: Dict[str, dict] = {f["key"]: f for f in FIELDS}


# A per-kg power cost month-over-month change at or beyond this magnitude (%) is
# surfaced as a non-blocking advisory ("jumped 34% vs last month"). It is a soft
# attention cue, never a gate.
SPIKE_THRESHOLD_PCT: float = 15.0

# A month-over-month DROP in the solar share (solar / (grid + solar)), measured in
# percentage points, at or beyond this magnitude is surfaced as a non-blocking
# advisory ("solar share fell from 42% to 25%"). A drop in solar share means the
# grid-vs-solar mix swung toward grid power — a real operational signal. Only a
# drop is flagged (a rising solar share needs no warning); soft cue, never a gate.
SOLAR_SHARE_DROP_PTS: float = 10.0

# A month-over-month JUMP in contractor cost-per-head (contractor_wages /
# contractor_count) at or beyond this magnitude (%) is surfaced as a non-blocking
# advisory ("contractor cost per head jumped 34% vs last month"). Only an increase
# is flagged (a falling cost-per-head needs no warning — it is good news), mirroring
# the one-directional solar-share alert. Soft attention cue, never a gate.
CONTRACTOR_JUMP_PCT: float = 25.0


def fields_for_unit(unit_key: str) -> List[dict]:
    """The manual fields applicable to one billing unit (some are unit-specific)."""
    return [f for f in FIELDS if unit_key in f["units"]]


def _num(v) -> Optional[float]:
    """Coerce a stored/entered value to float; blank/invalid → ``None`` (awaiting)."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def build_segment_inputs(
    months: List[str],
    inputs_by_key: Dict[Tuple[str, str], dict],
    prod_by_unit: Optional[Dict[Tuple[str, str], Dict[str, float]]] = None,
) -> dict:
    """Assemble the per-(month, unit) manual-input view.

    Args:
      months: ISO ``YYYY-MM`` months to show, oldest→newest.
      inputs_by_key: ``{(month, unit_key): {field_key: value, "set_by":, "when_disp":, "note":}}``
        — whatever has been captured (typically from the append-only store).
      prod_by_unit: optional ``{(month, unit_key): {uom: qty}}`` recomputed
        production used only for per-kg power cost (kg bucket only).

    Returns a template-ready dict. A field with no entered value is ``None`` so the
    UI shows "awaiting input"; per-kg power cost is ``None`` until BOTH the grid
    power amount and a kg production figure exist. Nothing is fabricated.
    """
    prod_by_unit = prod_by_unit or {}
    rows: List[dict] = []
    n_awaiting = 0
    n_fields_total = 0
    # Grouped per unit for templates: each unit carries its applicable field list
    # (some fields are unit-specific) plus its month rows.
    by_unit: List[dict] = []

    for unit in UNITS:
        ukey = unit["key"]
        applicable = fields_for_unit(ukey)
        unit_rows: List[dict] = []
        for month in months:
            raw = inputs_by_key.get((month, ukey), {}) or {}
            cells: Dict[str, dict] = {}
            entered_any = False
            for f in applicable:
                val = _num(raw.get(f["key"]))
                n_fields_total += 1
                if val is None:
                    n_awaiting += 1
                else:
                    entered_any = True
                cells[f["key"]] = {
                    "value": val,
                    "awaiting": val is None,
                    "label": f["label"],
                    "unit": f["unit"],
                    "money": f["money"],
                }

            prod = prod_by_unit.get((month, ukey), {}) or {}
            kg = float(prod.get("kg", 0.0) or 0.0)
            power = cells.get("jvvl_power", {}).get("value")
            per_kg_power: Optional[float] = None
            if power is not None and kg > 0:
                per_kg_power = power / kg

            # Solar share = solar / (grid + solar). Computed ONLY when BOTH the
            # grid (elec_gen) and solar (solar_gen) figures exist for the month —
            # never fabricated for an awaiting month, which is simply absent from
            # the series. A second solar source (Unit-2) is intentionally NOT mixed
            # in here: the share tracks the primary grid-vs-solar split.
            elec = cells.get("elec_gen", {}).get("value")
            solar = cells.get("solar_gen", {}).get("value")
            solar_share: Optional[float] = None
            if elec is not None and solar is not None and (elec + solar) > 0:
                solar_share = solar / (elec + solar)

            # Contractor cost-per-head = contractor_wages / contractor_count.
            # Computed ONLY when BOTH the head-count and wages exist for the month
            # (and the head-count is > 0) — never fabricated for an awaiting month,
            # which is simply absent from the series.
            ccount = cells.get("contractor_count", {}).get("value")
            cwages = cells.get("contractor_wages", {}).get("value")
            contractor_cph: Optional[float] = None
            if ccount is not None and cwages is not None and ccount > 0:
                contractor_cph = cwages / ccount

            row = {
                "month": month,
                "unit": ukey,
                "unit_label": unit["label"],
                "cells": cells,
                "set_by": (raw.get("set_by") or "").strip(),
                "when_disp": (raw.get("when_disp") or "").strip(),
                "note": (raw.get("note") or "").strip(),
                "entered_any": entered_any,
                "kg_production": kg,
                "per_kg_power": per_kg_power,
                "solar_share": solar_share,
                "contractor_cph": contractor_cph,
            }
            rows.append(row)
            unit_rows.append(row)

        # Per-kg power cost trend: only months where BOTH grid power and kg
        # production exist (per_kg_power computed). Never fabricated for awaiting
        # months — those are simply absent from the series.
        trend = [
            {"month": r["month"], "value": r["per_kg_power"]}
            for r in unit_rows
            if r["per_kg_power"] is not None
        ]

        # Month-over-month spike alert on per-kg power cost. Only CONSECUTIVE
        # months that BOTH carry a per_kg_power value form a valid comparison —
        # an awaiting month between them breaks the chain and is never bridged
        # (unit_rows are one-per-month in the requested order, so adjacency here
        # IS calendar adjacency). The advisory reflects the LATEST such pair, so
        # the loop overwrites and keeps the most recent valid comparison. With
        # fewer than two consecutive valued months it stays None (nothing shown).
        spike: Optional[dict] = None
        for i in range(1, len(unit_rows)):
            prev_v = unit_rows[i - 1]["per_kg_power"]
            curr_v = unit_rows[i]["per_kg_power"]
            if prev_v is None or curr_v is None or prev_v == 0:
                continue
            pct = (curr_v - prev_v) / prev_v * 100.0
            spike = {
                "month": unit_rows[i]["month"],
                "prev_month": unit_rows[i - 1]["month"],
                "value": curr_v,
                "prev_value": prev_v,
                "pct": pct,
                "direction": "up" if pct >= 0 else "down",
                "threshold": SPIKE_THRESHOLD_PCT,
                "exceeds": abs(pct) >= SPIKE_THRESHOLD_PCT,
            }

        # Solar-share trend: only months where BOTH grid and solar exist (share
        # computed). Awaiting months are absent (never fabricated).
        solar_trend = [
            {"month": r["month"], "value": r["solar_share"]}
            for r in unit_rows
            if r["solar_share"] is not None
        ]

        # Month-over-month solar-share drop alert. Mirrors the spike pattern: only
        # CONSECUTIVE months that BOTH carry a solar_share form a valid comparison
        # — an awaiting month between them breaks the chain and is never bridged.
        # The advisory reflects the LATEST such pair (loop overwrites). Only a DROP
        # is meaningful here, so ``exceeds`` is True solely when the share fell by
        # at least the threshold (a rising share gives a negative drop, not shown).
        # With fewer than two consecutive valued months it stays None.
        solar_alert: Optional[dict] = None
        for i in range(1, len(unit_rows)):
            prev_s = unit_rows[i - 1]["solar_share"]
            curr_s = unit_rows[i]["solar_share"]
            if prev_s is None or curr_s is None:
                continue
            drop_pts = (prev_s - curr_s) * 100.0
            solar_alert = {
                "month": unit_rows[i]["month"],
                "prev_month": unit_rows[i - 1]["month"],
                "share": curr_s,
                "prev_share": prev_s,
                "drop_pts": drop_pts,
                "threshold": SOLAR_SHARE_DROP_PTS,
                "exceeds": drop_pts >= SOLAR_SHARE_DROP_PTS,
            }

        # Contractor cost-per-head trend: only months where BOTH the head-count and
        # wages exist (cph computed). Awaiting months are absent (never fabricated).
        contractor_trend = [
            {"month": r["month"], "value": r["contractor_cph"]}
            for r in unit_rows
            if r["contractor_cph"] is not None
        ]

        # Month-over-month contractor cost-per-head JUMP alert. Mirrors the spike
        # pattern: only CONSECUTIVE months that BOTH carry a contractor_cph form a
        # valid comparison — an awaiting month between them breaks the chain and is
        # never bridged. The advisory reflects the LATEST such pair (loop
        # overwrites). Only a JUMP (increase) is meaningful here, so ``exceeds`` is
        # True solely when the cost-per-head rose by at least the threshold (a
        # falling cost gives a negative pct, not shown). With fewer than two
        # consecutive valued months it stays None.
        contractor_alert: Optional[dict] = None
        for i in range(1, len(unit_rows)):
            prev_c = unit_rows[i - 1]["contractor_cph"]
            curr_c = unit_rows[i]["contractor_cph"]
            if prev_c is None or curr_c is None or prev_c == 0:
                continue
            pct = (curr_c - prev_c) / prev_c * 100.0
            contractor_alert = {
                "month": unit_rows[i]["month"],
                "prev_month": unit_rows[i - 1]["month"],
                "value": curr_c,
                "prev_value": prev_c,
                "pct": pct,
                "threshold": CONTRACTOR_JUMP_PCT,
                "exceeds": pct >= CONTRACTOR_JUMP_PCT,
            }

        by_unit.append({
            "key": ukey,
            "label": unit["label"],
            "fields": applicable,
            "rows": unit_rows,
            "trend": trend,
            "spike": spike,
            "solar_trend": solar_trend,
            "solar_alert": solar_alert,
            "contractor_trend": contractor_trend,
            "contractor_alert": contractor_alert,
        })

    return {
        "months": list(months),
        "units": UNITS,
        "fields": FIELDS,
        "rows": rows,
        "by_unit": by_unit,
        "n_awaiting": n_awaiting,
        "n_fields_total": n_fields_total,
        "complete": n_awaiting == 0 and n_fields_total > 0,
    }
