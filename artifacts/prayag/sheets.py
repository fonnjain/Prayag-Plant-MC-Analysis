"""
Data layer: live Google Sheets reader (via the Replit Google Sheets connection)
plus a deterministic demo fallback. No data passes through any AI model.

Reads use the Replit "Google Sheets" connector (blueprint id: google-sheet):
we fetch a short-lived OAuth access token from the Replit connectors API and
reuse it until shortly before it expires, then call the public Google Sheets
REST API directly, reading each workbook by its pinned file ID (see sources.py).
"""
from __future__ import annotations
import os
import re
import json
import time
import random
import datetime
import urllib.request
import urllib.parse
import urllib.error
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

logger = logging.getLogger("prayag.sheets")

from metrics import Record
import parsers
import sources
import baselines
import store as _store

# ---------------------------------------------------------------------------
# Connection + token (cached within the process until near expiry)
# ---------------------------------------------------------------------------
_token_cache: dict = {"token": None, "exp": 0.0}
_data_cache: dict = {}          # months_key -> (ts, payload)
_DATA_TTL = 900.0               # seconds (15 min) on-demand fallback TTL. The
                                # always-on background refresher (bottom of file)
                                # refills well inside this window, so warm-cache
                                # hits are the norm; this TTL only governs the
                                # fallback when no refresher runs (e.g. autoscale).
_REFRESH_INTERVAL = 600.0       # seconds (10 min) — always-on background sync
                                # cadence. Effective only on an always-running
                                # (Reserved VM) deployment; idle/harmless on a
                                # scale-to-zero one (the process sleeps).
_last_fetch_status: dict = {}   # stale/failed info from the most recent live attempt
_sync_state: dict = {                      # live/background-sync observability
    "last_ok_ts": 0.0,                     # epoch of the last successful live read
    "last_attempt_ts": 0.0,                # epoch of the last background refresh attempt
    "last_error": None,                    # message of the most recent background failure
}
# Single-flight lock: under threaded gunicorn workers (gthread) concurrent cold
# requests would each run the full, slow Sheets fetch (a cache stampede). The
# lock serialises fills so the first request does the work and the rest reuse
# its result. Warm cache hits are checked BEFORE the lock, so they never block.
_fetch_lock = threading.Lock()


class SheetReadError(RuntimeError):
    """Raised when a real Google Sheet is configured but cannot be read."""


def _connector_available() -> bool:
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    has_token = bool(os.environ.get("REPL_IDENTITY") or os.environ.get("WEB_REPL_RENEWAL"))
    return bool(host and has_token)


def is_demo_mode() -> bool:
    """Live mode needs an authorized Google Sheets connection; else demo data."""
    return not _connector_available()


def last_fetch_status() -> dict:
    """Return info about the most recent live fetch attempt.

    Keys (all optional / may be absent):
      stale          bool  – True when serving cached data because the live
                             fetch failed.
      stale_age_seconds int – seconds since the cached snapshot was taken.
      stale_error    str  – the error message from the failed live fetch.
      failed_plants  list – plants that could not be loaded in the latest
                            (partial or stale) fetch; each entry is a dict
                            with keys ``plant``, ``title``, ``error``.
    """
    return dict(_last_fetch_status)


def clear_caches() -> None:
    """Drop every cached sheet payload so the next read fetches live data.

    Used by the manual "Refresh" action. The auth token cache is left intact
    (it is keyed to real expiry, not the data TTL) so a refresh re-reads the
    sheets without needlessly re-authenticating.  Also clears the shared
    Postgres L2 cache so other gunicorn workers pick up fresh data too.
    """
    _data_cache.clear()
    _daily_cache.clear()
    _last_fetch_status.clear()
    try:
        _store.pg_cache_clear()
    except Exception:
        pass


def _mark_synced() -> None:
    """Stamp the time of a successful live read from Google Sheets."""
    _sync_state["last_ok_ts"] = time.time()


def sync_status() -> dict:
    """Report when live data was last successfully pulled, and the auto-refresh
    cadence. ``last_ok_ts`` is 0 until the first successful read.

    Keys:
      available       bool – True once at least one live read has succeeded.
      last_ok_ts      float – epoch seconds of that last success (0 if none).
      age_seconds     int   – seconds since the last success (None if none).
      interval_seconds int  – the background refresh cadence.
      auto            bool  – True when live mode (the refresher is meaningful).
    """
    ok = _sync_state.get("last_ok_ts", 0.0)
    now = time.time()
    return {
        "available": bool(ok),
        "last_ok_ts": ok,
        "age_seconds": int(now - ok) if ok else None,
        "interval_seconds": int(_REFRESH_INTERVAL),
        "auto": not is_demo_mode(),
        "last_error": _sync_state.get("last_error"),
    }


def _fetch_token() -> Tuple[Optional[str], float]:
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_renewal = os.environ.get("WEB_REPL_RENEWAL")
    if repl_identity:
        xtoken = "repl " + repl_identity
    elif web_renewal:
        xtoken = "depl " + web_renewal
    else:
        return None, 0.0
    if not host:
        return None, 0.0

    url = (
        f"https://{host}/api/v2/connection"
        "?include_secrets=true&connector_names=google-sheet"
    )
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "X_REPLIT_TOKEN": xtoken}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.URLError, ValueError, OSError) as e:
        # URLError covers DNS/connect failures; ValueError covers a malformed
        # JSON body; the bare OSError catches a raw socket-level TimeoutError
        # (read timed out mid-response) which is NOT a URLError and would
        # otherwise escape unwrapped and 500 the page before any sheet is read.
        raise SheetReadError(
            "Couldn't verify the Google Sheets connection. "
            "Please reconnect it and try again."
        ) from e

    items = data.get("items", [])
    if not items:
        return None, 0.0
    settings = items[0].get("settings", {}) or {}
    token = settings.get("access_token")
    expires_at = settings.get("expires_at")
    if not token:
        oauth = settings.get("oauth", {}) or {}
        creds = oauth.get("credentials", {}) if isinstance(oauth, dict) else {}
        token = creds.get("access_token")
        expires_at = expires_at or creds.get("expires_at") or creds.get("expiry_date")

    # Resolve expiry to an epoch seconds value; default to a short TTL.
    exp_epoch = time.time() + 240.0
    if isinstance(expires_at, (int, float)):
        exp_epoch = expires_at / 1000.0 if expires_at > 1e12 else float(expires_at)
    elif isinstance(expires_at, str):
        try:
            exp_epoch = datetime.datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            pass
    return token, exp_epoch


def _get_access_token() -> Optional[str]:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["exp"] - 60:
        return _token_cache["token"]
    token, exp = _fetch_token()
    if token:
        _token_cache["token"] = token
        _token_cache["exp"] = exp
    return token


