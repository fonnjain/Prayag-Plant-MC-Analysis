"""
Management Report (C) — Group of Moulding M/C Summary
Route: /management-reports/gom-summary

SECTION 1 (SUMMARY worksheet):
  Identical to Section 2 of the (B) Moulding Summary — same 27 machines, same bands,
  same FY25-26 closed-annual block.  Produced by calling build_moulding_summary() from
  mgmt_moulding_summary and re-presenting its section2 output.  Do NOT reimplement; any
  divergence between the two pages is a data bug.
  Basis: NET output (rejection excluded).

SECTION 2 (SUMMARY-1 worksheet):
  Band × month matrix.  Basis: GROSS output = net + rejection.
  Avg. Per Output on Hours in the TOTAL row = total_gross_output ÷ total_hours (WEIGHTED
  mean) = 369,665.6 ÷ 35,972 = 10.28.
  This is DIFFERENT from the (B) SUMMARY tab where the TOTAL avg = SUM of band averages
  (76.10).  The two tabs in the same workbook use different conventions.  Do NOT
  "correct" either into the other; both are implemented exactly as their own tab has them.

SECTION 3 (band tabs 150 / 200 / 250 / 275 / 350 / 450):
  Machine × month matrix, one table per tonnage band.  Basis: GROSS output.
  Machine labels are 1-N WITHIN each band (band-relative numbering), not global
  M/C numbers.  Mould IDs shown come from the SUMMARY-tab roster.
  Each band TOTAL must reconcile to its SUMMARY-1 row.

Sources:
  Output, rejection, runner — Record.total_count, reject_count, runner_lumps
    (MOULDING daily path: Report-12 "Wt in Kgs" via PIPE key, R-38).
  Run hours — Record.actual_hours (joined from Report-5 via r5_runhours, R-38).
  Tonnage band — derived from Record.mould via _mould_to_band() imported from
    mgmt_moulding_summary.  Record.tonnage_band is blank for MOULDING records;
    do NOT rely on it.
  Machine roster (27 machines, incl. idle) — SUMMARY tab in the (B) Moulding
    workbook (MOULDING_WB_FILE_ID), already consumed by build_moulding_summary.
  FY25-26 closed-annual blocks — (B) SUMMARY-1 tab (R-03 exception), already
    parsed and returned by build_moulding_summary().

GOM page at app.py (route /reports/gom_summary):
  Uses load_report_records("gom") which returns GOM grid records (kind="gom_grid"),
  NOT MOULDING daily records.  The GOM grid parser DOES set tonnage_band on those
  records, so rollup_by_tonnage_band() works correctly there.  The blank tonnage_band
  for MOULDING daily records does NOT affect that existing page.

Ideal Hours = 500 per machine per month (R-16 flat placeholder, NOT from
baselines.json; must NOT be used to populate baselines.json).
"""
from __future__ import annotations

import re
import time
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ── Import helpers from (B) builder — do not duplicate ────────────────────────
from mgmt_moulding_summary import (
    _mould_to_band,  # extract '150'|'200'|… from mould code
    _mc_key,         # canonical 'M/C - N' from any label
    _num,
    _safe_div,
    BAND_ORDER,
    MOULDING_WB_FILE_ID,
    SUMMARY_TAB,
    IDEAL_HRS_PER_MC_PER_MONTH,
    _FY_YM,
    MOULDING_REPORT_DEFAULT_THROUGH,
    _parse_summary_roster,
    _record_roster_key,
    _roster_mould_map,
    build_moulding_summary,
)

# ── In-process cache (10-minute TTL) ─────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 600   # seconds


# ── Month label helpers ───────────────────────────────────────────────────────

def _ym_label(ym: str) -> str:
    """'2026-04' → 'APR'  (uppercase 3-letter month, FY-convention)."""
    import datetime
    dt = datetime.datetime.strptime(ym, "%Y-%m")
    return dt.strftime("%b").upper()


# ── Section 2: band × month GROSS matrix ─────────────────────────────────────

