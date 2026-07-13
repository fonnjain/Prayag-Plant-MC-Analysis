# Prayag Production Analytics

Mobile-first Flask dashboard that reads Prayag's real production Google Sheets (by explicit file ID) and computes OEE / utilisation / output-efficiency / rejection deterministically. Every ratio is recomputed in Python from raw cells — stored % cells are never trusted, and Claude only writes narrative prose from already-computed numbers. **Never fabricate data and never show a fake 0%**: a figure that cannot be computed is shown blank / "needs review", never zero.

## Run & operate

- App (Flask): the **`Prayag App`** workflow runs `cd artifacts/prayag && PORT=21800 python3 app.py`. Use `restart_workflow "Prayag App"` after code changes — do NOT use `artifacts/prayag-web: web` (it always fails its external health check due to a Replit proxy warmup race; see `.agents/memory/prayag-workflow-proxy-warmup.md`).
- **Reloader is OFF** — after editing any `app.py`/module, restart the workflow before testing or you debug stale code.
- Ad-hoc curl goes through the shared proxy: `localhost:80/...` (never the raw port).
- `DATABASE_URL` (Postgres) backs the durable manager-review store, ack/sign-off trail, spreadsheet-change fingerprints, and the L2 sheet cache. Everything degrades to a safe no-op without it.
- Tests: `cd artifacts/prayag && python3 -m pytest`.
- Spec-acceptance is a live route: `GET /build-state` (assertions vs the real sheets) — expect all PASS before sign-off.

## Stack

- Python 3 + Flask, server-rendered Jinja templates (`templates/*.html`), mobile-first.
- Google Sheets/Drive read via the configured integration; per-workbook caching (L1 in-process → L2 Postgres → L3 Sheets).
- PostgreSQL (psycopg2, append-only stores).
- Anthropic Claude — narrative prose + fuzzy machine-code matching ONLY; never reads or computes a figure.
- The surrounding pnpm monorepo also scaffolds a Node/Express api-server and a mockup sandbox; the Prayag product itself is the Flask app above.

## Where things live (`artifacts/prayag/`)

