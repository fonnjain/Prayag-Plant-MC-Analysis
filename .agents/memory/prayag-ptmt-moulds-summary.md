---
name: PTMT annual Moulds Summary
description: PTMT Moulds Summary 26-27 wiring, parser, machine overlap, and ₹/kg basis flag
---

## Source
- File: "ANNUALY PTMT Moulds Summary 26-27"
- File ID: `1kc6AOZJR8b29TBIMprNMxQ85VbAk2BgBU0Iz5u5Se2M`
- Registered in **ANNUAL_SOURCES** (not REPORT_SOURCES) with `kind: "ptmt_moulds_summary"`
- Tab finder: looks for any tab with "SUMMARY" in the name (case-insensitive)

## Parser: parse_ptmt_summary_tab
- Header columns: No. of Run Moulds, Mould Run Hours, Nett Output (KG), Rejection (KG), Rejection %age, Runner Produce (KG), Lumps (KG), 100% Wastage %age, Total Grinder Working, Labour, Paid Wages, Per KG Labour Cost
- Month format in source: "1-Aug-2026" (uses `_parse_date_cell_manpower` fallback after `parse_month_label` fails)
- Returns list of dicts; rows latest-first in sheet (TOTAL at end)

## ₹/kg basis mismatch
- Sheet TOTAL row states ₹3.53/kg; wages÷Nett Output = 2,009,948÷537,109 = ₹3.74
- Mismatch because Jul output (172,639 kg) has zero wages — sheet uses a different denominator
- `per_kg_mismatch` flag fires when |sheet - computed| / max > 2%

## Machine overlap (app vs annual)
- App: 55 machines (31 standard injection + 17 N-line + 3 blow mould + 1 corrugator + 3 grinding)
- Annual: 48 mould/tonnage-tagged machines = exactly the 31 standard + 17 N-line injection machines
- Blow Mould / Corrugator / Grinding = outside annual scope
- DO NOT build a parallel machine dimension without first confirming label mapping (app: "80-1", annual may say "80T-1")

## Verified acceptance targets
- APR: 216 moulds / 16,092h / 99,262 kg / 5.96% / 61 labour / ₹727,748
- MAY: 280 / 15,752 / 104,729 / 6.93% / 49 / ₹632,458
- JUN: 311 / 20,921 / 160,478 / 5.79% / 52 / ₹649,742
- JUL: 298 / 22,318 / 172,639 / 6.07% / labour BLANK → integrity flag fires
- TOTAL: 1,105 / 75,083 / 537,109 / 6.14% / 162 / ₹2,009,948 / sheet ₹/kg=3.53

## Integrity flag
- JUL: output > 0 and wages == 0 → "PTMT July: output without labour"
- `per_kg_cost` is 0 for JUL (never fabricated)

## Nett Output vs Weight of Total Production
- Nett Output 537,109 kg is authoritative (smaller figure)
- Weight of Total Production 541,258 kg is a different, larger basis — surface both, label clearly
