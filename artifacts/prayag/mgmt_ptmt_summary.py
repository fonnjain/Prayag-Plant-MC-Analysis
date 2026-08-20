"""
Management Report 11 — PTMT Moulds Summary (FY 2026-27).

Route: /management-reports/ptmt-moulds-summary
Source: PTMT daily Records (Report-5 per-machine matrix), excluding the
        grinding group (R-22).

Basis note
----------
Daily basis: our records give nett output stored in Record.total_count.
Summing total_count for non-grinding PTMT machines gives 524,465 kg (APR–JUL).
Annual worksheet basis: 537,109 kg — differs in June only (R-24, open with
the data owner).  Both bases are accepted per PRAYAG_RULES Part 4.

Column sourcing
---------------
Sourced from Records:
  • Mould Run Hours         — Record.actual_hours
  • Nett Output (KG)        — Record.total_count (nett, per parser)
  • Rejection (KG)          — Record.reject_count
  • Runner Produce (KG)     — Record.runner_lumps  ⚠ combines runner + lumps
  • Runner %age             — runner_lumps / total_count
  • Rejection %age          — reject_count / total_count

Not sourced (blank in report, never zero — R-07/R-08):
  • No. of Run Moulds       — mould count not in daily pipeline
  • Av. Run Hour Per Mould  — requires moulds count (above)
  • Lumps (KG)              — inseparable from runner_lumps
  • 100% Wastage %age       — formula (lumps / nett) requires separate lumps
  • Total Grinder Working   — grinding excluded from plant output (R-22)
  • Labour (headcount)      — not in Records; needs manpower sheet
  • Actual Paid Hours       — not in Records
  • Paid Wages              — not in Records (July wages pending)
  • Paid Hrs Devoted/Person — not in Records
  • Per Hour Cost           — not in Records
  • Per KG Labour Cost      — not in Records (AWAITING July wages)

Source sheet TOTAL defects (spec §"Three TOTAL cells add monthly values"):
  • Av. Run Hour Per Mould  sheet: 272.92 (sum), correct: 75,083 ÷ 1,105 = 67.95
  • Paid Hrs / Person       sheet: 1,121  (sum), correct: 59,967 ÷ 215    = 278.9
  • Per Hour Cost           sheet: 137    (sum), correct: 20,09,948 ÷ 59,967 = 33.52
  These columns are blank in our output; the correct formulas are documented here.
  Rejection %age TOTAL is correctly computed as a ratio in the source sheet.

Per KG Labour Cost TOTAL: sheet shows 3.53; July wages are blank so neither
  (Apr–Jun wages ÷ Apr–Jul output = 3.74) nor (Apr–Jun wages ÷ Apr–Jun output = 5.51)
  reproduces it.  Rendered AWAITING until all wages are available.

Do NOT touch: _PTMT_R24_NOTES (reused from mgmt_labour_power) · auth.py ·
  mp_*.py · pipe_reconcile.py · tank_reconcile.py · mgmt_moulding_summary's
  eleven exported symbols.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── Avoid circular imports: import heavy modules lazily ───────────────────────
_sheets_mod   = None
_sources_mod  = None


def _sh():
    global _sheets_mod
    if _sheets_mod is None:
        import sheets as _s
        _sheets_mod = _s
    return _sheets_mod


def _src():
    global _sources_mod
    if _sources_mod is None:
        import sources as _s
        _sources_mod = _s
    return _sources_mod


# ── FY constants ──────────────────────────────────────────────────────────────
# Month labels in calendar order (APR first).
_MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]

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

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict       = {}
_cache_lock        = threading.Lock()
_CACHE_TTL: int    = 600   # seconds


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ptmt_group(code: str) -> Optional[str]:
    """Return the PTMT_GROUPS key for a machine code, or None."""
    for grp, codes in _src().PTMT_GROUPS.items():
        if code in codes:
            return grp
    return None


def _safe_div(num: float, den: float) -> Optional[float]:
    return (num / den) if den else None


# ── Per-month computation ─────────────────────────────────────────────────────

def _build_month_row(ym: str, fy: str, abbr: str) -> dict:
    """Aggregate PTMT Records for *ym* into summary metrics.

    Only machines in non-grinding PTMT groups are included (R-22).
    Record.total_count is the nett output for PTMT (parser stores nett).
    Record.runner_lumps combines runner produce and lumps — they are not
    separately available in the daily pipeline.
    """
    all_records, reports, _ = _sh().get_daily_records([ym])
    failed_pairs = next(
        (report["_failed_pairs"] for report in reports
         if isinstance(report, dict) and "_failed_pairs" in report),
        [],
    )
    if ("PTMT", ym) in failed_pairs:
        failure_details = _sh().daily_failed_pair_details(reports, plants={"PTMT"})
        return {
            "ym": ym, "abbr": abbr, "disp": _FY_DISP[fy][abbr],
            "has_data": False, "partial_source": True,
            "partial_reason": failure_details[0] if failure_details else None,
        }

    # Records already have r.segment and r.is_finishing set by the PTMT
    # parser (sheets.py:2468-2471). Exclude grinding (is_finishing=True).
    hours      = 0.0
    output_kg  = 0.0
    reject_kg  = 0.0
    runner_kg  = 0.0
    has_any    = False

    for r in all_records:
        if getattr(r, "plant", "") != "PTMT":
            continue
        if getattr(r, "is_finishing", False):
            continue           # exclude grinding (R-22)
        hrs  = float(r.actual_hours  or 0.0)
        tot  = float(r.total_count   or 0.0)  # GROSS (mgmt_labour_power.py:1075)
        rej  = float(r.reject_count  or 0.0)
        run  = float(r.runner_lumps  or 0.0)
        hours     += hrs
        output_kg += (tot - rej)              # nett = gross − rejection
        reject_kg += rej
        runner_kg += run
        has_any = True

    if not has_any or output_kg == 0.0:
        # No data for this month yet (future month or missing source)
        return {
            "ym": ym, "abbr": abbr, "disp": _FY_DISP[fy][abbr],
            "has_data": False,
        }

    return {
        "ym":        ym,
        "abbr":      abbr,
        "disp":      _FY_DISP[fy][abbr],
        "has_data":  True,
        # Sourced
        "hours":      hours      or None,
        "output_kg":  output_kg,
        "reject_kg":  reject_kg  or None,
        "runner_kg":  runner_kg  if runner_kg else None,
        # Computed ratios — runner_pct is None when runner_kg has no data
        "reject_pct": _safe_div(reject_kg, output_kg),
        "runner_pct": _safe_div(runner_kg, output_kg) if runner_kg else None,
        # Not sourced — always None (blank, never 0 — R-07/R-08)
        "moulds":           None,
        "av_hr_per_mould":  None,
        "lumps_kg":         None,
        "wastage_pct":      None,
        "grinder_kg":       None,
        "labour":           None,
        "paid_hours":       None,
        "wages":            None,   # may be "AWAITING" for some months
        "paid_hrs_pp":      None,
        "cost_per_hr":      None,
        "cost_per_kg":      None,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_ptmt_summary(fy: str = "2627") -> dict:
    """Build the full PTMT Moulds Summary dict for *fy*.

    Returns
    -------
    dict with keys:
      fy, fy_label, rows (list, latest-first), total (dict or None),
      has_data (bool), sheet_total_bugs (list of dicts describing the
      three sum-of-monthly defects in the source sheet's TOTAL row),
      r24_notes (the _PTMT_R24_NOTES mapping).
    """
    if fy not in _FY_YM:
        fy = "2627"

    cache_key = f"ptmt_summary_{fy}"
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and (time.time() - hit["_ts"]) < _CACHE_TTL:
            return hit["data"]

    # Import R-24 notes (reuse from Report 1 — do not duplicate)
    from mgmt_labour_power import _PTMT_R24_NOTES

    ym_map   = _FY_YM[fy]
    # Latest-first display order: MAR → APR
    month_order = list(reversed(_MONTH_LABELS))

    rows = []
    for abbr in month_order:
        ym  = ym_map[abbr]
        row = _build_month_row(ym, fy, abbr)
        row["r24"] = _PTMT_R24_NOTES.get((fy, ym))
        rows.append(row)

    data_rows = [r for r in rows if r["has_data"]]
    has_data  = bool(data_rows)
    failed_months = [r["ym"] for r in rows if r.get("partial_source")]
    failed_month_details = [
        r["partial_reason"] for r in rows
        if r.get("partial_source") and r.get("partial_reason")
    ]

    total: Optional[dict] = None
    if has_data:
        t_hours  = sum(r.get("hours",  0.0) or 0.0 for r in data_rows) or None
        t_output = sum(r.get("output_kg", 0.0)      for r in data_rows)
        t_reject = sum(r.get("reject_kg", 0.0) or 0.0 for r in data_rows) or None
        t_runner = sum(r.get("runner_kg", 0.0) or 0.0 for r in data_rows) or None

        total = {
            "ym":        "TOTAL",
            "abbr":      "TOTAL",
            "disp":      "TOTAL",
            "has_data":  True,
            "is_total":  True,
            # Sourced totals
            "hours":     t_hours,
            "output_kg": t_output if t_output else None,
            "reject_kg": t_reject,
            "runner_kg": t_runner,
            # Ratios RECOMPUTED from totals (not summed — cardinal rule)
            "reject_pct": _safe_div(t_reject or 0.0, t_output) if t_output else None,
            "runner_pct": _safe_div(t_runner, t_output) if (t_output and t_runner) else None,
            # Not sourced
            "moulds":          None,
            "av_hr_per_mould": None,
            "lumps_kg":        None,
            "wastage_pct":     None,
            "grinder_kg":      None,
            "labour":          None,
            "paid_hours":      None,
            "wages":           "AWAITING",   # July wages pending; can't compute
            "paid_hrs_pp":     None,
            "cost_per_hr":     None,
            "cost_per_kg":     "AWAITING",   # period mismatch — see module docstring
        }

    # ── Document the three sum-of-monthly defects from the source sheet ──────
    # These columns are blank in our output, so we can't "demonstrate" the
    # correct figures from our own pipeline, but we document the defect.
    sheet_total_bugs = [
        {
            "col":      "Av. Run Hour Per Mould",
            "sheet":    272.92,
            "how":      "74.89 + 67.27 + 56.26 + 74.50 (sum of monthly)",
            "correct":  67.95,
            "formula":  "75,083 ÷ 1,105 (annual basis)",
            "sourced":  False,
            "note":     "Our moulds count is not in the daily pipeline — column blank.",
        },
        {
            "col":      "Paid Hrs Devoted by Per Person",
            "sheet":    1121,
            "how":      "301 + 273 + 297 + 251 (sum of monthly)",
            "correct":  278.9,
            "formula":  "59,967 ÷ 215 (annual basis)",
            "sourced":  False,
            "note":     "Paid hours and headcount not in Records — columns blank.",
        },
        {
            "col":      "Per Hour Cost on Paid Hours",
            "sheet":    137,
            "how":      "0 + 46 + 44 + 47 (sum of monthly)",
            "correct":  33.52,
            "formula":  "₹20,09,948 ÷ 59,967 (annual basis)",
            "sourced":  False,
            "note":     "Wages and paid hours not in Records — columns blank.",
        },
    ]

    result = {
        "fy":              fy,
        "fy_label":        "FY 2026-27" if fy == "2627" else fy,
        "rows":            rows,
        "total":           total,
        "has_data":        has_data,
        "sheet_total_bugs": sheet_total_bugs,
        "r24_notes":       _PTMT_R24_NOTES,
        "failed_months":   failed_months,
        "failed_month_details": failed_month_details,
        "warnings": [
            f"{ym}: PTMT daily source could not be read completely; its figures "
            "are excluded and this report is partial."
            for ym in failed_months
        ],
        # Per-KG Labour Cost TOTAL note
        "cost_per_kg_note": (
            "Sheet TOTAL 3.53 — derivation unclear: "
            "₹20,09,948 (Apr–Jun wages) ÷ 537,109 (Apr–Jul output) = 3.74 ; "
            "₹20,09,948 (Apr–Jun wages) ÷ 3,64,469 (Apr–Jun output) = 5.51 "
            "(like-for-like). TOTAL rendered AWAITING until July wages are available."
        ),
    }

    with _cache_lock:
        if not failed_months:
            _cache[cache_key] = {"data": result, "_ts": time.time()}

    return result