def _build_section2(
    records: list,
    active_months: list[str],
    roster: list[dict],
) -> dict:
    """Compute the SUMMARY-1-equivalent band × month gross matrix.

    Gross output = total_count + reject_count (net + rejection).

    Avg. Per Output on Hours convention:
      Per band, per month: gross ÷ hours.
      TOTAL row avg/hr   : total_gross ÷ total_hours  (WEIGHTED — sheet convention).
        This is 369,665.6 ÷ 35,972 = 10.28.
        Do NOT use the sum-of-band-averages convention (that is SUMMARY's tab convention).
    """
    # Band machine counts from roster (handles idle machines with 0 hours)
    band_mc_count: dict[str, int] = {}
    for entry in roster:
        b = entry["band"]
        band_mc_count[b] = band_mc_count.get(b, 0) + 1

    # Accumulate band × month
    from collections import defaultdict
    acc: dict[str, dict[str, dict]] = {
        band: {ym: {"hrs": 0.0, "gross": 0.0} for ym in active_months}
        for band in BAND_ORDER
    }
    total_by_month: dict[str, dict] = {ym: {"hrs": 0.0, "gross": 0.0} for ym in active_months}

    for r in records:
        if r.plant != "MOULDING":
            continue
        band = _mould_to_band(r.mould or r.machine or "")
        if band not in acc:
            continue
        ym = r.period
        if ym not in acc[band]:
            continue
        gross = float(r.total_count or 0.0) + float(r.reject_count or 0.0)
        hrs   = float(r.actual_hours or 0.0)
        acc[band][ym]["hrs"]   += hrs
        acc[band][ym]["gross"] += gross
        total_by_month[ym]["hrs"]   += hrs
        total_by_month[ym]["gross"] += gross

    # Build band rows
    band_rows = []
    band_totals: dict[str, dict] = {}   # band → {hrs, gross}

    for band in BAND_ORDER:
        mc_count = band_mc_count.get(band, 0)
        months_data = {}
        tot_hrs = tot_gross = 0.0
        for ym in active_months:
            d = acc[band][ym]
            h, g = d["hrs"], d["gross"]
            avg = _safe_div(g, h)
            months_data[ym] = {
                "hrs":     round(h, 2),
                "gross_kg": round(g, 2),
                "avg_hr":   round(avg, 2) if avg is not None else None,
            }
            tot_hrs   += h
            tot_gross += g
        tot_avg = _safe_div(tot_gross, tot_hrs)
        band_rows.append({
            "band":      band,
            "mc_count":  mc_count,
            "months":    months_data,
            "total": {
                "hrs":      round(tot_hrs,   2),
                "gross_kg": round(tot_gross, 2),
                "avg_hr":   round(tot_avg,   2) if tot_avg is not None else None,
            },
        })
        band_totals[band] = {"hrs": tot_hrs, "gross": tot_gross}

    # TOTAL row (across all bands and all months)
    grand_hrs = grand_gross = 0.0
    total_months_data = {}
    for ym in active_months:
        d = total_by_month[ym]
        h, g = d["hrs"], d["gross"]
        avg = _safe_div(g, h)
        total_months_data[ym] = {
            "hrs":      round(h, 2),
            "gross_kg": round(g, 2),
            "avg_hr":   round(avg, 2) if avg is not None else None,
        }
        grand_hrs   += h
        grand_gross += g

    grand_avg = _safe_div(grand_gross, grand_hrs)   # WEIGHTED (sheet convention for SUMMARY-1)

    total_row = {
        "mc_count": sum(band_mc_count.values()),
        "months":   total_months_data,
        "total": {
            "hrs":      round(grand_hrs,   2),
            "gross_kg": round(grand_gross, 2),
            # TOTAL avg/hr = total_gross ÷ total_hours (WEIGHTED mean).
            # This gives 369,665.6 ÷ 35,972 = 10.28.
            # This is DIFFERENT from the (B) SUMMARY convention where TOTAL avg/hr
            # = SUM of band averages (76.10).  Do NOT unify.
            "avg_hr":   round(grand_avg,   2) if grand_avg is not None else None,
        },
    }

    return {
        "band_rows":   band_rows,
        "total_row":   total_row,
        "months":      active_months,
        "month_labels": [_ym_label(ym) for ym in active_months],
    }


# ── Section 3: machine × month per band GROSS matrices ───────────────────────

