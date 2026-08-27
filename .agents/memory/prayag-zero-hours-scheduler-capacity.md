---
name: Zero-run-hours scheduler capacity blocker
description: Open production data-integrity issue where positive PIPE output has zero recorded hours and can understate scheduler capacity.
---

Positive PIPE output paired with zero recorded run hours is a production
capacity-model blocker, not an idle-machine assumption. The source and parser
path must be reconciled, or an explicit warning/gate must prevent the
understated hours from silently feeding utilisation and schedule capacity.

Known evidence:

- M/C-5: 808.00 kg on 2026-06-20.
- M/C-1: 943.80 kg on 2026-08-23.
- M/C-2: 1,329.60 kg on 2026-08-23.

This remains open because the follow-up task was cancelled before the source
versus parser diagnosis was completed. It needs an owner-visible work item
before the planning app is treated as relying on trustworthy capacity.

**Why:** The Plumbing schedule endpoint calculates available capacity from
recorded run hours. These rows can make available capacity smaller than the
machine actually had, so the resulting schedule can be needlessly incomplete.

**How to apply:** When changing PIPE daily ingestion, metrics, utilisation, or
scheduler capacity, test these three machine-days and verify both the
downstream capacity inputs and the user-facing warning/gating behavior.