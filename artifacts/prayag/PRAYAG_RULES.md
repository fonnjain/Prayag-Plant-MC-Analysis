# PRAYAG ANALYTICS — CANONICAL RULES

**Version 2** — amended after the Tank annual, Garden rejection and discovery work.
**Status:** authoritative. This document outranks any individual prompt, ticket, or agent conclusion.
**Purpose:** this app has repeatedly regressed after changes that looked correct in isolation. Most of those regressions violated a rule that was already known but lived only in chat history. Everything below is verified against source workbooks or the published code.

**How to use:** every change prompt must state which rules it touches. Any change that violates a rule in Part 1 or Part 3 is rejected regardless of test results. Cite rules by number (e.g. "violates R-07").

---

## PART 1 — NON-NEGOTIABLE INVARIANTS

**R-01 · Recompute; never trust a stored total.**
Every displayed figure is recomputed from source cells. Stored percentages, stored ratios, and roll-up totals are used only to *validate* — never as the data source.

**R-02 · FY2026-27 comes from live monthly data-entry sheets only.**
Annual SUMMARY tabs are computed roll-ups. They may be used for validation deltas and styling reference. They must never be the source for a live FY figure.

**R-03 · Pre-FY2026-27 is finalized.**
FY2025-26 and earlier are closed. History/YoY may read those annual sheets as-is. R-02 does not apply to closed years.

**R-04 · Parse by header text. Never by fixed column position.**
Headers drift. Positions lie. Match on header text, case- and whitespace-insensitively, and slash-tolerantly (`M/C` contains a slash — `"MC" in name` is False for `Month Wise M/C`).

**R-05 · Map months by label, not by order.**
Sheets run latest-first. Never assume index order.

**R-06 · Assert, then fail loudly.**
If an expected header or tab is not found, raise a **named error**. Never return `[]` silently, never fall back to a neighbouring column, never substitute another month or layer.

**R-07 · Never fabricate a number.**
If the source has no basis for a metric, the metric is blank. A blank is a correct answer. A plausible-but-wrong number is the worst possible output — it cannot be detected downstream.

**R-08 · The no-fake-0% invariant is load-bearing.**
`api.py::_metrics_json` nulls a metric when its availability flag is false. Do not weaken it, do not route around it, do not "fix" a blank into a zero. Extend it when a *numerator column* is missing too — a `0.00%` rendered green because the denominator happened to exist is a silent lie (this was the Garden rejection bug).

**R-09 · Units are part of the number.**
Never sum two columns with different units. Never label a kg figure as Ltr. Tank rejection lives in three columns (`REJECTION IN PCS.`, `REJECTION IN KG.`, `REJECTION MOUTH LID IN KG.`) — litres come from `pcs × SIZE (LTR.)`, never from adding the kg columns.

**R-10 · AI writes narrative only, never numbers.**
Every figure in any generated output traces to a recomputed source value.

---

## PART 2 — SOURCE OF TRUTH, PER SEGMENT

Verified against the master pipeline doc ("Preeti - Working Sheet") and the published code.

| Segment | Output | Hours | Rejection | Notes |
|---|---|---|---|---|
| **PIPE** | `MAX(Report-5, Report-11)` per machine-date | Report-5 | same reconcile | R11 supplies pipe *type* + machine-days R5 misses |
| **MOULDING** | Report-12 (`Wt in Kgs`) | Report-5 join (`r5_runhours`) | Report-12 | all three layers agree within 2 kg |
| **PTMT** | Report-5 (machine chain) | Report-5 `IDEAL HOUR` | Report-5 | annual also fed by a **separate mould chain** — see R-24 |
| **GARDEN (KH)** | **OPEN — see R-23** | `Daily Report` matrix | `Daily Report` matrix | block tabs have **no rejection column** |
| **GARDEN_WB (WB)** | `PRODUCTION` tab · long layout · one row per machine×date×item | suppressed (Daily Report all zeros) | `REJECTION IN KG.` col | New FY26-27 plant. Labour: **unjoined** (Segment Cost has KH-only "Garden Pipe" tab — see R-35). Machine prefix `WB GARDEN M/C - `. |
| **HDPE** | Per-machine **block tabs** (`MACHINE 1`–`MACHINE 6`); `Daily Report` per-date triplets supply run-hours join + rejection fallback | `Daily Report` per-date triplets (joined via `runhours_tab`); ideal = 550 h/machine/month from `APP_DEFAULT_IDEAL_HOURS` | Block-tab authoritative; DR per-date fallback where block-tab column present-but-blank (May M/C-1: 120 kg) | Phase 2 (2026-08-15). Jul: 22,448.04 kg / 3,782 kg rej (M/C-2 rej col blank → n/a, not 0%). May: 1,369.20 kg / 120 kg rej / 21 h / 3.82% util. Apr/Jun/Aug: 0. |
| **TANK** (KH/VN/WB) | `PROD. REPORT` (daily) | see R-25 | `pcs × size` | annual cross-check = `SUMMARY (LTR)`, `kind: tank_annual_2526` for **both** FYs — see R-36 |
| **LABOUR** (all) | Segment Cost workbook `1ttlpHLrlTsimcdSmk3-HGnPu14PX7SGtk9Of2Q5pDvw`, **dedicated per-segment tabs** | | | never read labour back from an annual SUMMARY |

