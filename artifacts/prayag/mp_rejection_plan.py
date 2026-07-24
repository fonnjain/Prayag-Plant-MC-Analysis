"""mp_rejection_plan.py — Rejection-aware piece gross-up for machine planning.

Reads per-item / per-material / overall rejection rates from the Settings
rejection stats (mp_rejection_item / mp_rejection_summary, populated by
mp_rejection.recompute_rejection) and applies the gross-up formula so that
net good output meets demand after quality rejection.

REJECTION BASIS — GROSS (Prayag's convention)
----------------------------------------------
All rates stored and applied here use the GROSS basis, matching Prayag's own
"REJECTION & PRODUCTION" tab:

    rejection_rate_gross = rej_kg / (prod_kg + rej_kg)

The gross-up formula then pairs correctly:

    gross_qty = net_demand / (1 − rejection_rate_gross)

This is equivalent to:  gross = net_demand × (1 + rej/prod)   [net rate]
Both identities give identical gross quantities.  Using the NET rate in the
gross formula (net/(1−r_net)) over-states by ~(r_net − r_gross)/(1−r_net) per
unit — about 1–2% at typical 9–11% NET rates.

The stored constant REJ_BASIS = "gross" must be saved alongside any frozen plan
run so that run remains reproducible if the formula is ever changed.

FORMULA SEQUENCE
----------------
    gross_qty   = net_demand / (1 − rejection_rate_gross)   [quality: more pieces]
    material_kg = gross_qty × wt_per_pc × (1 + waste_pct)  [process: more material]

Never conflate the two — rejection is a quality/piece loss, waste is a
process/material loss.

FALLBACK LADDER (get_item_rate)
---------------------------------
1. Item-level   — stored only for FITTING items with prod_kg ≥ MIN_PROD_KG
2. Material-level — "{PLANT_TYPE}:{MATERIAL}" key (e.g., "PIPE:CPVC")
3. Overall type   — "{PLANT_TYPE}" aggregate across all materials
4. 0% / basis="none" — no data at all (gross_qty == net_demand)
"""
from __future__ import annotations

import logging
from typing import Tuple

import store

logger = logging.getLogger(__name__)

REJ_CAP     = 0.50    # safety cap: no rate above 50% (implausible data guard)
MIN_PROD_KG = 500.0   # min cumulative production (kg) for item-level rate trust

# Basis label saved on every plan run so frozen runs remain reproducible.
REJ_BASIS = "gross"   # rej_kg / (prod_kg + rej_kg) — Prayag's convention


# ── Core formula ──────────────────────────────────────────────────────────────

def gross_qty(net_demand: float, rate: float, cap: float = REJ_CAP) -> float:
    """Return gross quantity so net good output = net_demand after rejection at rate.

    ``rate`` MUST be a GROSS-basis rate: rej_kg / (prod_kg + rej_kg).
    gross = net / (1 − rate).  rate is capped at *cap* before applying.
    If rate ≤ 0 returns net_demand unchanged.

    Do NOT pass a NET-basis rate (rej/prod) — that pairs with net*(1+r), not
    net/(1−r), and over-states gross quantity by ~1–2% at typical rates.
    """
    r = min(max(rate, 0.0), cap)
    if r <= 0.0:
        return net_demand
    return net_demand / (1.0 - r)


# ── Lookup builder (called once at plan run time) ─────────────────────────────

