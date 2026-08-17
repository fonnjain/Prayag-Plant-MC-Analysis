---
name: Pipe Moulds Summary (Report 13)
description: Data sourcing and parser quirks for Management Report 13 — (D) Pipe Moulds Summary
---

## Report 13 — (D) Pipe Moulds Summary

### Cumulative report format (FY26-27)
Reports 17–21 in the monthly PIPE workbook are **cumulative**: each new monthly
workbook appends a 4-column block (pcs / kg / gross_kg / util_hrs) for the new
month.  The JUL'26 workbook contains APR+MAY+JUN+JUL.  **Never sum across
multiple monthly workbooks — this double-counts earlier months.**

**Correct approach**: read Reports 17–21 from the LATEST available workbook
only, then sum all month blocks within that single file.

Parser: `parsers.parse_cumulative_mould_fy(values, group=..., n_months=4)`
Loader: `sheets.load_pipe_moulds_fy(fy="2627")`

### Annual tab format (FY25-26)
Per-material annual tabs (`CPVC Mould Summary (25-26)`, etc.) in the finalized
workbook ID `1N5W8QQmIAnWqbkATCruLyjSriqDJ6JtkwgIIXOm1dSk`.

**Key quirk — column alignment differs between TOTAL row and data rows:**
- CPVC tab: no leading blank; sub-header has 'MOULD' at col 1, 'PRODUCTION IN PCS' at col 6.  TOTAL row starts data at col 0 (no identity cols).  Data rows follow sub-header (pcs at col 6).
- AGRI/UPVC/SWR tabs: leading blank at col 0; 'MOULD' at col 2, pcs at col 7.  TOTAL row also has blanks (consistent with data rows).

**Fix**: find `mould_c` from the sub-header row itself (not the row above it).
The TOTAL cross-check values are unreliable for CPVC; use summed data rows.

Parser: `parsers.parse_annual_mould_summary_apr_jul(values, group=...)`
Loader: `sheets.load_pipe_moulds_annual_2526()`

### Validated metrics (FY26-27 vs spec)
All 15 reconciliation checks PASS:
- Pcs per material: exact matches (CPVC 2,986,681 / UPVC 1,678,108 / SWR 589,703 / AGRI 250,631 / PPR 35,659)
- KG per material: exact matches
- n_run (Moulds Run): exact matches (145/139/74/83/16 = 457 total)
- Avg/Month = KG / n_months (NOT pcs / n_months!)

### Not reproducible from Report-17..21
- **n_total** (full mould registry count): monthly reports only list moulds that ran.  Full registry (spec: CPVC 210 / UPVC 170 / SWR 89 / AGRI 129 / PPR 10 = 608) needs the FY26-27 annual workbook (not yet in Drive).
- **Run hours**: formula-derived (pcs × ct / 3600) gives ~30,900 h total vs spec 17,249 h.  Source of spec hours unknown (possibly actual tracked hours from a separate system).  FY25-26 formula hours DO match spec.

### PPR specifics
- New material in FY26-27; no FY25-26 history.
- Report-21 in the monthly PIPE workbook; cumulative like Reports 17-20.
- JUL'26 Report-21 shows 16 distinct mould codes, all with production.
- Spec says n_total=10 (unique moulds), n_run=16 (run events, some moulds ran >1 month).

### Sheet TOTAL defect (FY26-27)
The workbook TOTAL row omits PPR from 6 of 7 columns (only n_total includes PPR).
Report always shows the correct inclusive TOTAL and flags the defect in a note.

### Route
`/management-reports/pipe-moulds-summary` → `mgmt_pipe_moulds_summary_view()` → `mgmt_pipe_moulds_summary.py:build_pipe_moulds_summary()`
