---
name: MP Follow-Up engine
description: How plan lines are frozen, actuals ingested, and variance computed for the follow-up page.
---

## Tables
- `mp_plan_line` — one row per scheduler block at freeze time; join key = (plan_run_id, machine_norm, item_norm)
- `mp_actual_line` — upserted on ingest from R-11/R-12; UNIQUE on (segment, month, date, machine_norm, item_norm, source_tab)

## Freeze flow
At `/machine-planning/<run_id>/freeze` (POST), after `update_plan_run_freeze()`:
1. Re-run `run_shift_schedule()` with the session payload's demand
2. `build_plan_lines_from_schedule(sched, engine_result, run_id, segment, month)`
   — net_hours = planned_hours − excess_hours; kg = net_hrs × rate_kg_per_hr
3. `insert_plan_lines(rows)` — DELETE existing for run_id first (idempotent)
Non-fatal: any exception logs a WARNING and the redirect still happens.

## Actuals ingest
`ingest_actuals(month, segment)` → calls `load_pipe_actuals()` which `batch_get`s ["Report-11", "Report-12"] from the PIPE daily workbook. Header-based column detection, with spec-position fallback. Returns summary dict with counts. Routes: `POST /machine-planning/followup/<run_id>/refresh-actuals`.

## Report-11 parser key rules
- Scan for header row containing 'ITEM CODE' keyword (default row index 4)
- Columns detected by header text; fallbacks: DATE=B=1, MACHINE=D=3, ITEM=F=5, RUN HRS=G=6, PCS=I=8, WEIGHT=J=9, REJECTION=N=13
- Date and machine carry forward when blank
- Skip rows with pcs==0 AND kg==0; skip numeric-only item codes (serial numbers)

## Normalisation (join key)
- `norm_machine("PIPE M/C - 1")` → `"MC1"` (strip PIPE/MOULDING prefix, remove spaces/hyphens/slashes, uppercase)
- `norm_item()` delegates to `mp_seed.norm_code()`
- Critical: plan stores "M/C-1", actuals have "PIPE M/C - 1" → both normalise to "MC1"

## Plan-to-date cutoff
`elapsed_plan_days = round(calendar_day_of_max_actual_date / days_in_month * working_days)`
Filter plan_lines where `day <= elapsed_plan_days` for plan-to-date comparison.
`working_days` = max plan_line.day across all lines (not a config constant).

## RAG classification
`rag_status(actual, plan, amber_pct, red_pct)`:
- deviation = |actual - plan| / plan × 100
- deviation < amber_pct → GREEN (strictly less than; exactly at threshold = AMBER)
- deviation < red_pct   → AMBER
- else                  → RED
- plan==0, actual==0 → GREEN; plan==0, actual>0 → RED

## Warning types (9 total), severity-sorted
1. WRONG_MACHINE (sev 1) — item produced on machine not in its plan routing
2. NIGHT_CHANGEOVER (sev 1) — >1 item on same (mc, date) that had a NIGHT plan block
3. SHORT_BLOCK (sev 2) — actual hrs/day < min_run_block_hours
4. UNPLANNED (sev 2) — item in actuals but not in plan at all
5. NOT_STARTED (sev 2) — planned item with zero actual by elapsed_plan_days
6. QTY_SHORTFALL / QTY_OVERRUN (sev 3) — AMBER/RED adherence with actual > 0
7. HOURS_DEVIATION (sev 3) — machine hours deviation > hours_dev_pct
8. IDLE_VS_PLAN (sev 4) — machine had planned work but zero actual

## RAG thresholds stored in mp_params
`rag_amber_pct` (default 10), `rag_red_pct` (default 25), `hours_dev_pct` (default 15).
Saved via Data Setup page → Parameters section → Follow-Up RAG Thresholds subsection.

## Routes
- `GET  /machine-planning/followup`                         → redirect to latest run
- `GET  /machine-planning/followup/<run_id>`                → variance page
- `POST /machine-planning/followup/<run_id>/refresh-actuals`→ re-ingest + redirect
- `GET  /machine-planning/followup/<run_id>/download`       → XLSX (4 sheets)

## Why
Plan adherence needs a live join of plan vs actuals that survives multiple ingest cycles.
Storing plan lines at freeze time means the comparison is stable even if the engine inputs change later.
