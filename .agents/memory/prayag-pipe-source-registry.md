---
name: PIPE monthly workbook auto-discovery
description: source_registry.py resolves PIPE monthly workbook file IDs by Drive title search when not pinned in sources.py.
---

## Rule
`_daily_file_id("PIPE", ym)` in sheets.py now falls through to `source_registry.get_pipe_file_id(ym)` for unpinned months.

## Override precedence (highest → lowest)
1. `sources.py` explicit pin — never overwritten by discovery
2. In-process `_mem_cache` — sub-millisecond
3. Postgres `daily_source_registry` table — cross-worker
4. Drive title search via `find_monthly_workbook(year, month)`

## Title pattern
`"Pipe" AND "Fitting" AND MON_ABBREV AND YEAR` (case-insensitive `name contains`).
Verified examples: "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"

## Multiple-match tie-break
1. Prefer owner `preeti.chauhan@prayagindia.com`
2. Else most recently modified (Drive returns newest-first)

**Why:** PIPE workbooks span multiple Drive owners/folders so folder-based discovery is unreliable. Title search works when the Drive connector token has sufficient scope.

## How to apply
- New months auto-register when `recompute_rejection()` / `recompute_wastage()` run (both call `ensure_fy_months_registered()` first).
- A future month not yet in Drive returns None gracefully — never raises, never fabricates.
- The audit panel at /machine-planning/data shows pinned vs discovered per month.
