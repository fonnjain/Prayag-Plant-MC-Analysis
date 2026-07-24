"""costing_labour.py — Labour costing for Plumbing (PTMT stubbed).

Reads the "Annual <FY> Segment Wise Labour Cost, Solar Cost & Power Cost"
workbook, tab "Plumbing".

TWO COLUMN LAYOUTS
------------------
FY2026-27 adds "Contractor Labour" and "Paid Wages for Contractor" columns
(right of "No. Of Labour" and "Paid Wages" respectively).  FY2025-26 has a
"Per KG Labour Cost" column that FY2026-27 omits (we recompute it either way).
The parser is fully header-based and handles both layouts without branching.

FREEZE RULE
-----------
Frozen FYs (anything earlier than LIVE_FY = "2627") are loaded ONCE and then
the snapshot is immutable.  load_labour_fy() is a no-op for frozen FYs that
are already in the DB unless force=True.  FY2026-27 is always recomputable.

REPORT-22 MACHINE ALLOCATION
-----------------------------
Report-22 (A) in the monthly Pipe & Fitting workbook allocates plant-level
manpower to machines vs departments.  parse_report22_tab() splits them and
returns labour cost per machine = total_hours × per_hour_cost.

DATA MISMATCH FLAG
------------------
The FY2026-27 labour sheet's fittings production (tab-reported ≈3.89M kg for
3 months) is irreconcilable with Report-12 actuals (≈1.2M kg across 15 months).
Both figures are surfaced side-by-side as a data_mismatch warning; we never
silently pick one.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

import costing_model

logger = logging.getLogger(__name__)

# ── Month metadata ─────────────────────────────────────────────────────────────

MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]
_MONTH_NUM = {lbl: i + 1 for i, lbl in enumerate(MONTH_LABELS)}

# ── Header normalisation ───────────────────────────────────────────────────────

def _norm_hdr(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().upper())


_HDR_MAP: dict[str, str] = {
    "MONTH":                            "month_label",
    "NO. OF LABOUR":                    "no_of_labour",
    "NO OF LABOUR":                     "no_of_labour",
    "NUMBER OF LABOUR":                 "no_of_labour",
    "CONTRACTOR LABOUR":                "contractor_labour",
    "PAID HOURS":                       "paid_hours",
    "ACTUAL HOURS":                     "actual_hours",
    "PAID HOURS DEVOTED":               "paid_hours_devoted",
    "ACTUAL HOURS DEVOTED":             "actual_hours_devoted",
    "PAID WAGES":                       "paid_wages",
    "PAID WAGES FOR CONTRACTOR":        "contractor_wages",
    "PER HOUR COST ON PAID HOURS":      "per_hour_cost_paid",
    "PER HOUR COST ON ACTUAL HOURS":    "per_hour_cost_actual",
    "PIPE PRODUCTION (KGS)":            "pipe_prod_kg",
    "PIPE PRODUCTION (KG)":             "pipe_prod_kg",
    "PIPE PRODUCTION":                  "pipe_prod_kg",
    "FITTINGS PRODUCTION (KGS)":        "fitting_prod_kg",
    "FITTINGS PRODUCTION (KG)":         "fitting_prod_kg",
    "FITTINGS PRODUCTION":              "fitting_prod_kg",
    "TOTAL PRODUCTION":                 "total_prod_kg",
    "PER KG LABOUR COST":               "per_kg_labour_cost",
    "PER KG COST":                      "per_kg_labour_cost",
}

# ── Plumbing tab parser ────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Parse a cell value to float; return None for blanks/non-numeric."""
    if v is None or str(v).strip() in ("", "-", "—", "N/A"):
        return None
    s = re.sub(r"[,₹\s]", "", str(v).strip())
    try:
        return float(s)
    except ValueError:
        return None


def _find_header_row(values: list) -> int:
    """Return 0-based index of the header row (contains 'MONTH' cell).

    The spec says row 3 (1-based) = index 2, but we scan the first 8 rows
    to be robust against blank leading rows.
    """
    for ri, row in enumerate(values[:8]):
        for cell in row:
            if _norm_hdr(cell) == "MONTH":
                return ri
    return 2   # default: row 3 (0-indexed)


