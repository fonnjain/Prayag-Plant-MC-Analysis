---
name: Daily production month freeze
description: Durable R-46 boundaries and concurrency rules for closed-month canonical production snapshots.
---

R-46 is a narrow approved exception to the live-source Cardinal Rule. A closed
month may use an immutable canonical daily production snapshot only after two
uncached parses of the complete physical workbook match exactly and every
logical emitter has a positive durable high-water and passes completeness.

**Why:** A physical workbook can emit several logical plants, and a transition
between live and frozen state can otherwise expose mixed generations, call a
source after activation, or unfreeze a newer snapshot than the administrator
reviewed.

**How to apply:** Serialize freeze, canonical reads, and unfreeze on the physical
workbook plus month. Store one versioned snapshot per logical emitter; mixed
frozen/live reads may parse only unfrozen siblings and must never cache the
overlay. Bind unfreeze confirmation to the exact snapshot identity. Missing,
corrupt, or unavailable durable state fails closed. Specialized readers, wages,
manual entries, annual comparisons, and ideal-standard policy remain outside
the Phase 1 exception.

The first production freeze must be a deliberate, watched action and must not
use June or July 2026 because those months have a recovery and re-baseline
history. Prefer a clean closed month such as May 2026, subject to the production
preview proving matching uncached parses, expected high-water, and reviewed
verification totals.

**Why:** A technically eligible recovered month is a poor first operational
proof because recovery uncertainty and freeze behavior would be tested at the
same time.

**How to apply:** Verify the selected candidate through the published admin UI,
have an administrator review the exact preview, and perform the confirmed
second parse while the action is observed. Never create the first production
snapshot through a script or direct database write.

For the remaining May 2026 emitters, freeze one emitter at a time and stop for
review after its uncached preview. `TANK_VN` must retain exactly 196 reconciled
hours or the R-39 union did not run. `GARDEN` requires a fresh retained-revision
check both before preview and immediately before confirmation because rows were
added through August. Before `PTMT`, identify the changed derived cells and prove
whether any feed fields stored in the snapshot; resolve dependencies first.

**Why:** the user explicitly accepted these as freeze-blocking criteria after
post-close plant corrections caused the May, June, and July source drift.

**How to apply:** never infer completion from a closed calendar month. Re-run the
candidate-specific source audit at the stated point, then require two matching
uncached parses and the established durable high-water.

Phase 2 is approved for implementation only after April–July production freezes
are complete. Use two stages: production first, then monthly costs/actuals.
Classify inputs as monthly actual fact → snapshot; policy or standard → stay
live; derived output → recompute from frozen facts plus current policy.

**Why:** late wages and bills must not be lost, while corrected ideal rates
should still update closed-month derived figures.

**How to apply:** the Stage 2 readiness refusal must name every missing component
in actionable terms (for example, “July contractor invoice not entered”);
never return only “incomplete.” A late actual after cost freeze requires an
audited cost-stage unfreeze, live re-sync, review, and new immutable version.