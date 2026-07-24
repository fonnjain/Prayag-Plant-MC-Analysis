"""costing_employee.py — Parse "EMPLOYEE DATA DETAILS (COST)" workbooks.

Source: "<FY> EMPLOYEE DATA DETAILS (COST)"
Tabs and meaning (verified against Prayag's process doc):
  D-1 = "Actual Paid Hours"
  D-2 = "Actual Working Hours"
  D-3 = "Actual Number Of Persons"   (headcount, NOT wages)
  D-4 / D-5 are derived (hours per person / per day) — EMPTY on Plumbing row, computed here.

LAYOUT OF EACH D-TAB
---------------------
Within each D-tab there is a block whose column B is "PIPE & FITTING":

  Col A (idx 0): SR NO or blank
  Col B (idx 1): Segment label ("PIPE & FITTING" for the first row of the block;
                 blank for all subsequent sub-department rows in the block)
  Col C (idx 2): Sub-department label
                 (ADMIN, BALL VALVE, CIVIL, DISPATCH, ERP, GARDEN PIPE, HDPE PIPE,
                  HOUSE KEEPING, LOGISTIC, MAINTENANCE, MIXER, MOULDING, PIPELINE,
                  PURCHASE, QUALITY, SECURITY, STORE, TOOLROOM)
                 Then a TOTAL row (col C == "TOTAL").
                 Then an UNLABELLED row (col C blank) = Plumbing figure.
  Col D+ (idx 3+): Monthly data in REVERSE order (latest month in the leftmost data
                   column, earliest in the rightmost).

PLUMBING DERIVATION
-------------------
Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN

This is verified from the stored (unlabelled) row, which Prayag's sheet formula
computes the same way.  Any divergence between the computed and stored values is
surfaced as a reconciliation warning (does NOT abort — it is a layout-change
indicator for the operations team).

MONTH COLUMN MAPPING
--------------------
Month headers look like "MAR'26", "APR'25", etc.  The first three letters give
the month abbreviation.  The mapping is constructed from the header text, never
from column position, so any future column insertions before the month block are
handled automatically.

ACCEPTANCE (FY2025-26 Plumbing, sum across 12 month columns):
  D-1 paid hours   = 431,468
  D-2 actual hours = 362,225
  D-3 headcount    = 1,438
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Month metadata ─────────────────────────────────────────────────────────────

MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]
_MONTH_SET = frozenset(MONTH_LABELS)
_MONTH_RE  = re.compile(r"^(APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|JAN|FEB|MAR)", re.I)

# Sub-departments subtracted from PIPE & FITTING TOTAL to get Plumbing
_PLUMBING_SUBTRACT = frozenset({"GARDEN PIPE", "HDPE PIPE", "ADMIN"})

# Tab-name → semantic field for the result dict
_D_TAB_FIELD: dict[str, str] = {
    "D-1": "paid_hours",
    "D-2": "actual_hours",
    "D-3": "headcount",
}

# ── Utilities ──────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    if v is None or str(v).strip() in ("", "-", "—", "N/A"):
        return None
    s = re.sub(r"[,₹\s]", "", str(v).strip())
    try:
        return float(s)
    except ValueError:
        return None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s).strip().upper())


def _cell(row: list, idx: int):
    return row[idx] if idx < len(row) else None


# ── Month-column header parser ─────────────────────────────────────────────────

def _parse_month_col_map(values: list, max_rows: int = 20) -> tuple:
    """Scan the first ``max_rows`` rows for a header row that contains month
    abbreviations (e.g. "MAR'26", "APR'25") in columns 3+.

    Returns (header_row_idx, {col_idx: month_label}).
    Returns (-1, {}) if no suitable header row is found.
    """
    for ri, row in enumerate(values[:max_rows]):
        col_map: dict[int, str] = {}
        for ci, cell in enumerate(row):
            m = _MONTH_RE.match(str(cell).strip())
            if m:
                lbl = m.group(1).upper()
                if lbl in _MONTH_SET:
                    col_map[ci] = lbl
        if len(col_map) >= 1:   # at least one validated month abbreviation
            return ri, col_map
    return -1, {}


# ── PIPE & FITTING block finder ────────────────────────────────────────────────

def _find_pnf_block(values: list, hdr_idx: int, col_map: dict) -> dict:
    """Extract the PIPE & FITTING block rows from a D-tab.

    Returns:
    {
        "dept_rows":      {dept_label: {month_label: float}},
        "total_row":      {month_label: float},
        "unlabelled_row": {month_label: float},   # stored Plumbing row
        "block_start":    int,   # row index where block begins; -1 if not found
    }
    """
    block_start  = -1
    in_block     = False
    dept_rows:       dict[str, dict[str, float]] = {}
    total_row:       dict[str, float] = {}
    unlabelled_row:  dict[str, float] = {}
    saw_total        = False

    for ri, row in enumerate(values):
        if ri <= hdr_idx:
            continue

        seg_cell  = _norm(_cell(row, 1) or "")
        dept_cell = _norm(_cell(row, 2) or "")

        # Detect block start — if still hunting for the block, either begin or skip
        if not in_block:
            if "PIPE" in seg_cell and "FITTING" in seg_cell:
                in_block    = True
                block_start = ri
                # Fall through: process this first row (usually ADMIN sub-dept)
            else:
                continue

        # Block end: new non-empty segment label that is NOT Pipe & Fitting
        if ri > block_start and seg_cell and not ("PIPE" in seg_cell and "FITTING" in seg_cell):
            break

        # Extract month values for this row
        month_vals: dict[str, float] = {}
        for ci, lbl in col_map.items():
            v = _num(_cell(row, ci))
            if v is not None:
                month_vals[lbl] = v

        if dept_cell == "TOTAL":
            total_row = month_vals
            saw_total = True
        elif saw_total and not dept_cell and not seg_cell:
            # First blank-label row after TOTAL = Plumbing
            unlabelled_row = month_vals
            break
        elif dept_cell:
            dept_rows[dept_cell] = month_vals

    return {
        "dept_rows":      dept_rows,
        "total_row":      total_row,
        "unlabelled_row": unlabelled_row,
        "block_start":    block_start,
    }


# ── Main parser ────────────────────────────────────────────────────────────────

def parse_employee_d_tab(values: list, tab_label: str = "D-?") -> dict:
    """Parse a D-tab from the Employee Data Details workbook.

    Computes the Plumbing figure per month as:
        Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN

    Cross-checks the computed value against the stored unlabelled row.
    Warns if they differ by more than 0.5 (rounding tolerance).

    Returns::

        {
            "by_month":      {month_label: float},   # Plumbing per month
            "total_by_month":{month_label: float},   # PIPE & FITTING TOTAL per month
            "subtracted":    {dept_label: {month_label: float}},
            "unlabelled_row":{month_label: float},   # stored Plumbing (for audit)
            "recon_warnings":[str],   # computed ≠ stored (layout-change indicator)
            "warnings":      [str],
            "ok":            bool,
        }
    """
    _empty = {
        "by_month": {}, "total_by_month": {}, "subtracted": {},
        "unlabelled_row": {}, "recon_warnings": [], "warnings": [], "ok": False,
    }
    if not values:
        return _empty

    hdr_idx, col_map = _parse_month_col_map(values)
    if hdr_idx < 0 or not col_map:
        w = f"{tab_label}: could not find month-column header row"
        logger.warning(w)
        return {**_empty, "warnings": [w]}

    block = _find_pnf_block(values, hdr_idx, col_map)
    if block["block_start"] < 0:
        w = f"{tab_label}: PIPE & FITTING block not found"
        logger.warning(w)
        return {**_empty, "warnings": [w]}

    dept_rows  = block["dept_rows"]
    total_row  = block["total_row"]
    unlabelled = block["unlabelled_row"]

    if not total_row:
        w = f"{tab_label}: TOTAL row not found in PIPE & FITTING block"
        logger.warning(w)
        return {**_empty, "warnings": [w]}

    # Collect the three subtracted departments
    subtracted: dict[str, dict] = {
        k: dept_rows[k]
        for k in _PLUMBING_SUBTRACT
        if k in dept_rows
    }

    # Compute Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN
    by_month: dict[str, float] = {}
    recon_warnings: list[str] = []

    all_months = set(total_row.keys()) | set(unlabelled.keys())
    for lbl in all_months:
        total_val    = float(total_row.get(lbl) or 0.0)
        subtract_sum = sum(float(d.get(lbl, 0.0)) for d in subtracted.values())
        computed     = total_val - subtract_sum
        by_month[lbl] = computed

        # Cross-check computed vs the stored unlabelled row
        stored = unlabelled.get(lbl)
        if stored is not None and abs(computed - stored) > 0.5:
            recon_warnings.append(
                f"{tab_label} {lbl}: computed Plumbing {computed:.1f} "
                f"≠ stored {stored:.1f} (diff {computed - stored:+.1f}); "
                f"block layout may have changed"
            )

    all_warnings = recon_warnings[:]
    if recon_warnings:
        logger.warning(
            "%s: Plumbing recon mismatch in %d month(s) — "
            "check PIPE & FITTING block layout",
            tab_label, len(recon_warnings),
        )

    return {
        "by_month":       by_month,
        "total_by_month": total_row,
        "subtracted":     subtracted,
        "unlabelled_row": unlabelled,
        "recon_warnings": recon_warnings,
        "warnings":       all_warnings,
        "ok":             bool(by_month),
    }


# ── FY loader ─────────────────────────────────────────────────────────────────

def load_employee_data(file_id: str, fy: str, token: str) -> dict:
    """Read D-1, D-2, D-3 tabs from the Employee Data Details workbook.

    Returns::

        {
            "paid_hours":     {month_label: float},    # D-1
            "actual_hours":   {month_label: float},    # D-2
            "headcount":      {month_label: float},    # D-3
            "tab_names":      {"paid_hours":"D-1", "actual_hours":"D-2", "headcount":"D-3"},
            "source_file_id": str,
            "warnings":       [str],
            "ok":             bool,   # True if at least one tab parsed OK
        }
    """
    import sheets as _sh   # lazy — avoids circular at module level

    result: dict = {
        "paid_hours":   {},
        "actual_hours": {},
        "headcount":    {},
        "tab_names":    {
            "paid_hours":   "D-1",
            "actual_hours": "D-2",
            "headcount":    "D-3",
        },
        "source_file_id": file_id,
        "warnings":       [],
        "ok":             False,
    }

    tabs = ["D-1", "D-2", "D-3"]
    try:
        matrices = _sh.batch_get(file_id, tabs, token)
    except Exception as exc:
        result["warnings"].append(f"Employee data batch_get failed: {exc}")
        logger.warning("load_employee_data: batch_get failed: %s", exc)
        return result

    field_map = [
        ("D-1", "paid_hours"),
        ("D-2", "actual_hours"),
        ("D-3", "headcount"),
    ]
    ok_count = 0
    for tab_name, field in field_map:
        vals   = matrices.get(tab_name, [])
        parsed = parse_employee_d_tab(vals, tab_label=tab_name)
        result[field] = parsed.get("by_month", {})
        result["warnings"].extend(parsed.get("warnings", []))
        if parsed["ok"]:
            ok_count += 1

    result["ok"] = ok_count > 0
    return result
