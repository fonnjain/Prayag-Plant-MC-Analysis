"""mgmt_pipe_summary.py — Data builder for the Pipe M/C Summary management page.

Two sections:

SUMMARY (Section 1) — monthly FY view with 8 columns:
  Run Hours · Actual Output with Rejection (KG) · Labour ·
  Actual Paid Hours · Paid Wages · Paid Hours Devoted per Person ·
  Per Hour Cost · Per KG Labour Cost.

SUMMARY-1 (Section 2) — per-machine YoY comparison, FY26-27 vs FY25-26:
  Pipe Type · Machine · Ideal Hours · Actual Hours · Actual Output (KG) ·
  Ideal Output (rate) · Avg/Hr · Utilisation% · Output Efficiency%.

Sources:
  Run Hours + Gross Output — daily records, plant == "PIPE",
                             r.actual_hours + (r.total_count + r.reject_count)
  Labour + Paid Hours      — Employee Data Details workbook (EMPLOYEE_DATA_FILE_ID),
                             DASHBOARD tab, PIPELINE row (col-group offsets 0 + 2)
  Wages                    — monthly KH-1 workbooks (PIPE_WAGES), CPVC/PIPELINE
                             filter via costing_wages.parse_kh1_wages
  FY25-26 per-machine data — Pipe M/C workbook (PIPE_MC_FILE_ID),
                             tab "Pipe M/C 25-26", TOTAL row
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── FY constants ───────────────────────────────────────────────────────────────

MONTH_LABELS = ["APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                "OCT", "NOV", "DEC", "JAN", "FEB", "MAR"]

_FY_YM: dict[str, dict[str, str]] = {
    "2627": {
        "APR": "2026-04", "MAY": "2026-05", "JUN": "2026-06", "JUL": "2026-07",
        "AUG": "2026-08", "SEP": "2026-09", "OCT": "2026-10", "NOV": "2026-11",
        "DEC": "2026-12", "JAN": "2027-01", "FEB": "2027-02", "MAR": "2027-03",
    },
}

_FY_DISP: dict[str, dict[str, str]] = {
    "2627": {
        "APR": "APR'26", "MAY": "MAY'26", "JUN": "JUN'26", "JUL": "JUL'26",
        "AUG": "AUG'26", "SEP": "SEP'26", "OCT": "OCT'26", "NOV": "NOV'26",
        "DEC": "DEC'26", "JAN": "JAN'27", "FEB": "FEB'27", "MAR": "MAR'27",
    },
}

_FY_LABEL: dict[str, str] = {
    "2627": "FY 2026–27",
    "2526": "FY 2025–26",
}

# Management reports are published only through a reviewed month.  A download
# explicitly selects its own month; the direct page uses this approved default
# until the next period is ready to publish.
PIPE_REPORT_DEFAULT_THROUGH: dict[str, str] = {
    "2627": "2026-07",
}

# ── Source file IDs ────────────────────────────────────────────────────────────

# Pipe M/C workbook — HOURS tab (FY26-27) and Pipe M/C 25-26 tab (FY25-26)
PIPE_MC_FILE_ID = "1EHJvI7KxIahlfZ5ODiAea3Pj2Zk3z1T6cCybZJuDAMQ"
PIPE_MC_2526_TAB = "Pipe M/C 25-26"

# Employee Data Details workbook — DASHBOARD tab for PIPELINE hours/headcount
EMPLOYEE_DATA_FILE_ID = "1Mfjo-CaxboN52hUO_IzrKqEAFxHgegJI4BHQb6H4VYM"
DASHBOARD_TAB = "DASHBOARD"

# Monthly KH-1 wages workbooks for PIPELINE.
# Add new months here as workbooks become available; None-ym → wages = None.
PIPE_WAGES: dict[str, dict[str, str]] = {
    "2627": {
        "2026-04": "1lWOHcMsoWpTmiTyvSRys1xoMiAkeNdSmQGFySR0bBpw",
        "2026-05": "1jCU8KxjJzwsdhX0EcHuyYabvUp15Y1VAQzhloXnTHLQ",
        "2026-06": "1fxsmw7HQL7pockus_MI1SV3RhMakCFYv2-VE9iFMrh8",
        # Pinned from the far-right linked payroll-register column in the
        # Pipe M/C SUMMARY tab; KH-1 parses CPVC / PIPELINE only.
        "2026-07": "1Rzs9I_Ua6ij1Es65S-T-xEBAk8o9E0wrpiB5h9JQrSM",
        # AUG 2026 onwards: not yet registered
    }
}

# ── Machine reference data ─────────────────────────────────────────────────────

# Ideal output rate in kg/hr per machine
MACHINE_IDEAL_RATES: dict[str, int] = {
    "M/C-1": 120, "M/C-2": 280, "M/C-3": 320, "M/C-4": 380, "M/C-5": 350,
    "M/C-6": 350, "M/C-7": 200, "M/C-8": 150, "M/C-9": 400,
}

# Pipe type per machine
MACHINE_TYPES: dict[str, str] = {
    "M/C-1": "CPVC",     "M/C-2": "CPVC",
    "M/C-3": "AGRI/SWR", "M/C-4": "AGRI/SWR", "M/C-9": "AGRI/SWR",
    "M/C-5": "UPVC",     "M/C-6": "UPVC",
    "M/C-7": "OPVC",
    "M/C-8": "COLUMN",
}

MACHINE_ORDER = [f"M/C-{i}" for i in range(1, 10)]  # M/C-1 … M/C-9

# Each machine should run 500 ideal hours per month (confirmed against workbook)
IDEAL_HOURS_PER_MONTH = 500

# ── In-process cache ───────────────────────────────────────────────────────────

_cache_lock  = threading.Lock()
_cache: dict = {}          # {fy: (timestamp, data)}
_CACHE_TTL   = 600         # seconds


# ── Utilities ──────────────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Parse a sheet cell to float; None for blank / error / 0."""
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("#") or s in ("", "-", "—", "n/a", "N/A"):
        return None
    cleaned = re.sub(r"[,₹\s]", "", s)
    try:
        result = float(cleaned)
        return result if result != 0.0 else None
    except ValueError:
        return None


