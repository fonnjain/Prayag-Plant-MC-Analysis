---
name: PTMT rejection lumping & reject>output validity grain
description: Why the reject-exceeds-output impossibility check must run per machine-month, not per daily row, and the parser quirk that forces it.
---

# Rejection is recorded monthly, not per day (PTMT-style matrices)

PTMT Report-5 (a wide per-date "matrix" daily tab) has NO per-date rejection
column. Each date-group is just (Run Hours, Output). A single monthly column —
"Actual Rejection Weight (in Kgs)" — sits AFTER the 31 daily date-groups.

The matrix parser bounds the LAST date-group to the end of the row, so its
rejection sub-column resolves to that trailing monthly-total column. Net effect:
the whole month's rejection is booked against the LAST day's row; every other
day reads reject = 0.

**Consequence:** summing the daily rows recovers the correct monthly rejection
(so plant/machine monthly rejection figures are right), BUT an isolated daily
row on the last day shows a huge reject against only that day's small output.

# The rule: reject>output is a machine-month aggregate check

The Tier 3 "rejects exceed output" physical-impossibility check must run on the
**aggregate** (sum output vs sum reject per plant·machine·month, keyed by grain
too), NOT per daily row.

**Why:** per-row, the lumped last-day reject false-flags as impossible and
quarantines valid output (this dropped ~28 May-2026 rows across PTMT + PIPE,
understated figures, and blocked sign-off). At the month grain, output dwarfs
reject and there is no impossibility.

**How to apply:** keep per-row checks for things that ARE per-row (negatives,
NaN, downtime>production-time, hours>calendar). For reject>output, aggregate
first; a genuine month-level violation still quarantines every row of that
machine-month. Monthly-grain rows are a group of one, so their behavior is
unchanged. Do NOT "fix" this by changing the parser to drop the trailing
columns — that would lose the only rejection figure the sheet provides.

A cleaner long-term fix would separate daily rejection from the monthly-summary
rejection column so single-day views aren't distorted, but that is a parser /
data-model change, not a validity-tier change.
