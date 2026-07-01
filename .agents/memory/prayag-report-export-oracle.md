---
name: Report-export oracle test
description: Offline fixture-backed test that pins each management-report generator's key totals to the May 2026 acceptance oracle.
---

The live `/build-state` #19 gate pins report-export totals against the real
Google Sheets, so it can't run offline/in CI. The offline counterpart replays a
committed snapshot of the May 2026 daily records (feeds
`sheets.get_daily_records`) plus the raw PIPE Report-12 values (feeds
`gen_pipe_moulds`'s `sheets.read_values` path), monkeypatches those readers, runs
every generator, and asserts each report's headline TOTAL against the values read
off the attached oracle workbook.

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
