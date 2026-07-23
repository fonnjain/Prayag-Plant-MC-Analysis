"""
Machine Planning data model — Phase MP-0.

Dataclasses for each planning table and idempotent Postgres DDL.
All tables are scoped to (segment, effective_month) so each month's plan is
versioned independently and can be reset to source defaults without affecting
history.

ADDITIVE ONLY: this module touches only ``mp_`` tables. The existing headline
pipeline (/, /data, /reports, /plan) is not imported and not affected.
"""
from __future__ import annotations

import os
import json
import datetime
import dataclasses
from typing import Any, Dict, List, Optional

try:
    import psycopg2
    import psycopg2.extras
except Exception:
    psycopg2 = None  # type: ignore

AVAILABLE: bool = bool(os.environ.get("DATABASE_URL")) and psycopg2 is not None

_INITIALISED = False


class MpModelError(Exception):
    """Raised when a DB operation fails."""


def _conn():
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured (mp_model unavailable).")
    return psycopg2.connect(os.environ["DATABASE_URL"])


# ---------------------------------------------------------------------------
# Dataclasses (one per table — thin wrappers used by seeders and the future UI)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class MpMachine:
    segment: str
    machine: str
    kind: str                       # 'extrusion' | 'moulding'
    operators_ot: int = 0           # overtime-eligible operators
    support_w: int = 0              # support / regular workers
    capacity_hrs_month: float = 500.0
    effective_month: str = ""
    shifts_per_day: int = 2
    hours_per_shift: float = 10.0
    working_days_month: int = 25


@dataclasses.dataclass
class MpRouting:
    segment: str
    item_code: str                  # normalised (strip + uppercase + no spaces/hyphens)
    machine: str
    material: str = ""              # CPVC / UPVC / SWR / AGRI / FAUCET / …
    capable: bool = True
    effective_month: str = ""


@dataclasses.dataclass
class MpFittingStd:
    segment: str
    item_code: str
    machine: str
    cavity: Optional[float] = None
    cycle_time_sec: Optional[float] = None
    effective_month: str = ""


@dataclasses.dataclass
class MpBomWeight:
    segment: str
    item_code: str
    weight_per_pc_kg: float
    effective_month: str = ""


@dataclasses.dataclass
class MpPerHour:
    segment: str
    item_code: str
    basis: str                      # 'kg_per_hr' | 'cycle'
    value: float
    effective_month: str = ""


@dataclasses.dataclass
class MpCompoundRecipe:
    segment: str
    material: str                   # CPVC / UPVC / SWR / AGRI
    type: str                       # 'pipe' | 'fitting'
    component: str
    ratio_kg: float = 0.0
    price_per_kg: float = 0.0
    wastage_factor: float = 1.0
    needs_recipe: bool = False      # True when no recipe was found in the source
    effective_month: str = ""


@dataclasses.dataclass
class MpParams:
    segment: str
    waste_pct: float = 0.0   # 0 = use auto-measured rate from Report-15; >0 = manual override (%)
    pulverizer_pct: float = 25.0
    effective_month: str = ""
    min_run_block_hours: float = 2.0
    night_changeover_allowed: bool = False
    week_days: str = "[6,6,6,7]"
    cpvc_mat_rate: float = 0.0   # 0 = compute from seeded items
    upvc_mat_rate: float = 0.0
    swr_mat_rate: float = 0.0
    agri_mat_rate: float = 0.0
    # Follow-up RAG thresholds (% deviation from plan-to-date)
    rag_amber_pct: float = 10.0   # 0-10% = GREEN, 10-25% = AMBER
    rag_red_pct: float = 25.0     # >25% = RED
    hours_dev_pct: float = 15.0   # hours deviation warning threshold


@dataclasses.dataclass
class MpPlanLine:
    """One scheduler block row written at freeze time."""
    plan_run_id: int
    segment: str
    month: str
    week: int
    day: int
    shift: str          # 'DAY' | 'NIGHT'
    machine: str
    machine_norm: str
    item_code: str
    item_norm: str
    material: str
    planned_pcs: float = 0.0
    planned_kg: float = 0.0
    planned_hours: float = 0.0
    net_hours: float = 0.0      # planned_hours − excess_hours
    rate_used: float = 0.0
    rate_estimated: bool = False
    is_excess: bool = False
    is_idle: bool = False


@dataclasses.dataclass
class MpActualLine:
    """One production row ingested from Report-11 / Report-12."""
    segment: str
    month: str
    date: str           # ISO date string 'YYYY-MM-DD'
    machine: str
    machine_norm: str
    item_code: str
    item_norm: str
    material: str
    actual_pcs: float = 0.0
    actual_kg: float = 0.0
    actual_hours: float = 0.0
    rejection_kg: float = 0.0
    source_tab: str = ""


@dataclasses.dataclass
class MpMachineDowntime:
    """Planned or unplanned machine unavailability (breakdown or maintenance).

    Records are APPEND-ONLY and permanently retained.  Status lifecycle:
      open   → resolved=False, end_date=None  (machine currently unavailable)
      closed → resolved=True,  end_date set   (machine back in planning)
    Un-ticking "back in planning" resets to open state.
    """
    segment: str
    machine: str
    kind: str               # 'breakdown' | 'maintenance'
    start_date: datetime.date
    end_date: Optional[datetime.date] = None       # last day of downtime (inclusive); None = indefinite
    reason: str = ""
    resolved: bool = False                          # True once "back in planning" ticked
    resolved_at: Optional[datetime.datetime] = None # when the checkbox was ticked
    deleted: bool = False                           # soft-deleted (wrong entry); hidden from UI
    deleted_at: Optional[datetime.datetime] = None  # when the record was soft-deleted
    id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None
    updated_at: Optional[datetime.datetime] = None


