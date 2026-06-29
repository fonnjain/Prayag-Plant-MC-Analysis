---
name: Prayag stale-rollup acks vs issue acks
description: Why the two acknowledgement stores key on the fingerprint differently
---

The dashboard has TWO append-only acknowledgement stores, and they treat the
data fingerprint OPPOSITELY on purpose:

- **Confirmation issue acks** (`confirmation_issue_acks`): keyed to a STABLE
  issue identity only (numbers normalised out of the message). The ack survives
  magnitude drift so a recurring known anomaly (e.g. PIPE's by-design reconcile
  offset) stays acknowledged across data pulls.
- **Stale-rollup acks** (`stale_rollup_acks`): keyed to a stable compound·month
  key AND the alert's data fingerprint. The ack applies ONLY to the
  acknowledged data state, so the alert RE-SURFACES automatically if the rollup
  drifts again to a new state after a fix.

**Why:** a confirmation anomaly is "known and accepted forever"; a stale rollup
is "I know about THIS drift, ping me again if it changes." Don't unify them.

**How to apply:** when adding a new acknowledgeable signal, decide first whether
re-surfacing on data change is desired — include the fingerprint in the match if
yes (stale-rollup pattern), omit it if no (issue-ack pattern).