# ---------------------------------------------------------------------------
# Generic Sheets REST helpers
# ---------------------------------------------------------------------------
_API_MAX_RETRIES = 4    # total attempts on a throttle/transient error


def _api_get(url: str, token: str) -> dict:
    """GET a Google Sheets API endpoint, retrying transient throttle errors.

    Reads are fanned out across threads (see _load_live_monthly /
    get_daily_records), so a burst can briefly exceed Google's per-user read
    quota and come back 429 (or a transient 5xx). Those are retried with
    exponential backoff (honouring a Retry-After header when present) instead of
    failing the whole load. 401/403/404 are permanent and surface immediately.
    """
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    for attempt in range(_API_MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise SheetReadError("Spreadsheet or tab not found (404).") from e
            if e.code in (401, 403):
                raise SheetReadError(
                    "The Google account doesn't have access to a configured "
                    "spreadsheet, or the connection needs to be re-authorized."
                ) from e
            if e.code in (429, 500, 503) and attempt < _API_MAX_RETRIES - 1:
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    delay = float(retry_after) if retry_after else 0.0
                except ValueError:
                    delay = 0.0
                # Exponential backoff with jitter, floored by any Retry-After.
                delay = max(delay, (2 ** attempt) + random.uniform(0, 0.5))
                time.sleep(delay)
                continue
            raise SheetReadError(f"Google Sheets API error ({e.code}).") from e
        except urllib.error.URLError as e:
            if attempt < _API_MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                continue
            raise SheetReadError("Couldn't reach Google Sheets. Please try again.") from e
        except OSError as e:
            # A read/connect timeout that fires mid-response (or a dropped/reset
            # connection) surfaces as a raw socket-level OSError — e.g.
            # TimeoutError from ssl.read during getresponse() — NOT a URLError.
            # If it isn't caught here it escapes _api_get unwrapped, so the
            # per-pair isolation in get_daily_records (which only catches
            # SheetReadError) can't degrade gracefully and the whole page 500s.
            # Treat it as transient: retry with backoff, then wrap in
            # SheetReadError so callers isolate the one failing workbook.
            if attempt < _API_MAX_RETRIES - 1:
                time.sleep((2 ** attempt) + random.uniform(0, 0.5))
                continue
            raise SheetReadError(
                "Couldn't reach Google Sheets (the connection timed out or was "
                "dropped). Please try again."
            ) from e
    raise SheetReadError("Couldn't reach Google Sheets. Please try again.")


def list_tabs(file_id: str, token: str) -> List[str]:
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}"
        "?fields=sheets.properties.title"
    )
    data = _api_get(url, token)
    return [s.get("properties", {}).get("title", "") for s in data.get("sheets", [])]


def read_values(file_id: str, tab: str, token: str) -> List[list]:
    rng = urllib.parse.quote(tab, safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values/{rng}"
    return _api_get(url, token).get("values", []) or []


def drive_file_meta(file_id: str, token: str) -> dict:
    """Fetch file metadata (modifiedTime, size, name) via Drive API v3.

    Works for files the app has access to (drive.file scope). Returns {} on
    any error — this is best-effort enrichment for the manifest only and must
    never block a page render.
    """
    url = (
        f"https://www.googleapis.com/drive/v3/files/{file_id}"
        "?fields=modifiedTime%2Csize%2Cname"
    )
    try:
        data = _api_get(url, token)
        return {
            "modified_time": data.get("modifiedTime"),
            "file_size_bytes": int(data["size"]) if data.get("size") else None,
            "file_name": data.get("name"),
        }
    except Exception:
        return {}


def batch_get(file_id: str, tabs: List[str], token: str) -> dict:
    """Return {tab_title: value_matrix} for many tabs in one HTTP call."""
    if not tabs:
        return {}
    q = "&".join("ranges=" + urllib.parse.quote(t, safe="") for t in tabs)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values:batchGet?{q}"
    data = _api_get(url, token)
    out = {}
    for tab, vr in zip(tabs, data.get("valueRanges", [])):
        out[tab] = vr.get("values", []) or []
    return out


# ---------------------------------------------------------------------------
# Live monthly load — annual M/C summary workbooks
# ---------------------------------------------------------------------------
def _load_annual_family(src: dict, token: str) -> Tuple[List[Record], dict]:
    """Read one annual family workbook → (records, source_report)."""
    file_id = src["file_id"]
    tabs = list_tabs(file_id, token)
    detail_tabs = [t for t in tabs if parsers.DETAIL_TAB_RE.match(t.strip())]

    want = list(detail_tabs)
    if src["tab"] in tabs:
        want.append(src["tab"])
    matrices = batch_get(file_id, want, token)

    records: List[Record] = []
    for t in detail_tabs:
        records.extend(parsers.parse_mc_detail(
            matrices.get(t, []),
            plant=src["plant"],
            segment=src["segment"],
            unit=src["unit"],
            source_file=file_id,
            source_tab=t,
        ))

    # Reconcile summed detail output vs the grid TOTAL row.
    recon = None
    grid_total = parsers.grid_total_output(matrices.get(src["tab"], []))
    detail_total = sum(r.total_count for r in records)
    if grid_total is not None and grid_total > 0:
        diff = abs(detail_total - grid_total) / grid_total
        recon = {
            "grid_total": round(grid_total, 1),
            "detail_total": round(detail_total, 1),
            "diff_pct": round(diff * 100, 2),
            "ok": diff <= 0.02,
        }

    months = sorted({r.period for r in records})
    report = {
        "family": src["family"],
        "title": src["title"],
        "file_id": file_id,
        "tab": src["tab"],
        "detail_tabs": detail_tabs,
        "grain": "monthly",
        "months_available": months,
        "record_count": len(records),
        "segment": src["segment"],
        "plant": src["plant"],
        "reconcile": recon,
        "field_map": parsers.MC_DETAIL_FIELD_MAP,
    }
    return records, report


def _load_live_monthly(token: str) -> dict:
    all_records: List[Record] = []
    reports: List[dict] = []
    warnings: List[str] = []
    failed_plants: List[dict] = []

    # Read each family workbook concurrently — they are independent network
    # round-trips, so fanning them out turns a serial chain (one 30 s timeout
    # after another) into a single slowest-call wall time. Results are gathered
    # and then processed in source order so warnings/reports stay deterministic.
    def _try_family(src):
        try:
            return src, _load_annual_family(src, token), None
        except SheetReadError as e:
            return src, None, e

    max_workers = min(len(sources.ANNUAL_SOURCES), 8) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(_try_family, sources.ANNUAL_SOURCES))

    for src, loaded, err in results:
        if err is not None:
            failed_plants.append({
                "plant": src["plant"],
                "title": src["title"],
                "error": str(err),
            })
            warnings.append(
                f"{src['title']}: could not load ({err}). "
                "Figures for this plant are absent from this view."
            )
            continue
        recs, report = loaded
        all_records.extend(recs)
        reports.append(report)

        # Surface silent layout drift: a family that parses nothing despite
        # having detail tabs almost certainly means the parser/header detection
        # broke, not that the factory produced zero output.
        if report["record_count"] == 0:
            if report["detail_tabs"]:
                warnings.append(
                    f"{report['title']}: found {len(report['detail_tabs'])} machine "
                    "tab(s) but parsed 0 rows — sheet layout may have changed."
                )
            else:
                warnings.append(
                    f"{report['title']}: no machine (M/C-n) tabs found in the workbook."
                )

        recon = report.get("reconcile")
        if recon is None and report["record_count"] > 0:
            # Reconciliation could not run — the grid TOTAL row/tab was missing
            # or unreadable, so we cannot cross-check the parsed totals.
            warnings.append(
                f"{report['title']}: could not read the grid TOTAL row, so parsed "
                "totals were not reconciled against the source."
            )
        elif recon and not recon["ok"]:
            warnings.append(
                f"{report['title']}: month rows sum to {recon['detail_total']:.0f} "
                f"but grid TOTAL is {recon['grid_total']:.0f} "
                f"({recon['diff_pct']:.1f}% off)"
            )

    return {
        "records": all_records,
        "reports": reports,
        "recon_warnings": warnings,
        "grain": "monthly",
        "failed_plants": failed_plants,
    }


