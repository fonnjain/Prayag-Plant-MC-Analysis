"""costing_rm.py — Raw Material costing for Plumbing (PTMT stubbed).

PLANNED COST
------------
Comes from the machine plan session result (mp_engine.EngineResult):
    material_kg (per item) × recipe cost (Rs/kg for that material × type)

Split: fresh compound at full recipe Rs/kg; pulverizer at Rs 0 with recovery value shown.

ACTUAL COST
-----------
From Report-11 (pipe, "Weight" column) + Report-12 (fitting, "Wt in Kgs") for the
target month × the same recipe Rs/kg.

PRICE SANITY CHECK
------------------
Report-2/3/4 carries "Av. Purchase Price of Last 31 Days" for each compound type.
When the recipe Rs/kg diverges by more than 10% from the purchase price, we surface
a visible flag (not an error gate — just a planner alert).

DATA MISMATCH WARNING
---------------------
FY2026-27 labour sheet fittings production (~3.89M kg / 3 months) vs Report-12
actuals (~1.2M kg / 15 months) cannot describe the same quantity.  Surface both
with sources as a data_mismatch warning.  Never silently pick one.
"""
from __future__ import annotations

import logging
from typing import Optional

import costing_model

logger = logging.getLogger(__name__)

# ── Recipe cost map ────────────────────────────────────────────────────────────

def get_recipe_cost_map(segment: str, effective_month: str) -> dict:
    """Return {(material, type): Rs_per_kg} from mp_compound_recipe.

    The total cost per kg for a (material, type) combination is the sum of
    (ratio_kg × price_per_kg × wastage_factor) across all components,
    normalised to 1 kg of product.

    Returns an empty dict when the table has no data.
    """
    try:
        import mp_model as _mm
        rows = _mm.get_compound_recipes(segment, effective_month)
    except Exception as exc:
        logger.warning("get_recipe_cost_map: failed to read compound recipes: %s", exc)
        return {}

    # Aggregate: {(material, type): {"total_cost": float, "total_ratio": float}}
    acc: dict = {}
    for r in rows:
        mat  = str(r.get("material") or "").upper()
        typ  = str(r.get("type") or "").lower()
        key  = (mat, typ)
        ratio   = float(r.get("ratio_kg") or 0)
        price   = float(r.get("price_per_kg") or 0)
        wf      = float(r.get("wastage_factor") or 1.0)
        if ratio <= 0:
            continue
        bucket = acc.setdefault(key, {"total_cost": 0.0, "total_ratio": 0.0})
        bucket["total_cost"]  += ratio * price * wf
        bucket["total_ratio"] += ratio

    result: dict = {}
    for (mat, typ), v in acc.items():
        tr = v["total_ratio"]
        if tr > 0:
            result[(mat, typ)] = round(v["total_cost"] / tr, 4)

    return result


# ── Planned RM cost ────────────────────────────────────────────────────────────

def compute_planned_rm(engine_result, cost_map: dict) -> dict:
    """Compute planned RM cost from an engine result.

    Args:
        engine_result:  mp_engine.EngineResult (or None)
        cost_map:       result of get_recipe_cost_map()

    Returns a dict with per-material totals and grand total.
    """
    if engine_result is None:
        return {"loaded": False}

    items_by_mat: dict = {}
    no_cost_items: list = []
    total_fresh_kg  = 0.0
    total_pulv_kg   = 0.0
    total_cost_rs   = 0.0

    for it in engine_result.items:
        if not getattr(it, "has_weight", False):
            continue
        mat  = str(getattr(it, "material", "") or "").upper()
        typ  = "pipe"   # engine only handles pipe
        key  = (mat, typ)
        crs  = cost_map.get(key)   # Rs/kg; None if no recipe

        fresh_kg = getattr(it, "fresh_compound_kg", None) or getattr(it, "material_kg", 0)
        pulv_kg  = getattr(it, "pulverizer_kg", 0) or 0

        cost_rs = round(fresh_kg * crs, 0) if crs else None
        if cost_rs is not None:
            total_cost_rs += cost_rs
        total_fresh_kg += fresh_kg
        total_pulv_kg  += pulv_kg

        bucket = items_by_mat.setdefault(mat, {
            "material": mat, "type": typ,
            "recipe_rs_per_kg": crs,
            "fresh_kg": 0.0, "pulv_kg": 0.0,
            "cost_rs": 0.0 if crs else None,
            "item_count": 0,
        })
        bucket["fresh_kg"]    += fresh_kg
        bucket["pulv_kg"]     += pulv_kg
        bucket["item_count"]  += 1
        if crs and bucket["cost_rs"] is not None:
            bucket["cost_rs"] = round(bucket["cost_rs"] + cost_rs, 0)

        if crs is None:
            no_cost_items.append(getattr(it, "item_code", "?"))

    lines = sorted(items_by_mat.values(), key=lambda x: x["material"])
    for ln in lines:
        ln["fresh_kg"] = round(ln["fresh_kg"], 1)
        ln["pulv_kg"]  = round(ln["pulv_kg"],  1)

    return {
        "loaded":          True,
        "lines":           lines,
        "total_fresh_kg":  round(total_fresh_kg, 1),
        "total_pulv_kg":   round(total_pulv_kg,  1),
        "total_cost_rs":   round(total_cost_rs,  0),
        "no_cost_items":   no_cost_items[:20],   # cap for display
    }


