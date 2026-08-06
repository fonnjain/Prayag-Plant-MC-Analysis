"""mp_seed_provenance.py — Seed provenance tracking for Machine Planning.

Records, for each seeded mp_* table:
  - Which Drive source file was read (file ID + name, comma-separated for
    tables with multiple sources)
  - The Drive file's modifiedTime at seed time (MAX across all source files)
  - When the seed was written (seeded_at)
  - How many rows were seeded

Used at plan time to attach staleness warnings when seed data may be
outdated relative to its source sheet.  Never raises; never blocks a plan run.
Rejection / wastage seeds read from many monthly workbooks — for those,
Drive comparison is skipped and staleness is detected via empty row count.
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict, List, Optional

import store

logger = logging.getLogger("prayag.mp_seed_provenance")

# ── DDL ──────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS mp_seed_provenance (
    table_name            TEXT        PRIMARY KEY,
    source_file_ids       TEXT        NOT NULL DEFAULT '',
    source_file_names     TEXT        NOT NULL DEFAULT '',
    source_modified_time  TIMESTAMPTZ,
    seeded_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    row_count             INT         NOT NULL DEFAULT 0
);
"""

# ── Constants ─────────────────────────────────────────────────────────────────

# Seeds older than this many days → amber even when Drive shows no change
_AMBER_DAYS = 14

# Human-readable label for each tracked table
TABLE_LABELS: Dict[str, str] = {
    "mp_bom_weight":        "BOM weights",
    "mp_machine":           "Machine roster",
    "mp_routing":           "Pipe routing",
    "mp_per_hour":          "Per-hour rates",
    "mp_compound_recipe":   "Compound recipes",
    "mp_wastage_summary":   "Wastage rates",
    "mp_rejection_summary": "Rejection rates",
}


# ── Initialisation ────────────────────────────────────────────────────────────

def init_provenance_table() -> None:
    """Create mp_seed_provenance if it does not exist (idempotent)."""
    if not store.AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(_DDL)
    except Exception:
        logger.exception("mp_seed_provenance: DDL failed")


# ── Write ─────────────────────────────────────────────────────────────────────

