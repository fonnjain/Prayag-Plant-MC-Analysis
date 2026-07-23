"""mp_wastage.py — Measured material wastage (%) from PIPE Report-15.

Aggregates production_kg and wastage_kg by type across all available PIPE
months from Report-15, persists in mp_wastage_summary / mp_wastage_meta, and
provides the per-type waste fractions used by the machine-planning engine.

FORMULA
-------
    waste_pct = wastage_kg / production_kg

Verified against 19 monthly workbooks (16,639,312 kg production, 84,907 kg
wastage): overall measured rate = 0.51%.

ENGINE INTEGRATION (ALWAYS BOTH, IN ORDER)
-------------------------------------------
    gross_qty   = net_demand / (1 − rejection_rate)   ← mp_rejection_plan.py
    material_kg = gross_qty × wt × (1 + waste_frac)   ← this module

TYPE KEYS (from Report-15 column headers)
------------------------------------------
    "CPVC"   → PIPE, CPVC
    "UPVC"   → PIPE, UPVC
    "SWR"    → PIPE, SWR
    "AGRI"   → PIPE, AGRI
    "UPVC_F" → FITTING, UPVC  (header "PRODUCTION UPVC F")
    "SWR_F"  → FITTING, SWR   (header "PRODUCTION SWR F")

PLANNER OVERRIDE
----------------
When mp_params.waste_pct > 0 the planner has set a manual override;
build_wastage_lookup() applies it uniformly across all types.  Set to 0 to
revert to the auto-measured rate.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple

import sources
import store

logger = logging.getLogger(__name__)

WASTE_CAP    = 0.20    # 20% cap — anything higher is implausible data
SAFE_DEFAULT = 0.0051  # measured overall; used before first recompute (~0.51%)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fitting_key(material: str) -> Optional[str]:
    """Return fitting-specific type key when one exists, else None."""
    mat = material.upper()
    return f"{mat}_F" if mat in ("UPVC", "SWR") else None


# ── Engine lookup (called at plan run time) ────────────────────────────────────

def build_wastage_lookup(segment: str, override_pct: float = 0.0) -> dict:
    """Return per-type wastage fractions for use in the engine.

    When ``override_pct > 0`` (planner manual override), all types return that
    value with basis='override'.  Otherwise measured rates from mp_wastage_summary
    are used; falls back to SAFE_DEFAULT when DB has no data yet.

    Returns::

        {
            "rates":    {type_key: waste_fraction},
            "all":      float,   # overall weighted average
            "basis":    "measured" | "override" | "default",
            "has_data": bool,
        }
    """
    if override_pct > 0:
        rate = min(override_pct / 100.0, WASTE_CAP)
        return {"rates": {}, "all": rate, "basis": "override", "has_data": True}

    result: dict = {
        "rates": {}, "all": SAFE_DEFAULT, "basis": "default", "has_data": False,
    }
    if not store.AVAILABLE:
        return result
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT type_key, prod_kg, wastage_kg
                   FROM mp_wastage_summary
                   WHERE segment = %s AND prod_kg > 0
                   ORDER BY type_key""",
                (segment,),
            )
            rows = cur.fetchall()
    except Exception:
        logger.exception("build_wastage_lookup DB read failed for segment=%s", segment)
        return result

    if not rows:
        return result

    total_prod = total_waste = 0.0
    for type_key, prod_kg, wastage_kg in rows:
        prod  = float(prod_kg  or 0)
        waste = float(wastage_kg or 0)
        if prod <= 0:
            continue
        result["rates"][type_key] = min(waste / prod, WASTE_CAP)
        total_prod  += prod
        total_waste += waste

    if total_prod > 0:
        result["all"]      = min(total_waste / total_prod, WASTE_CAP)
        result["basis"]    = "measured"
        result["has_data"] = True

    return result


def get_waste_frac(
    lookup: dict,
    material: str,
    is_fitting: bool = False,
) -> Tuple[float, str]:
    """Return (waste_fraction, basis) for a demand item.

    Fallback ladder:
    1. Override applies uniformly when basis='override'
    2. Fitting-specific key (UPVC_F / SWR_F) when is_fitting and key present
    3. Pipe material key (CPVC / UPVC / SWR / AGRI)
    4. Overall weighted average ("all")
    5. SAFE_DEFAULT (0.51%)
    """
    basis = lookup.get("basis", "default")
    rates = lookup.get("rates", {})

    if basis == "override":
        return lookup.get("all", SAFE_DEFAULT), "override"

    mat = material.upper()

    # 1. Fitting-specific key
    if is_fitting:
        fk = _fitting_key(mat)
        if fk and fk in rates:
            return rates[fk], "measured"

    # 2. Pipe material key
    if mat in rates:
        return rates[mat], "measured"

    # 3. Overall
    overall = lookup.get("all", SAFE_DEFAULT)
    return overall, "measured" if lookup.get("has_data") else "default"


