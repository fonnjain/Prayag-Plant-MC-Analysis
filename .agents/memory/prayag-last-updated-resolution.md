---
name: Prayag "Last Updated" default-period resolution
description: How the default last_updated snapshot picks each plant's freshest day, and why it must skip empty in-progress days.
---

The default period `last_updated` resolves in `app.get_data()` to a PER-PLANT
freshest snapshot: each plant contributes the rows from ITS OWN most-recent day
with real production (reusing the `fresh_by_plant` map). It is NOT a single global
date.

**Why per-plant:** a single global max-date dropped any plant that reported on an
earlier day (e.g. Moulding, HDPE) the moment a different plant reported more
recently — the user wants every plant visible with its own last-updated date. The
banner lists each plant + its date.

**Window is not enough — also read each plant's newest available months.**
`parse_period` searches a fixed recent window (60-day), but a fixed window
silently HIDES any plant whose freshest day ages past it (an infrequently
reporting plant — TANK, GARDEN, MOULDING — vanishes from "Last updated" even
though it has data). So for the `last_updated` period only, `get_data` UNIONs into
`daily_file_months` each daily-capable plant's two newest available months from
`DAILY_SOURCES` (newest-2, not newest-1, so a brand-new empty in-progress month
falls back to the prior real month). Every plant with any daily data then resolves
to its own freshest real day, however old — the displayed date communicates
staleness. A plant with NO daily production at all simply doesn't appear — never
fabricated.

**Symptom vs. fix:** when the deployed app shows fewer plants than dev, suspect the
PROD L2 sheet cache is stale (background refresher only runs continuously on a
Reserved VM) — a republish re-reads live sheets. The window-widening above is the
durable code fix; refreshing/republishing clears the stale snapshot.

**Rule:** "real production" = a daily Record with `total_count > 0` OR
`actual_hours > 0`. Rejection is deliberately EXCLUDED from this test.

**Why the predicate:**
- Sheets often contain a freshly-created daily tab dated *ahead* of actual data
  entry — a placeholder for "tomorrow", or a date that looks like the future
  because the production sheets are kept in IST while the server clock is UTC.
  Its rows exist but are all-zero. Picking `max(date)` over *any* daily row lands
  the headline on that empty day → "No data recorded" / Total Output 0, even
  though real data exists a few days earlier.
- Rejection cannot be part of the signal: a wide-matrix parser (PTMT and similar)
  books the WHOLE month's rejection onto the last calendar day's row, so the last
  day of every month carries `reject_count > 0` with zero output/hours. Counting
  rejection would re-introduce the empty-day bug on the last of every month.

**How to apply:** `_has_production(r)` gates BOTH the per-plant freshness map and
the `last_updated` selection, so both agree. The selection also requires
`r.grain == "daily"` so month-grain Report-5 aux rows (grinders etc.) never leak
in. Only the `last_updated` branch uses this; sub-monthly windows, monthly and FY
views are untouched (they keep the from/to date-range filter + monthly-row drop).
