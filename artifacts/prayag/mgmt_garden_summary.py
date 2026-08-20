"""mgmt_garden_summary.py — Data builder for Garden Pipe M/C Summary management page.

Layout (SUMMARY tab):
  MONTHS | M/C Run Hours | Actual Output (KG) [net] | Rejection (KG) | Rejection %age |
  Total Output with Rejection (KG) [gross] | Labour | Actual Paid Hours | Paid Wages |
  Paid Hours Devoted by Per Person | Per Hour Cost on Paid Hours | Per KG Labour Cost

Rows are LATEST-FIRST (MAR'27 at top → APR'26 → TOTAL at bottom).

Sources (Cardinal Rule — every figure computed from live sources):
  Run Hours       — get_daily_records(), plant=GARDEN, r.actual_hours
  Actual Output   — get_daily_records(), plant=GARDEN, r.total_count (block tabs, KH only)
  Rejection       — get_daily_records(), plant=GARDEN, r.reject_count (Daily Report matrix)
  Labour + Hours  — Segment Cost workbook, "Garden Pipe" tab (R-11, via load_segment_tabs)
  Wages           — Segment Cost workbook, "Garden Pipe" tab (R-11); NOT the annual Garden workbook

GARDEN_WB (West Bengal plant) is SEPARATE — never merged into KH figures.
Its output is computed from daily records and disclosed in a separate panel.

Flags carried in data:
  R-23 — our block-tab output vs Daily Report output (both diverge; open with Anuj)
  R-42 — Segment Cost wages vs Annual Garden workbook wages; permanent banner
  May  — Daily Report unfilled (zero DR output); block tabs record real production
  AWAITING — wages None for a month (R-07/R-08)
"""
from __future__ import annotations

import logging
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
}

# R-42: Annual Garden workbook wages total (for comparison only).
# Source: "Annual 26-27 GARDEN PIPE M/C Summary" SUMMARY tab.
# NOT used as source — our wages come from Segment Cost workbook (R-11).
_GARDEN_ANNUAL_WAGES_TOTAL = 426_164       # ₹ from SUMMARY tab
_GARDEN_ANNUAL_WAGES_PER_KG = 2.97        # ₹/kg on DR-gross basis

# Month where Daily Report was not filled (May FY2627).
# Block tabs still have production; wages and headcount exist in Segment Cost.
_DR_UNFILLED_MONTHS: dict[str, frozenset] = {
    "2627": frozenset({"2026-05"}),
}

# ── In-process cache ───────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict = {}           # {fy: (timestamp, data)}
_CACHE_TTL  = 600           # seconds


# ── Utilities ──────────────────────────────────────────────────────────────────

def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _fsum(rows: list, key: str) -> Optional[float]:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return sum(vals) if vals else None


# ── Core data accumulator ──────────────────────────────────────────────────────

def _accumulate_garden(records) -> tuple[dict, dict, dict, dict, dict, dict, dict]:
    """Accumulate per-ym totals from daily Garden records.

    Returns:
      kh_net        {ym: float}  — block-tab net output (plant=GARDEN, KH only)
      kh_reject     {ym: float}  — rejection from Daily Report join
      kh_run_hrs    {ym: float}  — run hours from Daily Report
      wb_net        {ym: float}  — West Bengal net output (plant=GARDEN_WB)
      wb_reject     {ym: float}  — WB rejection
      kh_dr_net     {ym: float}  — DR-basis net output (sum of r.reject_denominator)
                                   0.0 for months where DR was not filled (e.g. May)
      kh_by_machine {ym: {machine: {net, reject, run_hrs}}}  — per-machine grain
    """
    kh_net: dict[str, float]    = {}
    kh_reject: dict[str, float] = {}
    kh_run_hrs: dict[str, float] = {}
    wb_net: dict[str, float]    = {}
    wb_reject: dict[str, float] = {}
    kh_dr_net: dict[str, float] = {}   # live DR-basis net — replaces hardcoded constants
    kh_by_machine: dict = {}           # ym -> machine_label -> {net, reject, run_hrs}

    for r in records:
        ym = getattr(r, "period", None)
        if not ym:
            continue

        if r.plant == "GARDEN":
            tc  = float(r.total_count      or 0.0)
            rc  = float(r.reject_count     or 0.0)
            rh  = float(r.actual_hours     or 0.0)
            rd  = float(r.reject_denominator or 0.0)

            kh_net[ym]    = kh_net.get(ym, 0.0)    + tc
            kh_reject[ym] = kh_reject.get(ym, 0.0) + rc
            kh_run_hrs[ym] = kh_run_hrs.get(ym, 0.0) + rh
            # r.reject_denominator = DR output per machine-date (sheets.py L1866/L1908).
            # Summing this over all GARDEN records for a month gives DR-basis net output.
            # For months where the DR was not filled (May), all records default to 0.0,
            # so kh_dr_net[ym] = 0.0 — correct: the DR genuinely shows zero for that month.
            kh_dr_net[ym] = kh_dr_net.get(ym, 0.0) + rd

            machine = getattr(r, "machine", None) or "?"
            mc_d = kh_by_machine.setdefault(ym, {}).setdefault(
                machine, {"net": 0.0, "reject": 0.0, "run_hrs": 0.0}
            )
            mc_d["net"]     += tc
            mc_d["reject"]  += rc
            mc_d["run_hrs"] += rh

        elif r.plant == "GARDEN_WB":
            wb_net[ym]    = wb_net.get(ym, 0.0)    + float(r.total_count  or 0.0)
            wb_reject[ym] = wb_reject.get(ym, 0.0) + float(r.reject_count or 0.0)

    return kh_net, kh_reject, kh_run_hrs, wb_net, wb_reject, kh_dr_net, kh_by_machine


