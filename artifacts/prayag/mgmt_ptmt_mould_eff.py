"""Management Report 12 — PTMT Moulding %age in Efficiency.

Data source
-----------
- Per-mould production   : Report-9 tab in monthly PTMT planning workbook
  (parse_ptmt_report9 → load_ptmt_report9).  This is the authoritative
  per-mould pcs source; APR TOTAL = 5,211,693 matches spec exactly.
- Cycle time (display)   : Report-9 C4 "CYCLE TIME PER PC" (secs/pc).
  Same value as PTMT MASTER (verified for sample moulds).
- MASTER                 : supplementary for cavity count, pc weight, name.
- Ideal M/C Run Min      : pcs × cycle_time / 60  (verified exact formula:
  PSF-219 6.25 s, PSF-23 21.25 s, PSF-314 100 s, PSF-184 8.75 s).
- Actual M/C Run Min     : Report-9 C11 (= pcs × ct / 60, the spec's
  "Actual M/C Run Min" column; APR C11 sum = 918,451 = spec actual ✓).

Efficiency note
---------------
The spec's "Ideal M/C Run Min" (891,279 for APR) comes from the annual
workbook "ANNUAL PTMT Moulding %age in Efficiency 26-27", which is not
accessible via Drive.  That ideal uses a DIFFERENT cycle-time table
(design/measured time vs the scheduling standard in Report-9 C4), so
our ideal ≈ our actual ≈ 100% for all moulds.

Efficiency is therefore not shown.  The Actual M/C Run Min column is
shown as the scheduling-standard baseline (C11 = pcs × ct / 60).
A reconciliation badge surfaces the spec anchor vs what we compute so
planners can see the sourcing gap clearly.

``ANNUAL PTMT Moulding %age in Efficiency 26-27`` not found in Drive —
sourced independently from Report-9 monthly workbooks (R-30 compliant).
"""

from __future__ import annotations
from typing import Optional

# ── FY config ────────────────────────────────────────────────────────────────

_FY_MONTHS: dict[str, list[str]] = {
    "2627": ["2026-04", "2026-05", "2026-06", "2026-07"],
}

