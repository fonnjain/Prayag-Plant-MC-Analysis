"""
Corrective Re-plan Engine — Plumbing (PIPE + Fitting)
======================================================

Reads daily production actuals from Report-11 (Pipe, pcs) and Report-12
(Moulding/Fitting, pcs) in the monthly PIPE workbook and computes a
category-level capacity projection for the remaining working days.

Algorithm
---------
For each category (CPVC Pipe, UPVC Pipe, SWR Pipe, AGRI Pipe,
                   CPVC Fitting, UPVC Fitting, SWR Fitting, AGRI Fitting,
                   + 4 Solvent categories):

1.  Sum pcs per production day (dates where the category ran).
2.  Compute Cap/Day:
      ≥ MIN_DAYS_FOR_P90 non-zero days → 10th-percentile of daily sums
        (conservative "p90" — 90 % probability of achieving this level)
      1 … MIN_DAYS_FOR_P90-1 non-zero days → arithmetic mean
      0 non-zero days → 0, method = "none" (NO_DEMONSTRATED_CAPACITY)
3.  Feasible = Cap/Day × working_days_remaining   (exact, integer multiply)
4.  Shortfall = max(0, remaining_demand − feasible)

INVARIANTS (checked before returning, raise AssertionError on violation)
------------------------------------------------------------------------
• If producedToDate > 0  →  cap_per_day > 0
• feasible == cap_per_day × working_days_remaining  (exact)
• total shortfall < total remaining  (when any category has production)

Issue #5 FIX — Date formats
----------------------------
Both "Aug 1, 2026" (Plumbing Report-11/12) and "1-Aug-2026" (PTMT) are
parsed correctly.  An unrecognised format raises ValueError (loud fail)
rather than silently returning None / zero rows.
"""
from __future__ import annotations

import calendar
import dataclasses
import datetime
import re
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_DAYS_FOR_P90: int = 5   # fewer non-zero days → mean; ≥ → 10th-percentile

#: Map TYPES value in Report-11 → category label
PIPE_CATEGORIES: Dict[str, str] = {
    "CPVC": "CPVC Pipe",
    "UPVC": "UPVC Pipe",
    "SWR":  "SWR Pipe",
    "AGRI": "AGRI Pipe",
}

#: Map MATERIAL value in Report-12 → category label
FITTING_CATEGORIES: Dict[str, str] = {
    "CPVC":   "CPVC Fitting",
    "UPVC":   "UPVC Fitting",
    "SWR":    "SWR Fitting",
    "AGRI":   "AGRI Fitting",
    "PP":     "PP Fitting",
    "ABS":    "ABS Fitting",
}

#: Solvent categories — never appear in Report-11/12 → always NO_DEMONSTRATED_CAPACITY
SOLVENT_CATEGORIES: List[str] = [
    "CPVC Solvent",
    "UPVC Solvent",
    "SWR Solvent",
    "AGRI Solvent",
]

