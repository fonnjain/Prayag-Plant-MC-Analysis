"""Per-report generators — rebuilt to call mgmt_*.py builders.

Cardinal Rule: each gen_* is a thin wrapper that derives the FY from the
``ym`` parameter, calls the corresponding serialiser (which calls the same
mgmt_*.py builder the web page uses), and wraps the result in a ReportModel
with a Cover tab (first) and a Notes tab populated from the flags (last).

Reports without a dedicated mgmt_*.py builder (``compound``, ``ptmt_eff``)
continue to use the previous inline logic; they also gain Cover + Notes.

Invariants preserved:
- Daily-first; figures summed from authoritative daily workbooks via builders.
- AWAITING SOURCE DATA / n/a / IDLE / ⚠ unavailable are written as text
  strings — never 0 or blank.
- A failed build never produces a clean-looking empty file. The failure is
  recorded on the Notes tab; the workbook is included in the ZIP (so the
  caller sees it in ``built`` count) but labelled clearly as failed.
- No mgmt_*.py builder is modified here or in serialisers.py.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Optional

import sheets
import sources
from metrics import Record, compute_metrics, rollup_by_machine

from . import serialisers
from .model import Column, Flag, ReportModel, ReportSheet, Section
from .period import month_disp

PTMT_IDEAL_HOURS = 572.0
MOULD_IDEAL_HOURS = 500.0
_PIPE_MATERIALS = ("CPVC", "UPVC", "SWR", "AGRI")
_NON_PIPE = "Non-pipe (PP/PPR/ABS/TEFLON)"
_MC_RE = re.compile(r"M\s*/?\s*C\s*-?\s*(\d+)", re.I)
_AUX_RE = re.compile(r"GRIND|PULVER|MIXER|SOCKET", re.I)


# ---------------------------------------------------------------------------
# Shared small helpers (kept for compound + ptmt_eff inline generators)
# ---------------------------------------------------------------------------
def _daily(ym: str) -> List[Record]:
    recs, _reports, _warn = sheets.get_daily_records([ym])
    return recs


def _plant_recs(ym: str, plant: str) -> List[Record]:
    return [r for r in _daily(ym) if r.plant == plant]


def _mould_recs(ym: str) -> List[Record]:
    return [r for r in _plant_recs(ym, "MOULDING")
            if not _AUX_RE.search(r.machine or "")]


def _pct(num: float, den: float) -> Optional[float]:
    if not den:
        return None
    return num / den * 100.0


def _avg(num: float, den: float) -> Optional[float]:
    if not den:
        return None
    return num / den


def _mc_num(label: str) -> Optional[int]:
    m = _MC_RE.search(label or "")
    return int(m.group(1)) if m else None


def _tonnage_ptmt(code: str) -> Optional[str]:
    m = re.search(r"(\d{2,4})", str(code) or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Common serialiser wrapper
# ---------------------------------------------------------------------------
def _wrap(serial_fn, rid: str, label: str, plant: str, ym: str,
          cover_source: str = "", serial_kwargs: Optional[dict] = None) -> ReportModel:
    """Call a serialiser, handle errors, and return a ReportModel.

    On success: returns a model with cover_source set for the Cover tab.
    On error  : returns a model with available=False and the failure recorded
                in flags (visible on the Notes tab) — never a silent blank.

    Use ``serial_kwargs`` to pass extra keyword arguments to the serialiser
    (e.g. ``serial_kwargs={"plant": "TANK"}`` for the tank reports).
    """
    try:
        if serial_kwargs:
            sheets_out, flags = serial_fn(ym, **serial_kwargs)
        else:
            sheets_out, flags = serial_fn(ym)
    except Exception as exc:
        flags = [Flag(
            rule="BUILD FAILURE",
            section="All",
            month="",
            our_figure="",
            source_figure="",
            difference="",
            note=f"{type(exc).__name__}: {exc}",
        )]
        return ReportModel(
            rid=rid, label=label, plant=plant, ym=ym,
            month_disp=month_disp(ym),
            available=False,
            flags=flags,
            cover_source="Build failed — see Notes tab for the error.",
            sheets=[],
            headline="BUILD FAILED",
        )

    # Serialiser itself may return _build_failed
    if not sheets_out and flags and flags[0].rule == "BUILD FAILURE":
        return ReportModel(
            rid=rid, label=label, plant=plant, ym=ym,
            month_disp=month_disp(ym),
            available=False,
            flags=flags,
            cover_source="Build failed — see Notes tab for the error.",
            sheets=[],
            headline="BUILD FAILED",
        )

    return ReportModel(
        rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        available=bool(sheets_out),
        flags=flags,
        cover_source=cover_source or (
            "Live Google Sheets — same source workbooks the management-report "
            "pages use. Figures recomputed from the daily and annual workbooks "
            "via the mgmt_*.py builders."
        ),
        sheets=sheets_out,
        headline=None,
    )


def _awaiting(rid, label, plant, ym, note) -> ReportModel:
    """Convenience wrapper for a clean 'awaiting source data' result."""
    return ReportModel(
        rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        available=False,
        flags=[Flag(rule="AWAITING", section="All", note=note)],
        cover_source="",
        sheets=[ReportSheet(name=label[:31], title=label,
                            subtitle=f"{plant} · {month_disp(ym)}", note=note)],
        headline=note,
    )


# ============================================================================
# (A) Pipe M/C Summary  — R2
# ============================================================================
def gen_pipe(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_pipe, rid, label, plant, ym,
                 cover_source=(
                     "PIPE workbook (Kaharani). Hours from Report-5; output "
                     "and rejection from the date-wise union of Report-5 and "
                     "Report-11. Wages awaiting HR source sheet."
                 ))


# ============================================================================
# (B) Moulding M/C Summary  — R3
# ============================================================================
def gen_moulding(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_moulding, rid, label, plant, ym,
                 cover_source=(
                     "Moulding workbook (Kaharani), Report-12. Hours joined "
                     "from Report-5 (ideal basis). Auxiliary machines "
                     "(grinders, mixers, sockets) excluded from plant output."
                 ))


# ============================================================================
# (C) Group of Moulding  — R4
# ============================================================================
def gen_gom(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_gom, rid, label, plant, ym,
                 cover_source=(
                     "Moulding workbook (Kaharani), Report-12. Grouped by "
                     "tonnage band; same source as Moulding M/C Summary. "
                     "Net basis (gross minus rejection)."
                 ))


# ============================================================================
# (D) Pipe Moulds Summary  — R13
# ============================================================================
def gen_pipe_moulds(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_pipe_moulds, rid, label, plant, ym,
                 cover_source=(
                     "PIPE workbook (Kaharani), Reports 17–21 (per-mould "
                     "working tabs). Authoritative source; Report-12 used "
                     "as cross-check only. PPR excluded when tab is absent."
                 ))


# ============================================================================
# Garden M/C Summary  — R5
# ============================================================================
def gen_garden(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_garden, rid, label, plant, ym,
                 cover_source=(
                     "GARDEN workbook (Kaharani). Daily-report (DR) basis. "
                     "R-23 check: our figure vs the daily-report tab total. "
                     "Wages from HR source sheet."
                 ))


# ============================================================================
# HDPE M/C Summary  — R6
# ============================================================================
def gen_hdpe(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_hdpe, rid, label, plant, ym,
                 cover_source=(
                     "HDPE workbook (Kaharani). Daily-report (DR) basis. "
                     "R-23 check applied. Idle months marked IDLE."
                 ))


# ============================================================================
# Mould Efficiency (PTMT)  — R12
# ============================================================================
def gen_mould_eff(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_ptmt_mould_eff, rid, label, plant, ym,
                 cover_source=(
                     "PTMT workbook (Bhiwadi). Per-mould efficiency sourced "
                     "from Report-9 (pcs). Ideal hours from annual workbook "
                     "(shown as '—' when unavailable). 8 duplicate codes per "
                     "month are SUMMED as per the reconciliation rule."
                 ))


# ============================================================================
# Tank KH  — R7
# ============================================================================
def gen_tank_kh(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_tank, rid, label, plant, ym,
                 serial_kwargs={"plant": "TANK"},
                 cover_source=(
                     "TANK workbook — Kaharani (KH). Two-source union: "
                     "daily-report total + PROD. REPORT PRODUCTION HOURS. "
                     "Date-wise max; R-26 data errors flagged."
                 ))


# ============================================================================
# PTMT Moulds Summary  — R11
# ============================================================================
def gen_ptmt_moulds(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_ptmt_moulds, rid, label, plant, ym,
                 cover_source=(
                     "PTMT workbook (Bhiwadi). Run hours from Report-5 "
                     "(machine hours, R-24 basis). Sheet TOTAL defects in 3 "
                     "columns documented in Notes tab."
                 ))


# ============================================================================
# Tank VN  — R8
# ============================================================================
def gen_tank_vn(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_tank, rid, label, plant, ym,
                 serial_kwargs={"plant": "TANK_VN"},
                 cover_source=(
                     "TANK workbook — Varanasi (VN). Same two-source union "
                     "as KH. Utilisation suppressed (output-only plant)."
                 ))


# ============================================================================
# Tank WB  — R9
# ============================================================================
def gen_tank_wb(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_tank, rid, label, plant, ym,
                 serial_kwargs={"plant": "TANK_WB"},
                 cover_source=(
                     "TANK workbook — Wambori (WB). Same two-source union "
                     "as KH. Combined MACHINE-1 + MACHINE-2 label in source."
                 ))


# ============================================================================
# Segment Labour / Power / Solar  — R1
# ============================================================================
def gen_segment_labour(rid, label, plant, ym) -> ReportModel:
    return _wrap(serialisers.serial_segment_labour, rid, label, plant, ym,
                 cover_source=(
                     "Segment Labour workbook (all locations). UNIT-1, "
                     "UNIT-2, UNIT-3 headcount and wages; ideal power and "
                     "labour cost from the costing module."
                 ))


# ============================================================================
# Compound / Material Balance  — R10
# (no dedicated mgmt_*.py builder; keep inline; add Cover + Notes via model)
# ============================================================================
def gen_compound(rid, label, plant, ym) -> ReportModel:
    """Compound mass-balance summary — recomputed from Pipe & Fitting daily
    mixer-logbook tabs (Reports 6–10, CG 122).

    TOTAL tab: full-FY aggregate across all 12 months.
    Monthly tabs (APR-26 … MAR-27): per-month balance + raw-material breakdown.
    Months with no data show the row structure with blank cells — never zeros
    (R-07/R-08).

    No dedicated mgmt_*.py builder exists; this uses the compound module
    directly — the same path as the /compound web page.

    gen_compound and gen_ptmt_eff are the two reports that cannot be trivially
    routed through a serial_* function because they have no mgmt_*.py builder.
    Moving them would require creating a new mgmt_compound.py / mgmt_ptmt_eff.py
    wrapper, which is out of scope here (the constraint is "do not touch
    mgmt_*.py builder calculations"). The inline path is retained intentionally.
    """
    import compound as _cmpd

    # ── Compute all 12 FY months from the requested ym ─────────────────────────
    try:
        yr, mo = int(ym[:4]), int(ym[5:7])
    except Exception:
        yr, mo = 2026, 7
    fy_start = yr if mo >= 4 else yr - 1
    _MO_ABBR = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
                7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    fy_months = (
        [f"{fy_start}-{m:02d}" for m in range(4, 13)] +
        [f"{fy_start + 1}-{m:02d}" for m in range(1, 4)]
    )

    def _tab_label(m_ym: str) -> str:
        try:
            my, mm = int(m_ym[:4]), int(m_ym[5:7])
            return f"{_MO_ABBR[mm]}-{str(my)[-2:]}"
        except Exception:
            return m_ym

    # Load all FY months in a single Sheets call
    try:
        data = sheets.load_compound_data(fy_months)
    except sheets.SheetReadError as e:
        return _awaiting(rid, label, plant, ym, str(e))

    by_compound = data["by_compound"]
    got_months  = set(data.get("months") or [])

    # Full-FY compilation for TOTAL tab
    fy_comp = _cmpd.build_compilation(by_compound, fy_months)
    if not fy_comp.get("has_data"):
        return _awaiting(rid, label, plant, ym,
                         "No compound data found for this financial year.")

    # ── Column / row helpers (shared across TOTAL + monthly tabs) ──────────────
    def _bal_cols():
        return [
            Column("cmp",      "Compound",        "text", width=16),
            Column("opening",  "Opening (kg)",    "kg"),
            Column("batch",    "Batch (kg)",      "kg",   total=True),
            Column("given",    "Given (kg)",      "kg",   total=True),
            Column("closing",  "Closing (kg)",    "kg"),
            Column("loss_kg",  "Loss (kg)",       "kg",   total=True),
            Column("loss_pct", "Loss %",          "pct"),
        ]

    def _mat_cols(comp):
        """One column per compound that has_data, plus Total."""
        cmp_keys = [c["key"] for c in comp["cols"] if c.get("has_data")]
        cmp_lbls = {c["key"]: c["label"] for c in comp["cols"]}
        cols = [Column("mat", "Material / Chemical", "text", width=24)]
        for k in cmp_keys:
            cols.append(Column(k, cmp_lbls.get(k, k), "kg", total=True))
        cols.append(Column("_tot", "Total (kg)", "kg", total=True))
        return cols, cmp_keys

    def _bal_rows_and_total(comp):
        """Rows = one per compound (all included, blanks for no-data months)."""
        rows = []
        for c in comp["cols"]:
            loss_kg = (c.get("batch") or 0.0) - (c.get("given") or 0.0)
            rows.append({
                "cmp":      c["label"],
                "opening":  c.get("opening") or None,
                "batch":    c.get("batch") or None,
                "given":    c.get("given") or None,
                "closing":  c.get("closing") or None,
                "loss_kg":  loss_kg if (c.get("batch") and c.get("given")) else None,
                "loss_pct": c.get("loss_pct") or None,
            })
        tot = comp["total"]
        t_batch = tot.get("batch") or 0.0
        t_given = tot.get("given") or 0.0
        total_row = {
            "cmp":      "GRAND TOTAL",
            "opening":  None,
            "batch":    t_batch or None,
            "given":    t_given or None,
            "closing":  None,
            "loss_kg":  (t_batch - t_given) if (t_batch and t_given) else None,
            "loss_pct": tot.get("loss_pct") or None,
        }
        return rows, total_row

    def _mat_rows_and_total(comp, cmp_keys):
        mat_items = comp.get("materials") or []
        rows = []
        m_grand: dict = {k: 0.0 for k in cmp_keys}
        m_grand["_tot"] = 0.0
        for item in mat_items:
            row: dict = {"mat": item["name"]}
            row_tot = 0.0
            for k in cmp_keys:
                v = item["by"].get(k, 0.0)
                row[k] = v or None
                row_tot += v
                m_grand[k] += v
            row["_tot"] = row_tot or None
            m_grand["_tot"] += row_tot
            rows.append(row)
        mat_total = {k: (m_grand[k] or None) for k in cmp_keys}
        mat_total["mat"] = "TOTAL"
        mat_total["_tot"] = m_grand["_tot"] or None
        return rows, mat_total

    # ── TOTAL tab (full FY) ─────────────────────────────────────────────────────
    fy_bc = _bal_cols()
    fy_brows, fy_btot = _bal_rows_and_total(fy_comp)
    fy_mc, fy_ckeys = _mat_cols(fy_comp)
    fy_mrows, fy_mtot = _mat_rows_and_total(fy_comp, fy_ckeys)
    fy_t_batch = fy_comp["total"].get("batch") or 0.0
    fy_t_given = fy_comp["total"].get("given") or 0.0
    fy_loss_pct = (fy_comp["total"].get("loss_pct") or 0) * 100

    result_sheets: list = [ReportSheet(
        name="TOTAL",
        title=f"Compound Balance — Full FY Total — {fy_start % 100:02d}-{(fy_start+1) % 100:02d}",
        subtitle=(
            "Kaharani · Pipe & Fitting mixer-logbook mass-balance, full-FY aggregate. "
            "Closing = Opening + Batch − Given. "
            "Loss % = Loss ÷ Batch (UPVC: Loss ÷ Material out of Mixer → 0.51% not 0.50%)."
        ),
        sections=[Section(fy_bc, fy_brows, fy_btot),
                  Section(fy_mc, fy_mrows, fy_mtot)],
        provenance=[
            f"Grand batch {fy_t_batch:,.0f} kg · given {fy_t_given:,.0f} kg "
            f"· loss {(fy_t_batch - fy_t_given):,.0f} kg ({fy_loss_pct:.2f}%).",
            "Source: Pipe & Fitting daily workbook (Kaharani), mixer-logbook tabs "
            "(Reports 6–10, CG 122). Same source as the /compound web page.",
        ],
    )]

    # ── Monthly tabs (APR-26 … MAR-27) ─────────────────────────────────────────
    for m_ym in fy_months:
        tab_lbl  = _tab_label(m_ym)
        has_data = m_ym in got_months
        # Filter each compound's parse list to this month only
        sub = {k: [p for p in plist if p.get("ym") == m_ym]
               for k, plist in by_compound.items()}
        mo_comp = _cmpd.build_compilation(sub, [m_ym])

        mo_bc = _bal_cols()
        if has_data:
            mo_brows, mo_btot = _bal_rows_and_total(mo_comp)
            mo_mc, mo_ckeys = _mat_cols(mo_comp)
            mo_mrows, mo_mtot = _mat_rows_and_total(mo_comp, mo_ckeys)
            sections_mo = [Section(mo_bc, mo_brows, mo_btot),
                           Section(mo_mc, mo_mrows, mo_mtot)]
        else:
            # Show row structure with blank cells — never zeros (R-07/R-08)
            mo_brows = [
                {"cmp": c["label"], "opening": None, "batch": None, "given": None,
                 "closing": None, "loss_kg": None, "loss_pct": None}
                for c in _cmpd.COMPOUNDS
            ]
            sections_mo = [Section(mo_bc, mo_brows)]

        result_sheets.append(ReportSheet(
            name=tab_lbl,
            title=f"Compound Balance — {tab_lbl}",
            subtitle=(
                "Monthly mixer-logbook balance. "
                "Loss = Batch − Material out of Mixer. Loss % = Loss ÷ Batch."
                if has_data else
                "No compound data recorded for this month — row structure shown, "
                "cells blank (R-07/R-08)."
            ),
            sections=sections_mo,
        ))

    t_batch = fy_t_batch
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        cover_source=(
            "Pipe & Fitting daily workbook (Kaharani), mixer-logbook tabs "
            "(Reports 6–10, CG 122). Same source as the /compound web page."
        ),
        sheets=result_sheets,
        headline=f"{t_batch:,.0f} kg batch (FY)")


# ============================================================================
# PTMT Efficiency by Tonnage  — standalone (no mgmt_*.py builder)
# ============================================================================
def _ptmt_stats(ym: str):
    """{code: {kg, hours, group}} for PTMT machines, plus a "had records" flag."""
    recs = _plant_recs(ym, "PTMT")
    stats: dict = {}
    for r in recs:
        code = r.machine.replace("PTMT ", "").strip() if r.machine else ""
        if not code:
            continue
        grp, _fin = sheets._ptmt_group(code)
        s = stats.setdefault(code, {"kg": 0.0, "hours": 0.0, "group": grp})
        s["kg"] += r.total_count
        s["hours"] += r.actual_hours
    return stats, bool(recs)


def gen_ptmt_eff(rid, label, plant, ym) -> ReportModel:
    """PTMT Efficiency by Tonnage — no mgmt_*.py builder; inline logic kept.

    Grouped by injection machine tonnage band; same figure as the web page
    but formatted as an exportable management-report .xlsx.
    """
    stats, ok = _ptmt_stats(ym)
    if not ok:
        return _awaiting(rid, label, plant, ym, "No PTMT daily data for this month.")
    inj_codes = (sources.PTMT_GROUPS["PTMT – Injection (standard)"]
                 + sources.PTMT_GROUPS["PTMT – Injection (N-line)"])
    bands = defaultdict(lambda: {"n": 0, "kg": 0.0, "hours": 0.0})
    for code in inj_codes:
        ton = _tonnage_ptmt(code) or "Other"
        s = stats.get(code, {"kg": 0.0, "hours": 0.0})
        b = bands[ton]
        b["n"] += 1; b["kg"] += s["kg"]; b["hours"] += s["hours"]
    cols = [Column("ton",  "Tonnage Group",   "text", width=14),
            Column("n",    "Machines",        "int",  total=True),
            Column("out",  "Output (KG)",     "kg",   total=True),
            Column("hrs",  "Run Hours",       "int",  total=True),
            Column("util", "Utilisation %",   "pct"),
            Column("avg",  "Avg Output / Hr", "rate")]
    rows = []
    t_n = t_kg = t_h = 0.0
    for ton in sorted(bands, key=lambda x: int(x) if str(x).isdigit() else 9999):
        b = bands[ton]
        ideal = b["n"] * PTMT_IDEAL_HOURS
        t_n += b["n"]; t_kg += b["kg"]; t_h += b["hours"]
        rows.append({"ton": f"{ton} Ton", "n": b["n"], "out": b["kg"],
                     "hrs": b["hours"] or None,
                     "util": _pct(b["hours"], ideal) if b["hours"] else None,
                     "avg": _avg(b["kg"], b["hours"])})
    t_ideal = t_n * PTMT_IDEAL_HOURS
    total = {"ton": "INJECTION TOTAL", "n": t_n, "out": t_kg,
             "hrs": t_h or None,
             "util": _pct(t_h, t_ideal) if t_h else None,
             "avg": _avg(t_kg, t_h)}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        cover_source=(
            "PTMT daily workbook (Bhiwadi). Injection machines only, "
            "grouped by tonnage band. Ideal: 572 h × machines in group. "
            "Corrugator, blow moulding and grinding excluded."
        ),
        sheets=[ReportSheet(name="PTMT %age Efficiency",
            title=f"PTMT %age Efficiency — {month_disp(ym)}",
            subtitle="Bhiwadi · Injection machines grouped by tonnage; output (kg), "
                     "run hours and utilisation vs the in-sheet ideal (572 h/machine "
                     "× machines in the group). Corrugator, blow & grinding excluded.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed injection output {t_kg:,.0f} kg across "
                        f"{int(t_n)} machines."])],
        headline=f"{t_kg:,.0f} kg injection")