def record_seed(
    table_name: str,
    source_file_ids: str = "",
    source_file_names: str = "",
    source_modified_time: Optional[str] = None,
    row_count: int = 0,
) -> None:
    """Upsert one provenance row for *table_name*.

    ``source_modified_time`` may be an ISO-8601 string returned by the Drive
    API (e.g. ``"2026-07-15T10:32:00.000Z"``) or ``None`` when the table reads
    from multiple workbooks with no single Drive file to compare against.
    When multiple source files exist, pass the MAX modifiedTime string.
    """
    if not store.AVAILABLE:
        return
    mod_ts: Optional[datetime.datetime] = None
    if source_modified_time:
        try:
            mod_ts = datetime.datetime.fromisoformat(
                source_modified_time.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            pass
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO mp_seed_provenance
                           (table_name, source_file_ids, source_file_names,
                            source_modified_time, seeded_at, row_count)
                   VALUES  (%s, %s, %s, %s, now(), %s)
                   ON CONFLICT (table_name) DO UPDATE
                       SET source_file_ids      = EXCLUDED.source_file_ids,
                           source_file_names    = EXCLUDED.source_file_names,
                           source_modified_time = EXCLUDED.source_modified_time,
                           seeded_at            = now(),
                           row_count            = EXCLUDED.row_count""",
                (table_name, source_file_ids, source_file_names, mod_ts, row_count),
            )
    except Exception:
        logger.exception("mp_seed_provenance: record_seed failed for %s", table_name)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_all_provenance() -> List[Dict]:
    """Return all provenance rows as list of dicts, ordered by table_name."""
    if not store.AVAILABLE:
        return []
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT table_name, source_file_ids, source_file_names,
                          source_modified_time, seeded_at, row_count
                     FROM mp_seed_provenance
                    ORDER BY table_name"""
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        logger.exception("mp_seed_provenance: get_all_provenance failed")
        return []


# ── Status panel (UI) ─────────────────────────────────────────────────────────

def _parse_iso(s: Optional[str]) -> Optional[datetime.datetime]:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _fetch_current_mod(fids_str: str, drive_token: str) -> Optional[datetime.datetime]:
    """Fetch max(modifiedTime) across all comma-separated file IDs.  Best-effort."""
    try:
        import sheets as _sh
        mods: List[datetime.datetime] = []
        for fid in [f.strip() for f in fids_str.split(",") if f.strip()]:
            meta = _sh.drive_file_meta(fid, drive_token)
            dt = _parse_iso(meta.get("modified_time"))
            if dt:
                mods.append(dt)
        return max(mods) if mods else None
    except Exception:
        return None


def get_status_panel(drive_token: Optional[str] = None) -> List[Dict]:
    """Return per-table status rows for the Seed Status panel.

    Each row dict has keys:
      table_name, label, row_count, seeded_at, source_modified_time,
      current_source_modified_time, status, days_behind, has_file

    ``status``: "green" | "amber" | "red" | "missing"
      green  — seed is at least as fresh as the source file
      amber  — no Drive mismatch detected but seed is older than _AMBER_DAYS
      red    — source file has been modified since the seed was written
      missing— row_count == 0 (data was never seeded or was cleared)
    """
    rows = get_all_provenance()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    out = []

    for row in rows:
        table     = row["table_name"]
        label     = TABLE_LABELS.get(table, table)
        seeded    = row["seeded_at"]
        stored_mod = row["source_modified_time"]
        fids      = (row["source_file_ids"] or "").strip()
        r_count   = row["row_count"]

        has_file = bool(fids)
        current_mod: Optional[datetime.datetime] = None
        if has_file and drive_token:
            current_mod = _fetch_current_mod(fids, drive_token)

        days_behind: Optional[float] = None
        if r_count == 0:
            status = "missing"
        elif has_file and stored_mod and current_mod and current_mod > stored_mod:
            days_behind = (current_mod - stored_mod).total_seconds() / 86400
            status = "red"
        elif seeded and (now_utc - seeded).days > _AMBER_DAYS:
            status = "amber"
        else:
            status = "green"

        out.append({
            "table_name":                   table,
            "label":                        label,
            "row_count":                    r_count,
            "seeded_at":                    seeded,
            "source_modified_time":         stored_mod,
            "current_source_modified_time": current_mod,
            "status":                       status,
            "days_behind":                  round(days_behind, 1) if days_behind else None,
            "has_file":                     has_file,
        })

    # Include any known table that has no provenance row yet (never seeded)
    known = {r["table_name"] for r in out}
    for tbl, lbl in TABLE_LABELS.items():
        if tbl not in known:
            out.append({
                "table_name": tbl, "label": lbl, "row_count": 0,
                "seeded_at": None, "source_modified_time": None,
                "current_source_modified_time": None,
                "status": "missing", "days_behind": None, "has_file": False,
            })

    out.sort(key=lambda r: r["table_name"])
    return out


# ── Plan-time staleness warnings ──────────────────────────────────────────────

def build_staleness_warnings(
    segment: str,
    drive_token: Optional[str] = None,
) -> List[str]:
    """Return human-readable staleness warnings for a plan run.

    Two classes of warning are produced:
      1. Missing rejection/wastage — silently changes material quantities by
         8-11%; always reported regardless of Drive token availability.
      2. Drive staleness — source file modified after seed; reported only when
         the Drive token is available and the modifiedTime call succeeds.

    Never raises.  Returns [] on any DB/Drive failure.
    """
    if not store.AVAILABLE:
        return []
    try:
        warnings: List[str] = []

        with store._conn() as conn, conn.cursor() as cur:
            # ── 1. Rejection missing? ─────────────────────────────────────
            cur.execute(
                "SELECT COUNT(*) FROM mp_rejection_summary WHERE segment = %s",
                (segment,),
            )
            if cur.fetchone()[0] == 0:
                warnings.append(
                    "Rejection data has never been seeded for this segment — "
                    "this plan applies 0 % rejection (typical range 8–11 %). "
                    "Material quantities are understated. "
                    "Run Reseed Rejection from the Settings page before trusting this plan."
                )

            # ── 2. Wastage missing? ───────────────────────────────────────
            cur.execute(
                "SELECT COUNT(*) FROM mp_wastage_summary WHERE segment = %s",
                (segment,),
            )
            if cur.fetchone()[0] == 0:
                warnings.append(
                    "Wastage data has never been seeded for this segment — "
                    "this plan applies 0 % wastage (measured rate ≈ 0.51 %). "
                    "Run Reseed Wastage from the Settings page."
                )

            # ── 3. Drive staleness (best-effort, skip if no token) ────────
            if drive_token:
                cur.execute(
                    """SELECT table_name, source_file_ids, source_modified_time,
                              seeded_at, row_count
                         FROM mp_seed_provenance
                        WHERE source_file_ids      != ''
                          AND source_modified_time IS NOT NULL"""
                )
                prov_rows = cur.fetchall()
                import sheets as _sh
                for tbl, fids_str, stored_mod, seeded_at, r_count in prov_rows:
                    if not fids_str:
                        continue
                    current_mod = _fetch_current_mod(fids_str, drive_token)
                    if current_mod and stored_mod and current_mod > stored_mod:
                        days = (current_mod - stored_mod).total_seconds() / 86400
                        lbl  = TABLE_LABELS.get(tbl, tbl)
                        seeded_str = seeded_at.date().isoformat() if seeded_at else "unknown"
                        warnings.append(
                            f"{lbl} seed is {days:.0f} day(s) behind its source sheet "
                            f"(last seeded {seeded_str}; source updated since). "
                            f"Re-seed {lbl.lower()} before trusting this plan."
                        )

        return warnings
    except Exception:
        logger.exception("mp_seed_provenance: build_staleness_warnings failed")
        return []
