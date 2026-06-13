---
name: Prayag manager sign-off & review trail
description: How the override that releases error-gated figures works, and why it binds to a data fingerprint.
---

# Manager sign-off (release of error-gated figures)

The four-tier confirmation withholds the Overview headline figures when status ==
`error`. A manager can release them by signing off on the Confirmation screen.

## Key design decisions

- **Release lifts the ERROR gate only.** Warnings already publish, so sign-off is
  meaningful only for error status; a release should never make a clean/warning
  figure look "overridden".

- **A sign-off binds to the data state, not just the period.** It is keyed by
  (unfiltered period_key, a deterministic fingerprint of the confirmation's issue
  set + status). If the sheets change, the fingerprint changes and the prior
  sign-off no longer matches → figures re-gate automatically.
  **Why:** prevents a stale approval from publishing numbers the manager never saw.
  **How to apply:** never weaken the key to period-only; changing the issue dict
  shape resets the fingerprint (and thus existing approvals) — that is intended.
  The route also recomputes live and rejects a posted fingerprint that no longer
  matches, so a stale form can't sign off changed data.

- **No authentication exists in this app.** The approver name is typed in and
  stored as-is — an attestation, not verified identity. Don't imply otherwise in copy.

- **Append-only log, latest row wins.** Both approve and revoke are appended;
  effective state = most recent row for (period, fingerprint). It's an audit trail,
  so durability matters → Replit Postgres, not the ephemeral deploy filesystem.
  Store degrades to a safe no-op (gate stays ON) when the DB is unavailable.

- **An empty period can't be released into real numbers.** Sign-off releases
  *withheld* figures that exist; it cannot manufacture data for a month with no
  rows (e.g. a current/future month not yet entered) — that just publishes zeros.

## Per-issue acknowledgement (a separate, lighter mechanism)

Alongside the all-or-nothing period sign-off there is per-issue acknowledgement:
a manager accepts ONE flagged issue (with optional note); an acknowledged error
drops out of the headline gate, and if every blocking error is acked the status
downgrades error→warning so figures publish.

- **Acks key on a STABLE issue identity, NOT the fingerprint.** `confirm.issue_key`
  hashes structural location (tier/plant/machine/month/sheet/file) + the message
  with all numbers normalised out. **Why:** known recurring anomalies (PIPE's
  by-design reconcile offset, >100% utilisation) change magnitude every pull; a
  fingerprint-bound ack would vanish each time and re-nag. **How to apply:** keep
  the number-stripping in `issue_key`; acks are keyed by (period_key, issue_key)
  in the separate `confirmation_issue_acks` table, so they intentionally survive
  data drift — contrast with the period sign-off, which must re-gate on drift.
- Same store conventions as sign-off: append-only, latest row per (period,issue)
  wins (`ack`/`unack`), Postgres, safe no-op when DB absent.
- The ack route recomputes live and refuses to ack an issue not present now.
