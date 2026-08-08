---
name: Prayag daily-capable plant set is data-driven, not the docs
description: Which Prayag plants are daily-capable vs summary-only — the live DAILY_SOURCES is the source of truth; replit.md/older notes are stale.
---

Live `sources.DAILY_SOURCES` keys are the ONLY source of truth for which plants are
daily-capable. As observed: daily = PIPE, PTMT, GARDEN, HDPE, TANK, **CP**.
`ANNUAL_SOURCES` plant codes = GARDEN, HDPE, **MOULDING**, PIPE.

**Why:** replit.md and earlier memory repeatedly say daily covers "PIPE, MOULDING,
GARDEN, HDPE, PTMT, TANK" — that list is STALE. In reality MOULDING is annual-only
(no daily workbook) and CP is daily-capable. Any feature that classifies plants by
grain (e.g. the /data Data Health status rule: daily-capable+no rows => red "empty";
summary-grain => gray "awaiting") must read DAILY_SOURCES.keys() at runtime, never a
hardcoded list, or MOULDING/CP get inverted.

**How to apply:** derive daily-capable set from `set(DAILY_SOURCES.keys())` and the
summary/annual set from `{s["plant"] for s in ANNUAL_SOURCES}`. A daily-capable plant
with zero daily rows in a closed FY window is a genuine red gap; an annual-only plant
with no posted monthly figures is gray "awaiting" (never red), per the status rule.

**Correction (Aug 2026):** MOULDING IS daily-capable — its daily records come from the PIPE workbook's Report-12 tab (emit MOULDING, long layout), not its own DAILY_SOURCES entry. DAILY_SOURCES.keys() lists workbooks, not emitted plants; evicting daily_PIPE_<ym> also refreshes MOULDING daily data.