def parse_plumbing_tab(values: list) -> list:
    """Parse the 'Plumbing' tab of the annual labour workbook.

    Returns a list of dicts, one per month (APR … MAR), in order.
    Skips TOTAL rows and any row whose first column does not match a known
    month abbreviation.

    Works for both the FY2025-26 layout (no contractor columns) and the
    FY2026-27 layout (with Contractor Labour + Paid Wages for Contractor).
    """
    if not values:
        return []

    hdr_idx = _find_header_row(values)
    hdr_row = values[hdr_idx]

    # Map column index → field name
    col_map: dict[int, str] = {}
    for ci, cell in enumerate(hdr_row):
        key = _norm_hdr(cell)
        if key in _HDR_MAP:
            field = _HDR_MAP[key]
            if field not in col_map.values():   # first match wins
                col_map[ci] = field

    if not col_map or "month_label" not in col_map.values():
        logger.warning("parse_plumbing_tab: no 'MONTH' column found in header row %d", hdr_idx)
        return []

    month_col = next(ci for ci, f in col_map.items() if f == "month_label")

    rows = []
    for row in values[hdr_idx + 1:]:
        if len(row) <= month_col:
            continue
        raw_month = str(row[month_col]).strip().upper()[:3]
        if raw_month not in _MONTH_NUM:
            continue   # TOTAL row or blank

        d: dict = {"month_label": raw_month, "month_num": _MONTH_NUM[raw_month]}
        for ci, field in col_map.items():
            if field == "month_label":
                continue
            val = row[ci] if ci < len(row) else None
            d[field] = _num(val)

        # Recompute per_kg_labour_cost if absent (present in FY2525-26, absent in FY2627)
        if d.get("per_kg_labour_cost") is None:
            total_wages = (d.get("paid_wages") or 0) + (d.get("contractor_wages") or 0)
            total_prod  = d.get("total_prod_kg") or 0
            if total_prod > 0:
                d["per_kg_labour_cost"] = round(total_wages / total_prod, 4)

        # Recompute per-hour costs if absent (cross-check with source)
        if d.get("per_hour_cost_paid") is None:
            wages = d.get("paid_wages") or 0
            ph    = d.get("paid_hours") or 0
            if ph > 0:
                d["per_hour_cost_paid"] = round(wages / ph, 4)

        if d.get("per_hour_cost_actual") is None:
            total_wages = (d.get("paid_wages") or 0) + (d.get("contractor_wages") or 0)
            ah = d.get("actual_hours") or 0
            if ah > 0:
                d["per_hour_cost_actual"] = round(total_wages / ah, 4)

        rows.append(d)

    return sorted(rows, key=lambda r: r["month_num"])


def parse_ideal_rates(values: list) -> dict:
    """Parse the 'Ideal Labour Cost' tab.

    Expected layout: a table with columns for segment/product and rate.
    Returns {"pipe": float, "fitting": float} using defaults if parsing fails.

    Default ideal rates (per spec): Pipe = Rs 2.50/kg, Fittings = Rs 6.50/kg.
    """
    defaults = {"pipe": 2.50, "fitting": 6.50}
    if not values:
        return defaults

    pipe_rate = fitting_rate = None
    for row in values:
        for ci, cell in enumerate(row):
            cell_str = str(cell).strip().upper()
            if "PIPE" in cell_str and "FITTING" not in cell_str:
                # Look right for a numeric value
                for v in row[ci + 1: ci + 5]:
                    n = _num(v)
                    if n is not None and 0.1 < n < 50:
                        pipe_rate = n
                        break
            if "FITTING" in cell_str:
                for v in row[ci + 1: ci + 5]:
                    n = _num(v)
                    if n is not None and 0.1 < n < 50:
                        fitting_rate = n
                        break
        if pipe_rate and fitting_rate:
            break

    return {
        "pipe":    pipe_rate    or defaults["pipe"],
        "fitting": fitting_rate or defaults["fitting"],
    }


# ── FY-level computations ──────────────────────────────────────────────────────

