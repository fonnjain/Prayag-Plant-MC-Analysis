---
name: Report-export oracle test
description: Offline fixture-backed test that pins each management-report generator's key totals to the May 2026 acceptance oracle.
---

The live `/build-state` gate separates two concerns: #19 checks May-only
canonical daily outputs against the acceptance values, while #19b checks that
every enabled report renders to a non-trivial XLSX and exposes any unavailable
model's named failure. Do not compare an FY/through-month export TOTAL with a
single-month oracle. The offline counterpart replays a committed snapshot of the
May 2026 daily records plus the raw PIPE Report-12 values.

**Only the recomputed OUTPUT (kg) totals reconcile exactly** — mirror the
build-state #19 philosophy and pin those. Secondary columns (run-hours,
rejection) in the oracle workbook are STALE relative to later source backfill:
garden run-hours now recompute to blank, hdpe rejection differs, and
moulding/gom/mould-eff run-hours drift ~0.6%. Do NOT pin those or the test
fails against oracle staleness rather than a real generator regression. Only
pin a secondary column where it still reconciles (pipe hrs+rej, moulding rej,
pipe_moulds pcs, ptmt machine count).

**Why:** the oracle output totals were re-baselined against fresh backfill but
the workbook's hrs/rej cells were not; the dashboard's whole design recomputes
figures rather than trusting stored cells, so recomputed run-hours legitimately
diverge from the frozen oracle.

**How to apply:** to re-baseline the fixtures after a genuine, coherent source
change, re-capture `tests/fixtures/daily_2026_05.json` and
`pipe_report12_2026_05.json` from live sheets (dataclasses.asdict of every
`get_daily_records(["2026-05"])` record; raw `read_values(fid,"Report-12")`),
then re-read the oracle TOTAL rows. `gen_pipe` renders TWO sheets (main +
Type-wise); match each expected key against the first total row that carries it,
never `rows[-1]`.

PIPE May's accepted 313,516 kg is the authoritative primary-machine daily
population after the confirmed July–September plant corrections. With 30,484 kg
rejection, the current May Report-5 gross basis is 344,000 kg. The freeze preview
adds 116,200 kg of finishing output and therefore captures 429,716 kg across all
125 logical PIPE records while normal headline metrics remain 313,516 kg. The
313,516 kg figure already incorporates the confirmed 121 kg correction. Revisions
11932, 12221, and 12438 all pair it with 30,484 kg rejection; 343,879 kg is not a
revision total, but a double-subtraction. A hypothetical 344,121 kg reverses only
the output correction while holding post-correction rejection fixed; no retained
revision produces that total either.

The accepted April–July Report-5 hours are final at 6,517:
833 + 1,838 + 1,008 + 2,838. The former 6,507 total used an eight-value
handover that paired M/C-1+M/C-2. Retained revisions fully trace the +10:
May M/C-5 +6; June M/C-5 +3, M/C-6 +19, M/C-7 −22 (net 0); July M/C-2
−11 and M/C-3 +15 (net +4); April unchanged.

**Why:** the user confirmed this revision trace closes the final open question,
so 6,517 is a final accepted Part 4 value rather than a provisional assertion.

**How to apply:** keep the month breakdown beside 6,517 whenever it is restated;
only move it after another retained-source revision audit accounts for the delta.

**June 2026 second month (`test_report_export_june_oracle.py`, fixtures
`daily_2026_06.json` + `pipe_report12_2026_06.json`).** Adds a
structurally-different month: PTMT ships as a SEPARATE workbook and there is a
STANDALONE (D) Pipe Moulds Summary workbook. Crucial gotcha: the June oracle
workbooks were frozen MID-MONTH (their Overview sheet admits June was still being
entered — Report-11 covered 5 of 11 dates), so the live Kaharani source has since
been backfilled and the oracle OUTPUT cells are STALE (not just the secondary
columns as in May). So June is a hybrid: `garden`, `ptmt_moulds`, `ptmt_eff` are
ORACLE-VERIFIED (fixture reconciles ≤0.5%); `pipe` pins the oracle's own
documented "complete Report-5 daily" grand total (168,738 kg on the Type-wise
sheet — NOT the A-cell 170,216, which is the stale R5/R11 max); `moulding`, `gom`,
`mould_eff`, `pipe_moulds` are SNAPSHOT-PINNED to the committed-fixture recompute
(oracle output drifted >0.5%). `hdpe` has no June production (oracle = "awaiting
source"), so it is skipped. **Why:** June was never re-baselined the way May was.
Do NOT "fix" the drifted June tests by pinning the oracle cells — that re-tests
oracle staleness, not a generator regression.
