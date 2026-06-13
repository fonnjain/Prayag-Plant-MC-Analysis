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
- **Months score denominator is the full FY (X/12)**; overdue period months with no data are flagged as individual completeness issues.
  **Why:** matches the spec's "1/12 months" score shape while still surfacing actionable per-month gaps.
- **Severity → gating (spec-mandated):** validity failures (negatives, reject>output, downtime>available-time, ANY ratio over 100%), internal engine self-reconcile mismatch, and no-data-at-all = `error` ("needs review"). Completeness gaps, sheet-reconcile-off, and plausibility (duplicates, outliers, sudden zeros, unit mismatch, very-high rejection) = `warning`.
  **Why:** a ratio over 100% is explicitly an *invalid value*, not a soft flag — it must gate the figure. A high rejection rate is possible (so warning), not impossible.

## Claude boundary (hard rule)

The ONLY AI in this layer fuzzy-matches leftover machine codes (after deterministic exact + trailing-number matching fails) and writes prose from the already-computed status/score/issue list. **Neither call ever receives raw sheet cells or computes a figure.** Both no-op without the API key, and the matcher only runs when deterministic matching leaves unmatched codes.

## Validity checks must use UNCLAMPED metrics

`compute_metrics` clamps `performance = min(raw, 1.0)` for display, so a validity
check on `computed.performance > 1.0` can NEVER fire. The engine exposes an
unclamped `performance_raw` specifically for the ratio-over-100% validity check.
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
