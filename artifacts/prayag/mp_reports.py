"""
Machine Planning Report generators — Phase MP-2.

Generates Report-11 (all pipe machines) and Report-11A–D (machine-group
splits) as openpyxl workbooks.

Column order (exact, per spec):
  DATE | MACHINE NAME | MACHINE NO. | TYPES | ITEM CODE |
  Running Hours | Ideal Weight (KG) | Pcs | Weight | Wt./Pc. |
  Ideal Output Per Hour | Actual Output Per Hour | Output Efficiency

Header at row 5 (rows 1–4 = title block). Data from row 6 onward.

ADDITIVE / ISOLATED: reads only mp_engine.EngineResult, never touches
the production pipeline.
"""
from __future__ import annotations

import io
import re
from typing import Dict, List, Optional, Set

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from mp_engine import AssignedPortion, EngineResult, ItemResult, REPORT_11_GROUPS

# ── Style constants ──────────────────────────────────────────────────────────
NAVY  = "1F3864"
TERRA = "C55A11"
WHITE = "FFFFFF"
LIGHT = "F2F2F2"
_FONT = "Arial"

_NAVY_FILL  = PatternFill("solid", fgColor=NAVY)
_TERRA_FILL = PatternFill("solid", fgColor=TERRA)
_LIGHT_FILL = PatternFill("solid", fgColor=LIGHT)
_THIN  = Side(style="thin", color="D9D9D9")
_MED   = Side(style="medium", color="9E9E9E")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_BORDER_HEADER = Border(left=_MED, right=_MED, top=_MED, bottom=_MED)

# Column spec — (header_text, width, num_format, align)
_COLUMNS = [
    ("DATE",                    11, "General",         "center"),
    ("MACHINE NAME",            14, "General",         "center"),
    ("MACHINE NO.",             12, "General",         "center"),
    ("TYPES",                   10, "General",         "center"),
    ("ITEM CODE",               14, "General",         "left"),
    ("Running Hours",           13, '#,##0.00;-#,##0.00;"-"', "right"),
    ("Ideal Weight (KG)",       16, '#,##0.0;-#,##0.0;"-"',  "right"),
    ("Pcs",                     11, '#,##0;-#,##0;"-"',       "right"),
    ("Weight",                  12, '#,##0.0;-#,##0.0;"-"',   "right"),
    ("Wt./Pc.",                 10, '0.0000;-0.0000;"-"',     "right"),
    ("Ideal Output Per Hour",   18, '#,##0.0;-#,##0.0;"-"',  "right"),
    ("Actual Output Per Hour",  18, "General",                "center"),
    ("Output Efficiency",       16, "General",                "center"),
    ("Compound Cost (Rs)",      18, '#,##0.0;-#,##0.0;"-"',  "right"),
]

_PLAN_PLACEHOLDER = ""    # blank for actuals-only columns in plan report
_HDR_ROW  = 5
_DATA_ROW = 6


def _mc_num(label: str) -> str:
    """Extract the numeric part from 'M/C-3' → '3'."""
    m = re.search(r"(\d+)$", label or "")
    return m.group(1) if m else label


def _month_label(em: str) -> str:
    """'2026-07' → 'Jul-2026'."""
    try:
        import datetime
        y, mo = int(em[:4]), int(em[5:7])
        return datetime.date(y, mo, 1).strftime("%b-%Y")
    except Exception:
        return em


def _write_cell(ws, row: int, col: int, value, num_fmt: str, align: str,
                bold: bool = False, fill=None, font_color: str = "000000",
                border=None):
    c = ws.cell(row=row, column=col)
    c.value = value
    if num_fmt and num_fmt != "General" and not isinstance(value, str):
        c.number_format = num_fmt
    c.font = Font(name=_FONT, bold=bold, color=font_color, size=10)
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    if fill:
        c.fill = fill
    c.border = border or _BORDER
    return c


