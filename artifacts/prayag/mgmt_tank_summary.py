"""mgmt_tank_summary.py — Data builder for Tank M/C Summary (Management Reports 7–9).

Report 7 = Tank KH  (plant=TANK,    family=tank_kh)
Report 8 = Tank VN  (plant=TANK_VN, family=tank_vn)
Report 9 = Tank WB  (plant=TANK_WB, family=tank_wb)

Layout: TRANSPOSED — months run across columns (latest-first).
Each month occupies two sub-columns: Production (in Ltr) | Rejection (in Ltr).

Two stacked pivot sections (both cover identical production, split two ways):
  1. By product type  (2 Layer Light, 3 Layer Colour, …)
  2. By tank size     (500, 750, 1000, …)
Both TOTAL rows carry the same figure.

Sources (Cardinal Rule — every figure recomputed from live data):
  Production (Ltr) — get_daily_records(), r.total_count
  Rejection  (Ltr) — get_daily_records(), r.reject_count
                     (already pcs × SIZE (LTR.) from parse_tank_prod, R-09)
  Sheet comparison — load_report_records(family), product-type section only
                     (annual parser stops at the second TOTAL, R-37)

Item code → (product_type, size_ltr): parse_item_code().
  Reads size from _SIZE_CODE_MAP — the suffix is a code, NOT arithmetic.
  -05=500 Ltr, -07=750 Ltr (not 700). Do not derive from numeric suffix.
  Unmapped codes raise TankItemCodeError (R-06); no silent default bucket.

Documented divergences per spec:
  KH JUN: daily 1,419,500 vs sheet 633,500 (R-26, 2.24×) — open, do not adjust
  KH APR/MAY/JUL: absent from annual sheet; present in daily (show daily, R-07/R-08)
  KH JUN: two source data errors (R-35) flagged but NOT adjusted:
            23-Jun WCT-3LC-05 (90 rej / 10 pcs → 45,000 Ltr rej)
            30-Jun WCT-3LL-10 (243 pcs from 6 cycles, inflates by ~219,000 Ltr)
  VN JUL: daily 565,500 vs sheet 563,500 (+2,000 Ltr)
  WB JUL: daily 1,702,000 vs sheet 1,432,000 (+270,000 Ltr)
  WB JUN: daily 1,430,000 vs sheet 1,429,600 (+400 Ltr)
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

# ── FY constants ────────────────────────────────────────────────────────────────

MONTH_LABELS = [
    "APR", "MAY", "JUN", "JUL", "AUG", "SEP",
    "OCT", "NOV", "DEC", "JAN", "FEB", "MAR",
]

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

_FY_LABEL: dict[str, str] = {"2627": "FY 2026–27"}

_PLANT_LABEL: dict[str, str] = {
    "TANK":    "Tanks (Kaharani)",
    "TANK_VN": "Tanks (Varanasi)",
    "TANK_WB": "Tanks (West Bengal)",
}

_PLANT_FAMILY: dict[str, str] = {
    "TANK":    "tank_kh",
    "TANK_VN": "tank_vn",
    "TANK_WB": "tank_wb",
}

# ── Item code → size mapping ───────────────────────────────────────────────────
# Size from SIZE (LTR.) column in PROD. REPORT. Confirmed from spec examples.
# These are CODE lookups, NOT arithmetic derivations.
# -05 = 500 Ltr, -07 = 750 Ltr (not 700): read directly from the sheet.

_SIZE_CODE_MAP: dict[str, int] = {
    "03": 300,   # Confirmed from SIZE (LTR.) col in PROD. REPORT (May 2026 KH workbook)
    "05": 500,
    "07": 750,
    "10": 1000,
    "15": 1500,
    "20": 2000,
    "30": 3000,
    "50": 5000,
}

# Layer-type character in item codes (NLX pattern: N=layers, L=layer indicator, X=type)
_LAYER_TYPE_MAP: dict[str, str] = {
    "L": "Light",
    "C": "Colour",
    "H": "Heavy",
    "I": "ISI",
}

# Canonical product-type sort order (matches SUMMARY (LTR) layout rows)
_PRODUCT_TYPE_ORDER: list[str] = [
    "2 Layer Light", "2 Layer ISI",
    "3 Layer Light", "3 Layer Colour", "3 Layer Heavy",
    "4 Layer Light", "4 Layer Colour", "4 Layer Heavy",
    "ISI",
]

# ── KH data errors (R-35, FY26-27 June) ────────────────────────────────────────
# Documented, flagged on the report, NOT adjusted. Source records already carry
# these figures; the pcs×size rule is applied faithfully (R-09).

_KH_DATA_ERRORS: list[dict] = [
    {
        "date":  "23 Jun 2026",
        "item":  "WCT-3LC-05",
        "description": (
            "10 pcs produced; 90 recorded as rejected. "
            "Our pcs×size rule (R-09) derives 45,000 Ltr rejection (90 × 500 Ltr). "
            "Workbook dashboard reads 0% rejection for this item-date — "
            "source data should be verified with the data owner."
        ),
        "impact_note": "Overstates JUN'26 rejection by ~45,000 Ltr.",
    },
    {
        "date":  "30 Jun 2026",
        "item":  "WCT-3LL-10",
        "description": (
            "243 pcs recorded from 6 cycles; 6 cycles elsewhere yield ~24 pcs. "
            "Likely data-entry error (219 excess pcs × 1,000 Ltr = ~219,000 Ltr). "
            "Source record carried as-is — no adjustment applied (Cardinal Rule)."
        ),
        "impact_note": "Overstates JUN'26 production by ~219,000 Ltr.",
    },
]


# ── Caches ──────────────────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict = {}          # {(plant, fy): (ts, data)}
_CACHE_TTL = 600.0         # 10 minutes


# ── Exceptions ──────────────────────────────────────────────────────────────────

class TankItemCodeError(ValueError):
    """R-06: item code does not map to a known product type or size."""


# ── Item code parsing ───────────────────────────────────────────────────────────

def parse_item_code(code: str) -> tuple[str, int]:
    """Map Tank item code → (product_type_label, size_ltr).

    Size is read from _SIZE_CODE_MAP (not computed from the numeric suffix).
    Raises TankItemCodeError (R-06) for unrecognised codes.

    Supported patterns (prefix = WCT | WT | any):
      {prefix}-{N}L{X}-{sz}  → "{N} Layer {Light|Colour|Heavy|ISI}"
      {prefix}-ISI-{sz}      → "ISI"
      {prefix}-{N}ISI-{sz}   → "{N} Layer ISI"

    where N ∈ {2,3,4}, X ∈ {L,C,H,I}, sz ∈ {05,07,10,15,20,30,50}.
    """
    parts = code.strip().split("-")
    if len(parts) < 3:
        raise TankItemCodeError(
            f"R-06: Tank item code {code!r} has only {len(parts)} segment(s); "
            "need at least 3 (prefix-type-size)."
        )

    size_seg = parts[-1].strip()
    if size_seg not in _SIZE_CODE_MAP:
        raise TankItemCodeError(
            f"R-06: Unknown size suffix {size_seg!r} in Tank item code {code!r}. "
            f"Known sizes: {sorted(_SIZE_CODE_MAP.keys())}."
        )
    size_ltr = _SIZE_CODE_MAP[size_seg]

    type_seg = parts[-2].strip().upper()

    # Pure ISI grade (no layer count)
    if type_seg == "ISI":
        return "ISI", size_ltr

    # {N}ISI  →  "{N} Layer ISI"
    if len(type_seg) >= 2 and type_seg[0].isdigit() and type_seg[1:] == "ISI":
        return f"{type_seg[0]} Layer ISI", size_ltr

    # {N}L{X}  →  "{N} Layer {type}"
    if len(type_seg) == 3 and type_seg[0].isdigit() and type_seg[1] == "L":
        layer_n  = type_seg[0]
        kind_chr = type_seg[2]
        kind_word = _LAYER_TYPE_MAP.get(kind_chr)
        if kind_word is None:
            raise TankItemCodeError(
                f"R-06: Unknown layer-type character {kind_chr!r} in {code!r}. "
                "Expected: L=Light, C=Colour, H=Heavy, I=ISI."
            )
        return f"{layer_n} Layer {kind_word}", size_ltr

    raise TankItemCodeError(
        f"R-06: Cannot parse type segment {type_seg!r} in Tank item code {code!r}. "
        "Expected pattern: {N}L{X} (e.g. 3LC), ISI, or {N}ISI (e.g. 2ISI)."
    )


# ── Sort keys ───────────────────────────────────────────────────────────────────

def _product_type_sort_key(label: str) -> tuple:
    """Sort product types in canonical SUMMARY order; unknown types come last."""
    try:
        return (0, _PRODUCT_TYPE_ORDER.index(label))
    except ValueError:
        return (1, label)


def _size_sort_key(label: str) -> int:
    """Sort tank size labels numerically."""
    try:
        return int(label)
    except ValueError:
        return 999_999


# ── Core accumulator ────────────────────────────────────────────────────────────

def _accumulate_tank(
    records,
    plant: str,
) -> tuple[dict, dict, dict, list]:
    """Accumulate daily Tank records into two pivot dicts.

    Returns:
      by_type  — {product_type_label → {ym → {"prod": float, "rej": float}}}
      by_size  — {size_label (str)   → {ym → {"prod": float, "rej": float}}}
      code_map — {item_code → {"product_type": str, "size_ltr": int}}
      unmapped — [item_code, ...]  (R-06 candidates; logged, never silently bucketed)
    """
    by_type: dict = {}
    by_size: dict = {}
    code_map: dict = {}
    unmapped: list = []

    for r in records:
        if r.plant != plant:
            continue
        ym        = getattr(r, "period", None)
        item_code = (getattr(r, "mould",  "") or "").strip()
        if not ym or not item_code:
            continue  # phantom run-hours records carry mould="" — skip gracefully

        # Resolve item code on first encounter
        if item_code not in code_map and item_code not in unmapped:
            try:
                pt, sz = parse_item_code(item_code)
                code_map[item_code] = {"product_type": pt, "size_ltr": sz}
                logger.info(
                    "mgmt_tank [%s] code_map: %s → type=%r size_ltr=%d",
                    plant, item_code, pt, sz,
                )
            except TankItemCodeError as exc:
                logger.error("mgmt_tank [%s] %s", plant, exc)
                unmapped.append(item_code)

        if item_code in unmapped:
            continue

        mapping = code_map.get(item_code)
        if not mapping:
            continue

        pt    = mapping["product_type"]
        sz_lbl = str(mapping["size_ltr"])
        prod  = float(r.total_count  or 0.0)
        rej   = float(r.reject_count or 0.0)
        if prod == 0.0 and rej == 0.0:
            continue

        # By product type
        by_type.setdefault(pt, {}).setdefault(ym, {"prod": 0.0, "rej": 0.0})
        by_type[pt][ym]["prod"] += prod
        by_type[pt][ym]["rej"]  += rej

        # By tank size
        by_size.setdefault(sz_lbl, {}).setdefault(ym, {"prod": 0.0, "rej": 0.0})
        by_size[sz_lbl][ym]["prod"] += prod
        by_size[sz_lbl][ym]["rej"]  += rej

    return by_type, by_size, code_map, unmapped


# ── Pivot section builder ───────────────────────────────────────────────────────

def _build_pivot_section(
    pivot: dict,
    months: list[str],
    sort_key=None,
) -> dict:
    """Build one pivot section: sorted rows + TOTAL row.

    Cell for a month with no data → None (blank), never 0 (R-07/R-08).
    The TOTAL row accumulates all rows' production and rejection.
    """
    labels = sorted(pivot.keys(), key=sort_key or (lambda x: x))
    rows: list = []
    tot_prod: dict[str, float] = {}
    tot_rej:  dict[str, float] = {}

    for label in labels:
        ym_data    = pivot.get(label, {})
        row_months: dict = {}
        has_data   = False

        for ym in months:
            cell = ym_data.get(ym)
            if cell and (cell["prod"] > 0 or cell["rej"] > 0):
                pv = cell["prod"] if cell["prod"] > 0 else None
                rv = cell["rej"]  if cell["rej"]  > 0 else None
                row_months[ym] = {"prod": pv, "rej": rv}
                has_data = True
                tot_prod[ym] = tot_prod.get(ym, 0.0) + (cell["prod"] or 0.0)
                tot_rej[ym]  = tot_rej.get(ym,  0.0) + (cell["rej"]  or 0.0)
            else:
                row_months[ym] = None   # blank — not 0

        if has_data:
            rows.append({"label": label, "months": row_months})

    total_months: dict = {}
    for ym in months:
        p = tot_prod.get(ym, 0.0)
        r = tot_rej.get(ym,  0.0)
        if p > 0 or r > 0:
            total_months[ym] = {"prod": p or None, "rej": r or None}
        else:
            total_months[ym] = None

    return {
        "rows":  rows,
        "total": {"label": "TOTAL", "months": total_months},
    }


# ── Annual sheet section ─────────────────────────────────────────────────────────

def _build_annual_section(
    annual_records,
    plant: str,
    months: list[str],
) -> dict | None:
    """Build a section from annual workbook records (sheet's SUMMARY first pivot).

    Annual records have r.mould = product type label (pre-split by the parser
    which stops at the second TOTAL row, R-37). Returns None if no data.
    """
    by_type: dict = {}
    for r in annual_records:
        if r.plant != plant:
            continue
        ym    = getattr(r, "period", None)
        label = (getattr(r, "mould", "") or "").strip()
        if not ym or not label:
            continue
        prod = float(r.total_count  or 0.0)
        rej  = float(r.reject_count or 0.0)
        if prod == 0.0 and rej == 0.0:
            continue
        by_type.setdefault(label, {}).setdefault(ym, {"prod": 0.0, "rej": 0.0})
        by_type[label][ym]["prod"] += prod
        by_type[label][ym]["rej"]  += rej

    if not by_type:
        return None
    return _build_pivot_section(by_type, months, sort_key=_product_type_sort_key)


# ── Divergence computation ───────────────────────────────────────────────────────

def _compute_divergences(
    our_total_months: dict,
    sheet_total_months: dict,
    fy_ym_inv: dict,
    failed_yms: set | None = None,
) -> list[dict]:
    """Flag per-month total production divergences between daily and annual sheet.

    ``failed_yms`` — months whose daily read failed.  Divergence banners must
    not fire against these: we have no "ours" figure, so a −100 % banner would
    be confident and wrong (R-06 Failure Mode #9).
    """
    divs: list = []
    all_yms = sorted(
        set(our_total_months.keys()) | set(sheet_total_months.keys())
    )
    for ym in all_yms:
        if failed_yms and ym in failed_yms:
            continue  # our data is unavailable — comparison is meaningless
        our_cell  = our_total_months.get(ym)
        shee_cell = sheet_total_months.get(ym)
        our_prod  = (our_cell["prod"]  if our_cell  else None) or 0.0
        shee_prod = (shee_cell["prod"] if shee_cell else None) or 0.0
        delta     = our_prod - shee_prod
        if abs(delta) < 1.0:
            continue
        pct = (delta / shee_prod * 100) if shee_prod > 0 else None
        divs.append({
            "ym":         ym,
            "month_disp": fy_ym_inv.get(ym, ym),
            "ours":       our_prod  or None,
            "sheet":      shee_prod or None,
            "delta":      delta,
            "pct":        pct,
        })
    return divs


# ── Top-level builder ──────────────────────────────────────────────────────────

def _do_build(plant: str, fy: str) -> dict:
    import sheets as _sh

    family    = _PLANT_FAMILY[plant]
    fy_ym     = _FY_YM.get(fy, _FY_YM["2627"])
    fy_disp   = _FY_DISP.get(fy, _FY_DISP["2627"])
    all_yms   = list(fy_ym.values())
    # Reverse mapping: ym → display label  (e.g. "2026-07" → "JUL'26")
    fy_ym_inv = {v: fy_disp[k] for k, v in fy_ym.items()}

    # ── Daily records (all plants; filter in accumulator) ─────────────────────
    try:
        daily_all, _daily_reports, _ = _sh.get_daily_records(all_yms)
    except Exception as exc:
        raise RuntimeError(f"Could not load daily Tank records: {exc}") from exc

    # Extract any failed (plant, ym) pairs surfaced by the sentinel dict that
    # get_daily_records appends to reports when reads were throttled or failed.
    # Filter to THIS plant only — failures elsewhere don't affect our pivot.
    _failed_pairs = next(
        (r["_failed_pairs"] for r in _daily_reports
         if isinstance(r, dict) and "_failed_pairs" in r),
        [],
    )
    failed_yms: set = {ym for p, ym in _failed_pairs if p == plant}

    by_type, by_size, code_map, unmapped = _accumulate_tank(daily_all, plant)

    # ── Months that carry our daily data ──────────────────────────────────────
    daily_yms: set = set()
    for ym_dict in list(by_type.values()) + list(by_size.values()):
        daily_yms.update(ym_dict.keys())

    # ── Annual records for sheet comparison ───────────────────────────────────
    try:
        annual_recs = _sh.load_report_records(family)
        # Only current FY
        all_yms_set = set(all_yms)
        annual_recs = [r for r in annual_recs if r.period in all_yms_set]
    except Exception as exc:
        logger.warning("mgmt_tank [%s]: load_report_records failed: %s", plant, exc)
        annual_recs = []

    # Annual may have months our daily doesn't (e.g. if daily pinned sources lag)
    annual_yms: set = {r.period for r in annual_recs if r.total_count and float(r.total_count) > 0}

    # Months to show: union, in descending (latest-first) order, FY months only
    all_data_yms = (daily_yms | annual_yms) & set(all_yms)
    months = sorted(all_data_yms, reverse=True)
    months_disp = [fy_ym_inv[m] for m in months]

    # ── Pivot sections ────────────────────────────────────────────────────────
    section_type = _build_pivot_section(
        by_type, months, sort_key=_product_type_sort_key,
    )
    section_size = _build_pivot_section(
        by_size, months, sort_key=_size_sort_key,
    )
    sheet_section = _build_annual_section(annual_recs, plant, months)

    # Both TOTAL rows carry the same figures (derived from the same daily source).
    # Assert totals match across the two sections for transparency.
    _t_type = section_type["total"]["months"]
    _t_size = section_size["total"]["months"]
    for _ym in months:
        _tp = (_t_type.get(_ym) or {}).get("prod") or 0.0
        _sp = (_t_size.get(_ym) or {}).get("prod") or 0.0
        if abs(_tp - _sp) > 0.5:
            logger.warning(
                "mgmt_tank [%s] %s: section_type TOTAL %g ≠ section_size TOTAL %g — "
                "unmapped codes may have been dropped. unmapped=%s",
                plant, _ym, _tp, _sp, unmapped,
            )

    # ── Divergences ───────────────────────────────────────────────────────────
    sheet_total_months = sheet_section["total"]["months"] if sheet_section else {}
    divs = _compute_divergences(
        _t_type,
        sheet_total_months,
        fy_ym_inv,
        failed_yms=failed_yms,      # suppress banners for months we couldn't read
    )

    # ── KH data errors (R-35, FY2627 only) ────────────────────────────────────
    data_errors = _KH_DATA_ERRORS if (plant == "TANK" and fy == "2627") else []

    # Log code map for validation transparency (spec: print codes + types before wiring)
    logger.info(
        "mgmt_tank [%s] code_map (%d codes, %d unmapped): %s",
        plant, len(code_map), len(unmapped),
        {k: v["product_type"] for k, v in code_map.items()},
    )

    return {
        "plant":         plant,
        "plant_label":   _PLANT_LABEL.get(plant, plant),
        "fy":            fy,
        "fy_label":      _FY_LABEL.get(fy, fy),
        "months":        months,          # latest-first ym strings
        "months_disp":   months_disp,     # latest-first display labels
        "n_months":      len(months),
        "section_type":  section_type,    # By product type
        "section_size":  section_size,    # By tank size
        "sheet_section": sheet_section,   # Annual workbook first section (or None)
        "divergences":   divs,
        "data_errors":   data_errors,
        "code_map":      code_map,        # transparency: item_code → {product_type, size_ltr}
        "unmapped_codes": unmapped,
        # Months whose daily read failed this run (throttled / Sheets unavailable).
        # Template renders "source unavailable" cells; result is NOT cached so the
        # next request retries fresh (R-06 Failure Mode #9).
        "failed_months": sorted(failed_yms),
        "error":         None,
    }


def build_tank_summary(plant: str, fy: str = "2627") -> dict:
    """Top-level builder, cached 10 minutes per (plant, fy).

    Entry point for the three management-report routes:
      Report 7 — build_tank_summary("TANK",    "2627")
      Report 8 — build_tank_summary("TANK_VN", "2627")
      Report 9 — build_tank_summary("TANK_WB", "2627")
    """
    if plant not in _PLANT_FAMILY:
        raise ValueError(f"mgmt_tank: unknown plant {plant!r}. Expected: {list(_PLANT_FAMILY.keys())}")
    cache_key = (plant, fy)
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CACHE_TTL:
            return cached[1]

    try:
        result = _do_build(plant, fy)
    except Exception as exc:
        logger.exception("build_tank_summary(%s, %s) failed", plant, fy)
        result = {
            "error":        str(exc),
            "plant":        plant,
            "plant_label":  _PLANT_LABEL.get(plant, plant),
            "fy":           fy,
            "fy_label":     _FY_LABEL.get(fy, fy),
        }

    # Do NOT cache a result built from partial reads — some (plant, ym) pairs
    # failed and their months show as blank rather than "source unavailable".
    # The next request will retry; once all pairs succeed the result is cached.
    if result.get("failed_months"):
        logger.warning(
            "build_tank_summary(%s, %s): skipping cache — %d month(s) failed: %s",
            plant, fy, len(result["failed_months"]), result["failed_months"],
        )
        return result

    with _cache_lock:
        _cache[cache_key] = (time.time(), result)
    return result


def invalidate_cache(plant: str | None = None, fy: str = "2627") -> None:
    """Evict cached data for one or all tank plants."""
    with _cache_lock:
        if plant:
            _cache.pop((plant, fy), None)
        else:
            for key in [("TANK", fy), ("TANK_VN", fy), ("TANK_WB", fy)]:
                _cache.pop(key, None)