def compute_planned_fitting_rm(fitting_result, cost_map: dict) -> dict:
    """Compute planned RM cost for fittings from fitting engine result."""
    if fitting_result is None:
        return {"loaded": False}

    items_by_mat: dict = {}
    total_kg   = 0.0
    total_cost = 0.0

    for it in fitting_result.items:
        if not getattr(it, "has_weight", False):
            continue
        mat  = str(getattr(it, "material", "") or "").upper()
        typ  = "fitting"
        key  = (mat, typ)
        crs  = cost_map.get(key)
        kg   = float(getattr(it, "material_kg", 0) or 0)

        cost_rs = round(kg * crs, 0) if crs else None
        if cost_rs is not None:
            total_cost += cost_rs
        total_kg += kg

        bucket = items_by_mat.setdefault(mat, {
            "material": mat, "type": typ,
            "recipe_rs_per_kg": crs,
            "kg": 0.0, "cost_rs": 0.0 if crs else None,
        })
        bucket["kg"] += kg
        if crs and bucket["cost_rs"] is not None:
            bucket["cost_rs"] = round(bucket["cost_rs"] + cost_rs, 0)

    lines = sorted(items_by_mat.values(), key=lambda x: x["material"])
    for ln in lines:
        ln["kg"] = round(ln["kg"], 1)

    return {
        "loaded":        True,
        "lines":         lines,
        "total_kg":      round(total_kg, 1),
        "total_cost_rs": round(total_cost, 0),
    }


# ── Actual RM cost (from production records) ───────────────────────────────────

def compute_actual_rm(
    monthly_rows: list,    # from get_labour_monthly — has pipe_prod_kg, fitting_prod_kg
    cost_map: dict,
    *,
    fitting_label_source: str = "labour_sheet",
    fitting_r12_kg: Optional[float] = None,
) -> dict:
    """Compute actual RM cost from labour-sheet production figures.

    Uses the labour sheet's pipe and fitting production (already in DB) as the
    actual volume, multiplied by the recipe cost per kg.

    When fitting_r12_kg is provided (from Report-12 actuals), a data_mismatch
    flag is set if the two figures diverge significantly.
    """
    total_pipe_kg    = sum(float(r.get("pipe_prod_kg") or 0) for r in monthly_rows)
    total_fitting_kg = sum(float(r.get("fitting_prod_kg") or 0) for r in monthly_rows)

    # Cost estimates: use overall CPVC (most common) as a proxy when we don't
    # have per-material breakdown from actuals.  This is clearly approximate
    # and labelled as such in the UI.
    pipe_cost_map = {
        mat: crs for (mat, typ), crs in cost_map.items() if typ == "pipe"
    }
    fitting_cost_map = {
        mat: crs for (mat, typ), crs in cost_map.items() if typ == "fitting"
    }

    # Best-effort: use CPVC or the first available pipe recipe
    pipe_crs    = pipe_cost_map.get("CPVC") or next(iter(pipe_cost_map.values()), None)
    fitting_crs = fitting_cost_map.get("CPVC") or next(iter(fitting_cost_map.values()), None)

    pipe_cost_rs    = round(total_pipe_kg    * pipe_crs,    0) if pipe_crs    else None
    fitting_cost_rs = round(total_fitting_kg * fitting_crs, 0) if fitting_crs else None

    # Data mismatch flag
    data_mismatch = None
    if fitting_r12_kg is not None and total_fitting_kg > 0:
        ratio = max(total_fitting_kg, fitting_r12_kg) / min(total_fitting_kg, fitting_r12_kg)
        if ratio > 1.5:   # >50% divergence — surface it
            data_mismatch = {
                "labour_sheet_fitting_kg": round(total_fitting_kg, 0),
                "report12_fitting_kg":     round(fitting_r12_kg, 0),
                "ratio":                   round(ratio, 2),
                "note": (
                    "The labour workbook's fittings production column and Report-12 "
                    "describe different quantities — they cannot both be the output figure. "
                    "Both values are shown here; the business must resolve the definition "
                    "before using either for per-kg cost."
                ),
            }

    return {
        "loaded":             True,
        "total_pipe_kg":      round(total_pipe_kg,    1),
        "total_fitting_kg":   round(total_fitting_kg, 1),
        "pipe_recipe_rs_kg":  pipe_crs,
        "fitting_recipe_rs_kg": fitting_crs,
        "pipe_cost_rs":       pipe_cost_rs,
        "fitting_cost_rs":    fitting_cost_rs,
        "total_cost_rs":      (
            (pipe_cost_rs or 0) + (fitting_cost_rs or 0)
            if (pipe_cost_rs or fitting_cost_rs) else None
        ),
        "data_mismatch":      data_mismatch,
        "note": (
            "Actual cost uses labour-sheet production volumes × recipe Rs/kg. "
            "Per-material breakdown requires a detailed material-consumption report."
        ),
    }


