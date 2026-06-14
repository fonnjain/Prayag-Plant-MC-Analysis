---
name: Prayag daily/monthly grain engine
description: Durable invariants for mixing daily and monthly Records in the metrics engine and reconciling the two grains.
---

# Daily Records come in two shapes — keep them distinct

There are two kinds of daily `Record`, and the metrics engine treats them differently:
- **shift-log** rows carry shift timing (`shift_len_min > 0`) → hours come from the time model and they can feed OEE.
- **daily-matrix** rows (per-date production grids) carry hours/output directly and leave `shift_len_min == 0` → they must be summed like monthly rows.

**Why:** a daily-matrix row with `shift_len_min == 0` run through the shift-log time model yields 0 worked/ideal hours, so utilisation and output-efficiency silently collapse to 0. This is invisible to reconciliation checks because those sum raw fields, not the metric output.

**How to apply:** when adding any new daily ingestion path, pick a shape deliberately — set shift timing OR set hours/output directly, never half of each. After changes, verify a full-month daily rollup's utilisation equals the monthly grid's for the same machines.

# Daily↔monthly reconcile by distributing the monthly ideal

Per-day ideal hours = the machine's monthly ideal hours ÷ its active days that month, so a full-month daily rollup collapses back to the monthly total and the two grains reconcile exactly. Absolute utilisation is genuinely low — machines run a few hundred hours against a flat ~500 hr/month/machine theoretical capacity, so low numbers are not a bug.

# Sub-monthly windows can't borrow monthly totals

Sub-monthly windows (yesterday / 7d / rolling 30d / custom < 27d) never align to calendar months, so monthly totals cannot be sliced into them. Never mix non-sliceable monthly totals into a partial window — read real daily rows for the window instead.

# Daily ingestion covers EVERY plant with a daily file, via a per-plant layout config

Each plant declares its daily tab + layout (`matrix` = wide per-date grid, or `long` = one row per machine per date). One workbook may emit several logical plants (e.g. the PIPE workbook emits both PIPE and a `MOULDING `-prefixed plant from a second tab). Do not hard-skip plants that lack a monthly grid — read them and resolve the ideal denominator by precedence.

**Why:** the original engine only ingested plants with BOTH a daily file and a monthly grid, so sub-monthly windows fell back to a misleading monthly summary ("0/N machines"). The fix reads daily for all configured plants and degrades the *baseline*, not the *data*.

# Ideal-denominator precedence + the "no baseline set" honest fallback

Per-machine ideal hours/output resolve in strict order: **monthly grid → in-sheet ideal column (e.g. PTMT) → baseline master → none**. When none resolves, utilisation/output-efficiency are *suppressed* (not computed as a misleading 0%) and the figure is flagged "No baseline set"; raw hours + output are still shown.

**How to apply:** metrics expose `util_available`/`eff_available`/`headline_available` and `headline` falls OEE→eff→util→"No baseline set". Templates must gate on these flags, **not just `oee_available`** — the OEE-vs-OutputEfficiency binary is wrong for util-only and no-baseline plants. Use `headline_label`-style branching (OEE / Output Efficiency / Utilisation / No baseline) for the headline caption everywhere it appears.

# Stay in daily grain whenever a daily FILE exists — even an empty window

A sub-monthly window must serve daily grain whenever a daily workbook exists for the needed month(s), *even when that specific window has zero rows* (e.g. "Yesterday" before today's data is entered). Only fall back to the monthly summary when NO daily file exists for the period, or the daily read fails outright.

**Why:** falling back to monthly on an empty window re-introduced the misleading "monthly totals shown instead" banner and showed un-sliceable monthly figures for a partial window. An empty daily window is honest as daily-zero with a banner that names the data horizon ("entered through <date>").

**How to apply:** gate on `daily_file_months` (any needed month has a configured daily file), not on whether the window has rows. Build the grain banner where daily-vs-monthly is decided, never optimistically in the period parser.

# No-roster daily plants count their own reporting machines (never 0/0)

PTMT and TANK have a daily file but NO monthly-grid roster (not in `ANNUAL_SOURCES`). In a daily view their completeness must count the daily-reporting machines as both expected and present, or the card shows "0/0 machines" despite real data.

**Why:** the master roster comes only from the full-FY monthly grid, so plants absent from it contribute 0 expected → the completeness loop's `if not master_codes: continue` zeroed them. The daily file is the only roster we hold for those plants.

**How to apply:** in `tier1_completeness`, when `daily_used` and `master_codes` is empty but daily rows are present, add `len(present)` to both present and expected (honest coverage); a separate note already records there's no grid to cross-check.

# Detected-sources screen shows the full configured inventory

The `/sources` screen must list every configured workbook (annual grids + daily descriptors) regardless of the selected period — build it from the full inventory, not the period's loaded subset, or daily/annual entries disappear depending on grain.

# Finishing/regrind rows are excluded from mixed totals — and the audit MUST mirror that

A `Record.is_finishing` row (e.g. PTMT regrind/grinder KG) re-processes already-counted material. `compute_metrics` excludes finishing rows from a *mixed* group (`prod_rows = non_fin if non_fin else rows`) so the plant total isn't double-counted, but a *pure-finishing* group still shows itself.

**Why:** the Tier-2 engine self-reconcile (`confirm.py`) compares the published plant total against a raw row sum. If the row sum includes finishing rows while the published total excludes them, every mixed page false-flags an ERROR equal to the regrind KG.

**How to apply:** any code that re-derives "the row sum behind the published total" (reconciliation, verification, exports) MUST apply the same `non_fin if non_fin else rows` selection as `compute_metrics`. Treat the two as a locked pair — change one, change the other.

# Plant-level (machine-less) plants need machine-less-aware reconciliation and UI filtering

Some daily plants report with NO machine identity (e.g. TANK, logged per item: `machine=""`, `mould=item`, unit pcs). Their segment total has no machine "lines" to roll up.

**Why:** the segment-vs-lines hierarchy check sums machine-bearing rows; a machine-less segment yields lines_sum=0 and false-flags "segment total ≠ Σ lines". Likewise machine views/report tables that key by `machine` render a blank-machine row.

**How to apply:** skip the segment-vs-lines reconcile for any (plant, segment) with zero machine-bearing rows (track `seg_has_machines`); gate the unit-mismatch check on `r.machine`; and filter `if not machine: continue` in machine_view, the machine filter list, and machine-keyed report tables. Plant-level output still shows everywhere else.

# Outliers are compared WITHIN a (plant, segment) process group, never plant-wide

PTMT mixes processes with incomparable output scales (Injection vs Blow vs Corrugator vs Grinding). A plant-wide median flags whole small-output processes as "outliers". Compare each machine to the median of its own `(plant, segment)` group instead.

**Note:** PTMT's daily file has no monthly grid, so its 52-machine roster + process-group segments (`_ptmt_group`: GRIND→Grinding[is_finishing], BLOW→Blow Moulding, CORRUGAT→Corrugator, N-→Injection N-line, else Injection standard) are DYNAMIC from the reporting machines — never hardcode a count. The stale `replit.md` gotcha that HDPE/PTMT/TANK are "skipped" is wrong; all daily-file plants are ingested via per-plant layout dispatch (`blocks`/`tank`/`matrix`/`long`).
