---
name: Prayag ideal-hours overrides & PIPE derived baseline
description: How the /input override layer and PIPE's Report-5 derived baseline plug into the ideal-hours precedence, and the cache-mutation trap.
---

# Ideal-hours precedence & overrides

The denominator behind a machine's utilisation is resolved by precedence (highest
first): manager **override** → in-sheet ideal-OUTPUT rate (HDPE) → in-sheet
ideal-HOURS column (PTMT) → PIPE **derived** (Report-5 per-day × calendar days) →
config baseline (baselines.json) → **app default** → none. `ideal_hours.resolve()`
is the single authority; `ideal_hours.SRC_*` are the source labels/badge keys.

## App-default tier (output-only-plant trap)
`ideal_hours.APP_DEFAULT_IDEAL_HOURS` carries real planned hours NOT published in
the sheets (GARDEN=500, TANK=500, HDPE=550 hrs/machine/month). `sheets._emit_daily`
stamps it (`ideal_source="app_default"`) only when no higher tier matched, in BOTH
the HDPE-rate branch (no in-sheet hours) and the final config-baseline else branch.
**Why the suppression matters:** an output-only plant (`actual_hours=0`) with a 500
denominator would render a FAKE 0% utilisation, and in a mixed rollup the 500 would
dilute real plants' utilisation. **How:** plants in
`ideal_hours.PLANTS_WITHOUT_RUNHOURS` get `Record.runhours_tracked=False`;
`compute_metrics` keeps a SEPARATE `util_ideal` denominator that only accumulates
ideal hours from rows where `runhours_tracked` is True (and shift-log rows), so
utilisation is `util_run/util_ideal` and `util_available = util_ideal > 0`.
`m.ideal_hours` still includes the 500 (the honest planned-hours total + the /input
"From sheet" column), but it no longer drives the ratio. HDPE logs run hours
(`runhours_tracked=True`) so util computes against 550 once workbooks fill.

**GARDEN now tracks run hours (only TANK remains output-only):** GARDEN keeps OUTPUT
from its per-machine block tabs but ALSO joins per-machine per-date RUN HOURS from
the workbook's "Daily Report" matrix tab (`runhours_tab` in `_emit_blocks`, joined
by trailing machine-number + date; output is NEVER read from the matrix). So
`PLANTS_WITHOUT_RUNHOURS={"TANK"}` and GARDEN's utilisation computes against the
app-default 500. **Day-grain fake-0% trap:** the source logs run hours sparsely (a
machine may produce output on a day with no run-hour entry). Spreading the 500
across ALL active/output days would give those no-run-hour days `ideal>0` and a
displayed 0% utilisation — a fake 0 at the DAY grain. Fix: spread the app default
ONLY across the days a machine actually logged run hours (`r.actual_hours>0`), so
Σ ideal per machine == 500 (monthly rollup unchanged) AND no-run-hour days keep
`ideal_hours=0` → utilisation BLANK. A machine/month with zero run hours (matrix not
yet filled) gets no denominator → fully suppressed. `_emit_blocks` also distinguishes
matrix-tab-missing vs parse-failure vs no-hours-yet in its warning text so a silent
layout drift doesn't masquerade as "no run hours yet".
`app._IDEAL_SRC_FROM_RECORD` must map `"app_default" → SRC_APP_DEFAULT` or the
/input badge falls back to "From sheet".

## PIPE derived baseline
PIPE publishes "Ideal Run Hour Per Day" in a SEPARATE tab (Report-5) with a SPLIT
header (the label sits one row above the `MACHINE` column header), and the tab is
shared with Mixer/Moulding/Grinder families. So it cannot be read by the same-row
summary-column parser — it needs a dedicated split-header scan, then filter to
PIPE M/C rows and join by machine number (`_mc_key`). Monthly ideal = per-day ×
the month's calendar days, distributed across the machine's daily rows (one row
per machine-day) so a full-month rollup reconciles. `ideal_source="derived"`.

## Overrides
Stored ONLY in the app DB (`store.ideal_hours_overrides`, append-only, latest-wins,
keyed (plant, machine, month)), NEVER written back to the sheets. Set/clear via the
`/input` page; clearing reverts to the sheet/derived value. An override of **0** is
meaningful ("not expected to run" → utilisation suppressed), not "missing". v1 is
ideal HOURS only — efficiency / PIPE Col I are out of scope.

## Cache-mutation trap (important)
**Why:** `_load_daily_cached`'s L1 in-process cache returns the SAME Record objects
across requests (no copy). Mutating `r.ideal_hours` in place to apply an override
would corrupt the cached sheet baseline — once the override is cleared, the stale
cached value would persist instead of the real sheet value.
**How to apply:** `app._apply_ideal_overrides` rebuilds the row list with
`dataclasses.replace` for overridden rows (copies), leaving cached objects intact.
Any future per-request mutation of cached daily Records must do the same. (Note
`_apply_baselines` mutates monthly rows in place safely only because its result is
deterministic/idempotent — overrides are not.)