def compute_fy_totals(monthly_rows: list) -> dict:
    """Aggregate monthly rows into FY totals."""
    totals: dict = {
        "no_of_labour":         None,
        "contractor_labour":    None,
        "paid_hours":           0.0,
        "actual_hours":         0.0,
        "paid_wages":           0.0,
        "contractor_wages":     0.0,
        "pipe_prod_kg":         0.0,
        "fitting_prod_kg":      0.0,
        "total_prod_kg":        0.0,
        "n_months":             0,
    }
    sum_fields = [
        "paid_hours", "actual_hours", "paid_wages", "contractor_wages",
        "pipe_prod_kg", "fitting_prod_kg", "total_prod_kg",
    ]
    for r in monthly_rows:
        totals["n_months"] += 1
        for f in sum_fields:
            v = r.get(f)
            if v is not None:
                totals[f] = (totals.get(f) or 0) + float(v)
        # Average labour head count
        lc = r.get("no_of_labour")
        if lc is not None:
            totals["no_of_labour"] = (totals["no_of_labour"] or 0) + float(lc)
        clc = r.get("contractor_labour")
        if clc is not None:
            totals["contractor_labour"] = (totals["contractor_labour"] or 0) + float(clc)

    # Derive per-unit aggregates
    ph  = totals["paid_hours"]
    ah  = totals["actual_hours"]
    pw  = totals["paid_wages"]
    cw  = totals["contractor_wages"]
    total_wages = pw + cw

    totals["per_hour_cost_paid"]   = round(total_wages / ph, 4) if ph > 0 else None
    totals["per_hour_cost_actual"] = round(total_wages / ah, 4) if ah > 0 else None
    tp = totals["total_prod_kg"]
    totals["per_kg_labour_cost"]   = round(total_wages / tp, 4) if tp > 0 else None

    return totals


def compute_ideal_comparison(
    monthly_rows: list,
    pipe_rate: float,
    fitting_rate: float,
) -> dict:
    """Compute actual-vs-ideal labour cost comparison.

    Ideal Rs/kg = production-weighted average:
        (pipe_kg × pipe_rate + fitting_kg × fitting_rate) / total_kg

    Returns dict with actual, ideal (per kg), variance (Rs + %).
    """
    total_pipe = sum((r.get("pipe_prod_kg") or 0) for r in monthly_rows)
    total_fit  = sum((r.get("fitting_prod_kg") or 0) for r in monthly_rows)
    total_kg   = total_pipe + total_fit
    total_wages = sum(
        (r.get("paid_wages") or 0) + (r.get("contractor_wages") or 0)
        for r in monthly_rows
    )

    actual_per_kg = round(total_wages / total_kg, 4) if total_kg > 0 else None
    ideal_per_kg  = (
        round((total_pipe * pipe_rate + total_fit * fitting_rate) / total_kg, 4)
        if total_kg > 0 else None
    )

    variance_rs  = None
    variance_pct = None
    if actual_per_kg is not None and ideal_per_kg is not None and ideal_per_kg > 0:
        variance_rs  = round(actual_per_kg - ideal_per_kg, 4)
        variance_pct = round(variance_rs / ideal_per_kg * 100, 2)

    return {
        "actual_per_kg":   actual_per_kg,
        "ideal_per_kg":    ideal_per_kg,
        "pipe_ideal_rate": pipe_rate,
        "fitting_ideal_rate": fitting_rate,
        "variance_rs":     variance_rs,
        "variance_pct":    variance_pct,
        "total_wages":     round(total_wages, 0),
        "total_pipe_kg":   round(total_pipe, 0),
        "total_fitting_kg": round(total_fit, 0),
        "total_kg":        round(total_kg, 0),
    }


# ── Report-22 machine allocation ───────────────────────────────────────────────

# Machine row patterns (matches row labels in column A)
_MACHINE_RE = re.compile(
    r"(PIPE\s*M\s*/?\s*C|M\s*/\s*C\s*-?\s*\d|MOULDING\s*MACHINE|HDPE|GARDEN|"
    r"SOCKET|MIXER|PULVER|GRINDER|TANK)",
    re.I,
)

