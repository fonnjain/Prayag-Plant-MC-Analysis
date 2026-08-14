---
name: Prayag discovery persistence gap
description: ensure_daily_discovery adds months to DAILY_SOURCES in-process only — each gunicorn worker restarts fresh from sources.py; missing months must be pinned explicitly.
---

## Rule
`ensure_daily_discovery` mutates `sources.DAILY_SOURCES[plant]["files"]` **in-process only**. The mutation is not persisted to Postgres, to disk, or to other processes. Each gunicorn worker starts fresh from the literal `sources.py` dict on import.

## Why
Confirmed in Phase 2: five Tank months (TANK_VN Apr/May, TANK_WB Apr/May/Jun) sat in scannable Drive folders with parseable titles for months yet never appeared persistently — because any worker that ran discovery added them locally, then the next worker (or restart) knew nothing of them. A single discovery cycle can find them fine; what it cannot do is make them durable.

## How to apply
- Any month that matters must be **pinned in `sources.py`** as soon as it is confirmed valid.
- Discovery is useful for surfacing new months interactively and for the `/manifest` page, not as a substitute for pinning.
- When adding a new plant to `_DAILY_LAYOUTS`, immediately pin its known months in `DAILY_SOURCES` — do not wait for discovery to "eventually" find them.
- The test: `python3 -c "import sources; print(sources.DAILY_SOURCES['PLANT']['files'])"` — if a month is missing here it will not be read by any worker regardless of discovery history.