_MONTH_DISP: dict[str, str] = {
    "2026-04": "APR'26",
    "2026-05": "MAY'26",
    "2026-06": "JUN'26",
    "2026-07": "JUL'26",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _sh():
    import sheets
    return sheets


def _safe_div(num: float, den: float) -> Optional[float]:
    if not den:
        return None
    return num / den


# ── Public API ────────────────────────────────────────────────────────────────

def build_ptmt_mould_eff(fy: str = "2627") -> dict:
    """Return the full mould-efficiency data dict for *fy*.

    Structure::

        {
          "fy": str,
          "fy_label": str,
          "has_data": bool,
          "months": [ym, …],
          "month_labels": {ym: disp},
          "view_months": [ym, …],   # months with Report-9 data
          "mould_count": int,       # moulds with any FY production
          "rows": [MouldRow, …],
          "total_row": TotalRow,
          "anchors": {...},
          "sourcing_note": str,
        }

    MouldRow::

        {
          "sno": int,           # MASTER / Report-9 order
          "code": str,
          "mat_type": str,
          "name": str,
          "cavity": int | None,
          "cycle_time": float | None,   # secs/pc from Report-9 or MASTER
          "pc_weight_g": float | None,
          "total": BlockData,
          "monthly": {ym: BlockData | None},
        }

    BlockData::

        {
          "pcs": float | None,
          "run_count": int | None,
          "ideal_min": float | None,   # pcs × ct / 60
          "std_min": float | None,     # C11 from Report-9 (spec 'Actual')
        }
    """
    months = _FY_MONTHS.get(fy, [])
    if not months:
        return _empty(fy)

    sh = _sh()

    # ── MASTER: cavity, pc weight, name, ordering ─────────────────────────────
    first_ym = months[0]
    try:
        master_list = sh.load_ptmt_master(first_ym)
    except Exception:
        master_list = []
    master: dict = {s.item_code: s for s in master_list}
    master_order: dict[str, int] = {s.item_code: i for i, s in enumerate(master_list)}

    # ── Report-9 per-mould data ───────────────────────────────────────────────
    monthly_raw: dict[str, dict] = {}
    view_months: list[str] = []
    for ym in months:
        try:
            d = sh.load_ptmt_report9(ym)
        except Exception:
            d = {"available": False, "total_pcs": 0.0, "moulds": {}}
        monthly_raw[ym] = d
        if d.get("available"):
            view_months.append(ym)

    if not view_months:
        return _empty(fy)

    # ── Mould universe ────────────────────────────────────────────────────────
    all_codes: set[str] = set()
    for d in monthly_raw.values():
        all_codes.update(d.get("moulds", {}).keys())

    sorted_codes = sorted(
        all_codes,
        key=lambda c: (master_order.get(c, 999_999), c),
    )

    # ── Per-mould rows ────────────────────────────────────────────────────────
    rows: list[dict] = []

    t_pcs_fy     = 0.0
    t_ideal_fy   = 0.0
    t_std_fy     = 0.0
    t_runs_fy    = 0
    t_monthly: dict[str, dict] = {
        ym: {"pcs": 0.0, "ideal_min": 0.0, "std_min": 0.0, "run_count": 0}
        for ym in months
    }

    for sno, code in enumerate(sorted_codes, 1):
        std   = master.get(code)

        # collect display fields from first month that has the mould
        ct_display   = None
        pw_display   = None
        name_display = (std.item_name if std else "") or ""
        mat_type     = ""

        monthly: dict[str, Optional[dict]] = {}
        row_pcs   = 0.0
        row_ideal = 0.0
        row_std   = 0.0
        row_runs  = 0

        for ym in months:
            md = monthly_raw[ym].get("moulds", {}).get(code)
            if not md or md["pcs"] == 0:
                monthly[ym] = None
                continue

            if ct_display is None:
                ct_display = md["ct"] or (std.cycle_time_per_pcs if std and std.cycle_time_per_pcs else None)
            if pw_display is None:
                pw_display = md["pc_weight_g"] or (std.wt_per_pc_gms if std else None)
            if not mat_type:
                mat_type = md["mat_type"]

            pcs       = md["pcs"]
            ct        = md["ct"] or (std.cycle_time_per_pcs if std and std.cycle_time_per_pcs else None)
            ideal     = (pcs * ct / 60) if ct else None
            std_min   = md["std_min"] or (ideal or 0.0)
            run_count = md["run_count"]

            monthly[ym] = {
                "pcs":       pcs,
                "run_count": run_count,
                "ideal_min": ideal,
                "std_min":   std_min,
            }

            row_pcs   += pcs
            if ideal: row_ideal += ideal
            row_std   += std_min
            row_runs  += run_count

            t_monthly[ym]["pcs"]       += pcs
            t_monthly[ym]["run_count"] += run_count
            if ideal: t_monthly[ym]["ideal_min"] += ideal
            t_monthly[ym]["std_min"]   += std_min

        rows.append({
            "sno":         sno,
            "code":        code,
            "mat_type":    mat_type,
            "name":        name_display,
            "cavity":      std.mould_cavity   if std else None,
            "cycle_time":  ct_display,
            "pc_weight_g": pw_display,
            "total": {
                "pcs":       row_pcs   if row_pcs   else None,
                "run_count": row_runs  if row_runs  else None,
                "ideal_min": row_ideal if row_ideal else None,
                "std_min":   row_std   if row_std   else None,
            },
            "monthly": monthly,
        })

        t_pcs_fy   += row_pcs
        t_ideal_fy += row_ideal
        t_std_fy   += row_std
        t_runs_fy  += row_runs

    # ── TOTAL row ─────────────────────────────────────────────────────────────
    total_row = {
        "total": {
            "pcs":       t_pcs_fy   if t_pcs_fy   else None,
            "run_count": t_runs_fy  if t_runs_fy  else None,
            "ideal_min": t_ideal_fy if t_ideal_fy else None,
            "std_min":   t_std_fy   if t_std_fy   else None,
        },
        "monthly": {
            ym: {
                "pcs":       v["pcs"]       if v["pcs"]       else None,
                "run_count": v["run_count"] if v["run_count"] else None,
                "ideal_min": v["ideal_min"] if v["ideal_min"] else None,
                "std_min":   v["std_min"]   if v["std_min"]   else None,
            }
            for ym, v in t_monthly.items()
        },
    }

    # ── Anchors (spec reference for reconciliation badge) ─────────────────────
    def _mb(ym):
        return total_row["monthly"].get(ym, {})

    anchors = {
        # Our computed figures
        "apr_pcs":    _mb("2026-04").get("pcs"),
        "apr_std":    _mb("2026-04").get("std_min"),
        "may_pcs":    _mb("2026-05").get("pcs"),
        "may_std":    _mb("2026-05").get("std_min"),
        "fy_pcs":     total_row["total"]["pcs"],
        "fy_moulds":  len(rows),
        # Spec reference values
        "spec_apr_pcs":    5_211_693,
        "spec_apr_actual": 918_451,   # = our std_min (C11) ← matches
        "spec_apr_ideal":  891_279,   # from annual workbook — we cannot reproduce
        "spec_apr_eff":    0.9704,
        "spec_may_pcs":    4_849_870,
        "spec_may_actual": 936_751,
        "spec_may_ideal":  952_311,
        "spec_may_eff":    1.0166,
        "spec_tot_actual": 4_414_618,
        "spec_tot_ideal":  4_437_919,
        "spec_tot_eff":    1.0053,
        "spec_fy_pcs":     24_487_601,
        "spec_fy_moulds":  393,
    }

    sourcing_note = (
        "Production Pcs from Report-9 (monthly PTMT planning workbook). "
        "Std Min (Actual M/C Run Min in spec) = pcs × cycle time ÷ 60 — "
        "matches spec 'Actual M/C Run Min' exactly for APR (918,451 ✓). "
        "Ideal M/C Run Min and Efficiency require the ANNUAL PTMT Moulding "
        "%age in Efficiency 26-27 workbook, which is not currently accessible "
        "via Drive — those columns are shown as dashes."
    )

    return {
        "fy":           fy,
        "fy_label":     "FY 2026-27",
        "has_data":     bool(rows),
        "months":       months,
        "month_labels": {ym: _MONTH_DISP.get(ym, ym) for ym in months},
        "view_months":  view_months,
        "mould_count":  len(rows),
        "rows":         rows,
        "total_row":    total_row,
        "anchors":      anchors,
        "sourcing_note": sourcing_note,
    }


def _empty(fy: str) -> dict:
    return {
        "fy": fy, "fy_label": "FY 2026-27", "has_data": False,
        "months": [], "month_labels": {}, "view_months": [],
        "mould_count": 0, "rows": [], "total_row": None,
        "anchors": {}, "sourcing_note": "",
    }
