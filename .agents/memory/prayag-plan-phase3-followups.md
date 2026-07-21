---
name: Phase 3 plan.py follow-ups (plant-wide banner, Feed/Tooling gates)
description: Design decisions for Phase 3 follow-up items — plant-wide RM banner, Feed gate wired to mixer batch log, Tooling gate wired to toolroom log.
---

## Plant-wide RM banner (`_is_plant_wide_rm`)

RM items whose `item_type` is empty or equals the plant name (e.g. `"PIPE"` for plant `"PIPE"`) are
generic plant-level inputs (e.g. GRANUALS-CG122).  They don't map to any specific machine type,
so showing identical RED on every card is misleading.

**Rule:** `_is_plant_wide_rm(item_type, plant)` returns True when `item_type` is empty or equals
the plant name (case-insensitive).  Such items get status `"plant-wide"` (not `"red"`) on every
machine gate.  The UI uses an amber ◐ dot.  Machine is NOT bottlenecked (plant-wide never counts
as bottleneck).  Instead, `build_plan` collects them into `plant_alerts: List[dict]` and the
`plan_board.html` banner renders them once above the summary bar.

**Why:** A machine running CPVC is not individually blocked by a GRANUALS shortage that affects
the whole plant; the operations team acts at the plant level, not per-machine.

**How to apply:** `_evaluate_gates` checks `_is_plant_wide_rm(rm_worst.item_type, plant)` after
finding `rm_worst`; branches to `"plant-wide"` vs `"red"`.  `build_plan` iterates `mat_recs`
directly to build `_best_pa` dict (one entry per item_name, lowest cover wins).  Routes unpack
`plans, plant_alerts = build_plan(plant, month)`.

## `_WorstRm` NamedTuple

`_find_worst_rm` used to return a 4-tuple; replaced by `_WorstRm` NamedTuple with fields:
`item_name, days_of_cover, cover_display, lead_time_days, as_of_date, item_type`.

- `days_of_cover` = recomputed rolling-average (sort key).
- `cover_display` = `stock_days_sheet` (sheet's own "Stock Days" column) when present, else
  falls back to `days_of_cover`.  For PIPE Report-2 these can diverge (sheet uses actual-month
  consumption, app uses rolling average).  The sheet value matches what staff read.

**Why:** Displaying the rolling-average cover (46.57d) to staff who see 17d in the sheet is
confusing and undermines trust.  `cover_display` shows what they recognise.

**How to apply:** Any test that previously did `name, cover, lead, _ = result` must now use
named access `result.item_name`, `result.days_of_cover`, etc.

## `build_plan` return type

`build_plan(plant, month) -> Tuple[List[MachinePlan], List[dict]]`

Both `/plan` and `/plan/detail` routes unpack the tuple:
```python
plans, plant_alerts = build_plan(plant, month)   # /plan
plans, _plant_alerts = build_plan(plant, month)  # /plan/detail
```

On exception: `plans=[], plant_alerts=[]`.

## Feed gate state machine (Phase 2D)

`mixer_recs_for_type: Optional[list]` kwarg to `_evaluate_gates`:
- `None` (default) → GREY "No compound/mixer data available"
- `[]` (data loaded, 0 records for this compound type) → GREY "No compound/mixer data for this type"
- non-empty, any record has `breakdown_hours > 0` AND `total_compound_kg == 0` → RED "Mixer breakdown"
- non-empty, no breakdown → GREEN "Compound available (N kg produced)"

## Tooling gate state machine (Phase 2D)

`toolroom_items: Optional[frozenset]` kwarg to `_evaluate_gates`:
- `None` (default) → GREY "No toolroom data for this machine"
- `frozenset()` (data loaded, no match for this machine) → GREEN "No active toolroom job"
- non-empty frozenset → RED "Active toolroom job: <items>"

For PIPE/CP, `_toolroom_for` always returns `None` (toolroom tracks moulds/lathe, no per-machine
extrusion die mapping).  For PTMT, it matches toolroom records against the machine's mould stds
via partial name matching.

## Per-machine compound-family resolution

PIPE/CP: `pipe_machine_materials.get(norm_m)` gives the set of families; falls back to all
`pipe_plan_by_family.keys()` for idle machines.
PTMT: classify each MouldStd item_name via `_classify_ptmt_item` → uppercased family set;
fallback to `{"FAUCET", "CISTERN", "SEATCOVER"}` for machines with no std register.
Other plants: empty set → `_mixer_for` returns None → GREY.
