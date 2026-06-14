# Build-state checklist — run BEFORE any sanity check / sign-off

Purpose: confirm the fixes are actually live in this build, so a review never runs against stale code or a stale data load. Run these as automated assertions (a `/health` route or a startup self-test) and print a PASS/FAIL table. **If any assertion FAILs, stop — the corresponding fix is not live; do not run the Claude sanity check or attempt sign-off until it passes.** These check the app's own computation against known-verified values; they do not edit anything.

## Assertions

| # | Assertion | Expected | If FAIL → the un-applied fix |
|---|---|---|---|
| 1 | Pipe May 2026 output, computed from daily Report-11 "Weight", detail rows only | **107,609 kg** (±0.5%) | one-authoritative-tab fix not live (double-count) |
| 2 | Pipe May: detail-row sum == Report-11 TOTAL row | equal (0 diff) | reconciliation/parse off |
| 3 | Moulding May 2026 output, from the Pipe file Report-12 "Wt in Kgs", detail rows only | **75,771 kg** (±0.5%) | Moulding not read from Report-12, or double-count |
| 4 | Pipe daily does NOT also include Report-13 / Summary / Summary-of-Report-5 in the output sum | only one tab counted | overlapping-tab double-count still present |
| 5 | "Last 7 days" / "Yesterday" reads daily files (no "daily breakdown not available" banner) | daily path active | daily-first read not live |
| 6 | HDPE June daily rows parsed | rows > 0 | Garden/HDPE/Tank parser not finished |
| 7 | Garden June + Tank May daily rows parsed | rows > 0 (Tank = plant-level) | same parser gap; a file with 0 rows must flag "parse failed", not "no data" |
| 8 | PTMT roster machine count | **55** (31 std + 17 N-line + 3 blow + 1 corrugator + 3 grinder) | PTMT roster not wired |
| 9 | PTMT utilisation uses in-sheet IDEAL HOUR (PTMT not flagged "no baseline") | true | PTMT wrongly on baseline list |
| 10 | PTMT outliers compared within process group / own history (not blended median) | grouped compare active | grouping not wired |
| 11 | Tank scored at plant level (no per-machine roster, no A/P/Q for Tank) | true | Tank still scored vs a machine roster |
| 12 | Planned-hours baselines for PIPE/MOULDING/GARDEN/HDPE read from `baselines.json`; missing → "no baseline set" (not computed vs 500) | config-driven | baseline config not wired |
| 13 | Validity: a row with actual_hours > calendar_hours_in_period is quarantined; utilisation>100% within calendar is a WARNING not an ERROR | calendar-max rule live | old "actual>ideal=impossible" rule still active |
| 14 | An impossible row is quarantined row-by-row; the rest of the period still publishes/signs off | true | period-level blocking still active |
| 15 | Current in-progress month labelled "in progress", not "overdue" | true | completeness wording not updated |
| 16 | No source value is ever written/modified by the app (read-only to Sheets) | true | safety violation — must fix immediately |

## Output format
Print:
```
BUILD STATE: 16/16 PASS  → safe to run sanity check
  or
BUILD STATE: 13/16 PASS — BLOCKED
  FAIL #1 Pipe May = 141,832 (expected 107,609) → one-tab fix not live
  FAIL #6 HDPE June rows = 0 → parser not finished
  FAIL #8 PTMT roster = 31 (expected 55) → roster not wired
```
Only when **all 16 PASS** should the app run the Claude sanity check or offer sign-off. Re-run this checklist after every redeploy.

## Note
These assertions encode verified ground truth from the source files (Pipe May = 107,609; Moulding May = 75,771; PTMT = 55 machines). They are guardrails against stale builds and stale reviews — not a substitute for the verification view, which remains the ongoing read-only reconciliation.
