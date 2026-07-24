"""costing_labour.py — Labour costing for Plumbing (PTMT stubbed).

LABOUR SHEET — WAGES AND HOURS ONLY
-------------------------------------
The "Annual <FY> Segment Wise Labour Cost, Solar Cost & Power Cost" workbook,
tab "Plumbing", is used for: No. of Labour, Contractor Labour, Paid/Actual Hours,
Paid Wages, Contractor Wages, and per-hour costs.

PIPE production (kg) is ALSO taken from this sheet's "Pipe Production" column —
verified correct for all FYs (that column IS genuinely kilograms).

FITTINGS PRODUCTION — SOURCED FROM REPORT-12 (NOT the labour sheet)
----------------------------------------------------------------------
The labour sheet's "Fittings Production (KGS)" column is MISLABELLED in
FY2026-27: it actually contains gross PIECES, not kilograms.

Proof: Apr/May/Jun 2026 labour-sheet reads ≈ 1,342,290 / 1,164,889 / 1,384,394.
Report-12 PIECE counts for the same months: 1,340,117 / 1,163,032 / 1,382,048
(gap = rejected pieces exactly).  Actual kg from Report-12: 93,839 / 79,875 /
101,512.

Fix: load_labour_fy() reads Report-12 "Weight of Total Production" from each
month's PIPE workbook (DAILY_SOURCES["PIPE"]) and stores that as the
authoritative fitting_prod_kg.  The labour sheet figure for fittings is never
stored.

TWO COLUMN LAYOUTS (labour sheet)
----------------------------------
FY2026-27 adds "Contractor Labour" and "Paid Wages for Contractor" columns.
FY2025-26 has a "Per KG Labour Cost" column that FY2026-27 omits (recomputed).
The parser is fully header-based and handles both layouts without branching.

REPORT-12 COLUMN LAYOUTS
-------------------------
FY2026-27: SAP Code | Item Name | Machine | Date | Pc | Wt in Kgs |
           Weight per Pc | Weight of Total Production | Runner Produce | ...
FY2025-26: no SAP Code column (everything shifts one column left).
Header-based parsing handles both layouts automatically.

DATA-QUALITY FLAG (J-vs-M)
---------------------------
For each month, if |Weight of Total Production − Wt in Kgs| / W-T-P > 2%,
a variance warning is raised naming the divergent rows (item, machine, pcs,
both weights).  "Wt in Kgs" is hand-keyed; "Weight of Total Production" is
formula-driven (pcs × standard weight / 1000).  June 2026 shows 4.4% total
divergence with 81 bad hand-entries — that is the design case.

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
# 0-based index within the FY (APR=0 … MAR=11) — used for DAILY_SOURCES lookup
_MONTH_IDX = {lbl: i for i, lbl in enumerate(MONTH_LABELS)}

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


# ── Report-12 fittings kg parser ──────────────────────────────────────────────
#
# Authoritative fittings formula (gross actual = good output + rejected weight):
#   fitting_kg = "Wt in Kgs" + "Actual Rejection Weight (in Kgs)"
#
# "Wt in Kgs" is a SUB-HEADER on the row below the main header row (it sits
# under the "Output Production" group header).  "Actual Rejection Weight
# (in Kgs)" lives on the main header row.
#
# "Weight of Total Production" (formula = pcs × std weight) is used ONLY for
# the J-vs-M data-quality variance check, NOT for the costing figure.
#
# Per-row divergence threshold (Wt-in-Kgs vs Weight-of-Total-Prod):
_R12_ROW_VARIANCE_PCT = 5.0
# Total-month divergence threshold for the UI warning card:
R12_TOTAL_VARIANCE_WARN_PCT = 2.0
# Unit-mismatch multiplier guard for the REJECTION & PRODUCTION tab:
_REJ_PROD_TAB_MISMATCH_RATIO = 10.0


def _fy_month_to_ym(fy: str, month_label: str) -> str:
    """Convert a 4-char FY code + month label to a YYYY-MM DAILY_SOURCES key.

    FY "2627" starts April 2026 → APR-SEP map to 2026-04…09,
    OCT-DEC to 2026-10…12, JAN-MAR to 2027-01…03.
    """
    start_year = 2000 + int(fy[:2])
    idx = _MONTH_IDX.get(month_label.upper(), 0)   # APR=0 … MAR=11
    if idx <= 8:           # APR(0)…DEC(8) → same calendar year as FY start
        return f"{start_year}-{idx + 4:02d}"
    else:                  # JAN(9)…MAR(11) → next calendar year
        return f"{start_year + 1}-{idx - 8:02d}"


def _r12_find_header(values: list) -> tuple:
    """Scan the first 12 rows for the Report-12 two-row header.

    Report-12 uses a TWO-ROW header:
      Main row  — contains "Actual Rejection Weight (in Kgs)" and
                  "Weight of Total Production" (and auxiliary fields).
      Sub-row   — immediately below the main row; contains "Wt in Kgs"
                  positioned under the "Output Production" group header.

    Both FY layouts (FY2526: no SAP Code column, FY2627: with SAP Code) are
    handled because all lookups are by header text, not position.

    Returns (main_hdr_idx, data_start_idx, col_map).
      main_hdr_idx == -1 → header not found.
      data_start_idx     → first true data row (main+1 or main+2).
    col_map keys: "rejection_kg", "weight_of_total_prod", "wt_in_kgs",
                  "item", "machine", "pc".
    """
    # Prefix-based matching — handles "(in Kgs)", "(IN KGS)", etc.
    _MAIN = [
        ("ACTUAL REJECTION WEIGHT", "rejection_kg"),
        ("WEIGHT OF TOTAL PRODUCTION", "weight_of_total_prod"),
        ("ITEM NAME",  "item"),
        ("ITEM",       "item"),
        ("MACHINE",    "machine"),
        ("M/C",        "machine"),
        ("PCS",        "pc"),
        ("PC",         "pc"),
        ("PIECES",     "pc"),
        ("WT IN KGS",  "wt_in_kgs"),   # sometimes on main row (FY2526)
    ]

    for ri, row in enumerate(values[:12]):
        col_map: dict[str, int] = {}
        for ci, cell in enumerate(row):
            key = _norm_hdr(cell)
            for prefix, field in _MAIN:
                if key.startswith(prefix) and field not in col_map:
                    col_map[field] = ci
                    break

        # Need at least the rejection column to consider this the main header
        if "rejection_kg" not in col_map:
            continue

        # Check the next row for the "Wt in Kgs" sub-header
        wik_in_sub = False
        if "wt_in_kgs" not in col_map:
            sub_row = values[ri + 1] if ri + 1 < len(values) else []
            for ci, cell in enumerate(sub_row):
                if _norm_hdr(cell).startswith("WT IN KGS"):
                    col_map["wt_in_kgs"] = ci
                    wik_in_sub = True
                    break

        data_start = ri + 2 if wik_in_sub else ri + 1
        return ri, data_start, col_map

    return -1, -1, {}


def parse_r12_fittings_kg(
    values: list,
    *,
    row_variance_pct: float = _R12_ROW_VARIANCE_PCT,
) -> dict:
    """Parse Report-12 tab for total fittings weight (for costing).

    AUTHORITATIVE FIGURE:
      total_fitting_kg = "Wt in Kgs" + "Actual Rejection Weight (in Kgs)"
      (gross actual = good output + rejected weight, same convention as pipe)

    DATA-QUALITY VARIANCE:
      variance_pct = |Wt-in-Kgs − Weight-of-Total-Production| / W-T-P × 100
      "Weight of Total Production" is formula-driven (pcs × std weight).
      When variance > R12_TOTAL_VARIANCE_WARN_PCT the UI shows a warning
      so bad hand-entries (e.g. wrong Wt-in-Kgs) can be corrected at source.

    Handles both FY layouts (FY2526 no SAP Code; FY2627 with SAP Code) and
    the two-row header (main header + "Wt in Kgs" sub-header row).

    ``row_variance_pct`` (default 5%): flag a data row in ``divergent_rows``
    when its per-row |Wt-in-Kgs − W-T-P| / W-T-P exceeds this threshold.

    Returns::

        {
            "total_fitting_kg":   float,  # authoritative costing figure (gross)
            "wt_in_kgs":          float,  # good-output sum (Wt in Kgs)
            "rejection_kg":       float,  # rejected-weight sum
            "weight_of_total_prod": float, # formula-driven sum (variance ref)
            "variance_pct":       float|None,
            "divergent_rows":     list[dict],
            "n_rows":             int,
        }
    """
    _EMPTY = {
        "total_fitting_kg": 0.0, "wt_in_kgs": 0.0, "rejection_kg": 0.0,
        "weight_of_total_prod": 0.0, "variance_pct": None,
        "divergent_rows": [], "n_rows": 0,
    }
    if not values:
        return _EMPTY

    main_idx, data_start, col_map = _r12_find_header(values)
    if main_idx < 0 or "rejection_kg" not in col_map:
        logger.warning("parse_r12_fittings_kg: required headers not found "
                       "(need 'Actual Rejection Weight (in Kgs)')")
        return _EMPTY

    rej_col  = col_map["rejection_kg"]
    wtp_col  = col_map.get("weight_of_total_prod", -1)
    wik_col  = col_map.get("wt_in_kgs", -1)
    item_col = col_map.get("item", -1)
    mc_col   = col_map.get("machine", -1)
    pc_col   = col_map.get("pc", -1)

    total_wik = 0.0
    total_rej = 0.0
    total_wtp = 0.0
    divergent: list = []
    n_rows = 0
    row_thr = row_variance_pct / 100.0

    for row in values[data_start:]:
        if not row or len(row) <= rej_col:
            continue

        # Skip TOTAL / sub-header rows: rejection cell must be numeric
        rej = _num(row[rej_col])
        if rej is None:
            continue

        # Skip TOTAL indicator rows (e.g. row labelled "TOTAL", "GRAND TOTAL")
        first = _norm_hdr(row[0]) if row else ""
        if any(t in first for t in ("TOTAL", "GRAND", "SUM", "SR NO", "S.NO")):
            continue

        n_rows += 1
        total_rej += rej

        wik = _num(row[wik_col] if wik_col >= 0 and wik_col < len(row) else None) or 0.0
        total_wik += wik

        wtp = _num(row[wtp_col] if wtp_col >= 0 and wtp_col < len(row) else None) or 0.0
        total_wtp += wtp

        # Flag per-row divergence: Wt-in-Kgs vs formula (data quality)
        if wtp > 0 and wik_col >= 0 and abs(wtp - wik) / wtp > row_thr:
            item = str(row[item_col]).strip() if item_col >= 0 and item_col < len(row) else "?"
            mc   = str(row[mc_col]).strip()   if mc_col   >= 0 and mc_col   < len(row) else "?"
            pc   = _num(row[pc_col] if pc_col >= 0 and pc_col < len(row) else None)
            divergent.append({
                "item": item, "machine": mc, "pcs": pc,
                "w_tot": round(wtp, 2), "w_kgs": round(wik, 2),
                "diff_pct": round(abs(wtp - wik) / wtp * 100, 1),
            })

    # Variance: Wt-in-Kgs vs Weight-of-Total-Prod (data quality signal)
    variance_pct = None
    if total_wtp > 0 and wik_col >= 0:
        variance_pct = round(abs(total_wtp - total_wik) / total_wtp * 100, 2)

    return {
        "total_fitting_kg":     round(total_wik + total_rej, 2),
        "wt_in_kgs":            round(total_wik, 2),
        "rejection_kg":         round(total_rej, 2),
        "weight_of_total_prod": round(total_wtp, 2),
        "variance_pct":         variance_pct,
        "divergent_rows":       divergent,
        "n_rows":               n_rows,
    }


def check_rejection_prod_tab_units(
    tab_values: list,
    r12_fitting_kg: float,
    *,
    mismatch_ratio: float = _REJ_PROD_TAB_MISMATCH_RATIO,
) -> dict:
    """Guard: detect if 'REJECTION & PRODUCTION' tab fittings column holds pieces.

    The "REJECTION & PRODUCTION" tab (in the same annual labour workbook) has a
    Fittings block with columns headed 'Production (Kgs)' and 'Rejection (Kgs)'.
    In FY2026-27 those cells still contain GROSS PIECES, not kg — the tab was
    not corrected when the Plumbing tab was fixed.

    DO NOT USE this tab as a costing source for fittings production.  This
    guard can be used defensively: if the sum of the 'Production (Kgs)' column
    exceeds ``mismatch_ratio × r12_fitting_kg`` (default 10×), the column
    almost certainly contains pieces, not kg.

    Parameters
    ----------
    tab_values:       raw cell grid from batch_get for this tab.
    r12_fitting_kg:   the trusted Report-12 fittings kg figure for the same month.
    mismatch_ratio:   multiplier threshold (default 10).

    Returns
    -------
    dict with keys:
      "is_unit_mismatch": bool
      "tab_sum":          float  (sum of 'Production (Kgs)' column values)
      "r12_kg":           float  (the trusted R12 figure passed in)
      "ratio":            float  (tab_sum / r12_kg, or 0 if r12_kg == 0)
    """
    tab_sum = 0.0
    # Two-pass scan to find the "Production (Kgs)" column in the Fittings block.
    # Pass 1: find the column where the "Fittings" group header appears.
    # Pass 2: find "Production" at or after that column (to skip the Pipe section).
    fitting_group_col = -1
    for row in tab_values[:15]:
        for ci, cell in enumerate(row):
            if "FITTING" in _norm_hdr(cell):
                fitting_group_col = ci
                break
        if fitting_group_col >= 0:
            break

    prod_col = -1
    hdr_row_idx = -1
    for ri, row in enumerate(tab_values[:20]):
        for ci, cell in enumerate(row):
            if fitting_group_col >= 0 and ci < fitting_group_col:
                continue   # skip Pipe / other columns that precede the Fittings block
            key = _norm_hdr(cell)
            if "PRODUCTION" in key and prod_col < 0:
                prod_col = ci
                hdr_row_idx = ri
        if prod_col >= 0:
            break

    if prod_col >= 0:
        for row in tab_values[hdr_row_idx + 1:]:
            if row and prod_col < len(row):
                v = _num(row[prod_col])
                if v is not None:
                    tab_sum += v

    ratio = (tab_sum / r12_fitting_kg) if r12_fitting_kg > 0 else 0.0
    return {
        "is_unit_mismatch": ratio > mismatch_ratio,
        "tab_sum": round(tab_sum, 2),
        "r12_kg":  round(r12_fitting_kg, 2),
        "ratio":   round(ratio, 2),
    }


def load_r12_for_fy(
    fy: str,
    month_labels: list,
    token: str,
) -> dict:
    """Read Report-12 from each month's PIPE workbook and parse fittings kg.

    Returns a dict keyed by month_label (e.g. "APR") with the result of
    parse_r12_fittings_kg() for that month, plus "source_ym" and "ok".

    Months whose PIPE workbook is not registered in DAILY_SOURCES get an empty
    result with ok=False so the caller can fall back gracefully.
    """
    import sheets as _sh          # lazy — avoid circular at module level
    import sources as _src        # lazy — avoid pulling real store in tests

    pipe_files = _src.DAILY_SOURCES.get("PIPE", {}).get("files", {})
    results: dict = {}

    for month_label in month_labels:
        ym = _fy_month_to_ym(fy, month_label)
        fid = pipe_files.get(ym)

        if not fid:
            results[month_label] = {
                "weight_of_total_prod": 0.0, "wt_in_kgs": 0.0,
                "variance_pct": None, "divergent_rows": [], "n_rows": 0,
                "source_ym": ym, "ok": False, "error": "no workbook registered",
            }
            logger.debug("load_r12_for_fy: no PIPE workbook for %s (%s)", ym, month_label)
            continue

        try:
            matrices = _sh.batch_get(fid, ["Report-12"], token)
            r12_vals = matrices.get("Report-12", [])
            parsed   = parse_r12_fittings_kg(r12_vals)
            parsed.update({"source_ym": ym, "ok": parsed["n_rows"] > 0})
            if not parsed["ok"]:
                parsed["error"] = "Report-12 tab empty or unparseable"
            results[month_label] = parsed
        except Exception as exc:
            logger.warning("load_r12_for_fy: %s %s failed: %s", month_label, ym, exc)
            results[month_label] = {
                "weight_of_total_prod": 0.0, "wt_in_kgs": 0.0,
                "variance_pct": None, "divergent_rows": [], "n_rows": 0,
                "source_ym": ym, "ok": False, "error": str(exc),
            }

    return results


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
    total_pipe = sum(float(r.get("pipe_prod_kg") or 0) for r in monthly_rows)
    total_fit  = sum(float(r.get("fitting_prod_kg") or 0) for r in monthly_rows)
    total_kg   = total_pipe + total_fit
    total_wages = sum(
        float(r.get("paid_wages") or 0) + float(r.get("contractor_wages") or 0)
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

    # Read both tabs in one batch call (labour sheet: wages + hours + pipe kg)
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

    # ── Source fittings kg from Report-12 (NOT the labour sheet) ─────────────
    # The labour sheet "Fittings Production" column is MISLABELLED in FY2026-27
    # (contains gross PIECES, not kg).  Report-12 "Weight of Total Production"
    # is formula-driven and authoritative for all FYs.
    month_labels = [r["month_label"] for r in monthly_rows]

    # ── Auto sources: employee data (hours/headcount) + KH-1 wages files ─────
    #
    # Hours/headcount: Employee Data Details (COST) tabs D-1/D-2/D-3.
    #   Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN, resolved by label.
    #   Month columns run in REVERSE (latest first) — mapped by header text.
    #
    # Wages: monthly KH-1 wages files, CPVC filter.
    #   TOTAL PAYABLE column found by header text (shifts between files).
    #
    # The segment labour-cost sheet's Plumbing tab is kept for:
    #   (a) pipe_prod_kg (still read from there)
    #   (b) cross-check reference (±0.5% divergence → reconciliation warning)
    #
    emp_file_id  = costing_model.EMPLOYEE_DATA_SOURCES.get(fy)
    emp_data: dict = {
        "paid_hours": {}, "actual_hours": {}, "headcount": {},
        "warnings": [], "ok": False, "source_file_id": None,
    }
    if emp_file_id:
        try:
            import costing_employee as _emp
            emp_data = _emp.load_employee_data(emp_file_id, fy, token)
            errors.extend(emp_data.get("warnings", []))
        except Exception as exc:
            errors.append(f"Employee data load failed: {exc}")
            logger.warning("load_labour_fy: employee data failed: %s", exc)

    wages_src       = costing_model.WAGES_SOURCES.get(segment, {}).get(fy, {})
    wages_by_month: dict = {}
    wages_file_count     = 0
    if wages_src:
        try:
            import costing_wages as _wag
            wages_by_month   = _wag.load_wages_fy(fy, segment, month_labels, token, wages_src)
            wages_file_count = sum(1 for r in wages_by_month.values() if r.get("ok"))
            for wr in wages_by_month.values():
                errors.extend(wr.get("warnings", []))
        except Exception as exc:
            errors.append(f"Wages auto-load failed: {exc}")
            logger.warning("load_labour_fy: wages auto-load failed: %s", exc)

    # Apply auto sources to each monthly row.  Cross-check against segment
    # sheet and surface reconciliation warnings; then overwrite with auto values.
    for row in monthly_rows:
        ml = row["month_label"]

        auto_ph = (emp_data.get("paid_hours")   or {}).get(ml)
        auto_ah = (emp_data.get("actual_hours") or {}).get(ml)
        auto_hc = (emp_data.get("headcount")    or {}).get(ml)
        winfo   = wages_by_month.get(ml, {})
        auto_wg = winfo.get("wages") if winfo.get("ok") else None

        # Store raw auto values for DB audit trail
        row["auto_paid_hours"]   = auto_ph
        row["auto_actual_hours"] = auto_ah
        row["auto_headcount"]    = auto_hc
        row["auto_wages"]        = auto_wg

        # Hours cross-check
        seg_ph          = row.get("paid_hours")
        hours_recon_pct = None
        if auto_ph is not None and seg_ph and seg_ph > 0:
            hours_recon_pct = round(abs(auto_ph - seg_ph) / seg_ph * 100, 3)
            if hours_recon_pct > 0.5:
                errors.append(
                    f"RECON WARNING {ml}: paid hours — auto={auto_ph:,.0f} "
                    f"vs segment sheet={seg_ph:,.0f} ({hours_recon_pct:.2f}%)"
                )
        row["hours_recon_pct"] = hours_recon_pct

        # Wages cross-check
        seg_wg          = row.get("paid_wages")
        wages_recon_pct = None
        if auto_wg is not None and seg_wg and seg_wg > 0:
            wages_recon_pct = round(abs(auto_wg - seg_wg) / seg_wg * 100, 3)
            if wages_recon_pct > 0.5:
                errors.append(
                    f"RECON WARNING {ml}: wages — auto={auto_wg:,.0f} "
                    f"vs segment sheet={seg_wg:,.0f} ({wages_recon_pct:.2f}%)"
                )
        row["wages_recon_pct"] = wages_recon_pct

        # Overwrite segment sheet hours/wages with auto-sourced values
        if auto_ph is not None:
            row["paid_hours"]   = auto_ph
            row["hours_source"] = f"employee_data:{emp_file_id}"
        else:
            row["hours_source"] = "labour_sheet"
        if auto_ah is not None:
            row["actual_hours"] = auto_ah
        if auto_hc is not None:
            row["no_of_labour"] = auto_hc
        if auto_wg is not None:
            row["paid_wages"]   = auto_wg
            row["wages_source"] = f"wages_file:{winfo.get('source_file_id', '')}"
        else:
            row["wages_source"] = "labour_sheet"

        # Recompute per-hour costs now that hours and wages may have changed
        _total_wg = float(row.get("paid_wages") or 0) + float(row.get("contractor_wages") or 0)
        _ph = float(row.get("paid_hours") or 0)
        _ah = float(row.get("actual_hours") or 0)
        if _ph > 0:
            row["per_hour_cost_paid"]   = round(_total_wg / _ph, 4)
        if _ah > 0:
            row["per_hour_cost_actual"] = round(_total_wg / _ah, 4)

    r12_by_month = {}
    r12_errors: list[str] = []
    try:
        r12_by_month = load_r12_for_fy(fy, month_labels, token)
    except Exception as exc:
        r12_errors.append(f"R12 load failed: {exc}")
        logger.warning("load_labour_fy: R12 load exception: %s", exc)

    r12_months_ok = 0
    for row in monthly_rows:
        ml = row["month_label"]
        r12 = r12_by_month.get(ml, {})
        # Authoritative = Wt in Kgs + Actual Rejection Weight (gross actual)
        r12_kg = float(r12.get("total_fitting_kg") or 0)

        if r12_kg > 0:
            # Authoritative R12 value available — use it
            row["fitting_r12_kg"]        = r12_kg
            row["wt_in_kgs_total"]       = float(r12.get("wt_in_kgs") or 0)
            row["r12_rejection_kg"]      = float(r12.get("rejection_kg") or 0)
            row["fitting_kg_source"]     = "report12"
            row["fitting_variance_pct"]  = r12.get("variance_pct")
            row["fitting_divergent_n"]   = len(r12.get("divergent_rows") or [])
            row["fitting_divergent_rows"] = r12.get("divergent_rows") or []
            # Overwrite the (potentially wrong) labour-sheet figure
            row["fitting_prod_kg"]       = r12_kg
            r12_months_ok += 1
        else:
            # No R12 data — keep labour-sheet figure with a source flag
            row["fitting_r12_kg"]        = None
            row["wt_in_kgs_total"]       = None
            row["r12_rejection_kg"]      = None
            row["fitting_kg_source"]     = "labour_sheet"
            row["fitting_variance_pct"]  = None
            row["fitting_divergent_n"]   = 0
            row["fitting_divergent_rows"] = []
            if r12.get("error"):
                r12_errors.append(f"{ml}: {r12['error']}")

        # Recompute total_prod_kg and per_kg_labour_cost from corrected figures
        pipe_kg    = float(row.get("pipe_prod_kg") or 0)
        fit_kg     = float(row.get("fitting_prod_kg") or 0)
        total_wages = float(row.get("paid_wages") or 0) + float(row.get("contractor_wages") or 0)
        row["total_prod_kg"]       = round(pipe_kg + fit_kg, 2) if (pipe_kg + fit_kg) > 0 else None
        row["per_kg_labour_cost"]  = (
            round(total_wages / row["total_prod_kg"], 4)
            if row.get("total_prod_kg") and row["total_prod_kg"] > 0 else None
        )

    if r12_errors:
        errors.extend(r12_errors)
    if r12_months_ok < len(monthly_rows):
        errors.append(
            f"R12 data unavailable for {len(monthly_rows) - r12_months_ok} month(s)"
            f" — labour-sheet fitting_prod_kg used as fallback."
        )

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
            emp_data_file_id=emp_file_id or "",
            wages_file_count=wages_file_count,
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