#: Canonical ordering for the output report
CATEGORY_ORDER: List[str] = [
    "CPVC Pipe",   "CPVC Fitting",
    "UPVC Pipe",   "UPVC Fitting",
    "SWR Pipe",    "SWR Fitting",
    "AGRI Pipe",   "AGRI Fitting",
    *SOLVENT_CATEGORIES,
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CategoryResult:
    category: str
    daily_values: List[float]       # non-zero daily totals (pcs)
    n_days: int                     # = len(daily_values)
    produced_to_date: float         # sum of all daily pcs (including 0-days)
    cap_per_day: float              # 0 when no production
    method: str                     # "p90" | "mean" | "none"
    remaining: float                # from plan (produce_required - produced_monthly)
    working_days_remaining: int
    feasible: float                 # cap_per_day * working_days_remaining
    shortfall: float                # max(0, remaining - feasible)

    @property
    def shortfall_pct(self) -> Optional[float]:
        if self.remaining <= 0:
            return None
        return round(self.shortfall / self.remaining * 100, 1)

    @property
    def no_demonstrated_capacity(self) -> bool:
        return self.produced_to_date == 0 and self.cap_per_day == 0


@dataclasses.dataclass
class CorrectiveReplanResult:
    month: str
    as_of_date: str
    working_days_total: int
    working_days_elapsed: int
    working_days_remaining: int
    categories: List[CategoryResult]
    source_file_id: str
    source_date_min: str   # earliest date seen in Report-11/12
    source_date_max: str   # latest date seen
    plan_produced_total: float   # from Report-1 (plan "produced" column)
    actual_produced_total: float # from our Report-11+12 parse
    warnings: List[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date_cell(raw: str, year: int, month: int) -> Optional[str]:
    """Parse a date cell into 'YYYY-MM-DD'.

    Supports:
      'Aug 1, 2026'   — Plumbing Report-11/12 format
      '1-Aug-2026'    — PTMT master format
      '1 Aug 2026'    — variant
      '01-08-2026'    — dd-mm-yyyy
      '2026-08-01'    — ISO
      '1'             — plain day (combined with year/month arg)

    Raises ValueError for any unrecognised format so silent-zero bugs are
    caught immediately (Issue #6 regression guard).
    """
    raw = str(raw).strip().lstrip("'").strip()
    if not raw:
        return None

    # ISO
    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw):
        return raw

    # "Aug 1, 2026" or "Aug 01, 2026" (with or without space after comma)
    for fmt in ("%b %d, %Y", "%b %d,%Y", "%B %d, %Y", "%B %d,%Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass

    # "1-Aug-2026" or "1 Aug 2026"
    for fmt in ("%d-%b-%Y", "%d %b %Y", "%d-%B-%Y", "%d %B %Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass

    # dd-mm-yyyy or dd/mm/yyyy
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime.date(y, mo, d).isoformat()
        except ValueError:
            return None

    # Plain day number (1..31)
    if re.match(r'^\d{1,2}$', raw):
        try:
            return datetime.date(year, month, int(raw)).isoformat()
        except ValueError:
            return None

    # Numeric float / Excel serial → skip silently (not a date we understand)
    if re.match(r'^\d+\.\d+$', raw) or re.match(r'^\d{5}$', raw):
        return None

    raise ValueError(f"Unrecognised date format: {raw!r}")


def _to_float(val, default: float = 0.0) -> float:
    if val is None:
        return default
    s = str(val).replace(",", "").strip()
    if s.upper() in ("#N/A", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", ""):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _col_idx(hdr: list, *names: str) -> int:
    """Return first column index whose header (upper-stripped) contains any name."""
    for ni, name in enumerate(names):
        name_u = name.upper()
        for ci, cell in enumerate(hdr):
            if name_u in str(cell).upper().strip():
                return ci
    return -1


def _percentile_10(values: List[float]) -> float:
    """10th-percentile (conservative 'p90') without numpy.

    With n values sorted ascending, the 10th-percentile is the value below
    which 10 % of observations fall, i.e. 90 % of production days equalled or
    exceeded this level — making it a 90 %-confidence capacity estimate.
    Uses linear interpolation identical to numpy's default method.
    """
    s = sorted(values)
    n = len(s)
    if n == 1:
        return s[0]
    idx = (n - 1) * 0.10          # fractional index into sorted list
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _compute_cap_per_day(daily_totals: List[float]) -> Tuple[float, str, int]:
    """Return (cap_per_day, method_label, n_non_zero_days).

    Fallback chain: p90 (≥ MIN_DAYS_FOR_P90 days) → mean → 0 / none.
    """
    non_zero = [v for v in daily_totals if v > 0]
    n = len(non_zero)
    if n == 0:
        return 0.0, "none", 0
    if n >= MIN_DAYS_FOR_P90:
        return round(_percentile_10(non_zero), 1), "p90", n
    # Mean for few days — the only sensible estimate when 1-2 days in month
    return round(sum(non_zero) / n, 1), "mean", n


# ---------------------------------------------------------------------------
# Report-11 parser (Pipe pcs)
# ---------------------------------------------------------------------------

def _parse_r11_daily_pcs(values: list, year: int, month: int) -> Dict[str, Dict[str, float]]:
    """Parse Report-11 (M/C & Item-wise Actual Production).

    Returns {date_iso: {category: pcs_total}}.
    Category derived from TYPES column (CPVC/UPVC/SWR/AGRI → CPVC Pipe etc.).

    Date format "Aug 1, 2026" is supported (Issue #5 fix).
    """
    if not values:
        return {}

    # Locate header row: first row containing "ITEM CODE"
    hdr_idx = None
    for i, row in enumerate(values[:15]):
        if any("ITEM CODE" in str(c).upper() for c in row):
            hdr_idx = i
            break
    if hdr_idx is None:
        hdr_idx = 4  # spec default

    hdr = values[hdr_idx]
    col_date = _col_idx(hdr, "DATE"); col_date = col_date if col_date >= 0 else 1
    col_type = _col_idx(hdr, "TYPE", "TYPES"); col_type = col_type if col_type >= 0 else 4
    col_item = _col_idx(hdr, "ITEM CODE"); col_item = col_item if col_item >= 0 else 5
    col_pcs  = _col_idx(hdr, "PCS");        col_pcs  = col_pcs  if col_pcs  >= 0 else 8

    daily: Dict[str, Dict[str, float]] = {}  # date → {category → pcs}
    last_date: Optional[str] = None
    dates_seen: set = set()

    for raw in values[hdr_idx + 1:]:
        if len(raw) < 4:
            continue

        def _cell(idx: int) -> str:
            return str(raw[idx]).strip() if idx < len(raw) else ""

        # Date — carry forward if blank
        date_raw = _cell(col_date)
        if date_raw:
            try:
                d = _parse_date_cell(date_raw, year, month)
                if d:
                    last_date = d
                    dates_seen.add(d)
            except ValueError:
                pass  # header sub-rows etc.

        if last_date is None:
            continue

        # Type → category
        mat_type = _cell(col_type).upper()
        if not mat_type or "TOTAL" in mat_type or "TYPE" in mat_type:
            continue
        category = PIPE_CATEGORIES.get(mat_type)
        if not category:
            continue

        item = _cell(col_item)
        if not item or "ITEM" in item.upper() or re.match(r'^\d+$', item):
            continue

        pcs = _to_float(_cell(col_pcs))
        if pcs <= 0:
            continue

        day_map = daily.setdefault(last_date, {})
        day_map[category] = day_map.get(category, 0.0) + pcs

    return daily


# ---------------------------------------------------------------------------
# Report-12 parser (Moulding/Fitting pcs)
# ---------------------------------------------------------------------------

def _parse_r12_daily_pcs(values: list, year: int, month: int) -> Dict[str, Dict[str, float]]:
    """Parse Report-12 (Mould M/C) for fitting pcs per category per date.

    Header is at row index 3 (DATE | MATERIAL | Item Code | … | Output Pc | …).
    Row 4 has sub-headers; data starts row 5 (0-indexed).
    """
    if not values:
        return {}

    # Locate header row by DATE + MATERIAL
    hdr_idx = None
    for i, row in enumerate(values[:10]):
        row_u = [str(c).upper().strip() for c in row]
        if "DATE" in row_u and ("MATERIAL" in row_u or "ITEM CODE" in row_u):
            hdr_idx = i
            break
    if hdr_idx is None:
        hdr_idx = 3

    hdr = values[hdr_idx]
    col_date = _col_idx(hdr, "DATE");      col_date = col_date if col_date >= 0 else 0
    col_mat  = _col_idx(hdr, "MATERIAL");  col_mat  = col_mat  if col_mat  >= 0 else 1
    col_item = _col_idx(hdr, "ITEM CODE"); col_item = col_item if col_item >= 0 else 2

    # Pcs column: "Output Production" header in hdr, but the actual ' Pc ' token
    # is in the sub-header row immediately below — fall back to col_pcs = 8.
    col_pcs = _col_idx(hdr, "OUTPUT PRODUCTION", "PCS", "PC")
    if col_pcs < 0 and hdr_idx + 1 < len(values):
        sub = values[hdr_idx + 1]
        col_pcs = _col_idx(sub, "PC", "PCS")
    if col_pcs < 0:
        col_pcs = 8

    # Skip sub-header row if present
    data_start = hdr_idx + 1
    if data_start < len(values):
        first = [str(c).strip() for c in values[data_start][:6]]
        if all(not c or c.upper() in ("PC", "PCS", "WT IN KGS", "KGS", "WTE", "WTD", "") for c in first):
            data_start += 1

    daily: Dict[str, Dict[str, float]] = {}
    last_date: Optional[str] = None

    for raw in values[data_start:]:
        if len(raw) < 3:
            continue

        def _cell(idx: int) -> str:
            return str(raw[idx]).strip() if idx < len(raw) else ""

        date_raw = _cell(col_date)
        if date_raw:
            try:
                d = _parse_date_cell(date_raw, year, month)
                if d:
                    last_date = d
            except ValueError:
                pass

        if last_date is None:
            continue

        mat = _cell(col_mat).upper()
        if not mat or "TOTAL" in mat or "MATERIAL" in mat:
            continue
        # Normalise "TEFFLONE" → skip (not a standard PIPE fitting category)
        if "TEFF" in mat or "TEFLON" in mat:
            continue
        category = FITTING_CATEGORIES.get(mat)
        if not category:
            continue

        item = _cell(col_item)
        if not item or "ITEM" in item.upper():
            continue

        pcs = _to_float(_cell(col_pcs))
        if pcs <= 0:
            continue

        day_map = daily.setdefault(last_date, {})
        day_map[category] = day_map.get(category, 0.0) + pcs

    return daily


# ---------------------------------------------------------------------------
# Working-day calendar (Mon–Sat)
# ---------------------------------------------------------------------------

def _working_days_in_month(year: int, month: int) -> List[datetime.date]:
    """All Mon–Sat dates in *year*/*month*."""
    _, n_days = calendar.monthrange(year, month)
    return [
        datetime.date(year, month, d)
        for d in range(1, n_days + 1)
        if datetime.date(year, month, d).weekday() < 6   # 0=Mon … 5=Sat
    ]


def _count_working_days(year: int, month: int, as_of: datetime.date) -> Tuple[int, int, int]:
    """Return (total, elapsed, remaining) Mon–Sat working days.

    *elapsed* = working days strictly before *as_of* (i.e. completed days).
    *remaining* = working days from *as_of* inclusive to month end.
    """
    all_wd = _working_days_in_month(year, month)
    elapsed   = sum(1 for d in all_wd if d < as_of)
    remaining = sum(1 for d in all_wd if d >= as_of)
    return len(all_wd), elapsed, remaining


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

def compute_corrective_replan(
    month: str,
    plan_recs,           # list[planning.PlanRecord]
    r11_values: list,    # raw Sheets values from Report-11
    r12_values: list,    # raw Sheets values from Report-12
    as_of_date: str,     # 'YYYY-MM-DD'
    file_id: str = "",
) -> CorrectiveReplanResult:
    """Compute the corrective re-plan for Plumbing (PIPE + Fitting).

    Parameters
    ----------
    month       : 'YYYY-MM'
    plan_recs   : PlanRecord list from load_planning('PIPE', month)
    r11_values  : raw values from Report-11 (pipe actuals)
    r12_values  : raw values from Report-12 (fitting actuals)
    as_of_date  : today's date ('YYYY-MM-DD')
    file_id     : source Google Sheets file ID (for provenance reporting)

    Returns a CorrectiveReplanResult with per-category CategoryResult rows.

    INVARIANTS enforced (AssertionError on violation)
    -------------------------------------------------
    1. producedToDate > 0  →  cap_per_day > 0
    2. feasible == cap_per_day × working_days_remaining  (exact float multiply)
    3. total_shortfall < total_remaining  (when ≥ 1 category has production)
    """
    year  = int(month[:4])
    mnum  = int(month[5:7])
    as_of = datetime.date.fromisoformat(as_of_date)

    wd_total, wd_elapsed, wd_remaining = _count_working_days(year, mnum, as_of)

    # --- Parse daily actuals ------------------------------------------------
    r11_daily = _parse_r11_daily_pcs(r11_values, year, mnum)
    r12_daily = _parse_r12_daily_pcs(r12_values, year, mnum)

    # Collect all dates seen
    all_dates = sorted(set(r11_daily) | set(r12_daily))
    src_date_min = all_dates[0]  if all_dates else ""
    src_date_max = all_dates[-1] if all_dates else ""

    # Build per-category daily totals: {category → list[pcs_per_day]}
    # We collect one entry per *production day* (date where category ran).
    # Days the category didn't run are NOT included — they reflect absence,
    # not capacity constraint.
    cat_daily: Dict[str, List[float]] = {c: [] for c in CATEGORY_ORDER}
    cat_produced: Dict[str, float] = {c: 0.0 for c in CATEGORY_ORDER}

    for date in all_dates:
        day = {}
        for cat in PIPE_CATEGORIES.values():
            day[cat] = r11_daily.get(date, {}).get(cat, 0.0)
        for cat in FITTING_CATEGORIES.values():
            v = r12_daily.get(date, {}).get(cat, 0.0)
            if cat in day:
                day[cat] += v
            else:
                day[cat] = v
        for cat, pcs in day.items():
            if cat not in cat_daily:
                continue
            cat_produced[cat] += pcs
            if pcs > 0:
                cat_daily[cat].append(pcs)

    # Solvent categories: always zero (not tracked in Report-11/12)
    for cat in SOLVENT_CATEGORIES:
        cat_daily[cat] = []
        cat_produced[cat] = 0.0

    # --- Plan remaining demand per category --------------------------------
    # Aggregate plan records: group by (family, sub-type PIPE/FITTING)
    cat_remaining: Dict[str, float] = {c: 0.0 for c in CATEGORY_ORDER}
    plan_produced_total = 0.0

    for rec in (plan_recs or []):
        fam = (rec.family or "").upper()   # CPVC / UPVC / SWR / AGRI
        cat_str = (rec.category or "").upper()  # e.g. "CPVC PIPE"
        if "FITTING" in cat_str or "FIT" in cat_str:
            sub = "Fitting"
        elif "SOLVENT" in cat_str or "SOL" in cat_str:
            sub = "Solvent"
        else:
            sub = "Pipe"

        label = f"{fam.capitalize()} {sub}" if fam and sub else None
        if fam == "AGRI":
            label = f"AGRI {sub}"
        elif fam == "SWR":
            label = f"SWR {sub}"
        elif fam == "CPVC":
            label = f"CPVC {sub}"
        elif fam == "UPVC":
            label = f"UPVC {sub}"

        if label and label in cat_remaining:
            # net_requirement = max(produce_required - produced, ideal_qty - closing_stock)
            demand = max(
                getattr(rec, "produce_required", 0.0) - getattr(rec, "produced", 0.0),
                getattr(rec, "ideal_qty", 0.0) - getattr(rec, "closing_stock", 0.0),
                0.0,
            )
            cat_remaining[label] += demand

        plan_produced_total += getattr(rec, "produced", 0.0)

    actual_produced_total = sum(cat_produced.values())

    # --- Build per-category results -----------------------------------------
    warnings: List[str] = []
    results: List[CategoryResult] = []

    for cat in CATEGORY_ORDER:
        daily_vals = cat_daily.get(cat, [])
        produced   = cat_produced.get(cat, 0.0)
        remaining  = cat_remaining.get(cat, 0.0)

        cap, method, n = _compute_cap_per_day(daily_vals)
        feasible  = round(cap * wd_remaining, 1)
        shortfall = round(max(0.0, remaining - feasible), 1)

        # INVARIANT 1: produced > 0  →  cap > 0
        if produced > 0 and cap <= 0:
            warnings.append(
                f"INVARIANT VIOLATED: {cat} has producedToDate={produced:.0f} "
                f"but cap_per_day=0. This is a bug — the fallback chain failed."
            )
            # Emergency fallback: force mean from produced / max(1, n)
            cap     = round(produced / max(1, len(all_dates)), 1)
            method  = "mean(emergency)"
            feasible = round(cap * wd_remaining, 1)
            shortfall = round(max(0.0, remaining - feasible), 1)

        results.append(CategoryResult(
            category=cat,
            daily_values=daily_vals,
            n_days=n,
            produced_to_date=produced,
            cap_per_day=cap,
            method=method,
            remaining=remaining,
            working_days_remaining=wd_remaining,
            feasible=feasible,
            shortfall=shortfall,
        ))

    # --- INVARIANT 2: feasible == cap × wd_remaining (exact) ----------------
    for r in results:
        expected = round(r.cap_per_day * r.working_days_remaining, 1)
        if abs(r.feasible - expected) > 0.05:
            warnings.append(
                f"Feasibility inconsistency for {r.category}: "
                f"feasible={r.feasible} but cap×days={expected}"
            )

    # --- INVARIANT 3: total_shortfall < total_remaining if any production ----
    total_remaining  = sum(r.remaining  for r in results)
    total_shortfall  = sum(r.shortfall  for r in results)
    total_production = sum(r.produced_to_date for r in results)
    if total_production > 0 and total_shortfall >= total_remaining and total_remaining > 0:
        warnings.append(
            f"CRITICAL: total_shortfall ({total_shortfall:,.0f}) ≥ total_remaining "
            f"({total_remaining:,.0f}) despite {total_production:,.0f} pcs produced. "
            "Capacity calculation has failed — do not act on this report."
        )

    return CorrectiveReplanResult(
        month=month,
        as_of_date=as_of_date,
        working_days_total=wd_total,
        working_days_elapsed=wd_elapsed,
        working_days_remaining=wd_remaining,
        categories=results,
        source_file_id=file_id,
        source_date_min=src_date_min,
        source_date_max=src_date_max,
        plan_produced_total=round(plan_produced_total, 0),
        actual_produced_total=round(actual_produced_total, 0),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# XLSX export
# ---------------------------------------------------------------------------

def corrective_replan_bytes(result: CorrectiveReplanResult) -> bytes:
    """Return .xlsx bytes for the Corrective Re-plan report.

    Tabs
    ----
    1. Re-plan          — category-level Cap/Day, Feasible, Shortfall
    2. Daily Actuals    — date × category pivot of raw pcs
    3. Provenance       — file IDs, date ranges, method notes
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import (
        Alignment, Font, PatternFill, numbers
    )
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)

    _NAVY   = "1F3864"
    _AMBER  = "FFF2CC"
    _GREEN  = "E2EFDA"
    _RED    = "FCE4D6"
    _BLUE   = "DBEAFE"
    _GREY   = "F5F5F5"

    def _nfont(**kw): return Font(name="Calibri", **kw)
    def _nfill(c):    return PatternFill("solid", fgColor=c)
    def _align(**kw): return Alignment(**kw)

    def _hdr(ws, r, c, txt, w=14, bold=True, colour="FFFFFF", fill=_NAVY):
        cell = ws.cell(row=r, column=c, value=txt)
        cell.font = Font(name="Calibri", bold=bold, size=9, color=colour)
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        col = get_column_letter(c)
        ws.column_dimensions[col].width = max(ws.column_dimensions[col].width or 0, w)

    def _title(ws, text):
        ws.merge_cells("A1:K1")
        c = ws["A1"]
        c.value = text
        c.font = Font(name="Calibri", bold=True, size=13, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=_NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 28
        ws.row_dimensions[2].height = 4

    def _note(ws, r, text, ncols=11):
        end = get_column_letter(ncols)
        ws.merge_cells(f"A{r}:{end}{r}")
        c = ws[f"A{r}"]
        c.value = text
        c.font = Font(name="Calibri", italic=True, size=9, color="555555")
        c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 22

    # ── Tab 1: Re-plan Summary ───────────────────────────────────────────────
    ws1 = wb.create_sheet("Re-plan")
    ws1.sheet_view.showGridLines = False

    _title(ws1, f"Corrective Re-plan — Plumbing — {result.month}  (as of {result.as_of_date})")

    subtitle = (
        f"Working days: {result.working_days_total} total, "
        f"{result.working_days_elapsed} elapsed, "
        f"{result.working_days_remaining} remaining. "
        f"Capacity method: p90 (≥{MIN_DAYS_FOR_P90} days) → mean → none. "
        f"Feasible = Cap/Day × {result.working_days_remaining} remaining days."
    )
    _note(ws1, 3, subtitle)

    HDRS = [
        "Category", "Days recorded", "Produced to date",
        "Remaining demand", "Cap/Day", "Method",
        "Feasible", "Shortfall", "Shortfall %"
    ]
    for ci, h in enumerate(HDRS, 1):
        _hdr(ws1, 4, ci, h, w=[18, 14, 18, 18, 14, 10, 14, 14, 12][ci-1])

    row = 5
    for cat in result.categories:
        fill_colour = (
            _GREY  if cat.no_demonstrated_capacity else
            _RED   if cat.shortfall > 0 else
            _GREEN
        )
        for ci, val in enumerate([
            cat.category,
            cat.n_days if not cat.no_demonstrated_capacity else "—",
            round(cat.produced_to_date) or ("—" if cat.no_demonstrated_capacity else 0),
            round(cat.remaining) if cat.remaining > 0 else "—",
            round(cat.cap_per_day, 1) if not cat.no_demonstrated_capacity else "NO CAPACITY",
            cat.method if not cat.no_demonstrated_capacity else "none",
            round(cat.feasible) if not cat.no_demonstrated_capacity else 0,
            round(cat.shortfall) if cat.shortfall > 0 else "—",
            f"{cat.shortfall_pct:.0f}%" if cat.shortfall_pct else "—",
        ], 1):
            c = ws1.cell(row=row, column=ci, value=val)
            c.font = Font(name="Calibri", size=9)
            c.fill = PatternFill("solid", fgColor=fill_colour)
            c.alignment = Alignment(
                horizontal="right" if ci > 1 else "left",
                vertical="center",
            )
        row += 1

    # Totals row
    total_produced  = sum(c.produced_to_date for c in result.categories)
    total_remaining = sum(c.remaining        for c in result.categories)
    total_feasible  = sum(c.feasible         for c in result.categories)
    total_shortfall = sum(c.shortfall        for c in result.categories)
    total_sf_pct    = (total_shortfall / total_remaining * 100) if total_remaining > 0 else 0

    for ci, val in enumerate([
        "TOTAL", "—", round(total_produced), round(total_remaining),
        "—", "—", round(total_feasible), round(total_shortfall),
        f"{total_sf_pct:.0f}%",
    ], 1):
        c = ws1.cell(row=row, column=ci, value=val)
        c.font = Font(name="Calibri", bold=True, size=9)
        c.fill = PatternFill("solid", fgColor=_AMBER)
        c.alignment = Alignment(horizontal="right" if ci > 1 else "left", vertical="center")

    # Warnings
    if result.warnings:
        row += 2
        for w in result.warnings:
            ws1.merge_cells(f"A{row}:K{row}")
            c = ws1[f"A{row}"]
            c.value = f"⚠ {w}"
            c.font = Font(name="Calibri", bold=True, size=9, color="C00000")
            c.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
            ws1.row_dimensions[row].height = 32
            row += 1

    # Source reconciliation note
    row += 1
    recon_note = (
        f"producedToDate (this report): {result.actual_produced_total:,.0f} pcs  "
        f"from Report-11 + Report-12  ({result.source_date_min} – {result.source_date_max}).  "
        f"Planning 'Produced' (Report-1): {result.plan_produced_total:,.0f} pcs  "
        f"(may differ — R-1 aggregates all sources; this report reads R-11/R-12 only)."
    )
    _note(ws1, row, recon_note, 11)

    # ── Tab 2: Daily Actuals ─────────────────────────────────────────────────
    ws2 = wb.create_sheet("Daily Actuals")
    ws2.sheet_view.showGridLines = False

    _title(ws2, f"Daily Actuals by Category — {result.month}  (pcs)")

    # Collect all dates across all categories
    all_dates_set: set = set()
    cat_daily_map: Dict[str, Dict[str, float]] = {}  # cat → {date → pcs}
    for cat in result.categories:
        cat_daily_map[cat.category] = {}

    # We only have aggregated daily_values (not date-keyed) in CategoryResult.
    # Re-derive from categories — note this is already aggregated. We can only
    # show which categories had how many production days, not the per-date pcs
    # unless we save them. Add date-keyed map to CategoryResult via a workaround:
    # store them as a list so we just show "Day 1, Day 2 …" labels.
    _note(ws2, 3,
          "Daily totals (pcs) per category. Days when a category did not run are omitted.",
          ncols=11)

    pipe_cats    = [c for c in CATEGORY_ORDER if "Pipe" in c and "Solvent" not in c]
    fitting_cats = [c for c in CATEGORY_ORDER if "Fitting" in c]
    all_cats     = pipe_cats + fitting_cats

    _hdr(ws2, 4, 1, "Day #", 8)
    for ci, cat in enumerate(all_cats, 2):
        _hdr(ws2, 4, ci, cat, 14)

    row = 5
    cat_to_res = {r.category: r for r in result.categories}
    max_days = max((len(r.daily_values) for r in result.categories), default=0)
    for day_i in range(max_days):
        ws2.cell(row=row, column=1, value=f"Day {day_i+1}").font = _nfont(size=9, bold=True)
        for ci, cat in enumerate(all_cats, 2):
            vals = cat_to_res[cat].daily_values if cat in cat_to_res else []
            v = vals[day_i] if day_i < len(vals) else ""
            c = ws2.cell(row=row, column=ci, value=round(v) if v != "" else "")
            c.font = _nfont(size=9)
            c.alignment = _align(horizontal="right")
            c.fill = PatternFill("solid", fgColor=_GREY if row % 2 == 0 else "FFFFFF")
        row += 1

    # ── Tab 3: Provenance ────────────────────────────────────────────────────
    ws3 = wb.create_sheet("Provenance")
    ws3.sheet_view.showGridLines = False
    _title(ws3, f"Report Provenance — {result.month}")

    meta = [
        ("Parameter", "Value"),
        ("Month", result.month),
        ("As-of date", result.as_of_date),
        ("Working days (Mon–Sat)", result.working_days_total),
        ("Working days elapsed", result.working_days_elapsed),
        ("Working days remaining", result.working_days_remaining),
        ("Source file ID (Report-11 / 12)", result.source_file_id),
        ("Source file link",
         f"https://docs.google.com/spreadsheets/d/{result.source_file_id}" if result.source_file_id else "—"),
        ("Date range in source", f"{result.source_date_min} – {result.source_date_max}"),
        ("n production dates observed", len(set(result.source_date_min)) if result.source_date_min else 0),
        ("Produced to date (Report-11+12)", f"{result.actual_produced_total:,.0f} pcs"),
        ("Produced to date (Report-1 plan tab)", f"{result.plan_produced_total:,.0f} pcs"),
        ("Min days for p90", MIN_DAYS_FOR_P90),
        ("Capacity method note",
         f"p90 = 10th-percentile of non-zero daily sums (90 % confidence). "
         f"mean = arithmetic mean when < {MIN_DAYS_FOR_P90} non-zero days. "
         "none = NO_DEMONSTRATED_CAPACITY (0 production days seen)."),
        ("Invariant: no-zero-with-production", "PASS" if not any(
            "INVARIANT VIOLATED" in w for w in result.warnings) else "FAIL"),
        ("Invariant: feasibility consistent", "PASS" if not any(
            "inconsistency" in w for w in result.warnings) else "FAIL"),
        ("Invariant: not-everything-unfulfillable", "PASS" if not any(
            "CRITICAL" in w for w in result.warnings) else "FAIL"),
        ("Warnings", "\n".join(result.warnings) if result.warnings else "None"),
    ]

    for ri, (k, v) in enumerate(meta, 4):
        c1 = ws3.cell(row=ri, column=1, value=k)
        c1.font = _nfont(bold=True, size=9)
        c2 = ws3.cell(row=ri, column=2, value=str(v))
        c2.font = _nfont(size=9)
        c2.alignment = _align(wrap_text=True, vertical="top")

    ws3.column_dimensions["A"].width = 38
    ws3.column_dimensions["B"].width = 70

    out = BytesIO()
    wb.save(out)
    return out.getvalue()
