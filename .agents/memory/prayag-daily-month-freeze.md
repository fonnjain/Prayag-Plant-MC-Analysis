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