---
name: MP-3 fitting engine design
description: Key decisions for the fittings optimiser, rate resolution, and Report-12
---

# Fitting engine architecture (MP-3)

## Data sources
- `mp_fitting_std` (207 rows): item_code × machine × cavity × cycle_time_sec — Report-12 history routes
- `mp_routing` non-M/C rows (207 rows): same data — item_code → moulding machine (material='' empty)
- `mp_per_hour` basis='cycle' (151 rows): item_code → cycle_time_sec — DISJOINT from fitting_std (0 overlap)
- 24 moulding machines (A01–D07), all capacity=500h, identified by `not machine.startswith("M/C-")`

## Rate precedence (pcs/hr)
1. fitting_std: cavity × 3600 / cycle_time_sec → rate_estimated=False
2. per_hour basis='cycle': 3600 / cycle_time_sec → rate_estimated=True
3. material average pcs/hr (from demand items that have fitting_std entries) → rate_estimated=True
4. overall average across all fitting_std rates → rate_estimated=True

## Routing / material-level fallback
- item_routes: from fitting_std machine column (primary) + mp_routing non-M/C rows (secondary)
- mat_machines: built from demand items whose material is known (from tab) AND appear in fitting_std → {material: [machines]}
- Items with no item_routes entry: assigned to mat_machines[material] → route_estimated=True
- Items whose material has no machines in mat_machines: unroutable (no_machine gap)

**Why:** mp_routing.material is empty ('') for moulding rows so we cannot build mat_machines from DB alone; must cross-reference with demand tab to know material.

## Coverage reality
~209 of ~332 planned fitting items have no Report-12 history route → material-level fallback
~23 have no BOM weight → flagged, not dropped

## Report-12 column order (header row 6, 16 cols)
DATE | MATERIAL | ITEM CODE | Moulding Machine | Mould Cavity | Run Cavity |
No. of Cycle | Pcs | Wt in Kgs | Cycle Time | Running Hours | Ideal Output Per Hour |
Actual Output Per Hour | Output Efficiency | Rejection Pcs | Rejection Kg

Actuals-only (cols 13-16): always blank in plan output.
No. of Cycle = qty / cavity (rounded). Ideal Output Per Hour = pcs_per_hr.
