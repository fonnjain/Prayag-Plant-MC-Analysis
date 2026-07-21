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
    Returns list of 13-tuples matching _COLUMNS order.
    """
    date_label = _month_label(result.effective_month)
    rows = []

    # Flatten assignments: (machine, item) → AssignedPortion
    for item in sorted(result.items, key=lambda x: (x.item_code,)):
        for a in item.assignments:
            if machine_filter and a.machine not in machine_filter:
                continue
            if not item.has_weight or not item.has_machine:
                continue
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
    # Sum numeric columns: Running Hours (5), Ideal Weight (6), Pcs (7), Weight (8)
    for col_idx in (5, 6, 7, 8):
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
