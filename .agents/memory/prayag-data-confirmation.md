---
name: Prayag Data Confirmation layer
description: Durable decisions behind the four-tier confirmation/audit gating — scoping, severity, and the hard Claude boundary.
---

# Prayag Data Confirmation & Audit Layer

The dashboard gates every published figure behind a deterministic four-tier check
(completeness, reconciliation, validity, plausibility).

## Durable decisions

- **Master roster = the full-FY monthly grid.** A machine/segment/mould blank in a later period is a completeness *gap*, never an implicit zero.
  **Why:** the brief forbids silently showing a blank month as a real low.
- **Confirmation must run on the UNFILTERED period rows**, not the plant/machine-filtered set.
  **Why:** otherwise a UI filter makes the dataset look incomplete and trips false flags.
- **Machine-presence scope is grain-aware.** Daily grain only holds daily-capable plants accountable; monthly grain expects every plant.
  **Why:** sub-monthly views deliberately cover only the daily-capable plants, so demanding every plant's machines would always read as massively incomplete.
- **Months score denominator = months that have actually ENDED (due), not the full FY.** The current in-progress month and future months are excluded from the denominator; a blank there is expected, never counted against completeness.
  **Why:** flagging the in-progress month as "overdue/missing" was a false positive; only ended months are genuinely due.
  **How to apply:** `_month_due(ym, as_of)` = last calendar day of the month < today. In-progress month (== as_of YYYY-MM) → `INFO` "still in progress"; ended-and-empty → `WARNING`; future → no issue.
- **Severity model (validity, revised):** the physical ceiling for monthly run-hours is the CALENDAR MONTH (`days×24`), NOT the planned ideal. `actual_hours > calendar_hours` (and negatives, reject>output, daily downtime>planned-production-time, NaN) = **hard error → QUARANTINE**. `ideal < actual ≤ calendar` (utilisation/efficiency over 100% within calendar) = **warning only**. Aggregate ratio-over-100% = warning (was error).
  **Why:** running above the planned baseline is possible (overtime/under-set ideal), so it must NOT be a hard error or it blocks legitimate sign-off; only the calendar-impossible case is a true error.
- **Quarantine = exclude, don't block.** A hard-error row is held aside (`quarantined=True`), EXCLUDED from every published metric, while the rest of the period publishes normally. Status is `error` only when there are *un-quarantined* (blocking) errors; quarantined rows + warnings → `warning`. `INFO` never affects status/gating. Raw values are surfaced with provenance; nothing is ever auto-corrected.
  **Why:** one impossible cell shouldn't gate an otherwise-clean month — hold the bad row aside and let the good data through.
  **How to apply:** `tier3_row_classify(period_rows)` returns `(clean, quarantined, issues)` and is the single source of the split — called in both `get_data` (publishes `clean_all`) and `full_confirm` (self-reconcile + aggregates run on clean). Fix happens in the sheet; next pull clears it.
- **Tier-4 outlier tempering:** a plant-median outlier is only flagged if ALSO an outlier vs the machine's OWN prior-month baseline (monthly grain only). 
  **Why:** structurally large/small machines (e.g. GARDEN M/C-3) tripped false outliers against peers despite being consistent with their own history.

## Claude boundary (hard rule)

The ONLY AI in this layer fuzzy-matches leftover machine codes (after deterministic exact + trailing-number matching fails) and writes prose from the already-computed status/score/issue list. **Neither call ever receives raw sheet cells or computes a figure.** Both no-op without the API key, and the matcher only runs when deterministic matching leaves unmatched codes.

## Validity checks must use UNCLAMPED metrics

`compute_metrics` clamps `performance = min(raw, 1.0)` for display, so a ratio
check on `computed.performance > 1.0` can NEVER fire. The engine exposes an
unclamped `performance_raw` specifically for the aggregate ratio-over-100% check
(now a `warning` in `tier3_aggregate`, not an error).
**Why:** any display-facing metric that is bounded/clamped is useless for "is this
value impossible?" detection — gate on the raw, unbounded value, not the rendered one.
**How to apply:** when adding a new validity (Tier-3) check on any ratio, confirm the
field you read is not clamped/floored in `metrics.py`; if it is, add a `*_raw` sibling.
The reject>output check also fires when output is 0 (`reject_count > total_count and
reject_count > 0`), since rejects with zero output are impossible, not just a gap.

## Hierarchy reconciliation (Tier-2)

Beyond sheet-TOTAL-vs-detail and engine self-reconcile, Tier-2 also checks segment ==
Σ its lines: a machine split across >1 segment, a segment total ≠ Σ of its machines,
and output recorded with no segment assigned (rolls to plant but not to any segment).

## Reconciliation note

PIPE's ~6.6% daily-vs-monthly offset is by design (daily Output is net-of-rejection) and surfaces as an honest warning, never hidden.
