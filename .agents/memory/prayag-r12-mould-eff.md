---
name: PTMT Moulding %age in Efficiency (Report 12)
description: Data sourcing, formula, and efficiency gap for Mgmt Report 12.
---

## Data source
- Per-mould production: **Report-9** tab in monthly PTMT planning workbooks (NOT Report-7).
  - Report-9 has ONE block per workbook (the workbook's month only).
  - Some mould codes appear on 2 rows in Report-9 (APR: 8 duplicates) — must **aggregate**, not overwrite.
  - Parser: `parse_ptmt_report9` in parsers.py → `load_ptmt_report9(ym)` in sheets.py.
  - APR total pcs = 5,211,697 vs spec 5,211,693 (+4 rounding ✓).

## Formula columns
- **Actual M/C Run Min** (spec's "Actual") = `pcs × ct / 60` = Report-9 C11.
  - APR sum = 918,451 — exact match to spec ✓.
  - C10 (MOULD UTILISATION IN HOURS) = same formula in hours; C11 = unlabeled minutes.
- **Ideal M/C Run Min** (spec's "Ideal") = from ANNUAL PTMT Moulding %age in Efficiency 26-27 workbook.
  - APR spec ideal = 891,279 ≠ 918,451 (formula). Uses a DIFFERENT design cycle-time table.
  - Annual workbook **not in Drive** — ideal is not reproducible from available data.
- **Efficiency** = Ideal / Actual.  Not computable. Spec: APR 97.04%, MAY 101.66%, TOTAL 100.53%.

## Why Report-7 fails
- Report-7 col-20 ("Actual M/C Run in Min") = col-17 ("Ideal M/C Run in Min") = `pcs × std_ct / 60`.
  Both columns are formula-derived from the same standard cycle time — there is no measured actual in R7.
  Using R7 as the pcs source also overcounts (double-counts some moulds) vs Report-9.

## Efficiency interpretation
- Efficiency = Ideal (measured actual time) / Actual (formula standard time).
- < 100%: machine ran longer than standard predicts → slow machine.
- > 100%: machine ran faster → ahead of standard.
- The design cycle time in the annual workbook is LOWER than the scheduling standard in Report-9.
  That's why APR formula (918 K) > spec ideal (891 K) → aggregate eff = 97%.

**Why:** ideal and actual are sourced from two different cycle-time tables (design vs scheduling standard).

## Report 12 template
- Shows per-mould pcs, run count, ideal_min (formula), std_min (= spec actual, exact match).
- Efficiency and actual M/C Run Min columns show "—" with clear sourcing note.
- Reconciliation badge surfaces spec anchors vs our computed figures.