def _num_nz(v) -> Optional[float]:
    """Like _num but keeps genuine zeroes (e.g. headcount)."""
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("#") or s in ("", "-", "—", "n/a", "N/A"):
        return None
    cleaned = re.sub(r"[,₹\s]", "", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().upper())


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


_MONTH_RE = re.compile(
    # No trailing \b — "MARCH'27", "JUNE'26" etc. only share the first 3 chars
    # with our month abbreviations; a word-boundary after "MAR" would not fire
    # inside "MARCH" because "CH" is still a word character.
    r"(APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|JAN|FEB|MAR)",
    re.I,
)
_MONTH_SET = frozenset(MONTH_LABELS)


def _extract_month_lbl(s: str) -> Optional[str]:
    m = _MONTH_RE.search(str(s).upper())
    return m.group(1).upper() if m else None


_PRIMARY_MC_RE = re.compile(r"\bM\s*/?\s*C\s*[-–]?\s*(\d+)\b", re.I)


def _norm_machine(raw: str) -> str:
    """Normalise a machine label to canonical form "M/C-N".

    Handles record labels like "PIPE M/C - 1", "M/C-1", "M/C - 1", etc.
    Only an explicit M/C token is accepted.  A trailing digit alone is not a
    machine identity: ``PIPE Grinder-1`` must never become ``M/C-1``.
    Returns the uppercased original if no M/C token is found.
    """
    s = _norm(raw)
    m = _PRIMARY_MC_RE.search(s)
    if m:
        return f"M/C-{m.group(1)}"
    return s


def _record_month(record) -> Optional[str]:
    """Return a record's monthly reporting key.

    Report-11-only rows are daily-grain records.  New rows are emitted with a
    monthly ``period``, but accepting an ISO date here also keeps a warm cache
    containing pre-fix rows from silently dropping valid production.
    """
    period = str(getattr(record, "period", "") or "")
    if re.fullmatch(r"\d{4}-\d{2}", period):
        return period
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
        return period[:7]
    return None


def _is_primary_pipe_record(record) -> bool:
    """Whether a record belongs to the Pipe extrusion M/C report.

    Finishing is an explicit parser classification and always wins over a
    machine-looking label.  The strict normalizer is a second guard against
    labels such as Grinder-1 and Pulverizer-2 being attributed to extruders.
    """
    return (
        getattr(record, "plant", None) == "PIPE"
        and not bool(getattr(record, "is_finishing", False))
        and _norm_machine(getattr(record, "machine", "") or "") in MACHINE_ORDER
    )


# ── DASHBOARD tab parser ───────────────────────────────────────────────────────

def _read_dashboard_pipeline(values: list) -> dict[str, dict]:
    """Extract paid_hours and labour per month for the PIPELINE sub-department.

    The DASHBOARD tab has one header row (or a merged-cell group header)
    containing month labels spaced 6 columns apart.  Within each 6-column block:
      offset 0 — Paid Hours
      offset 2 — Labour (Actual Number Of Person)

    The PIPELINE data row is identified by any of its first 3 cells containing
    "PIPELINE" (the sheet concatenates "PIPE & FITTINGS" with "PIPELINE" as a
    formula, producing labels like "PIPE & FITTINGSPIPELINE" in col 0).

    Returns {month_label: {"paid_hrs": float|None, "labour": float|None}}
    for each recognised month.  Empty dict on parse failure.
    """
    if not values:
        return {}

    # ── 1. Locate the month-header row ───────────────────────────────────────
    # Find the row that contains ≥ 3 distinct recognisable month abbreviations.
    # Month labels may look like "MARCH'27" or "APR'26" — we extract the 3-char
    # prefix so the regex in _extract_month_lbl covers both forms.
    hdr_idx = -1
    col_month_map: dict[int, str] = {}    # {col_idx: month_label}

    for ri, row in enumerate(values[:25]):
        cmap: dict[int, str] = {}
        for ci, cell in enumerate(row):
            lbl = _extract_month_lbl(str(cell))
            if lbl and lbl in _MONTH_SET:
                cmap[ci] = lbl
        if len(cmap) >= 3:
            hdr_idx       = ri
            col_month_map = cmap
            break

    if hdr_idx < 0 or not col_month_map:
        logger.warning("_read_dashboard_pipeline: month header row not found")
        return {}

    # ── 2. Find the PIPELINE data row ────────────────────────────────────────
    pipeline_row: Optional[list] = None
    for ri, row in enumerate(values):
        if ri <= hdr_idx:
            continue
        # The sheet formula concatenates segment + subdept; check first 3 cells
        head_cells = [_norm(row[c]) if c < len(row) else "" for c in range(3)]
        if any("PIPELINE" in c for c in head_cells):
            pipeline_row = row
            break

    if pipeline_row is None:
        logger.warning("_read_dashboard_pipeline: PIPELINE row not found in DASHBOARD tab")
        return {}

    # ── 3. Read paid_hrs (offset 0) and labour (offset 2) per month ──────────
    result: dict[str, dict] = {}
    for col_start, month_lbl in col_month_map.items():
        def _get(offset: int) -> Optional[float]:
            ci = col_start + offset
            return _num_nz(pipeline_row[ci]) if ci < len(pipeline_row) else None

        result[month_lbl] = {
            "paid_hrs": _get(0),
            "labour":   _get(2),
        }

    return result


# ── KH-1 wages loader (PIPELINE filter) ───────────────────────────────────────

def _find_subdept_col(hdr_row: list) -> int:
    """Return the 0-based column index of the SUB DEPARTMENT column in KH-1."""
    import costing_wages as _cw
    for ci, cell in enumerate(hdr_row):
        h = _cw._norm_hdr(cell)
        if h in ("SUB DEPARTMENT", "SUB-DEPARTMENT", "SUBDEPARTMENT",
                 "SUB DEPT", "SUB-DEPT"):
            return ci
    return -1


