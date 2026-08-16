---
name: Moulding BAND_ORDER hardcoded list
description: BAND_ORDER in mgmt_moulding_summary.py is a fixed list, not read live; _parse_summary_roster returns a tuple now
---

## Rule
`BAND_ORDER = ["150","200","250","275","350","450"]` is **hardcoded** — copied from the SUMMARY tab by inspection, not read dynamically from the workbook.

## Implication
`_parse_summary_roster` now returns `(roster, warnings)` — callers **must unpack**:
```python
roster, roster_warnings = _parse_summary_roster(summary_vals)
```
A bare `roster = _parse_summary_roster(...)` will fail (assigns a tuple to roster).

## Unknown-band detection (R-06)
Any numeric value in col 0 of the SUMMARY tab that is not in BAND_ORDER triggers a warning surfaced in the template under `data.roster_warnings`. Machines in unknown bands are excluded.  
**Why:** Silent drops are the failure mode — if the workbook adds a new tonnage, it would disappear without trace. Loud failure is better (R-06).

## If a new band is added to the workbook
Add it to `BAND_ORDER` in `mgmt_moulding_summary.py`. Also extend `_BAND_RE` in `_mould_to_band()` if the mould codes use that tonnage value.
