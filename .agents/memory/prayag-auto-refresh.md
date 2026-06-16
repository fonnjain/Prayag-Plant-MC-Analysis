---
name: Prayag always-on background refresh
description: Design rules for the 10-min background sheet refresher + "Last synced" header stamp, and why it only matters on a Reserved VM.
---

# Always-on background sync

A daemon thread re-pulls live Google Sheets every `_REFRESH_INTERVAL` (10 min) so the dashboard stays current around the clock without waiting for a visitor. A header stamp shows "Data synced X ago · dd-mm-yyyy HH:MM · auto-refresh on".

**Why it only helps on a Reserved VM:** the background loop advances only while the process is alive. On a scale-to-zero (autoscale) deployment the process sleeps between requests, so the loop simply doesn't run — harmless, but the always-on refresh is a no-op there. The feature was added *because* the deployment was switched to a Reserved VM.

**Rules to keep it correct:**
- `_REFRESH_INTERVAL` must stay **< `_DATA_TTL`** (currently 600 < 900). The refresher refills inside the TTL window so visitors keep hitting a warm cache and never trigger the slow (30–60 s) cold fetch. The TTL is just the on-demand fallback when no refresher runs.
- A force-refresh must **coordinate cache eviction with the request path's single-flight locks**, not bare dict pops. Monthly: re-fetch under `_fetch_lock` (don't pop-then-call `_live_payload`, that races and risks a double fetch). Daily: pop each recent key under its own `_daily_key_lock` before re-fetching, so a concurrent `_load_daily_cached` write is never clobbered. Never hold `_fetch_lock` while calling `_live_payload` — it re-acquires the same non-reentrant lock and deadlocks.
- The **"synced X ago" stamp is itself the staleness signal**: if the refresher silently dies (e.g. Google auth breaks), `last_ok_ts` stops advancing and the stamp visibly grows stale. Background failures are also recorded in `_sync_state.last_error` and logged (rate-limited), surfaced via `sync_status()`.
- Suppress the stamp entirely in demo mode (no live sync to report) — otherwise it falsely says "Syncing live data…".

**Multi-worker caveat:** prod runs gunicorn single-worker (the operational invariant). If workers ever go >1, each process spawns its own refresher → duplicate fetches and per-worker `last_ok_ts`. Acceptable (just extra quota/load), not broken; add leader-election only if worker count is raised.