# ── Price sanity check (vs purchased price) ────────────────────────────────────

SANITY_THRESHOLD = 0.10   # 10% divergence flag

def check_recipe_vs_purchase_price(cost_map: dict, purchase_prices: dict) -> list:
    """Flag materials where recipe cost diverges >10% from purchase price.

    Args:
        cost_map:        {(material, type): Rs/kg} — from get_recipe_cost_map
        purchase_prices: {material: Rs/kg}          — from Report-2/3/4

    Returns list of flag dicts.
    """
    flags = []
    for (mat, typ), recipe_rs in cost_map.items():
        pp = purchase_prices.get(mat)
        if pp is None or pp <= 0 or recipe_rs <= 0:
            continue
        delta = abs(recipe_rs - pp) / pp
        if delta > SANITY_THRESHOLD:
            flags.append({
                "material":    mat,
                "type":        typ,
                "recipe_rs":   recipe_rs,
                "purchase_rs": pp,
                "delta_pct":   round(delta * 100, 1),
                "direction":   "above" if recipe_rs > pp else "below",
            })
    return sorted(flags, key=lambda x: -x["delta_pct"])


# ── Combined cost summary ──────────────────────────────────────────────────────

def combined_cost_summary(
    labour_totals: dict,
    planned_rm: dict,
    actual_rm: dict,
) -> dict:
    """Build the top-of-page summary: RM + Labour totals, cost per kg.

    Uses actual RM when available, planned RM as fallback.

    Rs/kg is computed using only *labour-backed* months (months where paid
    hours or wages are present).  Production from partial months (e.g. a
    fittings R12 feed before the payroll tab is populated) is exposed as
    ``partial_prod_kg`` so the template can display it separately.  This
    prevents partial months from inflating the denominator and producing an
    artificially-low cost-per-kg figure.
    """
    rm_cost   = (actual_rm.get("total_cost_rs") if actual_rm.get("loaded")
                 else planned_rm.get("total_cost_rs"))
    lab_cost  = labour_totals.get("paid_wages", 0) + labour_totals.get("contractor_wages", 0)
    # Labour-backed production only (excludes partial months)
    total_kg  = (labour_totals.get("labour_total_prod_kg")
                 or labour_totals.get("total_prod_kg") or 0)
    pipe_kg   = labour_totals.get("pipe_prod_kg")  or 0
    fit_kg    = labour_totals.get("fitting_prod_kg") or 0
    partial_kg = labour_totals.get("partial_prod_kg") or 0
    full_kg    = (labour_totals.get("total_prod_kg") or 0)

    rm_per_kg  = round(rm_cost  / total_kg, 4) if rm_cost  and total_kg else None
    lab_per_kg = round(lab_cost / total_kg, 4) if lab_cost and total_kg else None
    combined_per_kg = (
        round((rm_per_kg + lab_per_kg), 4)
        if rm_per_kg and lab_per_kg else None
    )

    return {
        "rm_cost_rs":       rm_cost,
        "labour_cost_rs":   round(lab_cost, 0),
        "total_cost_rs":    round((rm_cost or 0) + lab_cost, 0),
        "rm_per_kg":        rm_per_kg,
        "labour_per_kg":    lab_per_kg,
        "combined_per_kg":  combined_per_kg,
        # total_kg = labour-backed months only (denominator for Rs/kg)
        "total_kg":         round(total_kg, 0),
        "pipe_kg":          round(pipe_kg, 0),
        "fitting_kg":       round(fit_kg, 0),
        # extra display fields
        "partial_prod_kg":  round(partial_kg, 0),
        "full_total_kg":    round(full_kg, 0),
    }
