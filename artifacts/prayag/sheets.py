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
import dataclasses
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
import ideal_hours
import pipe_reconcile
import store as _store

# ---------------------------------------------------------------------------
# Connection + token (cached within the process until near expiry)
# ---------------------------------------------------------------------------
_token_cache: dict = {"token": None, "exp": 0.0}
_data_cache: dict = {}          # months_key -> (ts, payload)
_DATA_TTL = 1800.0              # seconds (30 min) on-demand fallback TTL. The
                                # always-on background refresher (bottom of file)
                                # refills well inside this window, so warm-cache
                                # hits are the norm; this TTL only governs the
                                # fallback when no refresher runs (e.g. autoscale).
                                # 30 min chosen so secondary data (historical months,
                                # yield, mixer, etc.) stays hot for 3 refresh cycles
                                # after a first-visit fetch.
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
    _report_cache.clear()
    _compound_cache.clear()
    _seg_labour_cache.clear()
    _index_cache.clear()
    _last_fetch_status.clear()
    _planning_cache.clear()
    _ptmt_pieces_cache.clear()
    _ptmt_master_cache.clear()
    _mould_cap_cache.clear()
    _material_cache.clear()
    _maintenance_cache.clear()
    _manpower_cache.clear()
    _yield_cache.clear()
    _mixer_cache.clear()
    _toolroom_cache.clear()
    _wastage_cache.clear()
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

    url = f"https://{host}/api/v2/connection?include_secrets=true"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "X-Replit-Token": xtoken}
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
    # Pick the google-sheet connection by id prefix; the API has no reliable
    # connector_names filter (the parameter is ignored server-side).
    sheet_item = next(
        (it for it in items if str(it.get("id", "")).startswith("conn_google-sheet_")),
        items[0] if items else None,
    )
    if not sheet_item:
        return None, 0.0
    settings = sheet_item.get("settings", {}) or {}
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
# Google Drive folder auto-discovery
# ---------------------------------------------------------------------------
# The daily workbooks live in per-plant Drive folders (``folder_ids`` in
# sources.DAILY_SOURCES). Rather than hand-pin every new monthly file id, we
# list each folder at runtime and ADD any month we don't already have pinned.
# Pinned ids always win (they are hand-verified); discovery only fills gaps, so
# this can never change an existing month's figures — only surface a brand-new
# month automatically.
#
# Two connectors are in play: the app reads sheet CELLS via ``google-sheet``,
# but Drive metadata/listing needs a separate ``google-drive`` token. Binding
# that connection is what makes folder listing work despite the drive.file
# scope (the folders/files are shared with the connected account).
_drive_token_cache: dict = {"token": None, "exp": 0.0}
_discovery_state: dict = {
    "last_scan_ts": 0.0,   # epoch of the last folder scan (ok or not)
    "added": {},           # {plant: [ym, ...]} discovered on top of the pins
    "error": None,         # message of the most recent scan failure, if any
    "vanished": None,      # {plant:ym -> file_id} months previously seen, now gone
}

# Postgres key for the persistent "discovery_seen" registry.
# This is NOT a data cache — it is an append-only map of every (plant, ym)
# that discovery has ever successfully resolved, with the file ID it found.
# It must survive all normal TTL windows and process restarts (1-year TTL).
_DS_SEEN_KEY = "discovery_seen"
_DS_SEEN_TTL = 31_536_000  # seconds ≈ 1 year
_DISCOVERY_TTL = 600.0     # re-scan folders at most this often (10 min)
_discovery_lock = threading.Lock()

# Month-word → month number. Covers 3-letter and common full/variant spellings
# ("June"/"Jun", "July"/"Jul", "Sept"/"Sep") seen in the real filenames.
_MONTH_WORDS: dict = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
# Matches a month word directly followed by a 4-digit 20xx year, tolerating the
# apostrophe/space noise in titles like "... Report - Apr ' 2026" / "June ' 2026".
_MONTH_YEAR_RE = re.compile(r"([A-Za-z]{3,9})\s*['\u2019]?\s*(20\d{2})")


def parse_month_from_title(name: str) -> Optional[str]:
    """Extract a ``YYYY-MM`` from a workbook filename, or ``None``.

    Deterministic and conservative: requires a recognised month word directly
    followed by a 20xx year. If nothing matches we return ``None`` and the file
    is skipped rather than guessed — never fabricate a month for a file we can't
    confidently place.
    """
    if not name:
        return None
    for m in _MONTH_YEAR_RE.finditer(name):
        word = m.group(1).lower().rstrip(".")
        mon = _MONTH_WORDS.get(word)
        if mon:
            return f"{m.group(2)}-{mon:02d}"
    return None


def _fetch_drive_token() -> Tuple[Optional[str], float]:
    """Fetch a Google **Drive** access token (separate connector from Sheets).

    Mirrors ``_fetch_token`` but asks for the ``google-drive`` connection. Never
    raises: Drive discovery is best-effort and must never break a page load, so
    any failure returns ``(None, 0.0)`` and the caller keeps the pinned sources.
    """
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

    url = f"https://{host}/api/v2/connection?include_secrets=true"
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "X-Replit-Token": xtoken}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.URLError, ValueError, OSError):
        return None, 0.0

    items = data.get("items", [])
    # Pick the google-drive connection by id prefix.
    drive_item = next(
        (it for it in items if str(it.get("id", "")).startswith("conn_google-drive_")),
        None,
    )
    if not drive_item:
        return None, 0.0
    settings = drive_item.get("settings", {}) or {}
    token = settings.get("access_token")
    expires_at = settings.get("expires_at")
    if not token:
        oauth = settings.get("oauth", {}) or {}
        creds = oauth.get("credentials", {}) if isinstance(oauth, dict) else {}
        token = creds.get("access_token")
        expires_at = expires_at or creds.get("expires_at") or creds.get("expiry_date")

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


def _get_drive_token() -> Optional[str]:
    now = time.time()
    if _drive_token_cache["token"] and now < _drive_token_cache["exp"] - 60:
        return _drive_token_cache["token"]
    token, exp = _fetch_drive_token()
    if token:
        _drive_token_cache["token"] = token
        _drive_token_cache["exp"] = exp
    return token


