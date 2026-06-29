# Build Spec: Management Reports page (separate page)

**Goal:** A separate `/reports` ("Management Reports") page that reproduces the 14 annual
reports management uses (Meetings Col E), for both fiscal years, **recomputed from daily
production data**, styled to match the existing Final Output Sheets. The live plant dashboard
is unchanged; this is a distinct section.

**Agreed approach (confirmed):**
1. **RECOMPUTE** annual numbers from daily data. Use the Final Output Sheets only as the
   **styling reference** (column order, labels, grouping) and as a **validation** target.
2. The manual transform-chain sheets are documented separately (Transform Chain file) for
   later automation — v1 recomputes from daily directly and does NOT depend on them.
3. Achievability: production / utilisation / efficiency / **payroll** labour cost recompute
   from data on hand. **Power + solar + contractor** figures need manual monthly inputs
   (Manual Inputs file) — those columns stay "awaiting input" until entered.

---

## Supporting files to give Replit (all attached)
- `Prayag_Annual_Reports_Sheet_Inventory.xlsx` — the 14 reports × 2 FYs with live Google
  Sheet file IDs (styling + validation targets), plus the employee-cost source IDs.
- `Prayag_Annual_Reports_Transform_Chain.xlsx` — raw→tab→intermediate→final lineage per
  report (reference for v2 automation; explains what each final sheet is built from).
- `Prayag_Segment_Report_Manual_Inputs.xlsx` — the manual monthly inputs the Segment
  Labour/Solar/Power report needs (power, solar, contractor), with a blank capture template.

---

## 1. Report groups (recompute logic per report)

### Group A — fully recompute from daily data (build first)
| Report | Recompute source | Unit |
|---|---|---|
| (A) Pipe M/C Summary | Pipe daily Report-5 (rows 5-13) | kg |
| (B) Moulding M/C Summary | Pipe daily Report-5 (rows 34-60) | kg |
| (C) Group of Moulding | Report-5 moulding, grouped by tonnage band 150/200/250/275/350/450 | kg |
| (D) Pipe Moulds Summary | Pipe daily Report-17/18 (CPVC/UPVC/SWR/AGRI moulds) | kg |
| Garden Pipe M/C Summary | Garden daily Daily Report | kg |
| HDPE M/C Summary | HDPE daily Daily Report | kg |
| Moulding %age Efficiency | efficiency rollup of moulding summary | % |
| PTMT Moulds Summary | PTMT daily Report-5 (moulding) | kg |
| PTMT Moulding %age Efficiency | PTMT daily Report-9 + mould total | % |
| Compound Compilation | Pipe daily Report-6/7/8 (compound) | kg |
| TANK (KH/VN/WB) Ltr. Summary | Tank daily PROD. REPORT | Ltr |

Each computes the same columns the final sheet shows: Run Hours, Output±Rejection,
Ideal Output, Avg/hr, M/C Utilization %, Output Efficiency %, per machine + per month
(Apr→Mar), with the plant's headline unit (kg; Tank = Ltr). Use the baseline-resolution
(ideal hours/output per machine) from the Ideal Hours Input layer.

### Group B — partial: Segment Labour / Solar / Power Cost
Structure UNIT-1 (CP/PTMT/Hardware/Sink) / UNIT-2 (Plumbing+Tank) / UNIT-3 (Garden+HDPE).
- **Production (kg) per segment** → recompute from daily. ✓
- **Payroll labour** (persons, wages, per-kg labour cost) → from Employee Cost sheets
  (per-person cost/day, cost/hour, dept/sub-dept → segment) joined to paid hours. ✓
- **Power cost, solar, contractor labour** → require the MANUAL MONTHLY INPUTS
  (see Manual Inputs file). Until entered for a month, show those columns as
  "awaiting input" (not 0, not error). Per-kg power cost computes once inputs exist.

---

## 2. Page design (minimalist, existing brand)

- **Index**: grouped Location → Plant → Report (mirror the folder hierarchy: KH 26-27,
  PTMT 26-27, KH 25-26). All 14 reachable in two taps. FY selector (26-27 / 25-26).
- **Each report page**: title · FY + period selector (daily/weekly/monthly/quarterly/FY) ·
  the management-styled table (presentation profile from the final sheet) · one chart ·
  AI commentary (narrate from computed numbers only) · PDF export.
- **Validation badge** per report: recompute vs the Final Output Sheet → "matches source"
  or "variance: N" (advisory; never blocks). This is how we keep "exactly as management does".
