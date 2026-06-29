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

Per-machine ideal hours/output resolve in strict order: **in-sheet ideal-OUTPUT rate (a plant publishing its own per-machine Ideal Output, e.g. HDPE — also takes its M/C Run Hour for utilisation) → monthly grid → in-sheet ideal-HOURS column (e.g. PTMT) → baseline master → none**. When none resolves, utilisation/output-efficiency are *suppressed* (not computed as a misleading 0%) and the figure is flagged "No baseline set"; raw hours + output are still shown.

**How to apply:** metrics expose `util_available`/`eff_available`/`headline_available` and `headline` falls OEE→eff→util→"No baseline set". Templates must gate on these flags, **not just `oee_available`** — the OEE-vs-OutputEfficiency binary is wrong for util-only and no-baseline plants. Use `headline_label`-style branching (OEE / Output Efficiency / Utilisation / No baseline) for the headline caption everywhere it appears.

# Stay in daily grain whenever a daily FILE exists — even an empty window

A sub-monthly window must serve daily grain whenever a daily workbook exists for the needed month(s), *even when that specific window has zero rows* (e.g. "Yesterday" before today's data is entered). Only fall back to the monthly summary when NO daily file exists for the period, or the daily read fails outright.

**Why:** falling back to monthly on an empty window re-introduced the misleading "monthly totals shown instead" banner and showed un-sliceable monthly figures for a partial window. An empty daily window is honest as daily-zero with a banner that names the data horizon ("entered through <date>").

**How to apply:** gate on `daily_file_months` (any needed month has a configured daily file), not on whether the window has rows. Build the grain banner where daily-vs-monthly is decided, never optimistically in the period parser.

# No-roster daily plants count their own reporting machines (never 0/0)

TANK has a daily file but NO grid roster AND no fixed register, so its completeness counts the daily-reporting items as both expected and present, or the card shows "0/0" despite real data. (PTMT is NO LONGER in this bucket: it has the fixed 55-machine register in `sources.PTMT_GROUPS` — held to all 55 — see `prayag-ptmt-roster.md`. HDPE has a real monthly grid in `ANNUAL_SOURCES`, so its roster comes from the grid like PIPE/GARDEN/MOULDING.)

**Why:** the master roster comes from the full-FY monthly grid (plus the PTMT fixed register), so a plant with neither a grid nor a register contributes 0 expected → the completeness loop's `if not master_codes: continue` zeroed it. The daily file is the only roster we hold for such a plant (today: TANK only).

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

**Note:** PTMT's daily file has no monthly grid, but its roster is NOT dynamic — it is the authoritative 55-machine register in `sources.PTMT_GROUPS` (see `prayag-ptmt-roster.md`), so completeness is held to all 55 (a never-reporting machine is a gap). Each machine's process group + finishing flag come from that register; `_ptmt_group`'s string heuristic (GRIND→Grinding[is_finishing], BLOW→Blow Moulding, CORRUGAT→Corrugator, N-→Injection N-line, else Injection standard) is kept only as a fallback for unknown codes. Only TANK has a truly dynamic plant-level roster (its reporting items). The stale `replit.md` gotcha that HDPE/PTMT/TANK are "skipped" is wrong; all daily-file plants are ingested via per-plant layout dispatch (`blocks`/`tank`/`matrix`/`long`).

# Report-5-only auxiliary machines have no daily tab — synthesise month-grain Records

Grinders, pulverizers, sockets and mixers in the Pipe&Fitting workbook live ONLY in the Report-5 monthly summary (no per-machine daily tab), so the normal daily path never creates a Record for them and they vanish from the app. Synthesise one MONTH-grain Record per such machine after the daily precedence loop.

**Why:** the classifier MUST recognise ONLY grind/pulver/socket/mixer and return None for everything else. An earlier version defaulted unrecognised labels to a "Reprocessing" PIPE row, which leaked the untagged MOULDING production lines (A01–D07) and idle pipe M/C-7/8 in as bogus auxiliaries under the PIPE emit. Route owner by the label's `(PIPE)`/`(MOULD)` tag; untagged → PIPE. Skip any row already matched to a daily machine (by `_mc_key` or normalised label).

**How to apply:** carry run-day utilisation = run hrs / (ideal-per-day × run days), NO clamp (a grinder can exceed 100%); idle (0 run days) → ideal_hours=0 so util/eff stay blank, never a fake 0%. All are `is_finishing=True` so output stays out of the plant headline. A blank in-sheet Ideal Output Per Hour → efficiency n/a + a non-blocking advisory note. These month-grain rows must be excluded from sub-monthly day windows and from per-plant freshness/`last_updated` (guard those on `r.grain == "daily"`) — a whole-month figure can't be sliced into a day.