def _read_pipeline_wages(ym: str, file_id: str, token: str) -> Optional[float]:
    """Read the KH-1 tab from a monthly wages workbook and sum PIPELINE wages.

    KH-1 structure: DEPARTMENT (col 6) == "CPVC", SUB DEPARTMENT (col 7) == "PIPELINE".
    The PIPELINE machine operators sit inside the CPVC department block — the
    sub-department column distinguishes them from Mixer, Moulding, Toolroom, etc.
    TOTAL PAYABLE column is found by header text (position shifts between files).

    Returns the total wages float, or None on any parse failure.
    """
    import sheets as _sh
    import costing_wages as _cw

    try:
        matrices = _sh.batch_get(file_id, ["KH-1"], token)
        kh1_vals = matrices.get("KH-1", [])
    except Exception as exc:
        logger.warning("_read_pipeline_wages: batch_get failed for %s: %s", ym, exc)
        return None

    if not kh1_vals or len(kh1_vals) <= _cw._KH1_HEADER_ROW:
        return None

    hdr_row    = kh1_vals[_cw._KH1_HEADER_ROW]
    tp_col     = _cw._find_total_payable_col(hdr_row)
    dept_col   = _cw._find_dept_col(hdr_row)
    subdept_col = _find_subdept_col(hdr_row)

    if tp_col < 0 or dept_col < 0:
        logger.warning("_read_pipeline_wages: could not find DEPT/TP columns in KH-1 for %s", ym)
        return None

    if subdept_col < 0:
        logger.warning(
            "_read_pipeline_wages: SUB DEPARTMENT column not found in KH-1 for %s "
            "— cannot isolate PIPELINE rows (would over-sum entire CPVC dept)",
            ym,
        )
        return None

    # Sanity-check: confirm located column actually says TOTAL PAYABLE
    tp_label = _cw._norm_hdr(hdr_row[tp_col])
    if "TOTAL PAYABLE" not in tp_label:
        logger.warning(
            "_read_pipeline_wages: col %d header is '%s', not TOTAL PAYABLE — skipping",
            tp_col, tp_label,
        )
        return None

    total  = 0.0
    n_rows = 0
    for row in kh1_vals[_cw._KH1_DATA_START:]:
        if not row or dept_col >= len(row):
            continue
        dept    = _cw.normalise_dept(row[dept_col])
        subdept = _norm(row[subdept_col] if subdept_col < len(row) else "")
        if dept != "CPVC" or subdept != "PIPELINE":
            continue
        v = _num(row[tp_col] if tp_col < len(row) else None)
        if v is not None:
            total  += v
            n_rows += 1

    if n_rows == 0:
        logger.warning(
            "_read_pipeline_wages: no CPVC/PIPELINE rows found in KH-1 for %s "
            "(dept col=%d, subdept col=%d)",
            ym, dept_col, subdept_col,
        )
        return None

    logger.debug("_read_pipeline_wages: %s → %.0f (from %d PIPELINE rows)", ym, total, n_rows)
    return round(total, 2)


# ── Section 1: monthly summary ─────────────────────────────────────────────────

