# Prayag Production Analytics

A mobile-first Flask dashboard that reads Prayag's real production Google Sheets (by explicit file ID) and computes OEE / utilisation / efficiency / rejection metrics deterministically — Claude only writes narrative prose from already-computed numbers.

## Run & Operate

- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required env: `DATABASE_URL` — Postgres connection string

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `artifacts/prayag/confirm.py` — deterministic four-tier Data Confirmation engine. `build_masters` (roster of machines/segments/moulds/units per plant from full-FY grid), `expected_files_for`, `tier1_completeness`/`tier2_reconciliation`/`tier3_validity`/`tier4_plausibility`, `full_confirm(...)` → {status, score, score_label, issues, tiers, counts, reconciled, summary}. Tier1 checks files/machines/months score plus segment & mould roster coverage (missing-from-data and unknown-in-data, both ways). Tier2 reconciles each sheet's stored TOTAL vs detail, the engine self-reconcile, AND hierarchy (segment == Σ its lines: flags a machine split across segments, a segment total ≠ Σ lines, and output with no segment assigned). Pure; no network. Caller may pass an optional `matcher` (Claude) used only to fuzzy-map leftover machine codes.
- `artifacts/prayag/sources.py` — real Google Sheet file IDs (`ANNUAL_SOURCES`, `DAILY_SOURCES`), `PLANT_NAMES`, `FY_MONTHS`. Source of truth for what gets read.
- `artifacts/prayag/sheets.py` — Drive/Sheets readers + caching. Monthly grid (`get_records`) and daily matrix (`get_daily_records`, `_load_daily`). `detected_sources()` lists everything wired up.
- `artifacts/prayag/parsers.py` — deterministic layout parsers: `parse_mc_detail`/`grid_total_output` (monthly), `parse_daily_matrix` (wide per-date matrix).
- `artifacts/prayag/metrics.py` — `Record` (grain-agnostic), `compute_metrics` (recomputes every ratio), `rollup_by_*`.
- `artifacts/prayag/store.py` — durable manager review store (Replit Postgres via psycopg2, append-only). Two tables: (1) `confirmation_signoffs` for period-level sign-offs — `record`/`effective`/`history`, keyed to (unfiltered period_key, confirmation fingerprint), latest `approve`/`revoke` wins; (2) `confirmation_issue_acks` for per-issue acknowledgements — `ack_record`/`acks_for`, keyed to (unfiltered period_key, stable `confirm.issue_key`), latest `ack`/`unack` wins. Both degrade to a safe no-op (gate stays ON / no acks) when `DATABASE_URL` is absent.
- `artifacts/prayag/verify.py` — deterministic, network-free **read-only** Data Verification assembly. `build_verification(month, monthly_rows, monthly_reports, daily_rows, daily_reports, tol=0.005)` → per plant→machine→month figures with provenance (`source_file_id`/`source_sheet`/`source_ref`/`ideal_source`), plant + grand roll-ups, and three checks (daily-vs-summary, row-vs-total, plant-vs-machines) each PASS/FAIL/NA at 0.5% tol; `rows_to_csv`/`CSV_HEADER` for export. Mirrors the `_mc_key` machine-number join; PIPE daily net-of-rejection FAILs honestly with an annotation (never auto-passed). `source_ref` is the machine's own row label, not a fabricated A1 cell.
- `artifacts/prayag/app.py` — Flask routes; `parse_period` (period→months + `sub_monthly`), `get_data` (grain-aware daily-vs-monthly resolution; also stamps `confirmation` with `period_key`/`fingerprint`/`signoff`/`released`). `/confirmation/approve` + `/confirmation/revoke` (POST) record period sign-offs against the live data state; `/confirmation/ack_issue` + `/confirmation/unack_issue` (POST) record per-issue acknowledgements. `get_data` loads effective acks, marks each issue `acknowledged`, and downgrades status error→warning when every blocking error is acknowledged. Verification routes: `GET /verify` (`?month=`/`?period=YYYY-MM`, defaults to latest data month; degrades to monthly-only on daily `SheetReadError`), `GET /verify.csv` (provenance export), `POST /verify/log` (append-only audit entry — writes NO fact data).
- `artifacts/prayag/templates/*.html` — grain-aware UI.

## Architecture decisions

