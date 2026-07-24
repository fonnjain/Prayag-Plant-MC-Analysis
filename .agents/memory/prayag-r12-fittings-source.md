---
name: Report-12 fittings source fix
description: Fittings kg now sourced from R12 "Weight of Total Production" (not labour sheet); J-vs-M variance flag; lazy import of sources in costing_labour.
---

## Rule
`fitting_prod_kg` in costing_labour_monthly comes from Report-12 "Weight of Total Production" (formula-driven, authoritative), NOT the labour sheet's "Fittings Production" column.

## Why
Labour sheet FY2026-27 "Fittings Production (KGS)" column is MISLABELLED — it contains gross PIECES not kg.  Proof: labour-sheet Apr 2026 = 1,342,290 ≈ R12 piece count 1,340,117; actual R12 kg = 93,839.

## How to apply
- `costing_labour.parse_r12_fittings_kg(values)` — header-based parser, handles both FY layouts (with/without SAP Code column). Returns `weight_of_total_prod`, `wt_in_kgs`, `variance_pct`, `divergent_rows`.
- `costing_labour.load_r12_for_fy(fy, month_labels, token)` — reads Report-12 from each month's PIPE workbook (via `sources.DAILY_SOURCES["PIPE"]["files"]`). Lazy-imports `sources` to avoid polluting test sys.modules.
- `costing_labour.load_labour_fy()` — after parsing Plumbing tab, patches each row: `fitting_prod_kg = fitting_r12_kg` when R12 available; recomputes `total_prod_kg` and `per_kg_labour_cost`.
- New DB columns: `fitting_r12_kg`, `wt_in_kgs_total`, `fitting_kg_source` ("report12"|"labour_sheet"), `fitting_variance_pct`, `fitting_divergent_n`, `fitting_divergent_rows JSONB`.
- `costing_model._DDL_MIGRATION_STMTS` — idempotent ALTER TABLE statements run per-statement in `init_costing_tables()` so one failure doesn't block others.

## Acceptance figures (FY2026-27)
Apr 93,839 kg / May 79,875 kg / Jun 101,512 kg → total 275,226 kg fittings.
Cost: ₹5,791,710 / (637,410 + 275,226 kg) = ≈₹6.35/kg (higher than FY2526 ₹4.157/kg — expected trend).

## FY2525-26 unchanged
975,609 kg fittings for FY2526 comes from R12 as well (R12 is always read; labour sheet was correct for that FY too, so totals match).

## Variance flag
`R12_TOTAL_VARIANCE_WARN_PCT = 2.0` — month-level J-vs-M warning card in template.
Per-row threshold: 5% (flags individual rows in `divergent_rows` JSONB).
June 2026 expected ~4.4% total variance with 81 bad hand-entries in Wt-in-Kgs.

## Test isolation note
`import sources as _src` inside `load_r12_for_fy()` is LAZY (not at module level) so that test_costing.py's `setdefault` stub approach works. Putting it at module level caused the real store to load via sources.py, breaking 3 "no-DB" tests in the full suite.