def _live_payload() -> dict:
    """Cached full live monthly payload (all families, all FY months).

    Recovery behaviour when the live fetch fails:
    - Per-plant failures (partial read): ``_load_live_monthly`` already
      continues past them; the returned payload contains whatever plants
      succeeded plus a ``failed_plants`` list.
    - Full connector failure (auth down, network, etc.): if a stale cache
      entry exists it is returned with ``stale=True`` and metadata so the
      UI can show a banner; otherwise ``SheetReadError`` is re-raised.
    """
    global _last_fetch_status
    now = time.time()
    cached = _data_cache.get("live")
    if cached and now - cached[0] < _DATA_TTL:
        return cached[1]

    with _fetch_lock:
        # Re-check: another thread may have filled the cache while we waited.
        now = time.time()
        cached = _data_cache.get("live")
        if cached and now - cached[0] < _DATA_TTL:
            return cached[1]
        return _fetch_live_payload(now, cached)


def _fetch_live_payload(now: float, cached) -> dict:
    """Do the actual (slow) live monthly fetch and cache it. Always called while
    holding ``_fetch_lock`` so only one thread fetches at a time.

    Before hitting Google Sheets we check the shared Postgres L2 cache.  Any
    gunicorn worker that previously fetched from Sheets will have written the
    result there, so a cold worker can serve data without a Sheets round-trip.
    """
    global _last_fetch_status

    # --- L2: shared Postgres cache (fast; avoids Sheets round-trip) ----------
    try:
        pg_hit = _store.pg_cache_read("monthly_live", _DATA_TTL)
        if pg_hit is not None:
            _data_cache["live"] = (now, pg_hit)
            _last_fetch_status = {
                "stale": False,
                "failed_plants": pg_hit.get("failed_plants", []),
            }
            return pg_hit
    except Exception:
        pass  # fall through to live fetch

    # --- L3: Google Sheets (slow; single-flight under _fetch_lock) -----------
    try:
        token = _get_access_token()
        if not token:
            raise SheetReadError(
                "The Google Sheets connection isn't authorized. "
                "Reconnect it from the integrations panel and try again."
            )
        payload = _load_live_monthly(token)
        _data_cache["live"] = (now, payload)
        _mark_synced()
        _last_fetch_status = {
            "stale": False,
            "failed_plants": payload.get("failed_plants", []),
        }
        # Write to shared Postgres cache so other workers skip the Sheets trip.
        try:
            _store.pg_cache_write("monthly_live", payload)
        except Exception:
            pass
        return payload
    except SheetReadError as exc:
        if cached:
            age_s = int(now - cached[0])
            stale = dict(cached[1])   # shallow copy — don't mutate cached entry
            stale["stale"] = True
            stale["stale_age_seconds"] = age_s
            stale["stale_error"] = str(exc)
            _last_fetch_status = {
                "stale": True,
                "stale_age_seconds": age_s,
                "stale_error": str(exc),
                "failed_plants": stale.get("failed_plants", []),
            }
            return stale
        # No cache at all — let the Flask error handler surface it clearly.
        _last_fetch_status = {"stale": False, "failed_plants": []}
        raise


def get_records(months: List[str]) -> Tuple[List[Record], List[dict], List[str]]:
    """Return (records filtered to ``months``, source reports, recon warnings).

    Falls back to deterministic demo data when no connection is available.
    """
    if is_demo_mode():
        recs = _demo_records_for_months(months)
        return recs, _demo_reports(), []

    payload = _live_payload()
    wanted = set(months)
    recs = [r for r in payload["records"] if r.period in wanted]
    return recs, payload["reports"], payload["recon_warnings"]


# ---------------------------------------------------------------------------
# Live daily load — per-month workbooks (true day-level data)
# ---------------------------------------------------------------------------
_daily_cache: dict = {}          # (plant, ym) -> (ts, [(records, report), ...])
# Per-key single-flight locks for the daily cache. The monthly path uses one
# global lock (a single payload), but daily reads are many independent
# (plant, ym) workbooks — a single lock would serialise them and reintroduce the
# slow cold chain. A lock per key lets distinct keys load concurrently while
# still collapsing duplicate concurrent fetches of the SAME key.
_daily_key_locks: dict = {}
_daily_locks_guard = threading.Lock()


def _daily_key_lock(key) -> threading.Lock:
    with _daily_locks_guard:
        lock = _daily_key_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _daily_key_locks[key] = lock
        return lock


