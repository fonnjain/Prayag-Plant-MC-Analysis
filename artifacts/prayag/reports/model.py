"""Report data model — a plain, render-agnostic description of one report.

A generator recomputes figures and returns a ``ReportModel``. The xlsx writer
(``xlsx.py``) turns it into a styled workbook. Keeping the model separate from
the writer means a generator never touches openpyxl, and the styling rules
(navy headers, terracotta totals, dd-mm-yyyy, zeros as "-") live in one place.

Cell value conventions (respected by the writer):
- a number  -> numeric cell, formatted per the column ``kind``; a real 0 shows
  as "-" (spec), so zero is honest, not blank.
- ``None``  -> an EMPTY cell: the figure genuinely cannot be computed
  ("needs review"). This is deliberately distinct from a real 0 so the app
  never shows a fake 0%.
- a ``str`` -> written literally (e.g. "AWAITING SOURCE DATA", "n/a", "IDLE").
  The xlsx writer never converts a string to 0 or a blank.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Column:
    key: str
    label: str
    kind: str = "num"     # "text" | "int" | "num" | "kg" | "pct"
    total: bool = False   # does the totals row carry a value in this column?
    width: Optional[int] = None


@dataclass
class Section:
    columns: list
    rows: list                             # each dict keyed by Column.key
    total_row: Optional[dict] = None       # keyed by Column.key (terracotta)
    heading: str = ""                      # optional band above the table


@dataclass
class ReportSheet:
    name: str                              # worksheet tab name
    title: str                             # navy title row
    subtitle: str = ""                     # plant / method / units one-liner
    sections: list = field(default_factory=list)
    provenance: list = field(default_factory=list)  # footer lines
    note: str = ""                         # shown when there is no table (awaiting)
    # cell_comments: {(row_idx, col_key): comment_text} applied after render
    cell_comments: dict = field(default_factory=dict)


@dataclass
class Flag:
    """One row in the Notes tab and/or an Excel cell comment on the affected cell.

    ``cell_sheet`` + ``cell_row_label`` + ``cell_col_key`` identify which cell
    carries the comment (all three must be non-empty for a comment to be placed;
    any blank disables the cell comment without suppressing the Notes row).
    """
    rule: str = ""           # "R-24", "R-26", "R-42", "R-23", …
    section: str = ""        # tab / section name
    month: str = ""          # "APR", "JUN 2026", …
    our_figure: str = ""     # formatted ours
    source_figure: str = ""  # formatted sheet/annual
    difference: str = ""     # formatted delta
    note: str = ""           # human-readable explanation
    cell_comment: str = ""   # short form for the Excel cell comment (≤300 chars)
    cell_sheet: str = ""     # worksheet name where the comment lives
    cell_row_label: str = "" # row identifier (used by serialiser to place comment)
    cell_col_key: str = ""   # Column.key


@dataclass
class ReportModel:
    rid: str
    label: str
    plant: str
    ym: str
    month_disp: str
    sheets: list = field(default_factory=list)
    available: bool = True
    flags: List[Flag] = field(default_factory=list)
    headline: Optional[str] = None         # short headline for the index row
    cover_source: str = ""                 # source workbook description for Cover tab
