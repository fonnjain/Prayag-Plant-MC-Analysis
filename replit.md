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

- All ratios are recomputed in Python from raw cells; stored % cells are never trusted. Claude only writes prose from already-computed numbers.
- Daily data is ingested for EVERY plant with a daily workbook, via a per-plant layout config (`_DAILY_LAYOUTS` in `sheets.py`): `long` (row-per-machine-per-date: PIPE, MOULDING), `matrix` (wide per-date grid: PTMT), `blocks` (one per-machine block tab: GARDEN, HDPE), `tank` (per-item PROD. REPORT: TANK). The ideal denominator (for utilisation/efficiency) degrades by precedence — monthly grid → in-sheet ideal column (PTMT) → baseline master → none. Plants without any baseline still show raw hours + output; utilisation/efficiency are suppressed (not a misleading 0%) with a "No baseline set" notice. PTMT/HDPE/TANK have no monthly grid, so their roster is the daily-reporting machines themselves (dynamic, never hardcoded).
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
- Daily ingestion covers every plant with a daily workbook (PIPE, MOULDING, GARDEN, HDPE, PTMT, TANK). PIPE/GARDEN/MOULDING have monthly grids; HDPE/PTMT/TANK do not, so they show raw output (and PTMT's in-sheet ideal column) without a grid cross-check. TANK is logged per item with no machine identity, so it shows plant-level output + item detail and no per-machine OEE. PTMT regrind/grinder KG carries `is_finishing=True` and is excluded from the plant total to avoid double-counting (the Grinding segment still shows it when viewed alone).
- Run the app via the workflow (`PORT=21800 python3 app.py`); for ad-hoc curl use `localhost:80/...` through the shared proxy.
- Manager sign-off releases the ERROR gate only (warnings already publish). There is no login, so the approver name is typed into the form and recorded as-is — it is an attestation, not authenticated identity. A sign-off binds to the exact data state (fingerprint); any change to the underlying sheets re-gates the figures automatically and the prior sign-off no longer applies.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