def _load_daily_cached(plant: str, ym: str, token: str):
    """Return cached results for one (plant, ym), fetching once under a per-key
    lock on a cold miss. Safe to call from many threads concurrently.

    Cache hierarchy:
      L1 – in-process _daily_cache  (per worker, sub-millisecond)
      L2 – shared Postgres sheet_cache  (cross-worker, ~1-5 ms)
      L3 – Google Sheets live read  (slow, 5-30 s per workbook)
    """
    key = (plant, ym)
    cached = _daily_cache.get(key)
    if cached and time.time() - cached[0] < _DATA_TTL:
        return cached[1]
    pg_key = f"daily_{plant}_{ym}"
    with _daily_key_lock(key):
        # Re-check L1 — another thread may have filled it while we waited.
        cached = _daily_cache.get(key)
        if cached and time.time() - cached[0] < _DATA_TTL:
            return cached[1]
        # L2: shared Postgres cache — avoids re-fetching across workers.
        try:
            pg_hit = _store.pg_cache_read(pg_key, _DATA_TTL)
            if pg_hit is not None:
                _daily_cache[key] = (time.time(), pg_hit)
                return pg_hit
        except Exception:
            pass
        # L3: live Sheets read.
        results = _load_daily(plant, ym, token)
        _daily_cache[key] = (time.time(), results)
        _mark_synced()
        # Populate Postgres so sibling workers skip the Sheets trip.
        try:
            _store.pg_cache_write(pg_key, results)
        except Exception:
            pass
        return results

# Per-plant daily layout config. Each workbook can emit one OR MORE logical
# plants (the PIPE workbook also holds the Moulding tab). ``layout`` selects the
# parser: ``matrix`` = wide per-date grid (current parser), ``long`` = one row
# per machine per date (Report-11/Report-12). ``long`` carries the column specs
# for parsers.parse_daily_long. ``ideal_col`` (matrix only) names an in-sheet
# per-machine ideal-hours column used as a utilisation baseline when there is no
# monthly grid (PTMT). Tabs/columns are detected at read time; nothing assumed.
_DAILY_LAYOUTS: dict = {
    "PIPE": [
        {
            "emit": "PIPE", "tab": "Report-11", "layout": "long",
            "long": dict(
                machine_col=("eq", "MACHINE NO."),
                out_col=("eq", "WEIGHT"),
                run_col=("startswith", "RUNNING HOUR"),
                rej_col=("eq", "ACTUAL WT (KG)"),
            ),
        },
        {
            "emit": "MOULDING", "tab": "Report-12", "layout": "long",
            "long": dict(
                machine_col=("startswith", "MOULDING MACHI"),
                out_col=("contains", "WT IN KGS"),
                runner_col=("startswith", "RUNNER PRODUCE"),
                machine_prefix="MOULDING ",
            ),
        },
    ],
    # GARDEN keeps one tab PER MACHINE ("MACHINE 2", ...), each a date×production
    # block (no run hours); its single "Daily Report" tab is empty, so real output
    # lives in these per-machine tabs.
    "GARDEN": [{
        "emit": "GARDEN", "layout": "blocks",
        "tab_re": r"^MACHINE\s*\d+$", "machine_prefix": "GARDEN M/C - ",
    }],
    # HDPE has a populated "Daily Report" matrix (one row per machine, per-date
    # Run Hours / Output / Rejection) — the same family as PTMT Report-5 — so it is
    # read there, NOT from the per-machine MACHINE 1-6 block tabs (the DANA M/C tab
    # is a granulator/support and is excluded). HDPE supplies its OWN per-machine
    # baselines in that matrix: "Ideal Output" (kg/hr) drives output-efficiency and
    # "M/C Run Hour" (monthly available hours) drives utilisation — so HDPE needs no
    # baselines.json entry. The machine id is the canonical column ("MACHINE" =
    # M/C-1…6); the alias column to its left is ignored by the matrix parser.
    "HDPE": [{
        "emit": "HDPE", "tab": "Daily Report", "layout": "matrix",
        "ideal_output_col": ("contains", "IDEAL OUTPUT"),
        "ideal_hours_col": ("contains", "M/C RUN HOUR"),
        "summary_mc_header": ("eq", "MACHINE"),
    }],
    "PTMT": [{
        "emit": "PTMT", "tab": "Report-5", "layout": "matrix",
        "ideal_col": ("contains", "IDEAL HOUR"),
    }],
    # TANK records per-ITEM production (no machine dimension) on "PROD. REPORT".
    "TANK": [{"emit": "TANK", "tab": "PROD. REPORT", "layout": "tank"}],
}

# PTMT runs several distinct processes that should be compared within their own
# group, not against the whole plant. The grinder/regrind lines are FINISHING:
# their KG is regrind, never added to plant output.
_PTMT_TAB_NUM = re.compile(r"(\d+)")


def _ptmt_group(code: str) -> Tuple[str, bool]:
    """(segment, is_finishing) for a PTMT machine label.

    The authoritative roster (sources.PTMT_GROUPS) is the source of truth for
    which process group each machine belongs to. Only codes not in the roster
    fall back to heuristic string routing — so a never-before-seen machine is
    still grouped sensibly instead of crashing.
    """
    authoritative = sources.ptmt_group(code)
    if authoritative is not None:
        return authoritative
    up = str(code).strip().upper()
    if "GRIND" in up:
        return "PTMT – Grinding", True
    if "BLOW" in up:
        return "PTMT – Blow Moulding", False
    if "CORRUGAT" in up:
        return "PTMT – Corrugator", False
    if up.startswith("N-") or up.startswith("N "):
        return "PTMT – Injection (N-line)", False
    return "PTMT – Injection (standard)", False


_MC_RE = re.compile(r"M\s*/?\s*C\s*-?\s*(\d+)", re.I)
_MACHINE_RE = re.compile(r"\bMACHINE\s*-?\s*(\d+)\b", re.I)


def _mc_key(label) -> Optional[int]:
    """Primary-extruder join key from a machine label.

    Matches only the main machines — ``M/C-n`` (Pipe/grid) or ``MACHINE-n``
    (Garden) — and returns their number. Auxiliary/die rows in the daily sheet
    (SOCKET-n, Grinder-1, die codes like ``A02``) return None so they are NOT
    mis-joined onto a monthly machine of the same trailing number.
    """
    m = _MC_RE.search(str(label))
    if m:
        return int(m.group(1))
    m = _MACHINE_RE.search(str(label))
    if m:
        return int(m.group(1))
    return None


def _daily_plants() -> List[str]:
    """Workbook plants we read at daily grain — every plant that has a daily
    file configured. Each may emit one or more logical plants (see
    ``_DAILY_LAYOUTS``); plants with no monthly grid (PTMT/TANK) are still read,
    falling back to an in-sheet or no baseline rather than being skipped."""
    return [p for p in sources.DAILY_SOURCES
            if p in _DAILY_LAYOUTS and sources.DAILY_SOURCES[p].get("files")]


def _daily_seg_unit(plant: str) -> Tuple[str, str]:
    for s in sources.ANNUAL_SOURCES:
        if s["plant"] == plant:
            return s["segment"], s["unit"]
    return plant.title(), "kg"


