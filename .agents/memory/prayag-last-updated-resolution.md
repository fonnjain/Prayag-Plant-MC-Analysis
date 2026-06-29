---
name: Prayag "Last Updated" default-period resolution
description: How the default last_updated snapshot picks the freshest day, and why it must skip empty in-progress days.
---

The default period `last_updated` resolves in `app.get_data()` to the single most
recent day that has REAL production, then shows that day as a sub-monthly snapshot.

**Rule:** "real production" = a daily Record with `total_count > 0` OR
`actual_hours > 0`. Rejection is deliberately EXCLUDED from this test.

**Why:**
- Sheets often contain a freshly-created daily tab dated *ahead* of actual data
  entry — a placeholder for "tomorrow", or a date that looks like the future
  because the production sheets are kept in IST while the server clock is UTC.
  Its rows exist but are all-zero. Picking `max(date)` over *any* daily row lands
  the headline on that empty day → "No data recorded for this period yet" /
  Total Output 0, even though real data exists a few days earlier.
- Rejection cannot be part of the signal: a wide-matrix parser (PTMT and similar)
  books the WHOLE month's rejection onto the last calendar day's row, so the last
  day of every month carries `reject_count > 0` with zero output/hours. Counting
  rejection would re-introduce the empty-day bug on the last of every month.

**How to apply:** the `_has_production(r)` predicate gates BOTH the per-plant
freshness map ("Data reported through …") and the `last_updated` narrowing, so
both agree and neither points at a zero day. Only the `last_updated` branch uses
it; sub-monthly windows, monthly and FY views are untouched.
