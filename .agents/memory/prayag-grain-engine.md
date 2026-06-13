---
name: Prayag daily/monthly grain engine
description: Invariant for how compute_metrics handles the two kinds of daily Records, and the daily-vs-monthly reconciliation model.
---

# Two kinds of daily Records (compute_metrics invariant)

`compute_metrics` (metrics.py) handles THREE shapes through two branches:
- `grain=="daily" and shift_len_min>0` → **true shift-log** rows (mixer/shift logs): hours are derived from the time model (`shift_len_min - planned_stops - downtime`) and these rows feed OEE (A×P×Q).
- everything else (monthly rows, AND `grain=="daily"` daily-matrix rows where `shift_len_min==0`) → use the pre-populated `actual_hours`/`ideal_hours`/`ideal_output` fields directly.

**Why:** the daily-matrix path (`parse_daily_matrix` + `_load_daily`) produces daily rows that carry hours/output directly like monthly rows and leave `shift_len_min==0`. An earlier version branched only on `grain=="daily"` and tried to derive hours from `shift_len_min`/downtime → those were 0 → utilisation/output-efficiency silently collapsed to 0 even though totals reconciled. The bug was invisible because reconciliation sums raw fields, not `compute_metrics` output.

**How to apply:** if you add a new daily ingestion path, decide deliberately: shift-log style (set `shift_len_min>0`) OR matrix style (set `actual_hours`/`ideal_hours`/`ideal_output`, leave `shift_len_min==0`). Never set `shift_len_min` on a matrix row unless you also intend the time-model branch. After any change, sanity-check that a full-month daily rollup's utilisation equals the monthly grid's for the same machines.

# Reconciliation model

Per-day ideal hours = monthly ideal hours ÷ the machine's active days that month. This makes a full-month daily rollup's total ideal hours collapse back to the monthly figure, so daily↔monthly utilisation reconciles exactly. Verified: April PIPE+GARDEN = 831 actual / 5000 ideal hrs (16.6%) on both paths. Low absolute utilisation is real — machines run a few hundred hours against a flat 500 hr/month/machine theoretical capacity.
