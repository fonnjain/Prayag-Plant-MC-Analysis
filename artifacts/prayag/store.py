"""Durable store for manager sign-offs on flagged data (the review trail).

When the four-tier confirmation puts a period into an ``error`` state, the
headline figures are withheld. A manager can review the flags and explicitly
sign off ("Approve & publish"), which releases the withheld figures and records
*who* approved, *when*, and *what data state* they signed off on.

This is an audit trail, so it must be durable: it is stored in the Replit-managed
Postgres (``DATABASE_URL``), append-only. The effective state for a given
(period, data-fingerprint) is simply the most recent row — an ``approve`` row
releases the figures; a later ``revoke`` row puts the gate back.

The whole module degrades gracefully: if Postgres is unavailable, ``effective``
returns ``None`` and ``history`` returns ``[]`` so the gate stays ON (the safe
default), and ``record`` raises ``StoreError`` so the route can show a message.
"""
from __future__ import annotations

import os
import json
import math
import pickle
import dataclasses
import datetime
import secrets
import hashlib
from contextlib import contextmanager
from typing import List, Optional, Dict, Tuple
from zoneinfo import ZoneInfo

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # pragma: no cover - import guard
    psycopg2 = None  # type: ignore

AVAILABLE = bool(os.environ.get("DATABASE_URL")) and psycopg2 is not None

_TABLE = "confirmation_signoffs"
_initialised = False


class StoreError(Exception):
    """Raised when a write cannot be persisted."""


class FrozenSnapshotError(StoreError):
    """A daily freeze is missing, corrupt, or failed integrity validation."""


_FREEZE_HEADER = "daily_freeze_snapshots"
_FREEZE_RECORDS = "daily_freeze_records"
_FREEZE_AUDIT = "daily_freeze_audit"
_FREEZE_CONFIRM = "daily_freeze_confirmations"
_daily_freezes_initialised = False


def _init_daily_freezes() -> None:
    global _daily_freezes_initialised
    if _daily_freezes_initialised:
        return
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {_FREEZE_HEADER} (
            id BIGSERIAL PRIMARY KEY, emitter TEXT NOT NULL, ym TEXT NOT NULL,
            version INTEGER NOT NULL, fingerprint TEXT NOT NULL,
            report_payload JSONB NOT NULL, record_count INTEGER NOT NULL,
            physical_key TEXT NOT NULL DEFAULT '', source_file_id TEXT NOT NULL DEFAULT '',
            schema_version TEXT NOT NULL DEFAULT 'R46-1',
            preview_count INTEGER, live_count INTEGER, high_water INTEGER,
            verification JSONB NOT NULL DEFAULT '{{}}',
            integrity_checksum TEXT NOT NULL DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(emitter, ym, version))""")
        for col, typ in (
            ("physical_key", "TEXT NOT NULL DEFAULT ''"), ("source_file_id", "TEXT NOT NULL DEFAULT ''"),
            ("schema_version", "TEXT NOT NULL DEFAULT 'R46-1'"), ("preview_count", "INTEGER"),
            ("live_count", "INTEGER"), ("high_water", "INTEGER"),
            ("verification", "JSONB NOT NULL DEFAULT '{}'"), ("integrity_checksum", "TEXT NOT NULL DEFAULT ''"),
        ):
            cur.execute(f"ALTER TABLE {_FREEZE_HEADER} ADD COLUMN IF NOT EXISTS {col} {typ}")
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {_FREEZE_RECORDS} (
            snapshot_id BIGINT REFERENCES {_FREEZE_HEADER}(id) ON DELETE RESTRICT,
            ordinal INTEGER NOT NULL, record_payload JSONB NOT NULL,
            PRIMARY KEY(snapshot_id, ordinal))""")
        cur.execute(f"""CREATE UNIQUE INDEX IF NOT EXISTS {_FREEZE_HEADER}_one_active
            ON {_FREEZE_HEADER}(emitter, ym) WHERE active""")
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {_FREEZE_AUDIT} (
            id BIGSERIAL PRIMARY KEY, emitter TEXT NOT NULL, ym TEXT NOT NULL,
            action TEXT NOT NULL, user_id BIGINT, user_email TEXT NOT NULL DEFAULT '',
            snapshot_id BIGINT, detail JSONB NOT NULL DEFAULT '{{}}', created_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        cur.execute(f"""CREATE TABLE IF NOT EXISTS {_FREEZE_CONFIRM} (
            nonce TEXT PRIMARY KEY, user_id BIGINT NOT NULL, emitter TEXT NOT NULL,
            ym TEXT NOT NULL, fingerprint TEXT NOT NULL, payload JSONB NOT NULL,
            action TEXT NOT NULL DEFAULT 'freeze',
            expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ)""")
        cur.execute(
            f"ALTER TABLE {_FREEZE_CONFIRM} "
            "ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'freeze'"
        )
    _daily_freezes_initialised = True


def daily_freeze_confirmation_create(
    *, user_id, emitter, ym, fingerprint, payload, action="freeze", ttl=600
):
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_freezes()
    nonce = secrets.token_urlsafe(32)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""INSERT INTO {_FREEZE_CONFIRM}
          (nonce,user_id,emitter,ym,fingerprint,payload,action,expires_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,now()+make_interval(secs=>%s))""",
          (nonce, user_id, emitter, ym, fingerprint,
           json.dumps(payload, default=str), action, int(ttl)))
    return nonce


def daily_freeze_confirmation_consume(nonce, *, user_id):
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_freezes()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""UPDATE {_FREEZE_CONFIRM} SET used_at=now()
          WHERE nonce=%s AND user_id=%s AND used_at IS NULL AND expires_at>now()
          RETURNING emitter,ym,fingerprint,payload,action""", (nonce, user_id))
        row = cur.fetchone()
    if not row:
        return None
    return {
        "emitter": row[0], "ym": row[1], "fingerprint": row[2],
        "payload": row[3], "action": row[4],
    }


def daily_freeze_audit(*, emitter, ym, action, user_id=None, user_email="", detail=None,
                       snapshot_id=None):
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_freezes()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""INSERT INTO {_FREEZE_AUDIT}
          (emitter,ym,action,user_id,user_email,snapshot_id,detail)
          VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
          (emitter, ym, action, user_id, user_email or "", snapshot_id,
           json.dumps(detail or {}, default=str)))
        return cur.fetchone()[0]


def _freeze_fingerprint(records, reports) -> str:
    payload = {"records": [dataclasses.asdict(r) if dataclasses.is_dataclass(r) else r
                           for r in records], "reports": reports}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def daily_freeze_lock(emitter: str, ym: str):
    """Transaction advisory lock for one logical emitter/month."""
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    @contextmanager
    def locked():
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"daily-freeze:{emitter}:{ym}",))
            yield conn, cur
    return locked()


def daily_freeze_active(emitter: str, ym: str) -> bool:
    if not AVAILABLE:
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: durable store is not configured for {emitter} {ym}"
        )
    try:
        _init_daily_freezes()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {_FREEZE_HEADER} WHERE emitter=%s AND ym=%s AND active ORDER BY version DESC LIMIT 1", (emitter, ym))
            return cur.fetchone() is not None
    except StoreError:
        raise
    except Exception as exc:
        # An active freeze must never degrade to a live Sheets read.
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: cannot establish freeze state for {emitter} {ym}: {exc}"
        ) from exc


def daily_freeze_active_snapshot(emitter: str, ym: str):
    """Return the exact active snapshot identity used by guarded unfreeze."""
    if not AVAILABLE:
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: durable store is not configured for {emitter} {ym}"
        )
    _init_daily_freezes()
    try:
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT id, emitter, ym, version, fingerprint, physical_key, "
                f"source_file_id, record_count, created_at FROM {_FREEZE_HEADER} "
                "WHERE emitter=%s AND ym=%s AND active LIMIT 1",
                (emitter, ym),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as exc:
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: cannot read active snapshot for "
            f"{emitter} {ym}: {exc}"
        ) from exc


def daily_freeze_read(emitter: str, ym: str):
    """Return validated frozen (Record list, report list), or None if inactive."""
    if not AVAILABLE:
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: durable store is not configured for {emitter} {ym}"
        )
    try:
        _init_daily_freezes()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT id, fingerprint, integrity_checksum, schema_version, record_count, report_payload, version, physical_key, source_file_id FROM {_FREEZE_HEADER} WHERE emitter=%s AND ym=%s AND active ORDER BY version DESC LIMIT 1", (emitter, ym))
            header = cur.fetchone()
            if not header:
                return None
            cur.execute(f"SELECT record_payload FROM {_FREEZE_RECORDS} WHERE snapshot_id=%s ORDER BY ordinal", (header[0],))
            rows = [r[0] for r in cur.fetchall()]
        from metrics import Record
        records = [Record(**r) for r in rows]
        if header[3] != "R46-1" or len(records) != int(header[4]):
            raise FrozenSnapshotError(f"DAILY_FREEZE_CORRUPT: invalid schema/count for {emitter} {ym}")
        reports = header[5] if isinstance(header[5], list) else [header[5]]
        fingerprint = _freeze_fingerprint(records, reports)
        if fingerprint != header[1] or fingerprint != header[2]:
            raise FrozenSnapshotError(f"DAILY_FREEZE_CORRUPT: checksum mismatch for {emitter} {ym}")
        reports = [
            dict(
                r,
                frozen=True,
                freeze_emitter=emitter,
                freeze_month=ym,
                freeze_version=header[6],
                freeze_fingerprint=header[1],
                freeze_physical_key=header[7],
                freeze_source_file_id=header[8],
            )
            if isinstance(r, dict) else r
            for r in reports
        ]
        return records, reports
    except FrozenSnapshotError:
        raise
    except Exception as exc:
        raise FrozenSnapshotError(f"DAILY_FREEZE_CORRUPT: unable to read {emitter} {ym}: {exc}") from exc


