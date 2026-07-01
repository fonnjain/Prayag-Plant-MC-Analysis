---
name: Report id spaces & four-pillar AI analysis
description: Two distinct report id spaces (registry vs AI) and how the per-report analytical pages are grounded.
---

# Two report id spaces (do NOT assume they match)

- **Management-report registry** (`reports/registry.py`) ids and the **AI analytical pages** (`REPORT_TYPES` in `app.py`, served by `/reports/<id>`) are BOTH per-plant now, but they are still SEPARATE id spaces with different names (e.g. registry `pipe`/`ptmt_moulds` vs AI `pipe_summary`/`ptmt_summary`). The `/reports` index groups purely by plant via each `REPORT_TYPES` entry's `plant` key + `_AI_PLANT_ORDER`/`_AI_PLANT_NAMES` (no more location grouping / `_REPORT_CATALOGUE`).
- The two families that share ONE machine-based table layout each are keyed by sets, not single ids: `_EXTRUSION_REPORT_IDS` (pipe/garden/hdpe) and `_INJECTION_REPORT_IDS` (ptmt/cp). `_build_report_table` and `_report_reconciliation` (`machine_based`) branch on set membership — add a new extrusion/injection plant → add its id to the set or its table silently falls through.
- A mapping (`_MR_TO_AI`) deep-links each download to its per-plant AI page, gated by membership in the AI id set so an unmapped/disabled report just omits the link instead of 404ing.

**Why:** treating the two as one id space silently 404s or mislinks; a per-plant report whose id is missing from the family set renders no table. **How to apply:** when adding/renaming a report on either side, update `_MR_TO_AI`, the family set if machine-based, and the `plant` key; keep the gate.

# Four-pillar AI analytical pages

- `/reports/<id>` opens straight into **Analytics / Diagnostics / Red Flags / Recommended Actions**, auto-fetched client-side on load (never a server-blocking render — protects against the gunicorn worker timeout, see prayag-deploy-timeouts).
- Diagnostics/Red Flags are grounded ONLY in what the DETERMINISTIC engine detected (four-tier validation warnings + recon badge). The prompt forbids inventing data-quality flags beyond that list; if empty, Red Flags says "none detected" rather than fabricating one.
- Recon that counts as a red flag: only daily-first falling SHORT of the grid. An expected undercount (daily-first EXCEEDS grid → informational) is NEVER a flag — core invariant.

**How to apply:** any change to the pillars or the diagnostics grounding MUST bump the AI-report cache-key version in lockstep (it includes the diagnostics text), or a newly detected flag serves stale prose.

# Exact-month deep-link period

- `parse_period` accepts `?period=YYYY-MM` as an exact calendar month in its OWN year — distinct from the `1`–`12` FY-month tokens, which resolve to the CURRENT FY. Management Reports links pass the selected `YYYY-MM` so the AI page lands on the manager's chosen month (deep tier).
