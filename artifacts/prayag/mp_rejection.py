"""mp_rejection.py — Historical rejection % for Plumbing (Pipe + Fitting).

Reads raw Report-11 (pipe extrusion, by material type) and Report-12
(moulding / fitting) from every monthly PIPE workbook in DAILY_SOURCES.
Results are aggregated per (plant_type, material) and cached in Postgres.

INVARIANTS:
- EMPTY_SOURCES workbooks are skipped entirely.
- A month where R11 yields no rows is counted as a *fitting-only* month for
  that workbook (Sep-2025 pattern).
- The current partial month is included as-is.
- NO fabrication: a figure that cannot be computed is absent, never zero.
"""
from __future__ import annotations

import datetime
import json
import logging
from typing import Dict, List, Optional, Tuple

import parsers as _parsers
import pipe_reconcile
import sources
import store

logger = logging.getLogger(__name__)

# ── Schema ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS mp_rejection_summary (
    id          BIGSERIAL PRIMARY KEY,
    segment     TEXT NOT NULL,
    plant_type  TEXT NOT NULL,
    material    TEXT NOT NULL DEFAULT '',
    prod_kg     NUMERIC(18,3) NOT NULL DEFAULT 0,
    rej_kg      NUMERIC(18,3) NOT NULL DEFAULT 0,
    n_months    INT NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(segment, plant_type, material)
);
CREATE TABLE IF NOT EXISTS mp_rejection_item (
    id          BIGSERIAL PRIMARY KEY,
    segment     TEXT NOT NULL,
    plant_type  TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    material    TEXT NOT NULL DEFAULT '',
    prod_kg     NUMERIC(18,3) NOT NULL DEFAULT 0,
    rej_kg      NUMERIC(18,3) NOT NULL DEFAULT 0,
    n_months    INT NOT NULL DEFAULT 0,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(segment, plant_type, item_key)
);
CREATE TABLE IF NOT EXISTS mp_rejection_meta (
    segment         TEXT PRIMARY KEY,
    pipe_months     INT NOT NULL DEFAULT 0,
    fitting_months  INT NOT NULL DEFAULT 0,
    months_list     TEXT NOT NULL DEFAULT '[]',
    last_recomputed TIMESTAMPTZ
);
"""


def init_rejection_tables() -> None:
    if not store.AVAILABLE:
        return
    try:
        with store._conn() as conn, conn.cursor() as cur:
                cur.execute(_DDL)
    except Exception:
        logger.exception("mp_rejection: DDL failed")


# ── Raw parsers ──────────────────────────────────────────────────────────────

def _parse_r11_by_type(values: list, year_month: str) -> Dict[str, Dict[str, float]]:
    """Parse Report-11 rows → ``{material_type: {"out": kg, "rej": kg}}``.

    Sweeps every data row WITHOUT aggregating by machine or date so both
    output and rejection are split by type (CPVC / UPVC / SWR / AGRI …).
    Rows where the type cell is blank or contains "TOTAL" are skipped.
    Returns ``{}`` if the Report-11 header cannot be located.
    """
    if not values:
        return {}

    _H_DATE  = ("eq", "DATE")
    _H_MC    = ("eq", "MACHINE NO.")
    _H_TYPE  = ("eq", "TYPES")
    _H_OUT   = ("eq", "WEIGHT")
    _H_REJ   = ("eq", "ACTUAL WT (KG)")

    hdr = -1
    for i, row in enumerate(values[:15]):
        if any(_parsers._match_header(c, _H_DATE) for c in row):
            hdr = i
            break
    if hdr < 0:
        return {}

    band = values[hdr]

    def find(spec: tuple) -> int:
        for c, val in enumerate(band):
            if _parsers._match_header(val, spec):
                return c
        return -1

    date_c = find(_H_DATE)
    mc_c   = find(_H_MC)
    type_c = find(_H_TYPE)
    out_c  = find(_H_OUT)
    rej_c  = find(_H_REJ)

    if date_c < 0 or out_c < 0 or type_c < 0:
        return {}

    result: Dict[str, Dict[str, float]] = {}
    for row in values[hdr + 1:]:
        if not row:
            continue
        day = _parsers._long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        mc_label = str(row[mc_c]).strip() if 0 <= mc_c < len(row) else ""
        if not mc_label or "TOTAL" in mc_label.upper():
            continue
        out = _parsers.num(row[out_c]) if 0 <= out_c < len(row) else 0.0
        rej = _parsers.num(row[rej_c]) if 0 <= rej_c < len(row) else 0.0
        typ = str(row[type_c]).strip().upper() if 0 <= type_c < len(row) else ""
        if not typ or "TOTAL" in typ:
            continue
        if out <= 0 and rej <= 0:
            continue
        if typ not in result:
            result[typ] = {"out": 0.0, "rej": 0.0}
        result[typ]["out"] += out
        result[typ]["rej"] += rej

    return result


def _parse_r12_stats(
    values: list, year_month: str
) -> Tuple[float, float, Dict[str, Dict[str, float]]]:
    """Parse Report-12 → ``(total_out, total_rej, {material: {out, rej}})``.

    Handles BOTH FY layouts by locating columns via header text — never fixed
    column indices.  Layout differences:

    FY2025-26  header row 4, data row 5
        C = "Item"  ·  L = "Weight of Total Production"  ·  Y = "Actual Rejection Weight…"
    FY2026-27  header row 4, sub-headers row 5, data row 6
        C = "Item Code"  ·  D = "SAP Code"  ·  M = "Weight of Total Production"
        Z = "Actual Rejection Weight…"   (J = "Wt in Kgs" is NOT used)

    Production column: header must contain "WEIGHT OF TOTAL PRODUCTION".
        – Do NOT match "WEIGHT PER PC", "RUNNER" or "WT IN KGS".
    Rejection column:  header must contain "ACTUAL REJECTION".
        – Do NOT match "IDEAL REJECTION WEIGHT" — the "ACTUAL" requirement
          already excludes it.
    Item column: header exactly "ITEM CODE" (preferred) or exactly "ITEM".
        – Do NOT match "SAP CODE".

    The sub-header row in FY2026-27 has no valid date in its date cell, so
    ``_long_date_day`` returns None and it is skipped automatically.
    """
    if not values:
        return 0.0, 0.0, {}

    _H_DATE      = ("eq",       "DATE")
    _H_ITEM_CODE = ("eq",       "ITEM CODE")
    _H_ITEM      = ("eq",       "ITEM")
    _H_PROD      = ("contains", "WEIGHT OF TOTAL PRODUCTION")
    # "ACTUAL REJECTION" matches "Actual Rejection Weight (in Kgs)"
    # but NOT "Ideal Rejection Weight (in Kgs)" — the ACTUAL prefix is the guard.
    _H_REJ       = ("contains", "ACTUAL REJECTION")
    _H_MAT       = ("contains", "MATERIAL")

    # ── Locate header row: must contain DATE + (ITEM CODE or ITEM) ────────
    hdr = -1
    for i, row in enumerate(values[:12]):
        has_date = any(_parsers._match_header(c, _H_DATE) for c in row)
        has_item = any(
            _parsers._match_header(c, _H_ITEM_CODE) or _parsers._match_header(c, _H_ITEM)
            for c in row
        )
        if has_date and has_item:
            hdr = i
            break
    if hdr < 0:
        return 0.0, 0.0, {}

    band = values[hdr]

    def _find(spec: tuple) -> int:
        for c, val in enumerate(band):
            if _parsers._match_header(val, spec):
                return c
        return -1

    date_c = _find(_H_DATE)
    prod_c = _find(_H_PROD)
    rej_c  = _find(_H_REJ)
    mat_c  = _find(_H_MAT)

    # Item column: "ITEM CODE" preferred (FY2026-27); fall back to "ITEM" (FY2025-26)
    item_c = _find(_H_ITEM_CODE)
    if item_c < 0:
        item_c = _find(_H_ITEM)

    if date_c < 0 or prod_c < 0:
        return 0.0, 0.0, {}

    total_out    = 0.0
    total_rej    = 0.0
    by_material: Dict[str, Dict[str, float]] = {}

    for row in values[hdr + 1:]:
        if not row:
            continue
        # Date must parse — skips sub-header rows (e.g. the FY2026-27 row 5
        # whose date cell contains a group label, not a date).
        day = _parsers._long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        # Skip blank or total item rows
        item   = str(row[item_c]).strip() if 0 <= item_c < len(row) else ""
        item_u = item.upper()
        if not item or "TOTAL" in item_u or "GRAND" in item_u:
            continue

        out = _parsers.num(row[prod_c]) if 0 <= prod_c < len(row) else 0.0
        rej = _parsers.num(row[rej_c])  if 0 <= rej_c  < len(row) else 0.0
        if out <= 0 and rej <= 0:
            continue

        total_out += out
        total_rej += rej

        if mat_c >= 0:
            mat = str(row[mat_c]).strip().upper() if mat_c < len(row) else ""
            if mat and "TOTAL" not in mat:
                if mat not in by_material:
                    by_material[mat] = {"out": 0.0, "rej": 0.0}
                by_material[mat]["out"] += out
                by_material[mat]["rej"] += rej

    return total_out, total_rej, by_material


# ── Recompute ────────────────────────────────────────────────────────────────

def recompute_rejection(segment: str = "PLUMBING") -> dict:
    """Read every PIPE/MOULDING workbook, aggregate rejection stats, store in DB.

    Returns a summary dict ``{"ok": bool, "pipe_months": int,
    "fitting_months": int, "errors": [str]}``.
    """
    import sheets as _sheets  # local import avoids circular at module level

    errors: List[str] = []

    pipe_months_covered: List[str] = []
    fitting_months_covered: List[str] = []

    pipe_agg:    Dict[str, Dict[str, float]] = {}
    fitting_agg: Dict[str, Dict[str, float]] = {}

    daily_files = sources.DAILY_SOURCES.get("PIPE", {}).get("files", {})
    for ym in sorted(daily_files):
        if ("PIPE", ym) in sources.EMPTY_SOURCES:
            continue
        file_id = daily_files[ym]

        r11_vals = _sheets.get_raw_values(file_id, "Report-11")
        r11_stats = _parse_r11_by_type(r11_vals, ym)
        if r11_stats:
            pipe_months_covered.append(ym)
            for typ, td in r11_stats.items():
                if typ not in pipe_agg:
                    pipe_agg[typ] = {"out": 0.0, "rej": 0.0}
                pipe_agg[typ]["out"] += td["out"]
                pipe_agg[typ]["rej"] += td["rej"]

        r12_vals = _sheets.get_raw_values(file_id, "Report-12")
        r12_out, r12_rej, r12_by_mat = _parse_r12_stats(r12_vals, ym)
        if r12_out > 0 or r12_rej > 0:
            fitting_months_covered.append(ym)
            if r12_by_mat:
                for mat, md in r12_by_mat.items():
                    if mat not in fitting_agg:
                        fitting_agg[mat] = {"out": 0.0, "rej": 0.0}
                    fitting_agg[mat]["out"] += md["out"]
                    fitting_agg[mat]["rej"] += md["rej"]
            else:
                key = ""
                if key not in fitting_agg:
                    fitting_agg[key] = {"out": 0.0, "rej": 0.0}
                fitting_agg[key]["out"] += r12_out
                fitting_agg[key]["rej"] += r12_rej

    if not store.AVAILABLE:
        return {"ok": False, "error": "DB unavailable",
                "pipe_months": len(pipe_months_covered),
                "fitting_months": len(fitting_months_covered),
                "errors": errors}

    all_months = sorted(set(pipe_months_covered) | set(fitting_months_covered))

    try:
        with store._conn() as conn, conn.cursor() as cur:
                init_rejection_tables()

                cur.execute("DELETE FROM mp_rejection_summary WHERE segment=%s", (segment,))
                cur.execute("DELETE FROM mp_rejection_item    WHERE segment=%s", (segment,))

                for typ, td in pipe_agg.items():
                    cur.execute(
                        """INSERT INTO mp_rejection_summary
                               (segment, plant_type, material, prod_kg, rej_kg, n_months)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (segment, plant_type, material)
                           DO UPDATE SET prod_kg=EXCLUDED.prod_kg,
                                         rej_kg=EXCLUDED.rej_kg,
                                         n_months=EXCLUDED.n_months,
                                         computed_at=now()""",
                        (segment, "PIPE", typ,
                         round(td["out"], 3), round(td["rej"], 3),
                         len(pipe_months_covered)),
                    )
                    cur.execute(
                        """INSERT INTO mp_rejection_item
                               (segment, plant_type, item_key, material, prod_kg, rej_kg, n_months)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (segment, plant_type, item_key)
                           DO UPDATE SET prod_kg=EXCLUDED.prod_kg,
                                         rej_kg=EXCLUDED.rej_kg,
                                         n_months=EXCLUDED.n_months,
                                         material=EXCLUDED.material,
                                         computed_at=now()""",
                        (segment, "PIPE", f"PIPE-{typ}", typ,
                         round(td["out"], 3), round(td["rej"], 3),
                         len(pipe_months_covered)),
                    )

                for mat, md in fitting_agg.items():
                    label = mat if mat else "ALL"
                    cur.execute(
                        """INSERT INTO mp_rejection_summary
                               (segment, plant_type, material, prod_kg, rej_kg, n_months)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (segment, plant_type, material)
                           DO UPDATE SET prod_kg=EXCLUDED.prod_kg,
                                         rej_kg=EXCLUDED.rej_kg,
                                         n_months=EXCLUDED.n_months,
                                         computed_at=now()""",
                        (segment, "FITTING", mat,
                         round(md["out"], 3), round(md["rej"], 3),
                         len(fitting_months_covered)),
                    )
                    cur.execute(
                        """INSERT INTO mp_rejection_item
                               (segment, plant_type, item_key, material, prod_kg, rej_kg, n_months)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (segment, plant_type, item_key)
                           DO UPDATE SET prod_kg=EXCLUDED.prod_kg,
                                         rej_kg=EXCLUDED.rej_kg,
                                         n_months=EXCLUDED.n_months,
                                         material=EXCLUDED.material,
                                         computed_at=now()""",
                        (segment, "FITTING", f"FITTING-{label}", mat,
                         round(md["out"], 3), round(md["rej"], 3),
                         len(fitting_months_covered)),
                    )

                cur.execute(
                    """INSERT INTO mp_rejection_meta
                           (segment, pipe_months, fitting_months, months_list, last_recomputed)
                       VALUES (%s, %s, %s, %s, now())
                       ON CONFLICT (segment) DO UPDATE
                           SET pipe_months=EXCLUDED.pipe_months,
                               fitting_months=EXCLUDED.fitting_months,
                               months_list=EXCLUDED.months_list,
                               last_recomputed=now()""",
                    (segment, len(pipe_months_covered), len(fitting_months_covered),
                     json.dumps(all_months)),
                )
    except Exception:
        logger.exception("mp_rejection: recompute_rejection failed")
        return {"ok": False, "error": "DB write failed",
                "pipe_months": len(pipe_months_covered),
                "fitting_months": len(fitting_months_covered),
                "errors": errors}

    # Record provenance (rejection reads from many PIPE monthly workbooks;
    # no single Drive file ID — seeded_at is the freshness signal)
    try:
        import mp_seed_provenance as _prov
        total_rows = len(pipe_agg) + len(fitting_agg)
        _prov.record_seed(
            "mp_rejection_summary",
            source_file_ids="",
            source_file_names="PIPE monthly workbooks (Report-11, Report-12)",
            source_modified_time=None,
            row_count=total_rows,
        )
    except Exception:
        logger.warning("mp_rejection: provenance record failed (non-fatal)")

    return {"ok": True,
            "pipe_months": len(pipe_months_covered),
            "fitting_months": len(fitting_months_covered),
            "all_months": all_months,
            "errors": errors}


# ── Query helpers ────────────────────────────────────────────────────────────

def get_rejection_summary(segment: str = "PLUMBING") -> List[dict]:
    """Return all rows from mp_rejection_summary ordered by plant_type, material."""
    if not store.AVAILABLE:
        return []
    try:
        with store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT plant_type, material, prod_kg, rej_kg, n_months
                       FROM mp_rejection_summary
                       WHERE segment=%s
                       ORDER BY plant_type, material""",
                    (segment,),
                )
                rows = cur.fetchall()
    except Exception:
        logger.exception("mp_rejection: get_rejection_summary failed")
        return []

    result = []
    for plant_type, material, prod_kg, rej_kg, n_months in rows:
        prod = float(prod_kg)
        rej  = float(rej_kg)
        pct  = round(rej / prod * 100, 2) if prod > 0 else None
        result.append({
            "plant_type": plant_type,
            "material":   material or "ALL",
            "prod_kg":    prod,
            "rej_kg":     rej,
            "pct":        pct,
            "n_months":   n_months,
        })
    return result