**R-11 · Labour lives in one place.** No production workbook contains labour. Segment tabs: `Plumbing`, `TANK`, `Garden Pipe`, `HDPE Pipe`, `HARDWARE SINK PTMT CP`. Unit map: **UNIT 1** = Hardware/Sink/PTMT/CP · **UNIT 2** = Plumbing + Tank · **UNIT 3** = Garden Pipe + HDPE. Never read a combined UNIT row as a single segment's wages — the UNIT-3 label is a concatenation, and its `TOTAL` column is **headcount**, not wages.

**R-12 · `sources.py` is the *pinned* subset, not the whole registry.**
`ensure_daily_discovery()` scans Drive folders at runtime and adds unpinned months in-process (never overwriting a pin). "Not in `sources.py`" does **not** mean "unreadable".

**R-13 · Plant locations.** KH = Kharani (Plant 1 Pipe & Fittings; Plant 2 Tank & Garden Pipe) · Bhiwari Plant 1 = PTMT, CP, Sink, Hardware · VN = Varanasi · WB = West Bengal (Durgapur→Bankura from Feb). *Fixed in v2: `CP` retagged to Bhiwari; the comment's "Khandala / Vasna / Wambori" corrected to Kharani / Varanasi / West Bengal.*

**R-14 · Drive discovery: folder enumeration is primary; title search is the fallback.** *(amended v2; prefix corrected v2)*
`_list_drive_folder` uses `supportsAllDrives=true&includeItemsFromAllDrives=true` and reaches shared drives — it is demonstrably finding files that narrower calls miss, and is the working mechanism. Use title-pattern search as the **fallback** when a file is expected but not found, since files are owned across accounts (preeti@, bhawna@, team-ai@). Tank file titles carry plant prefixes: `(PRV)` = VN, **`(PDWB)`** = WB. Garden WB titles use `(PDWB)` too (e.g. "Garden (PDWB) - June' 2026").

**Discovery persistence gap (confirmed Phase 2):** `ensure_daily_discovery` mutates `DAILY_SOURCES` **in-process only** — each gunicorn worker restarts fresh from `sources.py`. Files found by one worker's scan are invisible to all other workers and lost on restart. Any month that is not pinned in `sources.py` is therefore **ephemerally available at best**. The five Tank months (VN Apr/May, WB Apr/May/Jun) proved this: they sat in scannable folders with parseable titles for months yet never persisted across restarts. **Pin new months in `sources.py` immediately** — do not rely on discovery to carry them.

---

## PART 3 — DO NOT TOUCH

These are correct, deliberate, and have each been "fixed" into a regression at least once.

**R-15 · `pipe_reconcile.py`** — the R5↔R11 date-wise max over the union of machine-dates. Validated against the audited April figure (157,883 kg). Neither source is complete alone.

**R-16 · `baselines.json` being empty is deliberate.** Its own comment: ship only real, business-supplied planned hours; do NOT enter estimates or the sheet's flat 500-h placeholder. Empty means efficiency is correctly suppressed, not broken.

**R-17 · `parse_mc_detail` ignores stored ratio cells and recomputes.** This is R-01 in code. Do not "fix" it to read the sheet's `%age` columns.