def daily_freeze_write(emitter, ym, records, reports, *, user_id=None, user_email="",
                       physical_key="", source_file_id="", preview_count=None,
                       live_count=None, high_water=None, verification=None,
                       transaction=None):
    """Append immutable version and make it active; audit failure aborts all writes."""
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_freezes()
    fingerprint = _freeze_fingerprint(records, reports)
    record_payload = [dataclasses.asdict(r) if dataclasses.is_dataclass(r) else r for r in records]
    def _write(cur):
        cur.execute(f"SELECT COALESCE(MAX(version),0)+1 FROM {_FREEZE_HEADER} WHERE emitter=%s AND ym=%s", (emitter, ym))
        version = int(cur.fetchone()[0])
        cur.execute(f"UPDATE {_FREEZE_HEADER} SET active=FALSE WHERE emitter=%s AND ym=%s", (emitter, ym))
        cur.execute(f"""INSERT INTO {_FREEZE_HEADER}
            (emitter,ym,version,fingerprint,report_payload,record_count,physical_key,
             source_file_id,schema_version,preview_count,live_count,high_water,verification,
             integrity_checksum)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'R46-1',%s,%s,%s,%s,%s) RETURNING id""",
            (emitter, ym, version, fingerprint, json.dumps(reports, default=str), len(record_payload),
             physical_key, source_file_id, preview_count, live_count, high_water,
             json.dumps(verification or {}, default=str), fingerprint))
        sid = cur.fetchone()[0]
        for i, payload in enumerate(record_payload):
            cur.execute(f"INSERT INTO {_FREEZE_RECORDS} VALUES (%s,%s,%s)", (sid, i, json.dumps(payload, default=str)))
        cur.execute(
            f"SELECT record_payload FROM {_FREEZE_RECORDS} "
            "WHERE snapshot_id=%s ORDER BY ordinal",
            (sid,),
        )
        stored_payload = [row[0] for row in cur.fetchall()]
        if len(stored_payload) != len(record_payload):
            raise FrozenSnapshotError(
                f"DAILY_FREEZE_WRITE_VERIFY_FAILED: row count mismatch for {emitter} {ym}"
            )
        if _freeze_fingerprint(stored_payload, reports) != fingerprint:
            raise FrozenSnapshotError(
                f"DAILY_FREEZE_WRITE_VERIFY_FAILED: checksum mismatch for {emitter} {ym}"
            )
        cur.execute(f"""INSERT INTO {_FREEZE_AUDIT}(emitter,ym,action,user_id,user_email,snapshot_id,detail)
            VALUES (%s,%s,'freeze',%s,%s,%s,%s)""",
            (emitter, ym, user_id, user_email or "", sid, json.dumps({"fingerprint": fingerprint, "count": len(record_payload)})))
        return sid, version
    if transaction is not None:
        _conn_in_use, cur_in_use = transaction
        sid, version = _write(cur_in_use)
    else:
        with daily_freeze_lock(physical_key or emitter, ym) as (_conn_in_use, cur_in_use):
            sid, version = _write(cur_in_use)
    return {"snapshot_id": sid, "version": version, "fingerprint": fingerprint}


def daily_freeze_unfreeze(
    emitter,
    ym,
    *,
    user_id=None,
    user_email="",
    expected_snapshot_id=None,
    expected_version=None,
    expected_fingerprint=None,
    expected_physical_key=None,
    reason="",
):
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_freezes()
    if expected_snapshot_id is not None:
        lock_key = expected_physical_key or emitter
    else:
        active = daily_freeze_active_snapshot(emitter, ym)
        if not active:
            raise StoreError(f"No active daily freeze exists for {emitter} {ym}.")
        lock_key = active["physical_key"] or emitter
    with daily_freeze_lock(lock_key, ym) as (conn, cur):
        if expected_snapshot_id is not None:
            cur.execute(
                f"UPDATE {_FREEZE_HEADER} SET active=FALSE "
                "WHERE id=%s AND emitter=%s AND ym=%s AND version=%s "
                "AND fingerprint=%s AND physical_key=%s AND active RETURNING id",
                (
                    expected_snapshot_id, emitter, ym, expected_version,
                    expected_fingerprint, expected_physical_key or "",
                ),
            )
        else:
            cur.execute(
                f"UPDATE {_FREEZE_HEADER} SET active=FALSE "
                "WHERE emitter=%s AND ym=%s AND active RETURNING id",
                (emitter, ym),
            )
        snapshot = cur.fetchone()
        if not snapshot:
            raise StoreError(
                f"The active daily freeze for {emitter} {ym} changed after "
                "preview. Preview the unfreeze again."
            )
        cur.execute(f"""INSERT INTO {_FREEZE_AUDIT}(emitter,ym,action,user_id,user_email,detail)
            VALUES (%s,%s,'unfreeze',%s,%s,%s)""",
            (emitter, ym, user_id, user_email or "",
             json.dumps({
                 "snapshot_id": snapshot[0],
                 "reason": str(reason or "").strip(),
             })))


def daily_freeze_history(limit=100):
    if not AVAILABLE:
        return []
    _init_daily_freezes()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""SELECT emitter,ym,version,active,record_count,created_at
                        FROM {_FREEZE_HEADER} ORDER BY created_at DESC LIMIT %s""", (int(limit),))
        return [dict(r) for r in cur.fetchall()]


def daily_freeze_audit_history(limit=100):
    if not AVAILABLE:
        return []
    _init_daily_freezes()
    with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f"""SELECT emitter,ym,action,user_email,detail,created_at
                        FROM {_FREEZE_AUDIT} ORDER BY created_at DESC LIMIT %s""", (int(limit),))
        return [dict(r) for r in cur.fetchall()]


def daily_freeze_state_token(emitter, ym):
    """Monotonic durable token used by readers to invalidate old caches."""
    if not AVAILABLE:
        raise FrozenSnapshotError(
            f"DAILY_FREEZE_STATE_UNAVAILABLE: durable store is not configured for {emitter} {ym}"
        )
    _init_daily_freezes()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""SELECT COALESCE(MAX(created_at), 'epoch'::timestamptz)
                        FROM {_FREEZE_AUDIT} WHERE emitter=%s AND ym=%s""", (emitter, ym))
        row = cur.fetchone()
    return row[0].isoformat() if row and row[0] else None


def _conn():
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    return psycopg2.connect(os.environ["DATABASE_URL"])


def init() -> None:
    """Create the sign-off table if it does not exist (idempotent, lazy)."""
    global _initialised
    if _initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_TABLE} (
        id            BIGSERIAL PRIMARY KEY,
        action        TEXT        NOT NULL,
        period_key    TEXT        NOT NULL,
        from_iso      TEXT        NOT NULL DEFAULT '',
        to_iso        TEXT        NOT NULL DEFAULT '',
        period_label  TEXT        NOT NULL DEFAULT '',
        fingerprint   TEXT        NOT NULL,
        status_at     TEXT        NOT NULL DEFAULT '',
        score_label   TEXT        NOT NULL DEFAULT '',
        error_count   INTEGER     NOT NULL DEFAULT 0,
        warning_count INTEGER     NOT NULL DEFAULT 0,
        issue_count   INTEGER     NOT NULL DEFAULT 0,
        approver      TEXT        NOT NULL,
        role          TEXT        NOT NULL DEFAULT '',
        note          TEXT        NOT NULL DEFAULT '',
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_TABLE}_lookup
        ON {_TABLE} (period_key, fingerprint, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def record(
    action: str,
    *,
    period_key: str,
    fingerprint: str,
    from_iso: str = "",
    to_iso: str = "",
    period_label: str = "",
    status_at: str = "",
    score_label: str = "",
    error_count: int = 0,
    warning_count: int = 0,
    issue_count: int = 0,
    approver: str,
    role: str = "",
    note: str = "",
) -> None:
    """Append a sign-off event. Raises StoreError if it cannot be persisted."""
    if action not in ("approve", "revoke"):
        raise StoreError(f"Unknown action: {action!r}")
    if not (approver or "").strip():
        raise StoreError("An approver name is required.")
    init()
    sql = f"""
        INSERT INTO {_TABLE}
            (action, period_key, from_iso, to_iso, period_label, fingerprint,
             status_at, score_label, error_count, warning_count, issue_count,
             approver, role, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        action, period_key, from_iso, to_iso, period_label, fingerprint,
        status_at, score_label, int(error_count), int(warning_count),
        int(issue_count), approver.strip(), (role or "").strip(),
        (note or "").strip(),
    )
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def effective(period_key: str, fingerprint: str) -> Optional[Dict]:
    """Return the active sign-off for this exact (period, data state), or None.

    The latest row wins: an ``approve`` means figures are released; a ``revoke``
    (or no row) means they stay withheld.
    """
    if not AVAILABLE:
        return None
    try:
        init()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT * FROM {_TABLE}
                    WHERE period_key=%s AND fingerprint=%s
                    ORDER BY created_at DESC, id DESC LIMIT 1""",
                (period_key, fingerprint),
            )
            row = cur.fetchone()
    except Exception:
        return None
    if not row or row["action"] != "approve":
        return None
    return _shape(row)


def history(period_key: Optional[str] = None, limit: int = 50) -> List[Dict]:
    """Recent sign-off events (newest first) for the review trail."""
    if not AVAILABLE:
        return []
    try:
        init()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            if period_key:
                cur.execute(
                    f"""SELECT * FROM {_TABLE} WHERE period_key=%s
                        ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (period_key, limit),
                )
            else:
                cur.execute(
                    f"""SELECT * FROM {_TABLE}
                        ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (limit,),
                )
            rows = cur.fetchall()
    except Exception:
        return []
    return [_shape(r) for r in rows]


def _shape(row: Dict) -> Dict:
    """Normalise a DB row into a template-friendly dict (dd-mm-yyyy dates)."""
    d = dict(row)
    ts = d.get("created_at")
    if isinstance(ts, datetime.datetime):
        d["when_disp"] = ts.strftime("%d-%m-%Y %H:%M")
    else:
        d["when_disp"] = str(ts or "")
    return d


# ---------------------------------------------------------------------------
# Per-issue acknowledgements (a lighter-weight review trail than full sign-off)
# ---------------------------------------------------------------------------
# A manager can mark a single flagged issue as "reviewed / accepted" with an
# optional note. This downgrades that one issue out of the headline gate without
# blanket-approving the whole period. Like the sign-off store this is append-only
# and the most recent row per (period, issue) wins: an ``ack`` accepts the issue,
# a later ``unack`` re-activates it. Acks are keyed to the period and a STABLE
# issue identity (not the data fingerprint) so a recurring known anomaly stays
# acknowledged as its exact magnitude drifts from one data pull to the next.
_ACK_TABLE = "confirmation_issue_acks"
_ack_initialised = False


