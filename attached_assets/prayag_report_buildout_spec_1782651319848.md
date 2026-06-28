# Prayag Analytics — Build Spec: Capture Management's Reports

**Goal:** Bring every report management actually uses (Meetings sheet, Col E — 14 reports)
into the existing Replit webapp, live across daily/weekly/monthly/quarterly, styled exactly
the way management already reads them. Add only what's missing; reuse what's built.

Source of truth for scope: `Preeti - Working Sheet`
(`1covDNYG2QXAUetDYNy2ihtOFWARCVPMqSGOaOaPZZDY`) — `Meetings` Col E (required reports),
`SUMMARY` (plant hierarchy), and the per-plant lineage tabs (how each report is sourced).

---

## 1. Gap analysis — required vs. already built

The app already has 8 report types. Of management's 14 required reports, **8 are covered,
2 are partial, 4 are new.** Do NOT rebuild the covered ones.

| Management report (Col E) | Status | Action |
|---|---|---|
| (A) Pipe M/C Summary | covered (extrusion_summary) | restyle only |
| (B) Moulding M/C Summary | covered (injection/mould_summary) | restyle only |
| (C) **Group of** Moulding M/C Summary | **NEW** | new tonnage-band rollup grain |
| (D) Pipe **Moulds** Summary | partial (mould_summary) | confirm Pipe-mould scope + restyle |
| Garden Pipe M/C Summary | covered | restyle only |
| HDPE M/C Summary | covered | restyle only |
| Moulding %age in Efficiency | covered (mould_efficiency) | restyle only |
| Compound Compilation 26-27 | covered (compound_summary) | restyle only |
| Segment-wise Labour, Solar & Power Cost | covered (segment_cost) | restyle; reaches 25-26 |
| TANK **(KH)** Ltr. Summary | partial (tank_summary) | scope to KH location |
| TANK **(VN)** Ltr. Summary | **NEW** | new plant location (Varanasi) |
| TANK **(WB)** Ltr. Summary | **NEW** | new plant location (West Bengal) |
| PTMT Moulds Summary 26-27 | covered (injection_summary) | restyle only |
| PTMT Moulding %age in Efficiency | covered (mould_efficiency / PTMT) | restyle only |

### The scope-changing finding — extra plant LOCATIONS
`SUMMARY` lists five locations, not the current set:
- KH — Kharani Plant 1 (Pipe & Fittings), Kharani Plant 2 (Tank & Garden)
- Bhiwari Plant 1 (PTMT, CP, Sink, Hardware)
- **VN — Varanasi (TANK)**  ← not wired
- **WB — West Bengal / Durgapur (TANK)**  ← not wired

So the three TANK reports (KH/VN/WB) are **three separate producing sites**. VN and WB tank
workbooks are not in `sources.py` today. Capturing management's reports therefore means
adding two plant locations, not just report tabs.

### New grains/capabilities introduced
1. **Group-of-Moulding tonnage rollup (C):** aggregate moulding machines by tonnage band
   (150 / 250 / 350 / 450 …), not by machine. New rollup key in `metrics.py`
   (`rollup_by_tonnage_group`), sourced from the "Then Tunnage Wise" + "150 to 450" lineage.
2. **Multi-location tank summary (KH/VN/WB):** `tank_summary` gains a location dimension;
   three report instances or one report with a location filter.
3. **Cross-FY reach:** several required reports are 25-26 (TANK, Segment Labour/Solar/Power).
   The FY selector must address **2025-26** as well as 2026-27. Wire the 25-26 annual
   workbooks as read-only historical sources.

---

## 2. Data sources to wire (sources.py → DAILY_SOURCES / ANNUAL_SOURCES)

The lineage tabs in the working sheet name the exact source workbooks per report. All five
tank-summary IDs below are **resolved from Preeti's Drive** and ready to wire. (drive.file
scope — every workbook is added explicitly; no auto-discovery.)

### 2a. Tank summary workbooks — RESOLVED IDs (wire these)

| Report | FY | Google Sheet File ID | Parent folder | Headline? |
|---|---|---|---|---|
| TANK (KH) Ltr. Summary | 25-26 | `1_6Foa8TXXP-xr0KIx04q7i8iigkrLjuT8r7uG62x8qQ` | `1DQu6Gabx_YcxhD1gui7b1f9B11AkKULv` | history |
| TANK (VN) Ltr. Summary | 25-26 | `1fe2ZgL8EcuUVkvjC3-mZ5Pr8WkXWQ5V70AiwkbDUh-0` | `1DQu6Gabx_YcxhD1gui7b1f9B11AkKULv` | history |
| TANK (WB) Ltr. Summary | 25-26 | `1mtgkCbNsWsSrgjJfN2zc7SDb2ysHoH11xG3afr0oovc` | `1DQu6Gabx_YcxhD1gui7b1f9B11AkKULv` | history |
| TANK (VN) Ltr. Summary | 26-27 | `1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag` | `124bS0JH4FmO5Iwr3IbWeoyhu9d3VEdWX` | **headline (VN)** |
| TANK (WB) Ltr. Summary | 26-27 | `1W6hGoEZauSkQyBUQbngnHNMD7Koon3_c8tnO0PDHrt8` | `124bS0JH4FmO5Iwr3IbWeoyhu9d3VEdWX` | **headline (WB)** |

