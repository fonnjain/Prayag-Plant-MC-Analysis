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
**Why the suppression matters:** GARDEN/TANK record OUTPUT only (their daily
parsers set `actual_hours=0`), so a 500 denominator with 0 run hours would render a
FAKE 0% utilisation, and in a mixed rollup the 500 would dilute real plants'
utilisation. **How:** plants in `ideal_hours.PLANTS_WITHOUT_RUNHOURS` get
`Record.runhours_tracked=False`; `compute_metrics` keeps a SEPARATE `util_ideal`
denominator that only accumulates ideal hours from rows where `runhours_tracked` is
True (and shift-log rows), so utilisation is `util_run/util_ideal` and
`util_available = util_ideal > 0`. `m.ideal_hours` still includes the 500 (it is
the honest planned-hours total + drives the /input "From sheet" column), but it no
longer drives the ratio. No regression: pre-change, no-baseline rows had
ideal_hours=0 so `util_ideal == m.ideal_hours` for all real plants. HDPE logs run
hours (`runhours_tracked=True`) so util computes against 550 once workbooks fill.
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
