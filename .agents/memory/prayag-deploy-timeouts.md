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

## Cross-worker cache isolation (cross-page re-fetching)

With `--workers 2`, each gunicorn process has its own in-process cache. A
request routed to worker-2 after worker-1 warmed the cache causes a full
cold re-fetch — users see the spinner on every page change even when the
data period hasn't changed.

**Rule:** For a low-traffic internal dashboard whose hot data fits in one
process's RAM, use **`--workers 1 --threads N`** (single process, N threads).
One shared cache means any page visit that fills the cache benefits all
subsequent page navigations in the same session.

**Why N threads still gives concurrency:** the Sheets fetch is I/O-bound;
gthread threads release the GIL during HTTP waits, so `/health` and other
quick requests are served concurrently. The `_fetch_lock` already serialises
concurrent cold fills so only one Sheets fetch runs at a time.

**Cache TTL:** `_DATA_TTL = 300.0` (5 min) so a browsing session across
Overview → Plant → Machine → Reports stays warm throughout.

## Cold-start read time must be parallel, not serial

The landing route `/` defaults to period `last_updated` (sub-monthly) → it reads
**daily** sheets for every plant, not monthly. On a *cold* cache that first read
must finish inside the proxy/browser patience window or the published page just
spins forever (`/health` stays instant, so the deploy is "healthy" while users
see nothing).

**Rule:** the live-sheet loaders must fan out their independent workbook reads
concurrently. Serial reads (one `urllib` GET after another, each up to a 30s
timeout) across ~6 plants × 2 months pushed the cold load **>150s**;
parallelizing with `ThreadPoolExecutor` cut it to **~8s** for the same 2533
daily records.

**How to apply:**
- `_load_live_monthly` fans `_load_annual_family` across `ANNUAL_SOURCES`;
  `get_daily_records` fans `_load_daily_cached` across `(plant, ym)` pairs.
  Always gather results then process them in the ORIGINAL source order so
  warnings/reports stay deterministic.
- The daily cache uses **per-key** single-flight locks (`_daily_key_lock`), not
  the one global `_fetch_lock` — a single lock would re-serialise the fan-out.
  Distinct keys load in parallel; duplicate concurrent fetches of the SAME key
  still collapse. (Monthly stays on the single `_fetch_lock` — it's one payload.)
- No deadlock: daily workers may call `_grid_ideal_for → _live_payload()` (which
  takes `_fetch_lock`) while holding a daily key lock, but nothing acquires a
  daily key lock while holding `_fetch_lock`, so there is no lock-order cycle.
- Parallel bursts can trip Google's per-user read quota → **429**. `_api_get`
  retries 429/500/503 with exponential backoff + jitter (honouring
  `Retry-After`); 401/403/404 are permanent and surface immediately.
- `_api_get` must catch raw socket-level `OSError` (e.g. `TimeoutError` from
  `ssl.read` during `getresponse()`), NOT only `HTTPError`/`URLError`. A read
  timeout that fires mid-response is a bare `TimeoutError`/`socket.timeout`
  (subclass of `OSError`, NOT of `URLError`), so without an explicit `OSError`
  handler it escapes `_api_get` **unwrapped**. That matters because the per-pair
  isolation in `get_daily_records` only catches `SheetReadError` — an unwrapped
  `TimeoutError` from a single workbook then 500s the WHOLE page (dev *and*
  prod), and the very next warm-cache request returns 200, so it looks
  intermittent. Rule: every transient network failure must funnel through the
  same retry-then-wrap-in-`SheetReadError` path so callers can degrade per-file.
- `_startup_warmup` (daemon thread, sleeps 3s so gunicorn binds first) pre-warms
  monthly then the two most recent daily months — keep monthly first because the
  daily ideal-baseline lookup reads the cached monthly grid.
