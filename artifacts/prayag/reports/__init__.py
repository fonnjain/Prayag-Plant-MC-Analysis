"""Management Reports export package.

Each management report is generated as its OWN standalone .xlsx workbook
(one report = one file), plus a "Download all (ZIP)" that bundles every
report for a selected plant + month. A report registry (``registry.py``)
maps a report id to a generator that recomputes the figures from the daily
source workbooks and returns a ``ReportModel``; ``xlsx.py`` renders that
model into a styled openpyxl ``Workbook``.

Core invariant (unchanged): every number is recomputed in Python from the
daily sources — stored %/summary cells are never trusted, and a figure that
cannot be computed is left blank ("needs review"), never shown as a fake 0.
"""
