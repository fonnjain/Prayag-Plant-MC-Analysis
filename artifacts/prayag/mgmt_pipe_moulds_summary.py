"""Management Report 13 — (D) Pipe Moulds Summary.

Two YoY blocks (FY26-27 Apr-Jul vs FY25-26 Apr-Jul), five materials
(CPVC / UPVC / SWR / AGRI / PPR), eight columns each.

Data sources
------------
FY26-27  : Reports 17–21 from the JUL'26 PIPE workbook (cumulative layout —
           each monthly workbook appends a 4-column block for the new month).
           ``parse_cumulative_mould_fy`` sums all four month blocks.

FY25-26  : Per-material annual tabs ('CPVC Mould Summary (25-26)', etc.) in
           the finalized "(D). Annual 25-26 Pipe Moulds Summary" workbook.
           ``parse_annual_mould_summary_apr_jul`` sums the APR-JUL columns.
           PPR is absent (new material; no FY25-26 history).

Column definitions
------------------
No. of Total Mould  FY26-27: NOT AVAILABLE — Reports 17-21 only list moulds
                    that ran, not the full registry.  FY25-26: row count of
                    the per-material annual tab (full register).
No. of Mould Run    Distinct mould codes with pcs > 0 or kg > 0 across the
                    4-month period.  Exact match to spec for FY26-27.
Mould Run Hours     Formula-derived (pcs × cycle-time / 3600) from the
                    Report-17..21 columns.  FY25-26 hours match spec exactly.
                    FY26-27 hours match formula but differ from the spec
                    source (see sourcing note — R-50).
Av. Run Hr/Mould    = Mould Run Hours / No. of Mould Run.
Output (Pcs)        Exact match to spec for both periods.
Output (KG)         Exact match; confirmed via Moulding path (Report-12).
Avg./Month          = Output (KG) / n_months.  Spec column unit is KG/month.

Sheet TOTAL defect (FY26-27)
-----------------------------
The workbook's TOTAL row omits PPR from six of seven columns:
  - No. of Total Mould  includes PPR (608 per spec)
  - All other sheet columns use the non-PPR sum

This report always shows our correct inclusive TOTAL and flags the sheet
defect with a single note.
"""
from __future__ import annotations
from typing import Optional


# ── FY config ─────────────────────────────────────────────────────────────────

_FY_PERIODS: dict[str, dict] = {
    "2627": {
        "label": "Apr,26 – Jul,26",
        "n_months": 4,
        "months": ["2026-04", "2026-05", "2026-06", "2026-07"],
    },
    "2526": {
        "label": "Apr,25 – Jul,25",
        "n_months": 4,
        "months": ["2025-04", "2025-05", "2025-06", "2025-07"],
    },
}

_MATERIAL_ORDER = ["CPVC", "UPVC", "SWR", "AGRI", "PPR"]