- `sources.py` — real Google Sheet file IDs (`ANNUAL_SOURCES`, `DAILY_SOURCES`), plant names, FY month maps, `EMPTY_SOURCES` (wired-but-empty templates that short-circuit to "awaiting source"). Source of truth for what gets read.
- `sheets.py` — Drive/Sheets readers + caching; monthly grid (`get_records`) and daily (`get_daily_records`, `_load_daily`); per-plant daily layout config (`_DAILY_LAYOUTS`).
- `parsers.py` / `pipe_reconcile.py` — deterministic header-based layout parsers (one reader handles both FY layouts); PIPE Report-5↔Report-11 reconciliation.
- `metrics.py` — `Record` (grain-agnostic), `compute_metrics` (recomputes every ratio), `rollup_by_*`.
- `ideal_hours.py` — ideal-denominator precedence + app-default planned hours.
- `confirm.py` — four-tier Data Confirmation engine (pure, no network).
- `verify.py` — read-only Data Verification + provenance (network-free).
- `recon.py` — pure standardized report-reconciliation badge engine: daily-first vs the monthly summary grid. The grid undercounts for every plant, so a positive delta is expected (info, never a fail); the only flagged concern is daily-first falling SHORT of the grid. Degrades to honest "recomputed only" where no grid is wired. Rendered by `templates/_recon_badge.html` on report-detail pages.
- `freshness.py` — spreadsheet-change tracking (content-hash fingerprints; Google's true modifiedTime is unreachable).
- `store.py` — durable Postgres stores: sign-offs, issue acks, source fingerprints, ideal-hours overrides, L2 sheet cache. All no-op without `DATABASE_URL`.
- `narrative.py` — Claude prose + code matching.
- `app.py` — Flask routes, period parsing, the read→filter→compute→validate pipeline.
- `templates/*.html` — grain-aware UI.

## Core invariants

- **Daily-first figures.** Monthly AND FY headline totals are summed from the authoritative daily files, not the monthly summary grid (the grid undercounts). The grid is a non-blocking reconciliation reference only, and is the headline ONLY for a month with no daily workbook at all. A total daily-read outage shows an honest error banner — the lower grid total is never substituted in. Per-(plant, month) read failures are isolated so one transient 429 doesn't nuke a whole FY read.
- **Ideal-hours precedence** (highest first): manager OVERRIDE (app DB, `/input`) → in-sheet ideal-output rate (HDPE) → in-sheet ideal-hours column (PTMT) → PIPE/MOULDING derived (Report-5 ideal/day × run days) → `baselines.json` → app-logic default (`APP_DEFAULT_IDEAL_HOURS`: GARDEN/TANK 500, HDPE 550) → none. The monthly grid's flat 500 "Ideal Hours" is a placeholder, NOT a precedence step.
- **No fake 0% utilisation.** Output-only / no-run-hour rows still get a planned-hours denominator for the honest total, but utilisation stays SUPPRESSED (blank) — enforced by `PLANTS_WITHOUT_RUNHOURS` → `runhours_tracked=False` and a separate `util_ideal` denominator in `compute_metrics`. TANK is always output-only; GARDEN suppresses on days/months with no logged run hours. Per-day ideal hours spread only across days that logged run hours, so a no-run-hour day stays blank while a full-month rollup still reconciles. Overrides follow the same gating, and a plant with no machine identity (TANK) takes a plant-level override (`machine=""`). UI distinguishes "No baseline set" (genuinely none) from "Run hours not recorded" (baseline exists, hours missing).
- **Per-plant units.** Output unit is per-plant (MOULDING = kg, TANK = Ltr). Never sum or compare across units — use the per-unit buckets.
- **Data Confirmation (gating).** Four deterministic tiers run on the UNFILTERED period rows (a filter never makes data look incomplete); the full-FY grid is the master roster, a later blank is a gap not a zero. Severity: validity / internal-reconcile-mismatch / no-data = error (figure gated "needs review"); completeness / sheet-reconcile-off / plausibility = warning. Manager sign-off releases the ERROR gate only and binds to the data fingerprint, so any sheet change re-gates automatically. No login — the typed approver name is an attestation, not authenticated identity.
- **Last Updated (default period).** Resolves PER PLANT to each plant's own most recent day with real production data, skipping empty in-progress days — so every active plant lands on real figures with its own date; a dormant plant is simply not shown (never fabricated).
- **Spreadsheet-change tracking.** "Last updated / what changed" is a content fingerprint (Google's modifiedTime is unreachable). It MUST be cross-process deterministic — sort canonical lines and normalise int/float — or every page load false-flags a change. Changing the fingerprint formula invalidates baselines: `TRUNCATE source_fingerprints` to re-baseline.

## Plant-specific notes

- **PIPE** — output & rejection reconciled as the per-(machine, date) MAX of Report-5 (run hours + many machine-days) and Report-11 (item journal; logs days R5 omits). Type split (CPVC/UPVC/SWR/AGRI) is audit-only and never reduces the headline. Report-5's own TOTAL row sums a stale machine range — the sum of real M/C-n rows is authoritative.
- **MOULDING** — output in kg (Report-12 "Wt in Kgs"). Efficiency n/a (no in-sheet ideal-output rate).
- **PTMT** — no monthly grid; roster is the 55-machine register (`sources.PTMT_GROUPS`). Machines carry process-group segments (`PTMT – Injection …`), never a flat `PTMT` — report filters must match by prefix. Utilisation from Report-5 Col E (flat 572 h/machine/month, read live). Whole-month rejection lumps onto the last day, so the reject>output check runs at machine-month aggregate grain, never per daily row.
- **HDPE** — no grid cross-check; publishes its own ideal output (kg/hr) + run hours, so utilisation/efficiency compute from the daily matrix.
- **GARDEN** — output from per-machine block tabs, run hours joined from the "Daily Report" matrix; utilisation against the app-default 500.
- **TANK** — logged per item, no machine identity → plant-level output + item detail, no per-machine OEE; output-only (utilisation suppressed). Headline unit chosen by data presence (Ltr→pcs→kg).
- **Auxiliaries** — Report-5-only grinders/pulverizers/sockets/mixers have no daily tab → synthesised as month-grain finishing records, excluded from the plant headline and from day-window / last-updated computations.

## User preferences

- Brand colours: navy `#1F3864`, terracotta `#C55A11`. Dates displayed dd-mm-yyyy.
- Read real production sheets by explicit file ID; never fabricate or silently fall back to demo numbers.

## Pointers

- Deeper implementation detail and hard-won gotchas live in `.agents/memory/` (agent working memory).
- See the `pnpm-workspace` skill for the surrounding monorepo structure.
