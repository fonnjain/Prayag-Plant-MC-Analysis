---
name: Phase 3 plan.py join correctness
description: Critical gotchas in build_plan roster, PTMT queue join, and Material gate logic
---

## PIPE production roster — must use _PIPE_PRODUCTION_MACHINES constant

`build_plan` roster = `_PIPE_PRODUCTION_MACHINES` (9 × "PIPE Pipe M/C-N") + daily prod_recs.
Maintenance records (Report-16/8) are a LEFT JOIN index only — NEVER added to the roster.
MouldStd names are a lookup index for PTMT — NEVER added to the roster.

**Why:** maint_recs contains lab balances, cooling towers, chiller units etc. which were
being added to the roster and bloating PIPE to 78+ machines including "ANALYTICAL BALANCE".

**How to apply:** Any time you touch `build_plan`, check that the roster seed loop only
touches `_PIPE_PRODUCTION_MACHINES` + `prod_recs`. maint_idx and ptmt_machine_stds must
only appear in the gate-evaluation / run-queue lookup, never in the machine set.

---

## PTMT run-queue join — MUST use family-based join, NOT item_code

`MouldStd.item_code` is a mould part number ("PSF-190", "PSF-34 PP").
`PlanRecord.item_code` is a finished-product catalog code ("121-O", "144").
**These are entirely different code systems — zero overlap in production data.**

Correct approach (`_build_run_queue` PTMT path):
1. Look up MouldStd records for the machine (via `_lookup_list(ptmt_machine_stds, norm_m)`)
2. Classify each `std.item_name` → family via `_classify_ptmt_item()` (keyword match → cistern/seatcover/faucet)
3. Fetch PlanRecords from `ptmt_plan_by_family[family]`
4. Rate comes from `plan_r.per_hour_output` (finished-product rate), NOT `std.theoretical_pcs_hr` (mould rate)

If a machine has no mould stds registered, it gets all three families (faucet/cistern/seatcover).

**Why:** First implementation joined through item_code → 0 queue items for all 49 PTMT machines.
Took two passes to find: (a) join key is wrong, (b) rates are wrong (mould ≠ finished product).

---

## Material gate — per-machine, with plant-name as generic type

`_evaluate_gates` Material gate:
- Empty run_queue → GREY ("No mapped demand — no job assigned")
- Non-empty → build `item_types = queue_families ∪ {plant.upper()}`
- Filter RM records to those item_types (empty item_type always included)
- `_find_worst_rm(mat_recs, item_types=item_types)` → RED if any at reorder; GREEN if all healthy; GREY if no RM data

**Why:** All PIPE RM records have `item_type='PIPE'` (plant-level generic, not type-specific like 'CPVC').
Including `plant.upper()` in `item_types` ensures these generic records are always checked.
A CPVC machine checks `item_types={'CPVC', 'PIPE'}` → PIPE-labelled resins are included.

---

## _evaluate_gates current signature (Phase 3)

```python
_evaluate_gates(norm_m, plant, run_queue, mat_recs, maint_idx, mp_idx, m_result, actual_h, ideal_h, month)
```

## _build_run_queue current signature (Phase 3)

```python
_build_run_queue(plant, norm_m, ptmt_machine_stds, ptmt_plan_by_family, pipe_machine_materials, pipe_plan_by_family, pipe_all_plan_recs=())
```
Note: 4th param is `ptmt_plan_by_family` (family→list), NOT `plan_by_code`.
