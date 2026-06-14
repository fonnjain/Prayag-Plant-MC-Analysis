---
name: Prayag deployment worker timeouts
description: Why the published Flask app 500s on heavy views, and the gunicorn + external-call budget rules that prevent it.
---

# Prayag deployment: synchronous request budget vs worker timeout

The published app 500s on its heaviest views (e.g. the 7-day / sub-monthly
window) when a single synchronous request's total wall time exceeds the gunicorn
worker timeout. Dev (Flask dev server) has no worker timeout, so the same slow
request just loads slowly and succeeds — the failure is **production-only** and
will not reproduce in dev.

**Rule:** any view that fans out to many live Google Sheets reads PLUS an
optional Claude narrative PLUS a Postgres call must keep its worst-case wall
time inside the production worker timeout, and every external call must be
individually bounded.

**Why:** gunicorn defaults to a 30s `--timeout` and (if unset) sync workers.
The Anthropic SDK defaults to a ~10-minute client timeout. So one slow Claude
call or a cold full-Sheets fetch silently blows past 30s → `[CRITICAL] WORKER
TIMEOUT` → the worker is killed → the user sees a bare "Internal Server Error".

**How to apply:**
- Production run config lives in `artifacts/prayag-web/.replit-artifact/artifact.toml`
  (NOTE: the registered artifact dir is `prayag-web`, a stub; the actual Flask
  code lives in `artifacts/prayag`). Edit it ONLY via the artifacts skill's
  `verifyAndReplaceArtifactToml` callback, never by hand.
- Gunicorn must run with a generous `--timeout` and threaded workers
  (`--worker-class gthread --threads N`) so the lightweight `/health` check is
  never starved while a worker is busy on a slow data load.
- The Claude narrative is optional (callers degrade to `None` on error), so its
  `anthropic.Anthropic(...)` client MUST set an explicit `timeout=` and small
  `max_retries=`; otherwise the SDK default lets it hang a worker for minutes.
  Remember the deep→fast model fallback does a *second* `create()` call, so the
  effective worst case is ~2× the per-call timeout.
- Module-level sheet caches (`_data_cache`, `_daily_cache` in `sheets.py`) are
  plain dicts. Under gthread, concurrent cold requests would each run the full
  slow fetch (a stampede). They're guarded by a single-flight `_fetch_lock` with
  double-checked locking: warm hits are checked BEFORE the lock (never block),
  cold fills happen once. Keep that pattern if you add another live cache.
- Config changes to artifact.toml only take effect on the next publish/deploy;
  a dev workflow restart does not exercise the production gunicorn command.
