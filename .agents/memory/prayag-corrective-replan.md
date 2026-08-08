---
name: Corrective Re-plan engine
description: mp_corrective_replan.py — run-rate PACE projection (not capacity); p90→mean(low-confidence)→not-started; 3 invariants; TEFFLONE tracked separately.
---

## Rule
This is a RUN-RATE MONITORING TOOL, not a capacity statement.
It answers: "if the plant keeps producing at last week's pace, where will output land?"
Labels: "Pace/day (run-rate)" / "Projected at current pace" / "Gap to demand at current pace".
The Capacity-Feasible Plan (machine hours × rates) is the authoritative "what can be made" source.

## Pace method fallback chain
1. ≥ MIN_DAYS_FOR_P90 (=5) non-zero days → **p90 (90th-percentile — optimistic ceiling)**
   Only the top 10% of production days exceeded this pace; it is grounded but not an average.
   **NOT the 10th-percentile (conservative floor) — that was a bug, now fixed.**
2. 1–4 non-zero days → mean(low-confidence,Nd) — shows a ⚠low-confidence flag; never presented as reliable
3. 0 non-zero days → cap_per_day=0, method="none", not_started=True

**Granularity rule:** p90 is computed over PER-CATEGORY-PER-DAY totals (sum of all item rows in
that category on each date), NOT over individual item-row pcs values. Using per-item values
would understate pace by 6-10× on busy days. `_parse_r11_daily_pcs` accumulates item rows into
day_map[category] before returning.

**Low-confidence:** CategoryResult.low_confidence = True when n_days > 0 and n_days < MIN_DAYS_FOR_P90.
Zero-production days are excluded from the mean/p90 (absence, not a constraint).

## "Not started" (Fix 2 — replaces "NO CAPACITY")
When produced_to_date == 0: not_started=True. NEVER emit "NO CAPACITY" string.
Display: "Not started — no pace data yet". Optional cap_feasible field shows capacity-plan figure if available.
Pass cap_feasible_by_cat: Optional[Dict[str, float]] to populate this column.

## produced-to-date fix (TEFFLONE)
R12 contains "TEFFLONE" (misspelled Teflon) = 17,600 pcs in Aug 2026.
Previously skipped → app showed 224,323 instead of correct 241,923.
Fix: _R12_OTHER_MATERIALS frozenset tracks unclassified materials as other_pcs (not assigned to any category row).
actual_produced_total = categorised_pcs + other_produced (reconciles with sheet TOTAL row).
result.other_produced holds these pcs for display in the provenance note.

## Authoritative Aug 2026 figure
R-11 pipe pcs = 50,293 (verified against R-11 TOTAL row).
R-12 fitting pcs = 174,030 (classified) + 17,600 (TEFFLONE) = 191,630 (verified against R-12 TOTAL row).
Total = 50,293 + 191,630 = 241,923. Report-1 shows 165,892 (different scope — all plants, not just Plumbing).
Live Aug 2026 CPVC Pipe p90 (90th-pct of 6 daily totals) = 5,433.5/day.

## Date column parsing
DATE column identified by HEADER TEXT ("DATE"), not hardcoded index. In Aug 2026 PIPE workbook
column A is blank and DATE is in column B (index 1). Parser reads by header so layout changes are safe.

## Date-resolution warning (unattributable rows)
_parse_r11_daily_pcs returns a sentinel key "_n_no_date" (int) when data rows appeared before any
date was resolved in the DATE column. compute_corrective_replan strips this sentinel and adds a
warning to result.warnings when n > 0. Normal carry-forward (blank date cells after the first row
of a day) remains silent.

## Cross-link
CategoryResult.cap_feasible: Optional[float] — machine-capacity feasible, passed in by caller.
When None: XLSX shows "—" column.
When provided: XLSX shows both pace projection AND capacity figure side by side.
Provenance tab always links to /machine-planning/report/capacity-feasible-plan.

## 3 enforced invariants (warn, never raise)
1. produced_to_date > 0 → cap_per_day > 0 (not_started categories exempt)
2. projected == pace × working_days_remaining (exact)
3. total_gap < total_remaining (when production exists)

## Date formats handled
_parse_date_cell (mp_corrective_replan.py): raises ValueError on unknown format (loud fail).
_parse_date (mp_followup.py): returns None on unknown format (silent skip — different contract).
Both handle: "Aug 1, 2026" (R-11/12), "1-Aug-2026" (PTMT), ISO, dd-mm-yyyy, plain day, leading apostrophe.

## Test suite
68 regression tests (63 original + 5 new p90 granularity tests):
- Relabelling, not-started, low-confidence flag, produced-to-date reconciliation, TEFFLONE
- XLSX label assertions, cap_feasible column optional/populated
- p90 is 90th-pct (not 10th); multiple items per day are SUMMED before p90; DATE by header text;
  unresolvable-date rows surface in warnings (not silently dropped)
