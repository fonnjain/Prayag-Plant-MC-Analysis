---
name: Prayag workbook Index tab as authoritative tab-metadata
description: How the Index tab drives description-keyed tab resolution, and the hard rule that figures must never depend on an unverified Index.
---

# The Index tab is advisory metadata, never on a figure's critical path

Each PTMT / Pipe&Fitting Google-Sheets workbook has its own "Index" tab — the
authoritative description of every Report-N tab. The app reads it to: key tabs by
(plant + report DESCRIPTION) not bare number (the same number means different
things across workbooks), drive daily-slicing by frequency, infer unit from the
description, surface unwired tabs on /sources, and flag month-over-month changes.

**The hard rule:** a production FIGURE must never silently change based on the
Index. `sheets.resolve_report_tab(plant, keywords, fallback, ...)` may only switch
the daily-production tab away from the configured `fallback` when it can
POSITIVELY VERIFY (via `list_tabs`) that the Index-named tab exists.

**Why:** an earlier version returned `(cand, True)` whenever the tab list was
unavailable (offline / transient `list_tabs` failure), trusting an unverified
Index id even when it differed from the fallback — its comment claimed "only if
it differs by spacing" but the code didn't enforce that. A transient read could
then point ingestion at a wrong/non-existent tab and zero out figures.

**How to apply:** when tab listing is unavailable, keep the fallback (return
`fallback, False`). The ONLY safe offline switch is when the candidate equals the
fallback modulo spacing — a no-op — and even then return the known-good
configured fallback string. `require_sliceable=True` additionally bars resolving
daily ingestion to a weekly/monthly snapshot report. Degradation: no Index tab /
read failure → `[]`; no `DATABASE_URL` → change-tracking is a no-op.

# Report-5 sub-blocks and report_key identity

Report-5's first machine family ("Pipe M/C") lives on the MAIN report row's
Include; the other two families (Mixer/Grinder/Pulverizer, Moulding M/C) are
continuation rows (blank "Reports" cell) folded in as `sub_blocks`. So Report-5
has 2 sub_blocks, not 3. Frequency is a merged cell (blank = inherit from the row
above); PTMT Report-12's frequency ("Every Monday") is mis-typed into the Types
column and is recovered from there.

`report_key` is space-insensitive (`re.sub(r"\s+","", ...)`) so "Report-8 (A)"
and "Report-8(A)" are one identity — otherwise cosmetic spacing edits in the Index
produce false added/removed change-flags month-over-month. The change-flag
baseline (store.index_baseline, keyed UNIQUE(plant, report_key)) records first
sight WITHOUT flagging; only a later description/frequency change flags.

Tests: `tests/test_index_parser.py`, `tests/test_resolve_report_tab.py`.