def _build_section1(
    fy: str,
    records,
    dashboard: dict[str, dict],
    token: str,
    active_yms: Optional[set[str]] = None,
) -> dict:
    """Build Section 1 (SUMMARY) monthly rows and a TOTAL row.

    Parameters
    ----------
    records     : list of daily Record values filtered to PIPE plant
    dashboard   : {month_label: {"paid_hrs": float|None, "labour": float|None}}
    token       : Google OAuth token

    Returns a dict with keys:
      month_rows  — list of 12 row dicts (APR … MAR), one per FY month
      total_row   — summed / derived TOTAL row dict
      warnings    — list of warning strings
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    wages_src = PIPE_WAGES.get(fy, {})
    warnings: list[str] = []

    # ── Accumulate run-hours and gross output from records per month ──────────
    # gross output = total_count + reject_count (production including rejection)
    run_hrs:      dict[str, float] = {}  # {ym: hours}
    gross_output: dict[str, float] = {}  # {ym: kg}

    for r in records:
        if not _is_primary_pipe_record(r):
            continue
        ym = _record_month(r)
        if not ym:
            continue
        run_hrs[ym]      = run_hrs.get(ym, 0.0) + float(r.actual_hours or 0.0)
        gross_output[ym] = gross_output.get(ym, 0.0) + (
            float(r.total_count or 0.0) + float(r.reject_count or 0.0)
        )

    # ── Build one row per FY month ────────────────────────────────────────────
    month_rows: list = []

    for lbl in MONTH_LABELS:
        ym        = fy_ym.get(lbl)
        month_disp = fy_disp.get(lbl, lbl)
        in_scope = active_yms is None or ym in active_yms

        rh = (run_hrs.get(ym, 0.0) or None) if in_scope else None
        go = (gross_output.get(ym, 0.0) or None) if in_scope else None

        # Labour + paid hours from DASHBOARD PIPELINE row
        db = dashboard.get(lbl, {}) if in_scope else {}
        labour    = db.get("labour")
        paid_hrs  = db.get("paid_hrs")

        # Wages from KH-1
        wages    = None
        awaiting = False
        fid = wages_src.get(ym) if in_scope else None
        wages_source = None
        if not in_scope:
            awaiting = False
        elif fid:
            wages = _read_pipeline_wages(ym, fid, token)
            wages_source = {
                "file_id": fid,
                "url": f"https://docs.google.com/spreadsheets/d/{fid}/edit",
                "label": "KH-1 payroll · CPVC / PIPELINE",
                "parsed": wages is not None,
            }
            if wages is None:
                awaiting = True
                warnings.append(
                    f"{lbl}: registered KH-1 payroll source {fid} could not be parsed"
                )
        else:
            awaiting = True  # no file registered yet for this month
            # A future empty month is expected to await its payroll register.
            # Surface the gap only once Pipe activity proves that a wages source
            # should already exist; otherwise every unstarted future month would
            # produce a misleading warning.
            if go is not None and paid_hrs is not None:
                warnings.append(
                    f"{lbl}: Pipe production ({go:,.0f} kg) and paid hours "
                    f"({paid_hrs:,.0f}) exist, but no KH-1 payroll source is registered"
                )

        # Derived columns
        devoted_per_person = _safe_div(paid_hrs, labour)
        per_hour_cost      = _safe_div(wages, paid_hrs)
        per_kg_cost        = _safe_div(wages, go)

        month_rows.append({
            "month_lbl":         lbl,
            "month_disp":        month_disp,
            "ym":                ym,
            "run_hrs":           rh,
            "gross_output_kg":   go,
            "labour":            labour,
            "paid_hrs":          paid_hrs,
            "wages":             wages,
            "wages_source":      wages_source,
            "awaiting":          awaiting,
            "devoted_per_person": devoted_per_person,
            "per_hour_cost":     per_hour_cost,
            "per_kg_cost":       per_kg_cost,
        })

    # ── TOTAL row ─────────────────────────────────────────────────────────────
    def _fsum(key: str) -> Optional[float]:
        vals = [r[key] for r in month_rows if r[key] is not None]
        return sum(vals) if vals else None

    t_run_hrs    = _fsum("run_hrs")
    t_gross_out  = _fsum("gross_output_kg")
    t_labour     = _fsum("labour")
    t_paid_hrs   = _fsum("paid_hrs")
    t_wages      = _fsum("wages")

    any_awaiting = any(r["awaiting"] for r in month_rows)
    parsed_wage_rows = [r for r in month_rows if r["wages"] is not None]
    wage_scope_paid_hrs = sum(
        r["paid_hrs"] for r in parsed_wage_rows if r["paid_hrs"] is not None
    ) or None
    wage_scope_gross_out = sum(
        r["gross_output_kg"]
        for r in parsed_wage_rows
        if r["gross_output_kg"] is not None
    ) or None
    parsed_wage_months = [
        r["month_disp"] for r in parsed_wage_rows
    ]
    awaiting_months = [
        r["month_disp"] for r in month_rows if r["awaiting"]
    ]

    total_row = {
        "run_hrs":            t_run_hrs,
        "gross_output_kg":    t_gross_out,
        "labour":             t_labour,
        "paid_hrs":           t_paid_hrs,
        "wages":              t_wages,
        "awaiting":           any_awaiting,
        "awaiting_months":    awaiting_months,
        "parsed_wage_months": parsed_wage_months,
        "devoted_per_person": _safe_div(t_paid_hrs, t_labour),
        # A partial payroll total must not be diluted by an unpaid month's
        # hours or output.  The template labels this scope when wages await.
        "per_hour_cost":      _safe_div(t_wages, wage_scope_paid_hrs),
        "per_kg_cost":        _safe_div(t_wages, wage_scope_gross_out),
    }

    return {"month_rows": month_rows, "total_row": total_row, "warnings": warnings}


# ── Section 2: per-machine FY26-27 from daily records ─────────────────────────

def _build_section2_fy2627(records, n_months: int) -> list:
    """Build per-machine rows for FY26-27 from daily production records.

    n_months — number of complete (or partial) months in scope, used to compute
    ideal hours (= n_months × IDEAL_HOURS_PER_MONTH per machine).

    Returns a list of row dicts for each machine in MACHINE_ORDER, plus a
    TOTAL row at the end.
    """
    # Accumulate actual_hours and gross output per machine label.
    # Record machine labels look like "PIPE M/C - 1"; normalise to "M/C-1".
    mc_hrs:    dict[str, float] = {}
    mc_output: dict[str, float] = {}

    for r in records:
        if not _is_primary_pipe_record(r):
            continue
        mc = _norm_machine(r.machine or "")
        if not mc:
            continue
        mc_hrs[mc]    = mc_hrs.get(mc, 0.0) + float(r.actual_hours or 0.0)
        mc_output[mc] = mc_output.get(mc, 0.0) + (
            float(r.total_count or 0.0) + float(r.reject_count or 0.0)
        )

    rows: list = []
    sum_actual_hrs  = 0.0
    sum_ideal_hrs   = 0
    sum_output      = 0.0
    sum_avg_hr      = 0.0    # for OutputEff% total formula: sum(avg_hr)/sum(ideal_rate)

    for mc_raw in MACHINE_ORDER:
        mc     = _norm_machine(mc_raw)
        ideal_rate = MACHINE_IDEAL_RATES.get(mc_raw, 0)
        ideal_hrs  = n_months * IDEAL_HOURS_PER_MONTH

        actual_hrs = mc_hrs.get(mc, 0.0) or None
        actual_out = mc_output.get(mc, 0.0) or None

        avg_hr       = _safe_div(actual_out, actual_hrs)
        util_pct     = (_safe_div(actual_hrs, ideal_hrs) * 100
                        if actual_hrs is not None else None)
        out_eff_pct  = (_safe_div(avg_hr, ideal_rate) * 100
                        if avg_hr is not None and ideal_rate else None)

        rows.append({
            "machine":       mc_raw,
            "pipe_type":     MACHINE_TYPES.get(mc_raw, ""),
            "ideal_hrs":     ideal_hrs,
            "actual_hrs":    actual_hrs,
            "actual_out_kg": actual_out,
            "ideal_rate":    ideal_rate,
            "avg_hr":        avg_hr,
            "util_pct":      util_pct,
            "out_eff_pct":   out_eff_pct,
        })

        # Accumulate totals
        sum_ideal_hrs  += ideal_hrs
        if actual_hrs is not None:
            sum_actual_hrs += actual_hrs
        if actual_out is not None:
            sum_output += actual_out
        if avg_hr is not None:
            sum_avg_hr += avg_hr

    # TOTAL row
    # Utilisation = sum(actual_hrs) / sum(ideal_hrs)
    # Output Efficiency = sum(avg_hr) / sum(ideal_rate)   (validated formula)
    sum_ideal_rates = sum(MACHINE_IDEAL_RATES.values())
    t_util_pct = (_safe_div(sum_actual_hrs, sum_ideal_hrs) * 100
                  if sum_actual_hrs else None)
    t_out_eff_pct = (_safe_div(sum_avg_hr, sum_ideal_rates) * 100
                     if sum_avg_hr else None)

    rows.append({
        "machine":       "TOTAL",
        "pipe_type":     "",
        "ideal_hrs":     sum_ideal_hrs,
        "actual_hrs":    sum_actual_hrs or None,
        "actual_out_kg": sum_output or None,
        "ideal_rate":    sum_ideal_rates,
        "avg_hr":        sum_avg_hr or None,
        "util_pct":      t_util_pct,
        "out_eff_pct":   t_out_eff_pct,
        "is_total":      True,
    })

    return rows


# ── Section 2: per-machine FY25-26 from Pipe M/C 25-26 tab ───────────────────

def _parse_pipe_mc_2526(values: list, n_months_2627: int) -> list:
    """Parse the 'Pipe M/C 25-26' tab and return per-machine rows for the prior FY.

    The tab has machine-group sections.  Each machine's data is in alternating
    column pairs (hours, output_kg) in a TOTAL row.  We locate:
      1. A header row containing machine labels (M/C-1 … M/C-9)
      2. The TOTAL row (first cell contains "TOTAL")
    And map machine → (hours_col, output_col).

    We use the same IDEAL_HOURS_PER_MONTH × n_months_2627 for the comparable
    ideal period (i.e. the same number of months elapsed, so totals are
    apples-to-apples for partial-year comparisons).

    Returns list of row dicts in MACHINE_ORDER plus a TOTAL row.
    Returns [] on parse failure.
    """
    if not values:
        return []

    MC_RE = re.compile(r"M[\s/\\-]*C[\s-]*(\d+)", re.I)

    # ── Locate header row with machine labels ─────────────────────────────────
    hdr_idx  = -1
    mc_col_map: dict[str, tuple[int, int]] = {}  # {mc_label: (hours_col, output_col)}

    for ri, row in enumerate(values[:30]):
        normed = [_norm(c) for c in row]
        mc_hits = {ci: _norm(f"M/C-{MC_RE.search(n).group(1)}")
                   for ci, n in enumerate(normed)
                   if MC_RE.search(n)}
        if len(mc_hits) >= 2:
            hdr_idx = ri
            # For each machine column found, assume hours=col and output=col+1
            # (alternating pair convention confirmed in session analysis)
            for col, mc_lbl in sorted(mc_hits.items()):
                if mc_lbl not in mc_col_map:
                    mc_col_map[mc_lbl] = (col, col + 1)
            break

    if hdr_idx < 0 or not mc_col_map:
        logger.warning("_parse_pipe_mc_2526: machine header row not found")
        return []

    # ── Locate TOTAL row ──────────────────────────────────────────────────────
    total_row: Optional[list] = None
    for ri, row in enumerate(values):
        if ri <= hdr_idx:
            continue
        if row and "TOTAL" in _norm(row[0]):
            total_row = row
            break

    if total_row is None:
        logger.warning("_parse_pipe_mc_2526: TOTAL row not found")
        return []

    # ── Build per-machine rows ─────────────────────────────────────────────────
    # Use same n_months period as FY26-27 so the comparison is period-equivalent
    rows: list = []
    sum_actual_hrs  = 0.0
    sum_ideal_hrs   = 0
    sum_output      = 0.0
    sum_avg_hr      = 0.0
    any_data        = False

    for mc_raw in MACHINE_ORDER:
        mc_norm    = _norm(mc_raw)
        pair       = mc_col_map.get(mc_norm)
        ideal_rate = MACHINE_IDEAL_RATES.get(mc_raw, 0)
        ideal_hrs  = n_months_2627 * IDEAL_HOURS_PER_MONTH

        if pair is not None:
            h_col, o_col = pair
            actual_hrs = _num(total_row[h_col] if h_col < len(total_row) else None)
            actual_out = _num(total_row[o_col] if o_col < len(total_row) else None)
        else:
            logger.warning("_parse_pipe_mc_2526: %s not found in header", mc_raw)
            actual_hrs = None
            actual_out = None

        avg_hr      = _safe_div(actual_out, actual_hrs)
        util_pct    = (_safe_div(actual_hrs, ideal_hrs) * 100
                       if actual_hrs is not None else None)
        out_eff_pct = (_safe_div(avg_hr, ideal_rate) * 100
                       if avg_hr is not None and ideal_rate else None)

        rows.append({
            "machine":       mc_raw,
            "pipe_type":     MACHINE_TYPES.get(mc_raw, ""),
            "ideal_hrs":     ideal_hrs,
            "actual_hrs":    actual_hrs,
            "actual_out_kg": actual_out,
            "ideal_rate":    ideal_rate,
            "avg_hr":        avg_hr,
            "util_pct":      util_pct,
            "out_eff_pct":   out_eff_pct,
        })

        sum_ideal_hrs += ideal_hrs
        if actual_hrs is not None:
            sum_actual_hrs += actual_hrs
            any_data = True
        if actual_out is not None:
            sum_output += actual_out
        if avg_hr is not None:
            sum_avg_hr += avg_hr

    if not any_data:
        logger.warning("_parse_pipe_mc_2526: no machine data read from TOTAL row")
        return []

    sum_ideal_rates = sum(MACHINE_IDEAL_RATES.values())
    t_util_pct     = (_safe_div(sum_actual_hrs, sum_ideal_hrs) * 100
                      if sum_actual_hrs else None)
    t_out_eff_pct  = (_safe_div(sum_avg_hr, sum_ideal_rates) * 100
                      if sum_avg_hr else None)

    rows.append({
        "machine":       "TOTAL",
        "pipe_type":     "",
        "ideal_hrs":     sum_ideal_hrs,
        "actual_hrs":    sum_actual_hrs or None,
        "actual_out_kg": sum_output or None,
        "ideal_rate":    sum_ideal_rates,
        "avg_hr":        sum_avg_hr or None,
        "util_pct":      t_util_pct,
        "out_eff_pct":   t_out_eff_pct,
        "is_total":      True,
    })

    return rows


# ── Sections 3–6 : MATERIAL / M/C WISE / MONTHWISE / per-machine ──────────────

# Pipe-type groups (spec: CPVC(2) / UPVC(2) / AGRI/SWR(3) / OPVC(1) / COLUMN(1))
PIPE_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    ("CPVC",     ["M/C-1", "M/C-2"]),
    ("UPVC",     ["M/C-5", "M/C-6"]),
    ("AGRI/SWR", ["M/C-3", "M/C-4", "M/C-9"]),
    ("OPVC",     ["M/C-7"]),
    ("COLUMN",   ["M/C-8"]),
]


def _mc_monthly(records, fy: str) -> dict[str, dict[str, dict]]:
    """Accumulate {mc: {ym: {hrs, output_kg}}} from PIPE records.

    Only machines in MACHINE_ORDER are kept; unrecognised labels are dropped.
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    all_yms = set(fy_ym.values())
    result: dict = {mc: {ym: {"hrs": 0.0, "output_kg": 0.0}
                          for ym in fy_ym.values()}
                    for mc in MACHINE_ORDER}

    mc_set = {_norm_machine(mc): mc for mc in MACHINE_ORDER}

    for r in records:
        if not _is_primary_pipe_record(r):
            continue
        ym = _record_month(r)
        if not ym or ym not in all_yms:
            continue
        raw_mc  = r.machine or ""
        canon   = _norm_machine(raw_mc)
        mc_orig = mc_set.get(canon)
        if not mc_orig:
            continue
        result[mc_orig][ym]["hrs"]       += float(r.actual_hours or 0.0)
        result[mc_orig][ym]["output_kg"] += (
            float(r.total_count or 0.0) + float(r.reject_count or 0.0)
        )
    return result