# Department keywords (rows that are overhead, not machines)
_DEPT_KEYWORDS = [
    "PACKING", "QUALITY", "FG SHIFT", "THREAD", "PRINT", "REWORK", "RE-WORK",
    "DISPATCH", "STORE", "MAINT", "OFFICE", "SECURITY", "CANTEEN",
]


def _is_machine_row(label: str) -> bool:
    return bool(_MACHINE_RE.search(label))


def _is_dept_row(label: str) -> bool:
    lu = label.upper()
    return any(k in lu for k in _DEPT_KEYWORDS)


def parse_report22_tab(values: list) -> dict:
    """Parse Report-22 (A) or (B) from the daily Pipe & Fitting workbook.

    Layout:
      Row 2 (index 1): date column headers starting at col E (index 4), in PAIRS
      Row 3 (index 2): sub-headers "TOTAL MANPOWER" / "TOTAL HOURS" per pair
      Row 6+ (index 5+): data rows; col A = row label

    Returns::

        {
            "machines":    [{"label": str, "manhours": float, "hours": float}, ...],
            "departments": [{"label": str, "manhours": float, "hours": float}, ...],
            "dates":       [str, ...],
            "raw_count":   int,   # total rows parsed
        }
    """
    empty = {"machines": [], "departments": [], "dates": [], "raw_count": 0}
    if not values or len(values) < 4:
        return empty

    # Row 2 (index 1): date headers; row 3 (index 2): MANPOWER / HOURS sub-headers
    date_row  = values[1] if len(values) > 1 else []
    sub_row   = values[2] if len(values) > 2 else []
    DATA_START = 5   # row 6 (0-indexed = 5)

    # Identify date column pairs starting at col E (index 4)
    # Each pair: (col_manpower, col_hours) for a given date
    col_pairs: list[tuple[int, int]] = []
    dates: list[str] = []

    ci = 4
    while ci < len(sub_row) - 1:
        s1 = _norm_hdr(sub_row[ci]   if ci < len(sub_row) else "")
        s2 = _norm_hdr(sub_row[ci+1] if ci+1 < len(sub_row) else "")
        if "MANPOWER" in s1 and "HOUR" in s2:
            date_val = date_row[ci] if ci < len(date_row) else ""
            dates.append(str(date_val).strip())
            col_pairs.append((ci, ci + 1))
            ci += 2
        else:
            ci += 1

    machines: list[dict] = []
    departments: list[dict] = []
    raw_count = 0

    for row in values[DATA_START:]:
        if not row:
            continue
        label = str(row[0]).strip()
        if not label or label.upper() in ("", "TOTAL", "SR NO", "S.NO"):
            continue
        raw_count += 1

        total_mp = total_hrs = 0.0
        for c_mp, c_hrs in col_pairs:
            total_mp  += _num(row[c_mp]  if c_mp  < len(row) else None) or 0.0
            total_hrs += _num(row[c_hrs] if c_hrs < len(row) else None) or 0.0

        entry = {"label": label, "manhours": round(total_mp, 1), "hours": round(total_hrs, 2)}

        if _is_machine_row(label):
            machines.append(entry)
        elif _is_dept_row(label):
            departments.append(entry)
        else:
            departments.append(entry)   # default: treat as overhead

    return {
        "machines":    sorted(machines,    key=lambda x: x["label"]),
        "departments": sorted(departments, key=lambda x: x["label"]),
        "dates":       dates,
        "raw_count":   raw_count,
    }


def allocate_machine_labour(r22_data: dict, per_hour_cost: float) -> list:
    """Return per-machine labour cost = hours × per_hour_cost.

    Args:
        r22_data:       result of parse_report22_tab()
        per_hour_cost:  monthly per-actual-hour cost from the Plumbing tab
    """
    result = []
    for m in r22_data.get("machines", []):
        hrs  = m["hours"]
        cost = round(hrs * per_hour_cost, 0) if per_hour_cost else None
        result.append({**m, "cost_rs": cost})
    return result


# ── Main loader (DB-backed, freeze-aware) ──────────────────────────────────────