# ── Recompute (triggered from Settings page) ───────────────────────────────────

def recompute_wastage(segment: str) -> dict:
    """Read Report-15 from all PIPE months, aggregate, write to mp_wastage_summary.

    Uses sheets.load_yield_records() which caches 15 min and handles auth.
    Only R15_kg records are counted (not R13/R14 pcs records).

    Returns a summary dict: {ok, n_months, errors, summary: [{type_key, pct, ...}]}.
    """
    import sheets as _sh  # noqa: PLC0415 – intentional late import

    months = sources.planning_months("PIPE")
    if not months:
        return {"ok": False, "error": "No PIPE months in sources."}

    # Accumulate: {type_key: {"prod": float, "waste": float}}
    acc: Dict[str, Dict[str, float]] = {}
    months_with_data = 0
    errors: list = []

    for ym in months:
        try:
            recs = _sh.load_yield_records("PIPE", ym)
        except Exception as exc:
            errors.append(f"{ym}: {exc}")
            continue
        month_ok = False
        for rec in recs:
            if getattr(rec, "source", "") != "R15_kg":
                continue
            tk   = getattr(rec, "type", "")
            prod  = float(getattr(rec, "production_kg", 0) or 0)
            waste = float(getattr(rec, "wastage_kg",    0) or 0)
            if not tk or (prod == 0 and waste == 0):
                continue
            bucket = acc.setdefault(tk, {"prod": 0.0, "waste": 0.0})
            bucket["prod"]  += prod
            bucket["waste"] += waste
            month_ok = True
        if month_ok:
            months_with_data += 1

    if not acc:
        return {
            "ok": False,
            "error": "No Report-15 data found.",
            "months_checked": len(months),
            "errors": errors,
        }

    if not store.AVAILABLE:
        return {"ok": False, "error": "Database unavailable."}

    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_wastage_summary WHERE segment = %s", (segment,)
            )
            for tk, v in acc.items():
                cur.execute(
                    """INSERT INTO mp_wastage_summary
                               (segment, type_key, prod_kg, wastage_kg, n_months)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (segment, type_key) DO UPDATE
                           SET prod_kg    = EXCLUDED.prod_kg,
                               wastage_kg = EXCLUDED.wastage_kg,
                               n_months   = EXCLUDED.n_months""",
                    (segment, tk, v["prod"], v["waste"], months_with_data),
                )
            cur.execute(
                """INSERT INTO mp_wastage_meta (segment, n_months, last_recomputed)
                   VALUES (%s, %s, now())
                   ON CONFLICT (segment) DO UPDATE
                       SET n_months = EXCLUDED.n_months,
                           last_recomputed = now()""",
                (segment, months_with_data),
            )
    except Exception as exc:
        logger.exception("recompute_wastage: DB write failed")
        return {"ok": False, "error": str(exc)}

    summary = []
    for tk, v in sorted(acc.items()):
        prod  = v["prod"]
        waste = v["waste"]
        pct   = round(waste / prod * 100, 3) if prod > 0 else None
        summary.append({"type_key": tk, "prod_kg": prod, "wastage_kg": waste, "pct": pct})

    return {
        "ok": True,
        "n_months": months_with_data,
        "n_months_checked": len(months),
        "errors": errors,
        "summary": summary,
    }


# ── Display helpers ───────────────────────────────────────────────────────────

def get_wastage_summary(segment: str) -> list:
    """Return rows for display on the Settings page (sorted by type_key)."""
    if not store.AVAILABLE:
        return []
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT type_key, prod_kg, wastage_kg, n_months
                   FROM mp_wastage_summary
                   WHERE segment = %s
                   ORDER BY type_key""",
                (segment,),
            )
            rows = cur.fetchall()
    except Exception:
        logger.exception("get_wastage_summary failed")
        return []
    result = []
    for type_key, prod_kg, wastage_kg, n_months in rows:
        prod  = float(prod_kg  or 0)
        waste = float(wastage_kg or 0)
        pct   = round(waste / prod * 100, 3) if prod > 0 else None
        result.append({
            "type_key":   type_key,
            "prod_kg":    round(prod,  1),
            "wastage_kg": round(waste, 1),
            "pct":        pct,
            "n_months":   n_months,
        })
    return result


def get_wastage_meta(segment: str) -> Optional[dict]:
    """Return the metadata row for *segment*, or None if not yet computed."""
    if not store.AVAILABLE:
        return None
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT n_months, last_recomputed FROM mp_wastage_meta WHERE segment=%s",
                (segment,),
            )
            row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    import datetime
    lr = row[1]
    return {
        "n_months": row[0],
        "last_recomputed": lr.strftime("%d-%m-%Y %H:%M") if isinstance(lr, datetime.datetime) else str(lr),
    }
