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
import datetime
from typing import List, Optional, Dict, Tuple

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
                created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS {_USER_BOOTSTRAP_TABLE} (
                seed_key      TEXT PRIMARY KEY,
                completed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)


def _user_shape(row) -> Dict:
    """Return a password-safe user record for templates and routes."""
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "is_active": bool(row["is_active"]),
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
                    f"""INSERT INTO {_USER_TABLE} (email, password_hash, role, is_active)
                        VALUES (%s, %s, %s, TRUE)
                        ON CONFLICT (email) DO UPDATE
                           SET password_hash = EXCLUDED.password_hash,
                               role = EXCLUDED.role,
                               is_active = TRUE,
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
                f"""SELECT id, email, password_hash, role, is_active
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
                f"""SELECT id, email, role, is_active, created_at, updated_at
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
                f"""SELECT id, email, role, is_active, created_at, updated_at
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
                f"""INSERT INTO {_USER_TABLE} (email, password_hash, role)
                    VALUES (%s, %s, %s)
                    RETURNING id, email, role, is_active, created_at, updated_at""",
                (email, password_hash, role),
            )
            row = cur.fetchone()
        return _user_shape(row)
    except Exception as exc:
        message = str(exc)
        if "unique" in message.lower():
            raise StoreError("An account with that email already exists.")
        raise StoreError(message)


def set_user_password(user_id: int, password_hash: str) -> bool:
    """Replace one password hash. Returns False for a missing user."""
    if not AVAILABLE:
        raise StoreError("User management requires the configured Postgres database.")
    try:
        _init_user_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""UPDATE {_USER_TABLE}
                    SET password_hash=%s, updated_at=now() WHERE id=%s""",
                (password_hash, int(user_id)),
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
