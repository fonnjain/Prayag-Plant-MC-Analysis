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
import datetime
from typing import List, Optional, Dict

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
