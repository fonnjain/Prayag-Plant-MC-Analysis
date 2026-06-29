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

        by_unit.append({
            "key": ukey,
            "label": unit["label"],
            "fields": applicable,
            "rows": unit_rows,
            "trend": trend,
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