**R-18 · `segment_cost` and `utilisation` have `segments: []` intentionally.** They are plant `ALL` and must span every segment. Do not populate their segment lists, and do not change `if rpt["segments"]:` to always-filter. (Reports that name a specific plant *must* declare segments — that was the Moulding/Tank leak.)

**R-19 · `_DAILY_LAYOUTS` in `sheets.py` is the authoritative per-segment tab map.** Read it before reasoning about where a number comes from. Its comments explain each deliberate suppression.

**R-20 · Machine Planning (`mp_*.py`, `machine_planning_*.html`) is out of scope** for any data/segment work.

**R-21 · Never write to Google Sheets.** The app is read-only against source, always.

---

## PART 4 — VERIFIED ACCEPTANCE NUMBERS (Apr–Jul 2026)

Use these as validation targets. If a change moves one of these, it needs an explanation.

| Segment | APR | MAY | JUN | JUL | Total |
|---|---|---|---|---|---|
| **PIPE** (gross) | 190,494 | 344,000 | 178,782 | 564,695 | 1,277,971 kg / 6,507 h |
| **MOULDING** | 89,152 | 75,771 | 97,007 | 104,086 | 366,015 kg / 35,972 h / rej ~1.0% |
| **PTMT** (annual Nett) | 99,262 | 104,729 | 160,478 | 172,639 | 537,109 kg / 75,083 h / 1,105 moulds / rej 6.14% |
| **GARDEN KH** (Daily Report) | 38,950 | 0 | 66,911 | 32,191 | 138,052 kg / 1,553 h / rej 3.81% *(KH only)* |
| **GARDEN KH** (block tabs) | 42,736 | 53,235 | 70,520 | 67,718 | 232,209 kg *(KH only; Jul guardrail was 66,038 in Phase 1. Confirmed 2026-08-14: code path is byte-for-byte identical pre/post Phase 2 — same file ID, same layout. Live re-read of the July workbook gives 67,718.2 kg. **Data change by owner between Phase 1 capture and Phase 2; no code regression.**)* |
| **GARDEN WB** (block PRODUCTION tab) | — | — | 22,152.8 | 6,457.4 | New plant FY26-27; Aug workbook exists but PRODUCTION tab blank at 2026-08-14 read |
| **HDPE** (block tabs) | 0 | 1,369.20 | 0 | 22,448.04 | May: 120 kg rej (8.76% on DR basis 1,370) / 21 h M/C-1 / 3.82% util. Jul: 3,782 kg rej (16.85% on DR basis 22,448); M/C-1 21,931.28 kg / 3,782 kg; M/C-2 516.76 kg / 0 rej (col blank → n/a). Note: May block-tab output 1,369.20 vs DR 1,370.00 (delta 0.80 kg) — R-35, do not reconcile. *(Phase 2: 2026-08-15; prior rows read from `Daily Report` matrix and showed 0 for Apr/Jun/Jul)* |
| **TANK** (annual) | 636,250 | 1,582,500 | 2,596,600 | 1,995,500 | 6,810,850 Ltr / rej 86,500 |
| **GARDEN KH rejection** *(v2)* | 1,191 | 0 (n/a) | 2,215 | 1,853.50 | 5,259.50 kg / **3.81%** on DR basis 138,052 *(KH only)* |
| **TANK daily** *(v2, expanded)* | VN 221,250 · WB 415,000 · KH 636,250 | VN 534,000 · WB 1,048,500 · KH 846,600 | VN 533,500 · WB 1,430,000 · KH 1,419,500 | VN 565,500 · WB 1,702,000 · KH (from annual) | VN 1,854,250 · WB 4,595,500 · KH 3,619,600 (Apr–Jul) |
| **TANK daily rejection** | VN — · WB 6,750 | VN — · WB 27,750 | VN 8,500 · WB 9,750 · KH 45,000 | VN 5,500 · WB 33,500 | WB Jun daily 1,430,000 vs annual 1,429,600 (delta 400 Ltr, R-30: do not adjust) |

**R-22 · PTMT output basis = Nett Output (537,109).** Not "Weight of Total Production" (541,258), not the per-machine block (454,867). Grinding/regrind is finishing throughput and is excluded from headline production.

---

## PART 5 — OPEN QUESTIONS. DO NOT GUESS.

No code may assume an answer to these. Flag and surface; never auto-reconcile.