def _build_section3(
    records: list,
    active_months: list[str],
    roster: list[dict],
) -> dict:
    """Per-band machine × month gross matrix.

    Machine labels use within-band numbering (1-N), NOT global M/C numbers.
    Mould IDs shown come from the SUMMARY-tab roster.
    Basis: GROSS output (net + rejection).

    Each band's TOTAL must reconcile to its Section 2 SUMMARY-1 row.
    """
    from collections import defaultdict

    # Build global-mc_key → roster entry lookup
    mc_entry: dict[str, dict] = {e["mc_key"]: e for e in roster}
    roster_by_mould = _roster_mould_map(roster)

    # Accumulate per (global mc_key, ym)
    acc: dict[str, dict[str, dict]] = {}
    for r in records:
        if r.plant != "MOULDING":
            continue
        key = _record_roster_key(r, roster_by_mould)
        if key not in mc_entry:
            continue
        ym = r.period
        if ym not in active_months:
            continue
        if key not in acc:
            acc[key] = {m: {"hrs": 0.0, "gross": 0.0} for m in active_months}
        gross = float(r.total_count or 0.0) + float(r.reject_count or 0.0)
        hrs   = float(r.actual_hours or 0.0)
        acc[key][ym]["hrs"]   += hrs
        acc[key][ym]["gross"] += gross

    # Build per-band tables
    by_band: dict[str, dict] = {}
    # Group roster entries by band in roster order
    band_entries: dict[str, list[dict]] = {b: [] for b in BAND_ORDER}
    for entry in roster:
        band_entries[entry["band"]].append(entry)

    for band in BAND_ORDER:
        entries = band_entries[band]
        machine_rows = []
        band_hrs = band_gross = 0.0

        for within_idx, entry in enumerate(entries, 1):
            global_key = entry["mc_key"]
            mould_id   = entry["mould_id"]
            mc_data    = acc.get(global_key, {m: {"hrs": 0.0, "gross": 0.0} for m in active_months})

            months_data = {}
            tot_hrs = tot_gross = 0.0
            for ym in active_months:
                d = mc_data.get(ym, {"hrs": 0.0, "gross": 0.0})
                h, g = d["hrs"], d["gross"]
                avg = _safe_div(g, h)
                months_data[ym] = {
                    "hrs":      round(h, 2),
                    "gross_kg": round(g, 2),
                    "avg_hr":   round(avg, 2) if avg is not None else None,
                }
                tot_hrs   += h
                tot_gross += g

            tot_avg = _safe_div(tot_gross, tot_hrs)
            machine_rows.append({
                "band_mc_num": within_idx,           # within-band label (1-N)
                "global_mc":   global_key,            # for debugging only
                "mould_id":    mould_id,              # from roster
                "months":      months_data,
                "total": {
                    "hrs":      round(tot_hrs,   2),
                    "gross_kg": round(tot_gross, 2),
                    "avg_hr":   round(tot_avg,   2) if tot_avg is not None else None,
                },
            })
            band_hrs   += tot_hrs
            band_gross += tot_gross

        band_avg = _safe_div(band_gross, band_hrs)
        total_row = {
            "mc_count": len(entries),
            "total": {
                "hrs":      round(band_hrs,   2),
                "gross_kg": round(band_gross, 2),
                "avg_hr":   round(band_avg,   2) if band_avg is not None else None,
            },
        }

        by_band[band] = {
            "machine_rows": machine_rows,
            "total_row":    total_row,
        }

    return {
        "by_band":     by_band,
        "months":      active_months,
        "month_labels": [_ym_label(ym) for ym in active_months],
    }


# ── Top-level builder ─────────────────────────────────────────────────────────

