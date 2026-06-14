# Make daily the only recurring source; summaries/legacy = one-time backfill into the DB

Change the data model so the **daily "Date Sheet & Monthly Report" Google Sheets are the only source read on the regular refresh cycle.** The monthly summary and prior-year workbooks are read **once** to populate the database with history, then **not read again** on the schedule. This removes the monthly-fallback behaviour that was producing empty "0/35" periods and the duplicate-tab double-count.

## 1. Recurring cycle — DAILY only
On every scheduled refresh (and the manual "Refresh"):
- Read **only** the current-FY daily files (per plant, list each daily folder, take files overlapping the window). One authoritative tab per metric (e.g. Pipe = Report-11 Weight; Moulding = Report-12 Wt in Kgs) — do not sum overlapping tabs.
- Upsert into the DB on the natural key (no duplicates). This is the live data for all current views (daily / weekly / monthly / FY).
- Monthly and FY figures are produced by **aggregating the daily facts in the DB**, not by reading a monthly summary sheet.
- Do **not** read the monthly summary or prior-year workbooks on this cycle.

## 2. One-time backfill (run once, then frozen)
A separate, manually-triggered backfill job (not on the schedule):
- Reads the **monthly summary** workbooks once to populate months that predate the daily files or have no daily file, writing to the same fact tables with `source_tier = 'backfill_summary'` and `is_backfill = true`.
- Reads **prior-year** (legacy) summaries/dailies once with `is_legacy = true` for YoY/trend history.
- Records each file in `load_ledger` (file id + modified-time + hash). Once logged, the regular cycle never re-reads it. Backfilled/legacy rows are **immutable** (no update/delete path); re-running the backfill is idempotent (upsert) and changes nothing unless a file is explicitly re-pointed.

## 3. Precedence in the DB (daily always wins)
For any month where both a daily-derived value and a backfilled-summary value exist, the **daily value is authoritative**; the summary value is retained only as the reconciliation reference (below), never shown as the figure. Record `source_tier` on every row (`daily` | `backfill_summary` | `legacy`) so the dashboard can show provenance.

## 4. Keep the summary as a CHECK, not a source
Do not delete the summary-reading code — repurpose it:
- It is **not** part of the recurring read and is **not** a fallback data source.
- Keep it available as an **on-demand / periodic reconciliation check**: sum the daily facts for a month and compare to that month's summary figure; surface PASS/FAIL with both numbers (this is what catches double-counts and source edits). Run it on demand or monthly, not every cycle.

## 5. When a daily file is genuinely missing
If a plant has no daily file for a month in the current FY (e.g. CP on a different cycle):
- Show that plant/month as **"no daily data for this period"** (informational), not as machines missing.
- Do **not** silently substitute the monthly summary into the live figure. If you want a stop-gap number, label it explicitly "from monthly summary (daily unavailable)" and tag `source_tier='backfill_summary'` — never blend it into the daily total unlabelled.

## 6. Acceptance criteria
- The scheduled refresh reads only daily files; monthly/FY views are aggregated from daily facts in the DB.
- Monthly summary and prior-year files are read only by the one-time backfill job, logged in `load_ledger`, and never re-read on the cycle.
- A month with daily data shows the daily-derived figure; `source_tier='daily'`.
- The reconciliation check (daily vs summary) still runs on demand and reports PASS/FAIL with both numbers.
- Re-running the backfill creates no duplicates and changes no existing rows.
