---
name: Prayag build-state verification gate
description: How to verify the Prayag app against its consolidated build spec — the /build-state route runs the spec's ground-truth assertions live.
---

The Prayag app's consolidated build spec (`attached_assets/REPLIT_PROMPT_FINAL_*.md`) and its 16-point checklist (`attached_assets/REPLIT_BUILD_STATE_CHECKLIST_*.md`) are encoded as a live self-test at the **`/build-state`** route. It runs static code/config assertions plus live-data assertions against the real Google Sheets and renders a PASS/FAIL table.

**How to apply:** to verify a Prayag change against the spec, restart the `artifacts/prayag-web: web` workflow and `curl -s localhost:80/build-state`. Expect **16/16 PASS** before sign-off. The verified ground-truth figures live here too: PIPE May 2026 = 107,609 kg (Report-11 "Weight"), MOULDING May 2026 = 75,771 kg (Report-12 "Wt in Kgs"); monthly/FY views must show `daily_used=True` (daily-only path, grid never substituted as a figure).

**Why:** spec acceptance is runtime-verifiable, not just code-readable — the live assertions catch a stale build or a broken sheet read that passing unit tests (which stub the readers) cannot. Tests use hermetic stubs; `/build-state` is the real-data gate.

The deterministic-figures + daily-only design is the gate's precondition; the Postgres/daily-only-refresh migration is the spec's explicitly DEFERRED next milestone (start only after daily parsing is verified for every plant — which `/build-state` confirms).
