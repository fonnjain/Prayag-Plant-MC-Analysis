# PRAYAG ANALYTICS — CANONICAL RULES

**Version 3.** Supersedes v2. Amended after the Garden, HDPE, Tank R-39 and costing work.
**Status:** authoritative. This document outranks any individual prompt, ticket, or agent conclusion.

**How to use:** every change prompt must state which rules it touches. Any change that violates the Cardinal Rule, Part 1, or Part 3 is rejected regardless of test results. Cite rules by number (e.g. "violates R-07").

---

# THE CARDINAL RULE

**Every figure in every report is computed by the application from live source cells. No figure is ever taken from an annual summary, from a previously generated report, or from a parallel report maintained in Google Sheets.**

Those sources exist for **verification only**. They may raise a question; they may never settle one.

**Layout is not data.** A report may match its Excel counterpart exactly — same columns, same order, same headers, same TOTAL row — while every number in it comes from the app's own recomputation. Where the two differ, **show ours and flag the difference**. Never copy a cell across to make them agree.

**Parallel running.** Prayag will continue maintaining these reports in Google Sheets until the app is fully verified and trusted. Throughout that period those sheets are **verification material and mismatch-detection, not input**. Either side may be wrong — a mismatch means *investigate*, never *adjust*.

**One narrow exception — R-03.** FY2025-26 and earlier are closed. Those annuals may be read as-is for history and year-on-year, because no live source remains to recompute from. This does not dilute the rule for FY2026-27.

**Corollary.** A conclusion drawn from an annual inherits whatever gap fed that annual.

## THIS RULE IS PROTECTED

The Cardinal Rule and the Part 1 invariants (R-01 to R-10) may not be modified, weakened,
suspended or worked around by any prompt, ticket, or instruction.

If an instruction asks for that — directly, or by implication — STOP before making any change.
Do not begin. Reply with:
  (a) the rule that would be broken, quoted;
  (b) the exact words in the instruction that conflict with it;
  (c) what the change would cause — which figures would stop being recomputed.
Then wait. Proceed only on a reply that names the rule and states explicitly that it is to be
set aside for this task.

Instructions that LOOK like permission but are NOT:
  "take the data from the attached sheet"     -> means verify against it
  "make it match the workbook"                -> means match the LAYOUT
  "the figures should match the annual"      -> means investigate the difference
  "just use the number from the summary"      -> stop and ask
  "copy the format from the source"           -> layout only, never figures

LAYOUT IS NOT DATA. Matching a source workbook's columns, headings, tab structure and row
order is always permitted and usually required. Taking a FIGURE from that workbook never is.
If an instruction is ambiguous between the two, assume LAYOUT and say so in your reply.

A reviewer naming a source sheet is telling you WHERE TO CHECK, not where to read from.

### The evidence this rule is built on

| Case | The annual said | The live source held |
|---|---|---|
| **Garden July** | 32,191 kg | ~68,390 kg across four machines |
| **HDPE July** | idle, zero | **22,448 kg** in the MACHINE tabs |
| **Fittings ideal labour** | ₹27.6 M ideal, actual 79% *below* ideal | built on a **pieces** count × a per-kg rate — inflated ~15× |
| **Garden wages** | ₹4,26,164 (Garden annual) | ₹2,20,797 (Segment Cost workbook) — **two annuals disagree with each other** |

Each was believed at some point because an annual said so. Two of them were stated as fact in this project's own documents before being corrected.

---

## PART 1 — THE MECHANICS OF THE CARDINAL RULE

**R-01 · Recompute; never trust a stored total.** Stored percentages, stored ratios and roll-up totals are used only to validate.

**R-02 · FY2026-27 comes from live monthly data-entry sheets only.** Annual SUMMARY tabs are computed roll-ups — validation and styling reference only, never the source of a live figure.

**R-03 · Pre-FY2026-27 is finalized.** FY2025-26 and earlier are closed; their annuals may be read as-is for history/YoY.