**R-23 · Garden (KH) output basis is undecided.** The documented management chain is `Daily Report → GARDEN M/C 26-27 → M/C 1-4 → SUMMARY → Annual`. The app instead sums the `MACHINE 1-4` item tabs, which are **not in that chain**. Difference: 232,528 vs 138,052 kg (**KH only** — GARDEN_WB is a separate plant with its own daily files; do not roll WB output into the KH basis debate). Both KH figures are real; only one is management's basis. Until decided, show both and flag divergence >2%.

**R-24 · PTMT annual is fed by two independent chains** — machine (`Report 5`) and mould (`MASTER PTMT Moulding Weight → MASTER → PTMT MOULDS → PTMT Mould Total`). This is why the annual can report more *net* output than Report-5 recorded *gross* (June +3,501 kg, July +11,783 kg). Not an error to fix.

**R-25 · Tank is NOT output-only.** `PLANTS_WITHOUT_RUNHOURS = {TANK, TANK_VN, TANK_WB}` and the "no run hours or machine dimension" tooltip are **factually wrong**. Every Tank workbook has a `PRODUCTION HOURS` column, a `DAILY REPORT` tab and a `MACHINE` row. VN has real hours (Jun 208 vs 184, Jul 224 vs 232 — the two tabs disagree, and the sign flips). KH and WB leave them unfilled. Tooltip wording corrected in v2 to "not currently tracked". `PLANTS_WITHOUT_RUNHOURS` itself is still wrong and still unfixed — no basis has been chosen. VN July verified directly at source: MACHINE-1, 232 h, 14,546.80 kg, full per-date detail.

**R-26 · Tank KH daily is ~2.24× its annual** (June 1,419,500 vs 633,500). **Confirmed in v2 against a working reconciliation** — VN June now matches its annual exactly (533,500 = 533,500, ±0), VN July within 0.4%, WB July +18.9%. The KH gap is therefore real data divergence, not broken wiring. Still unexplained.

**R-27 · Report-11 machine names don't match Report-5 in APR and JUL** (`TTS-88-2`, `KABRA-72-28` vs `M/C-1…9`), so the reconciliation silently degrades to R5-only in half the period. **Amended v2:** aliases were added and the zero/partial-match warning is now implemented — it names unmatched labels with their kg. One label (`1-KABRA-90-22`, 530 kg, May only) remains unmapped by design; aliases are added on evidence, never guessed.

**R-36 · Tank annual cross-check reads `SUMMARY (LTR)`, not `Sheet 1`.** *(new v2)*
Per the documented chain `PROD. REPORT → DATA → Sheet 1 → SUMMARY LTR → Annual`, `Sheet 1` is two steps downstream and derived. Both FY25-26 and FY26-27 use `kind: tank_annual_2526` — the two tabs are structurally identical (reversed months, `Production (in Ltr)` / `Rejection (in Ltr)` sub-header, item name always in col 1). This is deliberate, not a mislabel.

**R-37 · The Tank `SUMMARY (LTR)` tab contains TWO pivot sections.** *(new v2)*
A product-type breakdown, then a size breakdown, separated by a blank row and a **duplicate TOTAL**. Parsing must stop at the second TOTAL once data has been emitted. Without that guard every plant-month is counted twice — a silent doubling that produces a plausible figure nobody questions.

**R-38 · MOULDING has no `DAILY_SOURCES` entry of its own.** *(new v2)*
`_DAILY_LAYOUTS["PIPE"]` holds **two specs against one physical workbook**: `Report-5` → PIPE, `Report-12` → MOULDING, because Injection Moulding runs at KH Plant 1 alongside pipe extrusion. Moulding records are reachable only by loading the `PIPE` key and filtering `r.plant == "MOULDING"`. A naive per-segment probe on `"MOULDING"` returns `[]` and will wrongly appear empty. `rollup_by_plant` separates them correctly, so no double-count occurs.

---

## PART 6 — KNOWN FAILURE MODES

Every one of these has happened. Check against this list before shipping.