@dataclasses.dataclass
class MpPlanRun:
    segment: str
    month: str
    uploaded_demand: Any = None     # JSON-serialisable (pipe demand dicts)
    fitting_demand: Any = None      # JSON-serialisable (fitting demand dicts)
    frozen_inputs: Any = None       # snapshot of all seed inputs at run time
    results: Any = None             # optimiser output (pipe + fitting)
    status: str = "pending"         # 'pending' | 'draft' (frozen) | 'finalized'
    uploaded_file_path: str = ""    # path to .xlsx saved on disk
    created_at: Optional[datetime.datetime] = None


# ---------------------------------------------------------------------------
# DDL — all tables, unique keys, indexes
# ---------------------------------------------------------------------------
_DDL = """
CREATE TABLE IF NOT EXISTS mp_machine (
    id                  BIGSERIAL   PRIMARY KEY,
    segment             TEXT        NOT NULL,
    machine             TEXT        NOT NULL,
    kind                TEXT        NOT NULL,
    operators_ot        INT         NOT NULL DEFAULT 0,
    support_w           INT         NOT NULL DEFAULT 0,
    capacity_hrs_month  NUMERIC     NOT NULL DEFAULT 500,
    effective_month     TEXT        NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_machine_natural UNIQUE (segment, machine, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_routing (
    id              BIGSERIAL   PRIMARY KEY,
    segment         TEXT        NOT NULL,
    item_code       TEXT        NOT NULL,
    machine         TEXT        NOT NULL,
    material        TEXT        NOT NULL DEFAULT '',
    capable         BOOLEAN     NOT NULL DEFAULT TRUE,
    effective_month TEXT        NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_routing_natural UNIQUE (segment, item_code, machine, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_fitting_std (
    id              BIGSERIAL   PRIMARY KEY,
    segment         TEXT        NOT NULL,
    item_code       TEXT        NOT NULL,
    machine         TEXT        NOT NULL,
    cavity          NUMERIC,
    cycle_time_sec  NUMERIC,
    effective_month TEXT        NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_fitting_std_natural UNIQUE (segment, item_code, machine, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_bom_weight (
    id                  BIGSERIAL   PRIMARY KEY,
    segment             TEXT        NOT NULL,
    item_code           TEXT        NOT NULL,
    weight_per_pc_kg    NUMERIC     NOT NULL,
    effective_month     TEXT        NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_bom_weight_natural UNIQUE (segment, item_code, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_per_hour (
    id              BIGSERIAL   PRIMARY KEY,
    segment         TEXT        NOT NULL,
    item_code       TEXT        NOT NULL,
    basis           TEXT        NOT NULL,
    value           NUMERIC     NOT NULL,
    effective_month TEXT        NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_per_hour_natural UNIQUE (segment, item_code, basis, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_compound_recipe (
    id              BIGSERIAL   PRIMARY KEY,
    segment         TEXT        NOT NULL,
    material        TEXT        NOT NULL,
    type            TEXT        NOT NULL,
    component       TEXT        NOT NULL DEFAULT '',
    ratio_kg        NUMERIC     NOT NULL DEFAULT 0,
    price_per_kg    NUMERIC     NOT NULL DEFAULT 0,
    wastage_factor  NUMERIC     NOT NULL DEFAULT 1,
    needs_recipe    BOOLEAN     NOT NULL DEFAULT FALSE,
    effective_month TEXT        NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_compound_recipe_natural
        UNIQUE (segment, material, type, component, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_params (
    id              BIGSERIAL   PRIMARY KEY,
    segment         TEXT        NOT NULL,
    waste_pct       NUMERIC     NOT NULL DEFAULT 0,
    pulverizer_pct  NUMERIC     NOT NULL DEFAULT 25,
    effective_month TEXT        NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_params_natural UNIQUE (segment, effective_month)
);

CREATE TABLE IF NOT EXISTS mp_plan_run (
    id                   BIGSERIAL   PRIMARY KEY,
    segment              TEXT        NOT NULL,
    month                TEXT        NOT NULL,
    uploaded_demand      JSONB,
    fitting_demand       JSONB,
    frozen_inputs        JSONB,
    results              JSONB,
    status               TEXT        NOT NULL DEFAULT 'pending',
    uploaded_file_path   TEXT        NOT NULL DEFAULT '',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_plan_run_natural UNIQUE (segment, month, created_at)
);

CREATE TABLE IF NOT EXISTS mp_plan_line (
    id             BIGSERIAL   PRIMARY KEY,
    plan_run_id    BIGINT      NOT NULL,
    segment        TEXT        NOT NULL,
    month          TEXT        NOT NULL,
    week           INT         NOT NULL DEFAULT 0,
    day            INT         NOT NULL DEFAULT 0,
    shift          TEXT        NOT NULL DEFAULT '',
    machine        TEXT        NOT NULL DEFAULT '',
    machine_norm   TEXT        NOT NULL DEFAULT '',
    item_code      TEXT        NOT NULL DEFAULT '',
    item_norm      TEXT        NOT NULL DEFAULT '',
    material       TEXT        NOT NULL DEFAULT '',
    planned_pcs    NUMERIC     NOT NULL DEFAULT 0,
    planned_kg     NUMERIC     NOT NULL DEFAULT 0,
    planned_hours  NUMERIC     NOT NULL DEFAULT 0,
    net_hours      NUMERIC     NOT NULL DEFAULT 0,
    rate_used      NUMERIC     NOT NULL DEFAULT 0,
    rate_estimated BOOLEAN     NOT NULL DEFAULT FALSE,
    is_excess      BOOLEAN     NOT NULL DEFAULT FALSE,
    is_idle        BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS mp_plan_line_run ON mp_plan_line (plan_run_id);
CREATE INDEX IF NOT EXISTS mp_plan_line_lookup ON mp_plan_line (segment, month, machine_norm);

CREATE TABLE IF NOT EXISTS mp_actual_line (
    id             BIGSERIAL   PRIMARY KEY,
    segment        TEXT        NOT NULL,
    month          TEXT        NOT NULL,
    date           DATE        NOT NULL,
    machine        TEXT        NOT NULL DEFAULT '',
    machine_norm   TEXT        NOT NULL DEFAULT '',
    item_code      TEXT        NOT NULL DEFAULT '',
    item_norm      TEXT        NOT NULL DEFAULT '',
    material       TEXT        NOT NULL DEFAULT '',
    actual_pcs     NUMERIC     NOT NULL DEFAULT 0,
    actual_kg      NUMERIC     NOT NULL DEFAULT 0,
    actual_hours   NUMERIC     NOT NULL DEFAULT 0,
    rejection_kg   NUMERIC     NOT NULL DEFAULT 0,
    source_tab     TEXT        NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT mp_actual_line_natural
        UNIQUE (segment, month, date, machine_norm, item_norm, source_tab)
);
CREATE INDEX IF NOT EXISTS mp_actual_line_month ON mp_actual_line (segment, month);
CREATE INDEX IF NOT EXISTS mp_actual_line_mc ON mp_actual_line (segment, month, machine_norm);

CREATE TABLE IF NOT EXISTS mp_machine_downtime (
    id          BIGSERIAL   PRIMARY KEY,
    segment     TEXT        NOT NULL,
    machine     TEXT        NOT NULL,
    kind        TEXT        NOT NULL,
    start_date  DATE        NOT NULL,
    end_date    DATE,
    reason      TEXT        NOT NULL DEFAULT '',
    resolved    BOOLEAN     NOT NULL DEFAULT false,
    resolved_at TIMESTAMPTZ,
    deleted     BOOLEAN     NOT NULL DEFAULT false,
    deleted_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mp_machine_downtime_lookup
    ON mp_machine_downtime (segment, machine, kind);
CREATE INDEX IF NOT EXISTS mp_machine_downtime_open
    ON mp_machine_downtime (segment, start_date)
    WHERE end_date IS NULL;

CREATE TABLE IF NOT EXISTS mp_wastage_summary (
    segment     TEXT        NOT NULL,
    type_key    TEXT        NOT NULL,
    prod_kg     NUMERIC(18,3) NOT NULL DEFAULT 0,
    wastage_kg  NUMERIC(18,3) NOT NULL DEFAULT 0,
    n_months    INT         NOT NULL DEFAULT 0,
    CONSTRAINT mp_wastage_summary_natural UNIQUE (segment, type_key)
);

CREATE TABLE IF NOT EXISTS mp_wastage_meta (
    segment          TEXT        NOT NULL PRIMARY KEY,
    n_months         INT         NOT NULL DEFAULT 0,
    last_recomputed  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_MIGRATIONS = """
