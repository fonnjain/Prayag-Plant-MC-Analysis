"""
Machine Planning seeders — Phase MP-0.

Loads source data from five Google Drive files and populates segment='PLUMBING'
tables via mp_model.  All parsers are HEADER-BASED; item codes are normalised
(strip + uppercase + remove spaces and hyphens) so demand codes like "PW11"
resolve to the same key as the BOM entry "PW 11".

DRIVE ACCESS POLICY: ``seed_all`` confirms Drive access for every source file
before writing.  If any file is inaccessible it raises ``SeedAccessError``
reporting exactly which file(s) failed — it never silently seeds empty rows.

ADDITIVE/ISOLATED: nothing here imports from app.py, plan.py, or any existing
pipeline module.  Only sheets.py helpers (read_values, list_tabs, _get_access_token)
and mp_model upserts are called.
"""
from __future__ import annotations

import re
import datetime
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("prayag.mp_seed")

import sheets
import mp_model
from mp_model import (
    MpMachine, MpRouting, MpFittingStd, MpBomWeight,
    MpPerHour, MpCompoundRecipe, MpParams,
)

# ---------------------------------------------------------------------------
# Source file IDs
# ---------------------------------------------------------------------------
_FILE_IDS: Dict[str, str] = {
    "bom":          "1R7k5O6w4qaT74G-5X2VXBtD7-Fg3uByvIw3-TeViMmA",
    "pipe_routing": "1bJl4RLJhk0_6v-Pn1ve7T2ZR8YftpPxkwmlwcCxqHUA",
    "fitting":      "1y2HRoJNQmE2BthE0f18YU1w0ly1LMvyqP98f2_4Wero",
    "per_hour":     "1wlB4Y4lnP7Y2SLZX6atFN-nrKA--ByYF8m2TVHuBxD0",
    "compound":     "1owRHQodo_ye5WMAekqa7GgcIJbmjJgwCDRzfQk8-BYM",
}

# June 2026 "MACHINE PLANING JUNE 2026" workbook — real per-hour rates for all pipe materials.
# Two tabs must be merged: "PIPE PURD PLAN " (CPVC/SWR/AGRI) + "MACHINE" (UPVC).
_JUNE_PIPE_FILE_ID = "15mh7lXXuNqpyIg36jK7uMbensI4QRQ-5ILEqsjSbRNU"

# Per-material kg/hr fallback rates seeded as defaults.
# These are stored in mp_params and override the computed mat_avg in the engine.
_PIPE_MAT_DEFAULTS: Dict[str, float] = {
    "CPVC": 145.6,
    "UPVC": 250.0,
    "SWR":  295.0,
    "AGRI": 300.0,
}

SEGMENT = "PLUMBING"
_PIPE_MATERIAL_FAMILIES = ["CPVC", "UPVC", "SWR", "AGRI"]
_FITTING_MATERIAL_FAMILIES = ["CPVC", "UPVC", "SWR", "AGRI"]
_COMPOUND_WASTAGE_FACTOR = 1.01


class SeedAccessError(Exception):
    """Raised when one or more Drive files cannot be read."""


class SeedParseError(Exception):
    """Raised when a required header or block cannot be found in a sheet."""


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def current_month() -> str:
    """Return today's month as 'YYYY-MM'."""
    return datetime.date.today().strftime("%Y-%m")


def norm_code(code: str) -> str:
    """Normalise an item code: strip, uppercase, remove all spaces and hyphens.

    Examples:
        "PW 11"  -> "PW11"
        "PW-11"  -> "PW11"
        " ps-16 "-> "PS16"
    """
    return re.sub(r"[\s\-]+", "", str(code).strip()).upper()


def _to_float(val: str) -> Optional[float]:
    """Convert a spreadsheet cell string to float, or None if unparseable."""
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _cell(row: list, idx: int) -> str:
    """Safe cell access — returns stripped string or '' if out of bounds."""
    if idx < len(row):
        v = row[idx]
        return str(v).strip() if v is not None else ""
    return ""


def _is_spurious_numeric(nc: str) -> bool:
    """Return True if *nc* (already normalised) is clearly NOT a real item code.

    Drops:
      - Decimal strings like '104.8' or '11.8'  (OD / size values)
      - 8-or-more-digit pure integers like '33000778'  (internal ERP IDs)

    Keeps:
      - 4–7-digit integers like '5110', '5121'  (real fitting / pipe item codes)
      - Any code containing at least one letter
    """
    if "." in nc:
        return True                    # decimal → OD size
    if re.match(r"^\d{8,}$", nc):
        return True                    # 8+ pure digits → ERP internal ID
    return False


def _header_map(header_row: list) -> Dict[str, int]:
    """Return {normalised_header: col_index} from a header row."""
    out: Dict[str, int] = {}
    for i, cell in enumerate(header_row):
        key = str(cell).strip().lower()
        if key:
            out[key] = i
    return out


def _find_header_row(rows: List[list], keywords: List[str],
                     max_scan: int = 15) -> int:
    """Return the index of the first row containing ALL keywords (case-insensitive).

    Raises SeedParseError if not found within max_scan rows.
    """
    kws = [k.lower() for k in keywords]
    for i, row in enumerate(rows[:max_scan]):
        row_text = " ".join(str(c).lower() for c in row)
        if all(kw in row_text for kw in kws):
            return i
    raise SeedParseError(
        f"Could not find header row containing {keywords!r} in first {max_scan} rows."
    )


# ---------------------------------------------------------------------------
# Drive access check
# ---------------------------------------------------------------------------

def check_drive_access(token: str) -> Dict[str, bool]:
    """Probe each source file with a minimal read and return {name: ok}."""
    results: Dict[str, bool] = {}
    for name, fid in _FILE_IDS.items():
        try:
            sheets.read_values(fid, "A1:A2", token)
            results[name] = True
        except Exception as exc:
            logger.warning("Drive access FAIL for %s (%s): %s", name, fid, exc)
            results[name] = False
    return results


# ---------------------------------------------------------------------------
# 1. BOM weights
# ---------------------------------------------------------------------------