def _write_title_block(ws, report_label: str, em: str, machine_filter: str = ""):
    """Write rows 1–4 (title block above the header row)."""
    month_str = _month_label(em)
    titles = [
        f"REPORT - {report_label}",
        f"Machine Planning — Pipe Production Plan   [{month_str}]",
        machine_filter or "All Extrusion Machines",
        "",   # blank separator before header
    ]
    n_cols = len(_COLUMNS)
    for r, text in enumerate(titles, start=1):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=n_cols)
        c = ws.cell(row=r, column=1)
        c.value = text
        if r == 1:
            c.font = Font(name=_FONT, bold=True, size=13, color=WHITE)
            c.fill = _NAVY_FILL
            c.alignment = Alignment(horizontal="center", vertical="center")
        elif r == 2:
            c.font = Font(name=_FONT, bold=True, size=11, color=NAVY)
            c.alignment = Alignment(horizontal="center", vertical="center")
        else:
            c.font = Font(name=_FONT, size=10, color="595959")
            c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 18


def _write_header_row(ws):
    """Write row 5 (column headers)."""
    for col, (hdr, width, _, align) in enumerate(_COLUMNS, start=1):
        c = ws.cell(row=_HDR_ROW, column=col)
        c.value = hdr
        c.font = Font(name=_FONT, bold=True, color=WHITE, size=10)
        c.fill = _NAVY_FILL
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = _BORDER_HEADER
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.row_dimensions[_HDR_ROW].height = 36


def _build_rows(
    result: EngineResult,
    machine_filter: Optional[Set[str]] = None,
) -> List[tuple]:
    """
    Build data rows in (machine, item_code) order.
    Returns list of 14-tuples matching _COLUMNS order (incl. Compound Cost).
    """
    date_label = _month_label(result.effective_month)
    cost_map: Dict[str, float] = getattr(result, "effective_costs", {}) or {}
    rows = []

    # Flatten assignments: (machine, item) → AssignedPortion
    for item in sorted(result.items, key=lambda x: (x.item_code,)):
        for a in item.assignments:
            if machine_filter and a.machine not in machine_filter:
                continue
            if not item.has_weight or not item.has_machine:
                continue
            # Per-assignment compound cost: assignment's share of fresh × rate
            mat_upper = item.material.upper()
            cost_per_kg = cost_map.get(mat_upper)
            if cost_per_kg is not None and item.material_kg > 0:
                fresh_frac = item.fresh_compound_kg / item.material_kg
                a_cost: object = round(a.material_kg * fresh_frac * cost_per_kg, 1)
            else:
                a_cost = _PLAN_PLACEHOLDER
            rows.append((
                a.machine,                      # sort key
                item.item_code,                 # sort key
                date_label,                     # DATE
                a.machine,                      # MACHINE NAME
                _mc_num(a.machine),             # MACHINE NO.
                item.material,                  # TYPES
                item.item_code,                 # ITEM CODE
                round(a.hrs, 2),                # Running Hours
                round(a.material_kg, 1),        # Ideal Weight (KG)
                round(a.qty_pcs, 0),            # Pcs
                round(a.material_kg, 1),        # Weight (= Ideal Weight for plan)
                item.weight_per_pc_kg or 0.0,   # Wt./Pc.
                round(item.rate_kg_per_hr, 1),  # Ideal Output Per Hour
                _PLAN_PLACEHOLDER,              # Actual Output Per Hour
                _PLAN_PLACEHOLDER,              # Output Efficiency
                a_cost,                         # Compound Cost (Rs)
            ))

    # Sort by machine number, then item code
    def _sort_key(r):
        mc_num = int(r[0].split("-")[-1]) if "-" in r[0] else 0
        return (mc_num, r[1])

    rows.sort(key=_sort_key)
    # Return only the 13 data columns (strip sort keys)
    return [r[2:] for r in rows]


def _write_data_rows(ws, data_rows: List[tuple]):
    """Write data rows starting at _DATA_ROW."""
    for ri, row_vals in enumerate(data_rows):
        r = _DATA_ROW + ri
        # Alternate row shading
        fill = _LIGHT_FILL if ri % 2 == 0 else None
        for ci, (val, (_, _, nfmt, align)) in enumerate(zip(row_vals, _COLUMNS), start=1):
            _write_cell(ws, r, ci, val, nfmt, align, fill=fill)