def _init_acks() -> None:
    """Create the issue-acknowledgement table if it does not exist (idempotent)."""
    global _ack_initialised
    if _ack_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_ACK_TABLE} (
        id           BIGSERIAL   PRIMARY KEY,
        action       TEXT        NOT NULL,
        period_key   TEXT        NOT NULL,
        issue_key    TEXT        NOT NULL,
        tier         INTEGER     NOT NULL DEFAULT 0,
        severity     TEXT        NOT NULL DEFAULT '',
        plant        TEXT        NOT NULL DEFAULT '',
        machine      TEXT        NOT NULL DEFAULT '',
        message      TEXT        NOT NULL DEFAULT '',
        approver     TEXT        NOT NULL,
        role         TEXT        NOT NULL DEFAULT '',
        note         TEXT        NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_ACK_TABLE}_lookup
        ON {_ACK_TABLE} (period_key, issue_key, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _ack_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def ack_record(
    action: str,
    *,
    period_key: str,
    issue_key: str,
    tier: int = 0,
    severity: str = "",
    plant: str = "",
    machine: str = "",
    message: str = "",
    approver: str,
    role: str = "",
    note: str = "",
) -> None:
    """Append an acknowledge / un-acknowledge event for one issue."""
    if action not in ("ack", "unack"):
        raise StoreError(f"Unknown action: {action!r}")
    if not (approver or "").strip():
        raise StoreError("An approver name is required.")
    if not (issue_key or "").strip():
        raise StoreError("An issue reference is required.")
    _init_acks()
    sql = f"""
        INSERT INTO {_ACK_TABLE}
            (action, period_key, issue_key, tier, severity, plant, machine,
             message, approver, role, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        action, period_key, issue_key, int(tier or 0), severity, plant, machine,
        message, approver.strip(), (role or "").strip(), (note or "").strip(),
    )
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def acks_for(period_key: str) -> Dict[str, Dict]:
    """Effective acknowledgements for a period: ``{issue_key: ack_dict}``.

    The latest row per issue wins; only issues whose latest action is ``ack``
    are returned. Degrades to an empty dict when no store is configured.
    """
    if not AVAILABLE:
        return {}
    try:
        _init_acks()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT DISTINCT ON (issue_key) *
                    FROM {_ACK_TABLE}
                    WHERE period_key=%s
                    ORDER BY issue_key, created_at DESC, id DESC""",
                (period_key,),
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict] = {}
    for r in rows:
        if r.get("action") == "ack":
            out[r["issue_key"]] = _shape(r)
    return out


# ---------------------------------------------------------------------------
# Stale-rollup alert acknowledgements (dismiss a known stale compound rollup)
# ---------------------------------------------------------------------------
# The freshness panel shows a standing "stale rollup" alert when a compound's
# published "Compound 6-10" monthly summary drifts from its daily Mixer-Logbook
# detail. A manager who already knows about a specific compound·month drift
# (e.g. waiting on a plant team to fix the sheet) can acknowledge it so the
# panel stays clean. Append-only, latest-row-per-alert wins: an ``ack`` mutes
# the alert, a later ``unack`` reactivates it. Each ack is keyed to a STABLE
# alert identity (compound·month) AND the data ``fingerprint`` of that alert's
# figures — so the ack holds while the data state is unchanged but the alert
# RE-SURFACES automatically if the rollup drifts again to a new state after a
# fix (the fingerprint no longer matches). Degrades to a safe no-op (no acks)
# when no DATABASE_URL is configured.
_STALE_ACK_TABLE = "stale_rollup_acks"
_stale_ack_initialised = False


def _init_stale_acks() -> None:
    """Create the stale-rollup acknowledgement table if absent (idempotent)."""
    global _stale_ack_initialised
    if _stale_ack_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_STALE_ACK_TABLE} (
        id           BIGSERIAL   PRIMARY KEY,
        action       TEXT        NOT NULL,
        alert_key    TEXT        NOT NULL,
        fingerprint  TEXT        NOT NULL DEFAULT '',
        compound     TEXT        NOT NULL DEFAULT '',
        month        TEXT        NOT NULL DEFAULT '',
        message      TEXT        NOT NULL DEFAULT '',
        approver     TEXT        NOT NULL,
        role         TEXT        NOT NULL DEFAULT '',
        note         TEXT        NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_STALE_ACK_TABLE}_lookup
        ON {_STALE_ACK_TABLE} (alert_key, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _stale_ack_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def stale_rollup_ack_record(
    action: str,
    *,
    alert_key: str,
    fingerprint: str = "",
    compound: str = "",
    month: str = "",
    message: str = "",
    approver: str,
    role: str = "",
    note: str = "",
) -> None:
    """Append an acknowledge / un-acknowledge event for one stale-rollup alert."""
    if action not in ("ack", "unack"):
        raise StoreError(f"Unknown action: {action!r}")
    if not (approver or "").strip():
        raise StoreError("An approver name is required.")
    if not (alert_key or "").strip():
        raise StoreError("An alert reference is required.")
    _init_stale_acks()
    sql = f"""
        INSERT INTO {_STALE_ACK_TABLE}
            (action, alert_key, fingerprint, compound, month, message,
             approver, role, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        action, alert_key, fingerprint, compound, month, message,
        approver.strip(), (role or "").strip(), (note or "").strip(),
    )
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def stale_rollup_acks() -> Dict[str, Dict]:
    """Effective stale-rollup acks: ``{alert_key: ack_dict}`` (latest wins).

    Only alerts whose latest action is ``ack`` are returned. The caller compares
    the stored ``fingerprint`` against the live alert's fingerprint to decide
    whether the ack still applies (an ack made against a now-superseded data
    state lets the alert re-surface). Degrades to ``{}`` without a store.
    """
    if not AVAILABLE:
        return {}
    try:
        _init_stale_acks()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT DISTINCT ON (alert_key) *
                    FROM {_STALE_ACK_TABLE}
                    ORDER BY alert_key, created_at DESC, id DESC"""
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict] = {}
    for r in rows:
        if r.get("action") == "ack":
            out[r["alert_key"]] = _shape(r)
    return out


# ---------------------------------------------------------------------------
# Ideal run-hours overrides (manager-supplied monthly ideal hours per machine)
# ---------------------------------------------------------------------------
# Append-only, latest-wins, keyed (plant, machine, month YYYY-MM). A ``set`` row
# carries the override hours; a ``clear`` row reverts that machine-month to the
# live sheet value. These NEVER touch the Google Sheets — they live only here and
# drive utilisation via ``ideal_hours.resolve``. Degrades to a safe no-op (no
# overrides) when no DATABASE_URL is configured.
_IDEAL_TABLE = "ideal_hours_overrides"
_ideal_initialised = False


def _init_ideal() -> None:
    """Create the ideal-hours override table if absent (idempotent, lazy)."""
    global _ideal_initialised
    if _ideal_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_IDEAL_TABLE} (
        id           BIGSERIAL   PRIMARY KEY,
        action       TEXT        NOT NULL,
        plant        TEXT        NOT NULL,
        machine      TEXT        NOT NULL,
        month        TEXT        NOT NULL,
        ideal_hours  DOUBLE PRECISION NOT NULL DEFAULT 0,
        set_by       TEXT        NOT NULL,
        note         TEXT        NOT NULL DEFAULT '',
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_IDEAL_TABLE}_lookup
        ON {_IDEAL_TABLE} (month, plant, machine, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _ideal_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def ideal_override_record(
    action: str,
    *,
    plant: str,
    machine: str,
    month: str,
    hours: Optional[float] = None,
    set_by: str,
    note: str = "",
) -> None:
    """Append a ``set`` (with hours) or ``clear`` event for one machine-month."""
    if action not in ("set", "clear"):
        raise StoreError(f"Unknown action: {action!r}")
    if not (set_by or "").strip():
        raise StoreError("A name is required.")
    # ``machine`` may be empty: a plant-level override for a plant with no machine
    # identity (e.g. TANK, logged per item). plant + month are still required.
    if not (plant or "").strip() or not (month or "").strip():
        raise StoreError("plant and month are required.")
    h = 0.0
    if action == "set":
        if hours is None:
            raise StoreError("An ideal-hours value is required.")
        h = float(hours)
        if not math.isfinite(h):
            raise StoreError("Ideal hours must be a finite number.")
        if h < 0:
            raise StoreError("Ideal hours cannot be negative.")
    _init_ideal()
    sql = f"""
        INSERT INTO {_IDEAL_TABLE}
            (action, plant, machine, month, ideal_hours, set_by, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        action, plant, machine, month, h, set_by.strip(), (note or "").strip(),
    )
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def ideal_overrides_for(month: str) -> Dict[Tuple[str, str], Dict]:
    """Effective overrides for a month: ``{(plant, machine): override_dict}``.

    The latest row per (plant, machine) wins; only ones whose latest action is
    ``set`` are returned. Degrades to ``{}`` when no store is configured.
    """
    if not AVAILABLE:
        return {}
    try:
        _init_ideal()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT DISTINCT ON (plant, machine) *
                    FROM {_IDEAL_TABLE}
                    WHERE month=%s
                    ORDER BY plant, machine, created_at DESC, id DESC""",
                (month,),
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[Tuple[str, str], Dict] = {}
    for r in rows:
        if r.get("action") == "set":
            out[(r["plant"], r["machine"])] = _shape(r)
    return out


# ---------------------------------------------------------------------------
# Segment manual monthly inputs (Group B — power / solar / contractor)
# ---------------------------------------------------------------------------
# These fields do not exist in any production workbook (grid-power amount, solar
# generation, tariff rates, contractor head-count/wages) and must be captured by a
# person each month. Append-only: the latest row per (month, unit) wins. The value
# columns are stored as a JSON blob so the field set can evolve without a schema
# migration. Degrades to a safe no-op (no inputs) when no store is configured.
_SEG_INPUT_TABLE = "segment_manual_inputs"
_seg_input_initialised = False


def _init_seg_inputs() -> None:
    """Create the segment manual-input table if absent (idempotent, lazy)."""
    global _seg_input_initialised
    if _seg_input_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_SEG_INPUT_TABLE} (
        id          BIGSERIAL   PRIMARY KEY,
        month       TEXT        NOT NULL,
        unit        TEXT        NOT NULL,
        values_json TEXT        NOT NULL DEFAULT '{{}}',
        set_by      TEXT        NOT NULL,
        note        TEXT        NOT NULL DEFAULT '',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_SEG_INPUT_TABLE}_lookup
        ON {_SEG_INPUT_TABLE} (month, unit, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _seg_input_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def seg_input_record(
    *,
    month: str,
    unit: str,
    values: Dict[str, float],
    set_by: str,
    note: str = "",
) -> None:
    """Append a set of manual values for one (month, unit).

    ``values`` is a mapping of field key → number; blank fields should simply be
    omitted by the caller (a later save with a field re-included updates it). A
    name is required and recorded as an attestation — there is no login.
    """
    if not (set_by or "").strip():
        raise StoreError("A name is required.")
    if not (month or "").strip() or not (unit or "").strip():
        raise StoreError("month and unit are required.")
    clean: Dict[str, float] = {}
    for k, v in (values or {}).items():
        if v is None:
            continue
        f = float(v)
        if not math.isfinite(f):
            raise StoreError(f"{k} must be a finite number.")
        if f < 0:
            raise StoreError(f"{k} cannot be negative.")
        clean[k] = f
    _init_seg_inputs()
    sql = f"""
        INSERT INTO {_SEG_INPUT_TABLE} (month, unit, values_json, set_by, note)
        VALUES (%s,%s,%s,%s,%s)
    """
    params = (month, unit, json.dumps(clean), set_by.strip(), (note or "").strip())
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def seg_inputs_for(months: List[str]) -> Dict[Tuple[str, str], Dict]:
    """Effective manual inputs for the given months.

    Returns ``{(month, unit): {<field>: value, ..., "set_by", "when_disp", "note"}}``
    — the latest row per (month, unit) wins. Degrades to ``{}`` when no store is
    configured or no months are requested.
    """
    if not AVAILABLE or not months:
        return {}
    try:
        _init_seg_inputs()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT DISTINCT ON (month, unit) *
                    FROM {_SEG_INPUT_TABLE}
                    WHERE month = ANY(%s)
                    ORDER BY month, unit, created_at DESC, id DESC""",
                (list(months),),
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[Tuple[str, str], Dict] = {}
    for r in rows:
        shaped = _shape(r)
        try:
            vals = json.loads(shaped.get("values_json") or "{}")
        except (ValueError, TypeError):
            vals = {}
        entry: Dict = dict(vals)
        entry["set_by"] = shaped.get("set_by", "")
        entry["when_disp"] = shaped.get("when_disp", "")
        entry["note"] = shaped.get("note", "")
        out[(r["month"], r["unit"])] = entry
    return out


# ---------------------------------------------------------------------------
# Verification log (append-only audit trail for the read-only Verification view)
# ---------------------------------------------------------------------------
# Recorded only on an explicit "Run & log this verification" action. It captures
# WHO ran the check, WHEN, for which month, and how many of the reconciliation
# checks passed/failed over how many rows. It never touches a figure — it is an
# attestation that the numbers were reviewed against the source at a point in
# time. Append-only; the latest row for a month is the most recent run.
_VERIFY_TABLE = "verification_log"
_verify_initialised = False


def _init_verify() -> None:
    """Create the verification-log table if it does not exist (idempotent)."""
    global _verify_initialised
    if _verify_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_VERIFY_TABLE} (
        id             BIGSERIAL   PRIMARY KEY,
        period         TEXT        NOT NULL,
        run_by         TEXT        NOT NULL,
        checks_passed  INTEGER     NOT NULL DEFAULT 0,
        checks_failed  INTEGER     NOT NULL DEFAULT 0,
        n_rows         INTEGER     NOT NULL DEFAULT 0,
        note           TEXT        NOT NULL DEFAULT '',
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_VERIFY_TABLE}_lookup
        ON {_VERIFY_TABLE} (period, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _verify_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def verify_record(
    *,
    period: str,
    run_by: str,
    checks_passed: int = 0,
    checks_failed: int = 0,
    n_rows: int = 0,
    note: str = "",
) -> None:
    """Append one verification-run event. Raises StoreError if not persisted."""
    if not (run_by or "").strip():
        raise StoreError("A name is required to log a verification run.")
    if not (period or "").strip():
        raise StoreError("A period is required.")
    _init_verify()
    sql = f"""
        INSERT INTO {_VERIFY_TABLE}
            (period, run_by, checks_passed, checks_failed, n_rows, note)
        VALUES (%s,%s,%s,%s,%s,%s)
    """
    params = (
        period.strip(), run_by.strip(), int(checks_passed),
        int(checks_failed), int(n_rows), (note or "").strip(),
    )
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
    except StoreError:
        raise
    except Exception as e:
        raise StoreError(str(e))


def verify_last(period: Optional[str] = None) -> Optional[Dict]:
    """The most recent verification run (optionally for one period), or None."""
    if not AVAILABLE:
        return None
    try:
        _init_verify()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            if period:
                cur.execute(
                    f"""SELECT * FROM {_VERIFY_TABLE} WHERE period=%s
                        ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (period,),
                )
            else:
                cur.execute(
                    f"""SELECT * FROM {_VERIFY_TABLE}
                        ORDER BY created_at DESC, id DESC LIMIT 1""",
                )
            row = cur.fetchone()
    except Exception:
        return None
    return _shape(row) if row else None