def _list_drive_folder(folder_id: str, token: str) -> List[dict]:
    """List the Google-Sheet files directly inside a Drive folder.

    Returns ``[{"id", "name", "modifiedTime"}, ...]`` for spreadsheet children
    only (never trashed). Paginates fully. Uses the shared-drive flags so files
    living in a Shared Drive are included.
    """
    out: List[dict] = []
    page_token = None
    for _ in range(20):  # hard page cap — folders hold ~dozen files at most
        q = urllib.parse.quote(
            f"'{folder_id}' in parents and trashed=false and "
            "mimeType='application/vnd.google-apps.spreadsheet'"
        )
        url = (
            f"https://www.googleapis.com/drive/v3/files?q={q}"
            "&fields=nextPageToken,files(id,name,modifiedTime)"
            "&pageSize=1000&orderBy=name"
            "&supportsAllDrives=true&includeItemsFromAllDrives=true"
        )
        if page_token:
            url += "&pageToken=" + urllib.parse.quote(page_token)
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        out.extend(data.get("files", []) or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


def ensure_daily_discovery(force: bool = False) -> dict:
    """List each plant's Drive folder(s) and ADD any newly-found monthly file.

    Additive and idempotent: mutates ``sources.DAILY_SOURCES[plant]["files"]``
    in place so every existing consumer (app/freshness/confirm/manifest) picks
    up new months with no rewiring, but NEVER overwrites a pinned month (pins
    are hand-verified and authoritative). Only plants with a daily layout are
    touched — a plant we can't parse (e.g. CP) is left alone so we don't create
    phantom "file exists but no data" states.

    TTL-guarded and lock-serialised; wrapped so any Drive/network failure leaves
    the existing map intact and records the error in ``_discovery_state`` rather
    than raising. Returns ``{plant: [ym, ...]}`` of months added across all scans.
    """
    now = time.time()
    if not force and (now - _discovery_state["last_scan_ts"]) < _DISCOVERY_TTL:
        return _discovery_state["added"]
    if is_demo_mode():
        return _discovery_state["added"]
    with _discovery_lock:
        now = time.time()
        if not force and (now - _discovery_state["last_scan_ts"]) < _DISCOVERY_TTL:
            return _discovery_state["added"]
        token = _get_drive_token()
        if not token:
            _discovery_state["error"] = "google-drive connection unavailable"
            _discovery_state["last_scan_ts"] = now  # don't hot-loop on missing token
            return _discovery_state["added"]

        # (plant, ym) -> (file_id, modifiedTime) for months not already pinned.
        found: dict = {}
        try:
            for plant, cfg in sources.DAILY_SOURCES.items():
                if plant not in _DAILY_LAYOUTS:
                    continue  # only auto-add plants the daily pipeline can read
                pinned = cfg.get("files") or {}
                for fid in (cfg.get("folder_ids") or []):
                    for f in _list_drive_folder(fid, token):
                        ym = parse_month_from_title(f.get("name", ""))
                        if not ym or ym in pinned:
                            continue  # unparseable, or a pinned month wins
                        mtime = f.get("modifiedTime", "") or ""
                        prev = found.get((plant, ym))
                        # If two discovered files claim the same month, keep the
                        # most recently modified (don't guess silently past it).
                        if prev is None or mtime >= prev[1]:
                            found[(plant, ym)] = (f.get("id", ""), mtime)
            # Group additions per plant, then swap each plant's ``files`` dict by
            # reference (copy-on-write) rather than mutating it in place. The
            # reference reassignment is atomic under the GIL, so a request handler
            # mid-iteration over the OLD dict (app.py/freshness/confirm all read
            # DAILY_SOURCES from background-thread-adjacent request paths) finishes
            # cleanly on its snapshot instead of hitting "dictionary changed size
            # during iteration"; the next read simply sees the fuller map.
            adds_by_plant: dict = {}
            for (plant, ym), (fid, _mt) in found.items():
                if fid:
                    adds_by_plant.setdefault(plant, {})[ym] = fid
            applied: dict = {}
            for plant, adds in adds_by_plant.items():
                new_files = dict(sources.DAILY_SOURCES[plant].get("files") or {})
                for ym, fid in adds.items():
                    if ym not in new_files:   # never overwrite a pinned id
                        new_files[ym] = fid
                        applied.setdefault(plant, []).append(ym)
                if applied.get(plant):
                    sources.DAILY_SOURCES[plant]["files"] = new_files
            for plant in applied:
                applied[plant].sort()
            _discovery_state["added"] = applied
            _discovery_state["error"] = None

            # ── Seen-month registry + vanished-file detection ─────────────────
            # Load the persistent map of every (plant, ym) discovery has ever
            # resolved.  Use a 1-year TTL — this is a registry, not a data cache.
            seen_map: dict = _store.pg_cache_read(_DS_SEEN_KEY, _DS_SEEN_TTL) or {}
            new_seen = False
            for (ds_plant, ds_ym), (ds_fid, _mt) in found.items():
                k = f"{ds_plant}:{ds_ym}"
                if seen_map.get(k) != ds_fid:
                    seen_map[k] = ds_fid
                    new_seen = True

            # A month is vanished when it was previously seen, is not in this
            # scan's found set, and is not pinned in sources.py.  Future months
            # (beyond today) are not flagged — they may not have files yet.
            current_ym_str = time.strftime("%Y-%m")
            vanished: dict = {}
            for sk, sfid in seen_map.items():
                sk_parts = sk.split(":", 1)
                if len(sk_parts) != 2:
                    continue
                sk_plant, sk_ym = sk_parts
                if sk_ym > current_ym_str:
                    continue  # future month — not yet expected
                if sk_ym in (sources.DAILY_SOURCES.get(sk_plant, {})
                             .get("files") or {}):
                    continue  # still pinned — always reliable
                if (sk_plant, sk_ym) not in found:
                    vanished[sk] = sfid  # discovered before, absent now

            _discovery_state["vanished"] = vanished
            if new_seen or vanished:
                _store.pg_cache_write(_DS_SEEN_KEY, seen_map)
            # ─────────────────────────────────────────────────────────────────

        except Exception as exc:  # noqa: BLE001 — best-effort, never break a page
            _discovery_state["error"] = str(exc)
            logger.warning("Drive folder discovery failed: %s", exc)
        _discovery_state["last_scan_ts"] = time.time()
        return _discovery_state["added"]


def discovery_status() -> dict:
    """Observability for the Drive auto-discovery layer (for /build-state etc.)."""
    return {
        "last_scan_ts": _discovery_state.get("last_scan_ts", 0.0),
        "added": dict(_discovery_state.get("added") or {}),
        "error": _discovery_state.get("error"),
        "vanished": dict(_discovery_state.get("vanished") or {}),
    }


def _get_vanished_file_id(plant: str, ym: str) -> Optional[str]:
    """Return the last-known file ID for a discovered month that is no longer
    reachable, or None when the month never had a discovered file or is pinned.

    Checks the in-process ``_discovery_state["vanished"]`` map first (populated
    by the most recent ``ensure_daily_discovery`` call).  Falls back to the
    persistent Postgres ``discovery_seen`` registry when discovery has not yet
    run in this process (cold-start / fresh gunicorn worker).
    """
    # Pinned months are always authoritative and never considered vanished,
    # regardless of stale entries that may exist in the vanished map.
    if ym in (sources.DAILY_SOURCES.get(plant, {}).get("files") or {}):
        return None
    key = f"{plant}:{ym}"
    # Fast path: in-memory state updated by the most recent scan.
    v = _discovery_state.get("vanished")
    if v is not None:
        return v.get(key)
    # Cold-start fallback: discovery hasn't run yet in this worker process.
    # Check the persistent registry directly.
    seen_map = _store.pg_cache_read(_DS_SEEN_KEY, _DS_SEEN_TTL) or {}
    fid = seen_map.get(key)
    return fid if fid else None


def _vanished_reports(plant: str, ym: str, vanished_fid: str) -> List[Tuple[List, dict]]:
    """Build ([], warning-report) tuples for a month whose discovered file has vanished.

    Mirrors the shape of ``_load_daily`` output so callers can treat it the same
    way: one entry per logical emitter the workbook would have produced.  Uses
    ``_DAILY_LAYOUTS`` to determine the emitter names; falls back to a single
    plant-keyed report when no layout spec is registered.
    """
    short = vanished_fid[:20] + "…"
    msg = (
        f"{plant} {ym}: a previously discovered source file is no longer readable "
        f"(was {short}). This month may show no data. Source may have been "
        f"deleted, moved, or had access revoked."
    )
    specs = _DAILY_LAYOUTS.get(plant, [])
    if specs:
        return [
            ([], {
                "emit": spec["emit"], "plant": spec["emit"], "ym": ym,
                "record_count": 0, "vanished_source": True, "warning": msg,
            })
            for spec in specs
        ]
    # Plants with no layout spec: emit one report keyed to the plant itself.
    return [([], {
        "plant": plant, "ym": ym, "record_count": 0,
        "vanished_source": True, "warning": msg,
    })]


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


def get_raw_values(file_id: str, tab: str) -> List[list]:
    """Read raw cell values from a specific tab using the internal auth token.

    Wraps ``read_values`` with error handling.  Returns ``[]`` if the tab
    cannot be read (network error, tab absent, token unavailable).  Intended
    for modules (e.g. mp_rejection) that need raw sheet access without
    managing the token themselves.
    """
    try:
        tok = _get_access_token()
        if not tok:
            return []
        return read_values(file_id, tab, tok) or []
    except Exception:
        return []


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
    """Read one annual family workbook → (records, source_report).

    Dispatches on src["kind"]:
      mc_grid        — default: per-machine detail tabs (M/C-n)
      gom_grid       — Group-of-Moulding tonnage-band grid
      tank_annual_2526 / tank_annual_2627 — Tank VN/WB annual summaries
      seg_labour     — Segment Labour multi-tab (no Records emitted)
    """
    kind = src.get("kind", "mc_grid")
    file_id = src["file_id"]
    location = src.get("location", "")

    # ── GOM grid ─────────────────────────────────────────────────────────────
    if kind == "gom_grid":
        tabs = list_tabs(file_id, token)
        tab = src["tab"]
        actual_tab = next((t for t in tabs if t.strip() == tab.strip()), None)
        if actual_tab is None:
            return [], {
                "family": src["family"], "title": src["title"], "file_id": file_id,
                "tab": tab, "detail_tabs": [], "grain": "monthly",
                "months_available": [], "record_count": 0,
                "segment": src["segment"], "plant": src["plant"],
                "reconcile": None, "warning": f"Tab '{tab}' not found.",
            }
        values = read_values(file_id, actual_tab, token)
        records = parsers.parse_gom_grid(
            values, plant=src["plant"], segment=src["segment"],
            unit=src["unit"], source_file=file_id, source_tab=actual_tab,
        )
        months = sorted({r.period for r in records})
        return records, {
            "family": src["family"], "title": src["title"], "file_id": file_id,
            "tab": actual_tab, "detail_tabs": [], "grain": "monthly",
            "months_available": months, "record_count": len(records),
            "segment": src["segment"], "plant": src["plant"], "reconcile": None,
        }

    # ── Tank annual 25-26 ─────────────────────────────────────────────────────
    if kind == "tank_annual_2526":
        tabs = list_tabs(file_id, token)
        tab = src["tab"]
        actual_tab = next((t for t in tabs if t.strip().upper() == tab.strip().upper()), None)
        if actual_tab is None:
            return [], {
                "family": src["family"], "title": src["title"], "file_id": file_id,
                "tab": tab, "detail_tabs": [], "grain": "monthly",
                "months_available": [], "record_count": 0,
                "segment": src["segment"], "plant": src["plant"],
                "reconcile": None, "warning": f"Tab '{tab}' not found.",
            }
        values = read_values(file_id, actual_tab, token)
        records = parsers.parse_tank_annual_2526(
            values, plant=src["plant"], segment=src["segment"],
            unit=src["unit"], source_file=file_id, source_tab=actual_tab,
            location=location,
        )
        months = sorted({r.period for r in records})
        return records, {
            "family": src["family"], "title": src["title"], "file_id": file_id,
            "tab": actual_tab, "detail_tabs": [], "grain": "monthly",
            "months_available": months, "record_count": len(records),
            "segment": src["segment"], "plant": src["plant"],
            "reconcile": None, "location": location, "grain_note": "summary-only",
        }

    # ── Tank annual 26-27 ─────────────────────────────────────────────────────
    if kind == "tank_annual_2627":
        tabs = list_tabs(file_id, token)
        tab = src["tab"]
        actual_tab = next((t for t in tabs if t.strip().lower() == tab.strip().lower()), None)
        if actual_tab is None:
            return [], {
                "family": src["family"], "title": src["title"], "file_id": file_id,
                "tab": tab, "detail_tabs": [], "grain": "monthly",
                "months_available": [], "record_count": 0,
                "segment": src["segment"], "plant": src["plant"],
                "reconcile": None, "warning": f"Tab '{tab}' not found.",
            }
        values = read_values(file_id, actual_tab, token)
        records = parsers.parse_tank_annual_2627(
            values, plant=src["plant"], segment=src["segment"],
            unit=src["unit"], source_file=file_id, source_tab=actual_tab,
            location=location,
        )
        months = sorted({r.period for r in records})
        return records, {
            "family": src["family"], "title": src["title"], "file_id": file_id,
            "tab": actual_tab, "detail_tabs": [], "grain": "monthly",
            "months_available": months, "record_count": len(records),
            "segment": src["segment"], "plant": src["plant"],
            "reconcile": None, "location": location, "grain_note": "summary-only",
        }

    # ── Segment Labour — no Records emitted; rows cached for dedicated route ──
    if kind == "seg_labour":
        tabs = list_tabs(file_id, token)
        unit_tabs = [t for t in tabs if re.search(r"UNIT[-\s]*[123]", str(t).upper())]
        raw_rows: list = []
        for t in unit_tabs:
            values = read_values(file_id, t, token)
            lbl = re.search(r"[123]", t)
            unit_label = f"UNIT-{lbl.group()}" if lbl else t
            raw_rows.extend(parsers.parse_segment_labour(
                values, unit_label=unit_label, source_file=file_id, source_tab=t
            ))
        _seg_labour_cache[file_id] = {
            "rows": raw_rows, "title": src["title"],
            "fy": src.get("fy", ""), "tabs": unit_tabs,
        }
        months = sorted({r["month"] for r in raw_rows})
        return [], {
            "family": src["family"], "title": src["title"], "file_id": file_id,
            "tab": ", ".join(unit_tabs), "detail_tabs": unit_tabs,
            "grain": "monthly", "months_available": months,
            "record_count": len(raw_rows),
            "segment": src["segment"], "plant": src["plant"], "reconcile": None,
        }

    # ── Default: mc_grid — per-machine detail tabs ────────────────────────────
    tabs = list_tabs(file_id, token)
    detail_tabs = [t for t in tabs if parsers.DETAIL_TAB_RE.match(t.strip())]
    want = list(detail_tabs)
    if src["tab"] in tabs:
        want.append(src["tab"])
    matrices = batch_get(file_id, want, token)

    records: List[Record] = []
    mc_notes: List[str] = []
    for t in detail_tabs:
        records.extend(parsers.parse_mc_detail(
            matrices.get(t, []),
            plant=src["plant"], segment=src["segment"], unit=src["unit"],
            source_file=file_id, source_tab=t, notes=mc_notes,
        ))

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
        "family": src["family"], "title": src["title"], "file_id": file_id,
        "tab": src["tab"], "detail_tabs": detail_tabs, "grain": "monthly",
        "months_available": months, "record_count": len(records),
        "segment": src["segment"], "plant": src["plant"],
        "reconcile": recon, "field_map": parsers.MC_DETAIL_FIELD_MAP,
    }
    # Rejection-column drift on the per-machine tabs (dedup — every M/C tab
    # shares one layout, one message per distinct note is enough).
    if mc_notes:
        report["notes"] = list(dict.fromkeys(mc_notes))
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

      # Parser-emitted notes (e.g. a rejection column that stopped matching
      # the header) are surfaced alongside the reconciliation warnings so
      # layout drift is visible immediately instead of showing a plausible 0%.
      for n in report.get("notes") or []:
          warnings.append(n)

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
_index_cache: dict = {}          # file_id -> (ts, [index report dicts])
_seg_labour_cache: dict = {}     # file_id -> {rows, title, fy, tabs}