def _has_date_header(values: List[list]) -> bool:
    """True if a DATE column header exists in the first rows — used to tell a
    genuine no-production period (header present, no data) from a parse failure
    (no recognisable header at all)."""
    return any(
        str(c).strip().upper() == "DATE"
        for row in values[:12] for c in row
    )


def _matrix_has_dates(values: List[list]) -> bool:
    """True if a wide per-date matrix header row is recognisable (≥2 date labels).

    The matrix layout (HDPE/PTMT "Daily Report") labels its date columns "Jun, 1"
    rather than a literal "DATE" cell, so ``_has_date_header`` can't see it. This
    distinguishes a recognised-but-idle report (header present, all zeros → no
    rows) from a genuine parse failure (no date header at all)."""
    return any(
        sum(1 for c in row if parsers._day_from_label(c) is not None) >= 2
        for row in values[:8]
    )


def _emit_blocks(emit: str, ym: str, file_id: str, spec: dict, token: str,
                 seg: str, unit: str, report: dict) -> Tuple[List[Record], dict]:
    """Emit daily rows from per-machine 'block' tabs (GARDEN/HDPE).

    One tab == one machine. These tabs carry output + rejection but NO run hours,
    so utilisation/efficiency are left hidden (never shown as 0%). Distinguishes a
    genuine no-production period from a layout that could not be parsed."""
    tab_re = re.compile(spec["tab_re"], re.I)
    prefix = spec.get("machine_prefix", f"{emit} M/C - ")
    tabs = list_tabs(file_id, token)
    machine_tabs = [t for t in tabs if tab_re.match(str(t).strip())]
    report["tab"] = ", ".join(machine_tabs) if machine_tabs else spec["tab_re"]
    if not machine_tabs:
        report["warning"] = (
            f"{emit} {ym}: no per-machine tabs matching '{spec['tab_re']}' were "
            "found in the daily workbook."
        )
        return [], report

    raw: List[Record] = []
    any_header = False
    for t in machine_tabs:
        values = read_values(file_id, t, token)
        if _has_date_header(values):
            any_header = True
        mnum = _PTMT_TAB_NUM.search(str(t))
        machine = f"{prefix}{mnum.group(1)}".strip() if mnum else f"{prefix}{t}".strip()
        raw.extend(parsers.parse_daily_blocks(
            values, plant=emit, segment=seg, unit=unit, year_month=ym,
            source_file=file_id, source_tab=t, machine=machine,
        ))

    for r in raw:
        r.ideal_hours = 0.0      # no run hours in this layout → ratio hidden
        r.ideal_output = 0.0
        r.ideal_source = "none"

    report["detail_tabs"] = sorted({r.machine for r in raw})
    report["record_count"] = len(raw)
    report["columns_seen"] = list(machine_tabs)   # tab names serve as the column inventory

    if not raw:
        report["warning"] = (
            f"{emit} {ym}: machine tabs are present but no production has been "
            "recorded for this period yet."
            if any_header else
            f"{emit} {ym}: the machine tabs could not be parsed "
            "(date/output layout not recognised)."
        )
    else:
        report["warning"] = (
            f"{emit} {ym}: daily file records output only (no run hours), so "
            "utilisation/efficiency are not available — output is shown."
        )
    return raw, report


def _emit_tank(emit: str, ym: str, file_id: str, spec: dict, token: str,
               seg: str, report: dict) -> Tuple[List[Record], dict]:
    """Emit daily rows from the Tank per-item PROD. REPORT.

    Tank output is recorded per item (no machine, no run hours) in pieces, so
    rows carry ``machine=""`` and ``unit="pcs"``; utilisation/efficiency stay
    hidden. Distinguishes a genuine no-production period from a parse failure."""
    tab = spec["tab"]
    tabs = list_tabs(file_id, token)
    actual = next((t for t in tabs if str(t).strip().upper() == tab.upper()), None)
    if actual is None:
        report["warning"] = f"{emit} {ym}: daily tab '{tab}' not found in the workbook."
        return [], report
    report["tab"] = actual

    values = read_values(file_id, actual, token)
    report["columns_seen"] = [
        str(c).strip() for c in (values[0] if values else []) if str(c).strip()
    ][:40]
    raw = parsers.parse_tank_prod(
        values, plant=emit, segment=seg, unit="pcs", year_month=ym,
        source_file=file_id, source_tab=actual,
    )
    for r in raw:
        r.ideal_hours = 0.0
        r.ideal_output = 0.0
        r.ideal_source = "none"

    report["detail_tabs"] = sorted({r.mould for r in raw if r.mould})
    report["record_count"] = len(raw)

    if not raw:
        report["warning"] = (
            f"{emit} {ym}: production report is present but no output has been "
            "recorded for this period yet."
            if _has_date_header(values) else
            f"{emit} {ym}: the production report could not be parsed "
            "(layout not recognised)."
        )
    else:
        report["warning"] = (
            f"{emit} {ym}: tank output is recorded per item (no machine or run "
            "hours), so utilisation/efficiency are not available — output is shown."
        )
    return raw, report


