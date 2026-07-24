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

FITTINGS PRODUCTION SOURCE
---------------------------
fitting_prod_kg = Report-12 "Wt in Kgs" + "Actual Rejection Weight (in Kgs)"
(gross actual = good output + rejected weight).
The labour sheet's "Fittings Production" column is MISLABELLED in FY2026-27
(contains PIECES, not kg).  The R12 figure is authoritative for all FYs.

AUTO LABOUR SOURCES (Part A + B)
---------------------------------
Hours and headcount: "EMPLOYEE DATA DETAILS (COST)" workbook, tabs D-1/D-2/D-3.
  Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN (resolved by label, not row number).
  Months are reverse-ordered in the sheet (latest first); mapped by header text.

Wages: monthly "<N>. Wages <Mon>-<Year>" files, tab KH-1.
  Filter: DEPARTMENT == "CPVC" (Prayag's segment label for Plumbing).
  Column: "TOTAL PAYABLE" located by header text (shifts between files).

The segment labour-cost sheet's Plumbing tab is still read as a cross-check
reference and for pipe_prod_kg.  Divergence > 0.5% surfaces a reconciliation
warning but does not suppress either figure.
"""
from __future__ import annotations

import json
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
# Tab parsed: "Plumbing" (wages + hours ONLY — production from Report-12)

LABOUR_SOURCES: dict[str, dict[str, str]] = {
    "PLUMBING": {
        "2627": "1ttlpHLrlTsimcdSmk3-HGnPu14PX7SGtk9Of2Q5pDvw",
        "2526": "1N6gVEZyv1CLs5ARQHeebjAxOyvdkwOJFPqDWHUUOy_g",
        "2324": "1fjsJ6g91sWADHQ9vc0-cxiMQbQDlE5fKzyVx_mJq4Fc",
        "2223": "1H4W23l3YPPkLXm8HYP7-uRKS4zaamHW4BByX7O_uU68",
    },
    "PTMT": {},   # stubbed
}

# ── Employee Data file IDs ─────────────────────────────────────────────────────
# Source: "<FY> EMPLOYEE DATA DETAILS (COST)"
# Tabs: D-1 (Paid Hours), D-2 (Actual Working Hours), D-3 (Actual Number Of Persons)
# Plumbing = TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN (resolved by label, never by row).
EMPLOYEE_DATA_SOURCES: dict[str, str] = {
    "2627": "1Mfjo-CaxboN52hUO_IzrKqEAFxHgegJI4BHQb6H4VYM",
    "2526": "1b34kCxmbwIWQJdZNL4-I4wuWGU5EzG0NJTg_V7QfYEs",
}

# ── Monthly wages file IDs ─────────────────────────────────────────────────────
# Source: Monthly "<N>. Wages <Mon>-<Year>" files.  Tab KH-1; filter DEPT=="CPVC".
# TOTAL PAYABLE column found by header text (shifts between files — never hardcode).
# Acceptance: Apr-2025 = 1,904,701 ; Mar-2026 = 1,529,429 ; FY2025-26 = 21,452,790.
# Add new months here as workbooks become available.
WAGES_SOURCES: dict[str, dict[str, dict[str, str]]] = {
    "PLUMBING": {
        # FY 2025-26: Apr-2025 through Mar-2026
        "2526": {
            "2025-04": "1jgp3ftEr1xlEk8kXZp1Wo_ojrd2GrhGF7OZ5kNZO9z8",  # Apr-2025 → 1,904,701
            # 2025-05 through 2026-02: file IDs not yet registered — add here
            "2026-03": "1hl0FMeR6IxvXZonXVUpSVEtWJuqy7lZ9E3K8r4ZiOzE",  # Mar-2026 → 1,529,429
        },
        # FY 2026-27: Apr-2026 onwards (add as files become available)
        "2627": {
        },
    },
    "PTMT": {},   # stubbed — to be added when PTMT costing is built
}


# ── Per-FY tab name overrides ──────────────────────────────────────────────────
# Older workbooks use different tab names for the same data.
# "plumbing_tab": name of the Plumbing wages/hours tab (default "Plumbing")
# "ideal_rates_tab": name of the Ideal Labour Cost tab (default "Ideal Labour Cost");
#   set to None when the tab does not exist in that FY workbook.
LABOUR_TAB_OVERRIDES: dict[str, dict] = {
    # FY2023-24 workbook uses "Plumbing & Garden Pipe" instead of "Plumbing"
    "2324": {"plumbing_tab": "Plumbing & Garden Pipe"},
    # FY2022-23 workbook uses "KEHRANI PLANT" and has no Ideal Labour Cost tab
    "2223": {"plumbing_tab": "KEHRANI PLANT", "ideal_rates_tab": None},
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

CREATE TABLE IF NOT EXISTS costing_power_monthly (
    id                        BIGSERIAL   PRIMARY KEY,
    segment                   TEXT        NOT NULL,
    fy                        TEXT        NOT NULL,
    month_label               TEXT        NOT NULL,
    month_num                 INT         NOT NULL,
    pvc_prod_kg               NUMERIC,
    total_prod_kg_u2          NUMERIC,
    headcount_u2              NUMERIC,
    contractor_count_u2       NUMERIC,
    paid_wages_u2             NUMERIC,
    contractor_wages_u2       NUMERIC,
    total_wages_u2            NUMERIC,
    jvvl_amount               NUMERIC,
    elec_gen_kwh              NUMERIC,
    per_unit_cost             NUMERIC,
    solar1_kwh                NUMERIC,
    solar2_kwh                NUMERIC,
    total_kwh                 NUMERIC,
    kwh_per_kg                NUMERIC,
    rate_708_rs               NUMERIC,
    rate_1150_rs              NUMERIC,
    total_power_708           NUMERIC,
    total_power_1150          NUMERIC,
    per_kg_power_708          NUMERIC,
    per_kg_power_1150         NUMERIC,
    per_kg_labour_u2          NUMERIC,
    total_cost_708            NUMERIC,
    new_total_cost            NUMERIC,
    ideal_power_total         NUMERIC,
    actual_power_total        NUMERIC,
    ideal_kg_power            NUMERIC,
    actual_kg_power           NUMERIC,
    pipe_ideal_power_rate     NUMERIC,
    fitting_ideal_power_rate  NUMERIC,
    pipe_ideal_labour_rate    NUMERIC,
    fitting_ideal_labour_rate NUMERIC,
    CONSTRAINT costing_power_monthly_nat UNIQUE (segment, fy, month_label)
);
"""

# Idempotent migrations — run each ALTER separately so one failure doesn't
# block the others.  Postgres ADD COLUMN IF NOT EXISTS is safe to re-run.
_DDL_MIGRATION_STMTS = [
    # R12 fittings source columns (gross actual = wt_in_kgs + rejection_kg)
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS fitting_r12_kg        NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS wt_in_kgs_total       NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS r12_rejection_kg      NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS fitting_kg_source      TEXT",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS fitting_variance_pct   NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS fitting_divergent_n    INT",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS fitting_divergent_rows JSONB",
    # Auto-sourced hours / wages (Employee Data Details D-1/D-2/D-3 + KH-1 wages files)
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS auto_paid_hours   NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS auto_actual_hours  NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS auto_headcount     NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS auto_wages         NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS hours_source       TEXT",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS wages_source       TEXT",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS hours_recon_pct    NUMERIC",
    "ALTER TABLE costing_labour_monthly ADD COLUMN IF NOT EXISTS wages_recon_pct    NUMERIC",
    # Employee data + wages file tracking in meta
    "ALTER TABLE costing_labour_meta    ADD COLUMN IF NOT EXISTS emp_data_file_id   TEXT",
    "ALTER TABLE costing_labour_meta    ADD COLUMN IF NOT EXISTS wages_file_count   INT",
]

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
    """Create costing_ tables and run idempotent column migrations."""
    global _INITIALISED
    if _INITIALISED or not AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(_DDL)
            for stmt in _DDL_MIGRATION_STMTS:
                try:
                    cur.execute(stmt)
                except Exception:
                    conn.rollback()
                    logger.warning("costing migration skipped (non-fatal): %s", stmt[:80])
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
    """Insert/replace all monthly rows for (segment, fy). Returns count.

    Rows may carry the following R12 source fields (populated by load_r12_for_fy
    before this call):
      fitting_r12_kg, wt_in_kgs_total, fitting_kg_source,
      fitting_variance_pct, fitting_divergent_n, fitting_divergent_rows (dict list).
    """
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
                divergent_json = None
                dv = r.get("fitting_divergent_rows")
                if dv is not None:
                    divergent_json = json.dumps(dv)

                cur.execute(
                    """INSERT INTO costing_labour_monthly
                       (segment, fy, month_label, month_num, no_of_labour,
                        contractor_labour, paid_hours, actual_hours,
                        paid_hours_devoted, actual_hours_devoted,
                        paid_wages, contractor_wages,
                        per_hour_cost_paid, per_hour_cost_actual,
                        pipe_prod_kg, fitting_prod_kg, total_prod_kg,
                        per_kg_labour_cost,
                        fitting_r12_kg, wt_in_kgs_total, r12_rejection_kg,
                        fitting_kg_source,
                        fitting_variance_pct, fitting_divergent_n,
                        fitting_divergent_rows,
                        auto_paid_hours, auto_actual_hours, auto_headcount,
                        auto_wages, hours_source, wages_source,
                        hours_recon_pct, wages_recon_pct)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s)
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
                           per_kg_labour_cost    = EXCLUDED.per_kg_labour_cost,
                           fitting_r12_kg        = EXCLUDED.fitting_r12_kg,
                           wt_in_kgs_total       = EXCLUDED.wt_in_kgs_total,
                           r12_rejection_kg      = EXCLUDED.r12_rejection_kg,
                           fitting_kg_source     = EXCLUDED.fitting_kg_source,
                           fitting_variance_pct  = EXCLUDED.fitting_variance_pct,
                           fitting_divergent_n   = EXCLUDED.fitting_divergent_n,
                           fitting_divergent_rows = EXCLUDED.fitting_divergent_rows,
                           auto_paid_hours       = EXCLUDED.auto_paid_hours,
                           auto_actual_hours     = EXCLUDED.auto_actual_hours,
                           auto_headcount        = EXCLUDED.auto_headcount,
                           auto_wages            = EXCLUDED.auto_wages,
                           hours_source          = EXCLUDED.hours_source,
                           wages_source          = EXCLUDED.wages_source,
                           hours_recon_pct       = EXCLUDED.hours_recon_pct,
                           wages_recon_pct       = EXCLUDED.wages_recon_pct
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
                        r.get("fitting_r12_kg"), r.get("wt_in_kgs_total"),
                        r.get("r12_rejection_kg"),
                        r.get("fitting_kg_source"),
                        r.get("fitting_variance_pct"), r.get("fitting_divergent_n"),
                        divergent_json,
                        r.get("auto_paid_hours"), r.get("auto_actual_hours"),
                        r.get("auto_headcount"), r.get("auto_wages"),
                        r.get("hours_source"), r.get("wages_source"),
                        r.get("hours_recon_pct"), r.get("wages_recon_pct"),
                    ),
                )
        return len(rows)
    except Exception as e:
        raise CostingModelError(f"upsert_labour_monthly: {e}") from e


def upsert_labour_meta(
    segment: str, fy: str, *, frozen: bool,
    n_months: int, pipe_ideal_rate=None, fitting_ideal_rate=None,
    source_file_id: str = "",
    emp_data_file_id: str = "",
    wages_file_count: int = 0,
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
                    fitting_ideal_rate, source_file_id,
                    emp_data_file_id, wages_file_count, loaded_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (segment, fy) DO UPDATE SET
                       frozen             = EXCLUDED.frozen,
                       n_months           = EXCLUDED.n_months,
                       pipe_ideal_rate    = EXCLUDED.pipe_ideal_rate,
                       fitting_ideal_rate = EXCLUDED.fitting_ideal_rate,
                       source_file_id     = EXCLUDED.source_file_id,
                       emp_data_file_id   = EXCLUDED.emp_data_file_id,
                       wages_file_count   = EXCLUDED.wages_file_count,
                       loaded_at          = now()
                """,
                (segment, fy, frozen, n_months, pipe_ideal_rate,
                 fitting_ideal_rate, source_file_id,
                 emp_data_file_id, wages_file_count),
            )
    except Exception as e:
        raise CostingModelError(f"upsert_labour_meta: {e}") from e