def _build_section3_material(pipe_records, n_months: int) -> dict:
    """MATERIAL section: per pipe-type group totals.

    Columns: Qty | Run Hours | Actual Output (KG) | Ideal Output |
             Avg/Hr | Util% | Output Eff%

    Ideal Output for a group = sum over machines of (actual_hrs_m × ideal_rate_m).
    This gives "how much these machines could produce at ideal rates given the hours
    they actually ran" — the formula that reconciles with the workbook numbers.
    Output Eff% = actual_output / ideal_from_actual_hrs × 100.
    """
    mc_data: dict[str, dict] = {}    # {mc: {hrs, output}}
    for r in pipe_records:
        if not _is_primary_pipe_record(r):
            continue
        mc = _norm_machine(r.machine or "")
        if mc not in {_norm_machine(m) for m in MACHINE_ORDER}:
            continue
        mc_key = next((m for m in MACHINE_ORDER if _norm_machine(m) == mc), mc)
        d = mc_data.setdefault(mc_key, {"hrs": 0.0, "output": 0.0})
        d["hrs"]    += float(r.actual_hours or 0.0)
        d["output"] += float(r.total_count or 0.0) + float(r.reject_count or 0.0)

    group_rows: list = []
    t_qty = t_hrs = t_out = t_ideal_out = 0.0

    for type_name, machines in PIPE_TYPE_GROUPS:
        qty = len(machines)
        g_hrs = g_out = g_ideal_out = 0.0
        for mc in machines:
            d        = mc_data.get(mc, {"hrs": 0.0, "output": 0.0})
            ideal_hr = MACHINE_IDEAL_RATES.get(mc, 0)
            g_hrs    += d["hrs"]
            g_out    += d["output"]
            g_ideal_out += d["hrs"] * ideal_hr

        ideal_hrs = n_months * IDEAL_HOURS_PER_MONTH * qty
        avg_hr    = _safe_div(g_out, g_hrs)
        util_pct  = _safe_div(g_hrs, ideal_hrs) * 100 if ideal_hrs else None
        out_eff   = (_safe_div(g_out, g_ideal_out) * 100
                     if g_ideal_out else None)

        group_rows.append({
            "type":      type_name,
            "machines":  machines,
            "qty":       qty,
            "hrs":       g_hrs or None,
            "output_kg": g_out or None,
            "ideal_out": g_ideal_out or None,
            "ideal_hrs": ideal_hrs,
            "avg_hr":    avg_hr,
            "util_pct":  util_pct,
            "out_eff":   out_eff,
        })
        t_qty += qty; t_hrs += g_hrs; t_out += g_out; t_ideal_out += g_ideal_out

    t_ideal_hrs = n_months * IDEAL_HOURS_PER_MONTH * int(t_qty)
    total_row = {
        "type": "TOTAL", "qty": int(t_qty),
        "hrs": t_hrs or None, "output_kg": t_out or None,
        "ideal_out": t_ideal_out or None, "ideal_hrs": t_ideal_hrs,
        "avg_hr": _safe_div(t_out, t_hrs),
        "util_pct": (_safe_div(t_hrs, t_ideal_hrs) * 100 if t_ideal_hrs else None),
        "out_eff": (_safe_div(t_out, t_ideal_out) * 100 if t_ideal_out else None),
    }
    return {"groups": group_rows, "total_row": total_row}