def _emit_daily(emit: str, ym: str, file_id: str, spec: dict,
                token: str) -> Tuple[List[Record], dict]:
    """Read and baseline one logical plant's daily rows from a workbook tab.

    Ideal-denominator precedence per machine: in-sheet ideal-OUTPUT rate (a plant
    that publishes its own per-machine Ideal Output, e.g. HDPE) →
    in-sheet ideal-HOURS column (e.g. PTMT) → config baseline (baselines.json) →
    none ("no baseline set"). The monthly grid's "Ideal Hours" is a flat
    placeholder (500 for every machine), NOT a real planned-hours baseline, so it
    is deliberately NOT a precedence step — PIPE/MOULDING have no real shift-pattern
    baseline and correctly show "baseline not set". A no-baseline machine still
    reports run hours + output; its ratio is suppressed downstream rather than shown
    as a misleading figure against a placeholder.
    """
    seg, unit = _daily_seg_unit(emit)
    layout = spec["layout"]
    tab = spec.get("tab", "")
    report = {
        "family": emit.lower(),
        "title": f"{sources.PLANT_NAMES.get(emit, emit)} — daily ({ym})",
        "file_id": file_id,
        "tab": tab,
        "detail_tabs": [],
        "grain": "daily",
        "months_available": [ym],
        "record_count": 0,
        "segment": seg,
        "plant": emit,
        "reconcile": None,
        "warning": None,
    }

    # Per-machine block tabs (GARDEN/HDPE) and the per-item Tank report have their
    # own readers — they read several tabs / a different shape than the single-tab
    # matrix & long layouts handled below.
    if layout == "blocks":
        return _emit_blocks(emit, ym, file_id, spec, token, seg, unit, report)
    if layout == "tank":
        return _emit_tank(emit, ym, file_id, spec, token, seg, report)

    tabs = list_tabs(file_id, token)
    if tab not in tabs:
        report["warning"] = (
            f"{emit} {ym}: daily tab '{tab}' not found in the workbook."
        )
        return [], report

    values = read_values(file_id, tab, token)
    # Capture the header row for manifest schema checks.
    report["columns_seen"] = [
        str(c).strip() for c in (values[0] if values else []) if str(c).strip()
    ][:40]
    if layout == "long":
        raw = parsers.parse_daily_long(
            values, plant=emit, segment=seg, unit=unit, year_month=ym,
            source_file=file_id, source_tab=tab, **spec["long"],
        )
    else:
        raw = parsers.parse_daily_matrix(
            values, plant=emit, segment=seg, unit=unit, year_month=ym,
            source_file=file_id, source_tab=tab,
            mc_header_spec=spec.get("summary_mc_header"),
        )

    # PTMT runs several processes on one Report-5 matrix; route each machine to
    # its process group and flag grinder/regrind lines as finishing so their KG
    # is excluded from PTMT's plant output (compared within-group, not plant-wide).
    if emit == "PTMT":
        for r in raw:
            code = r.machine[len(emit) + 1:] if r.machine.startswith(emit + " ") else r.machine
            r.segment, r.is_finishing = _ptmt_group(code)

    # No rows extracted: a file that opened with a recognisable matrix header but
    # no production is IDLE (no output recorded yet) — never "missing". Only an
    # unrecognisable layout is a parse failure.
    if not raw:
        report["warning"] = (
            f"{emit} {ym}: the daily report is present but no production has been "
            "recorded for this period yet."
            if _matrix_has_dates(values) else
            f"{emit} {ym}: the daily report could not be parsed "
            "(date/output layout not recognised)."
        )
        report["record_count"] = 0
        return raw, report

    # Baseline sources, in precedence order. The monthly grid is deliberately NOT
    # a source: its "Ideal Hours" column is a flat placeholder (500 for every
    # machine), not a real per-machine planned-hours baseline, so using it would
    # manufacture a misleading utilisation/efficiency. PIPE/MOULDING therefore fall
    # through to "none" ("baseline not set") unless a real config baseline exists.
    summary_mc = spec.get("summary_mc_header", ("contains", "M/C NO"))
    sheet_ideal: dict = {}  # in-sheet ideal HOURS  (PTMT "IDEAL HOUR")    → utilisation
    sheet_rate: dict = {}   # in-sheet ideal OUTPUT rate, units/hr (HDPE) → efficiency
    sheet_hours: dict = {}  # in-sheet available HOURS/month (HDPE)       → utilisation
    if spec.get("ideal_col"):
        labels = parsers.parse_matrix_summary_col(
            values, header_spec=spec["ideal_col"], mc_header_spec=summary_mc)
        # Re-key onto the machine names the matrix parser emitted ("PLANT label").
        sheet_ideal = {f"{emit} {lbl}".strip(): hrs for lbl, hrs in labels.items()}
    if spec.get("ideal_output_col"):
        labels = parsers.parse_matrix_summary_col(
            values, header_spec=spec["ideal_output_col"], mc_header_spec=summary_mc)
        sheet_rate = {f"{emit} {lbl}".strip(): v for lbl, v in labels.items()}
    if spec.get("ideal_hours_col"):
        labels = parsers.parse_matrix_summary_col(
            values, header_spec=spec["ideal_hours_col"], mc_header_spec=summary_mc)
        sheet_hours = {f"{emit} {lbl}".strip(): v for lbl, v in labels.items()}

    # Active days per machine (full month) so per-day ideal hours reconcile to
    # the monthly figure.
    active: dict = {}
    for r in raw:
        active.setdefault(r.machine, set()).add(r.date)

    for r in raw:
        days = max(len(active.get(r.machine, ())), 1)
        if sheet_rate.get(r.machine, 0) > 0:
            # A plant that publishes its OWN per-machine "Ideal Output" rate in the
            # daily matrix (HDPE today) is authoritative — its in-sheet rate drives
            # efficiency and its in-sheet "M/C Run Hour" drives utilisation, so it
            # takes precedence over any monthly-grid baseline and needs no
            # baselines.json entry. Only HDPE populates sheet_rate, so other plants
            # (PIPE/MOULDING/GARDEN) keep monthly-grid precedence below.
            rate = sheet_rate[r.machine]
            r.ideal_rate = rate
            r.ideal_output = r.actual_hours * rate
            ih_month = sheet_hours.get(r.machine, 0.0)
            r.ideal_hours = (ih_month / days) if ih_month > 0 else 0.0
            r.ideal_source = "sheet"
        elif r.machine in sheet_ideal:
            r.ideal_hours = sheet_ideal[r.machine] / days
            r.ideal_output = 0.0  # no in-sheet output rate → efficiency hidden
            r.ideal_source = "sheet"
        else:
            base = baselines.resolve(emit, r.machine, ym)
            if base:
                r.ideal_hours = base["planned_hours"] / days
                if base.get("ideal_output") is not None:
                    rate = base["ideal_output"] / max(base["planned_hours"], 1e-9)
                    r.ideal_rate = rate
                    r.ideal_output = r.actual_hours * rate
                r.ideal_source = "config"
            else:
                r.ideal_hours = 0.0
                r.ideal_output = 0.0
                r.ideal_source = "none"

    report["detail_tabs"] = sorted({r.machine for r in raw})
    report["record_count"] = len(raw)

    # One summary line for machines without an efficiency baseline (not one per
    # machine). Their run hours + output still publish; only the ratio is hidden.
    total_m = len({r.machine for r in raw})
    no_base = sorted({r.machine for r in raw if r.ideal_source == "none"})
    if no_base:
        report["warning"] = (
            f"{emit} {ym}: {len(no_base)} of {total_m} machine(s) have no "
            "planned-hours baseline — run hours + output are shown but "
            "utilisation/efficiency are hidden."
        )
    return raw, report


def _load_daily(plant: str, ym: str, token: str) -> List[Tuple[List[Record], dict]]:
    """Read one workbook plant's daily file for month ``ym``.

    Returns a list of (records, report) — one per logical plant the workbook
    emits (the PIPE workbook emits both PIPE and Moulding)."""
    file_id = sources.DAILY_SOURCES.get(plant, {}).get("files", {}).get(ym)
    if not file_id:
        return []
    out: List[Tuple[List[Record], dict]] = []
    for spec in _DAILY_LAYOUTS.get(plant, []):
        recs, report = _emit_daily(spec["emit"], ym, file_id, spec, token)
        out.append((recs, report))
    return out


