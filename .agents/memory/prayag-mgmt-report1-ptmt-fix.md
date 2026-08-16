---
name: Report-1 PTMT R&P fix and R-24 divergence
description: How PTMT (and Tank) records are sourced for the three Part-B sections of Management Report 1 (R&P, Ideal Power, Ideal Labour), and how the R-24 June divergence is documented.
---

## Record sourcing rule
`_accumulate_seg_gross_reject` must be fed from TWO sources:
1. `get_records()` for ANNUAL_SOURCES plants (Pipe/Fittings/Garden/HDPE) — monthly grain, `total_count` = NET
2. `get_daily_records()` filtered to `_DAILY_ONLY_PLANTS` (PTMT/TANK/TANK_VN/TANK_WB) — daily grain, with `is_finishing=False` guard

`_DAILY_ONLY_PLANTS` lives as a module-level frozenset in `mgmt_labour_power.py`.

## PTMT gross→net conversion
For PTMT daily matrix records, `total_count` = GROSS (includes rejection), unlike annual records where `total_count` = NET.
The accumulator has a `_gross_plants = frozenset({"PTMT"})` branch that computes `net = max(0, gross - reject)` — consistent with `accum_record_kg` / `get_segment_prod_kg`.

## R-22: grinding/regrind exclusion
The `is_finishing=True` filter in `daily_supp` excludes all GRINDER-* machines from the supplemental PTMT records. This applies to ALL THREE sections (R&P, Ideal Power, Ideal Labour) via the shared `sgr` dict.

## Two accepted PTMT bases (PRAYAG_RULES R-22)
- **Annual basis (mould chain)**: APR 99,262 · MAY 104,729 · JUN 160,478 · JUL 172,639 · total 537,109 kg
- **Daily/Report-5 basis**: APR 99,262 · MAY 104,729 · JUN 147,835 · JUL 172,639 · total 524,465 kg
- These are different sources, not a discrepancy to resolve. Label which is in use.

## R-24: JUN divergence flag
`_PTMT_R24_NOTES` dict in `mgmt_labour_power.py`, keyed by `(fy, ym)`, carries the note, daily, and annual figures.
`_build_reject_prod_section` attaches `r24_note` + `r24_annual` to the affected cell.
Template renders an amber "R-24" badge (with tooltip) in the Net KG cell, plus a visible footnote block below the table.
Only JUN 2026 is flagged for FY2627. Add new entries as further months diverge or reconcile.

## Tank in R&P
Tank is intentionally excluded from `_build_reject_prod_section` (no reject column in records). Tank DOES appear in Ideal Power and Ideal Labour via `_IDEAL_COST_SEGS`. Both sections now populate correctly via the daily supplement.

## R-06 zero-segment guard
`_warn_zero_segments(sgr, all_yms)` fires a WARNING for any segment with all-zero records across the FY.
