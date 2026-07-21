---
name: MP-2 engine design
description: LPT+parallel-split optimiser, rate fallback, Report-11/11A-D; key design decisions for Phase MP-2.
---

## Rate fallback for missing kg/hr entries

SWR-pipe and AGRI-pipe items have no seeded per_hour rates. The engine resolves:
1. Direct lookup in ph_dict (item_code → kg_per_hr)
2. Average of seeded items with the **same material** (cross-ref via routing table's material column)
3. Overall average of all seeded kg_per_hr items

`rate_estimated=True` on all fallback items. The fallback must use the routing table to
determine which per_hour items belong to which material — per_hour rows don't store material directly.

**Why:** SWR/AGRI items have zero per-hour rows; using only-CPVC/UPVC rates for the overall avg
prevents divide-by-zero and gives a reasonable starting estimate.

## LPT + parallel-split algorithm

- Sort items by material_kg DESC (Largest Processing Time first)
- For each item: find least-loaded capable machine (by utilisation%)
- If item fits on that machine: assign all there
- If it won't fit AND multiple capable machines exist: split proportional to remaining capacity
- If all capable machines have zero remaining: assign overflow to least-overloaded

The split produces exact `sum(assignment.hrs) == item.machine_hrs` (verified in tests).

**Why:** Extrusion machines can run in parallel; splitting is physically valid and dramatically
reduces peak load (acceptance run: 42.6% peak reduction).

## Persistence pattern

- Demand JSON stored in `mp_plan_run` (Postgres) + `_MP2_RUN_CACHE` dict (in-process fallback)
- Session holds only `mp2_run_id` (small) — engine re-runs at each results/download request
- This means results are always fresh (use current mp_* table values, not stale stored result)
- Re-run is fast: all reads are from Postgres mp_* tables (no network I/O)

## Report-11 column order (exact, spec-locked)

DATE | MACHINE NAME | MACHINE NO. | TYPES | ITEM CODE |
Running Hours | Ideal Weight (KG) | Pcs | Weight | Wt./Pc. |
Ideal Output Per Hour | Actual Output Per Hour | Output Efficiency

Header at row 5. Rows 1–4 = title block. Data from row 6.
Actuals-only columns (Actual Output Per Hour, Output Efficiency) left blank for plan output.

## Machine-group config

`REPORT_11_GROUPS` in `mp_engine.py` (config-driven, not hardcoded in generators):
- A: M/C-1, M/C-2
- B: M/C-3, M/C-4
- C: M/C-5, M/C-6
- D: M/C-7, M/C-8, M/C-9

`mp_reports.py` imports this dict so group membership is changed in one place.

## Coverage gaps

- `no_weight`: items in demand but not in BOM — set before rate/routing lookup
- `no_machine`: items with BOM weight but no pipe routing row with M/C-* machine
- `idle_machines`: machines in routing but receiving zero assignments this plan
- `locked_out_machines`: machines in mp_machine(kind=extrusion) with zero routing rows
  (M/C-7 and M/C-8 in July 2026 seed data)

## Acceptance numbers (July 2026 seeded data, qty=254/item, 269 pipe-routed SKUs)

- Routable material kg: 433,298   Fresh: 324,974   Pulv: 108,325
- Estimated-rate items: 196 (all SWR+AGRI, none CPVC/UPVC)
- Locked-out: M/C-7, M/C-8
