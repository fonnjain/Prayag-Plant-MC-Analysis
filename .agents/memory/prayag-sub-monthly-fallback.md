---
name: Prayag sub-monthly no-fallback rule
description: Sub-monthly daily windows must never substitute monthly-grid figures; the rule lives in app.py get_data.
---

## Rule
When `sub_monthly=True` and `daily_used=False` (no configured daily files OR a SheetReadError), `get_data` returns **empty rows** with an honest grain banner — it does NOT call `get_records(months)` to fill in monthly totals.

**Why:** Monthly-grid numbers for an in-progress month (e.g. June) are partial or all-zero. Blending them silently into a "Yesterday" or "7-day" window produces misleading figures and was the root cause of the "0/35 machines" completeness errors.

**How to apply:**
- The gate is in the `if not daily_used:` block in `get_data` (app.py). Sub-monthly → `all_rows=[]`, `source_reports=[]`, appropriate grain banner.
- Monthly/FY views (`sub_monthly=False`) still read the monthly grid via `get_records(months)`.
- The **confirmation master rows** (`master_rows = get_records(FY_MONTHS)`) are unaffected — they are always the full-FY monthly grid, used only for roster completeness, not for displayed figures.
- The daily error message (`daily_err`) must not say "fell back to monthly totals" — that wording was removed.