def verify_history(period: Optional[str] = None, limit: int = 20) -> List[Dict]:
    """Recent verification runs (newest first) for the audit trail."""
    if not AVAILABLE:
        return []
    try:
        _init_verify()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            if period:
                cur.execute(
                    f"""SELECT * FROM {_VERIFY_TABLE} WHERE period=%s
                        ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (period, limit),
                )
            else:
                cur.execute(
                    f"""SELECT * FROM {_VERIFY_TABLE}
                        ORDER BY created_at DESC, id DESC LIMIT %s""",
                    (limit,),
                )
            rows = cur.fetchall()
    except Exception:
        return []
    return [_shape(r) for r in rows]


# ---------------------------------------------------------------------------
# Source content fingerprints (dashboard-detected "last changed" tracking)
# ---------------------------------------------------------------------------
# The connected Google account only has the ``drive.file`` scope, so Google's
# true "last edited" time for these spreadsheets is NOT reachable (the Drive
# metadata API returns 404 for files the app did not create). Instead the
# dashboard records a content fingerprint (a hash of the parsed values) per
# workbook each time it reads one. The store is append-ONLY and a new row is
# written ONLY when a workbook's fingerprint differs from the last one seen, so
# the most recent row's ``observed_at`` is exactly "when the dashboard first
# saw this version of the data" — i.e. when the sheet last had an input/update.
# Degrades to a no-op (no tracking) when DATABASE_URL is absent.
_FP_TABLE = "source_fingerprints"
_fp_initialised = False


def _init_fingerprints() -> None:
    """Create the source-fingerprint table if it does not exist (idempotent)."""
    global _fp_initialised
    if _fp_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_FP_TABLE} (
        id          BIGSERIAL   PRIMARY KEY,
        file_id     TEXT        NOT NULL,
        fingerprint TEXT        NOT NULL,
        label       TEXT        NOT NULL DEFAULT '',
        plant       TEXT        NOT NULL DEFAULT '',
        grain       TEXT        NOT NULL DEFAULT '',
        row_count   INTEGER     NOT NULL DEFAULT 0,
        observed_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS {_FP_TABLE}_lookup
        ON {_FP_TABLE} (file_id, observed_at DESC);
    CREATE UNIQUE INDEX IF NOT EXISTS {_FP_TABLE}_version_uniq
        ON {_FP_TABLE} (file_id, fingerprint);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _fp_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def fingerprint_state() -> Dict[str, Dict]:
    """Per-file latest fingerprint plus how many distinct snapshots exist.

    Returns ``{file_id: {fingerprint, observed_at, snapshots, label, ...}}``.
    ``snapshots`` is the count of recorded (change) rows for the file, so
    ``snapshots > 1`` means the workbook has actually changed at least once
    since tracking began (not just a first baseline). Degrades to ``{}``.
    """
    if not AVAILABLE:
        return {}
    try:
        _init_fingerprints()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT DISTINCT ON (file_id) *,
                        (SELECT COUNT(*) FROM {_FP_TABLE} c
                         WHERE c.file_id = f.file_id) AS snapshots
                    FROM {_FP_TABLE} f
                    ORDER BY file_id, observed_at DESC, id DESC"""
            )
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict] = {}
    for r in rows:
        d = _shape(r)
        ts = r.get("observed_at")
        if isinstance(ts, datetime.datetime):
            d["observed_at_disp"] = ts.strftime("%d-%m-%Y %H:%M")
        d["observed_at"] = ts
        d["snapshots"] = int(r.get("snapshots") or 1)
        out[r["file_id"]] = d
    return out


# ---------------------------------------------------------------------------
# Manifest run log — one row per advisory-review run
# ---------------------------------------------------------------------------
_ML_TABLE = "manifest_log"
_ml_initialised = False