ALTER TABLE mp_plan_run ADD COLUMN IF NOT EXISTS fitting_demand       JSONB;
ALTER TABLE mp_plan_run ADD COLUMN IF NOT EXISTS uploaded_file_path   TEXT NOT NULL DEFAULT '';
ALTER TABLE mp_machine  ADD COLUMN IF NOT EXISTS shifts_per_day       INT     NOT NULL DEFAULT 2;
ALTER TABLE mp_machine  ADD COLUMN IF NOT EXISTS hours_per_shift      NUMERIC NOT NULL DEFAULT 10;
ALTER TABLE mp_machine  ADD COLUMN IF NOT EXISTS working_days_month   INT     NOT NULL DEFAULT 25;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS min_run_block_hours  NUMERIC NOT NULL DEFAULT 5;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS night_changeover_allowed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS week_days            TEXT NOT NULL DEFAULT '[6,6,6,7]';
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS cpvc_mat_rate NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS upvc_mat_rate NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS swr_mat_rate  NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS agri_mat_rate NUMERIC NOT NULL DEFAULT 0;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS rag_amber_pct  NUMERIC NOT NULL DEFAULT 10;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS rag_red_pct    NUMERIC NOT NULL DEFAULT 25;
ALTER TABLE mp_params   ADD COLUMN IF NOT EXISTS hours_dev_pct  NUMERIC NOT NULL DEFAULT 15;
UPDATE mp_params SET waste_pct = 0 WHERE waste_pct = 4;
ALTER TABLE mp_machine_downtime ADD COLUMN IF NOT EXISTS resolved    BOOLEAN     NOT NULL DEFAULT false;
ALTER TABLE mp_machine_downtime ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS mp_machine_downtime_resolved ON mp_machine_downtime (segment, resolved) WHERE resolved = false;
ALTER TABLE mp_machine_downtime ADD COLUMN IF NOT EXISTS deleted     BOOLEAN     NOT NULL DEFAULT false;
ALTER TABLE mp_machine_downtime ADD COLUMN IF NOT EXISTS deleted_at  TIMESTAMPTZ;
"""

# Backfill: old records closed via close_downtime have end_date set but resolved=false.
# This UPDATE runs at init time and is idempotent.
_BACKFILL_DOWNTIME_RESOLVED = """
UPDATE mp_machine_downtime
   SET resolved=true, resolved_at=COALESCE(updated_at, now())
 WHERE end_date IS NOT NULL AND resolved=false