def parse_bom_weights(rows: List[list]) -> List[dict]:
    """Parse BOM weight rows (tab "NEW").

    Finds the header row containing 'item code' and 'weight', then collects
    (normalised item_code, weight_per_pc_kg) for every subsequent data row.

    Returns list of dicts with keys: item_code, weight_per_pc_kg.
    """
    if not rows:
        return []
    # Find header row — look for "item code" / "weight" keywords
    hdr_idx = -1
    for i, row in enumerate(rows[:20]):
        row_lower = " ".join(str(c).lower() for c in row)
        if ("item" in row_lower or "code" in row_lower) and "weight" in row_lower:
            hdr_idx = i
            break
    if hdr_idx < 0:
        # Fall back: assume row 0 is the header if it has 2+ non-empty cells
        hdr_idx = 0

    hdr = _header_map(rows[hdr_idx])
    # Column A = item code, Column B = weight — find by header or fallback
    code_col = next(
        (hdr[k] for k in hdr if "item" in k or "code" in k), 0
    )
    wt_col = next(
        (hdr[k] for k in hdr if "weight" in k or "wt" in k or "kg" in k), 1
    )

    out: List[dict] = []
    seen: Dict[str, float] = {}
    for row in rows[hdr_idx + 1:]:
        raw_code = _cell(row, code_col)
        raw_wt = _cell(row, wt_col)
        if not raw_code or not raw_wt:
            continue
        nc = norm_code(raw_code)
        if not nc:
            continue
        # Drop ERP IDs (8+ digits) and OD-size decimals; keep 4–7 digit item codes
        if _is_spurious_numeric(nc):
            continue
        wt = _to_float(raw_wt)
        if wt is None or wt <= 0:
            continue
        # Last value wins on duplicate codes (normalisation may collapse variants)
        seen[nc] = wt

    for code, wt in seen.items():
        out.append({"item_code": code, "weight_per_pc_kg": wt})
    return out


def seed_bom_weights(token: str, effective_month: str = "") -> dict:
    """Fetch BOM sheet and upsert weights. Returns {rows_loaded, unweighted_codes}."""
    em = effective_month or current_month()
    fid = _FILE_IDS["bom"]
    raw = sheets.read_values(fid, "NEW", token)
    parsed = parse_bom_weights(raw)
    if not parsed:
        raise SeedParseError("BOM 'NEW' tab parsed 0 weight rows — check sheet access.")
    records = [
        MpBomWeight(
            segment=SEGMENT,
            item_code=r["item_code"],
            weight_per_pc_kg=r["weight_per_pc_kg"],
            effective_month=em,
        )
        for r in parsed
    ]
    count = mp_model.upsert_bom_weights(records)
    return {"rows_loaded": count, "effective_month": em}


# ---------------------------------------------------------------------------
# 2. Pipe routing + staffing
# ---------------------------------------------------------------------------

# Machine name patterns for pipe extrusion machines
_MC_PATTERN = re.compile(r"M/?C[-\s]?(\d+)", re.IGNORECASE)
_NUM_PATTERN = re.compile(r"^\d+$")


def _is_machine_name(cell: str) -> bool:
    return bool(_MC_PATTERN.search(cell))


def parse_pipe_routing(rows: List[list]) -> Tuple[List[dict], List[dict]]:
    """Parse "Details" tab for pipe routing and machine staffing.

    Fixed geometry (verified from the real file — do not re-derive):
      Row 0 (0-indexed): Size(in), Size(mm), then repeating W/OT labels
      Row 1 (0-indexed): "", "", then staffing counts (W in first col, OT in second)
      Row 2 (0-indexed): "", "", then machine names in first col of each pair
                         e.g. "M/C- 1" (note space before digit)
      Rows 3+: item codes in first col of pair; material in second col

    Column pairs (0-indexed, A=0/B=1 are sizes — skipped):
      C/D=M/C-1 (2,3), E/F=M/C-2 (4,5), G/H=M/C-3 (6,7), I/J=M/C-4 (8,9),
      K/L=M/C-5 (10,11), M/N=M/C-6 (12,13), O/P=M/C-7 (14,15),
      Q/R=M/C-8 (16,17), S/T=M/C-9 (18,19)

    Returns:
        routing_rows: [{"item_code", "machine", "material"}, ...]
        machine_rows: [{"machine", "operators_ot", "support_w"}, ...]
    """
    if len(rows) < 4:
        return [], []

    staffing_row = rows[1]   # row 2 (1-indexed) = staffing counts
    data_start   = 3         # rows 4+ (1-indexed) = item data

    # Fixed pairs: 9 machines starting at col 2, stepping by 2
    _PAIRS = [(2 + i * 2, 2 + i * 2 + 1) for i in range(9)]

    # Canonical machine names — strip the space the sheet inserts before the digit
    def _canon_mc(raw: str) -> str:
        """'M/C- 1' → 'M/C-1', 'M/C-2' → 'M/C-2'"""
        s = re.sub(r"M/?C\s*-\s*", "M/C-", raw.strip(), flags=re.IGNORECASE)
        return s

    machine_rows: List[dict] = []
    machine_code_col: Dict[str, int] = {}
    machine_mat_col:  Dict[str, int] = {}

    for code_col, mat_col in _PAIRS:
        mc_raw = _cell(rows[2], code_col)
        mc_name = _canon_mc(mc_raw) if mc_raw else ""
        if not mc_name:
            # Derive from pair index as fallback
            pair_idx = (code_col - 2) // 2 + 1
            mc_name = f"M/C-{pair_idx}"

        w_val  = _to_float(_cell(staffing_row, code_col)) or 0
        ot_val = _to_float(_cell(staffing_row, mat_col))  or 0
        machine_rows.append({
            "machine":      mc_name,
            "support_w":    int(w_val),
            "operators_ot": int(ot_val),
        })
        machine_code_col[mc_name] = code_col
        machine_mat_col[mc_name]  = mat_col

    # Collect routing from data rows
    routing: Dict[Tuple[str, str], str] = {}
    for row in rows[data_start:]:
        if not any(str(c).strip() for c in row):
            continue
        for mc_name, code_col in machine_code_col.items():
            cell_val = _cell(row, code_col)
            if not cell_val:
                continue
            nc = norm_code(cell_val)
            if len(nc) < 2:
                continue
            if _is_spurious_numeric(nc):
                continue
            if nc in ("TRUE", "FALSE", "YES", "NO", "X", "NA"):
                continue
            mat_raw  = _cell(row, machine_mat_col[mc_name])
            material = mat_raw.strip().upper() if mat_raw else ""
            routing[(nc, mc_name)] = material

    routing_rows = [
        {"item_code": ic, "machine": mc, "material": mat}
        for (ic, mc), mat in routing.items()
    ]
    return routing_rows, machine_rows


def seed_pipe_routing(token: str, effective_month: str = "") -> dict:
    """Fetch pipe routing tab and upsert mp_routing + mp_machine (extrusion)."""
    em = effective_month or current_month()
    fid = _FILE_IDS["pipe_routing"]
    raw = sheets.read_values(fid, "Details", token)
    routing_rows, machine_rows = parse_pipe_routing(raw)

    machines = [
        MpMachine(
            segment=SEGMENT, machine=r["machine"], kind="extrusion",
            support_w=r["support_w"], operators_ot=r["operators_ot"],
            capacity_hrs_month=500.0, effective_month=em,
        )
        for r in machine_rows
    ]
    routing = [
        MpRouting(
            segment=SEGMENT, item_code=r["item_code"], machine=r["machine"],
            material=r.get("material", ""), capable=True, effective_month=em,
        )
        for r in routing_rows
    ]
    # Clean existing extrusion rows first so stale codes never accumulate
    mp_model.clean_extrusion_routing(SEGMENT, em)
    mc_count = mp_model.upsert_machines(machines)
    rt_count = mp_model.upsert_routing(routing)
    return {
        "machines_loaded": mc_count,
        "routing_rows_loaded": rt_count,
        "effective_month": em,
        "machine_detail": [
            {"machine": r["machine"], "W": r["support_w"], "OT": r["operators_ot"]}
            for r in machine_rows
        ],
    }