def _init_manifest_log() -> None:
    global _ml_initialised
    if _ml_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_ML_TABLE} (
        id            BIGSERIAL    PRIMARY KEY,
        as_of         TEXT         NOT NULL,
        fy            TEXT         NOT NULL DEFAULT '',
        fingerprint   TEXT         NOT NULL DEFAULT '',
        expected_count INTEGER     NOT NULL DEFAULT 0,
        fetched_count  INTEGER     NOT NULL DEFAULT 0,
        empty_count    INTEGER     NOT NULL DEFAULT 0,
        not_found_count INTEGER    NOT NULL DEFAULT 0,
        schema_flag_count INTEGER  NOT NULL DEFAULT 0,
        parse_note_count  INTEGER  NOT NULL DEFAULT 0,
        advisory_ok   BOOLEAN      NOT NULL DEFAULT FALSE,
        coverage      JSONB,
        schema_flags  JSONB,
        advisory      JSONB,
        created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
    );
    ALTER TABLE {_ML_TABLE} ADD COLUMN IF NOT EXISTS
        parse_note_count INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS {_ML_TABLE}_lookup
        ON {_ML_TABLE} (as_of, created_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _ml_initialised = True
    except Exception as e:
        raise StoreError(str(e))


def save_manifest_log(
    *,
    as_of: str,
    fy: str = "",
    fingerprint: str = "",
    coverage: dict,
    schema_flags: list,
    parse_note_count: int = 0,
    advisory: Optional[dict] = None,
) -> Optional[int]:
    """Persist one manifest run. Returns the new row id, or None on failure."""
    if not AVAILABLE:
        return None
    try:
        _init_manifest_log()
        cov = coverage or {}
        sql = f"""
            INSERT INTO {_ML_TABLE}
                (as_of, fy, fingerprint, expected_count, fetched_count,
                 empty_count, not_found_count, schema_flag_count,
                 parse_note_count, advisory_ok, coverage, schema_flags, advisory)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """
        params = (
            as_of, fy, fingerprint,
            int(cov.get("expected_count", 0)),
            int(cov.get("fetched_with_data", 0)),
            len(cov.get("present_but_empty", [])),
            len(cov.get("not_found_at_all", [])),
            len(schema_flags or []),
            int(parse_note_count),
            advisory is not None,
            json.dumps(cov) if cov else None,
            json.dumps(schema_flags) if schema_flags else None,
            json.dumps(advisory) if advisory else None,
        )
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def recent_manifest_logs(limit: int = 10) -> List[Dict]:
    """Recent manifest run log entries (newest first)."""
    if not AVAILABLE:
        return []
    try:
        _init_manifest_log()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT id, as_of, fy, fingerprint, expected_count,
                           fetched_count, empty_count, not_found_count,
                           schema_flag_count, parse_note_count,
                           advisory_ok, created_at
                    FROM {_ML_TABLE}
                    ORDER BY created_at DESC LIMIT %s""",
                (limit,),
            )
            rows = cur.fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        d = dict(r)
        ts = d.get("created_at")
        if isinstance(ts, datetime.datetime):
            d["created_at_disp"] = ts.strftime("%d-%m-%Y %H:%M")
        out.append(d)
    # Compute parse_note_delta for each row: current count minus the next-older run.
    # Rows are newest-first, so row[i] is newer than row[i+1].
    for i, d in enumerate(out):
        if i + 1 < len(out):
            prev_count = out[i + 1].get("parse_note_count") or 0
            curr_count = d.get("parse_note_count") or 0
            d["parse_note_delta"] = curr_count - prev_count
        else:
            d["parse_note_delta"] = None  # no older run to compare
    return out


def fingerprint_record(
    *,
    file_id: str,
    fingerprint: str,
    label: str = "",
    plant: str = "",
    grain: str = "",
    row_count: int = 0,
) -> Optional[Dict]:
    """Append one fingerprint snapshot for a workbook (call only on change).

    One row per content version: ``(file_id, fingerprint)`` is unique, so the
    snapshot count for a file equals the number of distinct versions ever seen
    (``snapshots > 1`` ⇒ the workbook genuinely changed at least once). The
    caller only calls this on a real transition (first sight, or current ≠ the
    last-seen version), never on an unchanged re-read.

    On conflict the row is touched (``observed_at = now()``) rather than left
    alone, which makes two cases correct at once:
      * concurrent duplicate inserts of the same transition collapse to one row
        (no race can inflate the snapshot count into a false "updated");
      * a *revert* to a previously-seen version (A→B→A) re-establishes that
        version as the latest with a fresh timestamp, so the next read converges
        (current == latest) instead of re-detecting a change every load.

    Returns the stored row (shaped, with ``observed_at``) on success, or ``None``
    when no store is configured / the write fails (best-effort: change tracking
    never breaks a page render).
    """
    if not AVAILABLE or not (file_id or "").strip() or not fingerprint:
        return None
    try:
        _init_fingerprints()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""INSERT INTO {_FP_TABLE}
                        (file_id, fingerprint, label, plant, grain, row_count)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (file_id, fingerprint) DO UPDATE
                        SET observed_at = now(),
                            row_count   = EXCLUDED.row_count,
                            label       = EXCLUDED.label,
                            plant       = EXCLUDED.plant,
                            grain       = EXCLUDED.grain
                    RETURNING *""",
                (file_id, fingerprint, label, plant, grain, int(row_count or 0)),
            )
            row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    d = _shape(row)
    ts = row.get("observed_at")
    if isinstance(ts, datetime.datetime):
        d["observed_at_disp"] = ts.strftime("%d-%m-%Y %H:%M")
    d["observed_at"] = ts
    d["snapshots"] = 1
    return d


# ---------------------------------------------------------------------------
# Index-tab baseline — month-over-month change tracking of tab metadata
# ---------------------------------------------------------------------------
# Each monthly workbook is a NEW file, so this baseline is keyed by PLANT (the
# workbook family), not file_id — that is what lets the dashboard compare this
# month's Index against the previously-seen description/frequency and flag a tab
# whose meaning changed, rather than silently assuming last month's mapping.
# First sight per (plant, report_key) is recorded as the baseline; an existing
# baseline's description/frequency are left INTACT so a later month that differs
# keeps flagging until intentionally re-baselined. No-op without DATABASE_URL.
_IDX_TABLE = "index_baseline"
_idx_initialised = False


def _init_index_baseline() -> None:
    """Create the Index-baseline table if it does not exist (idempotent)."""
    global _idx_initialised
    if _idx_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_IDX_TABLE} (
        id          BIGSERIAL   PRIMARY KEY,
        plant       TEXT        NOT NULL,
        report_key  TEXT        NOT NULL,
        report      TEXT        NOT NULL DEFAULT '',
        description TEXT        NOT NULL DEFAULT '',
        frequency   TEXT        NOT NULL DEFAULT '',
        file_id     TEXT        NOT NULL DEFAULT '',
        first_seen  TIMESTAMPTZ NOT NULL DEFAULT now(),
        observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (plant, report_key)
    );
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _idx_initialised = True
    except Exception as e:  # pragma: no cover - infra failure
        raise StoreError(str(e))


def index_baseline_state() -> Dict[str, Dict[str, Dict]]:
    """First-seen baseline per (plant, report_key): {plant: {report_key: {...}}}.

    Each leaf carries ``report``/``description``/``frequency``/``first_seen``.
    Degrades to ``{}`` when no store is configured or the read fails.
    """
    if not AVAILABLE:
        return {}
    try:
        _init_index_baseline()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(f"SELECT * FROM {_IDX_TABLE}")
            rows = cur.fetchall()
    except Exception:
        return {}
    out: Dict[str, Dict[str, Dict]] = {}
    for r in rows:
        d = {
            "report": r.get("report", ""),
            "description": r.get("description", ""),
            "frequency": r.get("frequency", ""),
            "file_id": r.get("file_id", ""),
        }
        ts = r.get("first_seen")
        if isinstance(ts, datetime.datetime):
            d["first_seen_disp"] = ts.strftime("%d-%m-%Y")
        out.setdefault(r["plant"], {})[r["report_key"]] = d
    return out


def index_baseline_record(plant: str, reports: List[Dict], file_id: str = "") -> None:
    """Record first-seen baselines for a plant's Index reports (best-effort).

    First sight per (plant, report_key) stores the baseline; an existing row's
    description/frequency are NOT overwritten (only observed_at/file_id refresh),
    so a changed description/frequency keeps surfacing on the Data Health page.
    No-op without a store; never raises (change tracking must not break a render).
    """
    if not AVAILABLE or not plant or not reports:
        return
    try:
        _init_index_baseline()
        with _conn() as conn, conn.cursor() as cur:
            for rep in reports:
                rk = (rep.get("report_key") or "").strip()
                if not rk:
                    continue
                cur.execute(
                    f"""INSERT INTO {_IDX_TABLE}
                            (plant, report_key, report, description, frequency, file_id)
                        VALUES (%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (plant, report_key) DO UPDATE
                            SET observed_at = now(),
                                file_id     = EXCLUDED.file_id""",
                    (plant, rk, rep.get("report", ""), rep.get("description", ""),
                     rep.get("frequency", ""), file_id),
                )
    except Exception:
        return


# ---------------------------------------------------------------------------
# Postgres-backed sheet cache — shared across gunicorn workers
# ---------------------------------------------------------------------------
# Each gunicorn worker has its own in-process _data_cache/_daily_cache.
# A cold worker would otherwise re-fetch from Google Sheets on every request.
# This table acts as a shared L2 cache: any worker that fetches from Sheets
# writes the result here; cold workers read from here instead of Sheets.
# Serialisation uses pickle (internal only, never user-supplied input).
# The whole section degrades silently when DATABASE_URL is absent.
# ---------------------------------------------------------------------------

_SC_TABLE = "sheet_cache"
_sc_initialised = False

# High-water record counts for daily source pairs.  A daily workbook may grow
# while the month is in progress, but a later successful read must never shrink
# below a previously observed population without being treated as incomplete.
_DRC_TABLE = "daily_read_counts"
_drc_initialised = False
_DRA_TABLE = "daily_rebaseline_audit"
_dra_initialised = False
_DRC_CONFIRM_TABLE = "daily_rebaseline_confirmations"
_drc_confirm_initialised = False


def _init_sheet_cache() -> None:
    global _sc_initialised
    if _sc_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_SC_TABLE} (
        cache_key  TEXT        PRIMARY KEY,
        payload    BYTEA       NOT NULL,
        cached_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _sc_initialised = True
    except Exception:
        pass  # degrade silently — per-request path still works


def pg_cache_read(key: str, max_age_s: float = 900.0):
    """Return unpickled payload from the shared Postgres sheet cache, or None.

    Returns None when:
    - Postgres is unavailable (DATABASE_URL missing)
    - No row exists for ``key``
    - The row is older than ``max_age_s`` seconds
    - Unpickling fails (e.g. Record class changed between deploys)
    Any exception is swallowed so a cache miss never breaks a page render.
    """
    if not AVAILABLE:
        return None
    try:
        _init_sheet_cache()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT payload FROM {_SC_TABLE}
                    WHERE cache_key = %s
                      AND cached_at > now() - make_interval(secs => %s::double precision)""",
                (key, float(max_age_s)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return pickle.loads(bytes(row[0]))
    except Exception:
        return None


def pg_cache_write(key: str, data) -> None:
    """Pickle and upsert ``data`` into the shared Postgres sheet cache.

    Best-effort: any failure is silently swallowed so a cache write never
    breaks a page render. The caller should not depend on the write succeeding.
    """
    if not AVAILABLE:
        return
    try:
        _init_sheet_cache()
        payload_bytes = pickle.dumps(data, protocol=4)
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_SC_TABLE} (cache_key, payload, cached_at)
                    VALUES (%s, %s, now())
                    ON CONFLICT (cache_key) DO UPDATE
                        SET payload   = EXCLUDED.payload,
                            cached_at = now()""",
                (key, psycopg2.Binary(payload_bytes)),
            )
    except Exception:
        pass  # degrade silently


def pg_cache_clear(key: str = "") -> None:
    """Delete one key (or every key when ``key`` is empty) from the sheet cache.

    Called on manual Refresh so the next request fetches live data from Sheets
    rather than serving a stale Postgres entry.  Best-effort.
    """
    if not AVAILABLE:
        return
    try:
        _init_sheet_cache()
        with _conn() as conn, conn.cursor() as cur:
            if key:
                cur.execute(f"DELETE FROM {_SC_TABLE} WHERE cache_key = %s", (key,))
            else:
                cur.execute(f"DELETE FROM {_SC_TABLE}")
    except Exception:
        pass  # degrade silently