# ── Section builder ────────────────────────────────────────────────────────────

def _build_section(
    fy: str,
    kh_net: dict,
    kh_reject: dict,
    kh_run_hrs: dict,
    wb_net: dict,
    wb_reject: dict,
    garden_labour: dict,
    kh_dr_net: dict,
    kh_by_machine: dict | None = None,
) -> dict:
    """Build monthly rows (APR-MAR order) + TOTAL row + per-machine sections.

    month_rows_display is the same list reversed (MAR→APR) for template rendering.
    by_machine: {machine_label: {month_rows, total_row}} — for HOURS/OUTPUT/MC-n tabs.
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    dr_unfilled = _DR_UNFILLED_MONTHS.get(fy, frozenset())

    month_rows: list = []

    for lbl in MONTH_LABELS:
        ym         = fy_ym.get(lbl)
        month_disp = fy_disp.get(lbl, lbl)

        net = kh_net.get(ym, 0.0)     or None
        rej = kh_reject.get(ym, 0.0)  or None
        rh  = kh_run_hrs.get(ym, 0.0) or None

        gross          = ((net or 0.0) + (rej or 0.0)) or None
        rej_pct_gross  = _safe_div(rej, gross)  * 100 if (rej and gross)  else None
        rej_pct_net    = _safe_div(rej, net)    * 100 if (rej and net)    else None

        # Labour from Segment Cost workbook (R-11)
        lab_row          = garden_labour.get(lbl, {})
        labour           = lab_row.get("n_labour")
        contractor_count = lab_row.get("n_contractor")
        paid_hrs         = lab_row.get("paid_hours")
        wages            = lab_row.get("paid_wages")
        contractor_wages = lab_row.get("contractor_wages")

        # R-07/R-08: wages None → AWAITING SOURCE DATA
        awaiting_wages = wages is None

        # Derived labour metrics
        devoted_per_person = _safe_div(paid_hrs, labour)
        per_hour_cost      = _safe_div(wages, paid_hrs)
        per_kg_cost        = _safe_div(wages, gross)   # wages per gross kg

        # R-23: compare our block-tab net vs Daily Report net (live from reject_denominator).
        # r23_has_data fires whenever we have block-tab records for this month —
        # the DR figure (even if 0 because the DR was unfilled) is meaningful to compare.
        r23_has_data = net is not None
        dr_net_ym   = kh_dr_net.get(ym, 0.0) if r23_has_data else None
        # DR gross = DR net + rejection (both from the Daily Report source)
        dr_gross_ym = (dr_net_ym + (rej or 0.0)) if r23_has_data else None
        r23_differs  = (
            r23_has_data
            and abs((net or 0.0) - (dr_net_ym or 0.0)) > 1.0
        )

        # May: Daily Report unfilled flag
        may_dr_unfilled = ym in dr_unfilled

        # WB figures for disclosure
        wb_net_ym = wb_net.get(ym, 0.0) or None
        wb_rej_ym = wb_reject.get(ym, 0.0) or None

        month_rows.append({
            "month_lbl":         lbl,
            "month_disp":        month_disp,
            "ym":                ym,
            # Production (KH, block tabs)
            "run_hrs":           rh,
            "net_kg":            net,
            "reject_kg":         rej,
            "gross_kg":          gross,
            "rej_pct_gross":     rej_pct_gross,
            "rej_pct_net":       rej_pct_net,
            # Labour (Segment Cost workbook)
            "labour":            labour,
            "contractor_count":  contractor_count,
            "paid_hrs":          paid_hrs,
            "wages":             wages,
            "contractor_wages":  contractor_wages,
            "awaiting_wages":    awaiting_wages,
            # Derived
            "devoted_per_person": devoted_per_person,
            "per_hour_cost":     per_hour_cost,
            "per_kg_cost":       per_kg_cost,
            # R-23 divergence metadata
            "r23_has_data":      r23_has_data,
            "r23_differs":       r23_differs,
            "dr_net_kg":         dr_net_ym,
            "dr_gross_kg":       dr_gross_ym,
            # May flag
            "may_dr_unfilled":   may_dr_unfilled,
            # WB disclosure
            "wb_net_kg":         wb_net_ym,
            "wb_reject_kg":      wb_rej_ym,
        })

    # ── TOTAL row ──────────────────────────────────────────────────────────────
    t_net     = _fsum(month_rows, "net_kg")
    t_rej     = _fsum(month_rows, "reject_kg")
    t_gross   = ((t_net or 0.0) + (t_rej or 0.0)) or None
    t_rh      = _fsum(month_rows, "run_hrs")
    t_labour  = _fsum(month_rows, "labour")
    t_contractor = _fsum(month_rows, "contractor_count")
    t_paid_hrs   = _fsum(month_rows, "paid_hrs")
    t_wages      = _fsum(month_rows, "wages")
    t_contr_w    = _fsum(month_rows, "contractor_wages")

    # WB totals for disclosure
    t_wb_net = _fsum(month_rows, "wb_net_kg")
    t_wb_rej = _fsum(month_rows, "wb_reject_kg")

    # DR-basis totals (live accumulation — for the banner and R-23 comparison)
    t_dr_net = sum(
        kh_dr_net.get(ym, 0.0)
        for ym in [fy_ym.get(lbl) for lbl in MONTH_LABELS]
        if ym and kh_net.get(ym) is not None   # only months with block-tab records
    )

    total_row = {
        "run_hrs":            t_rh,
        "net_kg":             t_net,
        "reject_kg":          t_rej,
        "gross_kg":           t_gross,
        "rej_pct_gross":      _safe_div(t_rej, t_gross) * 100 if (t_rej and t_gross) else None,
        "rej_pct_net":        _safe_div(t_rej, t_net)   * 100 if (t_rej and t_net)   else None,
        "labour":             t_labour,
        "contractor_count":   t_contractor,
        "paid_hrs":           t_paid_hrs,
        "wages":              t_wages,
        "contractor_wages":   t_contr_w,
        "awaiting_wages":     any(r["awaiting_wages"] for r in month_rows),
        "devoted_per_person": _safe_div(t_paid_hrs, t_labour),
        "per_hour_cost":      _safe_div(t_wages, t_paid_hrs),
        "per_kg_cost":        _safe_div(t_wages, t_gross),
        "wb_net_kg":          t_wb_net,
        "wb_reject_kg":       t_wb_rej,
        "dr_net_kg":          t_dr_net,   # for banner: total DR-basis net across active months
    }

    # ── Per-machine breakdown (for HOURS / OUTPUT / MC-n export tabs) ─────────────
    # Collect all machines seen across all months, sorted by trailing number.
    _all_mc = sorted(
        {mc for ym_d in (kh_by_machine or {}).values() for mc in ym_d},
        key=lambda n: int(n.rsplit("-", 1)[-1].strip())
            if n.rsplit("-", 1)[-1].strip().isdigit() else 0,
    )
    by_machine: dict = {}
    for mc_label in _all_mc:
        mc_month_rows: list = []
        mc_net_t = mc_rej_t = mc_rh_t = 0.0
        for lbl in MONTH_LABELS:
            ym     = fy_ym.get(lbl)
            mc_d   = (kh_by_machine or {}).get(ym, {}).get(mc_label, {})
            net    = mc_d.get("net")    or None
            rej    = mc_d.get("reject") or None
            rh     = mc_d.get("run_hrs") or None
            gross  = ((net or 0.0) + (rej or 0.0)) or None
            rp_g   = (_safe_div(rej, gross) * 100) if (rej and gross) else None
            mc_month_rows.append({
                "month_lbl": lbl, "ym": ym,
                "run_hrs":      rh,
                "net_kg":       net,
                "reject_kg":    rej,
                "gross_kg":     gross,
                "rej_pct_gross": rp_g,
            })
            if net: mc_net_t += net
            if rej: mc_rej_t += rej
            if rh:  mc_rh_t  += rh
        mc_gross_t = (mc_net_t + mc_rej_t) or None
        by_machine[mc_label] = {
            "month_rows": mc_month_rows,
            "total_row": {
                "month_lbl":    "TOTAL", "ym": None,
                "run_hrs":      mc_rh_t  or None,
                "net_kg":       mc_net_t or None,
                "reject_kg":    mc_rej_t or None,
                "gross_kg":     mc_gross_t,
                "rej_pct_gross": (_safe_div(mc_rej_t, mc_gross_t) * 100)
                    if (mc_rej_t and mc_gross_t) else None,
            },
        }

    return {
        "month_rows":         month_rows,
        "month_rows_display": list(reversed(month_rows)),  # latest-first for template
        "total_row":          total_row,
        "by_machine":         by_machine,
    }


# ── Top-level builder ──────────────────────────────────────────────────────────

def _do_build(fy: str) -> dict:
    import sheets as _sh
    import mgmt_labour_power as _mlp

    token   = _sh._get_access_token()
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    all_yms = list(fy_ym.values())

    # Daily records — Garden KH + WB
    try:
        daily_all, _daily_reports, _ = _sh.get_daily_records(
            all_yms, source_plants={"GARDEN", "GARDEN_WB"}
        )
    except Exception as exc:
        raise RuntimeError(f"Could not load daily Garden records: {exc}") from exc

    # Extract failed (plant, ym) pairs — filter to Garden plants only.
    _failed_pairs = next(
        (r["_failed_pairs"] for r in _daily_reports
         if isinstance(r, dict) and "_failed_pairs" in r),
        [],
    )
    failed_yms = sorted({ym for p, ym in _failed_pairs if p in {"GARDEN", "GARDEN_WB"}})
    failed_month_details = _sh.daily_failed_pair_details(
        _daily_reports, plants={"GARDEN", "GARDEN_WB"}
    )

    kh_net, kh_reject, kh_run_hrs, wb_net, wb_reject, kh_dr_net, kh_by_machine = \
        _accumulate_garden(daily_all)

    # Segment Cost workbook — "Garden Pipe" tab (R-11)
    try:
        seg_tabs = _mlp.load_segment_tabs(fy, token)
        garden_labour = seg_tabs.get("Garden Pipe", {})
    except Exception as exc:
        logger.warning("build_garden_summary: load_segment_tabs failed: %s", exc)
        garden_labour = {}

    section = _build_section(
        fy, kh_net, kh_reject, kh_run_hrs, wb_net, wb_reject, garden_labour, kh_dr_net,
        kh_by_machine=kh_by_machine,
    )

    return {
        "fy":       fy,
        "fy_label": _FY_LABEL.get(fy, fy),
        "section":  section,
        "annual_wages_ref":      _GARDEN_ANNUAL_WAGES_TOTAL,
        "annual_wages_per_kg":   _GARDEN_ANNUAL_WAGES_PER_KG,
        # Months whose daily read failed — result is not cached so next request
        # retries (R-06 Failure Mode #9).
        "failed_months": failed_yms,
        "failed_month_details": failed_month_details,
        "error":    None,
    }


def build_garden_summary(fy: str = "2627") -> dict:
    """Top-level builder, cached 10 minutes by FY."""
    with _cache_lock:
        cached = _cache.get(fy)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]

    try:
        result = _do_build(fy)
    except Exception as exc:
        logger.exception("build_garden_summary(%s) failed", fy)
        result = {
            "error":    str(exc),
            "fy":       fy,
            "fy_label": _FY_LABEL.get(fy, fy),
        }

    # Do NOT cache a result built from partial reads — some months are missing.
    if result.get("failed_months"):
        logger.warning(
            "build_garden_summary(%s): skipping cache — %d month(s) failed: %s",
            fy, len(result["failed_months"]), result["failed_months"],
        )
        return result

    with _cache_lock:
        _cache[fy] = (time.time(), result)
    return result


def invalidate_cache(fy: str = "2627") -> None:
    """Evict cached data so the next request re-reads from Sheets."""
    with _cache_lock:
        _cache.pop(fy, None)
