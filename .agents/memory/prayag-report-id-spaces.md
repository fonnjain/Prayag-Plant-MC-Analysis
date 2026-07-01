---
name: Report id spaces & four-pillar AI analysis
description: Two distinct report id spaces (registry vs AI) and how the per-report analytical pages are grounded.
---

# Two report id spaces (do NOT assume they match)

- **Management-report registry** (`reports/registry.py`) ids are per-plant/per-location; the **AI analytical pages** (`REPORT_TYPES` in `app.py`, served by `/reports/<id>`) are per-analysis, so several registry reports collapse onto one AI page (all three extrusion plants → one page; the three tanks → one; both PTMT → one). Some registry reports have no AI page by design.
- A mapping (`_MR_TO_AI`) deep-links each download to its AI page, gated by membership in the AI id set so an unmapped/disabled report just omits the link instead of 404ing.

**Why:** treating the two as one id space silently 404s or mislinks. **How to apply:** when adding/renaming a report on either side, update the mapping and keep the gate.

# Four-pillar AI analytical pages

- `/reports/<id>` opens straight into **Analytics / Diagnostics / Red Flags / Recommended Actions**, auto-fetched client-side on load (never a server-blocking render — protects against the gunicorn worker timeout, see prayag-deploy-timeouts).
- Diagnostics/Red Flags are grounded ONLY in what the DETERMINISTIC engine detected (four-tier validation warnings + recon badge). The prompt forbids inventing data-quality flags beyond that list; if empty, Red Flags says "none detected" rather than fabricating one.
- Recon that counts as a red flag: only daily-first falling SHORT of the grid. An expected undercount (daily-first EXCEEDS grid → informational) is NEVER a flag — core invariant.

**How to apply:** any change to the pillars or the diagnostics grounding MUST bump the AI-report cache-key version in lockstep (it includes the diagnostics text), or a newly detected flag serves stale prose.

# Exact-month deep-link period

- `parse_period` accepts `?period=YYYY-MM` as an exact calendar month in its OWN year — distinct from the `1`–`12` FY-month tokens, which resolve to the CURRENT FY. Management Reports links pass the selected `YYYY-MM` so the AI page lands on the manager's chosen month (deep tier).