def _init_daily_read_counts() -> None:
    global _drc_initialised
    if _drc_initialised or not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_DRC_TABLE} (
                    plant TEXT NOT NULL,
                    ym TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (plant, ym)
                )
            """)
        _drc_initialised = True
    except Exception:
        pass


def daily_read_count(plant: str, ym: str):
    """Return the largest complete population previously observed for a source pair."""
    if not AVAILABLE:
        return None
    try:
        _init_daily_read_counts()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT record_count FROM {_DRC_TABLE} WHERE plant = %s AND ym = %s",
                (plant, ym),
            )
            row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def remember_daily_read_count(plant: str, ym: str, record_count: int) -> None:
    """Persist a complete daily-pair high-water mark, never a reduced population."""
    if not AVAILABLE or record_count <= 0:
        return
    try:
        _init_daily_read_counts()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_DRC_TABLE} (plant, ym, record_count, observed_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (plant, ym) DO UPDATE
                    SET record_count = GREATEST({_DRC_TABLE}.record_count,
                                                EXCLUDED.record_count),
                        observed_at = now()
                """,
                (plant, ym, int(record_count)),
            )
    except Exception:
        pass


def rebaseline_daily_read_count(plant: str, ym: str, record_count: int) -> None:
    """Replace one daily logical-emitter baseline after an approved correction.

    Unlike ``remember_daily_read_count``, this operation may lower a count. It is
    intentionally narrow and raises on failure so callers cannot report a
    successful re-baseline when persistence did not occur.
    """
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    if not plant or not ym or record_count <= 0:
        raise StoreError("Plant, month and a positive record count are required.")
    try:
        _init_daily_read_counts()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {_DRC_TABLE} (plant, ym, record_count, observed_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (plant, ym) DO UPDATE
                    SET record_count = EXCLUDED.record_count,
                        observed_at = now()
                """,
                (plant, ym, int(record_count)),
            )
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def _init_daily_rebaseline_audit() -> None:
    """Create the append-only operator audit table when Postgres is available."""
    global _dra_initialised
    if _dra_initialised or not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_DRA_TABLE} (
                    id BIGSERIAL PRIMARY KEY,
                    invoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    user_id BIGINT,
                    user_email TEXT NOT NULL,
                    emitter TEXT NOT NULL,
                    ym TEXT NOT NULL,
                    old_count INTEGER,
                    live_count INTEGER NOT NULL,
                    new_count INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'attempted'
                        CHECK (status IN ('attempted', 'succeeded', 'refused', 'failed')),
                    error TEXT NOT NULL DEFAULT ''
                )
            """)
        _dra_initialised = True
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def start_daily_rebaseline_audit(
    *,
    user_id,
    user_email: str,
    emitter: str,
    ym: str,
    old_count,
    live_count: int,
    new_count: int,
) -> int:
    """Durably record an invocation before the guarded write is attempted."""
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    _init_daily_rebaseline_audit()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_DRA_TABLE}
                       (user_id, user_email, emitter, ym, old_count, live_count,
                        new_count, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'attempted')
                    RETURNING id""",
                (
                    user_id, user_email.strip(), emitter, ym, old_count,
                    int(live_count), int(new_count),
                ),
            )
            row = cur.fetchone()
        return int(row[0])
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def finish_daily_rebaseline_audit(
    audit_id: int,
    *,
    status: str,
    error: str = "",
) -> None:
    """Attach the guarded invocation outcome; the attempt remains if this fails."""
    if status not in ("succeeded", "refused", "failed"):
        raise StoreError("Invalid re-baseline audit status.")
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_DRA_TABLE}
                       SET status = %s, error = %s
                     WHERE id = %s""",
                (status, (error or "")[:1000], int(audit_id)),
            )
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def daily_rebaseline_audit_history(limit: int = 100) -> list[dict]:
    """Return recent operator invocations without exposing unrelated activity."""
    if not AVAILABLE:
        return []
    _init_daily_rebaseline_audit()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT id, invoked_at, user_email, emitter, ym, old_count,
                           live_count, new_count, status, error
                      FROM {_DRA_TABLE}
                     ORDER BY invoked_at DESC, id DESC
                     LIMIT %s""",
                (max(1, min(int(limit), 500)),),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def _init_daily_rebaseline_confirmations() -> None:
    """Create the short-lived server-side confirmation store."""
    global _drc_confirm_initialised
    if _drc_confirm_initialised or not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_DRC_CONFIRM_TABLE} (
                    nonce TEXT PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    emitter TEXT NOT NULL,
                    ym TEXT NOT NULL,
                    stored_count INTEGER,
                    live_count INTEGER NOT NULL,
                    new_count INTEGER NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    expires_at TIMESTAMPTZ NOT NULL,
                    consumed_at TIMESTAMPTZ
                )
            """)
        _drc_confirm_initialised = True
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def create_daily_rebaseline_confirmation(
    *,
    user_id: int,
    emitter: str,
    ym: str,
    stored_count,
    live_count: int,
    new_count: int,
) -> str:
    """Persist an expiring, single-use confirmation and return its opaque nonce."""
    _init_daily_rebaseline_confirmations()
    nonce = secrets.token_urlsafe(32)
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_DRC_CONFIRM_TABLE}
                       (nonce, user_id, emitter, ym, stored_count, live_count,
                        new_count, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s,
                            now() + interval '10 minutes')""",
                (
                    nonce, int(user_id), emitter, ym, stored_count,
                    int(live_count), int(new_count),
                ),
            )
        return nonce
    except Exception as exc:
        raise StoreError(str(exc)) from exc


def consume_daily_rebaseline_confirmation(nonce: str, user_id: int):
    """Atomically consume one unexpired confirmation for its creating admin."""
    _init_daily_rebaseline_confirmations()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_DRC_CONFIRM_TABLE}
                       SET consumed_at = now()
                     WHERE nonce = %s
                       AND user_id = %s
                       AND consumed_at IS NULL
                       AND expires_at > now()
                 RETURNING emitter, ym, stored_count, live_count, new_count""",
                (nonce, int(user_id)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "emitter": row[0],
            "month": row[1],
            "stored_count": row[2],
            "live_count": row[3],
            "new_count": row[4],
        }
    except Exception as exc:
        raise StoreError(str(exc)) from exc


@contextmanager
def daily_rebaseline_lock(emitter: str, ym: str):
    """Serialize operator re-baselines for one emitter/month across workers."""
    if not AVAILABLE:
        raise StoreError("No durable store configured (DATABASE_URL missing).")
    conn = None
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"daily-rebaseline:{emitter}:{ym}",),
            )
    except Exception as exc:
        if conn is not None:
            conn.close()
        raise StoreError(str(exc)) from exc
    try:
        yield
    finally:
        # End the lock-owning transaction without changing application data.
        conn.rollback()
        conn.close()


# ---------------------------------------------------------------------------
# API key store — single-row table that holds the active API key for the
# /data-api/v1 endpoints.  Managed from the /settings/api-key UI page.
# Degrades gracefully (returns None) when Postgres is unavailable.
# ---------------------------------------------------------------------------

_AK_TABLE = "api_keys"


def _init_api_key_table() -> None:
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_AK_TABLE} (
                id         SERIAL PRIMARY KEY,
                key_value  TEXT NOT NULL,
                prefix     TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)


def get_api_key() -> Optional[str]:
    """Return the most-recently created API key, or None.

    Kept for backward compatibility with api.py's env-var fallback path.
    Prefer ``get_all_api_keys`` for multi-key auth validation.
    """
    if not AVAILABLE:
        return None
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT key_value FROM {_AK_TABLE} ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
        return row["key_value"] if row else None
    except Exception:
        return None


def get_all_api_keys() -> List[str]:
    """Return every active API key value (all rows).  Used by auth to allow any key."""
    if not AVAILABLE:
        return []
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT key_value FROM {_AK_TABLE} ORDER BY id")
            rows = cur.fetchall()
        return [r["key_value"] for r in rows]
    except Exception:
        return []


def get_api_key_meta() -> Optional[Dict]:
    """Return display metadata for the most-recently created key (legacy, UI uses list_api_keys_meta)."""
    if not AVAILABLE:
        return None
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT prefix, created_at FROM {_AK_TABLE} ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "prefix": row["prefix"],
            "created_at": row["created_at"].strftime("%d-%m-%Y %H:%M") if row["created_at"] else "",
        }
    except Exception:
        return None


def list_api_keys_meta() -> List[Dict]:
    """Return display metadata for ALL active keys, newest first."""
    if not AVAILABLE:
        return []
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"SELECT id, prefix, created_at FROM {_AK_TABLE} ORDER BY id DESC"
            )
            rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "prefix": r["prefix"],
                "created_at": r["created_at"].strftime("%d-%m-%Y %H:%M") if r["created_at"] else "",
            }
            for r in rows
        ]
    except Exception:
        return []


def add_api_key(key: str) -> None:
    """Add a new API key without removing existing ones (multi-key support)."""
    if not AVAILABLE:
        return
    try:
        _init_api_key_table()
        prefix = key[:12] if len(key) >= 12 else key
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_AK_TABLE} (key_value, prefix) VALUES (%s, %s)",
                (key, prefix),
            )
    except Exception:
        pass


def set_api_key(key: str) -> None:
    """Replace ALL stored keys with a single new key (legacy regenerate behaviour)."""
    if not AVAILABLE:
        return
    try:
        _init_api_key_table()
        prefix = key[:12] if len(key) >= 12 else key
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_AK_TABLE}")
            cur.execute(
                f"INSERT INTO {_AK_TABLE} (key_value, prefix) VALUES (%s, %s)",
                (key, prefix),
            )
    except Exception:
        pass


def delete_api_key_by_id(key_id: int) -> None:
    """Delete a single API key by its row ID."""
    if not AVAILABLE:
        return
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_AK_TABLE} WHERE id = %s", (key_id,))
    except Exception:
        pass


def delete_api_key() -> None:
    """Remove ALL stored API keys (disables the API until a new key is generated)."""
    if not AVAILABLE:
        return
    try:
        _init_api_key_table()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_AK_TABLE}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Application users — per-user dashboard authentication and admin management.
# Passwords are always supplied as hashes by auth.py; this module never accepts
# or persists a plaintext password.
# ---------------------------------------------------------------------------
_USER_TABLE = "app_users"
_USER_BOOTSTRAP_TABLE = "app_user_bootstrap"
_USER_ACTIVITY_SESSION_TABLE = "app_user_activity_sessions"
_USER_ACTIVITY_EVENT_TABLE = "app_user_activity_events"
_USER_ACTIVITY_DAILY_TABLE = "app_user_activity_daily"
_ACTIVITY_TZ = ZoneInfo("Asia/Kolkata")
_ACTIVITY_INTERVAL_CAP_SECONDS = 120
_PASSWORD_CHANGE_POLICY_SEED = "password-change-policy-v1"


