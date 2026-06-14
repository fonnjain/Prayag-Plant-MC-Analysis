# Rule: daily files are the only source for current figures

Make the daily "Date Sheet & Monthly Report" Google Sheets the **only** source the app reads for any current figure. **Do not open the monthly summary workbooks** on any normal read, schedule, dashboard view, report, or sign-off. This is a contained change — no database migration required.

## 1. The rule
- Every current figure (daily / weekly / **monthly / FY**) is built by reading the **daily files** and aggregating the daily facts. Monthly = sum of that month's daily rows; FY = sum of the FY's daily rows.
- The app **never reads the monthly summary workbooks** as a source. Remove them from the discovery/read path for current periods.
- If a daily file for a needed month genuinely does not exist, show **"no daily data for this period"** (informational). Do **not** substitute a monthly summary into the figure.

## 2. Reconciliation now comes from the daily file itself
Keep one live reconciliation check, and make it the daily file's **internal** one:
- detail rows summed == that daily file's own TOTAL row (per the one-authoritative-tab rule: Pipe = Report-11 Weight; Moulding = Report-12 Wt in Kgs; etc.).
This is like-for-like and already passes at 100%. **Drop the daily-vs-monthly-summary comparison from the live checks and from sign-off** — it compares different definitions (incomplete summaries) and only produces false fails.

## 3. Summaries are not deleted — they become a manual, off-by-default tool
- Keep the summary-reading code, but it runs **only** when a user explicitly triggers it, for two purposes:
  - **One-time historical backfill** of months that predate the daily files, and **prior-year (YoY) history** — loaded once, clearly tagged `source = summary/backfill` or `legacy`, then not re-read.
  - An **optional manual spot-check** (clearly labelled "compares against the monthly summary, which may be incomplete / a different basis").
- Neither is part of the scheduled refresh, the dashboard's current numbers, or the sign-off gate.

## 4. Wording / UI
- Remove any "monthly summary" fallback notices and any "daily vs summary" panel from the normal sign-off flow.
- Monthly/FY views simply show the daily-aggregated figure with `source = daily`.

## 5. Acceptance criteria
- No monthly summary workbook is opened during a normal refresh, dashboard load, report, or sign-off (verify in logs / read list).
- Monthly and FY totals equal the sum of that period's daily facts (e.g. Pipe May = 107,609 from daily, not 81,654 from the summary).
- The only live reconciliation is the daily file's detail-vs-its-own-TOTAL check.
- Summary reads happen only on an explicit manual trigger (backfill / YoY / spot-check) and are tagged accordingly.
- Sign-off no longer blocks on any daily-vs-summary discrepancy.