def get_daily_records(months: List[str]) -> Tuple[List[Record], List[dict], List[str]]:
    """True day-level Records for ``months`` across all daily-capable plants."""
    if is_demo_mode():
        recs = _demo_records_for_months(months)
        return recs, _demo_reports(), []

    token = _get_access_token()
    if not token:
        raise SheetReadError(
            "The Google Sheets connection isn't authorized. "
            "Reconnect it from the integrations panel and try again."
        )

    all_recs: List[Record] = []
    reports: List[dict] = []
    warnings: List[str] = []

    # Every (plant, ym) is an independent workbook read, so fetch them
    # concurrently. _load_daily_cached collapses duplicate concurrent fetches of
    # the same key under a per-key lock; results are reassembled in the original
    # (plant, ym) order below so warnings/reports stay deterministic.
    pairs = [
        (plant, ym)
        for plant in _daily_plants()
        for ym in months
        if ym in sources.DAILY_SOURCES[plant]["files"]
    ]
    by_pair: dict = {}
    failed_pairs: List[Tuple[str, str]] = []
    if pairs:
        max_workers = min(len(pairs), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_load_daily_cached, plant, ym, token): (plant, ym)
                for plant, ym in pairs
            }
            for fut, pair in futures.items():
                try:
                    by_pair[pair] = fut.result()
                except SheetReadError:
                    # One workbook failing (e.g. a transient 429 on a cold,
                    # multi-month read) must NOT nuke the whole period and force a
                    # fall-back to the summary grid. Record the gap as an honest
                    # warning and keep every workbook that did load. We only raise
                    # below if NOTHING loaded at all.
                    failed_pairs.append(pair)
    if failed_pairs and not by_pair:
        raise SheetReadError(
            "Couldn't read any daily workbook for this period "
            "(Google Sheets is throttling or unavailable). Please try again."
        )
    for plant, ym in failed_pairs:
        warnings.append(
            f"{sources.PLANT_NAMES.get(plant, plant)} daily ({ym}) couldn't be read this "
            "time (Google Sheets throttled or unavailable) — its figures are "
            "temporarily missing from this view. Try Refresh in a moment."
        )

    for pair in pairs:
        results = by_pair.get(pair, [])
        for recs, report in results:
            all_recs.extend(recs)
            if not report:
                continue
            reports.append(report)
            if report.get("warning"):
                warnings.append(report["warning"])
            # Daily-vs-grid reconciliation data is kept in report["reconcile"]
            # for the Sources diagnostic page, but is no longer emitted as a
            # user-visible warning — daily files are the authoritative source.
    return all_recs, reports, warnings


def detected_sources() -> List[dict]:
    """Reviewable mapping of every source the engine reads (monthly + daily)."""
    if is_demo_mode():
        return _demo_reports()
    reports = list(_live_payload()["reports"])
    # Document the daily workbooks too (descriptors only — no extra network).
    grid_plants = {s["plant"] for s in sources.ANNUAL_SOURCES}
    for plant in sources.DAILY_SOURCES:
        files = sources.DAILY_SOURCES[plant].get("files", {})
        if not files:
            continue
        seg, _unit = _daily_seg_unit(plant)
        emits = _DAILY_LAYOUTS.get(plant, [])
        base = {
            "family": f"{plant.lower()}-daily",
            "title": f"{sources.PLANT_NAMES.get(plant, plant)} — daily workbooks",
            "file_id": next(iter(files.values()), ""),
            "detail_tabs": [],
            "grain": "daily",
            "months_available": sorted(files.keys()),
            "record_count": 0,
            "segment": seg,
            "plant": plant,
            "reconcile": None,
            "field_map": parsers.MC_DETAIL_FIELD_MAP,
        }
        if not emits:
            reports.append({**base, "tab": "", "note": (
                "Daily file present but no layout is configured yet — not ingested.")})
            continue
        tabs = [e.get("tab") or e.get("tab_re", "") for e in emits]
        layouts = sorted({e["layout"] for e in emits})
        emit_names = [e["emit"] for e in emits]
        # Ideal-denominator precedence (matches _emit_daily): grid → in-sheet ideal → none.
        if any(e["emit"] in grid_plants for e in emits):
            note = "Daily grain; ideal rate/hours joined from the monthly grid baseline."
        elif any(e.get("ideal_col") for e in emits):
            note = "Daily tab carries an in-sheet ideal column; utilisation uses it directly."
        else:
            note = ("Daily grain; no monthly grid or in-sheet ideal — hours & output "
                    "are shown and the plant is flagged 'no baseline set'.")
        reports.append({**base, "tab": "/".join(tabs), "note": (
            note
            + (f" Emits: {', '.join(emit_names)}." if emit_names != [plant] else "")
            + (f" Layout: {'/'.join(layouts)}." if layouts else ""))})
    return reports


def months_with_data() -> List[str]:
    """All FY months that currently hold any real data (for the period engine)."""
    if is_demo_mode():
        return sorted({d for d in _demo_month_index()})
    return sorted({r.period for r in _live_payload()["records"]})


# ---------------------------------------------------------------------------
# Demo data (deterministic) — daily grain so OEE path is exercised offline
# ---------------------------------------------------------------------------
_DEMO_PLANTS = {
    "PIPE": [("PIPE EX-1", "Pipe", 280, "kg"), ("PIPE EX-2", "Pipe", 240, "kg")],
    "GARDEN": [("GARDEN M/C-1", "Garden Pipe", 180, "kg")],
    "HDPE": [("HDPE M/C-1", "HDPE", 260, "kg")],
    "MOULDING": [("MOULDING M/C-1", "Moulding", 450, "pcs"),
                 ("MOULDING M/C-2", "Moulding", 380, "pcs")],
}
_DEMO_REASONS = [
    "Mould Change", "Material Change", "Breakdown - Hydraulic",
    "Breakdown - Electrical", "Colour Change", "Power Failure", "",
]
_DEMO_SHIFTS = ["A", "B", "C"]