# Spec anchors for the reconciliation badge (FY26-27)
_SPEC_2627: dict[str, dict] = {
    "CPVC": {"n_total": 210, "n_run": 145, "hrs": 7_195,  "av_hr": 49.62, "pcs": 2_986_681, "kg": 80_330.59},
    "UPVC": {"n_total": 170, "n_run": 139, "hrs": 4_398,  "av_hr": 31.64, "pcs": 1_678_108, "kg": 96_075.43},
    "SWR":  {"n_total":  89, "n_run":  74, "hrs": 4_455,  "av_hr": 60.20, "pcs":   589_703, "kg": 147_772.82},
    "AGRI": {"n_total": 129, "n_run":  83, "hrs": 1_162,  "av_hr": 14.00, "pcs":   250_631, "kg":  40_415.13},
    "PPR":  {"n_total":  10, "n_run":  16, "hrs":    39,  "av_hr":  2.45, "pcs":    35_659, "kg":   1_421.42},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sh():
    import sheets
    return sheets


def _safe_div(a: float, b: float) -> Optional[float]:
    return a / b if b else None


def _fmt_f1(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:,.2f}"


def _fmt_int(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{int(round(v)):,}"


# ── Per-row builder ───────────────────────────────────────────────────────────

def _build_row(grp: str, result: dict, n_months: int, *, has_n_total: bool) -> dict:
    """Build a display row dict from a parser result."""
    n_total = result.get("n_total")
    n_run   = result.get("n_run", 0)
    pcs     = result.get("total_pcs", 0.0)
    kg      = result.get("total_kg", 0.0)
    hrs     = result.get("total_hrs", 0.0)
    av_hr   = _safe_div(hrs, n_run)
    avg_mo  = _safe_div(kg, n_months)

    return {
        "material":  grp,
        "n_total":   n_total if has_n_total else None,
        "n_run":     n_run,
        "hrs":       hrs,
        "av_hr":     av_hr,
        "pcs":       pcs,
        "kg":        kg,
        "avg_month": avg_mo,
        # display strings
        "d_n_total":  _fmt_int(n_total) if has_n_total else "—",
        "d_n_run":    _fmt_int(n_run),
        "d_hrs":      _fmt_f1(hrs),
        "d_av_hr":    _fmt_f1(av_hr),
        "d_pcs":      _fmt_int(pcs),
        "d_kg":       _fmt_f1(kg),
        "d_avg_month": _fmt_int(avg_mo),
    }


def _build_total_row(rows: list[dict], n_months: int, *, has_n_total: bool) -> dict:
    """Sum a list of material rows into a TOTAL row."""
    total_n   = sum(r["n_total"] for r in rows if r["n_total"] is not None) or None
    total_run = sum(r["n_run"] for r in rows)
    total_pcs = sum(r["pcs"] for r in rows)
    total_kg  = sum(r["kg"] for r in rows)
    total_hrs = sum(r["hrs"] for r in rows)
    av_hr     = _safe_div(total_hrs, total_run)
    avg_mo    = _safe_div(total_kg, n_months)

    return {
        "material":  "TOTAL",
        "n_total":   total_n,
        "n_run":     total_run,
        "hrs":       total_hrs,
        "av_hr":     av_hr,
        "pcs":       total_pcs,
        "kg":        total_kg,
        "avg_month": avg_mo,
        "d_n_total":  _fmt_int(total_n) if has_n_total else "—",
        "d_n_run":    _fmt_int(total_run),
        "d_hrs":      _fmt_f1(total_hrs),
        "d_av_hr":    _fmt_f1(av_hr),
        "d_pcs":      _fmt_int(total_pcs),
        "d_kg":       _fmt_f1(total_kg),
        "d_avg_month": _fmt_int(avg_mo),
    }


# ── Reconciliation badge builder ──────────────────────────────────────────────

def _build_recon(rows_2627: list[dict]) -> list[dict]:
    """Compare FY26-27 our totals against spec anchors.

    Returns a list of check dicts: {label, ours, spec, status, note}.
    """
    checks = []
    by_mat = {r["material"]: r for r in rows_2627}

    # Pcs per material
    for mat in _MATERIAL_ORDER:
        sp = _SPEC_2627.get(mat, {})
        r  = by_mat.get(mat, {})
        if not sp or not r:
            continue
        ours = r["pcs"]
        spec = sp["pcs"]
        pct  = abs(ours - spec) / spec * 100 if spec else None
        status = "PASS" if pct is not None and pct < 0.1 else "WARN"
        checks.append({
            "label":  f"{mat} Pcs",
            "ours":   f"{ours:,.0f}",
            "spec":   f"{spec:,}",
            "status": status,
            "note":   f"Δ {pct:.3f}%" if pct is not None else "",
        })

    # KG per material
    for mat in _MATERIAL_ORDER:
        sp = _SPEC_2627.get(mat, {})
        r  = by_mat.get(mat, {})
        if not sp or not r:
            continue
        ours = r["kg"]
        spec = sp["kg"]
        pct  = abs(ours - spec) / spec * 100 if spec else None
        status = "PASS" if pct is not None and pct < 0.05 else "WARN"
        checks.append({
            "label":  f"{mat} KG",
            "ours":   f"{ours:,.2f}",
            "spec":   f"{spec:,.2f}",
            "status": status,
            "note":   f"Δ {pct:.4f}%" if pct is not None else "",
        })

    # n_run per material
    for mat in _MATERIAL_ORDER:
        sp = _SPEC_2627.get(mat, {})
        r  = by_mat.get(mat, {})
        if not sp or not r:
            continue
        ours = r["n_run"]
        spec = sp["n_run"]
        status = "PASS" if ours == spec else "WARN"
        checks.append({
            "label":  f"{mat} Moulds Run",
            "ours":   str(ours),
            "spec":   str(spec),
            "status": status,
            "note":   "" if ours == spec else f"Δ {ours - spec:+d}",
        })

    return checks


# ── Main build function ───────────────────────────────────────────────────────

def build_pipe_moulds_summary(fy: str = "2627") -> dict:
    """Return the full display dict for the Pipe Moulds Summary report.

    Structure::

        {
          "fy": str,
          "fy_label": str,
          "blocks": [
              {
                "period_label": str,      # "Apr,26 – Jul,26"
                "fy_key": str,            # "2627" | "2526"
                "rows": [row_dict, ...],  # one per material
                "total_row": row_dict,
                "has_n_total": bool,      # False for FY26-27
                "has_ppr": bool,          # PPR present in this block
                "missing": [str, ...],    # materials without source data
                "unavailable": bool,
              },
              ...
          ],
          "recon": [...],      # reconciliation badge checks (FY26-27)
          "sourcing_note": str,
          "defect_note": str,  # PPR omission note
          "hours_note": str,   # formula vs spec discrepancy
        }
    """
    sh = _sh()

    # ── FY26-27 block ─────────────────────────────────────────────────────────
    cfg_2627 = _FY_PERIODS["2627"]
    fy2627_data = sh.load_pipe_moulds_fy("2627")
    rows_2627: list[dict] = []
    missing_2627 = list(fy2627_data.get("missing", []))

    by_grp_2627 = {g["group"]: g for g in fy2627_data.get("groups", [])}
    for mat in _MATERIAL_ORDER:
        result = by_grp_2627.get(mat)
        if result is None:
            continue
        rows_2627.append(
            _build_row(mat, result, cfg_2627["n_months"], has_n_total=False)
        )

    total_2627 = _build_total_row(rows_2627, cfg_2627["n_months"], has_n_total=False)

    block_2627 = {
        "period_label":  cfg_2627["label"],
        "fy_key":        "2627",
        "rows":          rows_2627,
        "total_row":     total_2627,
        "has_n_total":   False,
        "has_ppr":       any(r["material"] == "PPR" for r in rows_2627),
        "missing":       missing_2627,
        "unavailable":   not fy2627_data.get("available", False),
        "source_ym":     fy2627_data.get("latest_ym", ""),
        "n_months":      fy2627_data.get("n_months", 0),
    }

    # ── FY25-26 block ─────────────────────────────────────────────────────────
    cfg_2526 = _FY_PERIODS["2526"]
    fy2526_data = sh.load_pipe_moulds_annual_2526()
    rows_2526: list[dict] = []
    missing_2526 = list(fy2526_data.get("missing", []))

    by_grp_2526 = {g["group"]: g for g in fy2526_data.get("groups", [])}
    for mat in _MATERIAL_ORDER:
        if mat == "PPR":
            continue  # PPR is new in FY26-27; no FY25-26 history
        result = by_grp_2526.get(mat)
        if result is None:
            continue
        rows_2526.append(
            _build_row(mat, result, cfg_2526["n_months"], has_n_total=True)
        )

    total_2526 = _build_total_row(rows_2526, cfg_2526["n_months"], has_n_total=True)

    block_2526 = {
        "period_label":  cfg_2526["label"],
        "fy_key":        "2526",
        "rows":          rows_2526,
        "total_row":     total_2526,
        "has_n_total":   True,
        "has_ppr":       False,
        "missing":       missing_2526,
        "unavailable":   not fy2526_data.get("available", False),
        "source_ym":     "",
        "n_months":      cfg_2526["n_months"],
    }

    # ── Reconciliation badge ─────────────────────────────────────────────────
    recon = _build_recon(rows_2627)

    # ── Notes ─────────────────────────────────────────────────────────────────
    sourcing_note = (
        "FY26-27 sourced from Reports 17–21 in the Jul'26 PIPE workbook "
        "(cumulative 4-month format, Apr–Jul).  "
        "FY25-26 sourced from per-material annual tabs in the finalized "
        "\"(D). Annual 25-26 Pipe Moulds Summary\" workbook "
        "(Apr,25–Jul,25 columns only).  "
        "PPR (Report-21) is a new material in FY26-27; no FY25-26 history."
    )

    defect_note = (
        "Sheet TOTAL defect (FY26-27): The workbook's TOTAL row counts "
        "all 5 materials for No. of Total Mould but omits PPR from the "
        "remaining 6 columns (Moulds Run, Hours, Pcs, KG, Avg/Month).  "
        "This report shows the correct inclusive TOTAL."
    )

    hours_note = (
        "Mould Run Hours = Σ(pcs × cycle-time / 3600) from Report-17..21 "
        "(formula in each cell).  "
        "FY26-27 formula hours differ from the spec workbook figures "
        "(spec: 17,249 h total; formula: ~30,900 h total) — the spec may "
        "use a different cycle-time table (design vs scheduling standard).  "
        "FY25-26 formula hours match the spec exactly.  "
        "No. of Total Mould for FY26-27 is not available from Reports "
        "17–21 (monthly templates list only moulds that ran); the full "
        "registry count (spec: 608) requires the FY26-27 annual workbook."
    )

    return {
        "fy":            fy,
        "blocks":        [block_2627, block_2526],
        "recon":         recon,
        "sourcing_note": sourcing_note,
        "defect_note":   defect_note,
        "hours_note":    hours_note,
    }