# ---------------------------------------------------------------------------
# 3. Fitting routing + cavity / cycle time
# ---------------------------------------------------------------------------

def parse_fitting_routing(rows: List[list]) -> Tuple[List[dict], List[dict], List[dict]]:
    """Parse "Report-12" tab (header at row 6, i.e. 0-indexed row 5).

    Expected header columns:
        C (idx 2): Item Code
        E (idx 4): Moulding Machine
        + columns named 'cavity' and 'cycle time' (located by header text)

    Returns:
        routing_rows: [{"item_code", "machine"}, ...]  — distinct (item, machine) pairs
        std_rows: [{"item_code", "machine", "cavity", "cycle_time_sec"}, ...]
        machine_rows: [{"machine"}, ...]  — distinct moulding machines
    """
    if not rows:
        return [], [], []

    # Find header row (spec says row 6, i.e. index 5; scan rows 3-10 to be safe)
    hdr_idx = -1
    for i in range(min(12, len(rows))):
        row = rows[i]
        row_text = " ".join(str(c).lower() for c in row)
        if ("item" in row_text or "code" in row_text) and "machine" in row_text:
            hdr_idx = i
            break
    if hdr_idx < 0:
        raise SeedParseError(
            "Fitting Report-12: header row with 'Item Code' and 'Machine' not found."
        )

    hdr = rows[hdr_idx]
    hdr_map = _header_map(hdr)

    # Locate columns by header text
    code_col = next(
        (hdr_map[k] for k in hdr_map if "item" in k or "code" in k), 2
    )
    mc_col = next(
        (hdr_map[k] for k in hdr_map
         if "moulding" in k or ("machine" in k and "moulding" in k)), None
    )
    if mc_col is None:
        mc_col = next((hdr_map[k] for k in hdr_map if "machine" in k), 4)

    cavity_col = next(
        (hdr_map[k] for k in hdr_map if "cavity" in k or "mould cav" in k), None
    )
    cycle_col = next(
        (hdr_map[k] for k in hdr_map if "cycle" in k), None
    )

    routing_seen: Dict[Tuple[str, str], bool] = {}
    std_rows: List[dict] = []
    machine_seen: set = set()

    for row in rows[hdr_idx + 1:]:
        raw_code = _cell(row, code_col)
        raw_mc = _cell(row, mc_col)
        if not raw_code or not raw_mc:
            continue
        nc = norm_code(raw_code)
        mc = raw_mc.strip()
        if not nc or not mc:
            continue

        machine_seen.add(mc)
        routing_seen[(nc, mc)] = True

        cavity = _to_float(_cell(row, cavity_col)) if cavity_col is not None else None
        cycle = _to_float(_cell(row, cycle_col)) if cycle_col is not None else None
        std_rows.append({
            "item_code": nc,
            "machine": mc,
            "cavity": cavity,
            "cycle_time_sec": cycle,
        })

    routing_rows = [{"item_code": ic, "machine": mc} for (ic, mc) in routing_seen]
    machine_rows = [{"machine": mc} for mc in sorted(machine_seen)]
    return routing_rows, std_rows, machine_rows


def seed_fitting_routing(token: str, effective_month: str = "") -> dict:
    """Fetch fitting Report-12 and upsert mp_routing + mp_fitting_std + mp_machine."""
    em = effective_month or current_month()
    fid = _FILE_IDS["fitting"]
    raw = sheets.read_values(fid, "Report-12", token)
    routing_rows, std_rows, machine_rows = parse_fitting_routing(raw)

    machines = [
        MpMachine(
            segment=SEGMENT, machine=r["machine"], kind="moulding",
            support_w=0, operators_ot=0, capacity_hrs_month=500.0,
            effective_month=em,
        )
        for r in machine_rows
    ]
    routing = [
        MpRouting(
            segment=SEGMENT, item_code=r["item_code"], machine=r["machine"],
            material="", capable=True, effective_month=em,
        )
        for r in routing_rows
    ]
    stds = [
        MpFittingStd(
            segment=SEGMENT, item_code=r["item_code"], machine=r["machine"],
            cavity=r["cavity"], cycle_time_sec=r["cycle_time_sec"],
            effective_month=em,
        )
        for r in std_rows
    ]
    # Clean existing moulding rows first so stale codes never accumulate
    mp_model.clean_moulding_routing(SEGMENT, em)
    mc_count = mp_model.upsert_machines(machines)
    rt_count = mp_model.upsert_routing(routing)
    std_count = mp_model.upsert_fitting_std(stds)
    cavity_present = sum(1 for r in std_rows if r.get("cavity") is not None)
    return {
        "machines_loaded": mc_count,
        "routing_rows_loaded": rt_count,
        "std_rows_loaded": std_count,
        "cavity_present": cavity_present,
        "effective_month": em,
    }


# ---------------------------------------------------------------------------
# 4. Per-hour rates
# ---------------------------------------------------------------------------

# Pairs to identify tabs for each material × type
_PER_HOUR_TAB_KEYWORDS: List[Tuple[str, str, str]] = [
    ("cpvc", "pipe",    ""),
    ("upvc", "pipe",    ""),
    ("swr",  "pipe",    ""),
    ("agri", "pipe",    ""),
    ("cpvc", "fitting", ""),
    ("upvc", "fitting", ""),
    ("swr",  "fitting", ""),
    ("agri", "fitting", ""),
]


def _match_per_hour_tab(tab_name: str, material: str, mat_type: str,
                        _unused: str = "") -> bool:
    """Return True if tab_name is the per-hour planning tab for material×type.

    Pipe tabs:    contain material + ("pipe" OR "planing"/"planning") AND NOT "fitting".
    Fitting tabs: contain material + "fitting"/"fitt".

    The positive pipe keyword ("pipe"/"planing") ensures generic tabs like
    "CPVC TOP ITEM" or "CPVC" are excluded even though they lack "fitting".
    """
    tn = tab_name.lower()
    if material not in tn:
        return False
    has_fitting = "fitt" in tn or "fitting" in tn
    if mat_type == "fitting":
        return has_fitting
    else:  # pipe
        has_pipe_kw = "pipe" in tn or "planing" in tn or "planning" in tn
        return has_pipe_kw and not has_fitting


