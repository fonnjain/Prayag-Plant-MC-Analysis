# Data verification routine (independent check — keeps the model out of the data path)

Goal: let anyone confirm the app's numbers match the source Google Sheets, without changing how data is read or computed. The app **fetches and computes deterministically** (unchanged). This routine only **exposes what it computed, with provenance**, so it can be reconciled against the source — by a person, or by Claude reading the same sheets independently. Verification never writes to the fact tables and never alters a value.

## 1. A "Verification" view / endpoint
Add a read-only screen (and a JSON endpoint, e.g. `GET /verify?period=2026-05`) that, for the selected period, returns the app's computed figures **with the source they came from**. For each plant → machine → month:
```
plant, machine, year_month,
output_kg, output_pcs, reject_kg, run_hours, breakdown_hours,
source_file_id, source_sheet, source_ref,   // provenance
ideal_hours_used, ideal_source              // config vs sheet
```
Plus period roll-ups: per-plant and grand totals for output, reject, run-hours.

## 2. Built-in reconciliation (already deterministic — surface the result)
For the same period, compute and display three checks, each as PASS / FAIL with the two numbers side by side:
- **Daily vs summary:** sum the daily facts to the month and compare to the monthly summary workbook's figure for each machine/plant. Show `daily_sum`, `summary_value`, `difference`, `% diff`.
- **Row vs total:** sum of machine rows == the sheet's own TOTAL row.
- **Plant vs machines:** plant total == Σ its machines.
Anything outside a small tolerance (e.g. 0.5%) is flagged FAIL with the exact location.

## 3. Spot-check export
A "Download verification CSV" button that exports the §1 table for the period. This is the artifact a human (or Claude, reading the source sheets directly by `source_file_id`) uses to confirm the app's number for a given machine/day matches the cell it came from.

## 4. A "verification log" (optional, audit trail)
When someone runs a verification, record `period, run_at, run_by, checks_passed, checks_failed, n_rows` in a `verification_log` table, and show the last run on the dashboard ("Last verified: 2026-05 · 3/3 checks passed · 14 Jun"). Append-only.

## 5. How an independent check works (no model in the data path)
1. Open the Verification view for a period (e.g. last month).
2. The three reconciliation checks must all be PASS.
3. Pick a few rows, follow `source_file_id` + `source_sheet` + `source_ref` to the actual Google Sheet cell, and confirm the raw value matches. (Claude can be asked to read the same source sheets and confirm the totals reconcile — it reads the source independently and compares; it does **not** supply or correct any number.)
4. If a row fails, it points to either a source-data issue (fix in the sheet) or a parser bug (fix the reader) — the routine localises which.

## Guardrails
- Verification is **read-only**. It never writes to or edits the fact tables, and never "auto-corrects" a mismatch.
- It reports raw computed values and their provenance only; it does not recompute through any model.
- A mismatch is surfaced and located, not silently reconciled.

## Acceptance criteria
- `GET /verify?period=…` returns per-machine figures with `source_file_id/sheet/ref` and the three reconciliation checks.
- All three checks PASS for a clean period; an introduced source change makes "daily vs summary" FAIL with the exact machine/month and the two numbers.
- The CSV export lets a row be traced to its source cell.
- Running verification writes one append-only `verification_log` entry and changes no fact data.
