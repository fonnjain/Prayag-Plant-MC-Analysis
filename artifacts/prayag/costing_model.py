"""costing_model.py — DB schema, FY constants, and freeze logic for Costing.

FREEZE RULE
-----------
Any FY earlier than LIVE_FY ("2627") is FROZEN.  A frozen-FY snapshot may be
written ONCE — subsequent load_labour_fy() calls are no-ops unless force=True.
FY2026-27 (LIVE_FY) is always recomputable on demand.

CATEGORIES
----------
"PLUMBING"  — built now (pipe + fittings, both labour and RM costing)
"PTMT"      — stubbed (shows "coming soon")
"""
from __future__ import annotations

import logging
import os
from typing import Optional

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None  # type: ignore

import store

logger = logging.getLogger(__name__)

AVAILABLE = store.AVAILABLE

# ── FY constants ──────────────────────────────────────────────────────────────

LIVE_FY = "2627"

FY_CONFIG: dict[str, dict] = {
    "2627": {"label": "FY2026-27", "frozen": False, "short": "26-27"},
    "2526": {"label": "FY2025-26", "frozen": True,  "short": "25-26"},
    "2324": {"label": "FY2023-24", "frozen": True,  "short": "23-24"},
    "2223": {"label": "FY2022-23", "frozen": True,  "short": "22-23"},
}

FY_ORDER = ["2627", "2526", "2324", "2223"]   # newest first

# FY2024-25 has no workbook (not listed by business).

CATEGORIES = ["PLUMBING", "PTMT"]
CATEGORY_LABELS = {"PLUMBING": "Plumbing", "PTMT": "PTMT"}


def is_frozen(fy: str) -> bool:
    """Return True for any FY earlier than the live FY."""
    return FY_CONFIG.get(fy, {}).get("frozen", True)


# ── Labour workbook file IDs ──────────────────────────────────────────────────
# Source: "Annual <FY> Segment Wise Labour Cost, Solar Cost & Power Cost"
# Tab parsed: "Plumbing"

LABOUR_SOURCES: dict[str, dict[str, str]] = {
    "PLUMBING": {
        "2627": "1ttlpHLrlTsimcdSmk3-HGnPu14PX7SGtk9Of2Q5pDvw",
        "2526": "1N6gVEZyv1CLs5ARQHeebjAxOyvdkwOJFPqDWHUUOy_g",
        "2324": "1fjsJ6g91sWADHQ9vc0-cxiMQbQDlE5fKzyVx_mJq4Fc",
        "2223": "1H4W23l3YPPkLXm8HYP7-uRKS4zaamHW4BByX7O_uU68",
    },
    "PTMT": {},   # stubbed
}


def labour_file_id(category: str, fy: str) -> Optional[str]:
    return LABOUR_SOURCES.get(category, {}).get(fy)


# ── DB DDL ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS costing_labour_monthly (
    id                    BIGSERIAL   PRIMARY KEY,
    segment               TEXT        NOT NULL,
    fy                    TEXT        NOT NULL,
    month_label           TEXT        NOT NULL,
    month_num             INT         NOT NULL,
    no_of_labour          NUMERIC,
    contractor_labour     NUMERIC,
    paid_hours            NUMERIC,
    actual_hours          NUMERIC,
    paid_hours_devoted    NUMERIC,
    actual_hours_devoted  NUMERIC,
    paid_wages            NUMERIC,
    contractor_wages      NUMERIC,
    per_hour_cost_paid    NUMERIC,
    per_hour_cost_actual  NUMERIC,
    pipe_prod_kg          NUMERIC,
    fitting_prod_kg       NUMERIC,
    total_prod_kg         NUMERIC,
    per_kg_labour_cost    NUMERIC,
    CONSTRAINT costing_labour_monthly_nat UNIQUE (segment, fy, month_label)
);

