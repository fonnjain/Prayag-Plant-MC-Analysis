---
name: Report-1 PTMT R&P fix
description: How PTMT (and Tank) records are sourced for the three Part-B sections of Management Report 1 (R&P, Ideal Power, Ideal Labour).
---

## Rule
`_accumulate_seg_gross_reject` must be fed from TWO sources:
1. `get_records()` for ANNUAL_SOURCES plants (Pipe/Fittings/Garden/HDPE) — monthly grain, `total_count` = NET
2. `get_daily_records()` filtered to `_DAILY_ONLY_PLANTS` (PTMT/TANK/TANK_VN/TANK_WB) — daily grain

`_DAILY_ONLY_PLANTS` lives as a module-level frozenset in `mgmt_labour_power.py`.

## PTMT gross→net conversion
For PTMT daily matrix records, `total_count` = GROSS (includes rejection), unlike annual records where `total_count` = NET.
The accumulator has a `_gross_plants = frozenset({"PTMT"})` branch that computes `net = max(0, gross - reject)` — consistent with `accum_record_kg` / `get_segment_prod_kg`.

**Why:** ANNUAL_SOURCES monthly records carry net production; daily matrix records carry gross output and rejection separately.

## Tank in R&P
Tank is intentionally excluded from `_build_reject_prod_section` (segs list has no "Tank", see comment `# Tank: net only, no reject`). Tank DOES appear in Ideal Power and Ideal Labour via `_IDEAL_COST_SEGS`. Both sections now populate correctly post-fix.

## R-06 zero-segment guard
`_warn_zero_segments(sgr, all_yms)` fires a WARNING for any segment in sgr with all-zero records across the FY. Catches exactly the PTMT-blank scenario before it hides across multiple builds.

## PTMT data note
PTMT net as of Aug 2026: ~716,549 kg (APR+MAY+JUN+JUL). Validation target of 537,109 kg was set from an earlier data state (pre-July completion). Reject figure (~32,796) is within 0.5% of the original target (32,952) — formula and sourcing are correct.