# Report-only annual sources (REPORT_SOURCES) loaded on-demand by /reports/*.
# Kept OFF the main dashboard critical path so the cold "/" load stays fast.
_report_cache: dict = {}         # family -> (ts, [Record, ...])
_REPORT_TTL = 900.0
_report_lock = threading.Lock()


def load_report_records(family: str) -> List[Record]:
    """On-demand load of a report-only annual source family (sources.REPORT_SOURCES).

    Only the dedicated /reports/* routes call this, so the main dashboard cold
    start never pays for these workbooks. Cached in-process (TTL). Per-source
    failures are isolated (a bad workbook yields no rows, not a hard error).
    For ``seg_labour`` families this also populates ``_seg_labour_cache`` as a
    side effect (the seg_labour branch of ``_load_annual_family`` writes it).
    """
    if is_demo_mode():
        return []
    now = time.time()
    cached = _report_cache.get(family)
    if cached and now - cached[0] < _REPORT_TTL:
        return cached[1]
    with _report_lock:
        now = time.time()
        cached = _report_cache.get(family)
        if cached and now - cached[0] < _REPORT_TTL:
            return cached[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError(
                "The Google Sheets connection isn't authorized. "
                "Reconnect it from the integrations panel and try again."
            )
        recs: List[Record] = []
        for src in sources.REPORT_SOURCES:
            if src.get("family") != family:
                continue
            try:
                r, _rep = _load_annual_family(src, token)
                recs.extend(r)
            except SheetReadError:
                continue
        _report_cache[family] = (now, recs)
        return recs


# ---------------------------------------------------------------------------
# Compound Compilation — on-demand load of the Pipe mixer-logbook tabs
# ---------------------------------------------------------------------------
_compound_cache: dict = {}       # months-key -> (ts, payload)
_COMPOUND_TTL = 900.0
_compound_lock = threading.Lock()


def load_compound_data(months: List[str]) -> dict:
    """Read the Pipe & Fitting compound mixer-logbook tabs for ``months``.

    Only the /reports/compound_compilation route calls this, so the main
    dashboard cold start never pays for it. Each month is a separate PIPE daily
    workbook; per-month/per-tab read failures are isolated. Cached in-process.

    Returns ``{"by_compound": {key: [parse per month]}, "rollup": {ym: dict},
    "months": [ym with data]}``. Empty in demo mode.
    """
    import compound as _cmp

    if is_demo_mode():
        return {"by_compound": {s["key"]: [] for s in _cmp.COMPOUNDS}, "rollup": {}, "months": []}

    wanted = sorted(m for m in months if m in sources.DAILY_SOURCES.get("PIPE", {}).get("files", {}))
    key = tuple(wanted)
    now = time.time()
    cached = _compound_cache.get(key)
    if cached and now - cached[0] < _COMPOUND_TTL:
        return cached[1]
    with _compound_lock:
        now = time.time()
        cached = _compound_cache.get(key)
        if cached and now - cached[0] < _COMPOUND_TTL:
            return cached[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError(
                "The Google Sheets connection isn't authorized. "
                "Reconnect it from the integrations panel and try again."
            )
        tabs = [s["tab"] for s in _cmp.COMPOUNDS] + ["Compound 6-10"]
        by_compound: dict = {s["key"]: [] for s in _cmp.COMPOUNDS}
        rollup: dict = {}
        got_months: List[str] = []
        attempted = 0
        failed = 0
        for ym in wanted:
            fid = sources.DAILY_SOURCES["PIPE"]["files"].get(ym)
            if not fid:
                continue
            attempted += 1
            try:
                res = batch_get(fid, tabs, token)
            except SheetReadError:
                logger.warning("compound: read failed for PIPE %s", ym)
                failed += 1
                continue
            any_data = False
            for s in _cmp.COMPOUNDS:
                vals = res.get(s["tab"], [])
                p = (parsers.parse_cg_logbook(vals) if s["layout"] == "cg"
                     else parsers.parse_mixer_logbook(vals))
                if p:
                    p["ym"] = ym
                    by_compound[s["key"]].append(p)
                    if p.get("days"):
                        any_data = True
            rd = parsers.parse_compound_rollup(res.get("Compound 6-10", []))
            if rd:
                rollup[ym] = rd
            if any_data:
                got_months.append(ym)
        # Honest failure: if every workbook we tried to read failed, this is a
        # read OUTAGE, not an empty source — raise rather than masquerade as
        # "no compound data". Do NOT cache a failed read.
        if attempted and failed == attempted:
            raise SheetReadError(
                "Couldn't read the Pipe & Fitting daily workbooks for the "
                "selected period. This is usually a temporary Google Sheets "
                "limit — please try again in a moment."
            )
        out = {"by_compound": by_compound, "rollup": rollup, "months": got_months}
        _compound_cache[key] = (now, out)
        return out


# ---------------------------------------------------------------------------
# (D) Pipe Moulds Summary — mould-wise working reports (Report-17..20)
# ---------------------------------------------------------------------------
# The four mould-working tables live INSIDE the monthly PIPE workbook, so no new
# Drive IDs are needed. Read directly (bounded batch_get of 4 tabs) rather than
# via the full daily pipeline — the cold daily pipeline reads the whole month and
# would time out for a report that only needs these four tabs.
_PIPE_MOULD_TABS = {
    "Report-17": "CPVC",
    "Report-18": "UPVC",
    "Report-19": "SWR",
    "Report-20": "AGRI",
}
_pipe_moulds_cache: dict = {}       # ym -> (ts, payload)
_PIPE_MOULDS_TTL = 900.0
_pipe_moulds_lock = threading.Lock()


def load_pipe_moulds(ym: Optional[str]) -> dict:
    """Read the (D) mould-wise working reports for one PIPE month.

    Returns ``{available, month, file_id, groups:[summary,...], grand_kg,
    grand_pcs}`` where each ``summary`` is a ``parsers.parse_mould_working``
    result (recomputed detail sums + the sheet's own TOTAL row for a
    cross-check). ``available`` is False when the workbook has no Report-17..20
    tabs (an older-FY workbook) or the read fails — never a fabricated zero.
    """
    if is_demo_mode():
        return {"available": False, "month": ym, "file_id": "", "groups": [],
                "grand_kg": 0.0, "grand_pcs": 0.0}
    file_id = _daily_file_id("PIPE", ym)
    if not file_id:
        return {"available": False, "month": ym, "file_id": "", "groups": [],
                "grand_kg": 0.0, "grand_pcs": 0.0}
    now = time.time()
    cached = _pipe_moulds_cache.get(ym)
    if cached and now - cached[0] < _PIPE_MOULDS_TTL:
        return cached[1]
    with _pipe_moulds_lock:
        now = time.time()
        cached = _pipe_moulds_cache.get(ym)
        if cached and now - cached[0] < _PIPE_MOULDS_TTL:
            return cached[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError(
                "The Google Sheets connection isn't authorized. "
                "Reconnect it from the integrations panel and try again."
            )
        try:
            values = batch_get(file_id, list(_PIPE_MOULD_TABS), token)
        except SheetReadError:
            raise
        groups = []
        grand_kg = grand_pcs = 0.0
        missing = []
        for tab, grp in _PIPE_MOULD_TABS.items():
            s = parsers.parse_mould_working(values.get(tab, []), group=grp)
            if not s:
                missing.append(grp)
                continue
            groups.append(s)
            grand_kg += s["total_kg"]
            grand_pcs += s["total_pcs"]
        # Completeness gate — NEVER silently publish a partial roster. If some
        # (but not all) of the four expected mould groups parsed, the workbook or
        # a tab/header drifted: surface an honest "incomplete source" flag so the
        # UI can gate the headline "needs review" rather than under-report kg.
        # (All four missing = an older-FY workbook with no Report-17..20 → simply
        # unavailable, not incomplete.)
        incomplete = bool(groups) and bool(missing)
        out = {
            "available": bool(groups),
            "incomplete": incomplete,
            "missing": missing,
            "month": ym,
            "file_id": file_id,
            "groups": groups,
            "grand_kg": grand_kg,
            "grand_pcs": grand_pcs,
        }
        _pipe_moulds_cache[ym] = (now, out)
        return out


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
      # L2b: known-empty months (24 h TTL, longer than pg_cache).
      # A plant-month confirmed empty in the last 24 h skips the L3 Sheets
      # read entirely — saving quota for months that never have production.
      try:
          if _store.is_known_empty(plant, ym):
              _daily_cache[key] = (time.time(), [])
              return []
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
      # Mark genuinely empty (plant, ym) pairs so future cold starts avoid L3.
      # "Empty" means _load_daily returned no (record, report) tuples at all,
      # not an EMPTY_SOURCES short-circuit (those never reach here).
      try:
          if not results:
              _store.mark_empty_month(plant, ym)
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
          # PIPE production is read from Report-5, the AUTHORITATIVE per-machine
          # daily matrix: one row per Pipe M/C with per-date Run Hours / Output (KG)
          # / Rejection (KG) triplets (from col O). Each machine's per-date Output
          # and Rejection sum EXACTLY to Report-5's own monthly Col G (OUTPUT) and
          # Col H (REJ) totals, and run hours sum to Col F. Report-11 (the
          # "M/C & Item Wise Actual Production" log) is deliberately NOT used: it is
          # an incomplete item-level journal whose per-row weights undercount the
          # true output (e.g. Pipe M/C-1 Report-11 = 5503 KG vs Report-5 Col G =
          # 7214 KG), so reading it produced wrong figures.
          "emit": "PIPE", "tab": "Report-5", "layout": "matrix",
          # PIPE headline output/rejection is the DATE-WISE MAX of Report-5 and
          # Report-11 per (machine, date): neither source is complete on its own
          # (each misses machine-days the other records), so the corrected figure
          # = max(R5, R11) over their UNION. Report-11 also carries the pipe TYPE
          # (CPVC/UPVC/SWR/AGRI). Header-based reader handles both FY layouts.
          # See pipe_reconcile.py. Run hours / utilisation baseline stay from
          # Report-5 (Report-11 has none) — a R11-only machine-date has no hours.
          "pipe_reconcile": True, "report11_tab": "Report-11",
          # Report-5 holds SEVERAL machine families in one tab (Pipe M/C, Socket,
          # Mixer, Grinder/Pulverizer, Moulding A01…D07). Only the primary extruder
          # rows (M/C-n) are PIPE plant output, so keep just those (mc_only): the
          # auxiliaries are synthesised separately below (is_finishing) and Moulding
          # is read from its own Report-12 tab — neither must leak into the headline.
          "mc_only": True,
          "summary_mc_header": ("eq", "MACHINE"),
          # Utilisation baseline ALSO comes from Report-5: per-machine Ideal Run
          # Hour/Day (col D) × Total Run Days (col E); run hours (col F) come from
          # the matrix itself. Utilisation is RUN-DAY based — see _emit_daily.
          "report5_tab": "Report-5",
      },
      {
          "emit": "MOULDING", "tab": "Report-12", "layout": "long",
          "resolve": ["moulding", "production"],
          # Report-12 records moulding OUTPUT only (no run hours), so its
          # utilisation baseline comes from the SAME workbook's Report-5 moulding
          # rows (joined by the bare machine label, e.g. "A01(NU-200)"). Same
          # run-day basis as PIPE — see _emit_daily. ``r5_runhours`` tells the
          # _r5_hit branch to take run hours FROM Report-5 (this source has none);
          # PIPE omits it because its Report-5 matrix already carries per-date hours.
          "report5_tab": "Report-5", "r5_runhours": True,
          "long": dict(
              machine_col=("startswith", "MOULDING MACHI"),
              out_col=("contains", "WT IN KGS"),
              rej_col=("contains", "ACTUAL REJECTION"),
              runner_col=("startswith", "RUNNER PRODUCE"),
              machine_prefix="MOULDING ",
          ),
      },
  ],
  # GARDEN keeps one tab PER MACHINE ("MACHINE 2", ...), each a date×production
  # block carrying the authoritative KG OUTPUT + rejection. Its "Daily Report"
  # matrix tab additionally logs per-machine, per-date RUN HOURS (Run Hours /
  # Output / Rejection groups) — only the RUN HOURS are read from there and
  # joined onto the block-tab rows by machine number + date (``runhours_tab``);
  # output is NEVER taken from Daily Report (the per-machine tabs are the output
  # source of truth). With real run hours, utilisation computes against the
  # app-default planned hours (GARDEN=500/machine/month).
  "GARDEN": [{
      "emit": "GARDEN", "layout": "blocks",
      "tab_re": r"^MACHINE\s*\d+$", "machine_prefix": "GARDEN M/C - ",
      "runhours_tab": "Daily Report",
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
      "resolve": ["output", "hours"],
      "ideal_col": ("contains", "IDEAL HOUR"),
  }],
  # TANK records per-ITEM production (no machine dimension) on "PROD. REPORT".
  # All three streams share the same tab name and parser; columns differ per
  # workbook but are resolved by header text, not by index.
  "TANK":    [{"emit": "TANK",    "tab": "PROD. REPORT", "layout": "tank"}],
  "TANK_VN": [{"emit": "TANK_VN", "tab": "PROD. REPORT", "layout": "tank"}],
  "TANK_WB": [{"emit": "TANK_WB", "tab": "PROD. REPORT", "layout": "tank"}],
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


def _r5_norm(label) -> str:
  """Normalise a machine label for Report-5 ↔ daily joins by bare name.

  Used for plants whose daily machines carry no M/C number (MOULDING's
  ``A01(NU-200)`` etc.). Upper-cases and strips ALL whitespace so a stray space
  in either source never breaks the match.
  """
  return "".join(str(label).split()).upper()


def _r5_aux_class(label: str) -> Optional[Tuple[Optional[str], str, bool]]:
  """Classify a Report-5 AUXILIARY machine → (owner_plant | None, segment, finishing).

  Only grinders / pulverizers / sockets / mixers qualify — these appear ONLY in
  Report-5 (no daily tab) and must be surfaced. Returns ``None`` for anything
  else (real pipe / moulding production lines), which the daily path already
  handles; without this guard an untagged moulding row or an idle pipe machine
  would leak in as a bogus auxiliary. The owner plant comes from the label's
  ``(PIPE)`` / ``(MOULD)`` tag; untagged rows (sockets, mixers) return ``None``
  so the caller defaults them to PIPE. All are reprocessing / auxiliary lines
  (is_finishing=True), so their output never inflates the plant headline — they
  still show in full inside their own segment.
  """
  u = label.upper()
  if "GRIND" in u:
      seg = "Grinding"
  elif "PULVER" in u:
      seg = "Pulverizing"
  elif "SOCKET" in u:
      seg = "Socketing"
  elif "MIXER" in u:
      seg = "Mixing"
  else:
      return None
  owner = "MOULDING" if "(MOULD" in u else ("PIPE" if "(PIPE" in u else None)
  return owner, seg, True


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
      block_notes: List[str] = []
      raw.extend(parsers.parse_daily_blocks(
          values, plant=emit, segment=seg, unit=unit, year_month=ym,
          source_file=file_id, source_tab=t, machine=machine,
          notes=block_notes,
      ))
      # Rejection-header-present-but-unmatched drift per tab (dedup — many
      # machine tabs share one layout, one message per distinct note is enough).
      for n in block_notes:
          existing = report.setdefault("notes", [])
          if n not in existing:
              existing.append(n)

  # ----- Run hours from a separate per-date matrix tab (GARDEN) --------------
  # The block tabs are the OUTPUT source of truth; some plants (GARDEN) ALSO
  # publish per-machine, per-date RUN HOURS in a wide "Daily Report" matrix.
  # Read ONLY run hours from there (never output) and join onto the block rows
  # by machine number + date so utilisation can compute. The matrix parser's
  # per-machine run-hour total reconciles to the sheet's own TOTAL column.
  rh_tab = spec.get("runhours_tab")
  rh_tab_found = False
  rh_parsed = 0
  rh_layout_ok = False   # True when parse_daily_matrix found the date-row header
  runhours_found = False
  if rh_tab:
      rh_actual = next(
          (t for t in list_tabs(file_id, token)
           if str(t).strip().upper() == rh_tab.upper()), None)
      if rh_actual is not None:
          rh_tab_found = True
          rh_lf: list = []   # _layout_found out-param: non-empty iff header detected
          rh_rows = parsers.parse_daily_matrix(
              read_values(file_id, rh_actual, token),
              plant=emit, segment=seg, unit=unit, year_month=ym,
              source_file=file_id, source_tab=rh_actual,
              _layout_found=rh_lf,
          )
          rh_parsed = len(rh_rows)
          rh_layout_ok = bool(rh_lf)
          rh_map: dict = {}
          for rr in rh_rows:
              if rr.actual_hours and rr.actual_hours > 0:
                  m = re.search(r"(\d+)", rr.machine)
                  if m:
                      rh_map[(m.group(1), rr.date)] = (
                          rh_map.get((m.group(1), rr.date), 0.0) + rr.actual_hours)
          for r in raw:
              m = re.search(r"(\d+)", r.machine)
              key = (m.group(1), r.date) if m else None
              if key and key in rh_map:
                  r.actual_hours = rh_map[key]
                  runhours_found = True

  # ----- Ideal-hours denominator --------------------------------------------
  # Lowest-priority app-logic default (GARDEN=500/machine/month). For a
  # run-hours-tracked plant the monthly default is spread ONLY across the days a
  # machine actually logged run hours, so (a) a full-month rollup still uses the
  # whole default (Σ ideal == app_default per machine) and (b) a day with output
  # but NO run hours keeps ideal=0 → utilisation BLANK, never a fake 0% — true at
  # the DAY grain too, not just monthly. A machine with no run hours at all this
  # period gets no denominator → suppressed. Output-only plants
  # (runhours_tracked=False, e.g. TANK) spread across every active day and rely
  # on the metrics gate to stay suppressed.
  app_default = ideal_hours.APP_DEFAULT_IDEAL_HOURS.get(emit)
  tracks_hours = emit not in ideal_hours.PLANTS_WITHOUT_RUNHOURS
  rh_days: dict = {}      # machine -> {dates with run hours} (tracked plants)
  out_days: dict = {}     # machine -> {all active dates} (output-only plants)
  for r in raw:
      out_days.setdefault(r.machine, set()).add(r.date)
      if r.actual_hours > 0:
          rh_days.setdefault(r.machine, set()).add(r.date)
  for r in raw:
      r.runhours_tracked = tracks_hours
      r.ideal_output = 0.0     # no in-sheet output rate → efficiency hidden
      give = False
      days = 1
      if app_default and app_default > 0:
          if tracks_hours:
              # Only days WITH run hours carry the denominator → no per-day fake 0%.
              if r.actual_hours > 0:
                  give = True
                  days = max(len(rh_days.get(r.machine, ())), 1)
          else:
              # Output-only plant: every active day (metrics gate suppresses util).
              give = True
              days = max(len(out_days.get(r.machine, ())), 1)
      if give:
          r.ideal_hours = app_default / days
          r.ideal_source = "app_default"
      else:
          r.ideal_hours = 0.0
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
  elif rh_tab and runhours_found:
      report["warning"] = (
          f"{emit} {ym}: output read from the per-machine tabs; run hours joined "
          f"from the '{rh_tab}' matrix — utilisation is shown."
      )
  elif rh_tab and not rh_tab_found:
      report["warning"] = (
          f"{emit} {ym}: output read from the per-machine tabs, but the "
          f"'{rh_tab}' run-hours matrix tab was not found — utilisation "
          "suppressed (check the workbook layout)."
      )
  elif rh_tab and rh_parsed == 0:
      if rh_layout_ok:
          # Layout was parseable but every data cell is zero — operators have
          # not yet filled in this month's run hours.
          report["warning"] = (
              f"{emit} {ym}: output read from the per-machine tabs, but the "
              f"'{rh_tab}' matrix has no run hours entered yet — utilisation "
              "suppressed."
          )
      else:
          report["warning"] = (
              f"{emit} {ym}: output read from the per-machine tabs, but the "
              f"'{rh_tab}' matrix could not be parsed (layout not recognised) — "
              "utilisation suppressed."
          )
  elif rh_tab:
      report["warning"] = (
          f"{emit} {ym}: output read from the per-machine tabs, but the "
          f"'{rh_tab}' matrix carries no run hours yet — utilisation suppressed."
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

  Tank output is recorded per item (no machine, no run hours). The sheet reports
  the same run in litres, pieces and kg; litres is Tank's primary headline unit,
  so rows carry ``machine=""`` and ``unit="Ltr"`` (pcs/kg kept as secondary on
  each Record); utilisation/efficiency stay hidden. Distinguishes a genuine
  no-production period from a parse failure."""
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
  try:
      raw = parsers.parse_tank_prod(
          values, plant=emit, segment=seg, unit="Ltr", year_month=ym,
          source_file=file_id, source_tab=actual,
      )
  except parsers.TankRejectionColumnError as exc:
      report["warning"] = str(exc)
      return [], report
  # TANK is output-only (no run hours), so utilisation/efficiency stay suppressed.
  # Mark runhours_tracked=False so that a manager OVERRIDE may set a planned-hours
  # baseline without the metrics gate ever fabricating a 0% utilisation, while the
  # plant still counts as "baseline set" (app-default) for honest UI messaging.
  tracks_hours = emit not in ideal_hours.PLANTS_WITHOUT_RUNHOURS
  for r in raw:
      r.runhours_tracked = tracks_hours
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
  resolve_note = None
  if spec.get("resolve"):
      resolved, via_index = resolve_report_tab(
          emit, spec["resolve"], tab, ym=ym, token=token)
      if via_index and resolved and resolved != tab:
          resolve_note = (
              f"{emit} {ym}: production tab resolved via the workbook Index by "
              f"description to '{resolved}' (configured fallback was '{tab}')."
          )
          tab = resolved
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
      parse_notes: List[str] = []
      raw = parsers.parse_daily_long(
          values, plant=emit, segment=seg, unit=unit, year_month=ym,
          source_file=file_id, source_tab=tab, notes=parse_notes,
          **spec["long"],
      )
      # A configured rejection/runner column that stopped matching the header
      # (layout drift) must be visible on the dashboard/manifest — output still
      # parses, so the month would otherwise look genuinely rejection-free.
      if parse_notes:
          report.setdefault("notes", []).extend(parse_notes)
  else:
      parse_notes: List[str] = []
      raw = parsers.parse_daily_matrix(
          values, plant=emit, segment=seg, unit=unit, year_month=ym,
          source_file=file_id, source_tab=tab,
          mc_header_spec=spec.get("summary_mc_header"),
          notes=parse_notes,
      )
      # A rejection sub-header that stopped matching any date group (layout
      # drift) must surface via the existing daily warnings channel — output
      # still parses so the month would otherwise look rejection-free.
      if parse_notes:
          report.setdefault("notes", []).extend(parse_notes)

  # PIPE's Report-5 matrix carries multiple machine families in one tab. Keep
  # only the primary extruder rows (M/C-n) as PIPE output — the Socket / Mixer /
  # Grinder / Pulverizer auxiliaries are synthesised separately below (finishing)
  # and Moulding is read from its own tab, so neither must leak in here.
  if spec.get("mc_only"):
      raw = [r for r in raw if _mc_key(r.machine) is not None]

  # PIPE reconciliation: the headline output/rejection is the DATE-WISE MAX of
  # Report-5 (parsed above as ``raw``) and Report-11 over the UNION of every
  # (machine, date) either reports. Neither source is complete on its own, so the
  # max recovers the true total (audited April: R5 135,634 + R11 157,278 →
  # corrected 157,883 out / 13,030 rej). Report-11 also carries the pipe TYPE;
  # its proportions are scaled pro-rata to the corrected output (R5-only cells are
  # "untyped pickup"). Run hours stay from Report-5 only — a Report-11-only
  # machine-date has output but NO run hours (flagged: efficiency understated).
  if spec.get("pipe_reconcile"):
      r11_tab = spec.get("report11_tab", "Report-11")
      r11 = {}
      if r11_tab in tabs:
          _r11_raw = read_values(file_id, r11_tab, token)
          _r11_key = lambda lbl: pipe_reconcile.resolve_r11_label(lbl, _mc_key)
          r11 = pipe_reconcile.parse_report11(_r11_raw, ym, _r11_key)
          # Zero-match guard: if the tab had data rows but none resolved, the
          # reconciliation is silently falling back to Report-5 only. Emit a
          # visible note rather than letting the failure go undetected.
          _r11_data_rows = sum(
              1 for row in _r11_raw[5:] if row and len(row) >= 4
              and str(row[0]).strip()  # has a date cell
          )
          if _r11_data_rows > 0 and not r11:
              report.setdefault("notes", []).append(
                  f"{emit} {ym}: Report-11 has {_r11_data_rows} data rows but "
                  f"none matched any known machine label — R11 is excluded from "
                  f"this month's reconciliation and type split is unavailable. "
                  f"Check that Report-11 machine names match the current M/C-n "
                  f"labels or the alias table in pipe_reconcile._R11_ALIAS."
              )
      # Report-5 output/rejection per (machine number, date) from the matrix rows.
      r5: dict = {}
      label_for: dict = {}
      by_key: dict = {}
      for r in raw:
          k = _mc_key(r.machine)
          if k is None:
              continue
          d = r5.get((k, r.date))
          if d is None:
              d = {"out": 0.0, "rej": 0.0}
              r5[(k, r.date)] = d
          d["out"] += r.total_count
          d["rej"] += r.reject_count
          label_for.setdefault(k, r.machine)
          by_key[(k, r.date)] = r
      corrected, audit = pipe_reconcile.reconcile(r5, r11)
      r11_only_out: dict = {}
      for (k, date), c in corrected.items():
          tgt = by_key.get((k, date))
          if tgt is not None:
              # Apply the corrected headline to the existing Report-5 row;
              # run hours / baseline are untouched.
              tgt.total_count = c["out"]
              tgt.reject_count = c["rej"]
              tgt.material = ""  # headline row is type-agnostic (split is audit-only)
          else:
              # Report-11-only machine-date: real output, but no Report-5 run
              # hours. Append a daily row with actual_hours=0 so the output is
              # counted while utilisation/efficiency stay suppressed for it.
              lbl = label_for.get(k, f"PIPE M/C-{k}")
              if raw:
                  newr = dataclasses.replace(
                      raw[0], machine=lbl, date=date, period=date,
                      actual_hours=0.0, total_count=c["out"],
                      reject_count=c["rej"], ideal_hours=0.0,
                      ideal_output=0.0, material="",
                      source_file=file_id, source_tab=r11_tab,
                  )
              else:
                  newr = Record(
                      grain="daily", period=date, date=date, plant=emit,
                      segment=seg, machine=lbl, unit=unit,
                      total_count=c["out"], reject_count=c["rej"],
                      source_file=file_id, source_tab=r11_tab,
                  )
              raw.append(newr)
              r11_only_out[k] = r11_only_out.get(k, 0.0) + c["out"]
      report["pipe_reconcile"] = {
          "audit": audit,
          "report11_present": bool(r11),
          "r5_out": round(sum(d["out"] for d in r5.values()), 1),
          "r11_out": round(sum(d["out"] for d in r11.values()), 1),
          "r5_rej": round(sum(d["rej"] for d in r5.values()), 1),
          "r11_rej": round(sum(d["rej"] for d in r11.values()), 1),
          "type_totals": {t: round(v, 1) for t, v in audit["type_totals"].items()},
          "untyped_kg": round(audit["untyped_kg"], 1),
          "r11_only_machines": {
              label_for.get(k, f"M/C-{k}"): round(v, 1)
              for k, v in sorted(r11_only_out.items())
          },
      }
      # Efficiency sanity check (non-blocking): machines whose output includes
      # Report-11-only production carry output with no matching run hours, so any
      # output-per-hour / efficiency reading for them is understated.
      if r11_only_out:
          names = ", ".join(
              f"{label_for.get(k, f'M/C-{k}')} (+{v:,.0f} {unit})"
              for k, v in sorted(r11_only_out.items()))
          note = (
              f"{emit} {ym}: {len(r11_only_out)} machine(s) have Report-11-only "
              f"output with no Report-5 run hours — efficiency is understated for "
              f"them: {names}."
          )
          report.setdefault("notes", []).append(note)

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

  # PIPE / MOULDING: utilisation baseline lives in a SEPARATE monthly-summary tab
  # (Report-5) shared by every machine family. Each row carries Ideal Run Hour Per
  # Day (col D), Total Run Days (col E) and Run Hours (col F). Utilisation is
  # RUN-DAY based — col F / (col D × col E) — so a low-activity month is judged
  # against the days it actually ran, not the calendar. Report-5 is AUTHORITATIVE
  # for both the numerator (run hours) and the denominator (ideal hours), so it
  # overrides the daily tab's own run hours (which can disagree, and are 0 for
  # output-only MOULDING). Two lookups so PIPE joins by machine number and MOULDING
  # joins by its bare label (e.g. "A01(NU-200)"); no _mc_key collision because
  # MOULDING's daily labels carry no M/C number.
  r5_parsed: dict = {}     # raw label -> {per_day,run_days,run_hours,output,reject,ideal_out}
  r5_by_mckey: dict = {}   # _mc_key            -> info dict
  r5_by_label: dict = {}   # normalised label   -> info dict
  r5_tab = spec.get("report5_tab")
  if r5_tab and r5_tab in tabs:
      r5_parsed = parsers.parse_pipe_run5(read_values(file_id, r5_tab, token))
      for lbl, info in r5_parsed.items():
          k = _mc_key(lbl)
          if k is not None:
              r5_by_mckey[k] = info
          r5_by_label[_r5_norm(lbl)] = info

  def _r5_hit(machine: str):
      """Resolve a daily machine to its Report-5 (per_day, run_days, run_hours)."""
      k = _mc_key(machine)
      if k is not None and k in r5_by_mckey:
          return r5_by_mckey[k]
      lab = machine
      pref = spec.get("long", {}).get("machine_prefix") or spec.get("machine_prefix")
      if pref and lab.startswith(pref):
          lab = lab[len(pref):]
      return r5_by_label.get(_r5_norm(lab))

  # Active days per machine (full month) so per-day ideal hours reconcile to
  # the monthly figure; row count per machine so a Report-5 monthly figure spreads
  # evenly across its daily rows (sum reconciles exactly).
  active: dict = {}
  rowcount: dict = {}
  for r in raw:
      active.setdefault(r.machine, set()).add(r.date)
      rowcount[r.machine] = rowcount.get(r.machine, 0) + 1

  # App-logic default monthly ideal hours for this plant (not in the sheets), and
  # whether the plant records run hours at all. The default is the lowest-priority
  # tier (override > sheet > derived > config baseline > APP DEFAULT > none); for
  # output-only plants it supplies the denominator but utilisation stays suppressed
  # (runhours_tracked=False) until run hours exist — never a fake 0%.
  app_default = ideal_hours.APP_DEFAULT_IDEAL_HOURS.get(emit)
  tracks_hours = emit not in ideal_hours.PLANTS_WITHOUT_RUNHOURS

  for r in raw:
      r.runhours_tracked = tracks_hours
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
          if ih_month > 0:
              r.ideal_hours = ih_month / days
              r.ideal_source = "sheet"
          elif app_default and app_default > 0:
              # No in-sheet ideal HOURS → fall to the app-logic default (HDPE's
              # 550). The in-sheet output rate above still drives efficiency.
              r.ideal_hours = app_default / days
              r.ideal_source = "app_default"
          else:
              r.ideal_hours = 0.0
              r.ideal_source = "none"
      elif r.machine in sheet_ideal:
          r.ideal_hours = sheet_ideal[r.machine] / days
          r.ideal_output = 0.0  # no in-sheet output rate → efficiency hidden
          r.ideal_source = "sheet"
      elif _r5_hit(r.machine) is not None:
          # PIPE / MOULDING: Report-5 is authoritative. Monthly ideal = Ideal Run
          # Hour/Day × Total Run DAYS (NOT calendar days); monthly actual = the
          # sheet's own Run Hours. Both are spread evenly across the machine's daily
          # rows so a full-month rollup reconciles exactly to the sheet figure and
          # utilisation = Σrun hours / Σideal hours = col F / (col D × col E). An
          # idle machine (run days = 0) gets ideal_hours = 0, so utilisation stays
          # blank (never a fake 0%). No clamp — a grinder running past its ideal
          # legitimately exceeds 100%.
          info = _r5_hit(r.machine)
          nrows = max(rowcount.get(r.machine, 0), 1)
          r.ideal_hours = (info["per_day"] * info["run_days"]) / nrows
          # Col M (ideal machine hours for the full month) is spread across the
          # machine's daily rows so a monthly rollup reconstructs the col-M value
          # exactly. getattr guard: older r5_parsed dicts pre-dating this field
          # return 0.0, leaving mc_eff_available False (never a fake 0%).
          r.ideal_month_hours = info.get("ideal_month_hours", 0.0) / nrows
          # Run hours: PIPE reads its own per-date run hours from the Report-5
          # matrix (they already sum to col F), so keep them. Only an output-ONLY
          # source flagged ``r5_runhours`` (MOULDING's Report-12, which carries NO
          # run hours of its own) falls back to Report-5's monthly run hours, spread
          # across its rows so the rollup still reconciles to col F. Gating on the
          # explicit flag (not a per-row actual_hours==0 test) means a PIPE row with
          # 0 hours is never silently backfilled, keeping ΣPIPE hours == col F.
          if spec.get("r5_runhours"):
              r.actual_hours = info["run_hours"] / nrows
          # Output efficiency = (output / run hours) / Ideal Output Per Hour
          # (Report-5 Col I, per machine — NOT global). Wire the per-machine rate so
          # efficiency computes as (G / F) / I in compute_metrics. A BLANK Col I
          # (e.g. moulding lines, Grinder-3 whose in-sheet K is #DIV/0!) leaves the
          # rate at 0 → ideal_output 0 → efficiency stays n/a, never a fake 0%
          # (the spec's IFERROR rule). No clamp: a machine past its ideal output
          # legitimately reads >100%.
          rate = info.get("ideal_out", 0.0)
          r.ideal_rate = rate
          r.ideal_output = r.actual_hours * rate if rate > 0 else 0.0
          r.ideal_source = "derived"
      else:
          base = baselines.resolve(emit, r.machine, ym)
          if base:
              r.ideal_hours = base["planned_hours"] / days
              if base.get("ideal_output") is not None:
                  rate = base["ideal_output"] / max(base["planned_hours"], 1e-9)
                  r.ideal_rate = rate
                  r.ideal_output = r.actual_hours * rate
              r.ideal_source = "config"
          elif app_default and app_default > 0:
              # App-logic default (GARDEN/TANK = 500): supplies the utilisation
              # denominator. For these output-only plants utilisation stays
              # suppressed in compute_metrics (runhours_tracked=False) until run
              # hours are recorded — the denominator alone is not a live figure.
              r.ideal_hours = app_default / days
              r.ideal_output = 0.0
              r.ideal_source = "app_default"
          else:
              r.ideal_hours = 0.0
              r.ideal_output = 0.0
              r.ideal_source = "none"

  # ----- Report-5-only auxiliary machines (no daily tab) --------------------
  # Grinders, pulverizers, sockets and mixers live ONLY in the Report-5 monthly
  # summary — they have no per-machine daily tab, so without this they vanish
  # from the app entirely (the reported bug). Surface each as a MONTH-grain
  # record carrying its run-day utilisation — run hours / (ideal/day × run days)
  # — and, where the sheet publishes an Ideal Output Per Hour, its efficiency.
  # Skip any Report-5 row already matched to a daily machine (handled above).
  # Route by the label's "(PIPE)"/"(MOULD)" tag; untagged socket/mixer rows
  # belong to PIPE. Idle rows (0 run days) get 0 ideal hours so utilisation /
  # efficiency stay BLANK — never a fake 0%. is_finishing keeps reprocessing
  # output out of the plant headline (their own segment still shows them).
  aux_notes: List[str] = []
  if r5_parsed:
      pref = spec.get("long", {}).get("machine_prefix") or spec.get("machine_prefix")
      seen_mckey = {_mc_key(r.machine) for r in raw if _mc_key(r.machine) is not None}
      seen_label = {_r5_norm(r.machine) for r in raw}
      for r in raw:
          lab = r.machine
          if pref and lab.startswith(pref):
              lab = lab[len(pref):]
          seen_label.add(_r5_norm(lab))
      for lbl, info in r5_parsed.items():
          k = _mc_key(lbl)
          if (k is not None and k in seen_mckey) or _r5_norm(lbl) in seen_label:
              continue  # already represented by a daily machine
          cls = _r5_aux_class(lbl)
          if cls is None:
              continue  # real pipe/moulding line — handled by the daily path
          owner, seg, fin = cls
          if (owner or "PIPE") != emit:
              continue  # routed to the other plant's emit
          core = re.sub(r"\s*\([^)]*\)\s*$", "", lbl).strip() or lbl
          ideal_h = info["per_day"] * info["run_days"]
          ideal_rate = info["ideal_out"]
          raw.append(Record(
              grain="monthly", period=ym, date=f"{ym}-01",
              plant=emit, segment=f"{emit} \u2013 {seg}",
              machine=f"{emit} {core}".strip(), unit="kg",
              total_count=info["output"], reject_count=info["reject"],
              actual_hours=info["run_hours"],
              ideal_hours=ideal_h, ideal_hours_sheet=ideal_h,
              ideal_rate=ideal_rate,
              ideal_output=info["run_hours"] * ideal_rate,
              ideal_source="derived", runhours_tracked=True, is_finishing=fin,
              # Col M — full-month ideal hours for M/C Efficiency. Month-grain
              # aux records are not spread (one row per machine), so use as-is.
              ideal_month_hours=info.get("ideal_month_hours", 0.0),
              source_family=f"{emit} {seg}", source_file=file_id, source_tab=r5_tab,
          ))
          if info["run_days"] > 0 and ideal_rate <= 0:
              aux_notes.append(
                  f"{emit} {core}: missing ideal output/hour in Report-5 — "
                  "efficiency shown as n/a (utilisation still computed)."
              )
  if aux_notes:
      report["notes"] = aux_notes
  if resolve_note:
      report.setdefault("notes", []).append(resolve_note)

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
      # Three distinct states must not be conflated:
      #   1. never had a file        → return [] (existing "awaiting source" behaviour)
      #   2. genuinely zero output   → handled below via EMPTY_SOURCES
      #   3. had a discovered file, now unreadable → new vanished warning
      vanished_fid = _get_vanished_file_id(plant, ym)
      if vanished_fid:
          return _vanished_reports(plant, ym, vanished_fid)
      return []
  # Known-empty template (e.g. a prior-year month whose workbook is all zeros):
  # do NOT read it as a real zero-output month — return an "awaiting source"
  # report (no records) for each logical plant the workbook would emit.
  if (plant, ym) in getattr(sources, "EMPTY_SOURCES", set()):
      return [
          ([], {
              "emit": spec["emit"], "ym": ym, "record_count": 0,
              "empty_source": True,
              "notes": [f"{spec['emit']} {ym}: source workbook is empty "
                        "(awaiting data) — not a real zero-output month."],
          })
          for spec in _DAILY_LAYOUTS.get(plant, [])
      ]
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
          for n in report.get("notes") or []:
              warnings.append(n)
          # Daily-vs-grid reconciliation data is kept in report["reconcile"]
          # for the Sources diagnostic page, but is no longer emitted as a
          # user-visible warning — daily files are the authoritative source.

  # Surface vanished-file warnings for months that discovery once found but
  # can no longer reach.  These are NOT in ``pairs`` (no file in sources.py
  # nor discovered this run), so they would silently render as "no data"
  # without this explicit check.  The report dict carries vanished_source=True
  # so the data-health panel can render an appropriate indicator.
  for ym in months:
      for dp in _daily_plants():
          if ym in (sources.DAILY_SOURCES.get(dp, {}).get("files") or {}):
              continue  # covered by the pairs loop above
          v_fid = _get_vanished_file_id(dp, ym)
          if v_fid:
              for _, v_report in _vanished_reports(dp, ym, v_fid):
                  reports.append(v_report)
                  if v_report.get("warning"):
                      warnings.append(v_report["warning"])

  return all_recs, reports, warnings


# ---------------------------------------------------------------------------
# Per-workbook "Index" tab — authoritative tab metadata.
#
# The PTMT and Pipe & Fitting daily workbooks each ship an "Index" sheet that
# documents every Report-N tab (description, frequency, owner, unit). The app
# uses it to key tabs by DESCRIPTION rather than a bare report number (the same
# number means different things across workbooks) and to surface tabs that exist
# but aren't wired yet. parsers.parse_index does the pure parsing; this layer
# adds the live read + caching + tab resolution.
# ---------------------------------------------------------------------------
_INDEX_TTL = 600.0   # seconds; Index changes at most monthly


def _index_tab_name(file_id: str, token: str) -> Optional[str]:
  """The workbook's Index tab title (case-insensitive), or None."""
  try:
      titles = list_tabs(file_id, token)
  except SheetReadError:
      return None
  for t in titles:
      if str(t).strip().lower() == "index":
          return t
  for t in titles:
      if "index" in str(t).strip().lower():
          return t
  return None


def read_index(file_id: str, token: Optional[str] = None) -> List[dict]:
  """Parsed Index metadata for one workbook file, cached per file id.

  Returns [] (degrades quietly) when the workbook has no Index tab or the read
  fails — the Index is advisory metadata, never on a figure's critical path.
  """
  if not file_id:
      return []
  now = time.time()
  hit = _index_cache.get(file_id)
  if hit and now - hit[0] < _INDEX_TTL:
      return hit[1]
  token = token or _get_access_token()
  if not token:
      return []
  tab = _index_tab_name(file_id, token)
  if not tab:
      _index_cache[file_id] = (now, [])
      return []
  try:
      rows = read_values(file_id, tab, token)
  except SheetReadError:
      return []
  parsed = parsers.parse_index(rows)
  _index_cache[file_id] = (now, parsed)
  return parsed


def _daily_file_id(plant: str, ym: Optional[str]) -> Optional[str]:
  """The daily workbook file id for a plant, for ``ym`` or the latest month.

  For PIPE, any month not explicitly pinned in sources.py is resolved via
  ``source_registry.get_pipe_file_id`` (title-based Drive search → Postgres
  cache → in-process cache).  Other plants still require an explicit pin.
  """
  files = sources.DAILY_SOURCES.get(plant, {}).get("files", {})
  if ym and ym in (files or {}):
      return files[ym]
  # PIPE: auto-discover months not pinned in sources.py
  if plant == "PIPE" and ym:
      try:
          import source_registry as _reg
          result = _reg.get_pipe_file_id(ym)
          if result and result.get("file_id"):
              return result["file_id"]
      except Exception:
          pass
  if not files:
      return None
  return files[sorted(files)[-1]]


def pipe_run5_parsed(ym: str) -> dict:
  """Return the parsed Report-5 dict for the PIPE daily workbook for month ``ym``.

  Reads from the L1 in-process cache when the tab was already fetched by
  ``_load_daily`` in the same request, so no extra network call is made.  The
  result maps every machine label to its Report-5 info dict (per_day, run_days,
  run_hours, ideal_month_hours, …).  Returns ``{}`` if the file or token is
  unavailable or the Report-5 tab cannot be parsed.

  Used by generators that need the **full** set of machines from Report-5
  (including idle machines with 0 run hours that have no daily production
  records) when computing a correct TOTAL-row M/C Efficiency denominator.
  """
  fid = _daily_file_id("PIPE", ym)
  if not fid:
      return {}
  token = _get_access_token()
  if not token:
      return {}
  return parsers.parse_pipe_run5(read_values(fid, "Report-5", token))


def workbook_index(plant: str, ym: Optional[str] = None,
                   token: Optional[str] = None) -> List[dict]:
  """Parsed Index for a plant's daily workbook (``ym`` or latest month)."""
  return read_index(_daily_file_id(plant, ym) or "", token)


def _norm_tab(s: str) -> str:
  """Tab title normaliser for existence checks: strip spaces, lower-case.

  The Index lists "Report-8 (A)" while the real tab is "Report-8(A)"; matching
  on the space-stripped form bridges that cosmetic gap."""
  return re.sub(r"\s+", "", str(s or "")).lower()


def resolve_report_tab(plant: str, keywords, fallback: str,
                       ym: Optional[str] = None,
                       token: Optional[str] = None,
                       require_sliceable: bool = True) -> Tuple[str, bool]:
  """Resolve a production/summary tab by Index DESCRIPTION, not by number.

  ``keywords`` is a string or list of substrings that must ALL appear in a
  report's description. Returns ``(tab_title, matched_via_index)``. When the
  Index is unavailable, the matched report's tab doesn't exist, or nothing
  matches, returns ``(fallback, False)`` so figures never depend on the Index.

  ``require_sliceable`` (default True) enforces the Index's own frequency rule:
  daily ingestion may ONLY resolve to a Daily (sliceable) report, so a weekly or
  monthly snapshot tab whose description happens to share keywords can never be
  selected for per-day figures. Pass False to resolve any frequency.
  """
  if isinstance(keywords, str):
      keywords = [keywords]
  kws = [k.lower() for k in keywords if k]
  reports = workbook_index(plant, ym, token)
  if not reports:
      return fallback, False
  token = token or _get_access_token()
  titles = []
  try:
      titles = list_tabs(_daily_file_id(plant, ym) or "", token) if token else []
  except SheetReadError:
      titles = []
  title_norm = {_norm_tab(t): t for t in titles}
  for rep in reports:
      if require_sliceable and not rep.get("sliceable"):
          continue
      desc = str(rep.get("description", "")).lower()
      if kws and all(k in desc for k in kws):
          cand = rep.get("report", "")
          real = title_norm.get(_norm_tab(cand))
          if real:
              return real, True
          # Tab listing unavailable (offline / transient list_tabs failure): we
          # cannot verify the Index-named tab actually exists, so we MUST keep
          # the configured fallback — a figure must never depend on an
          # unverified Index id. The only safe switch is when the candidate is
          # the SAME tab as the fallback modulo spacing (a no-op), in which case
          # we still return the known-good configured fallback string.
          if not titles and cand and _norm_tab(cand) == _norm_tab(fallback):
              return fallback, True
  return fallback, False


def _wired_daily_tabs(plant: str) -> set:
  """Tab titles the app actively reads for a plant (from _DAILY_LAYOUTS)."""
  wired = set()
  for e in _DAILY_LAYOUTS.get(plant, []):
      for k in ("tab", "report5_tab"):
          if e.get(k):
              wired.add(_norm_tab(e[k]))
  return wired


def index_catalogue(plant: str, ym: Optional[str] = None,
                    token: Optional[str] = None) -> dict:
  """Index metadata for a plant's workbook, annotated with wired/unwired status.

  Returns {available, plant, file_id, month, reports:[...]} where each report
  gains ``wired`` (the app reads this tab) and ``tab_exists`` (a matching tab is
  present in the workbook). Used by the Data Health page to show what is ingested
  versus "available — not yet built".
  """
  file_id = _daily_file_id(plant, ym)
  reports = read_index(file_id or "", token)
  out = {
      "available": bool(reports),
      "plant": plant,
      "plant_name": sources.PLANT_NAMES.get(plant, plant),
      "file_id": file_id or "",
      "month": ym or (sorted(sources.DAILY_SOURCES.get(plant, {}).get("files", {}))[-1]
                      if sources.DAILY_SOURCES.get(plant, {}).get("files") else ""),
      "reports": [],
  }
  if not reports:
      return out
  wired = _wired_daily_tabs(plant)
  titles = []
  token = token or _get_access_token()
  if token:
      try:
          titles = list_tabs(file_id or "", token)
      except SheetReadError:
          titles = []
  title_norm = {_norm_tab(t) for t in titles}
  for rep in reports:
      key = _norm_tab(rep.get("report", ""))
      out["reports"].append({
          **rep,
          "wired": key in wired,
          "tab_exists": (key in title_norm) if titles else True,
      })
  return out


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
  # Re-scan Drive folders first so a brand-new monthly workbook is in the
  # sources map before we pull the recent daily months below.
  try:
      ensure_daily_discovery(force=True)
  except Exception as exc:                       # noqa: BLE001 — best-effort leg
      errors.append(f"discovery: {exc}")
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
  # Warm compound data and pipe moulds — these are common secondary pages that
  # are NOT covered by the monthly/daily legs above, so without explicit warming
  # the first visit after TTL expiry always triggers a cold Sheets fetch.
  try:
      load_compound_data(recent)
  except Exception as exc:                       # noqa: BLE001 — best-effort leg
      errors.append(f"compound: {exc}")
  try:
      import datetime as _dt
      _cur_ym = _dt.date.today().strftime("%Y-%m")
      load_pipe_moulds(_cur_ym)
  except Exception as exc:                       # noqa: BLE001 — best-effort leg
      errors.append(f"pipe_moulds: {exc}")
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
  try:
      ensure_daily_discovery(force=True)
  except Exception:              # noqa: BLE001 — best-effort
      pass
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


# ---------------------------------------------------------------------------
# Planning domain — on-demand loaders (demand / pieces / mould standards)
# NEVER called from "/" or get_records — planning-only routes only.
# ---------------------------------------------------------------------------
_planning_cache: dict = {}
_ptmt_pieces_cache: dict = {}
_ptmt_master_cache: dict = {}
_mould_cap_cache: dict = {}
_material_cache     : dict = {}
_maintenance_cache  : dict = {}
_manpower_cache     : dict = {}
_yield_cache        : dict = {}
_mixer_cache        : dict = {}
_toolroom_cache     : dict = {}
_wastage_cache      : dict = {}
_PLANNING_TTL = 1800.0
_planning_lock      = threading.Lock()
_ptmt_pc_lock       = threading.Lock()
_ptmt_ms_lock       = threading.Lock()
_mould_cap_lock     = threading.Lock()
_material_lock      = threading.Lock()
_maintenance_lock   = threading.Lock()
_manpower_lock      = threading.Lock()
_yield_lock         = threading.Lock()
_mixer_lock         = threading.Lock()
_toolroom_lock      = threading.Lock()
_wastage_lock       = threading.Lock()


def load_planning(plant: str, ym: str) -> List:
    """Load PlanRecord list from PLANNING_SOURCES for *plant* + *ym*.

    Reads the Report-1* tabs from the same workbook as DAILY_SOURCES so no
    extra Drive sharing is needed. Cached 15 min. Returns [] on any error.
    """
    import planning as _pl, parsers as _par
    key = (plant, ym)
    now = time.time()
    c = _planning_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _planning_lock:
        c = _planning_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            tabs_cfg = sources.PLANNING_SOURCES.get(plant, {}).get("tabs", [])
            for tc in tabs_cfg:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    parser = tc["parser"]
                    if parser == "pipe_report1":
                        recs.extend(_par.parse_pipe_report1(vals, tc["family"], ym))
                    elif parser == "ptmt_report1":
                        recs.extend(_par.parse_ptmt_report1(vals, tc["family"], ym))
                except Exception:
                    continue
        _planning_cache[key] = (now, recs)
        return recs


def load_ptmt_pieces(ym: str) -> dict:
    """Load PTMT Report-7 (item × machine × date) for *ym*.

    Returns a dict with per-machine pcs/kg rollup and grand totals. Off the
    critical path — only called from /planning/ptmt-capacity.
    """
    import parsers as _par
    EMPTY = {"available": False, "total_pcs": 0.0, "total_kg": 0.0,
             "n_rows": 0, "n_dates": 0, "machines": {}, "ym": ym}
    now = time.time()
    c = _ptmt_pieces_cache.get(ym)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _ptmt_pc_lock:
        c = _ptmt_pieces_cache.get(ym)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id("PTMT", ym)
        r7_tab = sources.PLANNING_SOURCES["PTMT"]["report7_tab"]
        result = EMPTY
        if fid:
            try:
                vals = read_values(fid, r7_tab, token)
                result = _par.parse_ptmt_report7(vals, ym)
            except Exception:
                pass
        _ptmt_pieces_cache[ym] = (now, result)
        return result


def load_ptmt_master(ym: str) -> list:
    """Load PTMT MASTER mould standards for *ym*.

    Returns a list of MouldStd. Off the critical path.
    """
    import parsers as _par
    now = time.time()
    c = _ptmt_master_cache.get(ym)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _ptmt_ms_lock:
        c = _ptmt_master_cache.get(ym)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id("PTMT", ym)
        master_tab = sources.PLANNING_SOURCES["PTMT"]["master_tab"]
        stds: list = []
        if fid:
            try:
                vals = read_values(fid, master_tab, token)
                stds = _par.parse_ptmt_master(vals)
            except Exception:
                pass
        _ptmt_master_cache[ym] = (now, stds)
        return stds


def load_moulding_capacity(ym: str) -> dict:
    """Derive Moulding standard-vs-actual capacity from Report-12 (B3).

    Reads Report-12 from the PIPE workbook for *ym* and computes per-machine
    theoretical pcs/hr (from Mould Cavity + Cycle Time) vs actual pcs/hr.
    Off the critical path — only called from /planning.
    """
    import parsers as _par
    now = time.time()
    c = _mould_cap_cache.get(ym)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _mould_cap_lock:
        c = _mould_cap_cache.get(ym)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id("PIPE", ym)
        result: dict = {}
        if fid:
            try:
                vals = read_values(fid, "Report-12", token)
                result = _par.parse_report12_capacity(vals)
            except Exception:
                pass
        _mould_cap_cache[ym] = (now, result)
        return result


def load_material_records(plant: str, ym: str) -> List:
    """Load MaterialRecord list for *plant* + *ym* from material_tabs.

    Reads Report-2/3/4 from the same workbook as DAILY_SOURCES — no new
    Drive sharing needed. Cached 15 min. NEVER called from "/" or any
    production-metrics path; only called from /materials on demand.

    Returns [] on any error so the /materials route degrades gracefully.
    """
    import parsers as _par
    key = (plant, ym)
    now = time.time()
    c = _material_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _material_lock:
        c = _material_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            material_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("material_tabs", [])
            for tc in material_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_material_stock(vals, plant, tc["category"]))
                except Exception:
                    continue
        _material_cache[key] = (now, recs)
        return recs


def load_maintenance_records(plant: str, ym: str) -> list:
    """Load MaintenanceRecord list for *plant* from maintenance_tabs.

    Reads from the same workbook as DAILY_SOURCES — no new Drive sharing.
    Cached 15 min.  NEVER called from '/' or any production-metrics path;
    only called from /maintenance on demand.
    """
    import parsers as _par
    key = (plant, ym)
    now = time.time()
    c = _maintenance_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _maintenance_lock:
        c = _maintenance_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            maint_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("maintenance_tabs", [])
            for tc in maint_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_maintenance(vals, plant))
                except Exception:
                    continue
        _maintenance_cache[key] = (now, recs)
        return recs


