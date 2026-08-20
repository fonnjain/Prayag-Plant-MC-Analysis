---
name: Plumbing working days
description: How configurable Plumbing workday counts affect scheduling and corrective re-planning.
---

Four saved weekly values represent a **count** of working days, not a selection of specific calendar dates. For corrective re-planning, distribute that count proportionally through the month with deterministic floor rounding: elapsed days are `floor(total × completed_calendar_days / calendar_days_in_month)` and remaining is `total − elapsed`.

**Why:** A four-bucket setting cannot identify individual Sundays or other dates. Proportional counts keep mid-month feasible-capacity projections unbiased, while explicit date selection would create a disproportionate monthly maintenance burden.

**How to apply:** Preserve the legacy Mon–Sat corrective calendar until a planner explicitly saves a Plumbing working-day split. A full-calendar shortcut is count-based; if exact dates become necessary, treat a date picker and its storage as a distinct future feature.