1. **Silent column fallback** — the recurring enemy. Bank account read as wages, pieces as kg, headcount as wages, kg as litres, mouth-lid kg added to tank kg.
2. **Annual-vs-daily layer confusion** — reading a derived roll-up as source, or comparing figures from two different layers as if they were the same basis.
3. **Tab-name string matching** — `"MC" in "Month Wise M/C"` is False; `"Moulding M/C 26-27"` matches before `"PTMT Mould…"` without a prefix guard.
4. **Date-format variance within one column** — `Jul 2, 2026` and `3-Jul-2026` coexist; PTMT uses `1-Aug-2026`.
5. **Silent row drops** — undated rows are skipped (Garden July: exactly 2,352 kg); colour variants collapse on group-by (125 sheet rows → 111 records).
6. **Empty `segments: []` skipping the filter** — leaked 10M litres of Tank into the Moulding report.
7. **False "implemented"** — a task marked done with the root bug untouched (commit `6e057ef`). Verify, don't trust.
8. **Code-inspection green ≠ runtime green.** A static review declared all segments correct; the live run found PTMT reading the annual, Tank at 0 Ltr, and Garden labour broken.
9. **Transient Drive 404s** — a file can 404 once and read fine minutes later. Do not conclude "deleted" from a single failure. *Resolved v2:* vanished-source detection now requires **3 consecutive informative misses** plus a **direct-read probe**, and classifies a failed or empty scan as *no information* rather than absence — a scan that errors never moves the counter.
10. **Cache TTL is 900 s.** Past that, `pg_cache_read` returns `None`; stale rows are inert, not live. `pg_cache_write` is an **upsert** — it destroys what it overwrites.
11. **Duplicate pivot sections in one tab** — the same figures appear twice under a repeated TOTAL (see R-37). Parsing past the second TOTAL doubles everything silently.
12. **Tests that mirror production code** — `test_garden_rejection.py` reimplemented `_emit_blocks` rather than calling it, so it validated a copy. A green test against a duplicate proves nothing about the shipped path.
13. **Two repositories.** `prayag-analytics` is stale at `edf3348`; all live work is in `Prayag-Plant-MC-Analysis`. Publishing from the wrong tree is a live risk until the stale one is archived.
14. **A rejection column that is present-but-blank is not the same as genuinely zero rejection.** `rejection_tracked = (rej_c >= 0)` was the old guard — it rendered a green `0.00%` whenever a rejection header was found, even if every data cell was empty. Fixed: `rejection_tracked = (rej_c >= 0 and any_rej_nonzero)`. A column that exists but carries no non-zero values produces `rejection_tracked=False` → n/a, not green zero. DR per-date join then supplies the fallback value where available (HDPE May M/C-1: 120 kg from Daily Report May,1 triplet). *(confirmed 2026-08-15)*

---

## PART 7 — PROCESS RULES FOR ANY CHANGE

**R-28 · Diagnose read-only before fixing.** Print the current state and the root cause. Stop. Only then write the fix.

**R-29 · Verify at runtime, not by reading code.** A fix is not done until a live run prints the recomputed number *and* the source-layer flag side by side.

**R-30 · Validate against a target before wiring.** If the recomputed figure doesn't reconcile to the acceptance number, stop and report — do not wire it and do not bend the parser to hit the number.

**R-31 · Never mark a task implemented unless its validation step actually passed.** A wrongly-closed task is worse than an open one.

**R-32 · Scope tightly. One concern per change.** State explicitly what must not be touched. Broad rewrites of `parsers.py` are how working segments break.

**R-33 · Stop-and-report gates between phases.** Multi-part changes report after each part.

**R-34 · Preserve evidence.** Do not clear caches, delete rows, or overwrite artifacts that are the only record of a problem under investigation.

**R-35 · Source problems get flagged, not coded around.** Missing data, disagreeing layers, and unfilled tabs are for the data owners. The app's job is to surface them clearly — never to silently reconcile, average, or paper over them.

---

## PART 8 — DATA OWNERS

| Area | Owner |
|---|---|
| All annual summaries, pipeline | Preeti |
| Pipe machines (Report 5, M/C 1–9) | Nikhil |
| Moulding machines (Report 5, C34:C58) | Jitendra Yadav |
| Pipe moulds (Reports 17–20) | Ms Pooja |
| Compound (Reports 6–10) | Mr Shelendra |
| PTMT | Alok Roy |
| Garden Pipe / HDPE daily | Anuj |

---

*Amend this document when a rule is proven wrong — with the evidence. Do not amend it to accommodate a change that is convenient.*
