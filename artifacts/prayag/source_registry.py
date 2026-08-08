"""source_registry.py — Auto-discovery and persistence of monthly PIPE workbooks.

Resolves the Google Sheets file ID for a given month's Pipe & Fitting
workbook by searching Drive by title.  Resolved IDs are cached in Postgres
(daily_source_registry) and in-process memory so subsequent requests are fast.

OVERRIDE PRECEDENCE (highest → lowest)
---------------------------------------
  1. sources.py explicit pin   — hand-verified, always authoritative
  2. In-process memory cache   — sub-millisecond, populated from DB on miss
  3. Postgres daily_source_registry — cross-worker, populated from Drive on miss
  4. Drive title search        — slow (1-3 s), cached on success

TITLE PATTERN
-------------
  "5. Pipe & Fitting Plant Date Sheet & Monthly Report - <MON> ' <YYYY>"
  e.g. "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"
  Match is case-insensitive; spaces around the apostrophe are tolerated;
  a leading section number ("5.") is optional.

MULTIPLE-MATCH POLICY
---------------------
  If Drive returns several files for the same month:
    1. Prefer owner preeti.chauhan@prayagindia.com
    2. Else pick most recently modified (Drive already returns newest-first)
  The choice is always logged so it is auditable.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

import store

logger = logging.getLogger("prayag.source_registry")

# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------

_MONTH_ABBREVS = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
]

_PREFERRED_OWNER = "preeti.chauhan@prayagindia.com"


def _title_month_token(month: int) -> str:
    """3-letter upper-case month abbreviation, e.g. 8 → 'AUG'."""
    if not (1 <= month <= 12):
        raise ValueError(f"month must be 1-12, got {month}")
    return _MONTH_ABBREVS[month - 1]


def matches_pipe_fitting_title(name: str, year: int, month: int) -> bool:
    """True when *name* looks like the Pipe & Fitting report for *year*/*month*.

    Accepts:
      - "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"
      - "Pipe & Fitting ... - Aug ' 2026"
      - "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG '2026"
      - mixed case throughout
    """
    name_u = name.upper()
    if "PIPE" not in name_u:
        return False
    if "FITTING" not in name_u:
        return False
    mon = _title_month_token(month)
    if mon not in name_u:
        return False
    if str(year) not in name_u:
        return False
    return True


# ---------------------------------------------------------------------------
# DB layer  (daily_source_registry table)
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS daily_source_registry (
    plant           TEXT        NOT NULL,
    year_month      TEXT        NOT NULL,
    file_id         TEXT        NOT NULL,
    file_name       TEXT        NOT NULL DEFAULT '',
    modified_time   TEXT        NOT NULL DEFAULT '',
    discovered_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (plant, year_month)
);
"""


def _init_table() -> None:
    if not store.AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(_DDL)
    except Exception:
        logger.exception("source_registry: DDL failed")


def _db_get(plant: str, ym: str) -> Optional[Dict]:
    """Return registry row as dict, or None when not found."""
    if not store.AVAILABLE:
        return None
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT file_id, file_name, modified_time "
                "FROM daily_source_registry "
                "WHERE plant=%s AND year_month=%s",
                (plant, ym),
            )
            row = cur.fetchone()
            if row:
                return {"file_id": row[0], "file_name": row[1], "modified_time": row[2]}
    except Exception:
        logger.exception("source_registry: _db_get failed for %s/%s", plant, ym)
    return None


def _db_put(
    plant: str,
    ym: str,
    file_id: str,
    file_name: str,
    modified_time: str,
) -> None:
    """Upsert a registry row.  Never raises."""
    if not store.AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO daily_source_registry
                       (plant, year_month, file_id, file_name, modified_time)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (plant, year_month) DO UPDATE
                       SET file_id       = EXCLUDED.file_id,
                           file_name     = EXCLUDED.file_name,
                           modified_time = EXCLUDED.modified_time,
                           discovered_at = now()""",
                (plant, ym, file_id, file_name, modified_time),
            )
    except Exception:
        logger.exception("source_registry: _db_put failed for %s/%s", plant, ym)


def get_all_registered() -> List[Dict]:
    """All registry rows ordered by (plant, year_month)."""
    if not store.AVAILABLE:
        return []
    _init_table()
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT plant, year_month, file_id, file_name, modified_time, discovered_at "
                "FROM daily_source_registry ORDER BY plant, year_month"
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        logger.exception("source_registry: get_all_registered failed")
        return []


# ---------------------------------------------------------------------------
# In-process memory cache
# ---------------------------------------------------------------------------

# (plant, ym) → {"file_id", "file_name", "modified_time"}
_mem_cache: Dict[Tuple[str, str], Dict] = {}


# ---------------------------------------------------------------------------
# Drive title search
# ---------------------------------------------------------------------------

def _drive_search(query: str, drive_token: str) -> List[dict]:
    """Run a Drive files.list search.  Returns [{id, name, modifiedTime, owners}].

    Paginates up to 5 pages; ordered newest-first by the API so the
    multiple-match tie-break (prefer most recent) is already sorted.
    Uses supportsAllDrives so files in Shared Drives are included.
    """
    out: list = []
    page_token = None
    q = urllib.parse.quote(query)
    for _ in range(5):
        url = (
            f"https://www.googleapis.com/drive/v3/files?q={q}"
            "&fields=nextPageToken,files(id,name,modifiedTime,owners)"
            "&pageSize=50&orderBy=modifiedTime+desc"
            "&supportsAllDrives=true&includeItemsFromAllDrives=true"
        )
        if page_token:
            url += "&pageToken=" + urllib.parse.quote(page_token)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {drive_token}"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        out.extend(data.get("files") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


def find_monthly_workbook(
    year: int,
    month: int,
    drive_token: Optional[str] = None,
) -> Dict:
    """Locate the Pipe & Fitting monthly workbook for *year*/*month* via Drive.

    Returns::

        {
            "file_id":       str,
            "file_name":     str,
            "modified_time": str,   # ISO-8601, e.g. "2026-08-01T10:30:00.000Z"
            "match_count":   int,   # >1 means a tie-break was applied
        }

    Raises ``ValueError`` with a clear human-readable message when no workbook
    is found.  Never returns None — a failed resolution is always an error.

    NOTE: callers should check sources.py pins first; this function is the
    discovery layer only.
    """
    import sheets as _sh
    token = drive_token or _sh._get_drive_token()
    if not token:
        raise ValueError(
            "Google Drive connection unavailable — cannot search for the monthly "
            "workbook.  Ensure the google-drive connector is authorised."
        )

    mon_abbrev = _title_month_token(month)

    # Drive `name contains` is case-insensitive and a full-text substring match.
    # We AND four fragments: "Pipe", "Fitting", the month abbrev, and the year.
    query = (
        f"name contains 'Pipe' and name contains 'Fitting' "
        f"and name contains '{mon_abbrev}' and name contains '{year}' "
        "and mimeType='application/vnd.google-apps.spreadsheet' "
        "and trashed=false"
    )

    try:
        candidates = _drive_search(query, token)
    except Exception as exc:
        raise ValueError(
            f"Drive title search failed for {mon_abbrev} {year}: {exc}"
        ) from exc

    # Tighten to files whose full title actually matches the pattern.
    matches = [
        f for f in candidates
        if matches_pipe_fitting_title(f.get("name", ""), year, month)
    ]

    if not matches:
        raise ValueError(
            f"No Pipe & Fitting monthly workbook found in Drive for "
            f"{mon_abbrev} {year}.  "
            "Ensure the workbook exists and is shared with the connected Google "
            "account, then trigger a reseed."
        )

    # Tie-break: prefer preeti@, else newest (already sorted newest-first by API).
    chosen = matches[0]
    if len(matches) > 1:
        preferred = [
            f for f in matches
            if any(
                (o.get("emailAddress") or "").lower() == _PREFERRED_OWNER
                for o in (f.get("owners") or [])
            )
        ]
        if preferred:
            chosen = preferred[0]
            logger.info(
                "source_registry: %d match(es) for %s %s — chose preferred "
                "owner (%s): %s",
                len(matches), mon_abbrev, year, _PREFERRED_OWNER,
                chosen.get("name"),
            )
        else:
            logger.info(
                "source_registry: %d match(es) for %s %s — chose most "
                "recently modified: %s",
                len(matches), mon_abbrev, year, chosen.get("name"),
            )

    return {
        "file_id":       chosen.get("id", ""),
        "file_name":     chosen.get("name", ""),
        "modified_time": chosen.get("modifiedTime", ""),
        "match_count":   len(matches),
    }


# ---------------------------------------------------------------------------
# Public resolution API
# ---------------------------------------------------------------------------

def _patch_daily_sources(plant: str, ym: str, file_id: str) -> None:
    """Add *ym → file_id* to sources.DAILY_SOURCES in-memory.

    Additive only — never overwrites a pinned ID.  The reference swap is
    under the GIL so concurrent readers see a consistent snapshot.
    """
    import sources as _src
    cfg = _src.DAILY_SOURCES.get(plant)
    if not cfg:
        return
    existing = cfg.get("files") or {}
    if ym in existing:
        return  # pin wins — never overwrite
    new_files = dict(existing)
    new_files[ym] = file_id
    cfg["files"] = new_files  # atomic reference swap under GIL


def get_pipe_file_id(ym: str) -> Optional[Dict]:
    """Resolve the PIPE monthly workbook file ID for *ym* (e.g. '2026-08').

    Returns ``{"file_id", "file_name", "modified_time", "source"}`` where
    ``source`` is one of ``"pinned" | "cached" | "discovered"``, or ``None``
    when resolution fails.

    Precedence:
      1. sources.py pin       → source="pinned"
      2. In-process cache     → source="cached"
      3. Postgres cache       → source="cached"
      4. Drive title search   → source="discovered"
    """
    import sources as _src

    # 1. Pinned override in sources.py
    pinned_id = (_src.DAILY_SOURCES.get("PIPE") or {}).get("files", {}).get(ym)
    if pinned_id:
        return {
            "file_id":       pinned_id,
            "file_name":     "",
            "modified_time": "",
            "source":        "pinned",
        }

    # 2. In-process memory cache
    hit = _mem_cache.get(("PIPE", ym))
    if hit:
        return {**hit, "source": "cached"}

    # 3. Postgres cache
    _init_table()
    db_hit = _db_get("PIPE", ym)
    if db_hit:
        _mem_cache[("PIPE", ym)] = db_hit
        _patch_daily_sources("PIPE", ym, db_hit["file_id"])
        return {**db_hit, "source": "cached"}

    # 4. Drive title search
    try:
        year, month = int(ym[:4]), int(ym[5:7])
    except (ValueError, IndexError):
        return None

    try:
        result = find_monthly_workbook(year, month)
    except ValueError as exc:
        logger.warning("source_registry: resolution failed for PIPE/%s — %s", ym, exc)
        return None

    entry: Dict = {
        "file_id":       result["file_id"],
        "file_name":     result["file_name"],
        "modified_time": result["modified_time"],
    }
    _mem_cache[("PIPE", ym)] = entry
    _db_put("PIPE", ym, entry["file_id"], entry["file_name"], entry["modified_time"])
    _patch_daily_sources("PIPE", ym, entry["file_id"])
    logger.info(
        "source_registry: auto-registered PIPE/%s → %s (%s)",
        ym, entry["file_id"], entry["file_name"],
    )
    return {**entry, "source": "discovered"}


def ensure_fy_months_registered(
    fy_months: Optional[List[str]] = None,
) -> Dict:
    """Best-effort: resolve any FY months not yet pinned in sources.py.

    Calls ``get_pipe_file_id`` for each month in *fy_months* (defaults to
    ``sources.FY_MONTHS``).  Failures are collected, never raised.

    Returns::

        {
            "registered": [ym, ...],   # resolved OK
            "missing":    [ym, ...],   # resolution failed / no workbook found
            "errors":     {ym: str},   # per-month error messages
        }
    """
    import sources as _src
    if fy_months is None:
        fy_months = _src.FY_MONTHS

    registered: List[str] = []
    missing: List[str] = []
    errors: Dict[str, str] = {}
    for ym in fy_months:
        try:
            result = get_pipe_file_id(ym)
            if result and result.get("file_id"):
                registered.append(ym)
            else:
                missing.append(ym)
        except Exception as exc:
            errors[ym] = str(exc)
            missing.append(ym)

    return {"registered": registered, "missing": missing, "errors": errors}


# ---------------------------------------------------------------------------
# Panel data for the UI
# ---------------------------------------------------------------------------

def get_monthly_workbook_panel() -> List[Dict]:
    """Rows for the 'Registered PIPE Workbooks' audit panel in the UI.

    Each row::

        {
            "year_month":    str,
            "file_id":       str,
            "file_name":     str,
            "modified_time": str,
            "discovered_at": datetime | None,
            "source":        "pinned" | "discovered",
            "empty":         bool,
        }

    Combines pinned entries from sources.py with auto-discovered entries from
    the Postgres registry, deduped (pinned wins on overlap).
    """
    import sources as _src
    _init_table()

    pinned: Dict[str, str] = (
        (_src.DAILY_SOURCES.get("PIPE") or {}).get("files") or {}
    )
    db_rows: Dict[str, Dict] = {
        r["year_month"]: r for r in get_all_registered() if r.get("plant") == "PIPE"
    }

    all_months: set = set(pinned.keys()) | set(db_rows.keys())
    rows: List[Dict] = []
    for ym in sorted(all_months):
        if ym in pinned:
            dr = db_rows.get(ym, {})
            rows.append({
                "year_month":    ym,
                "file_id":       pinned[ym],
                "file_name":     dr.get("file_name") or "",
                "modified_time": dr.get("modified_time") or "",
                "discovered_at": dr.get("discovered_at"),
                "source":        "pinned",
                "empty":         ("PIPE", ym) in _src.EMPTY_SOURCES,
            })
        else:
            dr = db_rows[ym]
            rows.append({
                "year_month":    ym,
                "file_id":       dr["file_id"],
                "file_name":     dr["file_name"],
                "modified_time": dr["modified_time"],
                "discovered_at": dr.get("discovered_at"),
                "source":        "discovered",
                "empty":         False,
            })
    return rows
