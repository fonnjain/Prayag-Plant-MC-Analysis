---
name: Recompute from data-entry tabs (Garden / PTMT / Tank)
description: Architecture rule — SUMMARY tabs are derived roll-ups; primary figures MUST come from data-entry tabs. Covers parser design, Jinja2 format quirk, and completeness scope fix.
---

## Rule
SUMMARY tabs = validation delta only. Never source primary production figures from them.

- **Garden Pipe**: aggregate per-machine Records (from MC-1..4 parsed by parse_mc_detail) in app.py report_detail for `garden_summary`. Labour from seg_labour_cache ("Garden Pipe" segment match). SUMMARY tab → validation delta via `get_garden_monthly_summary()`.
- **PTMT**: `_load_annual_family` reads "Month Wise MC" + "PTMT Mould Apr26-Mar27" data-entry tabs via `batch_get`, stores aggregates in `_ptmt_monthly_cache` with `source_kind="data_entry_tabs"`. SUMMARY tab stored in `_ptmt_summary_tab_cache`. Labour joined from seg_labour_cache in app.py. ₹/kg computed as wages ÷ nett_output per month (fixes SUMMARY's stated 3.53 period-mismatch).
- **Tank**: `_load_annual_family` tank_annual_2627 branch also reads SUMMARY (LTR) tab via batch_get, stores in `_tank_summary_ltr_cache`. Validation delta shown in _tank_location_report.

## Parsers added (parsers.py)
- `parse_ptmt_monthly_mc_tab(values)` — handles Layout A (compound-header) and Layout B (metric-block); returns `{ym: {hours, output_kg, reject_kg, runner_kg, lumps_kg}}`.
- `parse_ptmt_mould_tab(values)` — returns `{ym: {run_moulds}}`.
- `parse_tank_summary_ltr(values)` — returns `{ym: ltr_total}`; handles both column-based and row-based layouts.

## sheets.py helpers added
- `get_ptmt_source_kind()` → "data_entry_tabs" | "summary_tab_fallback" | "unknown"
- `get_ptmt_summary_validation()` → SUMMARY tab rows (for delta, NOT primary)
- `get_tank_summary_ltr_validation(family)` → {ym: ltr} from SUMMARY LTR tab

## Completeness scope fix (confirm.py)
`_REPORT_ONLY_KINDS = frozenset({"ptmt_moulds_summary"})` — segment-summary ANNUAL_SOURCES kinds that emit no per-machine Records. Both `expected_files_for` and `_scope_plants` must skip them, or PTMT generates false 0-of-55 machine gaps in monthly-grain completeness checks.

**Why:** `ptmt_moulds_summary` was moved into ANNUAL_SOURCES. Without this exclusion, the completeness check finds PTMT in scope and flags all 55 machines as missing in monthly views.

## Jinja2 format quirk
`{{ "%+,.0f"|format(x) }}` is invalid — Python's `%` formatting doesn't support `,` (thousand separator). Use:
```jinja2
{% if x > 0 %}+{% endif %}{{ "{:,.0f}".format(x) }}
```

## Tests
`tests/test_recompute_sources.py` — 44 fixture-based tests covering parser behavior, garden July=32191 recompute, PTMT aggregation, tank combined=6,810,850, no-labour-fabrication assertions.