def parse_per_hour(rows: List[list], material: str, basis: str) -> List[dict]:
    """Parse a per-hour planning tab.

    Tab structure (from live data):
      Row 0: [material_label, 'AVG SALE ...', ..., 'PRODUCTION PER HOUR', 'WEIGHT', ...]
      Row 1: ['PIPE'/'FITTING', aggregate totals ...]
      Row 2+: [item_code, ...data...]

    Scans for a row that contains 'production per hour' (pipe→kg_per_hr) or
    'cycle time'/'cycle' (fitting→cycle).  The item-code column is identified
    by an 'item'/'code' header; if absent, col 0 is used (it holds item codes
    in the planning-tab layout).  Aggregate rows (where col 0 equals the
    material name, "PIPE", "FITTING", or "TOTAL") are skipped.

    Returns list of dicts: {item_code, basis, value}.
    """
    if not rows:
        return []

    is_pipe_tab = (basis == "kg_per_hr")
    hdr_idx = -1
    code_col: Optional[int] = None
    value_col: Optional[int] = None
    _code_from_explicit = False

    # ── Primary scan: find the row that names the value column ───────────────
    # Scan up to 40 rows to handle tabs with long preamble sections (e.g. SWR).
    for i, row in enumerate(rows[:40]):
        row_lower = " ".join(str(c).lower() for c in row)
        if is_pipe_tab:
            prod_match = (
                "per hour" in row_lower
                or "kg/hr" in row_lower
                or "kg / hr" in row_lower
                or "output/hr" in row_lower
                or "prod/hr" in row_lower
            )
        else:
            prod_match = "cycle time" in row_lower or "cycle" in row_lower

        if not prod_match:
            continue

        hm = _header_map(row)
        # Find value column
        if is_pipe_tab:
            vc = next(
                (hm[k] for k in hm
                 if "per hour" in k or "per hr" in k
                 or "kg/hr" in k or "kg / hr" in k
                 or "output/hr" in k or "prod/hr" in k),
                None,
            )
        else:
            vc = next((hm[k] for k in hm if "cycle" in k), None)

        if vc is None:
            continue

        # Find code column in the SAME header row; track whether it was found
        # explicitly (by label) or is just a 0-fallback.
        cc_explicit = next(
            (hm[k] for k in hm if "item" in k or "code" in k), None
        )
        cc = cc_explicit if cc_explicit is not None else 0

        # Guard: degenerate title row (e.g. "CPVC FITTING CYCLE TIME" at col 0
        # where the title text IS the only cell and maps to both cc and vc=0).
        if cc == vc:
            continue

        hdr_idx = i
        value_col = vc
        code_col = cc
        _code_from_explicit = cc_explicit is not None
        break

    if hdr_idx < 0 or value_col is None:
        logger.warning("parse_per_hour: could not locate header in %s tab", material)
        return []

    code_col = code_col if code_col is not None else 0

    # When the code column wasn't explicitly labeled in the header row, check
    # nearby rows for 'ITEM CODE' — handles layouts where the item-code header
    # appears in a different row (e.g. UPVC PIPE tab: col header in row 2 but
    # 'PRODUCTION PER HOUR' is in row 0).
    if not _code_from_explicit and code_col == 0 and value_col != 0:
        for scan_i in range(max(0, hdr_idx - 5), min(len(rows), hdr_idx + 6)):
            scan_hm = _header_map(rows[scan_i])
            cc2 = next(
                (scan_hm[k] for k in scan_hm if "item" in k or "code" in k),
                None,
            )
            if cc2 is not None and cc2 != value_col:
                code_col = cc2
                break

    # Aggregate labels in col 0 that are not item codes
    _AGGREGATE = {material.upper(), material.lower(),
                  "PIPE", "FITTING", "TOTAL", "GRAND TOTAL", "SUB TOTAL",
                  "pipe", "fitting", "total", "grand total", "sub total"}

    out: Dict[str, float] = {}
    for row in rows[hdr_idx + 1:]:
        raw_code = _cell(row, code_col)
        raw_val = _cell(row, value_col)
        if not raw_code or not raw_val:
            continue
        # Skip aggregate / section-label rows
        if raw_code.strip() in _AGGREGATE:
            continue
        nc = norm_code(raw_code)
        if not nc or len(nc) < 2:
            continue
        # Drop ERP IDs (8+ digits) and OD-size decimals; 4–7 digit codes are real
        if _is_spurious_numeric(nc):
            continue
        val = _to_float(raw_val)
        if val is None or val <= 0:
            continue
        out[nc] = val

    return [{"item_code": ic, "basis": basis, "value": v} for ic, v in out.items()]


def parse_june_pipe_tab(rows: List[list]) -> Dict[str, float]:
    """Parse the 'PIPE PURD PLAN ' tab from the June 2026 workbook.

    Layout (0-indexed):
      Row 2 = header: col 1='Row Labels' (item code), col 3='TYPE', col 5='PER HOUR OUT PUT'
      Row 3+ = data rows

    Returns {norm_code: kg_per_hr} for all rows where TYPE ∈ {CPVC,SWR,AGRI} and
    col F is a valid positive number.  UPVC rows have a blank col F — they are
    silently skipped and will be loaded from the MACHINE tab instead.
    """
    HEADER_IDX = 2  # 0-indexed row that contains the column headers
    CODE_COL  = 1
    TYPE_COL  = 3
    RATE_COL  = 5
    VALID_TYPES = {"CPVC", "UPVC", "SWR", "AGRI"}

    out: Dict[str, float] = {}
    for i, row in enumerate(rows):
        if i <= HEADER_IDX:
            continue
        raw_type = _cell(row, TYPE_COL).strip().upper()
        if raw_type not in VALID_TYPES:
            continue
        raw_code = _cell(row, CODE_COL)
        raw_rate = _cell(row, RATE_COL)
        if not raw_code or not raw_rate:
            continue
        nc = norm_code(raw_code)
        if not nc or len(nc) < 2:
            continue
        if _is_spurious_numeric(nc):
            continue
        val = _to_float(raw_rate)
        if val is None or val <= 0:
            continue
        out[nc] = val
    return out