def load_manpower_records(plant: str, ym: str) -> list:
    """Load ManpowerRecord list for *plant* + *ym* from manpower_tabs.

    Reads from the same workbook as DAILY_SOURCES — no new Drive sharing.
    Cached 15 min.  NEVER called from '/' or any production-metrics path;
    only called from /manpower on demand.

    GUARDRAIL: ManpowerRecord is NEVER a production-output record.
    """
    import parsers as _par
    key = (plant, ym)
    now = time.time()
    c = _manpower_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _manpower_lock:
        c = _manpower_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            mp_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("manpower_tabs", [])
            for tc in mp_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_manpower(vals, plant, tc["shift"], ym))
                except Exception:
                    continue
        _manpower_cache[key] = (now, recs)
        return recs


def load_yield_records(plant: str, ym: str) -> list:
    """Load YieldRecord list from Report-15/13/14 for *plant* + *ym*.
    Phase 2D — on-demand only, NEVER called on '/'.
    Cached 15 min.  Returns [] on any error.
    """
    import planning as _pl
    import parsers as _par

    key = f"yield:{plant}:{ym}"
    now = time.time()
    c = _yield_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _yield_lock:
        c = _yield_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            y_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("yield_tabs", [])
            for tc in y_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    parser = tc["parser"]
                    if parser == "yield_report15":
                        recs.extend(_par.parse_yield_report15(vals, plant, ym))
                    elif parser == "yield_report13":
                        recs.extend(_par.parse_yield_report13(vals, plant, ym))
                    elif parser == "yield_report14":
                        recs.extend(_par.parse_yield_report14(vals, plant, ym))
                except Exception:
                    continue
        _yield_cache[key] = (now, recs)
        return recs


