---
name: Plumbing working days
description: How configurable Plumbing workday counts affect scheduling and corrective re-planning.
---

Four saved weekly values represent a **count** of working days, not a selection of specific calendar dates. For corrective re-planning, distribute that count proportionally through the month with deterministic floor rounding: elapsed days are `floor(total × completed_calendar_days / calendar_days_in_month)` and remaining is `total − elapsed`.

**Why:** A four-bucket setting cannot identify individual Sundays or other dates. Proportional counts keep mid-month feasible-capacity projections unbiased, while explicit date selection would create a disproportionate monthly maintenance burden.

**How to apply:** Preserve the legacy Mon–Sat corrective calendar until a planner explicitly saves a Plumbing working-day split. A full-calendar shortcut is count-based; if exact dates become necessary, treat a date picker and its storage as a distinct future feature.

For an explicitly saved split, the declared machine capacity is a legacy 25-day baseline. Scale it by the selected day count, then quantize it to whole two-shift machine-days. The pipe and fitting schedulers must use the same selected machine-days for allocation, weekly capacity, totals, and utilisation.

**Why:** A fractional scaled hour budget cannot be scheduled by a whole-shift planner, and prorating it independently can show weekly utilisation over 100%.

**How to apply:** Only explicitly configured Plumbing schedules use this capacity plan. Off-capacity and downtime blocks are never idle capacity; weekly idle must be summed from capacity-eligible blank-idle blocks. Extension advice may count only the non-downtime extra days of machines capable of the unfinished items, capped by the same effective-capacity gain.