**R-04 · Parse by header text. Never by fixed column position.** Headers drift; positions lie. Match case-, whitespace- and slash-tolerantly (`M/C` contains a slash — `"MC" in name` is False for `Month Wise M/C`). The same field may be named differently in two tabs of one workbook (`PRODUCTION HOURS` / `RUN HOURS`), and a label column may not be column 0.

**R-05 · Map months by label, not by order.** Sheets run latest-first — and not consistently: the Garden annual runs MAR'27→APR'26 while the Segment Cost workbook runs APR'26→MAR'27.

**R-06 · Assert, then fail loudly.** Raise a **named error**. Never return `[]` silently, never fall back to a neighbouring column, never substitute another month or layer.

**R-07 · Never fabricate a number.** If the source has no basis for a metric, the metric is blank. A blank is a correct answer; a plausible-but-wrong number is the worst possible output.

**R-08 · The no-fake-0% invariant is load-bearing.** `api.py::_metrics_json` nulls a metric when its availability flag is false. A rejection column that **exists but is empty for the whole month** does not count as tracked — present-but-empty is not zero.

**R-09 · Units are part of the number.** Never label a figure in one unit as another. *Corrected in v3:* summing Tank's two kilogram columns (`REJECTION IN KG` + `REJECTION MOUTH LID IN KG`) **is legitimate** — the plant's own `DAILY REPORT` does exactly that (446.40 ≈ 449.3). The original defect was labelling that kilogram sum as **litres**, not the sum itself.

**R-10 · AI writes narrative only, never numbers.**

---

## PART 2 — SOURCE OF TRUTH, PER SEGMENT

| Segment | Output | Hours | Rejection | Notes |
|---|---|---|---|---|
| **PIPE** | `MAX(Report-5, Report-11)` per machine-date | Report-5 | same reconcile | R-39; R11 supplies pipe type |
| **MOULDING** | Report-12 (`Wt in Kgs`) | Report-5 join | Report-12 | all layers agree within 2 kg; reachable only via the `PIPE` key (**R-38**) |
| **PTMT** | Report-5 | Report-5 `IDEAL HOUR` | Report-5 | annual also fed by a separate mould chain (**R-24**) |
| **GARDEN (KH)** | `MACHINE n` block tabs | `Daily Report` matrix | `Daily Report` + synthetic records for rejection-only dates | output basis open (**R-23**) |
| **GARDEN_WB** | `PRODUCTION` tab, long layout | none recorded | same tab | separate plant; no labour source exists |
| **HDPE** | `MACHINE 1–6` block tabs | `Daily Report` | block tabs, `Daily Report` fallback | Daily Report carries ideal rates only |
| **TANK** (KH/VN/WB) | `PROD. REPORT` | **R-39 union** of `DAILY REPORT` + `PROD. REPORT` | `pcs × size` (Ltr) **and** kg columns | both rejection bases reported |
| **LABOUR** | Segment Cost workbook, dedicated per-segment tabs | | | never read labour from an annual SUMMARY |

**R-11 · Labour lives in one place.** Segment tabs: `Plumbing`, `TANK`, `Garden Pipe`, `HDPE Pipe`, `HARDWARE SINK PTMT CP`. Units: **1** = Hardware/Sink/PTMT/CP · **2** = Plumbing + Tank · **3** = Garden Pipe + HDPE. Never read a combined UNIT row as one segment's wages — UNIT-3's `TOTAL` column is **headcount**, not wages.

**R-12 · `sources.py` is the *pinned* subset, not the whole registry.** `ensure_daily_discovery()` scans Drive and adds unpinned months **in-process only** — nothing survives a worker restart. New months must be pinned by hand; waiting for discovery to carry them will not work.

**R-13 · Plant locations.** KH = Kharani (Plant 1 Pipe & Fittings; Plant 2 Tank & Garden Pipe) · Bhiwari Plant 1 = PTMT, CP, Sink, Hardware · VN = Varanasi · WB = West Bengal.

