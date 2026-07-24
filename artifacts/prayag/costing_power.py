"""costing_power.py — Power costing data for Plumbing.

SOURCES
-------
UNIT-2 tab of the annual labour workbook ("Annual <FY> Segment Wise Labour
Cost, Solar Cost & Power Cost"):
  - JVVL Power Amount, kWh consumed, per-unit cost, solar generation units,
    total units, kwh/kg, tariff-rate scenarios (7.08 / 11.5 basic rate),
    per-kg power cost, per-kg labour cost, total cost.
  - Production: PVC Production (Kgs) = Plumbing production.
  - Contractor wages for the contractor toggle ("Paid Wages for Contractor Labour").

"Ideal Power Cost" tab of the same workbook:
  - Per-month: total ideal power, total actual power, ideal/kg, actual/kg
    on an ALL-PLANTS denominator (Plumbing + Garden + HDPE + Tank).
  - TOTAL row: per-kg RATES (pipe=4.00, fitting=8.00, garden=3.00, HDPE=4.00, tank=5.00).

"Ideal Labour Cost" tab of the same workbook:
  - TOTAL row ONLY: pipe rate=2.50, fitting rate=6.50.
  - Month rows use PIECE COUNTS for fittings (known mislabelling) — never read.

DENOMINATOR NOTE
----------------
Per-kg power from "Ideal Power Cost" tab uses an ALL-PLANTS denominator.
Per-kg power from UNIT-2 uses Plumbing-only denominator. Both stored, labelled.

FREEZE RULE
-----------
Same as costing_labour.py: frozen FYs loaded once; no-op if already in DB
unless force=True.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import costing_model

logger = logging.getLogger(__name__)

MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]
_MONTH_NUM = {lbl: i + 1 for i, lbl in enumerate(MONTH_LABELS)}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().upper())


def _num(v) -> Optional[float]:
    if v is None or str(v).strip() in ("", "-", "—", "N/A"):
        return None
    s = re.sub(r"[,₹\s]", "", str(v).strip())
    try:
        return float(s)
    except ValueError:
        return None


def _parse_month(cell) -> Optional[str]:
    """Extract APR/MAY/… from "APR'26", "MAY'26", etc."""
    lbl = str(cell).strip().upper()[:3]
    return lbl if lbl in _MONTH_NUM else None


def _get(row: list, ci: int) -> Optional[float]:
    return _num(row[ci]) if 0 <= ci < len(row) else None


# ── UNIT-2 parser ──────────────────────────────────────────────────────────────

# Single-occurrence header → field name mappings
_U2_SINGLE: dict[str, str] = {
    "MONTH":                              "month_label",
    "PVC PRODUCTION (KGS)":              "pvc_prod_kg",
    "TOTAL PRODUCTION (KG)":             "total_prod_kg_u2",
    "TOTAL  PRODUCTION (KG)":            "total_prod_kg_u2",
    "LABOUR":                             "headcount_u2",
    "CONTRACTOR LABOUR":                  "contractor_count_u2",
    "PAID WAGES":                         "paid_wages_u2",
    "PAID WAGES FOR CONTRACTOR LABOUR":   "contractor_wages_u2",
    "TOTAL PAID WAGES":                   "total_wages_u2",
    "JVVL POWER AMOUNT":                  "jvvl_amount",
    "ELECTRICITY GENERATION UNIT (KWH)":  "elec_gen_kwh",
    "PER UNIT POWER COST":                "per_unit_cost",
    "SOLAR GENERATION UNIT (KWH)":        "solar1_kwh",
    "2ND SOLAR GENERATION UNIT (KWH)":    "solar2_kwh",
    "TOTAL UNIT CONSUMED":                "total_kwh",
    "UNIT UTILISE PER KG":                "kwh_per_kg",
    "AS PER 7.08 BASIC RATE":             "rate_708_rs",
    "AS PER 11.5 BASIC RATE":             "rate_1150_rs",
    "PER KG LABOUR COST":                 "per_kg_labour_u2",
    "NEW TOTAL COST":                     "new_total_cost",
}

# Duplicate-header fields: occurrence 1 → field A, occurrence 2 → field B
_U2_DUP: dict[str, dict[int, str]] = {
    "TOTAL POWER":       {1: "total_power_708",  2: "total_power_1150"},
    "PER KG POWER COST": {1: "per_kg_power_708", 2: "per_kg_power_1150"},
    "TOTAL COST":        {1: "total_cost_708"},
}


def _u2_col_map(hdr_row: list) -> dict[int, str]:
    seen: dict[str, int] = {}
    col_map: dict[int, str] = {}
    assigned: set = set()
    for ci, cell in enumerate(hdr_row):
        key = _norm(cell)
        seen[key] = seen.get(key, 0) + 1
        occ = seen[key]
        if key in _U2_DUP:
            field = _U2_DUP[key].get(occ)
            if field:
                col_map[ci] = field
        elif key in _U2_SINGLE:
            field = _U2_SINGLE[key]
            if field not in assigned:
                col_map[ci] = field
                assigned.add(field)
    return col_map


def parse_unit2_tab(values: list) -> list:
    """Parse UNIT-2 tab → one dict per month, Plumbing segment."""
    if not values:
        return []
    hdr_idx = -1
    for ri, row in enumerate(values[:6]):
        keys = {_norm(c) for c in row}
        if "MONTH" in keys and "SEGMENT" in keys:
            hdr_idx = ri
            break
    if hdr_idx < 0:
        logger.warning("parse_unit2_tab: header row not found")
        return []

    col_map = _u2_col_map(values[hdr_idx])
    month_col = next((ci for ci, f in col_map.items() if f == "month_label"), -1)
    if month_col < 0:
        logger.warning("parse_unit2_tab: MONTH column not found")
        return []

    rows = []
    for row in values[hdr_idx + 1:]:
        if len(row) <= month_col:
            continue
        month = _parse_month(row[month_col])
        if not month:
            continue
        d: dict = {"month_label": month, "month_num": _MONTH_NUM[month]}
        for ci, field in col_map.items():
            if field == "month_label":
                continue
            d[field] = _get(row, ci)
        rows.append(d)

    return sorted(rows, key=lambda r: r["month_num"])


# ── Ideal Power Cost tab parser ────────────────────────────────────────────────

def parse_ideal_power_tab(values: list) -> Tuple[dict, list]:
    """Parse 'Ideal Power Cost' tab.

    Returns:
        rates  = {"pipe": 4.0, "fitting": 8.0, "garden": 3.0, "hdpe": 4.0, "tank": 5.0}
        monthly = list of dicts: ideal_power_total, actual_power_total,
                  ideal_kg_power, actual_kg_power  (all-plants denominator)
    """
    rates: dict = {}
    monthly: list = []
    if not values:
        return rates, monthly

    hdr_idx = -1
    for ri, row in enumerate(values[:6]):
        for c in row:
            if _norm(c) == "MONTH":
                hdr_idx = ri
                break
        if hdr_idx >= 0:
            break
    if hdr_idx < 0:
        return rates, monthly

    hdr_row = values[hdr_idx]
    month_col = -1
    ideal_total_col = -1
    actual_total_col = -1
    ideal_kg_col = -1
    actual_kg_col = -1
    pipe_rate_col = -1
    fitting_rate_col = -1
    ideal_cols_seen = 0

    for ci, cell in enumerate(hdr_row):
        key = _norm(cell)
        if key == "MONTH":
            month_col = ci
        elif key in ("IDEAL POWER COST", "IDEAL COST"):
            ideal_cols_seen += 1
            if ideal_cols_seen == 1:
                pipe_rate_col = ci
            elif ideal_cols_seen == 2:
                fitting_rate_col = ci
        elif "TOTAL IDEAL" in key:
            ideal_total_col = ci
        elif "TOTAL ACTUAL" in key:
            actual_total_col = ci
        elif key == "IDEAL KG COST":
            ideal_kg_col = ci
        elif key == "ACTUAL KG COST":
            actual_kg_col = ci

    for row in values[hdr_idx + 1:]:
        if month_col < 0 or len(row) <= month_col:
            continue
        month_raw = _norm(str(row[month_col]))
        if month_raw == "TOTAL":
            r_pipe = _get(row, pipe_rate_col)
            r_fit  = _get(row, fitting_rate_col)
            if r_pipe and r_pipe < 50:
                rates["pipe"] = r_pipe
            if r_fit and r_fit < 50:
                rates["fitting"] = r_fit
            continue
        month = _parse_month(month_raw)
        if not month:
            continue
        monthly.append({
            "month_label":        month,
            "month_num":          _MONTH_NUM[month],
            "ideal_power_total":  _get(row, ideal_total_col),
            "actual_power_total": _get(row, actual_total_col),
            "ideal_kg_power":     _get(row, ideal_kg_col),
            "actual_kg_power":    _get(row, actual_kg_col),
        })

    return rates, sorted(monthly, key=lambda r: r["month_num"])


# ── Ideal Labour Cost tab — rates only ─────────────────────────────────────────

def parse_ideal_labour_rates(values: list) -> dict:
    """Read ONLY the per-kg RATES from the TOTAL row.

    Month rows in 'Ideal Labour Cost' use PIECE counts for fittings (mislabelled)
    so their totals are wrong.  The TOTAL row carries rates (2.50, 6.50) not totals.
    """
    defaults = {"pipe": 2.50, "fitting": 6.50}
    if not values:
        return defaults

    hdr_idx = -1
    for ri, row in enumerate(values[:6]):
        for c in row:
            if _norm(c) == "MONTH":
                hdr_idx = ri
                break
        if hdr_idx >= 0:
            break
    if hdr_idx < 0:
        return defaults

    hdr_row = values[hdr_idx]
    month_col = -1
    pipe_ideal_col = -1
    fitting_ideal_col = -1
    ideal_cols_seen = 0
    for ci, cell in enumerate(hdr_row):
        key = _norm(cell)
        if key == "MONTH":
            month_col = ci
        elif key in ("IDEAL LABOUR COST", "IDEAL COST"):
            ideal_cols_seen += 1
            if ideal_cols_seen == 1:
                pipe_ideal_col = ci
            elif ideal_cols_seen == 2:
                fitting_ideal_col = ci

    for row in values[hdr_idx + 1:]:
        if month_col < 0 or len(row) <= month_col:
            continue
        if _norm(str(row[month_col])) == "TOTAL":
            r_pipe = _get(row, pipe_ideal_col)
            r_fit  = _get(row, fitting_ideal_col)
            pipe = r_pipe if (r_pipe and r_pipe < 50) else defaults["pipe"]
            fit  = r_fit  if (r_fit  and r_fit  < 50) else defaults["fitting"]
            return {"pipe": pipe, "fitting": fit}

    return defaults


# ── DB upsert ──────────────────────────────────────────────────────────────────

_POWER_FIELDS = [
    "month_label", "month_num",
    "pvc_prod_kg", "total_prod_kg_u2",
    "headcount_u2", "contractor_count_u2",
    "paid_wages_u2", "contractor_wages_u2", "total_wages_u2",
    "jvvl_amount", "elec_gen_kwh", "per_unit_cost",
    "solar1_kwh", "solar2_kwh", "total_kwh", "kwh_per_kg",
    "rate_708_rs", "rate_1150_rs",
    "total_power_708", "total_power_1150",
    "per_kg_power_708", "per_kg_power_1150",
    "per_kg_labour_u2", "total_cost_708", "new_total_cost",
    "ideal_power_total", "actual_power_total",
    "ideal_kg_power", "actual_kg_power",
    "pipe_ideal_power_rate", "fitting_ideal_power_rate",
    "pipe_ideal_labour_rate", "fitting_ideal_labour_rate",
]


def _upsert_power_monthly(segment: str, fy: str, rows: list) -> int:
    import store
    if not costing_model.AVAILABLE:
        return 0
    try:
        costing_model.init_costing_tables()
        cols  = ", ".join(_POWER_FIELDS)
        plcdr = ", ".join(f"%({f})s" for f in _POWER_FIELDS)
        upd   = ", ".join(f"{f} = EXCLUDED.{f}" for f in _POWER_FIELDS if f not in ("month_label", "month_num"))
        sql = f"""
            INSERT INTO costing_power_monthly (segment, fy, {cols})
            VALUES (%(segment)s, %(fy)s, {plcdr})
            ON CONFLICT (segment, fy, month_label) DO UPDATE SET {upd}
        """
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM costing_power_monthly WHERE segment=%s AND fy=%s", (segment, fy))
            count = 0
            for r in rows:
                params = {"segment": segment, "fy": fy}
                params.update({f: r.get(f) for f in _POWER_FIELDS})
                cur.execute(sql, params)
                count += 1
        return count
    except Exception:
        logger.exception("_upsert_power_monthly failed")
        return 0


def get_power_monthly(segment: str, fy: str) -> list:
    """Return all monthly power rows from DB, ordered APR→MAR."""
    import store, psycopg2.extras
    if not costing_model.AVAILABLE:
        return []
    try:
        costing_model.init_costing_tables()
        with store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM costing_power_monthly WHERE segment=%s AND fy=%s ORDER BY month_num",
                (segment, fy),
            )
            return [costing_model._coerce_row(dict(r)) for r in cur.fetchall()]
    except Exception:
        logger.exception("get_power_monthly failed")
        return []


# ── Load entrypoint ────────────────────────────────────────────────────────────

def load_power_fy(segment: str, fy: str, *, force: bool = False) -> dict:
    """Load power data for (segment, fy) from UNIT-2 + ideal tabs into DB.

    Returns {"ok": bool, "n_months": int, "frozen": bool, "skipped": bool, "errors": list}.
    """
    import sheets as _sh

    file_id = costing_model.LABOUR_SOURCES.get(segment, {}).get(fy)
    if not file_id:
        return {"ok": False, "error": f"No workbook for {segment}/{fy}.",
                "n_months": 0, "frozen": False, "skipped": False, "errors": []}

    frozen = costing_model.is_frozen(fy)

    if frozen and not force:
        existing = get_power_monthly(segment, fy)
        if existing:
            return {"ok": True, "n_months": len(existing), "frozen": True,
                    "skipped": True, "errors": []}

    token = _sh._get_access_token()
    if not token:
        return {"ok": False, "error": "No auth token (Google Sheets unavailable).",
                "n_months": 0, "frozen": frozen, "skipped": False,
                "errors": ["No auth token."]}

    try:
        matrices = _sh.batch_get(
            file_id,
            ["UNIT-2", "Ideal Power Cost", "Ideal Labour Cost"],
            token,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc),
                "n_months": 0, "frozen": frozen, "skipped": False, "errors": [str(exc)]}

    u2_rows = parse_unit2_tab(matrices.get("UNIT-2", []))
    power_rates, ipc_monthly = parse_ideal_power_tab(matrices.get("Ideal Power Cost", []))
    labour_rates = parse_ideal_labour_rates(matrices.get("Ideal Labour Cost", []))

    if not u2_rows:
        err = "UNIT-2 tab returned 0 monthly rows."
        return {"ok": False, "error": err, "n_months": 0, "frozen": frozen,
                "skipped": False, "errors": [err]}

    ipc_by_month = {r["month_label"]: r for r in ipc_monthly}
    for r in u2_rows:
        ipc = ipc_by_month.get(r["month_label"], {})
        r["ideal_power_total"]        = ipc.get("ideal_power_total")
        r["actual_power_total"]       = ipc.get("actual_power_total")
        r["ideal_kg_power"]           = ipc.get("ideal_kg_power")
        r["actual_kg_power"]          = ipc.get("actual_kg_power")
        r["pipe_ideal_power_rate"]    = power_rates.get("pipe", 4.0)
        r["fitting_ideal_power_rate"] = power_rates.get("fitting", 8.0)
        r["pipe_ideal_labour_rate"]   = labour_rates.get("pipe", 2.50)
        r["fitting_ideal_labour_rate"] = labour_rates.get("fitting", 6.50)

    n = _upsert_power_monthly(segment, fy, u2_rows)
    return {"ok": True, "n_months": n, "frozen": frozen, "skipped": False, "errors": []}