### 2b. Tank sourcing rule — KH stays daily; VN/WB run on the annual sheet

- **KH (26-27): do NOT add an annual file. Keep the existing daily Kharani tank workbook as
  the headline source.** Daily-first is the architecture rule; KH already has daily grain,
  which is the better source. A 26-27 KH annual (if one exists) would be a redundant
  cross-check and would create a headline-grain mismatch against the daily KH data — so it is
  deliberately NOT wired. We did not search for it; this is intentional, not an omission.
- **VN + WB (26-27): wire the annual sheet as the headline source**, flagged
  `grain = summary-only, no daily detail`. These sites file no daily workbooks, so the annual
  sheet is the only source — same pattern as MOULDING (monthly-grid-only) in the current app.
- **All three (25-26): wire as read-only history** (cross-check trust level).

### 2c. Two tank-summary layout profiles (the FYs differ)
- **25-26 files** = old wide "DATA" format: month columns MAR-26…APR-25, repeating
  `ITEM CODE / COLOR / PRODUCTION IN PCS / PRODUCTION IN KG / REJECTION …` blocks.
- **26-27 files** = cleaner "Sheet1" format: `S.NO., CODE, LTR., DESCRIPTION, COLOUR,
  TOTAL PCS, Production (Pcs), Production (Ltr), Rejection (Pcs), Rejection (Ltr)` repeated
  per month APRIL'26…MAR'27.
- Parser needs **both** profiles; current-year report styling follows the 26-27 headers.
- **Validate the parser on the populated 25-26 files first** (real numbers, same sites), then
  point it at 26-27. The 26-27 VN/WB sheets are **all zeros today** (expected this early in
  the FY) — testing the reader against an empty target won't confirm it works.

### 2d. Other new sources (IDs still to resolve)
- **Group-of-Moulding** ("(C). Annual 26-27 Group Of Moulding M/C Summary" lineage:
  CUSTOM-01 → "150 to 450" → SUMMARY) — wire the grid, add tonnage-band grouping.
- **(D) Pipe Moulds** ("(D). Annual 26-27 Pipe Moulds Summary" lineage: Report-17 CPVC →
  C RAW-1) — confirm the mould-level source feeding mould_summary covers Pipe moulds.
- **25-26 Segment-wise Labour/Solar/Power** annual workbook (for that historical report).

Add a `location` field (KH / Bhiwari / VN / WB) to the Record dataclass and to
`PLANTS`/`SUMMARY` config so the Overview can group by location.

---

## 3. Report styling — match management's existing format EXACTLY

Management reads these as fixed-format annual summary sheets. The webapp report pages must
reproduce the **same column order, same headers, same grouping, same units** as the Col E
sheets — so a manager sees the report they already know, not a redesigned table.

Rules:
- Pull the **header row and column order** for each report directly from its FINAL OUTPUT
  sheet in the working/annual workbook; use those labels verbatim (e.g. exact wording,
  exact unit suffixes "(kg)", "Ltr.", "%age").
- Preserve **section/group breaks** management uses (e.g. PTMT's 5 process groups; tonnage
  bands for Group-of-Moulding; capacity×layer for Tank).
- Keep the **Annual (FY) column layout** (Apr…Mar month columns where the source has them)
  for the annual reports; the live period selector slices within that.
- Numbers are still computed deterministically in `metrics.py` (never trust stored % cells,
  per existing invariant) — only the **presentation** mirrors the management sheet.
- Each report keeps its existing PDF export; the PDF must match the on-screen (and thus
  management-familiar) layout.

> Implementation: add a per-report "presentation profile" (ordered column list + labels +
> group key) read from a small config, so styling matches the source sheet without hardcoding
> in templates. One profile per Col E report.

---

## 4. Live cadence — daily / weekly / monthly / quarterly

Reuse the existing period engine (`parse_period`). The reports are annual in the source, but
the app already supports sub-FY slicing. Required additions:
- Ensure **quarterly** is a first-class period (Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec,
  Q4 Jan–Mar). If not already discrete, add it alongside weekly/monthly/FY.
- Each Col E report must honour the period selector (daily where daily data exists; monthly/
  quarterly/FY via daily-first summation, grid as cross-check — existing data-flow rule).
- Annual-only / historical (25-26) reports: when a sub-period has no daily grain, fall back
  to the grid for that period and label trust level "reconciliation reference," per the
  existing monthly-grid rule.

---

## 5. Minimalist design direction

Keep the current brand (Navy `#1F3864`, Terracotta `#C55A11`, DD-MM-YYYY, mobile-first
max-width 2xl). Tighten, don't redecorate:
- One clean reports index grouped by the SUMMARY hierarchy (Location → Plant → Report),
  so all 14 are reachable in two taps.
- Each report page = title, period selector, the management-styled table, one bar chart,
  AI commentary, PDF button. No new chrome.
- Flat tables, generous whitespace, no card-in-card nesting, no decorative gradients.
- Reuse existing components (KPI cards, period selector, flags banner) verbatim.

