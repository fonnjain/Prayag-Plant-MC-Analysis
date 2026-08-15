"""Tank DAILY REPORT ↔ PROD. REPORT daily run-hours reconciliation (pure, network-free).

Tank run hours are recorded in TWO independent tabs inside each plant workbook:

  * **DAILY REPORT** — a wide per-date matrix.  Only the TOTAL row is reliable for
    per-date data; per-machine rows use a compressed layout that is NOT aligned with
    the date headers and cannot be parsed safely per-date.  The machine label
    dimension lives here (machine labels in col 1).

  * **PROD. REPORT** — the per-item production journal.  Carries a
    ``PRODUCTION HOURS`` column (the same field is labelled ``RUN HOURS`` in some
    tabs — match both, per R-04).  Hours are populated for VN; blank in KH (column
    present but every cell empty); absent entirely in WB (no hours column at all).

Neither source is complete alone — all four directions appear across VN Apr–Jul:

  * VN April: PROD. REPORT misses Apr 28 (DR-only, 8 h); DAILY REPORT misses
    Apr 16 (PR-only, 12 h) — gaps in BOTH directions in one month.
  * VN May: same 24 dates, different hours on May 8 (DR=12 h, PR=8 h); max wins.
  * VN June: PROD. REPORT has 26 dates (208 h); DAILY REPORT has 23 (184 h).
    Three PR-only dates (Jun 27 / 29 / 30) carry 24 extra hours.
  * VN July: DAILY REPORT has 29 dates (232 h); PROD. REPORT has 28 (224 h).
    Jul 19 is DR-only — machine ran 8 h with 0 kg output.

Reconciliation rule (R-39): for every date the corrected run hours = max(DR hours,
PR hours) over the UNION of all dates either source holds.

Where a plant-month has no second source (KH all months; WB May–July), there is no
reconciliation.  Hours come from whichever single source has them, or remain
unavailable.  Never fabricated (R-07).

Machine dimension: from DAILY REPORT machine label strings only.  PROD. REPORT has
no machine column.  Machine labels are reported for all plant-months (KH has
"MACHINE-1" in the tab even when hours are blank).  However, the machine field on
Records is set to the DR labels **only when the DR carries date-wise hours data**
for that plant-month — when DR is all-zero (KH all months, WB May-Jul) the machine
field stays blank and the reconcile report notes that machine-level hours are not
recorded, rather than rendering an unnamed empty bucket (R-35).

Guard — DR-only non-zero output: output is always sourced from PROD. REPORT.  A
DAILY REPORT-only date that carries non-zero output means that production would be
lost from the report entirely.  These dates are flagged (the VN Jul 19 DR-only date
carries 0 kg, so it is not flagged).

Guard — degenerate join (R-27): if both sources hold rows but share zero date
overlap, warn — silence would invisibly disable the reconciliation.

Guard — rejected-pieces exceed produced pieces: flag any per-row occurrence so
data owners can correct the source (KH Jun 23: 10 produced, 90 rejected; first
instance of the guard firing).

Pure: no network, no I/O.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import parsers

# ---------------------------------------------------------------------------
# DAILY REPORT column structure (confirmed against live VN files)
# ---------------------------------------------------------------------------
# The Sheets API returns the DAILY REPORT wide-matrix with:
#   col 0: always blank (merged-cell artefact)
#   col 1: machine label ('MACHINE' in header row; 'TOTAL'/'MACHINE-1' etc. in data)
#   col 2: monthly RUN HOURS total      (sub-header row: 'RUN HOURS')
#   col 3: monthly OUTPUT KG            (sub-header row: 'OUTPUT (KG)')
#   col 4: Average Per Hour Output      (derived, skip)
#   col 5: monthly REJECTION KG         (sub-header row: 'REJECTION (KG)')
#   col 6: Rejection % age              (derived, skip)
#   col 7+: per-date triplets keyed by 'Jul, 1' / 'Jun, 30' etc. in the header row;
#            triplet = Run Hours / Output KG / Rejection KG (3 cols each)
#
# Detection: header row has '' at col 0 and 'MACHINE' at col 1.
# Date pattern matches both 'Jul, 1' (comma-space) and 'Jul 1' (space).

_DR_DATE_RE = re.compile(
    r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[,.]?\s*(\d+)$',
    re.IGNORECASE,
)

# Fixed monthly-summary column offsets (see diagram above).
_DR_COL_MONTHLY_HRS = 2
_DR_COL_MONTHLY_OUT = 3
_DR_COL_MONTHLY_REJ = 5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_tank_dr(
    values: List[list],
    year_month: str,
) -> Tuple[Dict[str, dict], List[str], dict]:
    """Parse the Tank ``DAILY REPORT`` wide-matrix (pure, network-free).

    Reads per-date run hours / output kg / rejection kg from the **TOTAL row only**
    (per-machine rows use a compressed layout that cannot be aligned to the date
    headers safely).  Also reads the monthly summary totals and machine label strings.

    Returns ``(by_date, machine_labels, monthly)`` where:

    * ``by_date``        — ``{date_str: {"hrs", "out", "rej"}}`` for every date where
                           at least one value is non-zero.  Date strings are
                           ``"YYYY-MM-DD"``.
    * ``machine_labels`` — list of machine label strings from MACHINE-n data rows
                           (e.g. ``["MACHINE-1", "MACHINE-2"]``).
    * ``monthly``        — ``{"hrs", "out", "rej"}`` from the TOTAL-row summary cols
                           (cross-check only; per-date sum is authoritative).

    Returns ``({}, [], {})`` if the header row cannot be found (absent tab or
    unrecognised layout).
    """
    if not values:
        return {}, [], {}

    # Header row: col 0 = '' (blank), col 1 = 'MACHINE'.
    hdr_row: Optional[int] = None
    for i, row in enumerate(values[:8]):
        if len(row) > 1 and str(row[1]).strip().upper() == 'MACHINE':
            hdr_row = i
            break
    if hdr_row is None:
        return {}, [], {}

    hdr = values[hdr_row]
    data_start = hdr_row + 2  # header row + sub-header row

    # Collect (col_idx, "YYYY-MM-DD") for every date column in the header.
    date_cols: List[Tuple[int, str]] = []
    for c, v in enumerate(hdr):
        m = _DR_DATE_RE.match(str(v).strip())
        if m:
            day = int(m.group(2))
            date_cols.append((c, f"{year_month}-{day:02d}"))

    by_date: Dict[str, dict] = {}
    machine_labels: List[str] = []
    monthly: dict = {"hrs": 0.0, "out": 0.0, "rej": 0.0}

    for row in values[data_start:]:
        if not row or len(row) < 2:
            continue
        label = str(row[1]).strip()
        if not label:
            continue

        is_total   = label.upper() == 'TOTAL'
        is_machine = bool(re.search(r'\bMACHINE\b', label.upper()))

        if is_machine and not is_total:
            machine_labels.append(label)

        if is_total:
            def _c(col: int) -> float:
                return parsers.num(row[col]) if col < len(row) else 0.0

            monthly["hrs"] = _c(_DR_COL_MONTHLY_HRS)
            monthly["out"] = _c(_DR_COL_MONTHLY_OUT)
            monthly["rej"] = _c(_DR_COL_MONTHLY_REJ)

            for dc, date_str in date_cols:
                h = parsers.num(row[dc])   if dc   < len(row) else 0.0
                o = parsers.num(row[dc+1]) if dc+1 < len(row) else 0.0
                r = parsers.num(row[dc+2]) if dc+2 < len(row) else 0.0
                if h > 0 or o > 0 or r > 0:
                    by_date[date_str] = {"hrs": h, "out": o, "rej": r}

    return by_date, machine_labels, monthly


def parse_tank_pr_hours(
    values: List[list],
    year_month: str,
) -> Dict[str, float]:
    """Extract per-date run hours from a Tank ``PROD. REPORT``.

    The hours column may be labelled ``PRODUCTION HOURS`` or ``RUN HOURS`` — both
    names are matched (R-04: same field, different names in different tabs; this
    distinction silently zeroed all Tank hours in the original reader).

    Returns ``{date_str: total_hours}`` summed across all item rows for that date.
    Returns ``{}`` if the hours column is absent or every data cell is blank.
    """
    if not values:
        return {}

    hdr_idx = date_c = hrs_c = -1
    for i, row in enumerate(values[:12]):
        U = [str(c).strip().upper() for c in row]
        if 'DATE' in U and any('ITEM CODE' in u for u in U):
            hdr_idx = i
            for c, u in enumerate(U):
                if u == 'DATE' and date_c < 0:
                    date_c = c
                if ('PRODUCTION HOUR' in u or 'RUN HOUR' in u) and hrs_c < 0:
                    hrs_c = c
            break

    if hdr_idx < 0 or date_c < 0 or hrs_c < 0:
        return {}  # column absent (WB) or header not found

    by_date: Dict[str, float] = {}
    for row in values[hdr_idx + 1:]:
        day = parsers._long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        h = parsers.num(row[hrs_c]) if hrs_c < len(row) else 0.0
        if h > 0:
            ds = f"{year_month}-{day:02d}"
            by_date[ds] = by_date.get(ds, 0.0) + h
    return by_date


def check_rej_exceeds_pcs(
    values: List[list],
    year_month: str,
    plant: str,
) -> List[dict]:
    """Flag per-row occurrences where rejected pieces exceed produced pieces.

    Returns a list of dicts ``{"date", "item", "pcs_produced", "pcs_rejected"}``
    for every violating data row.  An empty list means the source is clean.

    This guard is specifically required by the spec for KH June (23 Jun: 10
    produced / 90 rejected; 30 Jun: 243 pcs from 6 cycles when 6 cycles → 24
    elsewhere).  The first case is exactly what this guard catches; the second is
    a cycle-count inflation which falls outside the scope of this guard.
    """
    if not values:
        return []

    hdr_idx = date_c = pcs_c = rej_pcs_c = item_c = -1
    for i, row in enumerate(values[:12]):
        U = [str(c).strip().upper() for c in row]
        if 'DATE' in U and any('ITEM CODE' in u for u in U):
            hdr_idx = i
            for c, u in enumerate(U):
                if u == 'DATE' and date_c < 0:
                    date_c = c
                if 'ITEM CODE' in u and item_c < 0:
                    item_c = c
                # "PRODUCTION IN PCS." / "PRODUCTION IN PCS"
                if 'PRODUCTION' in u and 'PC' in u and 'REJECT' not in u and pcs_c < 0:
                    pcs_c = c
                # "REJECTION IN PCS." / "REJECTION IN PCS"
                if 'REJECTION' in u and 'PC' in u and 'MOUTH' not in u and rej_pcs_c < 0:
                    rej_pcs_c = c
            break

    if hdr_idx < 0 or pcs_c < 0 or rej_pcs_c < 0:
        return []

    violations: List[dict] = []
    for row in values[hdr_idx + 1:]:
        day = parsers._long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        pcs_prod = parsers.num(row[pcs_c])     if pcs_c    < len(row) else 0.0
        pcs_rej  = parsers.num(row[rej_pcs_c]) if rej_pcs_c < len(row) else 0.0
        if pcs_rej > pcs_prod and pcs_prod >= 0:
            item = str(row[item_c]).strip() if 0 <= item_c < len(row) else ""
            violations.append({
                "date":         f"{year_month}-{day:02d}",
                "item":         item,
                "pcs_produced": int(pcs_prod),
                "pcs_rejected": int(pcs_rej),
            })
    return violations


def reconcile(
    dr_by_date: Dict[str, dict],
    pr_hrs_by_date: Dict[str, float],
    plant: str,
    ym: str,
    dr_machine_labels: List[str],
    dr_monthly: dict,
) -> Tuple[Dict[str, float], dict]:
    """Date-wise-max reconciliation of DAILY REPORT hours vs PROD. REPORT hours.

    For each date in the union of both sources, the reconciled hours =
    max(DR hours, PR hours).  The PROD. REPORT is the sole output source; this
    function deals only with run hours.

    Returns ``(union_hrs_by_date, audit)`` where:

    * ``union_hrs_by_date`` — ``{date_str: reconciled_hours}`` (only dates with
                               hours > 0 are included).
    * ``audit``             — structured dict with counts, totals, driving dates,
                               warnings, and cross-check figures.
    """
    dr_dates = set(dr_by_date)
    pr_dates = set(pr_hrs_by_date)
    all_dates = sorted(dr_dates | pr_dates)

    warnings: List[str] = []

    # ---- Degenerate join guard (R-27) ----------------------------------------
    if dr_by_date and pr_hrs_by_date:
        overlap = dr_dates & pr_dates
        if not overlap:
            warnings.append(
                f"{plant} {ym}: DAILY REPORT ({len(dr_dates)} date(s)) and "
                f"PROD. REPORT hours ({len(pr_dates)} date(s)) share no common "
                "dates — reconciliation is degenerate. "
                "Using each source independently."
            )

    # ---- DR-only dates with non-zero output (output would go unrecorded) -----
    dr_only_nonzero_out: List[str] = []
    for d in sorted(dr_dates - pr_dates):
        dr_o = dr_by_date[d].get("out", 0.0)
        if dr_o > 0:
            dr_only_nonzero_out.append(
                f"{d} (DR output={dr_o:.2f} kg, not in PROD. REPORT)"
            )

    if dr_only_nonzero_out:
        warnings.append(
            f"{plant} {ym}: {len(dr_only_nonzero_out)} DAILY REPORT-only date(s) "
            "carry non-zero output — this output is NOT counted (output source is "
            f"PROD. REPORT only). Dates: {'; '.join(dr_only_nonzero_out)}"
        )

    # ---- Date-wise max union --------------------------------------------------
    union_hrs: Dict[str, float] = {}
    driving: List[dict] = []

    for date_str in all_dates:
        dr  = dr_by_date.get(date_str, {})
        dr_h = dr.get("hrs", 0.0)
        pr_h = pr_hrs_by_date.get(date_str, 0.0)
        h = max(dr_h, pr_h)
        if h > 0:
            union_hrs[date_str] = h

        # Record dates that drive the reconciliation (evidence trail).
        in_dr = date_str in dr_dates
        in_pr = date_str in pr_dates
        if in_dr and not in_pr and dr_h > 0:
            driving.append({"date": date_str, "source": "DR-only", "hrs": dr_h})
        elif in_pr and not in_dr and pr_h > 0:
            driving.append({"date": date_str, "source": "PR-only", "hrs": pr_h})
        elif in_dr and in_pr and dr_h != pr_h and (dr_h > 0 or pr_h > 0):
            driving.append({
                "date": date_str, "source": "max",
                "dr_hrs": dr_h, "pr_hrs": pr_h, "hrs": h,
            })

    # ---- Internal DR discrepancy (per-date sum vs monthly summary cell) ------
    dr_sum_hrs   = sum(d.get("hrs", 0.0) for d in dr_by_date.values())
    dr_monthly_h = dr_monthly.get("hrs", 0.0)
    dr_internal_gap = round(abs(dr_sum_hrs - dr_monthly_h), 1)

    # ---- DR rejection kg cross-check (informational only) --------------------
    dr_rej_kg_total = round(sum(d.get("rej", 0.0) for d in dr_by_date.values()), 2)

    audit: dict = {
        "n_dates_dr":           len(dr_dates),
        "n_dates_pr_hrs":       len(pr_dates),
        "n_dates_both":         len(dr_dates & pr_dates),
        "n_dates_dr_only":      len(dr_dates - pr_dates),
        "n_dates_pr_only":      len(pr_dates - dr_dates),
        "union_hrs_total":      round(sum(union_hrs.values()), 1),
        "dr_hrs_total":         round(dr_sum_hrs, 1),
        "pr_hrs_total":         round(sum(pr_hrs_by_date.values()), 1),
        "machine_labels":       list(dr_machine_labels),
        "dr_monthly_hrs_cell":  dr_monthly_h,       # monthly summary cell value
        "dr_internal_gap_hrs":  dr_internal_gap,    # |per-date sum − monthly cell|
        "dr_rej_kg_total":      dr_rej_kg_total,    # DR rejection cross-check
        "driving_dates":        driving,
        "dr_only_nonzero_output_dates": dr_only_nonzero_out,
        "warnings":             warnings,
    }

    return union_hrs, audit
