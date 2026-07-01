---
name: Pipe Moulds Summary (D reports)
description: Where the (D) Pipe Moulds mould-wise reports live, how they're read, and the headline-column gotcha.
---

# (D) Pipe Moulds Summary — Report-17..20

The (D) "Pipe Moulds Summary" reports are NOT separate workbooks — they are tabs
**inside the monthly PIPE daily workbook** (`DAILY_SOURCES["PIPE"]["files"][ym]`):
Report-17 = CPVC, 18 = UPVC, 19 = SWR, 20 = AGRI (plus summary tabs S-17..20).
No new Drive IDs are ever needed.

**How to read them:** bounded `batch_get(file_id, [4 tabs], token)` in
`sheets.load_pipe_moulds(ym)` — NEVER via the full daily pipeline
(`get_daily_records`), which times out cold (>120 s) for PIPE.

**Layout (`parsers.parse_mould_working`):** row1 title, rows2-3 a two-row header
band, row4 the sheet's own TOTAL row, rows5+ per-mould detail. Columns are
located by header **text** (S.NO / MOULD / CAVITY / PRODUCTION IN PCS /
PRODUCTION IN KG / MOULD UTILISATION), so column reorders survive.

**Headline-column gotcha:** headline kg = the **"PRODUCTION IN KG"** column, NOT
"Weight of Total Production" (a different, adjacent column). Getting these
confused silently changes every total.

**Recompute vs reconcile:** the headline is the recomputed detail-row sum
(`total_kg`); the sheet's stored TOTAL row (`sheet_total_kg`) is kept only to
reconcile against, never trusted as the figure.

**Efficiency stays blank** — the ideal-hour denominator is not filled in these
tabs, so efficiency is `None` (never a fake 0%), per the project-wide rule.

**Completeness gate:** if some (but not all) of the 4 groups parse, the loader
sets `incomplete=True` + `missing=[...]` and the page shows a "Needs review /
Incomplete source" banner — partial totals are never published as if complete.
All 4 missing = an older-FY workbook → simply `available=False` (awaiting
source), not "incomplete".

**June-2026 acceptance baseline** (build-state #18): recomputed kg CPVC 19,591 /
UPVC 27,796 / SWR 33,178 / AGRI 8,586 = grand 89,152; pcs 1,340,117. Each group's
recomputed kg must also equal that tab's own stored TOTAL row.