def build_rejection_lookup(segment: str) -> dict:
    """Query rejection DB and return a structured lookup dict.

    Returns::

        {
            "items":    {norm_item_key: {"rate": float, "capped": bool}},
            "material": {"PIPE:CPVC": float, "FITTING:SWR": float, ...},
            "overall":  {"PIPE": float, "FITTING": float},
            "has_data": bool,
        }

    All sub-dicts are empty when the DB is unavailable or contains no data.
    ``has_data`` is True only when at least one material-level or overall rate
    is present (item-level rates alone are insufficient for pipe demand items
    which are never in mp_rejection_item).
    """
    result: dict = {"items": {}, "material": {}, "overall": {}, "has_data": False}
    if not store.AVAILABLE:
        return result
    try:
        with store._conn() as conn, conn.cursor() as cur:
            # Per-item rates (fitting items only in practice)
            cur.execute(
                """SELECT plant_type, item_key, prod_kg, rej_kg
                   FROM mp_rejection_item
                   WHERE segment = %s AND prod_kg >= %s""",
                (segment, MIN_PROD_KG),
            )
            for plant_type, item_key, prod_kg, rej_kg in cur.fetchall():
                prod = float(prod_kg or 0)
                rej  = float(rej_kg  or 0)
                if prod <= 0:
                    continue
                # GROSS basis: rej / (prod + rej) — matches Prayag's own reporting
                total = prod + rej
                raw = rej / total if total > 0 else 0.0
                result["items"][item_key] = {
                    "rate":   min(raw, REJ_CAP),
                    "capped": raw > REJ_CAP,
                }

            # Material-level summary (PIPE and FITTING, per material)
            cur.execute(
                """SELECT plant_type, material, prod_kg, rej_kg
                   FROM mp_rejection_summary
                   WHERE segment = %s AND material != '' AND prod_kg > 0
                   ORDER BY plant_type, material""",
                (segment,),
            )
            type_acc: dict = {}
            for plant_type, material, prod_kg, rej_kg in cur.fetchall():
                prod = float(prod_kg or 0)
                rej  = float(rej_kg  or 0)
                if prod <= 0:
                    continue
                mat_key = f"{plant_type.upper()}:{material.upper()}"
                # GROSS basis: rej / (prod + rej)
                mat_total = prod + rej
                result["material"][mat_key] = min(rej / mat_total if mat_total > 0 else 0.0, REJ_CAP)
                acc = type_acc.setdefault(plant_type.upper(), {"prod": 0.0, "rej": 0.0})
                acc["prod"] += prod
                acc["rej"]  += rej

            # Overall type rate (weighted sum across all materials) — GROSS basis
            for pt, acc in type_acc.items():
                total = acc["prod"] + acc["rej"]
                if total > 0:
                    result["overall"][pt] = min(acc["rej"] / total, REJ_CAP)

    except Exception:
        logger.exception("build_rejection_lookup failed for segment=%s", segment)
        return result

    result["has_data"] = bool(result["material"] or result["overall"])
    return result


# ── Per-item rate lookup ───────────────────────────────────────────────────────

def get_item_rate(
    lookup: dict,
    norm_item_code: str,
    plant_type: str,
    material: str,
) -> Tuple[float, str, bool]:
    """Return (rate, basis, capped) for one demand item.

    Fallback ladder:
    1. Item-level  — stored for FITTING items with prod_kg ≥ MIN_PROD_KG
    2. Material-level — "{PLANT_TYPE}:{MATERIAL}" in the summary
    3. Overall type   — "{PLANT_TYPE}" aggregate
    4. (0.0, "none", False) — no data; gross_qty == net_demand

    Args:
        lookup:          result of build_rejection_lookup()
        norm_item_code:  normalised item code matching mp_rejection_item.item_key
        plant_type:      "PIPE" or "FITTING"
        material:        "CPVC" / "UPVC" / "SWR" / "AGRI"
    """
    pt  = plant_type.upper()
    mat = material.upper()

    # 1. Item-level (fitting only in practice)
    item_entry = lookup.get("items", {}).get(norm_item_code)
    if item_entry is not None:
        return item_entry["rate"], "item", item_entry["capped"]

    # 2. Material-level
    mat_rate = lookup.get("material", {}).get(f"{pt}:{mat}")
    if mat_rate is not None:
        capped = mat_rate >= REJ_CAP
        return mat_rate, "material", capped

    # 3. Overall type
    overall = lookup.get("overall", {}).get(pt)
    if overall is not None:
        capped = overall >= REJ_CAP
        return overall, "overall", capped

    # 4. No data
    return 0.0, "none", False