CREATE TABLE IF NOT EXISTS costing_labour_meta (
    id                 BIGSERIAL   PRIMARY KEY,
    segment            TEXT        NOT NULL,
    fy                 TEXT        NOT NULL,
    frozen             BOOLEAN     NOT NULL DEFAULT FALSE,
    n_months           INT         NOT NULL DEFAULT 0,
    pipe_ideal_rate    NUMERIC,
    fitting_ideal_rate NUMERIC,
    source_file_id     TEXT,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT costing_labour_meta_nat UNIQUE (segment, fy)
);
"""

_INITIALISED = False


class CostingModelError(Exception):
    pass


def _coerce_row(row: dict) -> dict:
    """Convert decimal.Decimal values to float at the DB read boundary.

    Postgres NUMERIC columns come back as decimal.Decimal via psycopg2.
    All downstream maths in costing modules uses Python float, so mixing
    would raise TypeError on the first multiplication.  Coerce once here
    so every caller gets plain floats without any individual cast guards.
    """
    from decimal import Decimal
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in row.items()}


def init_costing_tables() -> None:
    """Create costing_ tables idempotently (lazy, once per process)."""
    global _INITIALISED
    if _INITIALISED or not AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(_DDL)
        _INITIALISED = True
    except Exception as e:
        raise CostingModelError(f"init_costing_tables failed: {e}") from e


# ── Meta helpers ──────────────────────────────────────────────────────────────

def get_labour_meta(segment: str, fy: str) -> Optional[dict]:
    """Return metadata row for (segment, fy), or None if not yet loaded."""
    if not AVAILABLE:
        return None
    try:
        init_costing_tables()
        with store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM costing_labour_meta WHERE segment=%s AND fy=%s",
                (segment, fy),
            )
            row = cur.fetchone()
            return _coerce_row(dict(row)) if row else None
    except Exception:
        logger.exception("get_labour_meta failed")
        return None


def get_labour_monthly(segment: str, fy: str) -> list:
    """Return all monthly rows for (segment, fy), ordered APR→MAR."""
    if not AVAILABLE:
        return []
    try:
        init_costing_tables()
        with store._conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM costing_labour_monthly "
                "WHERE segment=%s AND fy=%s ORDER BY month_num",
                (segment, fy),
            )
            return [_coerce_row(dict(r)) for r in cur.fetchall()]
    except Exception:
        logger.exception("get_labour_monthly failed")
        return []


def upsert_labour_monthly(segment: str, fy: str, rows: list) -> int:
    """Insert/replace all monthly rows for (segment, fy). Returns count."""
    if not rows or not AVAILABLE:
        return 0
    init_costing_tables()
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM costing_labour_monthly WHERE segment=%s AND fy=%s",
                (segment, fy),
            )
            for r in rows:
                cur.execute(
                    """INSERT INTO costing_labour_monthly
                       (segment, fy, month_label, month_num, no_of_labour,
                        contractor_labour, paid_hours, actual_hours,
                        paid_hours_devoted, actual_hours_devoted,
                        paid_wages, contractor_wages,
                        per_hour_cost_paid, per_hour_cost_actual,
                        pipe_prod_kg, fitting_prod_kg, total_prod_kg,
                        per_kg_labour_cost)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (segment, fy, month_label) DO UPDATE SET
                           month_num             = EXCLUDED.month_num,
                           no_of_labour          = EXCLUDED.no_of_labour,
                           contractor_labour     = EXCLUDED.contractor_labour,
                           paid_hours            = EXCLUDED.paid_hours,
                           actual_hours          = EXCLUDED.actual_hours,
                           paid_hours_devoted    = EXCLUDED.paid_hours_devoted,
                           actual_hours_devoted  = EXCLUDED.actual_hours_devoted,
                           paid_wages            = EXCLUDED.paid_wages,
                           contractor_wages      = EXCLUDED.contractor_wages,
                           per_hour_cost_paid    = EXCLUDED.per_hour_cost_paid,
                           per_hour_cost_actual  = EXCLUDED.per_hour_cost_actual,
                           pipe_prod_kg          = EXCLUDED.pipe_prod_kg,
                           fitting_prod_kg       = EXCLUDED.fitting_prod_kg,
                           total_prod_kg         = EXCLUDED.total_prod_kg,
                           per_kg_labour_cost    = EXCLUDED.per_kg_labour_cost
                    """,
                    (
                        segment, fy,
                        r.get("month_label"), r.get("month_num"),
                        r.get("no_of_labour"), r.get("contractor_labour"),
                        r.get("paid_hours"), r.get("actual_hours"),
                        r.get("paid_hours_devoted"), r.get("actual_hours_devoted"),
                        r.get("paid_wages"), r.get("contractor_wages"),
                        r.get("per_hour_cost_paid"), r.get("per_hour_cost_actual"),
                        r.get("pipe_prod_kg"), r.get("fitting_prod_kg"),
                        r.get("total_prod_kg"), r.get("per_kg_labour_cost"),
                    ),
                )
        return len(rows)
    except Exception as e:
        raise CostingModelError(f"upsert_labour_monthly: {e}") from e


def upsert_labour_meta(
    segment: str, fy: str, *, frozen: bool,
    n_months: int, pipe_ideal_rate=None, fitting_ideal_rate=None,
    source_file_id: str = "",
) -> None:
    """Insert or update the metadata row for (segment, fy)."""
    if not AVAILABLE:
        return
    init_costing_tables()
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO costing_labour_meta
                   (segment, fy, frozen, n_months, pipe_ideal_rate,
                    fitting_ideal_rate, source_file_id, loaded_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (segment, fy) DO UPDATE SET
                       frozen             = EXCLUDED.frozen,
                       n_months           = EXCLUDED.n_months,
                       pipe_ideal_rate    = EXCLUDED.pipe_ideal_rate,
                       fitting_ideal_rate = EXCLUDED.fitting_ideal_rate,
                       source_file_id     = EXCLUDED.source_file_id,
                       loaded_at          = now()
                """,
                (segment, fy, frozen, n_months, pipe_ideal_rate,
                 fitting_ideal_rate, source_file_id),
            )
    except Exception as e:
        raise CostingModelError(f"upsert_labour_meta: {e}") from e