- Daily-first headline figures: monthly AND FY views compute plant/machine/segment totals by summing the authoritative daily files (one tab per metric), not the monthly summary grid. The grid undercounts (e.g. PIPE May grid ≈81,654 vs daily 107,609), so it is kept only as a reconciliation reference — surfaced as a NON-BLOCKING note (`get_daily_records` already emits the daily-vs-grid gap) and never used to reduce the daily figures. The grid is the headline only for a month with no daily workbook at all; under the daily-only rule a TOTAL daily-read outage on a monthly/FY view shows NO production data with an honest error banner — the lower grid total is never substituted in. `get_data` runs the daily-first path for every period and refuses any grid substitution (both sub-monthly windows and monthly/FY outages). `get_daily_records` isolates per-(plant, month) read failures (a single transient 429 on a cold FY read no longer nukes the whole period) and raises only when every workbook fails.
- All ratios are recomputed in Python from raw cells; stored % cells are never trusted. Claude only writes prose from already-computed numbers.
- Daily data is ingested for EVERY plant with a daily workbook, via a per-plant layout config (`_DAILY_LAYOUTS` in `sheets.py`): `long` (row-per-machine-per-date: PIPE, MOULDING), `matrix` (wide per-date grid: PTMT, HDPE), `blocks` (one per-machine block tab: GARDEN), `tank` (per-item PROD. REPORT: TANK). The ideal denominator (for utilisation/efficiency) degrades by precedence — in-sheet ideal-OUTPUT rate (a plant publishing its own per-machine Ideal Output, e.g. HDPE) → in-sheet ideal-HOURS column (PTMT) → config baseline (baselines.json) → none. The monthly grid's "Ideal Hours" is a flat placeholder (500 for every machine), NOT a real planned-hours baseline, so it is deliberately NOT a precedence step: PIPE/MOULDING have no real shift-pattern baseline and correctly show "baseline not set" (raw hours + output still publish; the ratio is suppressed, never computed against the placeholder). The missing-baseline flag is an advisory, non-blocking warning — it never gates sign-off. baselines.json ships with NO machine entries; only real, business-supplied planned hours may be added (never estimates or the 500-h placeholder). Plants without any baseline still show raw hours + output; utilisation/efficiency are suppressed (not a misleading 0%) with a "No baseline set" notice. HDPE reads its 6 machines (M/C-1…6) from the "Daily Report" matrix tab (the separate DANA support tab is never read) and supplies its own in-sheet Ideal Output (kg/hr → efficiency) plus M/C Run Hour (monthly available hrs → utilisation), so it needs no baselines.json entry. HDPE still HAS a monthly grid (it is in `ANNUAL_SOURCES`), so its completeness roster comes from that grid like the other gridded plants — it is NOT given a fixed injected roster. TANK has no machine identity, so its roster is the daily-reporting items themselves (dynamic, plant-level). PTMT also has no monthly grid, but its roster is the authoritative 55-machine register in `sources.PTMT_GROUPS` (5 process groups) — so completeness is held to all 55 (a never-reporting machine is a gap, not silently dropped) and each machine's process group + finishing flag come from that file, with `_ptmt_group`'s string heuristic kept only as a fallback for unknown codes.
- `_mc_key` joins daily↔monthly machines on M/C-n or MACHINE-n only (ignores SOCKET/Grinder/die codes) to prevent mis-joins.
- Per-day ideal hours = monthly ideal hours ÷ the machine's active days that month, so a full-month daily rollup reconciles exactly to the monthly grid (verified: 831 actual / 5000 ideal hrs both paths).
- In `compute_metrics`, daily rows with `shift_len_min > 0` are true shift-logs (time model, feed OEE); daily-matrix rows (`shift_len_min == 0`) carry hours/output directly like monthly rows.
- For sub-monthly windows, monthly totals cannot be sliced into a partial month, so `get_data` shows only daily-capable plants and banners the omission rather than mixing in non-sliceable monthly totals.
- Data Confirmation runs four deterministic tiers on every page over the UNFILTERED period rows (a plant/machine filter never makes the dataset look incomplete). The full-FY monthly grid is the master roster; a blank in a later period is a completeness *gap*, never an implicit zero. Severity gating: validity / internal-reconcile-mismatch / no-data = error (figure shows "needs review"); completeness, sheet-reconcile-off, plausibility = warning. Claude (`match_codes`, `summarize_confirmation` in `narrative.py`) only fuzzy-matches leftover machine codes and writes prose from the already-computed issue list — it never reads or computes a figure.

## Product

Mobile-first dashboard over Prayag's real production sheets: OEE/utilisation/output-efficiency/rejection by plant, machine, segment, mould, and period. Supports FY/month views (monthly grain) and sub-monthly windows (Yesterday / 7d / 30d / custom) which surface true per-day data for daily-capable plants, with deterministic reconciliation against the monthly grid.

## User preferences

- Brand colours: navy `#1F3864`, terracotta `#C55A11`. Dates displayed dd-mm-yyyy.
- Read real production sheets by explicit file ID; never fabricate or silently fall back to demo numbers.

## Gotchas

- PIPE daily Output is net-of-rejection while the monthly grid Output is gross, so PIPE output reconciliation is ~6.6% off by design — surfaced as an honest warning, not hidden. GARDEN reconciles exactly.
- Daily ingestion covers every plant with a daily workbook (PIPE, MOULDING, GARDEN, HDPE, PTMT, TANK). PIPE/GARDEN/MOULDING have monthly grids; PTMT/TANK do not. PTMT shows raw output plus its in-sheet ideal column without a grid cross-check; HDPE also has no grid cross-check but publishes its own in-sheet Ideal Output (kg/hr) and M/C Run Hour, so its utilisation and efficiency are computed from the daily matrix itself. TANK is logged per item with no machine identity, so it shows plant-level output + item detail and no per-machine OEE. PTMT regrind/grinder KG carries `is_finishing=True` and is excluded from the plant total to avoid double-counting (the Grinding segment still shows it when viewed alone).
- Run the app via the workflow (`PORT=21800 python3 app.py`); for ad-hoc curl use `localhost:80/...` through the shared proxy.
- Manager sign-off releases the ERROR gate only (warnings already publish). There is no login, so the approver name is typed into the form and recorded as-is — it is an attestation, not authenticated identity. A sign-off binds to the exact data state (fingerprint); any change to the underlying sheets re-gates the figures automatically and the prior sign-off no longer applies.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