def load_mixer_records(plant: str, ym: str) -> list:
    """Load CompoundBatchRecord list from Report-5(A/B/C/D) for *plant* + *ym*.
    Phase 2D — on-demand only, NEVER called on '/'.
    DISTINCT from compound.py CP-fittings mass-balance.
    Cached 15 min.  Returns [] on any error.
    """
    import planning as _pl
    import parsers as _par

    key = f"mixer:{plant}:{ym}"
    now = time.time()
    c = _mixer_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _mixer_lock:
        c = _mixer_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            m_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("mixer_tabs", [])
            for tc in m_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_mixer_batch(
                        vals, plant,
                        mixer_id=tc.get("mixer_id", ""),
                        ym=ym,
                    ))
                except Exception:
                    continue
        _mixer_cache[key] = (now, recs)
        return recs


def load_toolroom_records(plant: str, ym: str) -> list:
    """Load ToolroomRecord list from Report-21 for *plant* + *ym*.
    Phase 2D — on-demand only, NEVER called on '/'.
    Cached 15 min.  Returns [] on any error.
    """
    import parsers as _par

    key = f"toolroom:{plant}:{ym}"
    now = time.time()
    c = _toolroom_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _toolroom_lock:
        c = _toolroom_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            t_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("toolroom_tabs", [])
            for tc in t_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_toolroom(vals, plant, ym))
                except Exception:
                    continue
        _toolroom_cache[key] = (now, recs)
        return recs