def build_gom_summary(fy: str = "2627", through_ym: Optional[str] = None) -> dict:
    """Build the full Group-of-Moulding Summary report data dict.

    Cached in-process for CACHE_TTL seconds.  Returns:
    {
        'fy', 'fy_label', 'error',
        'section1': section2 dict from build_moulding_summary (net basis, band YoY),
        'section2': {
            'label': str,
            'band_rows': [...], 'total_row': {...},
            'months': [...], 'month_labels': [...],
        },
        'section3': {
            'by_band': {band: {'machine_rows': [...], 'total_row': {...}}},
            'months': [...], 'month_labels': [...],
        },
        'build_time_s': float,
    }
    """
    selected_through = through_ym or MOULDING_REPORT_DEFAULT_THROUGH.get(fy)
    cache_key = f"gom_summary_{fy}_{selected_through}"
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]

    t0 = time.time()

    import sheets as _sh
    token = _sh._get_access_token()

    fy_yms   = _FY_YM.get(fy, _FY_YM["2627"])
    if selected_through not in fy_yms:
        selected_through = MOULDING_REPORT_DEFAULT_THROUGH.get(fy, fy_yms[-1])
    report_yms = [ym for ym in fy_yms if ym <= selected_through]
    fy_label = f"FY 20{fy[:2]}-{fy[2:]}"

    try:
        # ── 1. Section 1: reuse (B) builder ──────────────────────────────────
        # build_moulding_summary returns {section2: {fy2627: [...], ...}};
        # that is our SUMMARY tab equivalent (band-level YoY, NET basis).
        moulding_data = build_moulding_summary(fy, through_ym=selected_through)
        if moulding_data.get("error"):
            raise RuntimeError(
                f"(B) builder error (section1 unavailable): {moulding_data['error']}"
            )

        # Enrich FY26-27 TOTAL row with the correct weighted avg/hr (Report 6 / Cardinal Rule).
        # The (B) builder computes avg_hr as sum-of-band-averages = 76.10 (matching the SUMMARY
        # tab convention).  Report 6 must show the weighted mean (output ÷ actual_hrs = ~10.17)
        # and flag the divergence.  We add avg_hr_weighted without modifying avg_hr so that the
        # (B) page continues to match the sheet.
        raw_section2 = moulding_data["section2"]
        enriched_fy2627 = []
        for row in raw_section2.get("fy2627", []):
            r = dict(row)
            if r.get("is_total"):
                out = float(r.get("output_kg") or 0.0)
                hrs = float(r.get("actual_hrs") or 0.0)
                r["avg_hr_weighted"] = round(out / hrs, 2) if hrs > 0 else None
                r["avg_hr_sheet"]    = r.get("avg_hr")   # the 76.10 sum-of-averages
            enriched_fy2627.append(r)
        section1 = dict(raw_section2)
        section1["fy2627"] = enriched_fy2627

        # ── 2. Load roster (needed for machine counts and mould IDs) ─────────
        matrices = _sh.batch_get(MOULDING_WB_FILE_ID, [SUMMARY_TAB], token)
        summary_vals = matrices.get(SUMMARY_TAB, [])
        if not summary_vals:
            raise RuntimeError("Could not load SUMMARY tab from (B) Moulding workbook")

        # _parse_summary_roster returns (roster, warnings) — unpack both.
        roster, _roster_warnings = _parse_summary_roster(summary_vals)
        if not roster:
            raise RuntimeError("No machine rows found in SUMMARY tab — layout may have changed")

        # ── 3. Live records for FY26-27 ──────────────────────────────────────
        records_raw, daily_reports, _ = _sh.get_daily_records(report_yms)
        failed_pairs = next(
            (report["_failed_pairs"] for report in daily_reports
             if isinstance(report, dict) and "_failed_pairs" in report),
            [],
        )
        failed_yms = {ym for plant, ym in failed_pairs if plant in {"PIPE", "MOULDING"}}
        roster_by_mould = _roster_mould_map(roster)
        moulding_records = [
            r for r in records_raw
            if r.plant == "MOULDING"
            and getattr(r, "period", None) in report_yms
            and getattr(r, "period", None) not in failed_yms
            and not bool(getattr(r, "is_finishing", False))
            and _record_roster_key(r, roster_by_mould)
        ]

        active_months = sorted({r.period for r in moulding_records if r.period})
        if not active_months:
            raise RuntimeError("No MOULDING records found for FY26-27 periods")

        # ── 4. Section 2: band × month, GROSS basis ──────────────────────────
        s2 = _build_section2(moulding_records, active_months, roster)

        # Label: note GROSS basis explicitly (R-22)
        import datetime
        first_dt = datetime.datetime.strptime(active_months[0],  "%Y-%m")
        last_dt  = datetime.datetime.strptime(active_months[-1], "%Y-%m")
        period_str = (f"{first_dt.strftime('%b,%y')} – {last_dt.strftime('%b,%y')} "
                      f"(FY 2026-27) — GROSS basis (net + rejection) — recomputed")

        s2["label"] = period_str

        # ── 5. Section 3: machine × month per band, GROSS basis ──────────────
        s3 = _build_section3(moulding_records, active_months, roster)

        data = {
            "fy":        fy,
            "fy_label":  fy_label,
            "error":     None,
            "section1":  section1,   # from (B) builder — NET basis, band × FY YoY
            "section2":  s2,         # SUMMARY-1 equivalent — GROSS basis, band × month
            "section3":  s3,         # band tabs — GROSS basis, machine × month
            "band_order": BAND_ORDER,
            "through_ym": selected_through,
            "report_yms": report_yms,
            "failed_months": sorted(failed_yms),
            "warnings": [
                f"{ym}: daily Moulding source could not be read completely; "
                "its figures are excluded and this report is partial."
                for ym in sorted(failed_yms)
            ],
            "build_time_s": round(time.time() - t0, 2),
        }

    except Exception as exc:
        logger.exception("build_gom_summary failed: %s", exc)
        data = {
            "fy":       fy,
            "fy_label": fy_label,
            "error":    str(exc),
            "section1": {"fy2627": [], "fy2526": [], "warnings": [],
                         "fy2627_label": "", "fy2526_label": ""},
            "section2": {"band_rows": [], "total_row": {}, "months": [],
                         "month_labels": [], "label": ""},
            "section3": {"by_band": {}, "months": [], "month_labels": []},
            "band_order": BAND_ORDER,
            "build_time_s": round(time.time() - t0, 2),
        }

    with _cache_lock:
        if not data.get("failed_months"):
            _cache[cache_key] = {"ts": time.time(), "data": data}

    return data
