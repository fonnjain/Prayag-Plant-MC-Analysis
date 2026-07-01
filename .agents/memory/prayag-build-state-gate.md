---
name: Prayag build-state verification gate
description: How to verify the Prayag app against its consolidated build spec — the /build-state route runs the spec's ground-truth assertions live.
---

The Prayag app's consolidated build spec and checklist are encoded as a live self-test at the **`/build-state`** route. It runs static code/config assertions plus live-data assertions against the real Google Sheets and renders a PASS/FAIL table.

**How to apply:** to verify a Prayag change against the spec, restart the `artifacts/prayag-web: web` workflow and `curl -s localhost:80/build-state` (it renders HTML, not JSON — strip tags to read the PASS/FAIL lines). Expect **all-PASS** before sign-off (the assertion count grows as the spec evolves — treat `/build-state` itself as the source of truth for the current expected figures, not any number written here). Monthly/FY views must show `daily_used=True` (daily-only path, grid never substituted as a figure).

**Why:** spec acceptance is runtime-verifiable, not just code-readable — the live assertions catch a stale build or a broken sheet read that passing unit tests (which stub the readers) cannot. Tests use hermetic stubs; `/build-state` is the real-data gate. Acceptance numbers in the original prompt (e.g. PIPE April "157,883") are *as-of snapshots* that drift as machine-days are backfilled after month close — the live `/build-state` assertion carries the current baseline, so trust it over any literal in the prompt/docs.

**Live-data assertions must anchor to the latest COMPLETE data month, never `_today()`'s in-progress month.** On month rollover the current month is legitimately empty (no production entered yet; some plants like TANK are only created mid-month), so a check that requires current-month rows > 0 false-FAILs at the start of every month. Scan back over recent complete months and verify parser health where data genuinely exists (mirrors how the HDPE check pins its latest data month). This upholds the core invariant: an in-progress-but-empty month is not a failure and must never be treated as one.

The deterministic-figures + daily-only design is the gate's precondition.