def load_wastage_records(plant: str, ym: str) -> list:
    """Load WastageRecord list from Report-10 for *plant* (PTMT).
    Phase 2D — on-demand only, NEVER called on '/'.
    Wastage master is static (not date-filtered); *ym* is used only for file-ID lookup.
    Cached 15 min.  Returns [] on any error.
    """
    import parsers as _par

    key = f"wastage:{plant}:{ym}"
    now = time.time()
    c = _wastage_cache.get(key)
    if c and now - c[0] < _PLANNING_TTL:
        return c[1]
    with _wastage_lock:
        c = _wastage_cache.get(key)
        if c and now - c[0] < _PLANNING_TTL:
            return c[1]
        token = _get_access_token()
        if not token:
            raise SheetReadError("Google Sheets connection not authorized.")
        fid = sources.planning_file_id(plant, ym)
        recs: list = []
        if fid:
            w_tabs = sources.PLANNING_SOURCES.get(plant, {}).get("wastage_tabs", [])
            for tc in w_tabs:
                try:
                    vals = read_values(fid, tc["tab"], token)
                    recs.extend(_par.parse_wastage(vals, plant))
                except Exception:
                    continue
        _wastage_cache[key] = (now, recs)
        return recs


# ---------------------------------------------------------------------------
# Corrective re-plan actuals (Report-11 + Report-12 raw values)
# ---------------------------------------------------------------------------

