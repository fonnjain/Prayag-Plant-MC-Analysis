"""costing_wages.py — Parse monthly wages files for Plumbing Labour Costing.

Source: Monthly "<N>. Wages <Mon>-<Year>" files.
Tab: KH-1 (Khandala, Plumbing segment).
Filter: DEPARTMENT (col G, found by header) == "CPVC".
        "CPVC" is the SEGMENT label for Plumbing — not the material.
        ADMIN and TANK rows that appear in KH-1 are excluded.
        Garden Pipe and HDPE live in KH-2 and are never included here.
        BH is a different unit (CP / PTMT / SINK / HINGES) — ignored for Plumbing.
Sum: "TOTAL PAYABLE" column — MUST be located by header text.

CRITICAL — TOTAL PAYABLE COLUMN MOVES BETWEEN FILES
----------------------------------------------------
The TOTAL PAYABLE column is at AM in Apr-2025, AN in Mar-2026, AO in KH-2,
AR in BH — because columns are inserted during the year.  Any hard-coded column
letter will silently pick up the adjacent BANK ACCOUNT NUMBER column, which is
numeric and produces a large, plausible-looking but completely wrong total.
This module locates the column by header text and asserts that it actually
contains "TOTAL PAYABLE" before summing.

SANITY CHECK
------------
If a trailing average is provided, each monthly sum is checked against it.
Out-of-range (< 0.5× or > 2× trailing average) results are flagged with a
warning and marked is_sanity_fail=True.  The value is NOT silently suppressed
— the caller decides whether to store or discard it.

LABEL NORMALISATION (Part D — for future PTMT / BH multi-unit aggregation)
---------------------------------------------------------------------------
normalise_dept(s) trims, uppercases, and maps known typo variants:
  "HINGS" → "HINGES"  (observed in Mar-2026 BH)
  "HINGE" → "HINGES"
Stray "PURCHASE" that appears in BH is kept as-is (it will just not match CPVC).
Plumbing itself only needs this for the CPVC equality check, but normalisation
is applied universally so any future multi-unit aggregation is consistent.

ACCEPTANCE (FY2025-26):
  Apr-2025 KH-1 CPVC TOTAL PAYABLE = 1,904,701
  Mar-2026 KH-1 CPVC TOTAL PAYABLE = 1,529,429
  FY2025-26 full-year total         = 21,452,790
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Department-name normalisation ─────────────────────────────────────────────

# Known variant spellings → canonical form.  Applies to all tabs / units so
# PTMT / BH aggregation is consistent when those segments are added.
DEPT_ALIASES: dict[str, str] = {
    "HINGS":   "HINGES",
    "HINGE":   "HINGES",
    # Add further variants as discovered (keep alphabetically sorted)
}

# ── KH-1 structural constants ─────────────────────────────────────────────────

# Row 7 in the sheet = index 6 (0-based)
_KH1_HEADER_ROW = 6
# Data starts at row 8 = index 7
_KH1_DATA_START = 7

# Sanity range defaults (relative to trailing average)
_SANITY_LOW  = 0.5
_SANITY_HIGH = 2.0

# ── Month index (duplicate of costing_employee; no cross-import to avoid circular) ──

_MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]
_MONTH_IDX = {lbl: i for i, lbl in enumerate(_MONTH_LABELS)}

# ── Utilities ──────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    if v is None or str(v).strip() in ("", "-", "—", "N/A"):
        return None
    s = re.sub(r"[,₹\s]", "", str(v).strip())
    try:
        return float(s)
    except ValueError:
        return None


def _norm_hdr(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().upper())


def normalise_dept(s: str) -> str:
    """Trim, uppercase, and map known department-name variants to canonical form.

    Used when filtering or aggregating across files that may have inconsistent
    spelling (e.g. "HINGS" vs "HINGES" in BH wages).  The equality test for
    the CPVC filter goes through this function so a typo does not silently
    exclude a row or create a phantom department.
    """
    canonical = re.sub(r"\s+", " ", str(s).strip().upper())
    return DEPT_ALIASES.get(canonical, canonical)


def _fy_month_to_ym(fy: str, month_label: str) -> str:
    """Convert a 4-char FY code + month label to a YYYY-MM key.

    FY "2526" starts April 2025: APR=2025-04 … MAR=2026-03.
    FY "2627" starts April 2026: APR=2026-04 … MAR=2027-03.
    """
    start_year = 2000 + int(fy[:2])
    idx = _MONTH_IDX.get(month_label.upper(), 0)
    if idx <= 8:   # APR(0) … DEC(8)
        return f"{start_year}-{idx + 4:02d}"
    else:          # JAN(9) … MAR(11)
        return f"{start_year + 1}-{idx - 8:02d}"


# ── Column finders ─────────────────────────────────────────────────────────────

def _find_total_payable_col(hdr_row: list) -> int:
    """Return the 0-based column index of the 'TOTAL PAYABLE' column.

    NEVER hardcode the column letter — it shifts between monthly files as
    new columns are inserted during the year.  Returns -1 if not found.
    """
    for ci, cell in enumerate(hdr_row):
        if "TOTAL PAYABLE" in _norm_hdr(cell):
            return ci
    return -1


def _find_dept_col(hdr_row: list) -> int:
    """Return the 0-based column index of the DEPARTMENT column."""
    for ci, cell in enumerate(hdr_row):
        h = _norm_hdr(cell)
        if h in ("DEPARTMENT", "DEPT", "DEPARTMENT NAME", "DEPARTMENT /SECTION"):
            return ci
    return -1


# ── KH-1 parser ───────────────────────────────────────────────────────────────

def parse_kh1_wages(
    values: list,
    *,
    sanity_low_ratio:  float = _SANITY_LOW,
    sanity_high_ratio: float = _SANITY_HIGH,
    trailing_avg:      Optional[float] = None,
) -> dict:
    """Parse KH-1 tab: sum TOTAL PAYABLE for CPVC department rows.

    Returns::

        {
            "total_wages":             float,
            "n_rows":                  int,    # CPVC rows found and summed
            "total_payable_col":       int,    # 0-based column index
            "total_payable_col_label": str,    # exact header text at that column
            "dept_col":                int,    # 0-based DEPARTMENT column
            "is_sanity_fail":          bool,
            "sanity_ratio":            float|None,
            "warnings":                [str],
            "ok":                      bool,
        }
    """
    _empty: dict = {
        "total_wages": 0.0, "n_rows": 0,
        "total_payable_col": -1, "total_payable_col_label": "",
        "dept_col": -1, "is_sanity_fail": False,
        "sanity_ratio": None, "warnings": [], "ok": False,
    }

    if not values or len(values) <= _KH1_HEADER_ROW:
        return {**_empty, "warnings": ["KH-1: fewer rows than expected (need ≥7)"]}

    hdr_row  = values[_KH1_HEADER_ROW]
    tp_col   = _find_total_payable_col(hdr_row)
    dept_col = _find_dept_col(hdr_row)

    warnings: list[str] = []

    if tp_col < 0:
        w = "KH-1: 'TOTAL PAYABLE' column not found in header row — column may have moved"
        logger.warning(w)
        return {**_empty, "warnings": [w]}

    if dept_col < 0:
        w = "KH-1: 'DEPARTMENT' column not found in header row"
        logger.warning(w)
        return {**_empty, "warnings": [w]}

    tp_label = _norm_hdr(hdr_row[tp_col])

    # Paranoia assertion: the located header must actually contain "TOTAL PAYABLE"
    if "TOTAL PAYABLE" not in tp_label:
        w = (
            f"KH-1: located column {tp_col} has header '{tp_label}' "
            f"which does not contain 'TOTAL PAYABLE' — aborting to avoid reading "
            f"an adjacent column (e.g. BANK ACCOUNT NUMBER)"
        )
        logger.error(w)
        return {**_empty, "warnings": [w]}

    total_wages = 0.0
    n_rows      = 0

    for row in values[_KH1_DATA_START:]:
        if not row or dept_col >= len(row):
            continue
        dept = normalise_dept(row[dept_col])
        if dept != "CPVC":
            continue
        v = _num(row[tp_col] if tp_col < len(row) else None)
        if v is not None:
            total_wages += v
            n_rows      += 1

    # Sanity range check against trailing average
    is_sanity_fail = False
    sanity_ratio   = None
    if trailing_avg is not None and trailing_avg > 0 and n_rows > 0:
        sanity_ratio = total_wages / trailing_avg
        if sanity_ratio < sanity_low_ratio:
            is_sanity_fail = True
            warnings.append(
                f"KH-1 sanity fail: CPVC total {total_wages:,.0f} is "
                f"{sanity_ratio:.2f}× trailing average {trailing_avg:,.0f} "
                f"(threshold {sanity_low_ratio}×) — verify source file"
            )
        elif sanity_ratio > sanity_high_ratio:
            is_sanity_fail = True
            warnings.append(
                f"KH-1 sanity fail: CPVC total {total_wages:,.0f} is "
                f"{sanity_ratio:.2f}× trailing average {trailing_avg:,.0f} "
                f"(threshold {sanity_high_ratio}×) — verify source file"
            )

    ok = n_rows > 0

    return {
        "total_wages":             round(total_wages, 2),
        "n_rows":                  n_rows,
        "total_payable_col":       tp_col,
        "total_payable_col_label": tp_label,
        "dept_col":                dept_col,
        "is_sanity_fail":          is_sanity_fail,
        "sanity_ratio":            round(sanity_ratio, 4) if sanity_ratio is not None else None,
        "warnings":                warnings,
        "ok":                      ok,
    }


# ── FY wages loader ────────────────────────────────────────────────────────────

def load_wages_fy(
    fy:           str,
    segment:      str,
    month_labels: list,
    token:        str,
    wages_sources: dict,
) -> dict:
    """Load KH-1 wages from monthly files for a given FY.

    Processes months in FY order (APR → MAR) so the trailing average used by
    the sanity check is built from already-validated earlier months.

    Args:
        wages_sources: {YYYY-MM: file_id} mapping for this segment / FY.
                       Months with no entry are returned with ok=False.

    Returns a dict keyed by month_label::

        {
            month_label: {
                "wages":                  float|None,
                "source_file_id":         str|None,
                "source_ym":              str,
                "ok":                     bool,
                "is_sanity_fail":         bool,
                "total_payable_col_label":str,
                "n_cpvc_rows":            int,
                "warnings":               [str],
            }
        }
    """
    import sheets as _sh   # lazy — avoids circular at module level

    # Process months in FY forward order for meaningful trailing average
    ordered   = [m for m in _MONTH_LABELS if m in month_labels]
    remaining = [m for m in month_labels  if m not in ordered]
    ordered   = ordered + remaining

    results:    dict        = {}
    prev_wages: list[float] = []

    for month_label in ordered:
        ym  = _fy_month_to_ym(fy, month_label)
        fid = wages_sources.get(ym)

        if not fid:
            results[month_label] = {
                "wages": None, "source_file_id": None, "source_ym": ym,
                "ok": False, "is_sanity_fail": False,
                "total_payable_col_label": "",
                "n_cpvc_rows": 0,
                "warnings": [f"No wages file registered for {ym} ({month_label})"],
            }
            continue

        trailing_avg = (sum(prev_wages) / len(prev_wages)) if prev_wages else None

        try:
            matrices = _sh.batch_get(fid, ["KH-1"], token)
            kh1_vals = matrices.get("KH-1", [])
            parsed   = parse_kh1_wages(
                kh1_vals,
                trailing_avg=trailing_avg,
            )
            ok = parsed["ok"] and not parsed["is_sanity_fail"]
            results[month_label] = {
                "wages":                  parsed["total_wages"] if parsed["ok"] else None,
                "source_file_id":         fid,
                "source_ym":              ym,
                "ok":                     ok,
                "is_sanity_fail":         parsed["is_sanity_fail"],
                "total_payable_col_label": parsed["total_payable_col_label"],
                "n_cpvc_rows":            parsed["n_rows"],
                "warnings":               parsed["warnings"],
            }
            if ok:
                prev_wages.append(parsed["total_wages"])

        except Exception as exc:
            logger.warning("load_wages_fy: %s %s failed: %s", month_label, ym, exc)
            results[month_label] = {
                "wages": None, "source_file_id": fid, "source_ym": ym,
                "ok": False, "is_sanity_fail": False,
                "total_payable_col_label": "",
                "n_cpvc_rows": 0,
                "warnings": [str(exc)],
            }

    return results
