---
name: Prayag management-report exports
description: How the standalone per-report .xlsx / ZIP export set is structured and why figures are recomputed VALUES, not live formulas.
---

# Management-report exports (per-report .xlsx + ZIP)

The `/management-reports` page serves ONE standalone `.xlsx` per report plus a
"Download all (ZIP)" bundle. The set, filenames, and location grouping all come
from `reports/registry.py` (`REPORTS` list) — the single source of truth — so the
page, the download routes, and the ZIP can never drift apart. Generators live in
`reports/generators.py` and import only `sheets/metrics/parsers/sources`, never
`app`.

**Rule: exports carry recomputed VALUES, never live Excel formulas.**
**Why:** the approved approach (Option 1) avoids a LibreOffice/formula-eval
dependency; every workbook must open showing correct numbers on any machine.
Each generator recomputes its ratios in Python (same daily-first discipline as the
dashboard) and writes the resolved value. A ratio that can't be computed stays
blank — never a fake 0%.

**Rule: the ZIP must fail loudly, never serve a silently-empty archive.**
`registry.zip_bundle()` returns a `ZipResult(data, built, total, skipped)`; the
route returns 502 when `built == 0`. A single failing report is skipped (logged in
`skipped`) so one bad report never sinks the whole bundle.

**Layout oracle:** the acceptance layouts/totals are the attached reference
workbooks (`attached_assets/Prayag_*_Management_Reports_*.xlsx` and the PTMT one).
Column layouts must match these exactly; validate generator totals against them.

**PIPE monthly baseline drift:** post-close backfill grows PIPE month totals, so
the build-state `PIPE_*_EXP` constants go stale and #1/#17 fail. Re-baseline to the
new figure ONLY when the audit is coherent (#17/#17b PASS) and the reference oracle
confirms it — a coherent grow is fresh backfill, an incoherent one is a real
reconciliation regression. (May 2026 was re-baselined 264,717 → 313,637 this way.)
