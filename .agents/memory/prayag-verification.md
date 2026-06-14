---
name: Prayag Data Verification
description: The read-only verification layer — what it guarantees, how it differs from Data Confirmation, and the invariants any change must preserve.
---

# Prayag Data Verification (read-only reconciliation layer)

A separate concern from **Data Confirmation** (the four-tier gating audit). Verification does NOT gate or grade — it *exposes already-computed figures with provenance* so a human (or Claude reading the same sheets independently) can reconcile the app's numbers against the source Google Sheets, plus three reconciliation checks.

## Invariants any change MUST preserve
- **Read-only on facts.** Nothing in the verification path writes to or corrects a figure. The ONLY persisted write is the append-only `verification_log` audit entry on explicit `POST /verify/log` — and that row stores run metadata (who/when/checks_passed/failed/n_rows), never a fact value. There is a regression test asserting `GET /verify` writes zero audit rows.
- **Deterministic, no model in the data path.** Figures come straight from the already-loaded monthly + daily `Record`s; verdicts are pure arithmetic at a 0.5% tolerance. No Claude call anywhere in this path.
- **Surface AND locate mismatches; never silently reconcile.** Every FAIL carries the plant/machine and the two numbers (A vs B) + diff + %. PIPE daily is net-of-rejection vs gross monthly, so its daily-vs-summary line FAILs *by design* — it is annotated, NOT auto-passed.

## The three checks (each PASS / FAIL / NA)
1. daily-vs-summary — daily rows summed to the month vs the monthly summary grid; plant-level always, per-machine where the `_mc_key`-style machine-number join holds. NA when no daily rows.
2. row-vs-total — summed detail rows vs the sheet's own TOTAL (read from each monthly report's `reconcile` payload).
3. plant-vs-machines — plant total vs Σ its machines (catches output booked against a blank machine).

## Provenance honesty
`source_ref` is the machine's own row label in the named sheet, NOT a fabricated A1 cell address — we don't track per-cell addresses, so the honest locator is the row identity. Full file IDs are in the CSV export.

## Gotcha
Plants with daily data but no monthly baseline (e.g. PTMT) show a large daily-vs-summary FAIL (daily N vs summary 0). That is honest surfacing of a missing monthly grid, not a bug.