def load_labour_fy(
    segment: str,
    fy: str,
    *,
    force: bool = False,
) -> dict:
    """Load labour data for (segment, fy) from the source workbook into DB.

    FREEZE: if fy is frozen and already loaded, this is a NO-OP unless force=True.
    FY2026-27 (LIVE_FY) is always reloaded.

    Returns::

        {"ok": bool, "n_months": int, "frozen": bool, "skipped": bool,
         "errors": list, "ideal_rates": {...}}
    """
    import sheets as _sh  # local import avoids circular at module level

    file_id = costing_model.labour_file_id(segment, fy)
    if not file_id:
        return {
            "ok": False,
            "error": f"No workbook registered for {segment} / {fy}.",
            "n_months": 0, "frozen": False, "skipped": False, "errors": [],
        }

    frozen = costing_model.is_frozen(fy)

    # Check existing snapshot
    if frozen and not force:
        meta = costing_model.get_labour_meta(segment, fy)
        if meta and meta.get("n_months", 0) > 0:
            return {
                "ok": True, "n_months": meta["n_months"],
                "frozen": True, "skipped": True,
                "errors": [],
                "ideal_rates": {
                    "pipe":    float(meta.get("pipe_ideal_rate") or 2.50),
                    "fitting": float(meta.get("fitting_ideal_rate") or 6.50),
                },
            }

    token = _sh._get_access_token()
    if not token:
        return {
            "ok": False, "error": "No auth token (Google Sheets connection unavailable).",
            "n_months": 0, "frozen": frozen, "skipped": False, "errors": [],
        }

    errors: list = []

    # Read both tabs in one batch call
    try:
        matrices = _sh.batch_get(file_id, ["Plumbing", "Ideal Labour Cost"], token)
    except Exception as exc:
        return {
            "ok": False, "error": str(exc),
            "n_months": 0, "frozen": frozen, "skipped": False,
            "errors": [str(exc)],
        }

    plumbing_vals     = matrices.get("Plumbing", [])
    ideal_vals        = matrices.get("Ideal Labour Cost", [])

    monthly_rows = parse_plumbing_tab(plumbing_vals)
    ideal_rates  = parse_ideal_rates(ideal_vals)

    if not monthly_rows:
        errors.append("Plumbing tab returned 0 monthly rows — check tab name / layout.")
        return {
            "ok": False, "error": "No monthly rows parsed.",
            "n_months": 0, "frozen": frozen, "skipped": False,
            "errors": errors,
            "ideal_rates": ideal_rates,
        }

    # Persist to DB
    try:
        costing_model.upsert_labour_monthly(segment, fy, monthly_rows)
        costing_model.upsert_labour_meta(
            segment, fy,
            frozen=frozen,
            n_months=len(monthly_rows),
            pipe_ideal_rate=ideal_rates["pipe"],
            fitting_ideal_rate=ideal_rates["fitting"],
            source_file_id=file_id,
        )
    except costing_model.CostingModelError as e:
        errors.append(str(e))

    return {
        "ok": True,
        "n_months": len(monthly_rows),
        "frozen": frozen,
        "skipped": False,
        "errors": errors,
        "ideal_rates": ideal_rates,
    }


# ── Read path (DB) ─────────────────────────────────────────────────────────────

def get_labour_view(segment: str, fy: str) -> dict:
    """Return the full labour view dict for the costing page.

    Tries DB first; if no data, returns {"loaded": False}.
    """
    meta    = costing_model.get_labour_meta(segment, fy)
    monthly = costing_model.get_labour_monthly(segment, fy)

    if not monthly:
        return {"loaded": False, "meta": meta}

    pipe_rate    = float(meta.get("pipe_ideal_rate") or 2.50) if meta else 2.50
    fitting_rate = float(meta.get("fitting_ideal_rate") or 6.50) if meta else 6.50

    totals     = compute_fy_totals(monthly)
    comparison = compute_ideal_comparison(monthly, pipe_rate, fitting_rate)

    return {
        "loaded":     True,
        "frozen":     costing_model.is_frozen(fy),
        "meta":       meta,
        "monthly":    monthly,
        "totals":     totals,
        "comparison": comparison,
        "ideal_rates": {"pipe": pipe_rate, "fitting": fitting_rate},
    }