- Two FY layout profiles where 25-26 and 26-27 differ (Tank, Segment, Group-Moulding columns
  changed between years — read headers from each FY's final sheet).
- Brand navy #1F3864 + terracotta #C55A11, DD-MM-YYYY, sentence case, no emoji, mobile-first.

---

## 3. Replit prompt (paste-ready)

```
Add a SEPARATE page /reports ("Management Reports") that reproduces the 14
annual management reports (Meetings Col E) for both fiscal years 26-27 and
25-26. RECOMPUTE all numbers from daily production data; use the Final Output
Sheets only as the styling reference and as a validation target. Do not depend
on the manual transform-chain sheets. The live plant dashboard is unchanged.

I am providing three files:
- Sheet Inventory: the 14 reports x 2 FYs with native Google Sheet file IDs
  (styling + validation targets) and the employee-cost source IDs.
- Transform Chain: raw->tab->intermediate->final lineage per report (reference).
- Segment Report Manual Inputs: the monthly power/solar/contractor inputs the
  Segment report needs, with a blank capture template.

GROUP A - fully recompute from daily (build first):
  (A) Pipe, (B) Moulding, (C) Group-of-Moulding (tonnage bands 150/200/250/275/
  350/450), (D) Pipe Moulds, Garden, HDPE, Moulding %Eff, PTMT Moulds,
  PTMT %Eff, Compound, TANK (KH/VN/WB) Ltr.
  - Compute the same columns each final sheet shows (Run Hours, Output±Rejection,
    Ideal Output, Avg/hr, M/C Utilization %, Output Efficiency %, per machine +
    per month Apr->Mar).
  - Units per plant: kg for all except TANK = Ltr. Never mix units.
  - Use the ideal-hours / ideal-output baselines from the Ideal Hours Input
    layer (PTMT 572 flat; Pipe/Moulding 22 or 12 hr/day x run days; Garden/Tank
    500; HDPE 550; ideal output/hr per machine from Report-5 Col I).
  - Recompute ratios from raw cells; never read stored % cells.

GROUP B - Segment Labour/Solar/Power (partial):
  - Units UNIT-1 (CP/PTMT/Hardware/Sink), UNIT-2 (Plumbing+Tank), UNIT-3
    (Garden+HDPE). Mirror that structure.
  - Production (kg)/segment: recompute from daily.
  - Payroll labour: from the Employee Cost sheets (per-person cost/day, cost/hr,
    dept/sub-dept -> segment) joined to paid hours. Per-kg labour cost computes.
  - Power cost, solar, contractor labour: come ONLY from the manual monthly
    inputs (see Manual Inputs file). Add a small monthly-input surface for these
    (same pattern as the Ideal Hours Input page). Until a month's inputs are
    entered, show power/solar/contractor columns as "awaiting input" - NOT 0,
    NOT error. Per-kg power cost computes once inputs exist.

PAGE:
  - Index grouped Location -> Plant -> Report (folders KH 26-27, PTMT 26-27,
    KH 25-26). FY selector 26-27 / 25-26.
  - Each report: title, FY + period selector (daily/weekly/monthly/quarterly/FY),
    the management-styled table (column order/labels/grouping read from that
    report's Final Output Sheet so it matches what management reads), one chart,
    AI commentary (narrate computed numbers only), PDF export.
  - Validation badge per report: recompute vs Final Output Sheet -> "matches
    source" or "variance: N" (advisory, never blocks).
  - Two FY layout profiles where 25-26 and 26-27 columns differ (Tank, Segment,
    Group-Moulding) - read headers from each FY's final sheet.

CONSTRAINTS (unchanged invariants):
  - Wire native Google Sheet IDs from the inventory, never the .xlsx duplicates.
  - Daily-first; recompute from raw cells; Claude narrates, never computes.
  - Validate 26-27 recompute against the 25-26 final sheets (26-27 finals are
    near-empty this early in the FY).
  - Brand navy #1F3864 + terracotta #C55A11, DD-MM-YYYY, sentence case, no emoji,
    mobile-first. Link /reports in the main nav.

Build Group A first (all recompute, no manual inputs), then Group B with the
monthly-input surface. Report which reports validate clean against the 25-26
final sheets, and confirm the (B) Moulding 26-27 final-sheet ID.
```

---

## 4. Open item
- **(B) Moulding 26-27 final-sheet ID** unconfirmed (lineage points into the Pipe workbook).
  25-26 ID is known. Confirm before wiring (B)'s validation target for 26-27.
