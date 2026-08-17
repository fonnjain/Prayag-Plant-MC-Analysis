---
name: PTMT Records API & gross/nett
description: How to fetch and interpret PTMT daily Records; total_count is GROSS for PTMT; is_finishing already set by parser.
---

## Rules

**Use `get_daily_records([ym])`, not `get_records([ym])`.**  
PTMT is a daily-only plant. `get_records` reads ANNUAL_SOURCES and returns no PTMT rows.

**Filter by `r.plant == "PTMT"` then `r.is_finishing`.** The sheets.py PTMT parser (line 2468) strips the "PTMT " prefix from machine names and calls `_ptmt_group(code)` to set `r.segment` and `r.is_finishing` on every Record. You do NOT need to re-lookup the group — just check the field.

```python
all_records, _, _ = sheets.get_daily_records([ym])
records = [
    r for r in all_records
    if r.plant == "PTMT" and not getattr(r, "is_finishing", False)
]
```

`is_finishing=True` marks the grinding/regrind group (excluded from plant output — R-22).

**`total_count` is GROSS for PTMT (mgmt_labour_power.py:1075).**  
Nett output = `total_count − reject_count`.  
If you display `total_count` as "Nett Output" you will overstate by the full rejection tonnage.

**`runner_lumps` is not populated for PTMT.**  
`Record.runner_lumps` accumulates to 0.0 for all PTMT machines — runner and lumps are not separately extracted from the PTMT daily matrix. Treat 0.0 as None (blank, not zero — R-07/R-08). Do not show "0.00%" for runner %; render "—".

**Why:** The PTMT daily Report-5 matrix layout does not have a dedicated runner/lumps column (unlike PIPE). The parser never writes a non-zero value to `runner_lumps` for any PTMT machine.

**How to apply:** Any new PTMT management report or export that needs nett output must subtract reject_count. Any column depending on runner_lumps must render blank.