def _init_user_tables() -> None:
    """Create the per-user authentication tables, once per process."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_USER_TABLE} (
                id            SERIAL PRIMARY KEY,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'normal'
                              CHECK (role IN ('admin', 'normal')),
                is_active     BOOLEAN NOT NULL DEFAULT TRUE,
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS {_USER_BOOTSTRAP_TABLE} (
                seed_key      TEXT PRIMARY KEY,
                completed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            ALTER TABLE {_USER_TABLE} ADD COLUMN IF NOT EXISTS
                must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
        """)
        # Upgrade the accounts provisioned before password changes were
        # mandatory. This only flips the required-change flag: it never
        # touches an existing password hash, role, or active/inactive state.
        # The marker and update share one transaction, so a failed startup
        # cannot permanently skip the migration.
        cur.execute(
            f"""INSERT INTO {_USER_BOOTSTRAP_TABLE} (seed_key)
                VALUES (%s) ON CONFLICT (seed_key) DO NOTHING
                RETURNING seed_key""",
            (_PASSWORD_CHANGE_POLICY_SEED,),
        )
        if cur.fetchone():
            cur.execute(
                f"""UPDATE {_USER_TABLE}
                    SET must_change_password=TRUE, updated_at=now()
                    WHERE is_active=TRUE AND must_change_password=FALSE"""
            )


def _user_shape(row) -> Dict:
    """Return a password-safe user record for templates and routes."""
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
        "must_change_password": bool(row.get("must_change_password", False)),
        "created_at": (
            row["created_at"].strftime("%d-%m-%Y %H:%M")
            if row.get("created_at") else ""
        ),
        "updated_at": (
            row["updated_at"].strftime("%d-%m-%Y %H:%M")
            if row.get("updated_at") else ""
        ),
    }


def seed_initial_users(users: List[Dict], seed_key: str = "initial-admins-v1") -> None:
    """Create the requested initial accounts exactly once.

    The durable seed marker means a later delete or role change is respected:
    recurring login attempts never recreate an account the administrator removed.
    ``users`` must contain password hashes, never plaintext passwords.
    """
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM {_USER_BOOTSTRAP_TABLE} WHERE seed_key=%s",
                (seed_key,),
            )
            if cur.fetchone():
                return
            for user in users:
                cur.execute(
                    f"""INSERT INTO {_USER_TABLE}
                        (email, password_hash, role, is_active, must_change_password)
                        VALUES (%s, %s, %s, TRUE, TRUE)
                        ON CONFLICT (email) DO UPDATE
                           SET password_hash = EXCLUDED.password_hash,
                               role = EXCLUDED.role,
                               is_active = TRUE,
                               must_change_password = TRUE,
                               updated_at = now()""",
                    (user["email"], user["password_hash"], user["role"]),
                )
            cur.execute(
                f"INSERT INTO {_USER_BOOTSTRAP_TABLE} (seed_key) VALUES (%s)",
                (seed_key,),
            )
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError(str(exc))


def get_user_auth(email: str) -> Optional[Dict]:
    """Return an active user's identity plus password hash for authentication."""
    if not AVAILABLE:
        return None
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT id, email, password_hash, role, is_active, must_change_password
                    FROM {_USER_TABLE} WHERE email=%s""",
                (email,),
            )
            row = cur.fetchone()
        if not row or not row["is_active"]:
            return None
        return dict(row)
    except Exception:
        return None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Return password-safe metadata for one user, or None."""
    if not AVAILABLE:
        return None
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT id, email, role, is_active, must_change_password, created_at, updated_at
                    FROM {_USER_TABLE} WHERE id=%s""",
                (int(user_id),),
            )
            row = cur.fetchone()
        return _user_shape(row) if row else None
    except Exception:
        return None


def list_users() -> List[Dict]:
    """List every dashboard account without exposing password hashes."""
    if not AVAILABLE:
        return []
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT id, email, role, is_active, must_change_password, created_at, updated_at
                    FROM {_USER_TABLE} ORDER BY email"""
            )
            rows = cur.fetchall()
        return [_user_shape(row) for row in rows]
    except Exception as exc:
        raise StoreError(str(exc))


def create_user(email: str, password_hash: str, role: str) -> Dict:
    """Create one active account and return password-safe metadata."""
    if role not in ("admin", "normal"):
        raise StoreError("Role must be admin or normal.")
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""INSERT INTO {_USER_TABLE}
                    (email, password_hash, role, must_change_password)
                    VALUES (%s, %s, %s, TRUE)
                    RETURNING id, email, role, is_active, must_change_password,
                              created_at, updated_at""",
                (email, password_hash, role),
            )
            row = cur.fetchone()
        return _user_shape(row)
    except Exception as exc:
        message = str(exc)
        if "unique" in message.lower():
            raise StoreError("An account with that email already exists.")
        raise StoreError(message)


def set_user_password(
    user_id: int,
    password_hash: str,
    *,
    must_change_password: bool = False,
) -> bool:
    """Replace one password hash and set its first-login requirement."""
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_USER_TABLE}
                    SET password_hash=%s, must_change_password=%s, updated_at=now()
                    WHERE id=%s""",
                (password_hash, bool(must_change_password), int(user_id)),
            )
            return cur.rowcount == 1
    except Exception as exc:
        raise StoreError(str(exc))


def set_user_role(user_id: int, role: str) -> bool:
    """Update a user's role. Returns False for a missing user."""
    if role not in ("admin", "normal"):
        raise StoreError("Role must be admin or normal.")
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_USER_TABLE}
                    SET role=%s, updated_at=now() WHERE id=%s""",
                (role, int(user_id)),
            )
            return cur.rowcount == 1
    except Exception as exc:
        raise StoreError(str(exc))


def delete_user(user_id: int) -> bool:
    """Permanently remove an account. Returns False when it does not exist."""
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_USER_TABLE} WHERE id=%s", (int(user_id),))
            return cur.rowcount == 1
    except Exception as exc:
        raise StoreError(str(exc))


def active_admin_count() -> int:
    """Number of active admin accounts, used to prevent a lockout."""
    if not AVAILABLE:
        return 0
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""SELECT COUNT(*) FROM {_USER_TABLE}
                    WHERE role='admin' AND is_active=TRUE"""
            )
            return int(cur.fetchone()[0])
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# User activity — privacy-safe, server-accounted dashboard usage.
#
# The browser sends a heartbeat every minute while an authenticated dashboard
# page is open. We credit only the previous heartbeat interval and cap it at
# two minutes: a suspended browser or a lost tab can never turn into hours of
# fabricated activity. Days are reported in the operating timezone (IST).
# ---------------------------------------------------------------------------

def _activity_now(value: Optional[datetime.datetime] = None) -> datetime.datetime:
    now = value or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc)


def _activity_day(value: datetime.datetime) -> datetime.date:
    return value.astimezone(_ACTIVITY_TZ).date()


