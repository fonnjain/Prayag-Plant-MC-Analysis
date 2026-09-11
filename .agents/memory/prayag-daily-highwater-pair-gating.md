---
name: Daily high-water pair gating
description: Why a one-record decline in one logical emitter can hide other valid plants from a shared daily workbook.
---

Daily completeness is enforced at the physical workbook/month pair even though record-count high-water marks are tracked separately per logical emitted plant. If any emitter falls below its stored maximum, the whole pair is withheld, including other emitters whose populations are complete.

**Why:** This protects published totals from transient partial reads, but a legitimate historical row deletion or parser-eligibility change looks identical to a partial read. Repeated retries return the same lower population and cannot self-heal. A shared PIPE workbook also emits MOULDING, so a PIPE decline can make both plants disappear.

**How to apply:** When plants from one shared workbook vanish together, inspect current logical populations versus persisted high-water counts before debugging filters or chart rendering. Determine whether the source row was deleted/changed or the parser stopped recognizing it; do not reset a baseline until the lower population is verified as complete.

The June/July 2026 PIPE investigation is closed in PRAYAG_RULES R-45. The plant confirmed the edits were deliberate corrections; current 70/176 PIPE populations supersede the stale 71/177 high-waters. The derived July residual has no standing.

The 10 September 2026 production screenshot with PIPE and MOULDING absent was
confirmed as a stale-worker incident. Its “6/8 files” indicator, and the later
“7/8 files” indicator, belong to Data Confirmation—not the daily loader.

**Why:** Treating the confirmation badge as a loader count caused a false second
diagnostic round after the shared PIPE workbook was already rendering correctly.

**How to apply:** Establish loader completeness from `_failed_pairs` and its
visible amber warning, not from the confirmation coverage badge.