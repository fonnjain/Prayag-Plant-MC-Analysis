"""Render a ``ReportModel`` into a styled openpyxl ``Workbook``.

Styling (per the management-report spec):
- Arial throughout.
- Navy ``#1F3864`` title + table headers (white bold text).
- Terracotta ``#C55A11`` totals/subtotal rows.
- Dates dd-mm-yyyy; zeros shown as "-".
- A provenance/validation footer (source workbooks, tie-out result, flags).

Values approach (chosen over live Excel formulas): every figure is written as
the authoritative Python-recomputed VALUE. This guarantees the file opens with
correct numbers everywhere and can never contain a formula error, while the
per-report self-checks (see ``registry.build_and_validate``) provide a stronger
guarantee than a recalc-error scan would.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .model import ReportModel, ReportSheet, Section

NAVY = "1F3864"
TERRA = "C55A11"
WHITE = "FFFFFF"
GREY = "595959"
LIGHT = "F2F2F2"
FONT = "Arial"

_NAVY_FILL = PatternFill("solid", fgColor=NAVY)
_TERRA_FILL = PatternFill("solid", fgColor=TERRA)
_LIGHT_FILL = PatternFill("solid", fgColor=LIGHT)
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _num_fmt(kind: str) -> str:
    """Number format string; the third clause renders a real 0 as "-"."""
    if kind == "int":
        return '#,##0;-#,##0;"-"'
    if kind == "kg":
        return '#,##0;-#,##0;"-"'
    if kind == "pct":
        return '0.0"%";-0.0"%";"-"'
    if kind == "num":
        return '#,##0.0;-#,##0.0;"-"'
    return "General"


def _safe_tab(name: str) -> str:
    bad = set(r'[]:*?/\\')
    cleaned = "".join(c for c in name if c not in bad).strip()
    return (cleaned or "Report")[:31]


def _write_cell(ws, row, col, value, kind, *, bold=False, color=None,
                fill=None, align="right", border=True):
    c = ws.cell(row=row, column=col)
    if isinstance(value, str):
        c.value = value
        c.number_format = "General"
    elif value is None:
        c.value = None                     # genuinely-missing -> blank
    else:
        c.value = value
        c.number_format = _num_fmt(kind)
    c.font = Font(name=FONT, bold=bold, color=color or "000000", size=10)
    if fill is not None:
        c.fill = fill
    if kind == "text" or isinstance(value, str):
        align = "left" if align == "right" else align
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    if border:
        c.border = _BORDER
    return c


def _render_sheet(ws, sheet: ReportSheet):
    ncols = 1
    for sec in sheet.sections:
        ncols = max(ncols, len(sec.columns))
    ncols = max(ncols, 4)
    last_col = get_column_letter(ncols)

    # --- Title ---
    r = 1
    ws.merge_cells(f"A{r}:{last_col}{r}")
    t = ws.cell(row=r, column=1, value=sheet.title)
    t.font = Font(name=FONT, bold=True, size=14, color=NAVY)
    t.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[r].height = 22
    r += 1

    # --- Subtitle ---
    if sheet.subtitle:
        ws.merge_cells(f"A{r}:{last_col}{r}")
        s = ws.cell(row=r, column=1, value=sheet.subtitle)
        s.font = Font(name=FONT, italic=True, size=9, color=GREY)
        s.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
        ws.row_dimensions[r].height = 26
        r += 1
    r += 1  # spacer

    if not sheet.sections:
        ws.merge_cells(f"A{r}:{last_col}{r}")
        n = ws.cell(row=r, column=1, value=sheet.note or "Awaiting source data.")
        n.font = Font(name=FONT, bold=True, size=11, color=TERRA)
        n.alignment = Alignment(horizontal="left", vertical="center")
        r += 2

    col_widths: dict = {}
    for sec in sheet.sections:
        if sec.heading:
            ws.merge_cells(f"A{r}:{last_col}{r}")
            h = ws.cell(row=r, column=1, value=sec.heading)
            h.font = Font(name=FONT, bold=True, size=10, color=NAVY)
            r += 1
        # header row
        for j, col in enumerate(sec.columns, start=1):
            c = _write_cell(ws, r, j, col.label, "text",
                            bold=True, color=WHITE, fill=_NAVY_FILL,
                            align="center")
            col_widths[j] = max(col_widths.get(j, 0), len(str(col.label)) + 2,
                                col.width or 0)
        ws.row_dimensions[r].height = 28
        for cc in ws[r]:
            cc.alignment = Alignment(horizontal="center", vertical="center",
                                     wrap_text=True)
        r += 1
        # data rows
        for ri, row in enumerate(sec.rows):
            fill = _LIGHT_FILL if ri % 2 else None
            for j, col in enumerate(sec.columns, start=1):
                val = row.get(col.key)
                _write_cell(ws, r, j, val, col.kind, fill=fill,
                            align="left" if col.kind == "text" else "right")
                if isinstance(val, str):
                    col_widths[j] = max(col_widths.get(j, 0), len(val) + 2)
            r += 1
        # total row
        if sec.total_row is not None:
            for j, col in enumerate(sec.columns, start=1):
                val = sec.total_row.get(col.key)
                _write_cell(ws, r, j, val, col.kind, bold=True, color=WHITE,
                            fill=_TERRA_FILL,
                            align="left" if col.kind == "text" else "right")
            r += 1
        r += 1  # spacer between sections

    # --- Provenance / validation footer ---
    if sheet.provenance:
        r += 1
        for line in sheet.provenance:
            ws.merge_cells(f"A{r}:{last_col}{r}")
            f = ws.cell(row=r, column=1, value=line)
            f.font = Font(name=FONT, italic=True, size=8, color=GREY)
            f.alignment = Alignment(horizontal="left", vertical="center",
                                    wrap_text=True)
            r += 1

    for j, w in col_widths.items():
        ws.column_dimensions[get_column_letter(j)].width = min(max(w, 10), 40)
    ws.sheet_view.showGridLines = False


def render_workbook(model: ReportModel) -> Workbook:
    wb = Workbook()
    first = True
    used = set()
    sheets = model.sheets or [ReportSheet(name="Report", title=model.label,
                                          note="Awaiting source data.")]
    for sh in sheets:
        name = _safe_tab(sh.name)
        base, k = name, 2
        while name in used:
            name = f"{base[:28]}-{k}"
            k += 1
        used.add(name)
        if first:
            ws = wb.active
            ws.title = name
            first = False
        else:
            ws = wb.create_sheet(title=name)
        _render_sheet(ws, sh)
    return wb


def workbook_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
