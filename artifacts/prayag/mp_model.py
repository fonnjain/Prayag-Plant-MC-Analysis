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
    waste_pct: float = 4.0
    pulverizer_pct: float = 25.0
    effective_month: str = ""


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
    waste_pct       NUMERIC     NOT NULL DEFAULT 4,
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
"""

_MIGRATIONS = """
ALTER TABLE mp_plan_run ADD COLUMN IF NOT EXISTS fitting_demand       JSONB;
ALTER TABLE mp_plan_run ADD COLUMN IF NOT EXISTS uploaded_file_path   TEXT NOT NULL DEFAULT '';
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
            (segment, waste_pct, pulverizer_pct, effective_month, updated_at)
        VALUES (%s,%s,%s,%s,now())
        ON CONFLICT ON CONSTRAINT mp_params_natural
        DO UPDATE SET
            waste_pct      = EXCLUDED.waste_pct,
            pulverizer_pct = EXCLUDED.pulverizer_pct,
            updated_at     = now()
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                row.segment, row.waste_pct, row.pulverizer_pct, row.effective_month
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