def _build_section4_mc_wise(pipe_records, fy: str) -> dict:
    """M/C WISE pivot: months as rows, machines as column-pairs (HOURS | OUTPUT)."""
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    monthly = _mc_monthly(pipe_records, fy)

    month_rows: list = []
    t_mc: dict[str, dict] = {mc: {"hrs": 0.0, "out": 0.0} for mc in MACHINE_ORDER}

    for lbl in MONTH_LABELS:
        ym   = fy_ym[lbl]
        cols = {}
        for mc in MACHINE_ORDER:
            d = monthly.get(mc, {}).get(ym, {"hrs": 0.0, "output_kg": 0.0})
            h = d["hrs"] or None; o = d["output_kg"] or None
            cols[mc] = {"hrs": h, "out": o}
            if h: t_mc[mc]["hrs"] += h
            if o: t_mc[mc]["out"] += o
        month_rows.append({"month_lbl": lbl, "month_disp": fy_disp[lbl], "cols": cols})

    total_cols = {mc: {"hrs": t_mc[mc]["hrs"] or None,
                       "out": t_mc[mc]["out"] or None}
                  for mc in MACHINE_ORDER}
    return {"month_rows": month_rows, "total_cols": total_cols,
            "machines": MACHINE_ORDER}


def _build_section5_monthwise(pipe_records, fy: str, n_months: int) -> dict:
    """MONTHWISE: one row per machine–month with Ideal/Actual/Output/Ideal Output/Avg/Hr."""
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    monthly = _mc_monthly(pipe_records, fy)

    rows: list = []
    for mc in MACHINE_ORDER:
        ideal_rate = MACHINE_IDEAL_RATES.get(mc, 0)
        mc_rows: list = []
        for lbl in MONTH_LABELS:
            ym   = fy_ym[lbl]
            d    = monthly.get(mc, {}).get(ym, {"hrs": 0.0, "output_kg": 0.0})
            h    = d["hrs"] or None
            out  = d["output_kg"] or None
            avg  = _safe_div(out, h)
            i_out = (h * ideal_rate) if h and ideal_rate else None
            mc_rows.append({
                "month_lbl": lbl, "month_disp": fy_disp[lbl],
                "ideal_hrs": IDEAL_HOURS_PER_MONTH,
                "actual_hrs": h,
                "output_kg": out,
                "ideal_output": i_out,
                "avg_hr": avg,
            })
        rows.append({"machine": mc, "pipe_type": MACHINE_TYPES.get(mc, ""),
                     "ideal_rate": ideal_rate, "rows": mc_rows})
    return {"machines": rows}