"""


def init_mp_tables() -> None:
    """Create all mp_ tables if they do not exist (idempotent, lazy)."""
    global _INITIALISED
    if _INITIALISED or not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(_DDL)
            for stmt in _MIGRATIONS.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            # Backfill: any record closed before resolved column existed
            cur.execute(_BACKFILL_DOWNTIME_RESOLVED)
        _INITIALISED = True
    except Exception as e:
        raise MpModelError(f"init_mp_tables failed: {e}") from e


# ---------------------------------------------------------------------------
# Upsert helpers — one per table
# ---------------------------------------------------------------------------

def upsert_machines(rows: List[MpMachine]) -> int:
    """Insert or update mp_machine rows. Returns count of rows processed."""
    if not rows:
        return 0
    init_mp_tables()
    sql = """
        INSERT INTO mp_machine
            (segment, machine, kind, operators_ot, support_w,
             capacity_hrs_month, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_machine_natural
        DO UPDATE SET
            kind               = EXCLUDED.kind,
            operators_ot       = EXCLUDED.operators_ot,
            support_w          = EXCLUDED.support_w,
            capacity_hrs_month = EXCLUDED.capacity_hrs_month,
            updated_at         = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(sql, [
                (r.segment, r.machine, r.kind, r.operators_ot, r.support_w,
                 r.capacity_hrs_month, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_machines: {e}") from e


def upsert_routing(rows: List[MpRouting]) -> int:
    if not rows:
        return 0
    init_mp_tables()
    sql = """
        INSERT INTO mp_routing
            (segment, item_code, machine, material, capable, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_routing_natural
        DO UPDATE SET
            material        = EXCLUDED.material,
            capable         = EXCLUDED.capable,
            updated_at      = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(sql, [
                (r.segment, r.item_code, r.machine, r.material, r.capable, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_routing: {e}") from e


def upsert_fitting_std(rows: List[MpFittingStd]) -> int:
    if not rows:
        return 0
    init_mp_tables()
    sql = """
        INSERT INTO mp_fitting_std
            (segment, item_code, machine, cavity, cycle_time_sec,
             effective_month, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_fitting_std_natural
        DO UPDATE SET
            cavity          = EXCLUDED.cavity,
            cycle_time_sec  = EXCLUDED.cycle_time_sec,
            updated_at      = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(sql, [
                (r.segment, r.item_code, r.machine, r.cavity,
                 r.cycle_time_sec, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_fitting_std: {e}") from e


def upsert_bom_weights(rows: List[MpBomWeight]) -> int:
    """Replace BOM weights for the segment/effective_month with the new set.

    Uses DELETE + INSERT so stale codes (e.g. pure-numeric ERP IDs that were
    seeded before the numeric-filter fix) are removed rather than left behind
    by the ON CONFLICT DO UPDATE strategy.
    """
    if not rows:
        return 0
    init_mp_tables()
    seg = rows[0].segment
    em = rows[0].effective_month
    insert_sql = """
        INSERT INTO mp_bom_weight
            (segment, item_code, weight_per_pc_kg, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_bom_weight_natural
        DO UPDATE SET
            weight_per_pc_kg = EXCLUDED.weight_per_pc_kg,
            updated_at       = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_bom_weight WHERE segment=%s AND effective_month=%s",
                (seg, em),
            )
            cur.executemany(insert_sql, [
                (r.segment, r.item_code, r.weight_per_pc_kg, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_bom_weights: {e}") from e


def upsert_per_hour(rows: List[MpPerHour]) -> int:
    """Replace per-hour rates for the segment/effective_month with the new set.

    Uses DELETE + INSERT so stale rows (e.g. numeric-OD codes from earlier
    seeds before the alpha-code filter was added) are removed cleanly.
    """
    if not rows:
        return 0
    init_mp_tables()
    seg = rows[0].segment
    em = rows[0].effective_month
    insert_sql = """
        INSERT INTO mp_per_hour
            (segment, item_code, basis, value, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_per_hour_natural
        DO UPDATE SET
            value      = EXCLUDED.value,
            updated_at = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_per_hour WHERE segment=%s AND effective_month=%s",
                (seg, em),
            )
            cur.executemany(insert_sql, [
                (r.segment, r.item_code, r.basis, r.value, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_per_hour: {e}") from e


def upsert_compound_recipe(rows: List[MpCompoundRecipe]) -> int:
    """Replace compound recipes for the segment/effective_month with the new set.

    Uses DELETE + INSERT so stale component rows from previous seeds (e.g.
    rows from the ACTUAL section that are no longer generated) are removed.
    """
    if not rows:
        return 0
    init_mp_tables()
    seg = rows[0].segment
    em = rows[0].effective_month
    insert_sql = """
        INSERT INTO mp_compound_recipe
            (segment, material, type, component, ratio_kg, price_per_kg,
             wastage_factor, needs_recipe, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_compound_recipe_natural
        DO UPDATE SET
            ratio_kg       = EXCLUDED.ratio_kg,
            price_per_kg   = EXCLUDED.price_per_kg,
            wastage_factor = EXCLUDED.wastage_factor,
            needs_recipe   = EXCLUDED.needs_recipe,
            updated_at     = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_compound_recipe WHERE segment=%s AND effective_month=%s",
                (seg, em),
            )
            cur.executemany(insert_sql, [
                (r.segment, r.material, r.type, r.component, r.ratio_kg,
                 r.price_per_kg, r.wastage_factor, r.needs_recipe, r.effective_month)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_compound_recipe: {e}") from e


def clean_extrusion_routing(segment: str, effective_month: str) -> None:
    """DELETE extrusion machines and M/C-%% routing rows before a fresh pipe re-seed."""
    if not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_machine "
                "WHERE segment=%s AND effective_month=%s AND kind='extrusion'",
                (segment, effective_month),
            )
            cur.execute(
                "DELETE FROM mp_routing "
                "WHERE segment=%s AND effective_month=%s AND machine LIKE 'M/C-%%'",
                (segment, effective_month),
            )
    except Exception as e:
        raise MpModelError(f"clean_extrusion_routing: {e}") from e


def clean_moulding_routing(segment: str, effective_month: str) -> None:
    """DELETE moulding machines, non-M/C routing rows, and fitting_std rows."""
    if not AVAILABLE:
        return
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_machine "
                "WHERE segment=%s AND effective_month=%s AND kind='moulding'",
                (segment, effective_month),
            )
            cur.execute(
                "DELETE FROM mp_routing "
                "WHERE segment=%s AND effective_month=%s AND machine NOT LIKE 'M/C-%%'",
                (segment, effective_month),
            )
            cur.execute(
                "DELETE FROM mp_fitting_std "
                "WHERE segment=%s AND effective_month=%s",
                (segment, effective_month),
            )
    except Exception as e:
        raise MpModelError(f"clean_moulding_routing: {e}") from e


def upsert_params(row: MpParams) -> int:
    init_mp_tables()
    sql = """
        INSERT INTO mp_params
            (segment, waste_pct, pulverizer_pct, effective_month,
             min_run_block_hours, night_changeover_allowed, week_days,
             cpvc_mat_rate, upvc_mat_rate, swr_mat_rate, agri_mat_rate,
             rag_amber_pct, rag_red_pct, hours_dev_pct,
             updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_params_natural
        DO UPDATE SET
            waste_pct               = EXCLUDED.waste_pct,
            pulverizer_pct          = EXCLUDED.pulverizer_pct,
            min_run_block_hours     = EXCLUDED.min_run_block_hours,
            night_changeover_allowed= EXCLUDED.night_changeover_allowed,
            week_days               = EXCLUDED.week_days,
            cpvc_mat_rate           = EXCLUDED.cpvc_mat_rate,
            upvc_mat_rate           = EXCLUDED.upvc_mat_rate,
            swr_mat_rate            = EXCLUDED.swr_mat_rate,
            agri_mat_rate           = EXCLUDED.agri_mat_rate,
            rag_amber_pct           = EXCLUDED.rag_amber_pct,
            rag_red_pct             = EXCLUDED.rag_red_pct,
            hours_dev_pct           = EXCLUDED.hours_dev_pct,
            updated_at              = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                row.segment, row.waste_pct, row.pulverizer_pct, row.effective_month,
                row.min_run_block_hours, row.night_changeover_allowed, row.week_days,
                row.cpvc_mat_rate, row.upvc_mat_rate, row.swr_mat_rate, row.agri_mat_rate,
                row.rag_amber_pct, row.rag_red_pct, row.hours_dev_pct,
            ))
        return 1
    except Exception as e:
        raise MpModelError(f"upsert_params: {e}") from e


def insert_plan_run(row: MpPlanRun) -> int:
    """Insert a new plan run (no upsert — each submission is a distinct record)."""
    init_mp_tables()
    sql = """
        INSERT INTO mp_plan_run
            (segment, month, uploaded_demand, fitting_demand,
             frozen_inputs, results, status, uploaded_file_path)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                row.segment, row.month,
                json.dumps(row.uploaded_demand) if row.uploaded_demand is not None else None,
                json.dumps(row.fitting_demand) if row.fitting_demand is not None else None,
                json.dumps(row.frozen_inputs) if row.frozen_inputs is not None else None,
                json.dumps(row.results) if row.results is not None else None,
                row.status,
                row.uploaded_file_path or "",
            ))
            new_id = cur.fetchone()[0]
        return new_id
    except Exception as e:
        raise MpModelError(f"insert_plan_run: {e}") from e


def update_plan_run_file_path(run_id: int, file_path: str) -> None:
    """Set uploaded_file_path after file has been saved to disk."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mp_plan_run SET uploaded_file_path=%s WHERE id=%s",
                (file_path, run_id),
            )
    except Exception as e:
        raise MpModelError(f"update_plan_run_file_path: {e}") from e


def update_plan_run_freeze(run_id: int, frozen_inputs: Any, results: Any) -> None:
    """Write frozen_inputs + results snapshot; set status='draft'."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE mp_plan_run
                   SET frozen_inputs=%s, results=%s, status='draft'
                   WHERE id=%s AND status != 'finalized'""",
                (
                    json.dumps(frozen_inputs),
                    json.dumps(results),
                    run_id,
                ),
            )
    except Exception as e:
        raise MpModelError(f"update_plan_run_freeze: {e}") from e


def finalize_plan_run(run_id: int) -> None:
    """Lock a plan run (status → 'finalized', immutable thereafter)."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE mp_plan_run SET status='finalized' WHERE id=%s AND status='draft'",
                (run_id,),
            )
    except Exception as e:
        raise MpModelError(f"finalize_plan_run: {e}") from e


def list_plan_runs(segment: str, limit: int = 30) -> list:
    """Return most-recent plan runs for the segment, newest first."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT id, segment, month, status, uploaded_file_path, created_at
                   FROM mp_plan_run
                   WHERE segment=%s
                   ORDER BY created_at DESC
                   LIMIT %s""",
                (segment, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_plan_run_by_id(run_id: int) -> Optional[dict]:
    """Return a single plan run as a dict (or None if not found)."""
    if not AVAILABLE:
        return None
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT id, segment, month, uploaded_demand, fitting_demand,
                          frozen_inputs, results, status, uploaded_file_path, created_at
                   FROM mp_plan_run WHERE id=%s""",
                (run_id,),
            )
            row = cur.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_bom_weights(segment: str, effective_month: str) -> Dict[str, float]:
    """Return {item_code: weight_per_pc_kg} for the given month."""
    if not AVAILABLE:
        return {}
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                "SELECT item_code, weight_per_pc_kg FROM mp_bom_weight "
                "WHERE segment=%s AND effective_month=%s",
                (segment, effective_month),
            )
            return {r["item_code"]: float(r["weight_per_pc_kg"]) for r in cur.fetchall()}
    except Exception:
        return {}


def get_params(segment: str, effective_month: str) -> Optional[MpParams]:
    """Return the mp_params row for the given (segment, month), or None."""
    if not AVAILABLE:
        return None
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                "SELECT * FROM mp_params WHERE segment=%s AND effective_month=%s LIMIT 1",
                (segment, effective_month),
            )
            row = cur.fetchone()
        if not row:
            return None
        return MpParams(
            segment=row["segment"],
            waste_pct=float(row["waste_pct"]),
            pulverizer_pct=float(row["pulverizer_pct"]),
            effective_month=row["effective_month"],
            min_run_block_hours=float(row["min_run_block_hours"]) if row.get("min_run_block_hours") is not None else 2.0,
            night_changeover_allowed=bool(row.get("night_changeover_allowed", False)),
            week_days=str(row.get("week_days") or "[6,6,6,7]"),
            cpvc_mat_rate=float(row["cpvc_mat_rate"]) if row.get("cpvc_mat_rate") is not None else 0.0,
            upvc_mat_rate=float(row["upvc_mat_rate"]) if row.get("upvc_mat_rate") is not None else 0.0,
            swr_mat_rate=float(row["swr_mat_rate"])  if row.get("swr_mat_rate")  is not None else 0.0,
            agri_mat_rate=float(row["agri_mat_rate"]) if row.get("agri_mat_rate") is not None else 0.0,
            rag_amber_pct=float(row["rag_amber_pct"]) if row.get("rag_amber_pct") is not None else 10.0,
            rag_red_pct=float(row["rag_red_pct"])   if row.get("rag_red_pct")   is not None else 25.0,
            hours_dev_pct=float(row["hours_dev_pct"]) if row.get("hours_dev_pct") is not None else 15.0,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Read queries — MP-1 data page
# ---------------------------------------------------------------------------

def get_machines(segment: str, effective_month: str, kind: Optional[str] = None) -> List[dict]:
    """Return machine rows as plain dicts, optionally filtered by kind."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if kind:
                cur.execute(
                    "SELECT * FROM mp_machine WHERE segment=%s AND effective_month=%s "
                    "AND kind=%s ORDER BY machine",
                    (segment, effective_month, kind),
                )
            else:
                cur.execute(
                    "SELECT * FROM mp_machine WHERE segment=%s AND effective_month=%s "
                    "ORDER BY kind, machine",
                    (segment, effective_month),
                )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_routing(segment: str, effective_month: str) -> List[dict]:
    """Return all routing rows as plain dicts."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM mp_routing WHERE segment=%s AND effective_month=%s "
                "ORDER BY item_code, machine",
                (segment, effective_month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_fitting_std(segment: str, effective_month: str) -> List[dict]:
    """Return all fitting_std rows as plain dicts."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM mp_fitting_std WHERE segment=%s AND effective_month=%s "
                "ORDER BY item_code, machine",
                (segment, effective_month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_per_hour(segment: str, effective_month: str) -> List[dict]:
    """Return all per-hour rows as plain dicts."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM mp_per_hour WHERE segment=%s AND effective_month=%s "
                "ORDER BY basis, item_code",
                (segment, effective_month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_bom_weight_rows(segment: str, effective_month: str) -> List[dict]:
    """Return all BOM weight rows as plain dicts (sorted by item_code)."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT item_code, weight_per_pc_kg FROM mp_bom_weight "
                "WHERE segment=%s AND effective_month=%s ORDER BY item_code",
                (segment, effective_month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_compound_recipes(segment: str, effective_month: str) -> List[dict]:
    """Return all compound recipe rows as plain dicts."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM mp_compound_recipe WHERE segment=%s AND effective_month=%s "
                "ORDER BY material, type, component",
                (segment, effective_month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def get_available_months(segment: str) -> List[str]:
    """Return distinct effective_month values that have any mp_ data for this segment."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT effective_month FROM mp_params WHERE segment=%s "
                "ORDER BY effective_month DESC",
                (segment,),
            )
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Targeted single-row upserts — MP-1 edit endpoints
# ---------------------------------------------------------------------------

def upsert_single_bom(row: MpBomWeight) -> int:
    """Upsert one BOM weight row (ON CONFLICT UPDATE)."""
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured.")
    init_mp_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mp_bom_weight (segment, item_code, weight_per_pc_kg, effective_month, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (segment, item_code, effective_month)
            DO UPDATE SET weight_per_pc_kg = EXCLUDED.weight_per_pc_kg, updated_at = now()
            """,
            (row.segment, row.item_code, row.weight_per_pc_kg, row.effective_month),
        )
        conn.commit()
    return 1


def upsert_single_per_hour(row: MpPerHour) -> int:
    """Upsert one per-hour row (ON CONFLICT UPDATE)."""
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured.")
    init_mp_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mp_per_hour (segment, item_code, basis, value, effective_month, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (segment, item_code, basis, effective_month)
            DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            (row.segment, row.item_code, row.basis, row.value, row.effective_month),
        )
        conn.commit()
    return 1


def upsert_single_fitting_std(row: MpFittingStd) -> int:
    """Upsert one fitting_std row (ON CONFLICT UPDATE cavity + cycle_time_sec)."""
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured.")
    init_mp_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mp_fitting_std
                (segment, item_code, machine, cavity, cycle_time_sec, effective_month, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (segment, item_code, machine, effective_month)
            DO UPDATE SET cavity = EXCLUDED.cavity,
                          cycle_time_sec = EXCLUDED.cycle_time_sec,
                          updated_at = now()
            """,
            (row.segment, row.item_code, row.machine,
             row.cavity, row.cycle_time_sec, row.effective_month),
        )
        conn.commit()
    return 1


def upsert_routing_for_item(
    segment: str, item_code: str, effective_month: str, machines: List[str]
) -> int:
    """Replace routing rows for one item (DELETE current + INSERT capable machines)."""
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured.")
    init_mp_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM mp_routing WHERE segment=%s AND item_code=%s AND effective_month=%s",
            (segment, item_code, effective_month),
        )
        for mc in machines:
            cur.execute(
                """
                INSERT INTO mp_routing
                    (segment, item_code, machine, capable, effective_month, updated_at)
                VALUES (%s, %s, %s, TRUE, %s, now())
                ON CONFLICT (segment, item_code, machine, effective_month) DO NOTHING
                """,
                (segment, item_code, mc, effective_month),
            )
        conn.commit()
    return len(machines)


def upsert_compound_wastage(
    segment: str, material: str, type_: str, wastage_factor: float, effective_month: str
) -> int:
    """Update wastage_factor for all component rows of a (material, type) card."""
    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured.")
    init_mp_tables()
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mp_compound_recipe
               SET wastage_factor = %s, updated_at = now()
             WHERE segment=%s AND material=%s AND type=%s AND effective_month=%s
            """,
            (wastage_factor, segment, material, type_, effective_month),
        )
        count = cur.rowcount
        conn.commit()
    return count


# ---------------------------------------------------------------------------
# mp_plan_line helpers
# ---------------------------------------------------------------------------

def insert_plan_lines(rows: List[MpPlanLine]) -> int:
    """Bulk-insert plan lines for a run (DELETE existing first for idempotency)."""
    if not rows or not AVAILABLE:
        return 0
    init_mp_tables()
    run_id = rows[0].plan_run_id
    sql = """
        INSERT INTO mp_plan_line
            (plan_run_id, segment, month, week, day, shift,
             machine, machine_norm, item_code, item_norm, material,
             planned_pcs, planned_kg, planned_hours, net_hours,
             rate_used, rate_estimated, is_excess, is_idle)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM mp_plan_line WHERE plan_run_id=%s", (run_id,))
            cur.executemany(sql, [
                (r.plan_run_id, r.segment, r.month, r.week, r.day, r.shift,
                 r.machine, r.machine_norm, r.item_code, r.item_norm, r.material,
                 r.planned_pcs, r.planned_kg, r.planned_hours, r.net_hours,
                 r.rate_used, r.rate_estimated, r.is_excess, r.is_idle)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"insert_plan_lines: {e}") from e


def get_plan_lines(plan_run_id: int) -> List[dict]:
    """Return all plan lines for a run, ordered by day/machine/shift."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT * FROM mp_plan_line
                   WHERE plan_run_id=%s
                   ORDER BY day, machine, shift""",
                (plan_run_id,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# mp_actual_line helpers
# ---------------------------------------------------------------------------

def upsert_actual_lines(rows: List[MpActualLine]) -> int:
    """Upsert actual production lines (ON CONFLICT DO UPDATE)."""
    if not rows or not AVAILABLE:
        return 0
    init_mp_tables()
    sql = """
        INSERT INTO mp_actual_line
            (segment, month, date, machine, machine_norm,
             item_code, item_norm, material,
             actual_pcs, actual_kg, actual_hours, rejection_kg, source_tab)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT ON CONSTRAINT mp_actual_line_natural
        DO UPDATE SET
            actual_pcs   = EXCLUDED.actual_pcs,
            actual_kg    = EXCLUDED.actual_kg,
            actual_hours = EXCLUDED.actual_hours,
            rejection_kg = EXCLUDED.rejection_kg,
            created_at   = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.executemany(sql, [
                (r.segment, r.month, r.date, r.machine, r.machine_norm,
                 r.item_code, r.item_norm, r.material,
                 r.actual_pcs, r.actual_kg, r.actual_hours,
                 r.rejection_kg, r.source_tab)
                for r in rows
            ])
        return len(rows)
    except Exception as e:
        raise MpModelError(f"upsert_actual_lines: {e}") from e


def get_actual_lines(segment: str, month: str) -> List[dict]:
    """Return all actual lines for (segment, month), ordered by date/machine."""
    if not AVAILABLE:
        return []
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT * FROM mp_actual_line
                   WHERE segment=%s AND month=%s
                   ORDER BY date, machine, item_code""",
                (segment, month),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def delete_actual_lines(segment: str, month: str) -> int:
    """Delete all actual lines for (segment, month) — used before full re-ingest."""
    if not AVAILABLE:
        return 0
    try:
        init_mp_tables()
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM mp_actual_line WHERE segment=%s AND month=%s",
                (segment, month),
            )
            return cur.rowcount
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Downtime CRUD helpers
# ---------------------------------------------------------------------------

class DowntimeValidationError(Exception):
    """Raised on invalid downtime data before any DB write."""


def insert_downtime(record: MpMachineDowntime) -> int:
    """Insert a new downtime record.  Returns the new id.

    Validates:
      - kind in ('breakdown', 'maintenance')
      - end_date >= start_date when provided
      - no overlapping OPEN record for the same (segment, machine, kind)
    """
    if record.kind not in ("breakdown", "maintenance"):
        raise DowntimeValidationError(f"kind must be 'breakdown' or 'maintenance', got {record.kind!r}")
    if record.end_date is not None and record.end_date < record.start_date:
        raise DowntimeValidationError("end_date must be >= start_date")

    if not AVAILABLE:
        raise MpModelError("DATABASE_URL not configured")
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            # Check for overlapping open (unresolved) record on same (segment, machine, kind)
            cur.execute(
                """SELECT id FROM mp_machine_downtime
                   WHERE segment=%s AND machine=%s AND kind=%s AND resolved=false""",
                (record.segment, record.machine, record.kind),
            )
            existing = cur.fetchone()
            if existing:
                raise DowntimeValidationError(
                    f"Machine '{record.machine}' already has an open {record.kind} record (id={existing[0]}). "
                    "Close it before opening another of the same kind."
                )
            cur.execute(
                """INSERT INTO mp_machine_downtime
                       (segment, machine, kind, start_date, end_date, reason,
                        resolved, resolved_at, deleted, deleted_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id""",
                (record.segment, record.machine, record.kind,
                 record.start_date, record.end_date, record.reason or "",
                 record.resolved, record.resolved_at,
                 record.deleted, record.deleted_at),
            )
            return cur.fetchone()[0]
    except DowntimeValidationError:
        raise
    except Exception as e:
        raise MpModelError(f"insert_downtime: {e}") from e


def resolve_downtime(record_id: int, end_date: datetime.date) -> bool:
    """Mark a downtime record as resolved (machine back in planning).

    Sets resolved=true, resolved_at=now(), end_date=end_date.
    Records are NEVER deleted — this is the only "close" path.
    Returns True if a row was updated.
    """
    if not AVAILABLE:
        return False
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT start_date FROM mp_machine_downtime WHERE id=%s", (record_id,))
            row = cur.fetchone()
            if not row:
                return False
            if end_date < row[0]:
                raise DowntimeValidationError("end_date must be >= start_date")
            cur.execute(
                """UPDATE mp_machine_downtime
                   SET resolved=true, resolved_at=now(), end_date=%s, updated_at=now()
                   WHERE id=%s""",
                (end_date, record_id),
            )
            return cur.rowcount > 0
    except DowntimeValidationError:
        raise
    except Exception as e:
        raise MpModelError(f"resolve_downtime: {e}") from e


def unresolve_downtime(record_id: int) -> bool:
    """Re-open a resolved downtime record (un-tick 'back in planning').

    Clears resolved, resolved_at, and end_date so the machine is
    unavailable from start_date onward again.  Returns True if updated.
    """
    if not AVAILABLE:
        return False
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE mp_machine_downtime
                   SET resolved=false, resolved_at=NULL, end_date=NULL, updated_at=now()
                   WHERE id=%s""",
                (record_id,),
            )
            return cur.rowcount > 0
    except Exception as e:
        raise MpModelError(f"unresolve_downtime: {e}") from e


def close_downtime(record_id: int, end_date: datetime.date) -> bool:
    """Backward-compat alias for resolve_downtime."""
    return resolve_downtime(record_id, end_date)


def delete_downtime(record_id: int) -> bool:
    """Soft-delete a downtime record (sets deleted=true, deleted_at=now).

    The row is preserved in the DB for audit but excluded from all calculations:
    machine availability, capacity math, follow-up suppression, and UI panels.
    Returns True if a row was updated; False if not found or already deleted.
    Use restore_downtime() to undo.
    """
    if not AVAILABLE:
        return False
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE mp_machine_downtime
                   SET deleted=true, deleted_at=now(), updated_at=now()
                   WHERE id=%s AND NOT deleted""",
                (record_id,),
            )
            return cur.rowcount > 0
    except Exception as e:
        raise MpModelError(f"delete_downtime: {e}") from e


def restore_downtime(record_id: int) -> bool:
    """Undo a soft delete — re-activates the downtime record in all calculations.

    Returns True if a row was updated; False if not found or not currently deleted.
    """
    if not AVAILABLE:
        return False
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE mp_machine_downtime
                   SET deleted=false, deleted_at=NULL, updated_at=now()
                   WHERE id=%s AND deleted""",
                (record_id,),
            )
            return cur.rowcount > 0
    except Exception as e:
        raise MpModelError(f"restore_downtime: {e}") from e


def count_downtime_records(segment: str) -> int:
    """Return the total number of downtime records for the segment (for tests)."""
    if not AVAILABLE:
        return 0
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM mp_machine_downtime WHERE segment=%s", (segment,)
            )
            return cur.fetchone()[0]
    except Exception:
        return 0


def get_downtime_records(
    segment: str,
    machine: Optional[str] = None,
    kind: Optional[str] = None,
    only_open: bool = False,
    include_deleted: bool = False,
) -> List[dict]:
    """Return downtime records for the segment (permanently retained).

    By default excludes soft-deleted records.  Pass include_deleted=True to
    retrieve everything (e.g. for the "Show deleted" history toggle).
    Open records (resolved=false) are pinned first, then sorted by start_date DESC.
    Each returned dict also carries a computed 'days_down' int (inclusive).
    """
    if not AVAILABLE:
        return []
    init_mp_tables()
    try:
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            clauses = ["segment=%s"]
            params: List[Any] = [segment]
            if machine:
                clauses.append("machine=%s"); params.append(machine)
            if kind:
                clauses.append("kind=%s"); params.append(kind)
            if only_open:
                clauses.append("resolved=false")
            if not include_deleted:
                clauses.append("NOT deleted")
            cur.execute(
                f"SELECT * FROM mp_machine_downtime WHERE {' AND '.join(clauses)} "
                f"ORDER BY resolved ASC, start_date DESC, id DESC",
                params,
            )
            today = datetime.date.today()
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                sd = d.get("start_date")
                ed = d.get("end_date")
                if sd:
                    ref = ed if ed else today
                    d["days_down"] = max(1, (ref - sd).days + 1)
                else:
                    d["days_down"] = 0
                rows.append(d)
            return rows
    except Exception:
        return []


def get_downtime_affecting_month(segment: str, month: str) -> List[dict]:
    """Return records that overlap with the given month (YYYY-MM).

    A record overlaps if: start_date <= month_last_day AND (end_date IS NULL OR end_date >= month_first_day).
    """
    if not AVAILABLE:
        return []
    init_mp_tables()
    try:
        import calendar as _cal
        year, mnum = int(month[:4]), int(month[5:7])
        first = datetime.date(year, mnum, 1)
        last  = datetime.date(year, mnum, _cal.monthrange(year, mnum)[1])
        with _conn() as conn, conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT * FROM mp_machine_downtime
                   WHERE segment=%s
                     AND NOT deleted
                     AND start_date <= %s
                     AND (end_date IS NULL OR end_date >= %s)
                   ORDER BY start_date""",
                (segment, last, first),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def machine_down_on_date(
    machine: str,
    date: datetime.date,
    downtime_records: List[dict],
) -> bool:
    """Pure helper: return True if *machine* is down on *date* per the records list.

    Soft-deleted records (deleted=True) are always skipped.
    """
    for rec in downtime_records:
        if rec.get("deleted", False):
            continue
        if rec.get("machine") != machine:
            continue
        sd = rec.get("start_date")
        ed = rec.get("end_date")
        if isinstance(sd, str):
            try:
                sd = datetime.date.fromisoformat(str(sd))
            except Exception:
                continue
        if isinstance(ed, str) and ed:
            try:
                ed = datetime.date.fromisoformat(str(ed))
            except Exception:
                ed = None
        if sd is None:
            continue
        if date >= sd and (ed is None or date <= ed):
            return True
    return False
