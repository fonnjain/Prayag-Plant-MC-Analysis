---
name: Corrective Re-plan engine
description: mp_corrective_replan.py computes Plumbing Cap/Day from Report-11/12 daily pcs; p90→mean→NDC fallback; enforces 3 invariants.
---

## Rule
`compute_corrective_replan(month, plan_recs, r11_values, r12_values, as_of_date, file_id)` reads daily Pipe (Report-11) + Fitting (Report-12) pcs, groups by category (CPVC/UPVC/SWR/AGRI × Pipe/Fitting), computes Cap/Day, and projects feasible vs shortfall.

## Capacity method fallback chain
1. ≥ MIN_DAYS_FOR_P90 (=5) non-zero days → 10th-percentile (= "p90", conservative 90%-confidence)
2. 1–4 non-zero days → arithmetic mean (only sensible choice for few-day months like early August)
3. 0 non-zero days → cap_per_day=0, method="none" (NO_DEMONSTRATED_CAPACITY)
Zero-production days excluded from the denominator (absence, not a capacity constraint).

## 3 enforced invariants
1. produced_to_date > 0 → cap_per_day > 0 (emergency fallback if violated, warning emitted)
2. feasible == cap_per_day × working_days_remaining (exact, checked post-compute)
3. total_shortfall < total_remaining (CRITICAL warning if violated)

**Why:** The root-cause bug (Aug 2026) was p90 returning 0 with only 2 production days; adding the mean fallback fixes it. The invariants ensure silent-zero regressions are immediately surfaced.

## Date formats (Issue #5 fix)
Both `_parse_date_cell` (in mp_corrective_replan.py) AND `_parse_date` (in mp_followup.py) now handle:
- "Aug 1, 2026" — Plumbing Report-11/12 (Google Sheets month-name format)
- "1-Aug-2026"  — PTMT master format
Unknown formats raise ValueError in mp_corrective_replan._parse_date_cell (loud fail).
Unknown formats return None in mp_followup._parse_date (silent skip, carries last date forward).

## Data sources
- Report-11: Pipe pcs by item × date (col_type=TYPES=CPVC/UPVC/SWR/AGRI, col_pcs=8)
- Report-12: Fitting pcs by item × date (col_mat=MATERIAL, col_pcs=8 = "Output Production / Pc")
- Both live in the PIPE monthly workbook (same as DAILY_SOURCES["PIPE"]["files"][ym])
- Loaded via `sheets.load_corrective_replan_actuals(ym)` (15-min cache, twin-lock)

## Route
GET /planning/corrective-replan?month=YYYY-MM&as_of=YYYY-MM-DD → XLSX download
Button on /planning page when plant=PIPE.

## Working days
Mon–Sat. `_count_working_days(year, month, as_of)` returns (total, elapsed, remaining).
elapsed = working days strictly BEFORE as_of; remaining = as_of to month end inclusive.