def _add_totals_row(ws, data_rows: List[tuple], n_data: int):
    """Append a totals row for numeric columns."""
    r = _DATA_ROW + n_data
    totals = [None] * len(_COLUMNS)
    totals[0] = "TOTAL"   # DATE col
    # Sum numeric columns: Running Hours (5), Ideal Weight (6), Pcs (7), Weight (8), Cost (13)
    for col_idx in (5, 6, 7, 8, 13):
        vals = []
        for row_vals in data_rows:
            v = row_vals[col_idx]
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
        totals[col_idx] = round(sum(vals), 2) if vals else None

    for ci, (val, (_, _, nfmt, align)) in enumerate(zip(totals, _COLUMNS), start=1):
        c = ws.cell(row=r, column=ci)
        c.value = val
        if val is not None and not isinstance(val, str):
            c.number_format = nfmt
        c.font = Font(name=_FONT, bold=True, color=WHITE, size=10)
        c.fill = _TERRA_FILL
        c.alignment = Alignment(horizontal="right" if ci > 1 else "left", vertical="center")
        c.border = _BORDER_HEADER


def _build_workbook(
    result: EngineResult,
    report_label: str,
    machine_filter: Optional[Set[str]] = None,
    filter_label: str = "",
) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = f"Report-{report_label}"
    ws.freeze_panes = f"A{_DATA_ROW}"

    _write_title_block(ws, report_label, result.effective_month, filter_label)
    _write_header_row(ws)
    data_rows = _build_rows(result, machine_filter)
    _write_data_rows(ws, data_rows)
    _add_totals_row(ws, data_rows, len(data_rows))

    # Auto-filter on header row
    ws.auto_filter.ref = (
        f"A{_HDR_ROW}:{get_column_letter(len(_COLUMNS))}{_HDR_ROW + len(data_rows)}"
    )
    return wb


def _wb_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


# ── Public API ───────────────────────────────────────────────────────────────

def report_11_bytes(result: EngineResult) -> bytes:
    """Report-11: all pipe machines combined."""
    wb = _build_workbook(result, "11", machine_filter=None,
                         filter_label="All Extrusion Machines")
    return _wb_bytes(wb)


def report_11x_bytes(result: EngineResult, group: str) -> bytes:
    """Report-11A / 11B / 11C / 11D: machine-group split.

    `group` is one of 'A' | 'B' | 'C' | 'D'.
    Machine membership comes from REPORT_11_GROUPS (config-driven).
    """
    group = group.upper()
    machines = REPORT_11_GROUPS.get(group, [])
    label = f"11{group}   [{', '.join(machines)}]"
    wb = _build_workbook(
        result,
        f"11{group}",
        machine_filter=set(machines),
        filter_label=", ".join(machines),
    )
    return _wb_bytes(wb)


# ═══════════════════════════════════════════════════════════════════════════
# REPORT-12 — MOULDING PLAN (MP-3 fittings)
# ═══════════════════════════════════════════════════════════════════════════
#
# Exact plant layout: header at row 6 (rows 1-5 = title block).
# Columns (16 total):
#   DATE | MATERIAL | ITEM CODE | Moulding Machine | Mould Cavity |
#   Run Cavity | No. of Cycle | Pcs | Wt in Kgs | Cycle Time |
#   Running Hours | Ideal Output Per Hour | Actual Output Per Hour |
#   Output Efficiency | Rejection Pcs | Rejection Kg
#
# PLAN fields populated: material, item code, machine, mould cavity,
#   run cavity (=mould cavity), no. of cycle, pcs, wt in kgs,
#   cycle time (sec), running hours, ideal output per hour.
# Actuals-only columns left blank: actual output per hour,
#   output efficiency, rejection pcs, rejection kg.

_R12_COLS = [
    "DATE",
    "MATERIAL",
    "ITEM CODE",
    "Moulding Machine",
    "Mould Cavity",
    "Run Cavity",
    "No. of Cycle",
    "Pcs",
    "Wt in Kgs",
    "Cycle Time",
    "Running Hours",
    "Ideal Output Per Hour",
    "Actual Output Per Hour",
    "Output Efficiency",
    "Rejection Pcs",
    "Rejection Kg",
    "Compound Cost (Rs)",
]
_R12_N_COLS = len(_R12_COLS)   # 17


