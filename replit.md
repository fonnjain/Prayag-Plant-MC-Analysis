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

- `artifacts/prayag/sources.py` — real Google Sheet file IDs (`ANNUAL_SOURCES`, `DAILY_SOURCES`), `PLANT_NAMES`, `FY_MONTHS`. Source of truth for what gets read.
- `artifacts/prayag/sheets.py` — Drive/Sheets readers + caching. Monthly grid (`get_records`) and daily matrix (`get_daily_records`, `_load_daily`). `detected_sources()` lists everything wired up.
- `artifacts/prayag/parsers.py` — deterministic layout parsers: `parse_mc_detail`/`grid_total_output` (monthly), `parse_daily_matrix` (wide per-date matrix).
- `artifacts/prayag/metrics.py` — `Record` (grain-agnostic), `compute_metrics` (recomputes every ratio), `rollup_by_*`.
- `artifacts/prayag/app.py` — Flask routes; `parse_period` (period→months + `sub_monthly`), `get_data` (grain-aware daily-vs-monthly resolution).
- `artifacts/prayag/templates/*.html` — grain-aware UI.

## Architecture decisions

- All ratios are recomputed in Python from raw cells; stored % cells are never trusted. Claude only writes prose from already-computed numbers.
- Daily data is ingested only for plants that have BOTH a daily workbook AND a monthly grid baseline (PIPE, GARDEN). `_load_daily` keeps only machines present in the monthly grid; `ideal_rate`/`ideal_hours` are joined from the cached monthly payload. Plants without a monthly baseline (HDPE/PTMT/TANK) are skipped with an explicit warning.
- `_mc_key` joins daily↔monthly machines on M/C-n or MACHINE-n only (ignores SOCKET/Grinder/die codes) to prevent mis-joins.
- Per-day ideal hours = monthly ideal hours ÷ the machine's active days that month, so a full-month daily rollup reconciles exactly to the monthly grid (verified: 831 actual / 5000 ideal hrs both paths).
- In `compute_metrics`, daily rows with `shift_len_min > 0` are true shift-logs (time model, feed OEE); daily-matrix rows (`shift_len_min == 0`) carry hours/output directly like monthly rows.
- For sub-monthly windows, monthly totals cannot be sliced into a partial month, so `get_data` shows only daily-capable plants and banners the omission rather than mixing in non-sliceable monthly totals.

## Product

Mobile-first dashboard over Prayag's real production sheets: OEE/utilisation/output-efficiency/rejection by plant, machine, segment, mould, and period. Supports FY/month views (monthly grain) and sub-monthly windows (Yesterday / 7d / 30d / custom) which surface true per-day data for daily-capable plants, with deterministic reconciliation against the monthly grid.

## User preferences

- Brand colours: navy `#1F3864`, terracotta `#C55A11`. Dates displayed dd-mm-yyyy.
- Read real production sheets by explicit file ID; never fabricate or silently fall back to demo numbers.

## Gotchas

- PIPE daily Output is net-of-rejection while the monthly grid Output is gross, so PIPE output reconciliation is ~6.6% off by design — surfaced as an honest warning, not hidden. GARDEN reconciles exactly.
- Daily ingestion currently covers PIPE + GARDEN only (the plants with both a daily file and a monthly grid).
- Run the app via the workflow (`PORT=21800 python3 app.py`); for ad-hoc curl use `localhost:80/...` through the shared proxy.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