def parse_june_machine_tab(rows: List[list]) -> Dict[str, float]:
    """Parse the 'MACHINE' (11 NO SHEET) tab from the June 2026 workbook.

    Layout (0-indexed):
      Row 0  = header ('11 NO SHEET', ..., 'PC IN HOURS')
      Row 1+ = data rows: col 1=item code, col 5=per-hour rate

    Skips cells containing #N/A, #DIV/0!, empty strings, or any non-numeric value.
    Returns {norm_code: kg_per_hr} for all parseable rows.
    """
    CODE_COL = 1
    RATE_COL = 5
    _FORMULA_ERRORS = {"#N/A", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NULL!", "#NUM!"}

    out: Dict[str, float] = {}
    for i, row in enumerate(rows):
        if i == 0:
            continue  # header row
        raw_code = _cell(row, CODE_COL)
        raw_rate = _cell(row, RATE_COL)
        if not raw_code or not raw_rate:
            continue
        if raw_rate.strip().upper() in _FORMULA_ERRORS:
            continue
        nc = norm_code(raw_code)
        if not nc or len(nc) < 2:
            continue
        if _is_spurious_numeric(nc):
            continue
        val = _to_float(raw_rate)
        if val is None or val <= 0:
            continue
        out[nc] = val
    return out


def seed_june_pipe_per_hour(token: str, effective_month: str = "") -> dict:
    """Read the June 2026 workbook and seed pipe kg/hr rates for all four materials.

    Reads two tabs and merges them:
      - 'PIPE PURD PLAN ' — CPVC (8 items), SWR (24 items), AGRI (31 items)
      - 'MACHINE'         — UPVC (7 items, all 250 kg/hr); also has some CPVC rows
    Where an item appears in both, PIPE PURD PLAN takes precedence.

    Returns a report dict describing what was read.
    """
    em = effective_month or current_month()
    fid = _JUNE_PIPE_FILE_ID
    tab_report: List[dict] = []

    # ── Tab 1: PIPE PURD PLAN  (trailing space) ───────────────────────────────
    try:
        rows1 = sheets.read_values(fid, "PIPE PURD PLAN ", token)
        pplan = parse_june_pipe_tab(rows1)
        tab_report.append({
            "tab": "PIPE PURD PLAN ", "rows": len(pplan), "status": "ok" if pplan else "empty",
        })
    except Exception as exc:
        logger.warning("seed_june_pipe_per_hour: PIPE PURD PLAN read failed: %s", exc)
        pplan = {}
        tab_report.append({"tab": "PIPE PURD PLAN ", "rows": 0, "status": f"error: {exc}"})

    # ── Tab 2: MACHINE (11 NO SHEET) ──────────────────────────────────────────
    try:
        rows2 = sheets.read_values(fid, "MACHINE", token)
        machine = parse_june_machine_tab(rows2)
        tab_report.append({
            "tab": "MACHINE", "rows": len(machine), "status": "ok" if machine else "empty",
        })
    except Exception as exc:
        logger.warning("seed_june_pipe_per_hour: MACHINE tab read failed: %s", exc)
        machine = {}
        tab_report.append({"tab": "MACHINE", "rows": 0, "status": f"error: {exc}"})

    # ── Merge: PIPE PURD PLAN preferred ───────────────────────────────────────
    merged: Dict[str, float] = {**machine, **pplan}  # pplan overwrites machine

    records = [
        MpPerHour(
            segment=SEGMENT, item_code=ic,
            basis="kg_per_hr", value=val, effective_month=em,
        )
        for ic, val in merged.items()
    ]
    return {
        "rows_loaded": len(records),
        "tab_report": tab_report,
        "effective_month": em,
        "_records": records,
    }


def seed_per_hour(token: str, effective_month: str = "") -> dict:
    """Seed per-hour rates for all materials and types.

    Pipe (kg/hr): read from the June 2026 workbook (_JUNE_PIPE_FILE_ID) which
    has real per-machine output rates for all four pipe materials.

    Fitting (cycle): still read from the old per_hour file using the existing
    tab-matching logic.
    """
    em = effective_month or current_month()
    all_records: List[MpPerHour] = []
    tab_report: List[dict] = []

    # ── Pipe rates from June 2026 workbook ────────────────────────────────────
    june_result = seed_june_pipe_per_hour(token, em)
    all_records.extend(june_result.get("_records", []))
    tab_report.extend(june_result.get("tab_report", []))

    # ── Fitting cycle times from the original per_hour file ───────────────────
    fid = _FILE_IDS["per_hour"]
    available_tabs = sheets.list_tabs(fid, token)
    for material, mat_type, type_kw in _PER_HOUR_TAB_KEYWORDS:
        if mat_type != "fitting":
            continue  # pipe rates already loaded from June workbook
        basis = "cycle"
        matched = [t for t in available_tabs if _match_per_hour_tab(t, material, mat_type, type_kw)]
        if not matched:
            tab_report.append({
                "material": material.upper(), "type": mat_type,
                "tab": None, "rows": 0, "status": "tab_not_found",
            })
            continue
        type_kw_lower = mat_type.lower()
        matched_sorted = sorted(
            matched, key=lambda t: 0 if type_kw_lower in t.lower() else 1,
        )
        tab = matched_sorted[0]
        raw = sheets.read_values(fid, tab, token)
        parsed = parse_per_hour(raw, material.upper(), basis)
        tab_report.append({
            "material": material.upper(), "type": mat_type,
            "tab": tab, "rows": len(parsed), "status": "ok" if parsed else "empty",
        })
        all_records.extend([
            MpPerHour(
                segment=SEGMENT, item_code=r["item_code"],
                basis=r["basis"], value=r["value"], effective_month=em,
            )
            for r in parsed
        ])

    count = mp_model.upsert_per_hour(all_records)
    return {"rows_loaded": count, "tab_report": tab_report, "effective_month": em}


# ---------------------------------------------------------------------------
# 5. Compound recipes
# ---------------------------------------------------------------------------

# Expected 8 combinations: 4 materials × 2 types
_COMPOUND_COMBOS: List[Tuple[str, str]] = [
    ("CPVC", "pipe"), ("UPVC", "pipe"), ("SWR", "pipe"), ("AGRI", "pipe"),
    ("CPVC", "fitting"), ("UPVC", "fitting"), ("SWR", "fitting"), ("AGRI", "fitting"),
]

# Skip keywords in component name column
_COMP_SKIP_WORDS = ("total", "effective", "cost", "waste", "wastage", "---")

# ---------------------------------------------------------------------------
# Explicit verified column map (0-indexed) for each recipe block.
# Columns: (compound_name_col, ratio_kg_col, price_per_kg_col)
#
# "COMPOUND COST - P" tab (pipe recipes):
#   CPVC-Pipe → M,N,O = cols 12,13,14  ← canonical block; NOT the H or R variants
#   UPVC-Pipe → C,D,E = cols  2, 3, 4
#   SWR-Pipe  → W,X,Y = cols 22,23,24
#   AGRI-Pipe → AB,AC,AD = cols 27,28,29
#
# "COMPOUND COST - F" tab (fitting recipes):
#   UPVC-Fitting → C,D,E  = cols  2, 3, 4  (header "UPVC MOULDING")
#   CPVC-Fitting → H,I,J  = cols  7, 8, 9  (header "CPVC MOULDING")
#   SWR-Fitting  → M,N,O  = cols 12,13,14  (header "SWR / AGRI MOULDING" — SWR block)
#   AGRI-Fitting → R,S,T  = cols 17,18,19  (header "SWR / AGRI MOULDING" — AGRI block)
# ---------------------------------------------------------------------------
_PIPE_EXPLICIT_COLS: Dict[str, Tuple[int, int, int]] = {
    "UPVC": ( 2,  3,  4),
    "CPVC": (12, 13, 14),
    "SWR":  (22, 23, 24),
    "AGRI": (27, 28, 29),
}
_FITTING_EXPLICIT_COLS: Dict[str, Tuple[int, int, int]] = {
    "UPVC": ( 2,  3,  4),
    "CPVC": ( 7,  8,  9),
    "SWR":  (12, 13, 14),
    "AGRI": (17, 18, 19),
}
# Row index (0-based) where component data begins (= row 5 in Google Sheets)
_COMPOUND_DATA_START_ROW: int = 4


def _parse_block_explicit(
    rows: List[list],
    comp_col: int,
    ratio_col: int,
    price_col: int,
) -> List[dict]:
    """Parse a single compound recipe block at FIXED column positions.

    Scans from ``_COMPOUND_DATA_START_ROW`` (row 5 in Sheets, 0-indexed row 4)
    downward:

    * Collects component rows until the first row whose compound-name cell
      contains "total" (case-insensitive).
    * After the Total row, scans up to 5 further rows for a "WASTAGE" row;
      the Ratio column of that row holds the wastage_factor (typically 1.01).
    * Non-component rows before Total (e.g. section labels containing "effective"
      or "---") are skipped without stopping the scan.

    Returns a (possibly empty) list of component dicts::

        {"component": str, "ratio_kg": float,
         "price_per_kg": float, "wastage_factor": float}

    ``wastage_factor`` is back-patched on all components once the WASTAGE row
    is found.  If no WASTAGE row is found within 5 rows after Total, the module
    default ``_COMPOUND_WASTAGE_FACTOR`` is used.
    """
    components: List[dict] = []
    wastage_factor = _COMPOUND_WASTAGE_FACTOR
    past_total = False
    total_row_idx = -1

    for ri in range(_COMPOUND_DATA_START_ROW, len(rows)):
        row = rows[ri]
        comp_name = _cell(row, comp_col)

        if past_total:
            # Stop hard after 5 rows beyond Total (handles gaps in the sheet)
            if ri > total_row_idx + 5:
                break
            if not comp_name:
                continue
            if "wastage" in comp_name.lower():
                wf_val = _to_float(_cell(row, ratio_col))
                if wf_val is not None and wf_val > 0:
                    wastage_factor = wf_val
            break  # done with this block regardless

        if not comp_name:
            continue

        comp_lower = comp_name.lower().strip()

        # WASTAGE row encountered before any Total (e.g. CPVC-Fitting has no Total row).
        # Treat as end-of-block and capture wastage_factor from the ratio column.
        if "wastage" in comp_lower:
            wf_val = _to_float(_cell(row, ratio_col))
            if wf_val is not None and wf_val > 0:
                wastage_factor = wf_val
            break

        if "total" in comp_lower:
            past_total = True
            total_row_idx = ri
            continue

        # Section labels / decorative rows — skip without stopping
        if any(kw in comp_lower for kw in ("effective", "---")):
            continue

        ratio = _to_float(_cell(row, ratio_col))
        price = _to_float(_cell(row, price_col))
        if ratio is None and price is None:
            continue

        components.append({
            "component": comp_name.strip(),
            "ratio_kg": ratio or 0.0,
            "price_per_kg": price or 0.0,
            "wastage_factor": _COMPOUND_WASTAGE_FACTOR,  # back-patched below
        })

    # Stamp the resolved wastage_factor onto every component
    for c in components:
        c["wastage_factor"] = wastage_factor

    return components


def _parse_compound_tab(rows: List[list]) -> Dict[Tuple[str, str], List[dict]]:
    """Parse a compound-cost tab with a horizontal multi-block layout.

    Layout (from live sheets):
      Row 1 (0-indexed): block title labels, e.g. 'UPVC PIPE', 'CPVC PIPE',
                         'UPVC MOULDING', 'SWR / AGRI MOULDING'
      Row 2           : column headers per block: 'Compound', 'Ratio in KG', 'Price'
      Row 3           : optional section label ('WORKING COMPOUND COST')
      Rows 4+         : component data rows
      Row ~9          : 'Total' row (skipped)
      Row ~11         : 'WASTAGE' row (skipped)

    Each block starts at the column where its label appears in row 1.
    When multiple blocks share the same label (e.g. three 'CPVC PIPE' blocks),
    the one with the most non-zero ratio values is kept (most complete recipe).

    'SWR / AGRI MOULDING' labels expand into both SWR and AGRI fitting entries.

    Returns {(material, mat_type): [component_dicts]}.
    """
    if len(rows) < 5:
        return {}

    # ── Find the label row ─────────────────────────────────────────────────
    label_row_idx = -1
    for i in range(min(5, len(rows))):
        row_text = " ".join(str(c).strip().lower() for c in rows[i] if str(c).strip())
        if any(m in row_text for m in ("upvc", "cpvc", "swr", "agri")):
            if any(kw in row_text for kw in ("pipe", "moulding", "fitting")):
                label_row_idx = i
                break
    if label_row_idx < 0:
        return {}

    label_row = rows[label_row_idx]
    col_hdr_row_idx = label_row_idx + 1
    col_hdr_row = rows[col_hdr_row_idx] if col_hdr_row_idx < len(rows) else []

    # ── Find data start: skip optional section-label rows ─────────────────
    data_start = col_hdr_row_idx + 1
    for i in range(col_hdr_row_idx + 1, min(col_hdr_row_idx + 4, len(rows))):
        # Rows like ['', 'WORKING COMPOUND COST', ...] are section labels
        non_empty = [str(c).strip() for c in rows[i] if str(c).strip()]
        if len(non_empty) <= 1:
            data_start = i + 1

    # ── Collect block positions from label row ─────────────────────────────
    # block_specs: (start_col, material, mat_type)
    block_specs: List[Tuple[int, str, str]] = []
    for j, cell in enumerate(label_row):
        cell_s = str(cell).strip()
        if not cell_s:
            continue
        cell_l = cell_s.lower()
        mat: Optional[str] = None
        for m in ("cpvc", "upvc", "swr", "agri"):
            if m in cell_l:
                mat = m.upper()
                break
        if mat is None:
            continue
        if "pipe" in cell_l:
            ptype = "pipe"
        elif "moulding" in cell_l or "fitting" in cell_l:
            ptype = "fitting"
        else:
            continue
        # 'SWR / AGRI MOULDING' → both SWR and AGRI fitting
        if "swr" in cell_l and "agri" in cell_l:
            block_specs.append((j, "SWR", ptype))
            block_specs.append((j, "AGRI", ptype))
        else:
            block_specs.append((j, mat, ptype))

    if not block_specs:
        return {}

    # ── For each block starting column, find ratio and price cols ─────────
    def _find_ratio_price(start_col: int) -> Tuple[Optional[int], Optional[int]]:
        ratio_col: Optional[int] = None
        price_col: Optional[int] = None
        for k in range(start_col, min(start_col + 6, len(col_hdr_row))):
            hdr_s = str(col_hdr_row[k]).lower().strip()
            if ("ratio" in hdr_s or "qty" in hdr_s or "kg" in hdr_s) and ratio_col is None:
                ratio_col = k
            elif "price" in hdr_s and price_col is None:
                price_col = k
        return ratio_col, price_col

    # ── Parse components for each block ───────────────────────────────────
    # Group by (mat, ptype): keep a list of component-list versions
    block_versions: Dict[Tuple[str, str], List[List[dict]]] = {}

    for (start_col, mat, ptype) in block_specs:
        ratio_col, price_col = _find_ratio_price(start_col)
        if ratio_col is None:
            continue

        components: List[dict] = []
        for ri in range(data_start, min(data_start + 20, len(rows))):
            row = rows[ri]
            comp = _cell(row, start_col)
            if not comp:
                continue
            # BREAK (not continue) on total/wastage — marks the end of the
            # working-cost block.  Without breaking we bleed into the ACTUAL
            # section which repeats the same component names at different prices.
            if any(kw in comp.lower() for kw in _COMP_SKIP_WORDS):
                break
            ratio = _to_float(_cell(row, ratio_col))
            price = _to_float(_cell(row, price_col)) if price_col is not None else None
            if ratio is None and price is None:
                continue
            components.append({
                "component": comp.strip(),
                "ratio_kg": ratio or 0.0,
                "price_per_kg": price or 0.0,
                "wastage_factor": _COMPOUND_WASTAGE_FACTOR,
            })

        if components:
            block_versions.setdefault((mat, ptype), []).append(components)

    # ── For each key, pick the most complete version (most non-zero ratios) ─
    result: Dict[Tuple[str, str], List[dict]] = {}
    for key, versions in block_versions.items():
        best = max(versions, key=lambda v: sum(1 for c in v if c["ratio_kg"] > 0))
        result[key] = best

    return result


def seed_compound_recipes(token: str, effective_month: str = "") -> dict:
    """Read compound cost tab(s) and upsert recipes using EXPLICIT column map.

    Sources:
      'COMPOUND COST - P' → pipe recipes  (UPVC/CPVC/SWR/AGRI PIPE)
      'COMPOUND COST - F' → fitting recipes (UPVC/CPVC/SWR/AGRI MOULDING)

    Column positions are hardcoded via ``_PIPE_EXPLICIT_COLS`` /
    ``_FITTING_EXPLICIT_COLS`` — no header label scanning.  This avoids
    the ambiguity caused by three 'CPVC PIPE' blocks in the P tab; the
    canonical CPVC-Pipe block is always cols M,N,O (indices 12,13,14).
    """
    em = effective_month or current_month()
    fid = _FILE_IDS["compound"]

    pipe_rows    = sheets.read_values(fid, "COMPOUND COST - P", token)
    fitting_rows = sheets.read_values(fid, "COMPOUND COST - F", token)

    # Parse each block via the verified explicit column map
    all_blocks: Dict[Tuple[str, str], List[dict]] = {}
    for mat, (cc, rc, pc) in _PIPE_EXPLICIT_COLS.items():
        comps = _parse_block_explicit(pipe_rows, cc, rc, pc)
        if comps:
            all_blocks[(mat, "pipe")] = comps
    for mat, (cc, rc, pc) in _FITTING_EXPLICIT_COLS.items():
        comps = _parse_block_explicit(fitting_rows, cc, rc, pc)
        if comps:
            all_blocks[(mat, "fitting")] = comps

    all_records: List[MpCompoundRecipe] = []
    combo_report: List[dict] = []

    for material, mat_type in _COMPOUND_COMBOS:
        components = all_blocks.get((material, mat_type))
        if components:
            for c in components:
                all_records.append(MpCompoundRecipe(
                    segment=SEGMENT,
                    material=material,
                    type=mat_type,
                    component=c["component"],
                    ratio_kg=c["ratio_kg"],
                    price_per_kg=c["price_per_kg"],
                    wastage_factor=c["wastage_factor"],
                    needs_recipe=False,
                    effective_month=em,
                ))
            total_ratio = sum(c["ratio_kg"] for c in components)
            total_cost = sum(c["ratio_kg"] * c["price_per_kg"] for c in components)
            wf = components[0]["wastage_factor"] if components else _COMPOUND_WASTAGE_FACTOR
            tab = "COMPOUND COST - P" if mat_type == "pipe" else "COMPOUND COST - F"
            # eff_rate matches _mp_build_compound_cards: (cost/ratio) × wastage_factor
            eff_rate = (total_cost / total_ratio * wf if total_ratio > 0 else None)
            combo_report.append({
                "material": material, "type": mat_type,
                "components": len(components),
                "total_ratio_kg": round(total_ratio, 2),
                "total_cost": round(total_cost, 2),
                "effective_rate_per_kg": round(eff_rate, 2) if eff_rate else None,
                "tab": tab,
                "status": "found",
            })
        else:
            all_records.append(MpCompoundRecipe(
                segment=SEGMENT,
                material=material,
                type=mat_type,
                component="",
                ratio_kg=0.0,
                price_per_kg=0.0,
                wastage_factor=_COMPOUND_WASTAGE_FACTOR,
                needs_recipe=True,
                effective_month=em,
            ))
            combo_report.append({
                "material": material, "type": mat_type,
                "status": "needs_recipe",
            })

    count = mp_model.upsert_compound_recipe(all_records)
    return {
        "rows_loaded": count,
        "combo_report": combo_report,
        "effective_month": em,
    }


# ---------------------------------------------------------------------------
# 6. Params
# ---------------------------------------------------------------------------

def seed_params(effective_month: str = "") -> dict:
    """Seed default planning parameters for the segment.

    Defaults:
      waste_pct=4, pulverizer_pct=25, min_run_block_hours=2
      Material fallback rates: CPVC 145.6, UPVC 250, SWR 295, AGRI 300 kg/hr
    """
    em = effective_month or current_month()
    mp_model.upsert_params(MpParams(
        segment=SEGMENT, waste_pct=4.0, pulverizer_pct=25.0, effective_month=em,
        min_run_block_hours=2.0,
        cpvc_mat_rate=_PIPE_MAT_DEFAULTS["CPVC"],
        upvc_mat_rate=_PIPE_MAT_DEFAULTS["UPVC"],
        swr_mat_rate=_PIPE_MAT_DEFAULTS["SWR"],
        agri_mat_rate=_PIPE_MAT_DEFAULTS["AGRI"],
    ))
    return {
        "waste_pct": 4.0, "pulverizer_pct": 25.0,
        "min_run_block_hours": 2.0,
        "mat_rates": _PIPE_MAT_DEFAULTS,
        "effective_month": em,
    }


# ---------------------------------------------------------------------------
# Orchestrator: seed_all
# ---------------------------------------------------------------------------

def seed_all(effective_month: str = "") -> dict:
    """Seed all PLUMBING tables for the given month from Drive sources.

    Checks Drive access for all 5 files FIRST.  Raises SeedAccessError listing
    every inaccessible file instead of seeding partial data.

    Returns a full report dict for acceptance-check logging.
    """
    if sheets.is_demo_mode():
        raise SeedAccessError(
            "No Google Sheets connector available (running in demo mode)."
        )

    token = sheets._get_access_token()
    if not token:
        raise SeedAccessError(
            "Could not obtain a Google Sheets access token. "
            "Please re-authorize the Google Sheets connection."
        )

    # ── 1. Drive access check ────────────────────────────────────────────────
    access = check_drive_access(token)
    failed = [name for name, ok in access.items() if not ok]
    if failed:
        raise SeedAccessError(
            f"Cannot read the following Drive file(s): {', '.join(failed)}. "
            "Verify that these files are shared with the connected Google account."
        )

    em = effective_month or current_month()
    mp_model.init_mp_tables()
    report: dict = {"effective_month": em, "drive_access": access}

    # ── 2. BOM weights ──────────────────────────────────────────────────────
    try:
        report["bom"] = seed_bom_weights(token, em)
    except Exception as exc:
        raise SeedParseError(f"BOM seed failed: {exc}") from exc

    # ── 3. Pipe routing + staffing ──────────────────────────────────────────
    try:
        report["pipe_routing"] = seed_pipe_routing(token, em)
    except Exception as exc:
        raise SeedParseError(f"Pipe routing seed failed: {exc}") from exc

    # ── 4. Fitting routing + cavity/cycle ───────────────────────────────────
    try:
        report["fitting"] = seed_fitting_routing(token, em)
    except Exception as exc:
        raise SeedParseError(f"Fitting seed failed: {exc}") from exc

    # ── 5. Per-hour rates ────────────────────────────────────────────────────
    try:
        report["per_hour"] = seed_per_hour(token, em)
    except Exception as exc:
        raise SeedParseError(f"Per-hour seed failed: {exc}") from exc

    # ── 6. Compound recipes ─────────────────────────────────────────────────
    try:
        report["compound"] = seed_compound_recipes(token, em)
    except Exception as exc:
        raise SeedParseError(f"Compound seed failed: {exc}") from exc

    # ── 7. Params ───────────────────────────────────────────────────────────
    report["params"] = seed_params(em)

    # ── 8. Provenance recording ─────────────────────────────────────────────
    # Best-effort; failure is logged but never aborts a successful seed run.
    try:
        import mp_seed_provenance as _prov
        drive_token = sheets._get_drive_token()

        # Fetch Drive modifiedTime for every source file (best-effort)
        file_meta: Dict[str, dict] = {}
        if drive_token:
            for _key, _fid in _FILE_IDS.items():
                file_meta[_key] = sheets.drive_file_meta(_fid, drive_token)
            file_meta["june_pipe"] = sheets.drive_file_meta(_JUNE_PIPE_FILE_ID, drive_token)

        def _max_mod(*keys: str) -> Optional[str]:
            times = [file_meta.get(k, {}).get("modified_time") for k in keys]
            return max((t for t in times if t), default=None)

        def _fids(*keys: str) -> str:
            parts = []
            for k in keys:
                fid = _FILE_IDS.get(k) or (_JUNE_PIPE_FILE_ID if k == "june_pipe" else "")
                if fid:
                    parts.append(fid)
            return ",".join(parts)

        def _fnames(*keys: str) -> str:
            return ", ".join(file_meta.get(k, {}).get("file_name") or k for k in keys)

        # Combine machine/routing counts from pipe + fitting seeds
        mc_total = (report.get("pipe_routing", {}).get("machines_loaded", 0)
                    + report.get("fitting", {}).get("machines_loaded", 0))
        rt_total = (report.get("pipe_routing", {}).get("routing_rows_loaded", 0)
                    + report.get("fitting", {}).get("routing_rows_loaded", 0))

        _prov.record_seed(
            "mp_bom_weight",
            source_file_ids=_fids("bom"),
            source_file_names=_fnames("bom"),
            source_modified_time=_max_mod("bom"),
            row_count=report.get("bom", {}).get("rows_loaded", 0),
        )
        _prov.record_seed(
            "mp_machine",
            source_file_ids=_fids("pipe_routing", "fitting"),
            source_file_names=_fnames("pipe_routing", "fitting"),
            source_modified_time=_max_mod("pipe_routing", "fitting"),
            row_count=mc_total,
        )
        _prov.record_seed(
            "mp_routing",
            source_file_ids=_fids("pipe_routing", "fitting"),
            source_file_names=_fnames("pipe_routing", "fitting"),
            source_modified_time=_max_mod("pipe_routing", "fitting"),
            row_count=rt_total,
        )
        _prov.record_seed(
            "mp_per_hour",
            source_file_ids=_fids("per_hour", "june_pipe"),
            source_file_names=_fnames("per_hour", "june_pipe"),
            source_modified_time=_max_mod("per_hour", "june_pipe"),
            row_count=report.get("per_hour", {}).get("rows_loaded", 0),
        )
        _prov.record_seed(
            "mp_compound_recipe",
            source_file_ids=_fids("compound"),
            source_file_names=_fnames("compound"),
            source_modified_time=_max_mod("compound"),
            row_count=report.get("compound", {}).get("rows_loaded", 0),
        )
        report["provenance"] = "recorded"
    except Exception:
        logger.warning("seed_all: provenance recording failed (non-fatal)")
        report["provenance"] = "failed"

    return report


# ---------------------------------------------------------------------------
# reset_defaults
# ---------------------------------------------------------------------------

def reset_defaults(segment: str, table: str, effective_month: str = "") -> dict:
    """Re-seed one table to source defaults for the current effective_month.

    ``table`` must be one of: bom, pipe_routing, fitting, per_hour, compound, params.
    Raises SeedAccessError / SeedParseError on failure (same as seed_all).
    """
    if sheets.is_demo_mode():
        raise SeedAccessError("No connector available.")

    token = sheets._get_access_token()
    if not token:
        raise SeedAccessError("Could not obtain access token.")

    em = effective_month or current_month()
    mp_model.init_mp_tables()

    dispatch = {
        "bom":          lambda: seed_bom_weights(token, em),
        "pipe_routing": lambda: seed_pipe_routing(token, em),
        "fitting":      lambda: seed_fitting_routing(token, em),
        "per_hour":     lambda: seed_per_hour(token, em),
        "compound":     lambda: seed_compound_recipes(token, em),
        "params":       lambda: seed_params(em),
    }
    if table not in dispatch:
        raise ValueError(
            f"Unknown table {table!r}. Must be one of: {list(dispatch)}."
        )
    result = dispatch[table]()
    return {"segment": segment, "table": table, "effective_month": em, **result}