def _demo_records_range(from_iso: str, to_iso: str) -> List[Record]:
    rng = random.Random(42)
    rows: List[Record] = []
    start = datetime.date.fromisoformat(from_iso)
    end = datetime.date.fromisoformat(to_iso)
    day = start
    while day <= end:
        for plant, machines in _DEMO_PLANTS.items():
            for shift in _DEMO_SHIFTS:
                for mc_id, seg, ideal, unit in machines:
                    shift_len = 480
                    planned_stops = rng.choice([0, 15, 20, 30])
                    ppt = shift_len - planned_stops
                    downtime = rng.randint(0, int(ppt * 0.35))
                    run = ppt - downtime
                    reason = rng.choice(_DEMO_REASONS) if downtime > 0 else ""
                    run_hrs = run / 60.0
                    ideal_out = run_hrs * ideal
                    total = round(ideal_out * rng.uniform(0.70, 0.98), 2)
                    reject = round(total * (1 - rng.uniform(0.93, 0.995)), 2)
                    runner = round(total * rng.uniform(0.005, 0.02), 2) if unit == "pcs" else 0.0
                    rows.append(Record(
                        grain="daily", has_oee=True,
                        period=day.strftime("%Y-%m"), date=day.isoformat(),
                        plant=plant, segment=seg, machine=mc_id, unit=unit,
                        shift=shift, ideal_rate=ideal, shift_len_min=shift_len,
                        planned_stops_min=planned_stops, downtime_min=downtime,
                        downtime_reason=reason, total_count=total,
                        reject_count=reject, runner_lumps=runner,
                        planned_output=round(ideal * (ppt / 60.0) * 0.9, 2),
                        labour_cost=round(rng.uniform(800, 1800), 2),
                        power_cost=round(total * rng.uniform(2.5, 5.0), 2),
                        solar_cost=round(total * rng.uniform(0.4, 1.2), 2),
                        source_family=seg, source_file="demo", source_tab="demo",
                    ))
        day += datetime.timedelta(days=1)
    return rows


def _demo_month_index() -> List[str]:
    return ["2026-04", "2026-05", "2026-06"]


def _demo_records_for_months(months: List[str]) -> List[Record]:
    out: List[Record] = []
    for ym in months:
        if ym not in _demo_month_index():
            continue
        y, m = int(ym[:4]), int(ym[5:7])
        first = datetime.date(y, m, 1)
        if m == 12:
            nxt = datetime.date(y + 1, 1, 1)
        else:
            nxt = datetime.date(y, m + 1, 1)
        last = nxt - datetime.timedelta(days=1)
        out.extend(_demo_records_range(first.isoformat(), last.isoformat()))
    return out


def _demo_reports() -> List[dict]:
    reports = []
    for plant, machines in _DEMO_PLANTS.items():
        reports.append({
            "family": plant.lower(),
            "title": f"DEMO — {sources.PLANT_NAMES.get(plant, plant)}",
            "file_id": "demo",
            "tab": "demo daily log",
            "detail_tabs": [m[0] for m in machines],
            "grain": "daily",
            "months_available": _demo_month_index(),
            "record_count": 0,
            "segment": machines[0][1],
            "plant": plant,
            "reconcile": None,
        })
    return reports


# ---------------------------------------------------------------------------
# Startup cache warmup
# ---------------------------------------------------------------------------
def _recent_daily_months() -> List[str]:
    """The two most recently-relevant calendar months (this month + last) — the
    daily windows the dashboard lands on by default."""
    today = datetime.date.today()
    first_of_month = today.replace(day=1)
    prev = first_of_month - datetime.timedelta(days=1)
    return sorted({prev.strftime("%Y-%m"), today.strftime("%Y-%m")})


def _force_live_payload() -> dict:
    """Re-fetch the monthly payload under ``_fetch_lock`` and overwrite the cache,
    bypassing the TTL. Sharing the lock with ``_live_payload`` means a background
    refresh and a concurrent request never both fetch (single-flight), and the
    cache write stays coordinated — no unsynchronised pop-then-fetch race.
    """
    with _fetch_lock:
        now = time.time()
        cached = _data_cache.get("live")
        return _fetch_live_payload(now, cached)


def _refresh_once() -> None:
    """Force one full live re-pull (monthly grid + recent daily months) into the
    caches, bypassing the TTL so the data is genuinely current. Best-effort: each
    leg records its own error (surfaced via ``sync_status``) but never aborts the
    other. Shared by both the boot warmup and the always-on background refresher.

    Cache eviction is coordinated with the request path's own single-flight locks
    (``_fetch_lock`` for monthly via ``_force_live_payload``; each daily key's
    ``_daily_key_lock`` for the pop) so a refresh never races a request mid-write.
    """
    errors: list = []
    try:
        _force_live_payload()
    except Exception as exc:                       # noqa: BLE001 — best-effort leg
        errors.append(f"monthly: {exc}")
    recent = _recent_daily_months()
    # Evict the recent daily keys under their per-key lock so a concurrent
    # _load_daily_cached write is never clobbered, then force a fresh fetch.
    for key in [k for k in list(_daily_cache.keys()) if k[1] in recent]:
        with _daily_key_lock(key):
            _daily_cache.pop(key, None)
    try:
        get_daily_records(recent)
    except Exception as exc:                       # noqa: BLE001 — best-effort leg
        errors.append(f"daily: {exc}")
    _sync_state["last_attempt_ts"] = time.time()
    _sync_state["last_error"] = "; ".join(errors) if errors else None
    if errors:
        logger.warning("background sheet refresh had failures: %s", "; ".join(errors))


def _startup_warmup() -> None:
    """Pre-fill in-process caches a few seconds after the module loads.

    Gunicorn binds the socket synchronously before this thread fires, so the
    healthcheck at /health responds immediately (no data fetch needed there).
    Three seconds later this thread warms both the monthly cache (_live_payload)
    and the daily cache for the two most recent calendar months, so the very
    first user navigation hits warm data instead of triggering a 30-60 s cold
    Google Sheets round-trip that would time out through Replit's proxy.

    Entirely best-effort: silently swallows every error. The normal per-request
    path surfaces errors to the user if the sheets are genuinely unreachable.
    """
    import time as _t
    _t.sleep(3)                    # let the worker finish booting first
    if is_demo_mode():
        return
    _refresh_once()


def _auto_refresh_loop() -> None:
    """Always-on background sync: every ``_REFRESH_INTERVAL`` seconds, force a
    fresh live pull so the dashboard stays current around the clock without
    waiting for a visitor to trigger a fetch.

    This is only effective on an always-running (Reserved VM) deployment — on a
    scale-to-zero deployment the process sleeps between requests and this loop
    simply doesn't advance, which is harmless (the per-request path still keeps
    data within the TTL). Best-effort and silent; the per-request path still
    surfaces genuine read errors to the user.
    """
    import time as _t
    while True:
        _t.sleep(_REFRESH_INTERVAL)
        if is_demo_mode():
            continue
        try:
            _refresh_once()
        except Exception:
            pass


threading.Thread(target=_startup_warmup, daemon=True, name="cache-warmup").start()
threading.Thread(target=_auto_refresh_loop, daemon=True, name="cache-refresh").start()