def _init_user_activity_tables() -> None:
    """Create the append-only event log plus bounded-time session rollups."""
    if not AVAILABLE:
        return
    _init_user_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_USER_ACTIVITY_SESSION_TABLE} (
                session_id          TEXT PRIMARY KEY,
                user_id             INTEGER NOT NULL,
                user_email          TEXT NOT NULL,
                started_at          TIMESTAMPTZ NOT NULL,
                last_heartbeat_at   TIMESTAMPTZ NOT NULL,
                last_state          TEXT NOT NULL DEFAULT 'active'
                                    CHECK (last_state IN ('active', 'idle')),
                active_seconds      DOUBLE PRECISION NOT NULL DEFAULT 0,
                idle_seconds        DOUBLE PRECISION NOT NULL DEFAULT 0,
                ended_at            TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS {_USER_ACTIVITY_SESSION_TABLE}_user_started
                ON {_USER_ACTIVITY_SESSION_TABLE} (user_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS {_USER_ACTIVITY_EVENT_TABLE} (
                id           BIGSERIAL PRIMARY KEY,
                user_id      INTEGER NOT NULL,
                user_email   TEXT NOT NULL,
                session_id   TEXT NOT NULL DEFAULT '',
                event_type   TEXT NOT NULL
                             CHECK (event_type IN ('login', 'logout', 'page', 'action')),
                label        TEXT NOT NULL DEFAULT '',
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS {_USER_ACTIVITY_EVENT_TABLE}_user_created
                ON {_USER_ACTIVITY_EVENT_TABLE} (user_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS {_USER_ACTIVITY_DAILY_TABLE} (
                user_id          INTEGER NOT NULL,
                user_email       TEXT NOT NULL,
                activity_day     DATE NOT NULL,
                active_seconds   DOUBLE PRECISION NOT NULL DEFAULT 0,
                idle_seconds     DOUBLE PRECISION NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, activity_day)
            );
            CREATE INDEX IF NOT EXISTS {_USER_ACTIVITY_DAILY_TABLE}_day
                ON {_USER_ACTIVITY_DAILY_TABLE} (activity_day DESC, user_email);
        """)


def _record_user_event(
    cur,
    *,
    user_id: int,
    user_email: str,
    session_id: str,
    event_type: str,
    label: str = "",
    occurred_at: Optional[datetime.datetime] = None,
) -> None:
    if event_type not in ("login", "logout", "page", "action"):
        raise StoreError(f"Unknown user activity event: {event_type!r}")
    cur.execute(
        f"""INSERT INTO {_USER_ACTIVITY_EVENT_TABLE}
            (user_id, user_email, session_id, event_type, label, created_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
        (int(user_id), (user_email or "").strip(), session_id or "", event_type,
         (label or "").strip()[:160], _activity_now(occurred_at)),
    )


def start_user_activity_session(
    session_id: str,
    *,
    user_id: int,
    user_email: str,
    occurred_at: Optional[datetime.datetime] = None,
) -> None:
    """Start one browser session and append its login event."""
    if not AVAILABLE or not session_id:
        return
    now = _activity_now(occurred_at)
    try:
        _init_user_activity_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {_USER_ACTIVITY_SESSION_TABLE}
                    (session_id, user_id, user_email, started_at, last_heartbeat_at)
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (session_id) DO NOTHING""",
                (session_id, int(user_id), (user_email or "").strip(), now, now),
            )
            _record_user_event(
                cur, user_id=user_id, user_email=user_email, session_id=session_id,
                event_type="login", occurred_at=now,
            )
    except Exception as exc:
        raise StoreError(str(exc))


def record_user_activity_event(
    *,
    user_id: int,
    user_email: str,
    session_id: str,
    event_type: str,
    label: str = "",
    occurred_at: Optional[datetime.datetime] = None,
) -> None:
    """Append a safe page/action event; callers must never pass request data."""
    if not AVAILABLE or not user_id:
        return
    try:
        _init_user_activity_tables()
        with _conn() as conn, conn.cursor() as cur:
            _record_user_event(
                cur, user_id=user_id, user_email=user_email, session_id=session_id,
                event_type=event_type, label=label, occurred_at=occurred_at,
            )
    except Exception as exc:
        raise StoreError(str(exc))


def record_user_activity_heartbeat(
    session_id: str,
    state: str,
    *,
    occurred_at: Optional[datetime.datetime] = None,
) -> bool:
    """Credit the bounded prior interval to active or idle time.

    ``state`` describes the browser during the *next* interval. The elapsed
    time since the last heartbeat is credited using the state already recorded
    on the server, avoiding a client choosing its own historical duration.
    """
    if not AVAILABLE or not session_id or state not in ("active", "idle"):
        return False
    now = _activity_now(occurred_at)
    try:
        _init_user_activity_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT user_id, user_email, last_heartbeat_at, last_state, ended_at
                    FROM {_USER_ACTIVITY_SESSION_TABLE}
                    WHERE session_id=%s FOR UPDATE""",
                (session_id,),
            )
            row = cur.fetchone()
            if not row or row["ended_at"] is not None:
                return False
            last_at = row["last_heartbeat_at"]
            elapsed = max(0.0, min(
                (now - last_at).total_seconds(),
                float(_ACTIVITY_INTERVAL_CAP_SECONDS),
            ))
            active = elapsed if row["last_state"] == "active" else 0.0
            idle = elapsed if row["last_state"] == "idle" else 0.0
            cur.execute(
                f"""UPDATE {_USER_ACTIVITY_SESSION_TABLE}
                    SET active_seconds=active_seconds+%s, idle_seconds=idle_seconds+%s,
                        last_heartbeat_at=%s, last_state=%s
                    WHERE session_id=%s""",
                (active, idle, now, state, session_id),
            )
            if elapsed:
                cur.execute(
                    f"""INSERT INTO {_USER_ACTIVITY_DAILY_TABLE}
                        (user_id, user_email, activity_day, active_seconds, idle_seconds)
                        VALUES (%s,%s,%s,%s,%s)
                        ON CONFLICT (user_id, activity_day) DO UPDATE
                        SET user_email=EXCLUDED.user_email,
                            active_seconds={_USER_ACTIVITY_DAILY_TABLE}.active_seconds
                                           + EXCLUDED.active_seconds,
                            idle_seconds={_USER_ACTIVITY_DAILY_TABLE}.idle_seconds
                                         + EXCLUDED.idle_seconds""",
                    (row["user_id"], row["user_email"], _activity_day(now), active, idle),
                )
        return True
    except Exception as exc:
        raise StoreError(str(exc))


def end_user_activity_session(
    session_id: str,
    *,
    user_id: int,
    user_email: str,
    occurred_at: Optional[datetime.datetime] = None,
) -> None:
    """Finish a session, crediting no more than one final heartbeat interval."""
    if not AVAILABLE or not session_id:
        return
    now = _activity_now(occurred_at)
    try:
        record_user_activity_heartbeat(session_id, "idle", occurred_at=now)
        _init_user_activity_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_USER_ACTIVITY_SESSION_TABLE}
                    SET ended_at=COALESCE(ended_at, %s)
                    WHERE session_id=%s""",
                (now, session_id),
            )
            _record_user_event(
                cur, user_id=user_id, user_email=user_email, session_id=session_id,
                event_type="logout", occurred_at=now,
            )
    except Exception as exc:
        raise StoreError(str(exc))


def user_activity_report(
    from_day: datetime.date,
    to_day: datetime.date,
    *,
    user_id: Optional[int] = None,
) -> Dict:
    """Return daily rollups and safe event detail for an administrator report."""
    if not AVAILABLE:
        raise StoreError("User activity requires the configured Postgres database.")
    if from_day > to_day:
        raise StoreError("The start date must not be after the end date.")
    try:
        _init_user_activity_tables()
        filter_sql = " AND user_id=%s" if user_id is not None else ""
        filter_params = (int(user_id),) if user_id is not None else ()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                f"""SELECT user_id, user_email, activity_day, active_seconds, idle_seconds
                    FROM {_USER_ACTIVITY_DAILY_TABLE}
                    WHERE activity_day BETWEEN %s AND %s {filter_sql}
                    ORDER BY activity_day DESC, user_email""",
                (from_day, to_day) + filter_params,
            )
            daily_rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT user_id, user_email,
                           (started_at AT TIME ZONE 'Asia/Kolkata')::date AS activity_day,
                           COUNT(*) AS session_count
                    FROM {_USER_ACTIVITY_SESSION_TABLE}
                    WHERE (started_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s AND %s
                    {filter_sql}
                    GROUP BY user_id, user_email, activity_day""",
                (from_day, to_day) + filter_params,
            )
            session_rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                f"""SELECT user_id, user_email, event_type, label, created_at,
                           (created_at AT TIME ZONE 'Asia/Kolkata')::date AS activity_day
                    FROM {_USER_ACTIVITY_EVENT_TABLE}
                    WHERE (created_at AT TIME ZONE 'Asia/Kolkata')::date BETWEEN %s AND %s
                    {filter_sql}
                    ORDER BY created_at DESC, id DESC LIMIT 500""",
                (from_day, to_day) + filter_params,
            )
            events = [dict(r) for r in cur.fetchall()]
    except StoreError:
        raise
    except Exception as exc:
        raise StoreError(str(exc))

    by_key: Dict[Tuple[int, datetime.date], Dict] = {}
    for row in daily_rows:
        day = row["activity_day"]
        by_key[(row["user_id"], day)] = {
            "user_id": row["user_id"],
            "user_email": row["user_email"],
            "day": day.isoformat(),
            "active_seconds": round(float(row["active_seconds"] or 0), 1),
            "idle_seconds": round(float(row["idle_seconds"] or 0), 1),
            "session_count": 0,
            "pages": [],
            "actions": [],
        }
    for row in session_rows:
        key = (row["user_id"], row["activity_day"])
        entry = by_key.setdefault(key, {
            "user_id": row["user_id"], "user_email": row["user_email"],
            "day": row["activity_day"].isoformat(), "active_seconds": 0.0,
            "idle_seconds": 0.0, "session_count": 0, "pages": [], "actions": [],
        })
        entry["session_count"] = int(row["session_count"])

    event_detail = []
    for event in events:
        key = (event["user_id"], event["activity_day"])
        entry = by_key.setdefault(key, {
            "user_id": event["user_id"], "user_email": event["user_email"],
            "day": event["activity_day"].isoformat(), "active_seconds": 0.0,
            "idle_seconds": 0.0, "session_count": 0, "pages": [], "actions": [],
        })
        if event["event_type"] == "page" and event["label"] not in entry["pages"]:
            entry["pages"].append(event["label"])
        if event["event_type"] == "action" and event["label"] not in entry["actions"]:
            entry["actions"].append(event["label"])
        created = event["created_at"].astimezone(_ACTIVITY_TZ)
        event_detail.append({
            "day": event["activity_day"].isoformat(),
            "user_email": event["user_email"],
            "type": event["event_type"],
            "label": event["label"],
            "when": created.strftime("%d-%m-%Y %H:%M"),
        })

    rows = sorted(
        by_key.values(), key=lambda r: (r["day"], r["user_email"]), reverse=True
    )
    return {
        "from_day": from_day.isoformat(),
        "to_day": to_day.isoformat(),
        "rows": rows,
        "events": event_detail,
        "summary": {
            "active_seconds": round(sum(r["active_seconds"] for r in rows), 1),
            "idle_seconds": round(sum(r["idle_seconds"] for r in rows), 1),
            "sessions": sum(r["session_count"] for r in rows),
            "users": len({r["user_id"] for r in rows}),
        },
    }


# ---------------------------------------------------------------------------
# Known-empty months cache
#
# Cold gunicorn workers skip the L2 Postgres sheet_cache when it expires
# (_DATA_TTL, typically 30 min).  For plant-months that have NO production
# data (e.g. a future month, a site that shut down) the L3 Google Sheets
# read always returns zero records — wasting quota and adding latency on
# every cache miss.  ``known_empty_months`` provides a longer-lived (24 h)
# signal that a (plant, ym) pair is genuinely empty, letting cold workers
# skip the L3 read entirely until the TTL expires and a fresh check is made.
#
# The TTL is intentionally finite so that a month that was empty yesterday
# but starts receiving data today is detected within 24 hours.
#
# NOTE on source_fingerprints: if the fingerprint formula ever changes,
# run ``TRUNCATE source_fingerprints`` in Postgres to re-baseline all
# timestamps — otherwise every page load will false-flag a "sheet changed"
# event against the old formula's fingerprints.
# ---------------------------------------------------------------------------
_EM_TABLE = "known_empty_months"
_em_initialised = False


def _init_em_table() -> None:
    global _em_initialised
    if _em_initialised or not AVAILABLE:
        return
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {_EM_TABLE} (
        id          BIGSERIAL PRIMARY KEY,
        plant       TEXT      NOT NULL,
        ym          TEXT      NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS {_EM_TABLE}_plant_ym
        ON {_EM_TABLE} (plant, ym, recorded_at DESC);
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(ddl)
        _em_initialised = True
    except Exception:
        pass


def mark_empty_month(plant: str, ym: str) -> None:
    """Record that (plant, ym) returned zero records from Google Sheets.

    Idempotent — multiple writes for the same (plant, ym) are harmless;
    ``is_known_empty`` selects the most recent row, so a fresh re-check
    simply inserts a new row and resets the TTL.
    """
    if not AVAILABLE:
        return
    try:
        _init_em_table()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {_EM_TABLE} (plant, ym) VALUES (%s, %s)",
                (plant, ym),
            )
    except Exception:
        pass


def is_known_empty(plant: str, ym: str, ttl_seconds: float = 86_400.0) -> bool:
    """Return True if (plant, ym) was confirmed empty within ``ttl_seconds``.

    A True result lets the caller skip the live L3 Google Sheets read.
    Returns False when Postgres is unavailable, so the caller falls through
    to the normal read path (safe default).
    """
    if not AVAILABLE:
        return False
    try:
        _init_em_table()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT recorded_at FROM {_EM_TABLE}
                WHERE plant = %s AND ym = %s
                ORDER BY recorded_at DESC LIMIT 1
                """,
                (plant, ym),
            )
            row = cur.fetchone()
        if row is None:
            return False
        import datetime
        age = (datetime.datetime.now(tz=datetime.timezone.utc) - row[0]).total_seconds()
        return age <= ttl_seconds
    except Exception:
        return False
