"""mgmt_labour_power.py — Data builder for Management Report 1.

Segment Labour / Power / Solar — full 17-column FY view.

Cardinal Rule (PRAYAG_RULES §Cardinal): **layout is not data.** Every figure
comes from our own recomputed sources. The annual workbook is verification
material only. Where our figure differs from the sheet's we show ours and flag.

Sources:
  wages / power / headcount — Annual "Segment Wise Labour Cost …" workbook
    (LABOUR_SOURCES["PLUMBING"][fy] file ID), tabs UNIT-1 / UNIT-2 / UNIT-3.
    Wages and power columns ARE populated. Headcount is #REF! for UNIT-1.
    Production is 0 for UNIT-1 (formula broken in the source workbook).
  production (kg) — our own recomputed daily records + costing module.
  per-kg metrics — computed: wages ÷ our_prod_kg, power ÷ our_prod_kg.

Do-not-touch list (PRAYAG_RULES): costing_labour.py · costing_power.py ·
parsers.parse_segment_labour · tank_reconcile.py · pipe_reconcile.py ·
baselines.json · mp_*.py · auth.py.  No Sheets writes (R-21).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── FY constants ───────────────────────────────────────────────────────────────

MONTH_LABELS = ["APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                "OCT", "NOV", "DEC", "JAN", "FEB", "MAR"]

_FY_YM: dict[str, dict[str, str]] = {
    "2627": {
        "APR": "2026-04", "MAY": "2026-05", "JUN": "2026-06", "JUL": "2026-07",
        "AUG": "2026-08", "SEP": "2026-09", "OCT": "2026-10", "NOV": "2026-11",
        "DEC": "2026-12", "JAN": "2027-01", "FEB": "2027-02", "MAR": "2027-03",
    },
}

_FY_DISP: dict[str, dict[str, str]] = {
    "2627": {
        "APR": "APR'26", "MAY": "MAY'26", "JUN": "JUN'26", "JUL": "JUL'26",
        "AUG": "AUG'26", "SEP": "SEP'26", "OCT": "OCT'26", "NOV": "NOV'26",
        "DEC": "DEC'26", "JAN": "JAN'27", "FEB": "FEB'27", "MAR": "MAR'27",
    },
}

# ── Unit / segment definitions ─────────────────────────────────────────────────

UNIT_LABELS = ["UNIT-1", "UNIT-2", "UNIT-3"]
UNIT_SEGMENTS: dict[str, list[str]] = {
    "UNIT-1": ["CP", "PTMT", "Hardware", "Sink"],
    "UNIT-2": ["Plumbing", "Tank"],
    "UNIT-3": ["Garden Pipe", "HDPE Pipe"],
}

# Plants in our daily records that map to each management-report segment name.
# Segments with no plant mapping (CP / Hardware / Sink / Plumbing) are handled
# separately — never shown as zero when we have no basis (R-07/R-08).
_SEG_PLANTS: dict[str, frozenset] = {
    "PTMT":        frozenset(["PTMT"]),
    "Tank":        frozenset(["TANK", "TANK_VN", "TANK_WB"]),
    "Garden Pipe": frozenset(["GARDEN", "GARDEN_WB"]),
    "HDPE Pipe":   frozenset(["HDPE"]),
}

# Production basis note shown in the report footer per segment
PROD_BASIS: dict[str, str] = {
    "PTMT":        "Nett (daily records)",
    "Plumbing":    "Gross pipe + fitting (costing module, R-12 source)",
    "Tank":        "secondary_counts['kg'] from daily workbooks",
    "Garden Pipe": "Nett (daily records)",
    "HDPE Pipe":   "Nett (daily records)",
    "CP":          "No source available",
    "Hardware":    "No source available",
    "Sink":        "No source available",
}


# ── Cell value helpers ─────────────────────────────────────────────────────────

def _num(v) -> Optional[float]:
    """Parse a sheet cell to float.  None for blank / broken / error values."""
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("#") or s in ("", "-", "—", "n/a", "N/A"):
        return None
    cleaned = re.sub(r"[,₹\s]", "", s)
    try:
        result = float(cleaned)
        return result if result != 0.0 else None  # 0 = broken formula, treat as None
    except ValueError:
        return None


def _num_allow_zero(v) -> Optional[float]:
    """Like _num but preserves genuine zeroes (for headcount / kWh)."""
    if v is None:
        return None
    s = str(v).strip()
    if s.startswith("#") or s in ("", "-", "—", "n/a", "N/A"):
        return None
    cleaned = re.sub(r"[,₹\s]", "", s)
    try:
        return float(cleaned)
    except ValueError:
        return None


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().upper())


# ── Column finder ──────────────────────────────────────────────────────────────

_COL_KEYS = [
    "seg", "month",
    "n_labour", "n_contractor", "n_total_lab",
    "paid_wages", "contractor_wages", "total_wages",
    "prod_kg",
    "jvvl", "kwh", "unit_per_kg", "rate_708", "total_power", "solar",
    "per_kg_power_src", "per_kg_labour_src", "total_cost_src",
]


def _find_cols(header_rows: list) -> dict:
    """Map logical column keys to 0-based column indices from one or two header rows.

    Disambiguation rules applied most-specific-first so 'TOTAL PAID WAGES' is
    captured before the plain 'PAID WAGES' pattern, etc.
    """
    cols: dict[str, int] = {k: -1 for k in _COL_KEYS}
    for hrow in header_rows:
        for i, cell in enumerate(hrow):
            s = _norm(cell)
            if not s or s in ("-", "—"):
                continue
            # --- most specific first ---
            if "7.08" in s and cols["rate_708"] < 0:
                cols["rate_708"] = i
            elif "JVVL" in s and cols["jvvl"] < 0:
                cols["jvvl"] = i
            elif "PER KG POWER" in s and cols["per_kg_power_src"] < 0:
                cols["per_kg_power_src"] = i
            elif "PER KG LABOUR" in s and cols["per_kg_labour_src"] < 0:
                cols["per_kg_labour_src"] = i
            elif ("TOTAL COST" in s and "POWER" not in s
                  and "PAID" not in s and cols["total_cost_src"] < 0):
                cols["total_cost_src"] = i
            elif "TOTAL POWER" in s and cols["total_power"] < 0:
                cols["total_power"] = i
            elif ("TOTAL PAID" in s or "TOTAL WAGES" in s) and cols["total_wages"] < 0:
                cols["total_wages"] = i
            elif "TOTAL LABOUR" in s and cols["n_total_lab"] < 0:
                cols["n_total_lab"] = i
            elif "UTILISE" in s and cols["unit_per_kg"] < 0:
                cols["unit_per_kg"] = i
            elif ("KWH" in s or ("ELECTRICITY" in s
                  and ("UNIT" in s or "GENERATION" in s))) and cols["kwh"] < 0:
                cols["kwh"] = i
            elif "PRODUCTION" in s and ("KG" in s or "TOT" in s) and cols["prod_kg"] < 0:
                cols["prod_kg"] = i
            elif "SOLAR" in s and cols["solar"] < 0:
                cols["solar"] = i
            # --- contractor before plain ---
            elif "CONTRACTOR" in s and "LABOUR" in s and cols["n_contractor"] < 0:
                cols["n_contractor"] = i
            elif "CONTRACTOR" in s and ("PAID" in s or "WAGES" in s) and cols["contractor_wages"] < 0:
                cols["contractor_wages"] = i
            elif "LABOUR" in s and cols["n_labour"] < 0:
                cols["n_labour"] = i
            elif "PAID WAGES" in s and cols["paid_wages"] < 0:
                cols["paid_wages"] = i
            elif "SEGMENT" in s and cols["seg"] < 0:
                cols["seg"] = i
            elif "MONTH" in s and cols["month"] < 0:
                cols["month"] = i
    return cols


# ── Month label parser ─────────────────────────────────────────────────────────

def _parse_month_lbl(s: object) -> Optional[str]:
    """Extract the 3-letter FY month abbreviation (APR–MAR) from any cell text."""
    m = re.search(
        r"\b(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\b",
        str(s or "").upper(),
    )
    return m.group(1) if m else None


# ── Tab parser ─────────────────────────────────────────────────────────────────

def parse_unit_tab(values: list, *, unit_label: str, fy: str = "2627") -> list:
    """Parse one UNIT-N tab from the annual workbook.

    Returns a list of row dicts — one per (segment, month_label) including the
    TOTAL row.  Each dict contains all 17 column values (None when absent/broken).

    Keys: unit · segment · month_label · month_disp · ym · is_total_row ·
          n_labour · n_contractor · n_total_lab ·
          paid_wages · contractor_wages · total_wages ·
          src_prod_kg (workbook's own figure — 0 treated as None) ·
          jvvl · kwh · unit_per_kg · rate_708 · total_power · solar ·
          per_kg_power_src · per_kg_labour_src · total_cost_src
    """
    if not values:
        return []

    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])

    # Locate header row: first row with (SEGMENT or MONTH) AND LABOUR
    header_idx = -1
    for i, row in enumerate(values[:8]):
        joined = " ".join(_norm(c) for c in row)
        if ("SEGMENT" in joined or "MONTH" in joined) and "LABOUR" in joined:
            header_idx = i
            break
    if header_idx < 0:
        logger.warning("parse_unit_tab(%s): no header row found", unit_label)
        return []

    # Check if the next row is a sub-header (not a data row with month/TOTAL)
    header_rows = [values[header_idx]]
    if header_idx + 1 < len(values):
        next_row = values[header_idx + 1]
        nc = [_norm(c) for c in next_row[:6] if _norm(c)]
        has_data_start = any(_parse_month_lbl(c) for c in nc) or any(
            "TOTAL" in c and len(c) <= 8 for c in nc
        )
        if not has_data_start:
            header_rows.append(next_row)

    cols = _find_cols(header_rows)
    month_c = cols["month"]
    seg_c   = cols["seg"]

    if month_c < 0:
        logger.warning("parse_unit_tab(%s): MONTH column not found", unit_label)
        return []

    data_start = header_idx + len(header_rows)
    rows_out: list = []
    carry_seg = ""

    def g(row: list, c: int) -> object:
        return row[c] if 0 <= c < len(row) else ""

    for row in values[data_start:]:
        if not any(str(c).strip() for c in row):
            continue  # skip fully-blank rows

        # Carry segment name forward (merged cells in workbook)
        if seg_c >= 0:
            sv = _norm(g(row, seg_c))
            if sv and sv not in ("SEGMENT", "GRAND TOTAL", "TOTAL", ""):
                # Title-case normalisation keeps downstream matching robust
                carry_seg = sv.title()

        month_raw = str(g(row, month_c)).strip().upper()
        month_lbl = _parse_month_lbl(month_raw)
        is_total  = "TOTAL" in month_raw and not month_lbl

        if not month_lbl and not is_total:
            continue

        ym = fy_ym.get(month_lbl) if month_lbl else None

        # Workbook production: 0 treated as broken formula → None
        raw_prod    = _num_allow_zero(g(row, cols["prod_kg"]))
        src_prod_kg = raw_prod if (raw_prod is not None and raw_prod > 0) else None

        rows_out.append({
            "unit":         unit_label,
            "segment":      carry_seg,
            "month_label":  month_lbl or "TOTAL",
            "month_disp":   fy_disp.get(month_lbl, "TOTAL") if month_lbl else "TOTAL",
            "ym":           ym,
            "is_total_row": is_total,
            # headcount (#REF! → None for UNIT-1)
            "n_labour":     _num_allow_zero(g(row, cols["n_labour"])),
            "n_contractor": _num_allow_zero(g(row, cols["n_contractor"])),
            "n_total_lab":  _num_allow_zero(g(row, cols["n_total_lab"])),
            # wages (populated in workbook for most months)
            "paid_wages":       _num(g(row, cols["paid_wages"])),
            "contractor_wages": _num(g(row, cols["contractor_wages"])),
            "total_wages":      _num(g(row, cols["total_wages"])),
            # workbook's own production (0 = broken)
            "src_prod_kg":  src_prod_kg,
            # power / solar
            "jvvl":        _num(g(row, cols["jvvl"])),
            "kwh":         _num_allow_zero(g(row, cols["kwh"])),
            "unit_per_kg": _num(g(row, cols["unit_per_kg"])),
            "rate_708":    _num(g(row, cols["rate_708"])),
            "total_power": _num(g(row, cols["total_power"])),
            "solar":       _num(g(row, cols["solar"])),
            # per-kg as computed by workbook (blank when production = 0)
            "per_kg_power_src":  _num(g(row, cols["per_kg_power_src"])),
            "per_kg_labour_src": _num(g(row, cols["per_kg_labour_src"])),
            "total_cost_src":    _num(g(row, cols["total_cost_src"])),
        })

    return rows_out


# ── Annual workbook loader ─────────────────────────────────────────────────────

def load_annual_tabs(fy: str, token: str) -> dict:
    """Read UNIT-1 / UNIT-2 / UNIT-3 tabs from the annual segment-cost workbook.

    Returns {unit_label: [row_dict, ...]} — raw parsed rows, not yet enriched.
    """
    import costing_model
    import sheets as _sheets

    file_id = costing_model.labour_file_id("PLUMBING", fy)
    if not file_id:
        raise ValueError(f"No labour source file registered for FY{fy}")

    # Resolve actual tab names (workbook may use "UNIT 1" or "UNIT-1")
    want = ["UNIT-1", "UNIT-2", "UNIT-3"]
    try:
        live_tabs = _sheets.list_tabs(file_id, token)
        resolved = []
        for lbl in want:
            num = lbl[-1]
            pat = re.compile(r"UNIT[-\s]*" + num + r"\b", re.I)
            match = next((t for t in live_tabs if pat.search(t)), None)
            resolved.append(match or lbl)
    except Exception as exc:
        logger.warning("load_annual_tabs: list_tabs failed (%s); using default names", exc)
        resolved = want

    try:
        matrices = _sheets.batch_get(file_id, resolved, token)
    except Exception as exc:
        logger.exception("load_annual_tabs: batch_get failed")
        raise RuntimeError(f"Could not read annual workbook tabs: {exc}") from exc

    result = {}
    for actual_tab, unit_label in zip(resolved, want):
        rows = parse_unit_tab(matrices.get(actual_tab, []),
                              unit_label=unit_label, fy=fy)
        result[unit_label] = rows
    return result


# ── Production kg from our own sources ────────────────────────────────────────

def accum_record_kg(r, seg: str) -> float:
    """Return the nett production kg contribution of a single Record for `seg`.

    For Tank the authoritative kg figure comes from secondary_counts['kg']
    (a separate kg-specific measurement independent of the primary litre unit).
    For all other plant-backed segments nett = total_count − reject_count
    (gross output minus rejected material) following the canonical metrics model.
    reject_count is in the same unit as total_count for PTMT / Garden / HDPE.
    """
    if seg == "Tank":
        return float((r.secondary_counts or {}).get("kg") or 0.0)
    gross  = float(r.total_count or 0.0)
    reject = float(getattr(r, "reject_count", 0.0) or 0.0)
    return max(0.0, gross - reject)


def accumulate_monthly(records, seg_plants: dict) -> dict:
    """Accumulate nett production kg per segment from a list of Records.

    Parameters
    ----------
    records    : iterable of Record (from get_daily_records for one month)
    seg_plants : mapping {segment_name: frozenset of plant codes}

    Returns {segment_name: float} — 0.0 when no matching records exist.
    This helper is a pure function of its inputs so it is easy to unit-test.
    """
    totals: dict[str, float] = {seg: 0.0 for seg in seg_plants}
    for r in records:
        for seg, plants in seg_plants.items():
            if r.plant in plants:
                totals[seg] += accum_record_kg(r, seg)
    return totals


def get_segment_prod_kg(fy: str = "2627") -> dict:
    """Return {segment_name: {ym: float|None}} for every segment.

    Plumbing comes from the costing module (gross: pipe + fitting from R-12).
    PTMT / Garden Pipe / HDPE Pipe use nett kg = total_count − reject_count
    from daily production records (canonical metrics definition; R-19).
    Tank uses secondary_counts['kg'] which is already a direct kg measurement.
    Segments with no data source (CP / Hardware / Sink) always return None.
    Months with no records return None (never zero — R-07/R-08).
    """
    import sheets as _sheets

    fy_ym = _FY_YM.get(fy, _FY_YM["2627"])
    all_yms = [fy_ym[lbl] for lbl in MONTH_LABELS]

    # Collect per-segment per-month from daily records (one call per month)
    raw: dict[str, dict[str, float]] = {s: {} for s in _SEG_PLANTS}
    for ym in all_yms:
        try:
            month_recs, _, _ = _sheets.get_daily_records([ym])
        except Exception as exc:
            logger.warning("get_segment_prod_kg(%s): get_daily_records failed: %s", ym, exc)
            month_recs = []
        monthly = accumulate_monthly(month_recs, _SEG_PLANTS)
        for seg, kg in monthly.items():
            if kg > 0:
                raw[seg][ym] = raw[seg].get(ym, 0.0) + kg

    # Convert to {seg: {ym: float|None}} — zero means no records → show blank
    result: dict[str, dict[str, Optional[float]]] = {}
    for seg in _SEG_PLANTS:
        result[seg] = {ym: (raw[seg][ym] if raw[seg].get(ym, 0.0) > 0 else None)
                       for ym in all_yms}

    # Plumbing: costing module gross production (pipe kg + fitting kg from R-12)
    try:
        import costing_model as _cm
        monthly_rows = _cm.get_labour_monthly("PLUMBING", fy)
        ym_map = {lbl: ym for lbl, ym in fy_ym.items()}
        plumbing = {}
        for row in (monthly_rows or []):
            lbl = str(row.get("month_label", "")).upper().strip()
            ym  = ym_map.get(lbl)
            if not ym:
                continue
            tpk = row.get("total_prod_kg")
            plumbing[ym] = float(tpk) if tpk else None
        result["Plumbing"] = {ym: plumbing.get(ym) for ym in all_yms}
    except Exception as exc:
        logger.warning("get_segment_prod_kg: Plumbing from costing_model failed: %s", exc)
        result["Plumbing"] = {ym: None for ym in all_yms}

    # No-source segments
    for seg in ("CP", "Hardware", "Sink"):
        result[seg] = {ym: None for ym in all_yms}

    return result


# ── Row enrichment ─────────────────────────────────────────────────────────────

def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _enrich_rows(raw_rows: list, seg_kg: dict,
                 unit_label: str, fy: str = "2627") -> dict:
    """Merge workbook rows with our production kg and compute per-kg metrics.

    Returns {segment_name: {"total_row": row, "month_rows": [row, ...]}}
    where every row has extra keys:
      our_prod_kg, per_kg_power, per_kg_labour, total_cost,
      awaiting, headcount_broken, prod_overridden, per_kg_computed.
    """
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])

    # Index workbook rows by segment → month_label
    by_seg: dict[str, dict[str, dict]] = {}
    for row in raw_rows:
        seg = row.get("segment") or ""
        by_seg.setdefault(seg, {})[row["month_label"]] = row

    result = {}
    for seg in UNIT_SEGMENTS[unit_label]:
        seg_key   = _resolve_seg_key(by_seg, seg)
        seg_rows  = by_seg.get(seg_key, {})
        prod_map  = seg_kg.get(seg, {})

        # ── Monthly rows (APR through MAR) ─────────────────────────────────────
        month_rows = []
        for lbl in MONTH_LABELS:
            wb   = seg_rows.get(lbl) or {}
            ym   = fy_ym[lbl]
            our_kg = prod_map.get(ym)

            total_wages = wb.get("total_wages")
            paid_wages  = wb.get("paid_wages")
            total_power = wb.get("total_power")

            # July with blank wages = awaiting source data (R-07)
            awaiting = (lbl == "JUL"
                        and total_wages is None
                        and (paid_wages is None or paid_wages == 0))

            headcount_broken = (wb.get("n_labour") is None
                                and wb.get("n_contractor") is None
                                and bool(wb))

            src_prod = wb.get("src_prod_kg")
            prod_overridden = our_kg is not None and (src_prod is None or src_prod <= 0)

            per_kg_power  = _safe_div(total_power, our_kg)
            per_kg_labour = _safe_div(total_wages, our_kg)
            total_cost    = (
                total_power + total_wages
                if total_power is not None and total_wages is not None
                else None
            )
            per_kg_computed = (
                (per_kg_power is not None or per_kg_labour is not None)
                and (wb.get("per_kg_power_src") is None
                     or wb.get("per_kg_labour_src") is None)
            )

            month_rows.append({
                **{k: None for k in _EXTRA_KEYS},
                **wb,
                "unit":         unit_label,
                "segment":      seg,
                "month_label":  lbl,
                "month_disp":   fy_disp[lbl],
                "ym":           ym,
                "is_total_row": False,
                "our_prod_kg":    our_kg,
                "per_kg_power":   per_kg_power,
                "per_kg_labour":  per_kg_labour,
                "total_cost":     total_cost,
                "awaiting":           awaiting,
                "headcount_broken":   headcount_broken,
                "prod_overridden":    prod_overridden,
                "per_kg_computed":    per_kg_computed,
            })

        # ── TOTAL row ──────────────────────────────────────────────────────────
        # Prefer workbook TOTAL for wages/power (formula aggregation is correct);
        # compute our own production total and per-kg metrics.
        wb_tot  = seg_rows.get("TOTAL") or {}
        our_kg_vals = [r["our_prod_kg"] for r in month_rows
                       if r["our_prod_kg"] is not None]
        our_kg_total = sum(our_kg_vals) if our_kg_vals else None

        tw_t  = wb_tot.get("total_wages")
        tp_t  = wb_tot.get("total_power")
        per_kg_power_t  = _safe_div(tp_t, our_kg_total)
        per_kg_labour_t = _safe_div(tw_t, our_kg_total)
        total_cost_t    = (
            tp_t + tw_t
            if tp_t is not None and tw_t is not None
            else None
        )
        src_prod_t      = wb_tot.get("src_prod_kg")
        prod_ov_t       = our_kg_total is not None and (src_prod_t is None or src_prod_t <= 0)
        hc_broken_t     = (wb_tot.get("n_labour") is None
                           and wb_tot.get("n_contractor") is None
                           and bool(wb_tot))
        per_kg_comp_t   = (
            (per_kg_power_t is not None or per_kg_labour_t is not None)
            and (wb_tot.get("per_kg_power_src") is None
                 or wb_tot.get("per_kg_labour_src") is None)
        )

        total_row = {
            **{k: None for k in _EXTRA_KEYS},
            **wb_tot,
            "unit":         unit_label,
            "segment":      seg,
            "month_label":  "TOTAL",
            "month_disp":   "TOTAL",
            "ym":           None,
            "is_total_row": True,
            "our_prod_kg":    our_kg_total,
            "per_kg_power":   per_kg_power_t,
            "per_kg_labour":  per_kg_labour_t,
            "total_cost":     total_cost_t,
            "awaiting":           False,
            "headcount_broken":   hc_broken_t,
            "prod_overridden":    prod_ov_t,
            "per_kg_computed":    per_kg_comp_t,
        }

        result[seg] = {
            "total_row":  total_row,
            "month_rows": month_rows,
            "has_headcount_broken": hc_broken_t or any(
                r["headcount_broken"] for r in month_rows),
            "has_prod_overridden": prod_ov_t or any(
                r["prod_overridden"] for r in month_rows),
            "has_per_kg_computed": per_kg_comp_t or any(
                r["per_kg_computed"] for r in month_rows),
        }
    return result


# Keys added during enrichment (pre-seeded to None in case wb row is missing)
_EXTRA_KEYS = [
    "n_labour", "n_contractor", "n_total_lab",
    "paid_wages", "contractor_wages", "total_wages",
    "src_prod_kg", "jvvl", "kwh", "unit_per_kg", "rate_708",
    "total_power", "solar", "per_kg_power_src", "per_kg_labour_src", "total_cost_src",
]


def _resolve_seg_key(by_seg: dict, want: str) -> str:
    """Case-insensitive key lookup with first-word fallback."""
    want_n = want.upper().replace(" ", "")
    for k in by_seg:
        if k.upper().replace(" ", "") == want_n:
            return k
    # fuzzy: first word
    first = want.upper().split()[0] if want.split() else want.upper()
    for k in by_seg:
        if k.upper().startswith(first):
            return k
    return want


# ── In-process cache ───────────────────────────────────────────────────────────

_cache: dict = {}            # fy -> (ts, data_dict)
_cache_lock = threading.Lock()
_CACHE_TTL  = 900.0          # 15 minutes


# ── Main entry point ───────────────────────────────────────────────────────────

def build_mgmt_report_data(fy: str = "2627") -> dict:
    """Build the full data structure for the management report web view.

    Returns:
      {
        "units": [
          {
            "label": "UNIT-1",
            "segments": [
              {
                "name": "PTMT",
                "prod_basis": "Nett (daily records)",
                "total_row": {...},
                "month_rows": [...],     # APR through MAR
                "has_headcount_broken": bool,
                "has_prod_overridden":  bool,
                "has_per_kg_computed":  bool,
              },
              ...
            ]
          },
          ...
        ],
        "fy":       "2627",
        "fy_label": "FY2026-27",
        "error":    None | str,
      }
    Cached for 15 minutes; call ``invalidate_cache`` to force a reload.
    """
    now = time.time()
    hit = _cache.get(fy)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    with _cache_lock:
        now = time.time()
        hit = _cache.get(fy)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
        data = _do_build(fy)
        _cache[fy] = (now, data)
    return data


def _do_build(fy: str) -> dict:
    import sheets as _sheets

    fy_label = {"2627": "FY2026-27", "2526": "FY2025-26"}.get(fy, f"FY{fy[:2]}-{fy[2:]}")

    token = _sheets._get_access_token()
    if not token:
        return {
            "units": [], "fy": fy, "fy_label": fy_label,
            "error": (
                "Google Sheets connection not authorised. "
                "Reconnect it from the integrations panel and try again."
            ),
        }

    # 1. Load annual workbook tabs
    try:
        annual = load_annual_tabs(fy, token)
    except Exception as exc:
        logger.exception("build_mgmt_report_data: load_annual_tabs failed")
        return {
            "units": [], "fy": fy, "fy_label": fy_label,
            "error": f"Could not read annual workbook: {exc}",
        }

    # 2. Load production kg from our own sources
    try:
        seg_kg = get_segment_prod_kg(fy)
    except Exception as exc:
        logger.warning("build_mgmt_report_data: get_segment_prod_kg failed: %s", exc)
        seg_kg = {}

    # 3. Enrich and assemble per unit
    units = []
    for unit_label in UNIT_LABELS:
        enriched = _enrich_rows(annual.get(unit_label, []), seg_kg, unit_label, fy)
        segments = []
        for seg in UNIT_SEGMENTS[unit_label]:
            seg_data = enriched.get(seg)
            if seg_data is None:
                # Unit tab was empty or segment not found — create blank rows
                seg_data = _blank_seg(unit_label, seg, fy)
            seg_data["name"]       = seg
            seg_data["prod_basis"] = PROD_BASIS.get(seg, "")
            segments.append(seg_data)
        units.append({"label": unit_label, "segments": segments})

    return {"units": units, "fy": fy, "fy_label": fy_label, "error": None}


def _blank_seg(unit: str, seg: str, fy: str) -> dict:
    fy_ym   = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    blank   = {k: None for k in _EXTRA_KEYS + [
        "our_prod_kg", "per_kg_power", "per_kg_labour", "total_cost",
    ]}
    blank.update(awaiting=False, headcount_broken=False,
                 prod_overridden=False, per_kg_computed=False)

    def _row(is_tot: bool, lbl: str = "") -> dict:
        return {
            **blank,
            "unit": unit, "segment": seg,
            "month_label": "TOTAL" if is_tot else lbl,
            "month_disp":  "TOTAL" if is_tot else fy_disp.get(lbl, lbl),
            "ym":          None if is_tot else fy_ym.get(lbl),
            "is_total_row": is_tot,
        }

    return {
        "total_row":  _row(True),
        "month_rows": [_row(False, lbl) for lbl in MONTH_LABELS],
        "has_headcount_broken": False,
        "has_prod_overridden":  False,
        "has_per_kg_computed":  False,
    }


def invalidate_cache(fy: str = "2627") -> None:
    """Evict the cached report data so the next request re-reads from Sheets."""
    with _cache_lock:
        _cache.pop(fy, None)