def _build_section6_per_machine(pipe_records, fy: str, n_months: int) -> dict:
    """Per-machine detail: one table per M/C-1..M/C-9, monthly rows + TOTAL.

    Columns: Month | Ideal Hrs | Actual Hrs | Output (KG) | Ideal Output |
             Avg/Hr | Util% | Output Eff%

    Util%  = actual_hrs / ideal_hrs × 100
    Output Eff% = avg_hr / ideal_rate × 100
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    monthly = _mc_monthly(pipe_records, fy)

    machines: list = []
    for mc in MACHINE_ORDER:
        ideal_rate = MACHINE_IDEAL_RATES.get(mc, 0)
        t_h = t_out = 0.0
        month_rows: list = []

        for lbl in MONTH_LABELS:
            ym   = fy_ym[lbl]
            d    = monthly.get(mc, {}).get(ym, {"hrs": 0.0, "output_kg": 0.0})
            h    = d["hrs"] or None
            out  = d["output_kg"] or None
            avg  = _safe_div(out, h)
            i_out = (h * ideal_rate) if h and ideal_rate else None
            util_pct = (_safe_div(h, IDEAL_HOURS_PER_MONTH) * 100 if h else None)
            eff_pct  = (_safe_div(avg, ideal_rate) * 100
                        if avg is not None and ideal_rate else None)
            month_rows.append({
                "month_lbl": lbl, "month_disp": fy_disp[lbl],
                "ideal_hrs": IDEAL_HOURS_PER_MONTH,
                "actual_hrs": h, "output_kg": out, "ideal_output": i_out,
                "avg_hr": avg, "util_pct": util_pct, "eff_pct": eff_pct,
            })
            if h:   t_h   += h
            if out: t_out += out

        ideal_hrs_total = n_months * IDEAL_HOURS_PER_MONTH
        ideal_out_total = t_h * ideal_rate if (t_h and ideal_rate) else None
        t_avg = _safe_div(t_out, t_h)
        machines.append({
            "machine": mc, "pipe_type": MACHINE_TYPES.get(mc, ""),
            "ideal_rate": ideal_rate,
            "month_rows": month_rows,
            "total_row": {
                "ideal_hrs": ideal_hrs_total,
                "actual_hrs": t_h or None,
                "output_kg": t_out or None,
                "ideal_output": ideal_out_total,
                "avg_hr": t_avg,
                "util_pct": (_safe_div(t_h, ideal_hrs_total) * 100
                             if t_h else None),
                "eff_pct": (_safe_div(t_avg, ideal_rate) * 100
                            if t_avg is not None and ideal_rate else None),
            },
        })
    return {"machines": machines}


# ── Top-level builder ──────────────────────────────────────────────────────────

def build_pipe_summary(fy: str = "2627", through_ym: Optional[str] = None) -> dict:
    """Build all data for the Pipe M/C Summary management page.

    Returns a dict consumed by ``report_mgmt_pipe_summary.html``::

        {
            "fy":             str,
            "fy_label":       str,
            "error":          str | None,
            "section1": {
                "month_rows": [...],
                "total_row":  {...},
                "warnings":   [...],
            },
            "section2": {
                "fy2627": [...],   # per-machine rows + TOTAL
                "fy2526": [...],   # per-machine rows + TOTAL (from Pipe M/C 25-26 tab)
                "fy2627_label": str,
                "fy2526_label": str,
                "n_months": int,   # months of FY data available
            },
            "build_time_s": float,
            "failed_months": [str, ...],
        }
    """
    if fy not in _FY_YM:
        fy = "2627"

    fy_ym = _FY_YM[fy]
    full_yms = [fy_ym[lbl] for lbl in MONTH_LABELS]
    selected_through = through_ym or PIPE_REPORT_DEFAULT_THROUGH.get(fy)
    if selected_through not in full_yms:
        selected_through = PIPE_REPORT_DEFAULT_THROUGH.get(fy, full_yms[-1])
    report_yms = [ym for ym in full_yms if ym <= selected_through]

    # Serve from cache if fresh
    cache_key = (fy, selected_through)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached:
            ts, data = cached
            if time.monotonic() - ts < _CACHE_TTL:
                return data

    t0 = time.monotonic()

    try:
        import sheets as _sh
        token = _sh._get_access_token()
        if not token:
            return _error_result(fy, "Could not obtain Google access token")

        # ── Fetch PIPE records for the selected reporting window ────────────
        # PIPE daily records already apply the Report-5 ↔ Report-11 date-wise
        # reconciliation.  The annual grid is a verification source only and
        # must not feed this management report.
        try:
            records, daily_reports, _ = _sh.get_daily_records(report_yms)
        except Exception as exc:
            logger.exception("build_pipe_summary: get_daily_records failed")
            return _error_result(fy, f"Could not load PIPE production records: {exc}")

        all_pipe_records = [r for r in records if r.plant == "PIPE"]
        pipe_records = [
            r for r in all_pipe_records
            if _is_primary_pipe_record(r) and _record_month(r) in report_yms
        ]
        finishing_records = [
            r for r in all_pipe_records
            if bool(getattr(r, "is_finishing", False))
            and _record_month(r) in report_yms
        ]
        failed_pairs = next(
            (report["_failed_pairs"] for report in daily_reports
             if isinstance(report, dict) and "_failed_pairs" in report),
            [],
        )
        failed_yms = {ym for plant, ym in failed_pairs if plant == "PIPE"}

        # ── n_months: count months with any PIPE data ─────────────────────────
        yms_with_data = {getattr(r, "period", None) for r in pipe_records
                         if getattr(r, "period", None)}
        n_months = max(1, len([
            ym for ym in report_yms
            if ym in yms_with_data and ym not in failed_yms
        ]))

        # ── DASHBOARD tab (Employee Data Details) ─────────────────────────────
        dashboard: dict[str, dict] = {}
        dash_warning: Optional[str] = None
        try:
            dash_vals = _sh.read_values(EMPLOYEE_DATA_FILE_ID, DASHBOARD_TAB, token)
            dashboard = _read_dashboard_pipeline(dash_vals)
            if not dashboard:
                dash_warning = (
                    "DASHBOARD tab: PIPELINE row not found or no month columns "
                    "parsed — Labour and Paid Hours will show as blank."
                )
        except Exception as exc:
            dash_warning = f"DASHBOARD tab read failed: {exc}"
            logger.warning("build_pipe_summary: dashboard failed: %s", exc)

        # ── Pipe M/C 25-26 tab ────────────────────────────────────────────────
        mc2526_rows: list = []
        mc2526_warning: Optional[str] = None
        try:
            mc_vals = _sh.read_values(PIPE_MC_FILE_ID, PIPE_MC_2526_TAB, token)
            mc2526_rows = _parse_pipe_mc_2526(mc_vals, n_months)
            if not mc2526_rows:
                mc2526_warning = (
                    f"'{PIPE_MC_2526_TAB}' tab: could not parse machine data "
                    f"— FY25-26 comparison will be unavailable."
                )
        except Exception as exc:
            mc2526_warning = f"Pipe M/C 25-26 tab read failed: {exc}"
            logger.warning("build_pipe_summary: mc2526 failed: %s", exc)

        # ── Section 1 ─────────────────────────────────────────────────────────
        s1 = _build_section1(
            fy, pipe_records, dashboard, token, active_yms=set(report_yms)
        )
        if dash_warning:
            s1["warnings"].insert(0, dash_warning)
        if finishing_records:
            s1["warnings"].append(
                f"{len(finishing_records)} finishing PIPE record(s) were excluded "
                "from the primary extrusion M/C report."
            )
        for lbl in MONTH_LABELS:
            ym = fy_ym[lbl]
            if ym in failed_yms:
                s1["warnings"].append(
                    f"{lbl}: PIPE daily workbook ({ym}) could not be read; "
                    "its figures are excluded and this report is partial."
                )

        # ── Section 2 ─────────────────────────────────────────────────────────
        mc2627_rows = _build_section2_fy2627(pipe_records, n_months)

        s2_warnings: list[str] = []
        if mc2526_warning:
            s2_warnings.append(mc2526_warning)

        # ── Sections 3–6 ──────────────────────────────────────────────────────
        s3 = _build_section3_material(pipe_records, n_months)
        s4 = _build_section4_mc_wise(pipe_records, fy)
        s5 = _build_section5_monthwise(pipe_records, fy, n_months)
        s6 = _build_section6_per_machine(pipe_records, fy, n_months)

        data = {
            "fy":        fy,
            "fy_label":  _FY_LABEL.get(fy, f"FY {fy[:2]}-{fy[2:]}"),
            "error":     None,
            "section1":  s1,
            "section2": {
                "fy2627":       mc2627_rows,
                "fy2526":       mc2526_rows,
                "fy2627_label": (
                    f"{_FY_DISP[fy][MONTH_LABELS[0]]} – "
                    f"{_FY_DISP[fy][next(lbl for lbl in MONTH_LABELS if fy_ym[lbl] == selected_through)]} "
                    f"({_FY_LABEL.get(fy, f'FY {fy}')})"
                ),
                "fy2526_label": _FY_LABEL.get("2526", "FY 2025–26"),
                "n_months":     n_months,
                "warnings":     s2_warnings,
            },
            "section3":  s3,
            "section4":  s4,
            "section5":  s5,
            "section6":  s6,
            "failed_months": sorted(failed_yms),
            "through_ym": selected_through,
            "report_yms": report_yms,
            "build_time_s": round(time.monotonic() - t0, 2),
        }

    except Exception as exc:
        logger.exception("build_pipe_summary: unexpected error")
        return _error_result(fy, str(exc))

    with _cache_lock:
        _cache[cache_key] = (time.monotonic(), data)

    return data


def _error_result(fy: str, msg: str) -> dict:
    return {
        "fy": fy, "fy_label": _FY_LABEL.get(fy, fy),
        "error": msg, "section1": None, "section2": None,
        "build_time_s": 0.0,
    }
