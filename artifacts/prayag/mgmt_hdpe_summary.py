"""mgmt_hdpe_summary.py — Data builder for HDPE M/C Summary management page.

Layout (SUMMARY tab, Annual 26-27 HDPE M/C Summary):
  MONTHS | M/C Run Hours | Actual Output (KG) [net] | Rejection (KG) | Rejection %age |
  Total Output with Rejection (KG) [gross] | Labour | Actual Paid Hours | Paid Wages |
  Paid Hours Devoted by Per Person | Per Hour Cost on Paid Hours | Per KG Labour Cost

Rows are LATEST-FIRST (MAR'27 at top → APR'26 → TOTAL at bottom).

Sources (Cardinal Rule — every figure computed from live sources, never copied):
  Run Hours       — get_daily_records(), plant=HDPE, r.actual_hours (DR matrix join)
  Actual Output   — get_daily_records(), plant=HDPE, r.total_count (MACHINE 1–6 block tabs)
  Rejection       — block tabs where column carries values; DR fallback via rejection_tracked
  Labour + Hours  — Segment Cost workbook, "HDPE Pipe" tab (R-11)
  Wages           — Segment Cost workbook, "HDPE Pipe" tab (R-11)

Key divergences and flags:
  HDPE-JUL  — Sheet SUMMARY reports July 0.00 kg because its SUMMARY feeds only from the
              Daily Report matrix, which is entirely unmaintained for HDPE (one machine,
              one month, all year). Block tabs show M/C-1 21,931.28 + M/C-2 516.76 = 22,448.04 kg.
              Flagged prominently; our figure is correct.
  MAY-0.80  — Sheet shows 1,370.00 kg, we compute 1,369.20 kg (0.80 kg rounding). Noted.
  HDPE-R42  — Segment Cost wages ₹13,78,857 vs sheet SUMMARY ₹8,16,185 for same
              headcount / hours (both three-month totals). Open divergence. We use Segment Cost.
  AWAITING  — JUL wages not yet entered in Segment Cost workbook.
  n/a-rej   — JUL M/C-2: output=516.76 kg, rejection column present but blank →
              rejection_tracked=False, reject_count=0. Rej% suppressed for July (R-08/#14).
  IDLE      — APR and JUN genuinely idle in both block tabs and Daily Report. Not "not recorded".
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

# Months verified idle in BOTH block tabs AND Daily Report.
# Not "data not yet available" — machines were genuinely not running.
# Distinguish from future months (→ "—") which are simply not recorded yet.
_IDLE_MONTHS: dict[str, frozenset] = {
    "2627": frozenset({"2026-04", "2026-06"}),   # APR and JUN (R-06)
}

# HDPE-R42: Segment Cost workbook vs sheet SUMMARY wages comparison.
# These are named, fixed divergence figures — an open question, not a live comparison.
# Our source is always the Segment Cost workbook (R-11).
#   Segment Cost HDPE Pipe tab (our source): APR 4,62,166 + MAY 4,24,919 + JUN 4,91,772
#   Sheet SUMMARY (comparison only):         APR 3,26,121 + MAY 2,68,928 + JUN 2,21,136
_HDPE_WAGES_SEG_COST   = 1_378_857   # ₹ from Segment Cost workbook (APR+MAY+JUN)
_HDPE_WAGES_SHEET      =   816_185   # ₹ from sheet SUMMARY (APR+MAY+JUN; JUL blank in both)
# Both sources carry the same 44 workers / 9,827 paid hours.

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

def _accumulate_hdpe(records) -> tuple[dict, dict, dict, dict, dict, dict]:
    """Accumulate per-ym totals from daily HDPE records.

    Returns:
      kh_net          {ym: float}  — block-tab net output (plant=HDPE)
      kh_reject       {ym: float}  — rejection (block tab value or DR fallback)
      kh_run_hrs      {ym: float}  — run hours from Daily Report join
      kh_dr_net       {ym: float}  — DR-basis net (reject_denominator sum) for R-23 comparison
      has_unknown_rej {ym: bool}   — True if any machine had output but blank rejection column
                                     (rejection_tracked=False AND reject_count==0 AND output>0)
      kh_by_machine   {ym: {machine: {net, reject, run_hrs, has_unknown_rej}}}  — per-machine grain
    """
    kh_net: dict[str, float]          = {}
    kh_reject: dict[str, float]       = {}
    kh_run_hrs: dict[str, float]      = {}
    kh_dr_net: dict[str, float]       = {}
    has_unknown_rej: dict[str, bool]  = {}
    kh_by_machine: dict               = {}   # ym -> machine_label -> {net, reject, run_hrs, has_unknown_rej}

    for r in records:
        ym = getattr(r, "period", None)
        if not ym or r.plant != "HDPE":
            continue

        tc = float(r.total_count      or 0.0)
        rc = float(r.reject_count     or 0.0)
        rh = float(r.actual_hours     or 0.0)
        rd = float(r.reject_denominator or 0.0)

        kh_net[ym]     = kh_net.get(ym, 0.0)    + tc
        kh_reject[ym]  = kh_reject.get(ym, 0.0) + rc
        kh_run_hrs[ym] = kh_run_hrs.get(ym, 0.0) + rh
        # reject_denominator = DR output per machine-date (sheets.py L1866/L1908).
        # For HDPE the DR is unmaintained (only M/C-1 May has actuals), so this
        # sums to ~1,370 for May and 0 for all other months — matching what the sheet's
        # SUMMARY tab shows (it reads only the DR). This is the R-23 comparison figure.
        kh_dr_net[ym]  = kh_dr_net.get(ym, 0.0) + rd

        # Unknown rejection: machine was producing but rejection column was blank.
        # Condition: rejection_tracked=False (DR had no row for this machine-date)
        #            AND total_count > 0 (machine was running)
        #            AND reject_count == 0 (block tab column present but empty).
        # This fires on JUL M/C-2 (516.76 kg output, blank rejection column).
        # It does NOT fire on JUL M/C-1 (reject_count=3782 from block tab → non-zero).
        unk_rej_mc = tc > 0.0 and not r.rejection_tracked and rc == 0.0
        if unk_rej_mc:
            has_unknown_rej[ym] = True

        machine = getattr(r, "machine", None) or "?"
        mc_d = kh_by_machine.setdefault(ym, {}).setdefault(
            machine, {"net": 0.0, "reject": 0.0, "run_hrs": 0.0, "has_unknown_rej": False}
        )
        mc_d["net"]             += tc
        mc_d["reject"]          += rc
        mc_d["run_hrs"]         += rh
        if unk_rej_mc:
            mc_d["has_unknown_rej"] = True

    return kh_net, kh_reject, kh_run_hrs, kh_dr_net, has_unknown_rej, kh_by_machine


# ── Section builder ────────────────────────────────────────────────────────────

def _build_section(
    fy: str,
    kh_net: dict,
    kh_reject: dict,
    kh_run_hrs: dict,
    kh_dr_net: dict,
    has_unknown_rej: dict,
    hdpe_labour: dict,
    kh_by_machine: dict | None = None,
) -> dict:
    """Build monthly rows (APR-MAR order) + TOTAL row + per-machine sections.

    by_machine: {machine_label: {month_rows, total_row}} — for HOURS/OUTPUT/MC-n tabs.
    Idle months (APR, JUN) carry is_idle=True in per-machine rows.
    JUL M/C-2: has_unknown_rej → rej_pct_gross = 'n/a', never 0.
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    idle    = _IDLE_MONTHS.get(fy, frozenset())

    month_rows: list = []

    for lbl in MONTH_LABELS:
        ym         = fy_ym.get(lbl)
        month_disp = fy_disp.get(lbl, lbl)
        is_idle    = ym in idle

        # net=None means no block-tab records for this month.
        # For idle months: that is expected and correct — show "0 (Idle)" not "—".
        # For future months: no records yet — show "—".
        net = kh_net.get(ym, 0.0) or None
        rej = kh_reject.get(ym, 0.0) or None
        rh  = kh_run_hrs.get(ym, 0.0) or None

        gross = ((net or 0.0) + (rej or 0.0)) or None

        # JUL M/C-2: rejection column present but blank → has_unknown_rej=True.
        # Rej% cannot be computed meaningfully when one machine's rejection is unknown
        # (showing a % based on only M/C-1 would be misleadingly low). Suppress it (R-08/#14).
        unk_rej = has_unknown_rej.get(ym, False)
        rej_pct_gross = (
            None if unk_rej
            else (_safe_div(rej, gross) * 100 if (rej and gross) else None)
        )
        rej_pct_net = (
            None if unk_rej
            else (_safe_div(rej, net) * 100 if (rej and net) else None)
        )

        # Labour from Segment Cost workbook (R-11)
        lab_row          = hdpe_labour.get(lbl, {})
        labour           = lab_row.get("n_labour")
        contractor_count = lab_row.get("n_contractor")
        paid_hrs         = lab_row.get("paid_hours")
        wages            = lab_row.get("paid_wages")
        contractor_wages = lab_row.get("contractor_wages")

        awaiting_wages = wages is None

        # Derived labour metrics
        devoted_per_person = _safe_div(paid_hrs, labour)
        per_hour_cost      = _safe_div(wages, paid_hrs)
        per_kg_cost        = _safe_div(wages, gross)   # wages per gross kg

        # R-23 equivalent: block-tab net vs DR-basis net (what the sheet's SUMMARY sees).
        # For HDPE the DR is unmaintained, so dr_net ≈ 0 for all months except MAY.
        # Threshold 1.0: JUL fires (22448 vs 0), MAY does not (0.80 kg rounding).
        r23_has_data = net is not None or is_idle
        dr_net_ym    = kh_dr_net.get(ym, 0.0) if r23_has_data else None
        dr_gross_ym  = (dr_net_ym + (rej or 0.0)) if r23_has_data else None
        r23_differs  = (
            r23_has_data
            and abs((net or 0.0) - (dr_net_ym or 0.0)) > 1.0
        )

        month_rows.append({
            "month_lbl":          lbl,
            "month_disp":         month_disp,
            "ym":                 ym,
            "is_idle":            is_idle,
            # Production (block tabs)
            "run_hrs":            rh,
            "net_kg":             net,
            "reject_kg":          rej,
            "gross_kg":           gross,
            "rej_pct_gross":      rej_pct_gross,
            "rej_pct_net":        rej_pct_net,
            "has_unknown_rej":    unk_rej,      # Rej% suppressed; "n/a" in template
            # Labour (Segment Cost workbook)
            "labour":             labour,
            "contractor_count":   contractor_count,
            "paid_hrs":           paid_hrs,
            "wages":              wages,
            "contractor_wages":   contractor_wages,
            "awaiting_wages":     awaiting_wages,
            # Derived
            "devoted_per_person": devoted_per_person,
            "per_hour_cost":      per_hour_cost,
            "per_kg_cost":        per_kg_cost,
            # R-23 comparison metadata
            "r23_has_data":       r23_has_data,
            "r23_differs":        r23_differs,
            "dr_net_kg":          dr_net_ym,
            "dr_gross_kg":        dr_gross_ym,
        })

    # ── TOTAL row ──────────────────────────────────────────────────────────────
    t_net      = _fsum(month_rows, "net_kg")
    t_rej      = _fsum(month_rows, "reject_kg")
    t_gross    = ((t_net or 0.0) + (t_rej or 0.0)) or None
    t_rh       = _fsum(month_rows, "run_hrs")
    t_labour   = _fsum(month_rows, "labour")
    t_contr    = _fsum(month_rows, "contractor_count")
    t_paid_hrs = _fsum(month_rows, "paid_hrs")
    t_wages    = _fsum(month_rows, "wages")
    t_contr_w  = _fsum(month_rows, "contractor_wages")

    # Total Rej%: suppress if any month had unknown rejection (same rule as per-month)
    any_unknown = any(r["has_unknown_rej"] for r in month_rows)
    t_rej_pct_gross = (
        None if any_unknown
        else (_safe_div(t_rej, t_gross) * 100 if (t_rej and t_gross) else None)
    )
    t_rej_pct_net = (
        None if any_unknown
        else (_safe_div(t_rej, t_net) * 100 if (t_rej and t_net) else None)
    )

    total_row = {
        "run_hrs":            t_rh,
        "net_kg":             t_net,
        "reject_kg":          t_rej,
        "gross_kg":           t_gross,
        "rej_pct_gross":      t_rej_pct_gross,
        "rej_pct_net":        t_rej_pct_net,
        "has_unknown_rej":    any_unknown,
        "labour":             t_labour,
        "contractor_count":   t_contr,
        "paid_hrs":           t_paid_hrs,
        "wages":              t_wages,
        "contractor_wages":   t_contr_w,
        "awaiting_wages":     any(r["awaiting_wages"] for r in month_rows),
        "devoted_per_person": _safe_div(t_paid_hrs, t_labour),
        "per_hour_cost":      _safe_div(t_wages, t_paid_hrs),
        "per_kg_cost":        _safe_div(t_wages, t_gross),
    }

    # ── Per-machine breakdown (for HOURS / OUTPUT / MC-n export tabs) ─────────────
    # Collect all machines, sorted by trailing number.
    _all_mc = sorted(
        {mc for ym_d in (kh_by_machine or {}).values() for mc in ym_d},
        key=lambda n: int(n.rsplit("-", 1)[-1].strip())
            if n.rsplit("-", 1)[-1].strip().isdigit() else 0,
    )
    by_machine: dict = {}
    for mc_label in _all_mc:
        mc_rows: list = []
        mc_net_t = mc_rej_t = mc_rh_t = 0.0
        mc_has_unk = False
        for lbl in MONTH_LABELS:
            ym        = fy_ym.get(lbl)
            is_mc_idle = ym in idle
            mc_d      = (kh_by_machine or {}).get(ym, {}).get(mc_label, {})
            net       = (mc_d.get("net")     or None) if not is_mc_idle else None
            rej       = (mc_d.get("reject")  or None) if not is_mc_idle else None
            rh        = (mc_d.get("run_hrs") or None) if not is_mc_idle else None
            unk       = mc_d.get("has_unknown_rej", False)
            mc_has_unk = mc_has_unk or unk
            gross     = ((net or 0.0) + (rej or 0.0)) or None
            rej_pct   = (
                "n/a" if unk
                else ((_safe_div(rej, gross) * 100) if (rej and gross) else None)
            )
            mc_rows.append({
                "month_lbl":       lbl, "ym": ym, "is_idle": is_mc_idle,
                "run_hrs":         rh,
                "net_kg":          net,
                "reject_kg":       rej,
                "gross_kg":        gross,
                "rej_pct_gross":   rej_pct,
                "has_unknown_rej": unk,
            })
            if net: mc_net_t += net
            if rej: mc_rej_t += rej
            if rh:  mc_rh_t  += rh
        mc_gross_t = (mc_net_t + mc_rej_t) or None
        by_machine[mc_label] = {
            "month_rows": mc_rows,
            "total_row": {
                "month_lbl":       "TOTAL", "ym": None, "is_idle": False,
                "run_hrs":         mc_rh_t  or None,
                "net_kg":          mc_net_t or None,
                "reject_kg":       mc_rej_t or None,
                "gross_kg":        mc_gross_t,
                "rej_pct_gross":   (
                    "n/a" if mc_has_unk
                    else (_safe_div(mc_rej_t, mc_gross_t) * 100
                          if (mc_rej_t and mc_gross_t) else None)
                ),
                "has_unknown_rej": mc_has_unk,
            },
        }

    return {
        "month_rows":         month_rows,
        "month_rows_display": list(reversed(month_rows)),   # latest-first for template
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

    # Daily records — plant=HDPE (block tabs + DR matrix join for run hours/rejection)
    try:
        daily_all, _daily_reports, _ = _sh.get_daily_records(all_yms)
    except Exception as exc:
        raise RuntimeError(f"Could not load daily HDPE records: {exc}") from exc

    # Extract failed (plant, ym) pairs — filter to HDPE only.
    _failed_pairs = next(
        (r["_failed_pairs"] for r in _daily_reports
         if isinstance(r, dict) and "_failed_pairs" in r),
        [],
    )
    failed_yms = sorted({ym for p, ym in _failed_pairs if p == "HDPE"})
    failed_month_details = _sh.daily_failed_pair_details(
        _daily_reports, plants={"HDPE"}
    )

    kh_net, kh_reject, kh_run_hrs, kh_dr_net, has_unknown_rej, kh_by_machine = \
        _accumulate_hdpe(daily_all)

    # Segment Cost workbook — dedicated "HDPE Pipe" tab (R-11, not a UNIT roll-up)
    try:
        seg_tabs = _mlp.load_segment_tabs(fy, token)
        hdpe_labour = seg_tabs.get("HDPE Pipe", {})
    except Exception as exc:
        logger.warning("build_hdpe_summary: load_segment_tabs failed: %s", exc)
        hdpe_labour = {}

    section = _build_section(
        fy, kh_net, kh_reject, kh_run_hrs, kh_dr_net, has_unknown_rej, hdpe_labour,
        kh_by_machine=kh_by_machine,
    )

    return {
        "fy":       fy,
        "fy_label": _FY_LABEL.get(fy, fy),
        "section":  section,
        # HDPE-R42 named divergence figures (open question, not live comparison)
        "hdpe_wages_seg_cost":  _HDPE_WAGES_SEG_COST,
        "hdpe_wages_sheet":     _HDPE_WAGES_SHEET,
        # Months whose daily read failed — result is not cached so next request
        # retries (R-06 Failure Mode #9).
        "failed_months": failed_yms,
        "failed_month_details": failed_month_details,
        "error":    None,
    }


def build_hdpe_summary(fy: str = "2627") -> dict:
    """Top-level builder, cached 10 minutes by FY."""
    with _cache_lock:
        cached = _cache.get(fy)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]

    try:
        result = _do_build(fy)
    except Exception as exc:
        logger.exception("build_hdpe_summary(%s) failed", fy)
        result = {
            "error":    str(exc),
            "fy":       fy,
            "fy_label": _FY_LABEL.get(fy, fy),
        }

    # Do NOT cache a result built from partial reads — some months are missing.
    if result.get("failed_months"):
        logger.warning(
            "build_hdpe_summary(%s): skipping cache — %d month(s) failed: %s",
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
