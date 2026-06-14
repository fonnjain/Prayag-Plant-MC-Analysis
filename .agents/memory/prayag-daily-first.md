---
name: Prayag daily-first headline figures
description: Why monthly/FY totals are summed from daily files (not the summary grid), and the rules that keep that honest.
---

## Decision: daily files are the source of truth for EVERY period

Monthly and FY headline totals are summed from the authoritative daily tabs (one tab per metric — PIPE Report-11 "Weight", MOULDING Report-12 "Wt in Kgs"). The monthly summary grid is only a reconciliation reference: surfaced as a NON-BLOCKING note, never the headline.

**Why:** The summary grid undercounts (PIPE May grid ≈81,654 vs daily 107,609). The user directive is explicit: never reconcile daily totals DOWN to the grid.

**How to apply (the invariants that keep it honest):**
- Grid is the headline ONLY for a month that has no daily workbook at all (append for those `grid_only_months`, disjoint from daily months → no double count).
- A TOTAL daily outage (every workbook read failed) shows NO production data with an honest error banner — the grid is NEVER substituted, on monthly/FY OR sub-monthly. `get_data` refuses any grid substitution under the daily-only rule. (The lower grid total must never silently replace a daily outage; the regression test is `test_total_daily_outage_shows_nothing_not_grid`.)
- `daily_used=True` now drives monthly/FY confirmation scope too. That path is built for daily grain (rosterless plants like TANK scored honestly; a roster machine that never ran = WARNING, not a blocker).
- `get_daily_records` MUST isolate per-(plant,ym) read failures and only raise when ALL fail. Otherwise a single transient 429 on a cold multi-month (FY) read nukes the whole period — which now shows NO data for it (an honest but avoidable outage), never a grid substitution.

## Known caveat: month-completeness is still grid-based

`months_with_data()` / the `fy_months_with_data` passed to confirmation reflects only months present in the monthly GRID. If a future ended month has daily data but the grid lags, Tier-1 can falsely warn "month ended but holds no data".

**Why it's currently harmless:** the only ended months (Apr/May 2026) also have grid data; the in-progress month is treated as INFO.

**How to fix properly (when it bites):** make the signal data-driven from loaded daily rows, NOT from configured daily months — a configured daily file can be empty, so "configured" ≠ "populated" (using configured months would instead hide a real empty-month gap; the file-level "no data read" warning only partly covers that).
