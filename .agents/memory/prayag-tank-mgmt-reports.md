---
name: Tank management reports (Reports 7-9)
description: Design decisions and data-source quirks for mgmt_tank_summary.py (Reports 7/8/9 — KH/VN/WB)
---

# Tank Management Reports 7–9

## Module
`mgmt_tank_summary.py` — shared builder for all three plants.  
Routes: `/management-reports/tank-kh-summary`, `/management-reports/tank-vn-summary`, `/management-reports/tank-wb-summary`.  
Template: `templates/report_mgmt_tank_summary.html` (transposed: months are columns, two stacked pivot sections).

## Critical `mould` field duality
Same field name, different content depending on source:
- **Daily records** (`get_daily_records()`): `r.mould` = **item code** (e.g., `WCT-3LC-05`)
- **Annual records** (`load_report_records("tank_kh/vn/wb")`): `r.mould` = **product type label** (e.g., `3 Layer Colour`)

The annual parser stops at the second TOTAL row (R-37 guard), so it only returns the first pivot section (by product type).

**Why:** The two sources have different granularity. Daily workbooks record per-item-code; the annual SUMMARY (LTR) tab aggregates by product type. Always check the source before using `r.mould`.

## Item code → (product_type, size_ltr) mapping
`parse_item_code(code)` in the module handles these patterns:
- `{prefix}-{N}L{X}-{sz}` → `"{N} Layer {Light|Colour|Heavy|ISI}"`
- `{prefix}-ISI-{sz}` → `"ISI"`
- `{prefix}-{N}ISI-{sz}` → `"{N} Layer ISI"`

SIZE SUFFIX IS A CODE, NOT ARITHMETIC:
- `-05` = 500 Ltr, `-07` = 750 Ltr (not 700), `-10` = 1000, `-15` = 1500, `-20` = 2000, `-30` = 3000, `-50` = 5000

Unmapped codes → `TankItemCodeError` (R-06); never silently bucketed.

## Known divergences (documented, not adjusted)
- **KH JUN**: our daily 1,419,500 vs sheet 633,500 (R-26, 2.24×, open)
- **KH APR/MAY/JUL**: absent from annual sheet; present in daily (shown from daily, R-07/R-08)
- **VN JUL**: +2,000 Ltr delta
- **WB JUL**: +270,000 Ltr; **WB JUN**: +400 Ltr

## KH data errors (R-35, FY2627 June)
- 23-Jun `WCT-3LC-05`: 90 rej / 10 pcs → 45,000 Ltr rejection (not adjusted)
- 30-Jun `WCT-3LL-10`: 243 pcs from 6 cycles → ~219,000 Ltr excess production (not adjusted)

## Layout quirk
The report is TRANSPOSED vs all other management reports. Months are columns (latest-first), each spanning two sub-columns (Production Ltr | Rejection Ltr). Two separate pivot sections share the same TOTAL row values.
