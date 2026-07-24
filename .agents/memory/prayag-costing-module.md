---
name: Costing module design
description: Architecture, freeze rules, file IDs, and parser lessons for the /costing hub (Labour + RM costing).
---

## Freeze rule
- LIVE_FY = "2627" (FY2026-27); any earlier FY is FROZEN.
- Frozen FY snapshot: written ONCE to DB (costing_labour_monthly + costing_labour_meta); load_labour_fy() is a no-op unless force=True.
- No edit controls shown on frozen pages; badge: "Frozen — FY closed".

## File IDs — Plumbing labour workbooks
- 2627: 1ttlpHLrlTsimcdSmk3-HGnPu14PX7SGtk9Of2Q5pDvw (already in sources.py as seg_labour 26-27)
- 2526: 1N6gVEZyv1CLs5ARQHeebjAxOyvdkwOJFPqDWHUUOy_g (sources.py seg_labour 25-26)
- 2324: 1fjsJ6g91sWADHQ9vc0-cxiMQbQDlE5fKzyVx_mJq4Fc (costing-only, not in ANNUAL_SOURCES)
- 2223: 1H4W23l3YPPkLXm8HYP7-uRKS4zaamHW4BByX7O_uU68 (costing-only, not in ANNUAL_SOURCES)
- FY2024-25 has no workbook.

## Plumbing tab dual-layout parser (costing_labour.parse_plumbing_tab)
- Header row is at index 2 (row 3 in sheet); scanner checks first 8 rows for robustness.
- FY2025-26: no contractor columns; has "Per KG Labour Cost" column.
- FY2026-27: adds "Contractor Labour" (after No. Of Labour) + "Paid Wages for Contractor" (after Paid Wages); no Per KG col → recomputed.
- TOTAL row (row 4) is skipped because its month_label ("TOTAL") is not in MONTH_LABELS.
- Per-kg and per-hour costs are always recomputed from raw figures — sheet formulas are never trusted.

## Report-12 fittings formula — GROSS ACTUAL (corrected)
- AUTHORITATIVE: fitting_kg = "Wt in Kgs" + "Actual Rejection Weight (in Kgs)"
  Same gross convention as pipe (good output + rejected weight).
- "Weight of Total Production" (formula col = pcs × std weight) is ONLY for
  the data-quality variance flag (>2% = bad hand-keyed Wt-in-Kgs values).
- FY2026-27 acceptance: Apr 90,038.43 / May 76,523.39 / Jun 97,980.99 = 264,542.81 kg total.
- FY2526 fittings: 975,609 kg, Rs 4.157/kg.
- DO NOT use "REJECTION & PRODUCTION" tab (fittings block still holds pieces, not kg).
  check_rejection_prod_tab_units() guard: is_unit_mismatch when tab_sum > 10× r12_fitting_kg.

## Report-12 two-row header (FY2627)
- Main header row: has "Actual Rejection Weight (in Kgs)" + "Weight of Total Production".
- Sub-header row (immediately below): has "Wt in Kgs" under "Output Production" group.
- _r12_find_header() returns (main_idx, data_start, col_map).
  data_start = main+2 if sub-header present, main+1 otherwise.
  Requires "rejection_kg" to anchor the main header (robust to FY layout differences).
- DB column r12_rejection_kg stores the rejection-weight component separately.

## Ideal Labour Cost tab
- Default rates: Pipe Rs 2.50/kg, Fittings Rs 6.50/kg.
- parse_ideal_rates() scans for "PIPE" / "FITTING" row labels and looks right for a numeric.
- Falls back to defaults on any parse failure — never crashes.

## Acceptance figures (FY2025-26, verified from source)
- Paid hours: 431,468 · Actual hours: 362,225 · Wages: Rs 21,452,790
- Per-kg actual: Rs 4.157 · Weighted ideal: ~Rs 3.26/kg → ~28% above ideal
- Pipe: 4,184,706 kg · Fittings: 975,609 kg · Total: 5,160,315 kg

## Acceptance figures (FY2026-27, Apr–Jun 2026)
- Pipe: 637,410 kg · Fittings: 264,542.81 kg · Total: 901,952.81 kg
- Per-kg labour cost: Rs 6.122416759 (worsening vs FY2526 Rs 4.157)

## Report-22 machine allocation
- Tab in the monthly Pipe & Fitting workbook; may be absent (handled gracefully).
- Row 2: date headers from col E in pairs; Row 3: TOTAL MANPOWER / TOTAL HOURS sub-headers.
- Machine rows: match _MACHINE_RE (PIPE M/C, M/C-N, MOULDING MACHINE, etc.).
- Dept/overhead rows: contain PACKING, QUALITY, FG SHIFT, THREAD, PRINT, REWORK, etc.
- Labour cost per machine = total_hours × per_hour_cost_actual.

## DB tables
- costing_labour_monthly: (segment, fy, month_label) UNIQUE; upsert deletes then re-inserts all months.
  Extra columns: fitting_r12_kg, wt_in_kgs_total, r12_rejection_kg, fitting_kg_source,
                 fitting_variance_pct, fitting_divergent_n, fitting_divergent_rows JSONB.
- costing_labour_meta: (segment, fy) UNIQUE; tracks frozen flag, ideal rates, load timestamp.

## RM costing
- get_recipe_cost_map(segment, effective_month) → {(material, type): Rs/kg} from mp_compound_recipe.
- Effective month mapping: 2627→2027-03, 2526→2026-03, 2324→2024-03, 2223→2023-03.
- Price sanity check threshold: 10% divergence from Report-2/3/4 purchase price flags a warning.

## Routes
- GET /costing — hub (category + FY + tab selectors)
- POST /costing/api/load-labour — {"category", "fy", "force"} → loads/freezes; returns {"ok", "n_months", "skipped"}
- /labour remains intact (run-hours page; tile in home.html now points to /costing)

## Test isolation — costing tests
- The real store module may be imported before test_costing.py runs (e.g. by test_daily_parsers.py).
- patch_deps fixture MUST force-set sys.modules["store"] (not setdefault) and MUST use yield
  so teardown restores the original store after the module completes.
- Costing modules (costing_model/labour/rm) must be evicted from sys.modules in both setup
  and teardown so they re-import with fresh stubs each time.
- Decisive test: run test_daily_parsers.py FIRST then test_costing.py in one pytest call.
  If you get only isolation-run pass and suite fails: the setdefault bug is back.

**Why:** FY freeze prevents accidental overwrite of audited historical snapshots; dual-layout
parser avoids year-specific branching; gross-actual convention matches how Prayag's accountants
derive the Plumbing tab figures; variance flag surfaces data-entry errors without changing the
authoritative sourcing.