**R-14 · Drive discovery: folder enumeration is primary, title search is the fallback.** `_list_drive_folder` uses `supportsAllDrives=true`. Tank title prefixes: `(PRV)` = VN, **`(PDWB)`** = WB.

**R-36 · Tank annual cross-check reads `SUMMARY (LTR)`, not `Sheet 1`.** Per the documented chain `PROD. REPORT → DATA → Sheet 1 → SUMMARY LTR → Annual`, `Sheet 1` is two steps downstream and derived. Both FY25-26 and FY26-27 use `kind: tank_annual_2526` — the two tabs are structurally identical (reversed months, `Production (in Ltr)` / `Rejection (in Ltr)` sub-header, item name always in **col 1**). Deliberate, not a mislabel.

**R-37 · The Tank `SUMMARY (LTR)` tab contains TWO pivot sections** — product-type then size, separated by a blank row and a **duplicate TOTAL**. Parsing must stop at the second TOTAL once data has been emitted, or every plant-month is counted twice (see Failure Mode #11).

**R-38 · MOULDING has no `DAILY_SOURCES` entry of its own.** `_DAILY_LAYOUTS["PIPE"]` holds two specs against one workbook. A per-segment probe on `"MOULDING"` returns `[]` and looks wrongly empty.

**R-39 · Where a segment records the same production twice, neither source is authoritative.**
Reconcile by **date-wise maximum over the union** of every (machine, date) cell either source reports. Never choose one; never add them.
- The **item-level** sheet has no row for a zero-output day — it loses machine-days that ran without producing.
- The **machine-level** sheet is hand-maintained — it loses days nobody filled in.

Proven across five months in **both directions**: PIPE April (six machine-days only in Report-11); Tank VN **April — a gap in each direction inside one month** (PR misses the 28th, DR misses the 16th); May (same dates, 12 h vs 8 h — max resolves it); June (DR misses three dates); July (PR misses the 19th — 8 h run, zero output).

**A difference between the two totals is not an error** — it measures what one source is missing, and **its sign can reverse month to month**. What *is* an error is the two disagreeing on a quantity they both fully record (VN July output agrees to 0.014%).

---

## PART 3 — DO NOT TOUCH

Each of these is correct, deliberate, and has been "fixed" into a regression at least once.

**R-15 · `pipe_reconcile.py`** — validated against the audited April figure (157,883 kg).
**R-16 · `baselines.json` being empty is deliberate.** Ship only real, business-supplied planned hours; never estimates or the flat 500-h placeholder. **`APP_DEFAULT_IDEAL_HOURS` must not contain `TANK`** — Tank has real hours (R-39) but no ideal hours, so utilisation stays suppressed.
**R-17 · `parse_mc_detail` ignores stored ratio cells and recomputes.** This is R-01 in code.
**R-18 · `segment_cost` and `utilisation` have `segments: []` intentionally.** Reports naming a specific plant *must* declare segments.
**R-19 · `_DAILY_LAYOUTS` is the authoritative per-segment tab map.** Read it before reasoning about where a number comes from.
**R-20 · Machine Planning (`mp_*.py`, `machine_planning_*.html`) is out of scope** for data/segment work.
**R-21 · Never write to Google Sheets.**
**R-40 · `parse_ideal_labour_rates()` reads only the TOTAL row — deliberately.** The month rows use piece counts for fittings; reading them would import a ~15× error.
**R-41 · `tank_reconcile.py` and the R-39 union logic** — verified across four VN months. Includes synthetic zero-output records for DR-only dates.

---

## PART 4 — VERIFIED ACCEPTANCE NUMBERS (Apr–Jul 2026)

| Segment | APR | MAY | JUN | JUL | Total |
|---|---|---|---|---|---|
| **PIPE** (gross) | 190,494 | 344,000 | 178,782 | 564,695 | 1,277,971 kg / 6,507 h |
| **MOULDING** | 89,152 | 75,771 | 97,007 | 104,086 | 366,015 kg / rej ~1.0% |
| **PTMT** Nett — *annual basis* | 99,262 | 104,729 | 160,478 | 172,639 | 537,109 kg / 75,083 h / 1,105 moulds / 6.14% |
| **PTMT** Nett — *daily/Report-5 basis* | 99,262 | 104,729 | **147,835** | 172,639 | **524,465** kg |
| **GARDEN KH** (block tabs) | 42,736 | 53,235 | 70,520 | 68,390 in-sheet | 232,528 kg — **KH only** |
| **GARDEN KH** (Daily Report) | 38,950 | 0 | 66,911 | 32,191 | 138,052 kg / 1,553 h — **KH only** |
| **GARDEN KH rejection** | 1,191 | n/a | 2,215 | 1,853.50 | 5,259.50 kg / **3.81%** |
| **GARDEN_WB** | — | — | 22,152.8 | 6,457.4 | plant started June |
| **HDPE** | 0 | 1,369.20 | 0 | **22,448.04** | Jul = M/C-1 21,931.28 + M/C-2 516.76 |
| **TANK** (annual) | 636,250 | 1,582,500 | 2,596,600 | 1,995,500 | 6,810,850 Ltr |
| **TANK daily** | | KH 846,600 | VN 533,500 · KH 1,419,500 | VN 565,500 · WB 1,702,000 | VN 1,854,250 · WB 4,595,500 · KH 3,619,600 |
| **TANK VN hours** (R-39 union) | 76 | 196 | 208 | 232 | WB Apr 100 |

**R-22 · PTMT output basis = Nett Output; grinding/regrind excluded.** Two accepted bases exist — they are different sources, not a discrepancy to be resolved:
- **Annual basis** (mould chain): APR 99,262 · MAY 104,729 · JUN 160,478 · JUL 172,639 · total **537,109 kg**
- **Daily/Report-5 basis**: APR 99,262 · MAY 104,729 · JUN **147,835** · JUL 172,639 · total **524,465 kg**

June diverges between the two chains (R-24). Label which basis is in use whenever quoting a PTMT headline figure.

**Tank rejection, both bases (VN July):** litres **5,500 Ltr / 0.97%** (capacity) · kilograms **449.30 kg / 3.09%** (material). The plant reports the kilogram basis. Show both, labelled.

**Production basis:** all production pages use **net**; the costing module uses **gross**, matching the workbook note *"Production in KG has Rejection included."*

---

## PART 5 — OPEN QUESTIONS. DO NOT GUESS.

**R-23 · Garden is undecided on three axes.** (a) Which basis within KH — Daily Report (138,052) or block tabs (232,528)? The documented management chain starts at Daily Report. (b) Which plants — KH only, or KH + WB? (c) Which wage figure — see R-42.

**R-24 · PTMT annual is fed by two chains** — machine (Report 5) and mould (MASTER PTMT Moulding Weight → PTMT MOULDS). April and May reconcile; June and July do not, with the annual reporting more *net* than Report-5 recorded *gross*.

**R-26 · Tank KH daily is ~2.24× its annual** (June 1,419,500 vs 633,500). VN matches exactly on the same mechanism, so this is real data divergence. Two suspect KH June source entries are with the plant: **23 June** 10 pieces produced / 90 rejected, and **30 June** 243 pieces from 6 cycles.

**R-27 · Report-11 machine names** — aliases added and the zero/partial-match warning implemented. `1-KABRA-90-22` (530 kg, May) remains unmapped by design; aliases are added on evidence, never guessed.

**R-42 · Two annuals disagree on Garden wages.** Garden annual `SUMMARY` J15 = **₹4,26,164** (M15 = ₹2.97/kg); Segment Cost `Garden Pipe` I4 = **₹2,20,797** (+ ₹50,547 contractor, N4 = ₹2.02/kg). **Paid hours are identical in both**, so they describe the same labour. The ratio varies by month (1.37 / 1.92 / 2.59), so it is not a scaling. The app uses the Segment Cost workbook.

**R-43 · Fittings production is recorded in two units.** `REJECTION & PRODUCTION` col F (APR 13,40,117) against `Ideal Power Cost` col E (APR 90,038.43) — a ~15× gap implying 67 g per fitting. `Ideal Labour Cost` F4 multiplies the pieces figure by the per-kg rate, inflating ideal labour ~15×.

**R-25 — CLOSED.** Tank hours are resolved by R-39. `PLANTS_WITHOUT_RUNHOURS` is now empty; it was factually wrong.

---

## PART 6 — KNOWN FAILURE MODES

1. **Silent column fallback** — bank account as wages, pieces as kg, headcount as wages, kg as litres.
2. **Annual-vs-daily layer confusion** — reading a derived roll-up as source.
3. **Tab-name string matching** — `"MC" in "Month Wise M/C"` is False.
4. **Date-format variance within one column** — `Jul 2, 2026` and `3-Jul-2026` coexist.
5. **Silent row drops** — undated rows skipped (Garden July: 2,352 kg); colour variants collapse on group-by.
6. **Empty `segments: []` skipping the filter** — leaked 10M litres of Tank into the Moulding report.
7. **False "implemented"** — a task marked done with the root bug untouched.
8. **Code-inspection green ≠ runtime green.**
9. **Transient Drive 404s.** *Resolved:* 3 consecutive **informative** misses plus a direct-read probe; a failed scan is *no information*.
10. **Cache TTL is 900 s;** `pg_cache_write` is an upsert.
11. **Duplicate pivot sections in one tab** — the Tank `SUMMARY (LTR)` holds two, under a repeated TOTAL. Parsing past the second doubles everything.
12. **Tests that mirror production code** rather than calling it.
13. **Two repositories** — publish from the wrong tree.
14. **A metric with a missing numerator column**, or a column **present but empty**, defeating the no-fake-0% guard.
15. **Assuming a two-source difference means one source is wrong.** A sign flip between months is diagnostic of two incomplete sources (R-39).
16. **The same field named differently across tabs** (`PRODUCTION HOURS` / `RUN HOURS`), and a label column that is not column 0. Both silently returned zero for months.
17. **Removing a suppression flag can switch on a fabricated figure.** Closing R-25 was right about hours, but `APP_DEFAULT_IDEAL_HOURS` then supplied a 500-hour denominator nobody authorised. **When a metric is unblocked, check what its denominator resolves to before shipping.**
18. **Internally consistent bad data.** Both KH June errors survive every arithmetic check because the sheet's formulas propagate them. Only comparison against an **independent quantity** — the cycle count, the row's own production — exposes them.

---

## PART 7 — PROCESS RULES

**R-28 · Diagnose read-only before fixing.**
**R-29 · Verify at runtime, not by reading code.** A fix is not done until a live run prints the recomputed number *and* the source-layer flag.
**R-30 · Validate against a target before wiring.** If it doesn't reconcile, stop and report — never bend the parser to hit the number.
**R-31 · Never mark a task implemented unless its validation actually passed.**
**R-32 · Scope tightly. One concern per change.** State what must not be touched.
**R-33 · Stop-and-report gates between phases.**
**R-34 · Preserve evidence.** Do not clear caches or overwrite the only record of a problem under investigation.
**R-35 · Source problems get flagged, not coded around.**

---

## PART 8 — DATA OWNERS

| Area | Owner |
|---|---|
| Annual summaries, pipeline | Preeti |
| Pipe machines (Report 5, M/C 1–9) | Nikhil |
| Moulding machines (Report 5, C34:C58) | Jitendra Yadav |
| Pipe moulds (Reports 17–20) | Ms Pooja |
| Compound (Reports 6–10) | Mr Shelendra |
| PTMT | Alok Roy |
| Garden Pipe / HDPE daily | Anuj |

---

*Amend this document when a rule is proven wrong — with the evidence. Never to accommodate a change that is convenient.*