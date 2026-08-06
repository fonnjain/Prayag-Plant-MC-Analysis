---
name: Report-15 date parsing quirk
description: PIPE Report-15 dates arrive as "'Jul 1, 2026'" (Sheets text-prefix apostrophe + "Month D, Year" format) — _parse_date_cell_manpower must handle both.
---

## Rule
`_parse_date_cell_manpower` in `parsers.py` must:
1. Strip a leading `'` (apostrophe) before attempting any parse — Google Sheets stores text-forced cells with a leading single-quote that the API includes in the raw value.
2. Include `"%b %d, %Y"` and `"%b %d, %y"` in the format list to handle "Jul 1, 2026" / "Jul 01, 2026" variants.

**Why:** PIPE Report-15 stores daily dates as text-prefixed strings ("'Jul 1, 2026'"). Without the strip + format, `parse_yield_report15` returned 0 records for every month, causing `recompute_wastage` to fail with "No Report-15 data found."

**How to apply:** Any time a new parser reads date cells from PIPE Report-15 (or any sheet that uses Sheets text-prefix), check if `_parse_date_cell_manpower` handles the format. The fix (strip `'` + `%b %d, %Y`) is now baked into the function so no special handling is needed per-parser.

**Affected callers (as of Aug 2026):** `parse_yield_report15`, `parse_yield_report13`, `parse_yield_report14`, `parse_manpower`, `_parse_yield_daily_pcs_generic`. All benefit automatically.
