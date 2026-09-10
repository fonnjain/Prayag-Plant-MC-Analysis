---
name: Daily high-water pair gating
description: Why a one-record decline in one logical emitter can hide other valid plants from a shared daily workbook.
---

Daily completeness is enforced at the physical workbook/month pair even though record-count high-water marks are tracked separately per logical emitted plant. If any emitter falls below its stored maximum, the whole pair is withheld, including other emitters whose populations are complete.

**Why:** This protects published totals from transient partial reads, but a legitimate historical row deletion or parser-eligibility change looks identical to a partial read. Repeated retries return the same lower population and cannot self-heal. A shared PIPE workbook also emits MOULDING, so a PIPE decline can make both plants disappear.

**How to apply:** When plants from one shared workbook vanish together, inspect current logical populations versus persisted high-water counts before debugging filters or chart rendering. Determine whether the source row was deleted/changed or the parser stopped recognizing it; do not reset a baseline until the lower population is verified as complete.