def report_12_bytes(result: "FittingEngineResult") -> bytes:  # type: ignore[name-defined]
    """Return Report-12 planning .xlsx for the given fitting engine result."""
    from mp_engine import FittingEngineResult, FittingItemResult, FittingAssignedPortion

    wb = Workbook()
    ws = wb.active
    ws.title = "Report-12"

    month  = result.effective_month      # e.g. "2026-07"
    seg    = result.segment

    # ── Style helpers ────────────────────────────────────────────────────────
    title_font  = Font(bold=True, size=13, color="FFFFFF")
    header_font = Font(bold=True, size=10)
    plan_font   = Font(color="1F3864")   # navy
    plan_fill   = PatternFill("solid", fgColor="EBF0FA")
    sub_fill    = PatternFill("solid", fgColor="D9E1F2")

    thin  = Side(style="thin")
    med   = Side(style="medium")
    bord_header = Border(left=med, right=med, top=med, bottom=med)
    bord_data   = Border(left=thin, right=thin, top=thin, bottom=thin)
    bord_total  = Border(left=med, right=med, top=med, bottom=med)
    center_aln  = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_aln    = Alignment(horizontal="left",   vertical="center")

    def _hfmt(cell: Any, val: Any) -> None:
        cell.value     = val
        cell.font      = header_font
        cell.fill      = sub_fill
        cell.border    = bord_header
        cell.alignment = center_aln

    def _dfmt(cell: Any, val: Any, bold: bool = False) -> None:
        cell.value     = val
        cell.font      = Font(bold=bold, size=10, color="1F3864")
        cell.fill      = plan_fill
        cell.border    = bord_data
        cell.alignment = left_aln

    # ── Title block (rows 1-5) ───────────────────────────────────────────────
    _last_col = get_column_letter(_R12_N_COLS)

    ws.merge_cells(f"A1:{_last_col}1")
    t1 = ws["A1"]
    t1.value     = f"PRAYAG PIPES & FITTINGS — MOULDING SECTION"
    t1.font      = title_font
    t1.fill      = PatternFill("solid", fgColor="1F3864")
    t1.alignment = center_aln

    ws.merge_cells(f"A2:{_last_col}2")
    t2 = ws["A2"]
    t2.value     = "REPORT-12  ·  PRODUCTION PLAN (FITTINGS)"
    t2.font      = Font(bold=True, size=11, color="1F3864")
    t2.alignment = center_aln

    ws.merge_cells(f"A3:{_last_col}3")
    t3 = ws["A3"]
    t3.value     = f"Month: {month}   |   Segment: {seg}"
    t3.font      = Font(size=10, italic=True)
    t3.alignment = center_aln

    ws.merge_cells(f"A4:{_last_col}4")
    t4 = ws["A4"]
    t4.value     = "Status: MACHINE PLAN (actuals-only columns left blank)"
    t4.font      = Font(size=9, italic=True, color="C55A11")
    t4.alignment = center_aln

    ws.merge_cells(f"A5:{_last_col}5")
    # blank spacer row

    # ── Header row 6 ─────────────────────────────────────────────────────────
    for col_idx, col_name in enumerate(_R12_COLS, start=1):
        cell = ws.cell(row=6, column=col_idx)
        _hfmt(cell, col_name)
    ws.row_dimensions[6].height = 36

    # ── Column widths ─────────────────────────────────────────────────────────
    _widths = [12, 9, 14, 18, 12, 12, 13, 10, 12, 12, 14, 20, 22, 16, 14, 12, 18]
    for i, w in enumerate(_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Data rows (row 7 onward) ──────────────────────────────────────────────
    row = 7
    machine_totals: dict = {}   # for summary at bottom

    routable_items = [
        it for it in result.items
        if it.has_weight and it.has_machine and it.assignments
    ]
    # Sort by material, then item_code for readability
    routable_items = sorted(routable_items, key=lambda x: (x.material, x.item_code))

    fit_cost_map: Dict[str, float] = getattr(result, "effective_costs", {}) or {}

    for it in routable_items:
        # Fresh fraction is constant across all assignments for this item
        if it.material_kg > 0:
            fresh_frac = it.fresh_compound_kg / it.material_kg
        else:
            fresh_frac = 0.0
        item_cost_per_kg = fit_cost_map.get(it.material.upper())

        for a in it.assignments:
            mc          = a.machine
            qty_a       = a.qty_pcs
            mat_kg_a    = a.material_kg
            hrs_a       = a.hrs
            cavity      = it.cavity
            cycle       = it.cycle_time_sec
            ideal_pps   = it.pcs_per_hr  # cavity × 3600 / cycle  (or fallback)
            wt_a        = round(qty_a * (it.weight_per_pc_kg or 0.0), 3)
            num_cycles_a = round(qty_a / cavity) if (cavity and cavity > 0) else None

            if item_cost_per_kg is not None:
                a_cost_r12 = round(a.material_kg * fresh_frac * item_cost_per_kg, 1)
            else:
                a_cost_r12 = None

            row_vals = [
                month,           # DATE (planning month)
                it.material,     # MATERIAL
                it.item_code,    # ITEM CODE
                mc,              # Moulding Machine
                cavity,          # Mould Cavity
                cavity,          # Run Cavity (plan = mould cavity)
                num_cycles_a,    # No. of Cycle
                round(qty_a),    # Pcs
                wt_a,            # Wt in Kgs
                cycle,           # Cycle Time (sec)
                round(hrs_a, 3), # Running Hours
                round(ideal_pps, 2) if ideal_pps else None,  # Ideal Output Per Hour
                None,            # Actual Output Per Hour (blank — plan)
                None,            # Output Efficiency (blank — plan)
                None,            # Rejection Pcs (blank — plan)
                None,            # Rejection Kg (blank — plan)
                a_cost_r12,      # Compound Cost (Rs)
            ]
            for col_idx, val in enumerate(row_vals, start=1):
                _dfmt(ws.cell(row=row, column=col_idx), val)

            # Accumulate machine totals
            if mc not in machine_totals:
                machine_totals[mc] = {"hrs": 0.0, "pcs": 0.0, "wt_kg": 0.0}
            machine_totals[mc]["hrs"]   += hrs_a
            machine_totals[mc]["pcs"]   += qty_a
            machine_totals[mc]["wt_kg"] += wt_a
            row += 1

    # ── Coverage gaps block ───────────────────────────────────────────────────
    row += 1
    ws.merge_cells(f"A{row}:{_last_col}{row}")
    gap_cell = ws.cell(row=row, column=1)
    no_wt    = len(result.coverage_gaps.no_weight)
    no_mc    = len(result.coverage_gaps.no_machine)
    gap_cell.value = (
        f"Coverage: {len(routable_items)} items planned  |  "
        f"No-weight flagged: {no_wt}  |  "
        f"No-machine (unroutable): {no_mc}  |  "
        f"Material-level fallback used: {result.n_route_estimated} items"
    )
    gap_cell.font      = Font(bold=True, italic=True, size=9, color="C55A11")
    gap_cell.alignment = left_aln
    row += 2

    # ── Per-machine summary ───────────────────────────────────────────────────
    ws.merge_cells(f"A{row}:{_last_col}{row}")
    ws.cell(row=row, column=1).value = "MACHINE SUMMARY"
    ws.cell(row=row, column=1).font  = Font(bold=True, size=10, color="FFFFFF")
    ws.cell(row=row, column=1).fill  = PatternFill("solid", fgColor="1F3864")
    ws.cell(row=row, column=1).alignment = center_aln
    row += 1

    sum_headers = ["Machine", "Planned Hours", "Total Pcs", "Total Wt (kg)"]
    for ci, h in enumerate(sum_headers, start=1):
        c = ws.cell(row=row, column=ci)
        _hfmt(c, h)
    row += 1

    for mc in sorted(machine_totals):
        t = machine_totals[mc]
        _dfmt(ws.cell(row=row, column=1), mc, bold=True)
        _dfmt(ws.cell(row=row, column=2), round(t["hrs"], 3))
        _dfmt(ws.cell(row=row, column=3), round(t["pcs"]))
        _dfmt(ws.cell(row=row, column=4), round(t["wt_kg"], 2))
        row += 1

    return _wb_bytes(wb)