_replan_cache: dict = {}
_replan_lock  = threading.Lock()
_REPLAN_TTL   = 15 * 60  # 15 min — same as planning TTL


def load_corrective_replan_actuals(ym: str) -> dict:
    """Return raw Report-11 and Report-12 values for *ym* (PIPE workbook).

    Returns a dict:
      {
        "file_id": str,
        "r11": list[list],   # raw values from Report-11
        "r12": list[list],   # raw values from Report-12
        "error": str | None,
      }

    Cached 15 min. Used only by /planning/corrective-replan (never on '/').
    """
    now = time.time()
    c = _replan_cache.get(ym)
    if c and now - c[0] < _REPLAN_TTL:
        return c[1]
    with _replan_lock:
        c = _replan_cache.get(ym)
        if c and now - c[0] < _REPLAN_TTL:
            return c[1]

        token = _get_access_token()
        if not token:
            result: dict = {
                "file_id": "", "r11": [], "r12": [],
                "error": "Google Sheets connection not authorized.",
            }
            _replan_cache[ym] = (now, result)
            return result

        fid = sources.planning_file_id("PIPE", ym)
        if not fid:
            import source_registry as _sr
            reg = _sr.get_pipe_file_id(ym)
            fid = reg["file_id"] if reg else None

        if not fid:
            result = {
                "file_id": "", "r11": [], "r12": [],
                "error": f"No PIPE workbook registered for {ym}.",
            }
            _replan_cache[ym] = (now, result)
            return result

        r11, r12 = [], []
        error = None
        try:
            r11 = read_values(fid, "Report-11", token)
        except Exception as exc:
            error = f"Report-11 read failed: {exc}"
        try:
            r12 = read_values(fid, "Report-12", token)
        except Exception as exc:
            error = (error + "; " if error else "") + f"Report-12 read failed: {exc}"

        result = {"file_id": fid, "r11": r11, "r12": r12, "error": error}
        _replan_cache[ym] = (now, result)
        return result
