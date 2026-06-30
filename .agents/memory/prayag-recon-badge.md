---
name: Reconciliation badge (daily-first vs summary grid)
description: How the standardized report reconciliation badge classifies daily-first vs the monthly summary grid, and why positive deltas are expected.
---

# Reconciliation badge

The report-detail badge cross-checks the recomputed **daily-first** figures
against the **monthly summary grid** (`get_records` / ANNUAL_SOURCES). It is a
cross-check, never a rewrite — the daily-first recompute is always authoritative.

**Rule:** the monthly summary grid undercounts for EVERY plant (the core invariant
that makes daily-first authoritative), so daily-first exceeding the grid is
*expected* — `expect_exceeds=True` is set universally, not just for PIPE. A grid
that is sparsely populated makes the positive delta huge (e.g. GARDEN ~+300%) yet
still legitimately "expected", not a failure.

**Why:** treating a positive total delta as a red "off by +X%" fail labelled good
data as broken — a never-mislead violation. The only genuine concern is the
opposite direction.

**How to apply:**
- The ONLY real signal is a cell where daily-first falls **short** of the grid
  (a daily data gap). Cell-level flags must drive status — a shortfall cell must
  surface as `warn` even when the total is a positive expected undercount. Order
  the status checks so `flagged` is considered before the expected-undercount info
  branch, or shortfalls get silently downgraded to a blue info badge.
- No grid wired (PTMT/TANK, mixed-unit views) → honest "recomputed only" info,
  never a fabricated 0 or fake mismatch.
- Distinguish a transient grid read failure (`SheetReadError`) from "not wired by
  design" — the message must not claim a grid is missing when it merely failed to
  read this once.
