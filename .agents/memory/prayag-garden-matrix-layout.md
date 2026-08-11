---
name: GARDEN run-hours matrix empty-vs-unrecognisable
description: How parse_daily_matrix signals layout-found vs layout-failure to _emit_blocks, and why the GARDEN May/July months hit the wrong warning.
---

## The distinction

`parse_daily_matrix` returns `[]` in two very different situations:
1. **Layout failure** — no date row found (`date_row_idx < 0`) or no date groups built.
2. **Data absence** — layout is fine, but every machine row has run=0, out=0, rej=0, so all rows are skipped.

In the GARDEN Daily Report, case 2 fires for months where operators haven't yet filled in the run-hours columns (e.g. May 2026 had all-zero cells). The old code treated both as case 1 and fired "could not be parsed (layout not recognised)" — wrong.

## Fix: `_layout_found` out-param

`parse_daily_matrix` accepts `_layout_found: Optional[list] = None`. When the date header is detected and groups are built (`groups` is non-empty), `True` is appended to `_layout_found`, even if 0 records are ultimately returned.

Do NOT use the `notes` list for this signal — `notes` is user-visible and existing tests assert `notes == []` for successful parses.

## sheets.py `_emit_blocks` usage

```python
rh_lf: list = []
rh_rows = parsers.parse_daily_matrix(..., _layout_found=rh_lf)
rh_layout_ok = bool(rh_lf)
```

When `rh_parsed == 0`:
- `rh_layout_ok=True` → warning: "matrix has no run hours entered yet"
- `rh_layout_ok=False` → warning: "matrix could not be parsed (layout not recognised)"

**Why:** Month-with-no-data fires a "could not be parsed" alert that misleads operators into thinking the parser is broken when actually the sheet just hasn't been filled in yet.

## Observed pattern (GARDEN FY2627)

- April 2026: 30 records with hours>0=30 (joined correctly, util=27.5%)  
- May 2026: 44 records, hours>0=0, rh_parsed=0 — all Daily Report cells zero (not filled yet)
- June 2026: 62 records with hours>0=60 (joined correctly, util=66.9%)  
- July 2026: 52 records, hours>0=0, rh_parsed>0 — output cells non-zero but run hours all zero

## Utilisation display

`m.util_available = util_ideal > 0` (metrics.py). Since no ideal_hours are assigned when `actual_hours == 0` (sheets.py gate), util_ideal=0 → `util_available=False` → template shows "n/a". The display was already correct; only the warning message needed fixing.
