---
name: Prayag Workbook Index layer
description: How the per-workbook "Index" tab drives tab metadata, by-description resolution, and month-over-month change flags.
---

# Workbook Index as authoritative tab metadata

Each PTMT / Pipe&Fitting daily workbook ships an "Index" sheet documenting every
Report-N tab (description, frequency, owner, include/feeds). The dashboard treats
it as the source of truth for what each tab MEANS.

- **Key tabs by (plant + description), never bare number.** The same "Report-N"
  number means different things across workbooks (e.g. PIPE Report-12 = Moulding
  production, PTMT Report-12 = Wastage Mgmt). `resolve_report_tab` matches by
  description keywords and falls back to the configured hardcoded tab, so figures
  never depend on the Index being present/correct.

- **Frequency governs slicing.** `resolve_report_tab(require_sliceable=True)` is
  the default: daily ingestion may ONLY resolve to a Daily (sliceable) report, so
  a weekly/monthly snapshot tab that shares description keywords can never be
  picked for per-day figures. This is the enforcement point for "only Daily
  sliceable" — do not remove the gate.

- **Change-flag baseline is keyed by (plant, report_key), NOT file_id.**
  **Why:** every month is a NEW workbook file, so a file_id key would make every
  month look like first-sight and never detect a change. The plant-level key lets
  the engine compare this month's Index against the previously-seen desc/frequency.
  First sight is baselined silently (not flagged); on conflict the baseline's
  desc/frequency are LEFT INTACT (only observed_at/file_id refresh) so a genuine
  desc/frequency change keeps flagging. Tradeoff: a legitimate permanent change
  flags forever (no ack/rebaseline UI was built — acceptable as a strict alert,
  and ingestion is unaffected because resolution is by description, not baseline).

- **Resolution falls back to configured tab.** For all CURRENT tabs resolved ==
  configured, so wiring it into `_emit_daily` changed no figures. PTMT production
  (Report-6 "...KG & Pcs") has no bare tab (split into Report-6 (A/B/C)), so its
  resolver correctly falls back; PTMT OEE stays on the Report-5 matrix.

- **`/sources` surfaces the full catalogue** with status wired / available (tab
  exists, not ingested) / documented (in Index, no matching tab in this workbook).

**Gotcha:** `app.py` imports sheet functions by NAME (`from sheets import ...`),
it does NOT bind a `sheets` module. A broad `except Exception` once masked a
`NameError` from calling `sheets.index_catalogue`; the builder now catches
`SheetReadError` narrowly and logs unexpected errors via `logging`.
