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
- a ``str`` -> written literally (e.g. "awaiting", "n/a").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


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


@dataclass
class ReportModel:
    rid: str
    label: str
    plant: str
    ym: str
    month_disp: str
    sheets: list = field(default_factory=list)
    available: bool = True
    flags: list = field(default_factory=list)
    headline: Optional[str] = None         # short headline for the index row
