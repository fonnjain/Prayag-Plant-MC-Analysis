---
name: Tank R-39 run-hours reconciliation
description: How Tank (KH/VN/WB) run hours are sourced and reconciled from two tabs; machine label column offset; placeholder records for DR-only dates.
---

## Rule

Corrected hours per date = `max(DR, PR)` over the **union** of all dates either source holds.

## Sources

- **DAILY REPORT** — wide per-date matrix. **Machine labels are at col 1 (col 0 always blank due to merged-cell artefact).** TOTAL row carries per-date triplets (run hrs / output kg / rej kg). Output from this tab is a cross-check only — never the authoritative figure.
- **PROD. REPORT** — per-item journal. Hours column is named `PRODUCTION HOURS` (not `RUN HOURS`); match by header text (R-04).

## Per-plant status (FY26-27)

| Plant | DAILY REPORT | PROD. REPORT PRODUCTION HOURS |
|---|---|---|
| TANK_VN | Real data — Apr–Jul | Some dates present |
| TANK_WB | Apr only (MACHINE-1 + MACHINE-2); May–Jul all-zero | No hours column |
| TANK (KH) | All-zero all months | Column present but every cell blank |

## VN four-month union evidence

| Month | DR h | PR h | Union h | Key driving dates |
|-------|------|------|---------|-------------------|
| Apr | 64 | 68 | **76** | Apr-16 max(12h); Apr-28 DR-only 8h |
| May | 196 | 192 | **196** | May-8 DR wins 12h vs PR 8h |
| Jun | 184 | 208 | **208** | Jun-27/29/30 PR-only 8h each |
| Jul | 232 | 224 | **232** | Jul-19 DR-only 8h, 0 kg output |

Apr is the decisive case: gaps in BOTH directions within one month.

## Placeholder records (CRITICAL)

DR-only hours dates (e.g. VN Jul-19: 8h, 0 production) have **no PROD. REPORT rows**. Without a synthetic record, those hours are tracked in `recon_audit["union_hrs_total"]` but lost from `sum(r.actual_hours for r in recs)` — the UI shows the wrong run time.

Fix: after applying hours to PROD. REPORT records, create a synthetic Record (total_count=0, reject_count=0, runhours_tracked=True) for every date in `union_hrs` not yet in `dates_assigned`. This is honest — the machine ran but made nothing.

## PLANTS_WITHOUT_RUNHOURS

Corrected to the **empty frozenset** (R-25 closed 2026-08-15). `_emit_tank` stamps `runhours_tracked` per-record based on union hours > 0, not a plant-level constant.

## Utilisation

`ideal_hours = 0.0` on every Tank record → utilisation suppressed. Actual run hours ARE recorded and visible. Utilisation activates only when a planned-hours baseline is supplied externally.

## WB April internal DR gap

Per-date sum = 100h; monthly summary cell = 98h. Use per-date sum; flag the 2h discrepancy in `recon_audit["dr_internal_gap_hrs"]`.

**Why:** DR monthly summary cell can lag or differ from per-date triplet sum. Per-date triplets are primary; the cell is a check.