---

## 6. Phased Replit prompt (paste-ready)

```
We are extending the Prayag Analytics app to cover ALL reports management uses
(Preeti Working Sheet -> Meetings tab, Col E = 14 required reports). Do this in
phases. Reuse existing modules (sources.py, parsers.py, metrics.py, confirm.py,
templates). Do NOT rebuild reports that already exist.

PHASE 1 — Scope & restyle (no new data):
- For the 8 already-built reports that map to Col E, add a per-report
  "presentation profile": ordered column list + exact header labels + group
  key, read from the matching FINAL OUTPUT sheet in the annual workbook. Render
  report tables and PDFs using these profiles so they match the format
  management already reads (same columns, order, units, section breaks).
  Keep all figures computed in metrics.py; never trust stored % cells.

PHASE 2 — New report grains (existing data):
- Add report (C) Group-of-Moulding Summary: new rollup_by_tonnage_group in
  metrics.py (group moulding machines by tonnage band 150/250/350/450), sourced
  from the "150 to 450" / tonnage lineage. New report page + profile.
- Confirm report (D) Pipe Moulds Summary covers Pipe moulds in mould_summary;
  add a Pipe-mould scoped profile if needed.

PHASE 3 — New plant LOCATIONS (new data, IDs already resolved):
- Add a `location` field (KH / Bhiwari / VN / WB) to the Record dataclass and
  plant config, sourced from the SUMMARY hierarchy.
- Wire these tank-summary workbooks into sources.py (IDs below):
    KH 25-26  1_6Foa8TXXP-xr0KIx04q7i8iigkrLjuT8r7uG62x8qQ   (history)
    VN 25-26  1fe2ZgL8EcuUVkvjC3-mZ5Pr8WkXWQ5V70AiwkbDUh-0   (history)
    WB 25-26  1mtgkCbNsWsSrgjJfN2zc7SDb2ysHoH11xG3afr0oovc   (history)
    VN 26-27  1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag   (headline)
    WB 26-27  1W6hGoEZauSkQyBUQbngnHNMD7Koon3_c8tnO0PDHrt8   (headline)
- KH 26-27: DO NOT add an annual file. Keep the existing daily Kharani tank
  workbook as KH's headline source (daily-first). VN/WB have no daily files,
  so their annual sheet IS the headline — flag them grain=summary-only,
  no daily detail, no OEE/utilisation (same as MOULDING monthly-grid-only).
- Two tank-summary parser profiles: 25-26 wide "DATA" layout vs 26-27 "Sheet1"
  layout (S.NO./CODE/LTR./DESCRIPTION/COLOUR/TOTAL PCS/Production/Rejection).
  Build and validate the parser against the POPULATED 25-26 files first; the
  26-27 VN/WB sheets are all-zero today and won't confirm the reader works.
- Make tank_summary location-aware: TANK (KH), TANK (VN), TANK (WB) as three
  instances (or one report with a location filter). VN/WB must render a clean
  "summary-level only — no machine/daily detail" state, not empty machine tables.

PHASE 4 — Cross-FY + cadence:
- Wire the 2025-26 annual workbooks (Segment Labour/Solar/Power, TANK KH/VN/WB)
  as read-only historical sources; let the FY selector address 2025-26.
- Ensure quarterly (Q1 Apr-Jun ... Q4 Jan-Mar) is a first-class period
  alongside daily/weekly/monthly/FY, and every Col E report honours it.

PHASE 5 — Index & polish:
- Rebuild /reports as a minimal index grouped Location -> Plant -> Report
  (all 14 reachable in two taps). Keep brand + mobile-first; no new chrome.
- Each report: title, period selector, management-styled table, one bar chart,
  AI commentary, PDF. Reuse existing components.

Constraints (unchanged architecture invariants):
- drive.file scope: every workbook added explicitly to sources.py, no folder
  auto-discovery.
- Daily-first: daily tabs are headline; monthly grid is cross-check only.
- baselines.json stays empty unless real factory ideal-hours are supplied;
  utilisation stays suppressed where no baseline (PIPE/GARDEN/MOULDING).
- Claude narrates from computed numbers only; never computes figures.
- Tier 1-4 confirmation + sign-off gate apply to the new reports too.

Deliver phase by phase; after each phase tell me exactly which Drive file IDs
you still need from me.
```

---

## 7. What I still need from you to finish wiring

Tank IDs are **resolved** (Section 2a) — nothing needed there. Remaining:

- Drive file IDs for the **Group-of-Moulding** ("150 to 450") source and the **2025-26
  Segment Labour/Solar/Power** annual workbook (if you want that historical report live).
- Confirmation of the **tonnage bands** for the Group-of-Moulding report (e.g. exactly which
  bands: 150 / 250 / 350 / 450, or finer).
- Whether **CP Fittings** (Bhiwari) should be activated now or stay pending.
- Confirm it's acceptable that **VN and WB show summary-level only** (no machine drill-down,
  no daily/last-7-days slicing) — this is a source-data limitation, fixable only if those
  sites begin filing daily workbooks.