def get_rejection_items(
    segment: str = "PLUMBING",
    search: str = "",
    page: int = 1,
    per_page: int = 50,
) -> Tuple[List[dict], int]:
    """Return paginated rows from mp_rejection_item with optional search."""
    if not store.AVAILABLE:
        return [], 0
    try:
        with store._conn() as conn, conn.cursor() as cur:
                where = "WHERE segment=%s"
                params: list = [segment]
                if search:
                    where += " AND (item_key ILIKE %s OR material ILIKE %s OR plant_type ILIKE %s)"
                    q = f"%{search}%"
                    params += [q, q, q]
                cur.execute(f"SELECT COUNT(*) FROM mp_rejection_item {where}", params)
                total = cur.fetchone()[0]
                offset = (page - 1) * per_page
                cur.execute(
                    f"""SELECT plant_type, item_key, material, prod_kg, rej_kg, n_months
                        FROM mp_rejection_item {where}
                        ORDER BY plant_type, rej_kg DESC NULLS LAST
                        LIMIT %s OFFSET %s""",
                    params + [per_page, offset],
                )
                rows = cur.fetchall()
    except Exception:
        logger.exception("mp_rejection: get_rejection_items failed")
        return [], 0

    result = []
    for plant_type, item_key, material, prod_kg, rej_kg, n_months in rows:
        prod = float(prod_kg)
        rej  = float(rej_kg)
        pct  = round(rej / prod * 100, 2) if prod > 0 else None
        result.append({
            "plant_type": plant_type,
            "item_key":   item_key,
            "material":   material or "ALL",
            "prod_kg":    prod,
            "rej_kg":     rej,
            "pct":        pct,
            "n_months":   n_months,
        })
    return result, total


def get_rejection_meta(segment: str = "PLUMBING") -> Optional[dict]:
    """Return the metadata row for ``segment``, or ``None`` if not yet computed."""
    if not store.AVAILABLE:
        return None
    try:
        with store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT pipe_months, fitting_months, months_list, last_recomputed
                       FROM mp_rejection_meta WHERE segment=%s""",
                    (segment,),
                )
                row = cur.fetchone()
    except Exception:
        logger.exception("mp_rejection: get_rejection_meta failed")
        return None
    if not row:
        return None
    pipe_months, fitting_months, months_list, last_recomputed = row
    try:
        months = json.loads(months_list)
    except Exception:
        months = []
    return {
        "pipe_months":     pipe_months,
        "fitting_months":  fitting_months,
        "months_covered":  months,
        "last_recomputed": last_recomputed,
    }
