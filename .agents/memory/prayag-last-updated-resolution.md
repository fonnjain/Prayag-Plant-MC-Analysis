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
banner lists each plant + its date; `parse_period` searches a 60-day window (was
30) so a plant ~2 months stale (HDPE) is still loaded. A plant with NO daily
production in the window simply doesn't appear (Tank/CP are dormant all FY) — never
fabricated.

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
