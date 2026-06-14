"""
Deterministic parsers for the live Google Sheet layouts.

Every figure is read straight from raw cells; stored ratio cells
(M/C Utilization %, Output Efficiency %) are IGNORED and recomputed downstream,
because the live sheets contain erroneous stored percentages.
"""
from __future__ import annotations
import re
from typing import List, Optional
from metrics import Record

_MONTHS3 = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# A per-machine detail tab title, e.g. "M/C-1", "M/C - 12".
DETAIL_TAB_RE = re.compile(r"^M\s*/?\s*C\s*-\s*\d+$", re.IGNORECASE)

# How the per-machine detail headers map onto canonical Record fields.
# Surfaced on the Detected Sources screen so the mapping is auditable.
# Stored ratio cells (Utilization %, Output Efficiency %) are deliberately
# absent here — they are IGNORED and every ratio is recomputed downstream.
MC_DETAIL_FIELD_MAP = [
    {"header": "Ideal Hours", "field": "ideal_hours", "note": "carried forward across month rows"},
    {"header": "Actual Hours", "field": "actual_hours", "note": "run time"},
    {"header": "Actual Output (KG) / Output (KG) / Output (PCS)", "field": "total_count", "note": "raw produced quantity"},
    {"header": "Ideal Output", "field": "ideal_rate", "note": "ideal kg/hr → ideal_output = actual_hrs × rate"},
    {"header": "Rejection (KG) / Rejection", "field": "reject_count", "note": "% reject recomputed, never read"},
    {"header": "Runner", "field": "runner_lumps", "note": "moulding layout only"},
    {"header": "Month label column", "field": "period", "note": "e.g. APR'26 → 2026-04"},
    {"header": "Machine / Mould Machine", "field": "machine", "note": "carried forward; merged label cell"},
]


def num(val) -> float:
    """Coerce a sheet cell to float; handles commas, ₹, %, blanks."""
    if val is None or val == "":
        return 0.0
    s = str(val).strip().replace(",", "").replace("₹", "").replace("%", "")
    if not s:
        return 0.0
    try:
        return float(s.split()[0])
    except (ValueError, IndexError):
        return 0.0


def parse_month_label(s) -> Optional[str]:
    """'APR'26' / 'JUNE'26' / 'JAN'27' / 'APR-26' -> 'YYYY-MM'. Else None."""
    if not s:
        return None
    t = str(s).strip().upper().replace("\u2019", "'")
    m = re.match(r"^([A-Z]{3,9})'?\s*-?\s*(\d{2,4})$", t)
    if not m:
        return None
    mon = _MONTHS3.get(m.group(1)[:3])
    if not mon:
        return None
    yr = int(m.group(2))
    if yr < 100:
        yr += 2000
    return f"{yr:04d}-{mon:02d}"


def is_month_label(s) -> bool:
    return parse_month_label(s) is not None


def parse_mc_detail(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    source_file: str,
    source_tab: str,
) -> List[Record]:
    """Parse a per-machine detail tab (M/C-n) into monthly Records.

    Handles both layouts:
      * Pipe / Garden / HDPE: Ideal Hours, Actual Hours, Actual Output (KG),
        Ideal Output (=ideal kg/hr), [Rejection (KG)].
      * Moulding: Ideal Hours, Actual Hours, Output (KG), Rejection, Runner.

    Merged cells (machine label, mould/material code, ideal hours, ideal rate)
    appear once then blank — we carry them forward across the month rows.
    """
    # Locate header row (contains both ACTUAL and HOUR).
    header_idx = None
    header: List[str] = []
    for i, row in enumerate(values[:6]):
        joined = " ".join(str(c).upper() for c in row)
        if "ACTUAL" in joined and "HOUR" in joined:
            header_idx, header = i, row
            break
    if header_idx is None:
        return []

    U = [str(c).strip().upper() for c in header]

    def find(pred) -> int:
        for idx, h in enumerate(U):
            if pred(h):
                return idx
        return -1

    ideal_h_c = find(lambda h: "IDEAL" in h and "HOUR" in h)
    actual_h_c = find(lambda h: "ACTUAL" in h and "HOUR" in h)
    out_c = find(lambda h: "OUTPUT" in h and "KG" in h and "IDEAL" not in h)
    if out_c < 0:
        out_c = find(lambda h: "OUTPUT" in h and ("PCS" in h or "PC" in h))
    irate_c = find(lambda h: "IDEAL" in h and "OUTPUT" in h)
    rej_c = find(lambda h: "REJECT" in h and "%" not in h and "AGE" not in h)
    run_c = find(lambda h: "RUNNER" in h)
    mc_c = find(lambda h: h == "MACHINE" or "MOULD MACHINE" in h)
    if mc_c < 0:
        mc_c = 1

    # Detect the month column by scanning data rows for month-like cells.
    month_c = -1
    max_c = max(len(r) for r in values[header_idx + 1:header_idx + 9]) if len(values) > header_idx + 1 else 0
    for c in range(max_c):
        if any(
            c < len(row) and is_month_label(row[c])
            for row in values[header_idx + 1: header_idx + 9]
        ):
            month_c = c
            break
    if month_c < 0:
        return []

    mat_c = month_c - 1 if (month_c - 1) not in (mc_c, -1) and (month_c - 1) >= 0 else -1

    recs: List[Record] = []
    carry = {"ih": 0.0, "ir": 0.0, "mc": "", "mat": ""}

    for row in values[header_idx + 1:]:
        def g(c):
            return row[c] if 0 <= c < len(row) else ""

        if str(g(mc_c)).strip():
            carry["mc"] = str(g(mc_c)).strip()
        if mat_c >= 0 and str(g(mat_c)).strip() and not is_month_label(g(mat_c)):
            carry["mat"] = str(g(mat_c)).strip()

        ihv = num(g(ideal_h_c)) if ideal_h_c >= 0 else 0.0
        if ihv > 0:
            carry["ih"] = ihv
        if irate_c >= 0:
            irv = num(g(irate_c))
            if irv > 0:
                carry["ir"] = irv

        ym = parse_month_label(g(month_c))
        if not ym:
            continue  # TOTAL / blank label rows

        ah = num(g(actual_h_c)) if actual_h_c >= 0 else 0.0
        out = num(g(out_c)) if out_c >= 0 else 0.0
        if ah <= 0 and out <= 0:
            continue  # future month — "no data yet", do not fabricate

        rej = num(g(rej_c)) if rej_c >= 0 else 0.0
        runner = num(g(run_c)) if run_c >= 0 else 0.0
        ideal_out = ah * carry["ir"] if carry["ir"] > 0 else 0.0
        label = carry["mc"] or "M/C"
        machine = f"{plant} {label}".strip()

        recs.append(Record(
            grain="monthly",
            period=ym,
            date=ym,
            plant=plant,
            segment=segment,
            unit=unit,
            machine=machine,
            mould=carry["mat"],
            material=carry["mat"],
            actual_hours=ah,
            ideal_hours=carry["ih"],
            total_count=out,
            reject_count=rej,
            runner_lumps=runner,
            ideal_rate=carry["ir"],
            ideal_output=ideal_out,
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


def _day_from_label(s) -> Optional[int]:
    """Extract a day-of-month (1..31) from a per-date column header.

    Handles both live layouts: ``"01-Apr-26"`` (day-first) and ``"Apr, 1"`` /
    ``"Apr,1"`` (month-first). Returns None for non-date cells (merged blanks,
    summary labels), which is how we detect where each per-date column group
    starts.
    """
    if s is None or s == "":
        return None
    t = str(s).strip()
    m = re.match(r"^\s*(\d{1,2})\s*[-/ ]\s*[A-Za-z]{3}", t)  # 01-Apr-26
    if m:
        d = int(m.group(1))
        return d if 1 <= d <= 31 else None
    m = re.search(r"[A-Za-z]{3,9}\.?\s*,?\s*(\d{1,2})\s*$", t)  # Apr, 1
    if m:
        d = int(m.group(1))
        return d if 1 <= d <= 31 else None
    return None


def _long_date_day(s) -> Optional[int]:
    """Day-of-month from a long-format DATE cell.

    Handles all formats found in real workbooks:
    - Month-name first: ``"Jun 1, 2026"``, ``"Jun 1 2026"``
    - Day-first with 3-letter month: ``"01-Jun-26"``, ``"01 Apr 26"``
    - Month-comma: ``"Apr, 1"``, ``"Apr,1"``
    - All-numeric: ``"01/06/2026"`` / ``"01-06-2026"`` (DD/MM, Indian standard)
      and ISO ``"2026-06-01"``
    Returns ``None`` for any non-date cell (blank, label, serial number, etc.).
    """
    d = _day_from_label(s)
    if d is not None:
        return d
    t = str(s).strip()
    # "Jun 1, 2026" or "Jun 1 2026" style (month-name first)
    m = re.match(r"^[A-Za-z]{3,9}\.?\s+(\d{1,2})\b", t)
    if m:
        d = int(m.group(1))
        return d if 1 <= d <= 31 else None
    # All-numeric: "01/06/2026", "01-06-26", "2026-06-01" (ISO).
    # Group 1 = first segment, Group 2 = middle, Group 3 = last.
    m = re.match(r"^\s*(\d{1,4})[/\-](\d{1,2})[/\-](\d{2,4})\s*$", t)
    if m:
        a = int(m.group(1))
        if a > 31:
            # YYYY-MM-DD (ISO): day is the last segment
            d = int(m.group(3))
        else:
            # DD/MM/YYYY or DD-MM-YYYY (Indian convention): day is the first
            d = a
        return d if 1 <= d <= 31 else None
    return None


def _match_header(cell, spec) -> bool:
    """Match a header cell against a ``(mode, token)`` spec.

    mode ∈ {"eq", "contains", "startswith"}; comparison is upper-cased and
    whitespace-stripped so layout quirks (" Wt in Kgs ") still match.
    """
    if not spec:
        return False
    mode, token = spec
    u = str(cell).strip().upper()
    t = str(token).strip().upper()
    if mode == "eq":
        return u == t
    if mode == "contains":
        return t in u
    if mode == "startswith":
        return u.startswith(t)
    return False


def parse_daily_long(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    year_month: str,
    source_file: str,
    source_tab: str,
    date_col=("eq", "DATE"),
    machine_col,
    out_col,
    run_col=None,
    rej_col=None,
    runner_col=None,
    machine_prefix: str = "",
) -> List[Record]:
    """Parse a long (one row per machine per date) daily tab into Records.

    Used for PIPE ``Report-11`` and Moulding ``Report-12`` (the latter lives
    inside the PIPE workbook). Unlike the wide matrix, each physical row is a
    single (machine, date) observation; multiple rows for the same machine/date
    (different item codes) are summed. Column positions are detected from the
    header band by ``(mode, token)`` spec — never assumed — and the stored ratio
    cells are ignored. ``run_col``/``rej_col``/``runner_col`` are optional: a
    layout without run hours (Moulding) simply leaves run hours at zero, which
    downstream marks the plant ``no baseline set`` rather than fabricating a
    figure.
    """
    if not values:
        return []

    # Header row = first row carrying the DATE label that also exposes the
    # machine column somewhere in its header band (header row + any sub-header
    # rows that sit above the first real data row).
    header_idx = -1
    date_c = -1
    for i, row in enumerate(values[:12]):
        for c, v in enumerate(row):
            if _match_header(v, date_col):
                header_idx, date_c = i, c
                break
        if header_idx >= 0:
            break
    if header_idx < 0 or date_c < 0:
        return []

    # First data row: the first row after the header whose DATE cell parses.
    first_data = -1
    for j in range(header_idx + 1, len(values)):
        if _long_date_day(values[j][date_c] if date_c < len(values[j]) else "") is not None:
            first_data = j
            break
    if first_data < 0:
        return []

    band = values[header_idx:first_data]  # header + sub-header rows

    def find_col(spec) -> int:
        if not spec:
            return -1
        for row in band:
            for c, v in enumerate(row):
                if _match_header(v, spec):
                    return c
        return -1

    mc_c = find_col(machine_col)
    out_c = find_col(out_col)
    run_c = find_col(run_col)
    rej_c = find_col(rej_col)
    runner_c = find_col(runner_col)
    if mc_c < 0 or out_c < 0:
        return []

    # Aggregate every (machine, day) — a machine can have several item rows per
    # day. Sum run hours/output/rejection/runner so the day rolls up correctly.
    agg: dict = {}
    for row in values[first_data:]:
        day = _long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        label = str(row[mc_c]).strip() if mc_c < len(row) else ""
        u_label = label.upper()
        # Skip subtotal / summary rows. The exact set covers common header
        # labels; the "TOTAL" substring check catches variants the sheet uses
        # to re-present the same output (e.g. "GRAND TOTAL", "M/C-1 TOTAL",
        # "TOTAL OF JUNE") — these must not be summed alongside detail rows
        # or the plant output is double-counted.
        if not label or u_label in _DAILY_SKIP_LABELS or "TOTAL" in u_label:
            continue
        machine = f"{machine_prefix}{label}".strip()
        key = (machine, day)
        a = agg.get(key)
        if a is None:
            a = {"run": 0.0, "out": 0.0, "rej": 0.0, "runner": 0.0}
            agg[key] = a
        a["run"] += num(row[run_c]) if 0 <= run_c < len(row) else 0.0
        a["out"] += num(row[out_c]) if 0 <= out_c < len(row) else 0.0
        a["rej"] += num(row[rej_c]) if 0 <= rej_c < len(row) else 0.0
        a["runner"] += num(row[runner_c]) if 0 <= runner_c < len(row) else 0.0

    recs: List[Record] = []
    for (machine, day), a in agg.items():
        if a["run"] <= 0 and a["out"] <= 0:
            continue  # nothing produced that day — don't fabricate
        recs.append(Record(
            grain="daily",
            period=year_month,
            date=f"{year_month}-{day:02d}",
            plant=plant,
            segment=segment,
            unit=unit,
            machine=machine,
            actual_hours=a["run"],
            total_count=a["out"],
            reject_count=a["rej"],
            runner_lumps=a["runner"],
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


# Row labels that are subtotals / headers, never real machines.
_DAILY_SKIP_LABELS = {"TOTAL", "PART-1", "PART-2", "%AGE", "%", "MACHINE", "M/C NO.", "S.NO."}


def parse_daily_matrix(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    year_month: str,
    source_file: str,
    source_tab: str,
) -> List[Record]:
    """Parse a wide per-date daily matrix into raw daily-grain Records.

    These tabs ("Report-5" for Pipe/PTMT, "Daily Report" for Garden/HDPE/Tank)
    carry one row per machine and a repeating group of per-date columns. Each
    group starts at a date label (``"Apr, 1"`` / ``"01-Apr-26"``) and contains
    sub-columns for Run Hours / Output / Rejection (order and presence vary by
    layout — detected from the sub-header row, never assumed).

    Only the raw triplet (run hours, output, rejection) is read here; the ideal
    rate and ideal hours-per-day are joined from the monthly grid in
    ``sheets._load_daily`` so daily figures reconcile with the monthly engine.
    """
    if not values:
        return []

    # Locate the date-label header row: the row with the most date-like cells.
    date_row_idx = -1
    best = 1
    for i, row in enumerate(values[:8]):
        cnt = sum(1 for c in row if _day_from_label(c) is not None)
        if cnt > best:
            best, date_row_idx = cnt, i
    if date_row_idx < 0:
        return []
    date_row = values[date_row_idx]
    sub_row = values[date_row_idx + 1] if date_row_idx + 1 < len(values) else []

    # Per-date column groups: (start_col, day). Group spans to the next start.
    starts = [(c, _day_from_label(v)) for c, v in enumerate(date_row)
              if _day_from_label(v) is not None]
    if not starts:
        return []
    first_group_col = starts[0][0]

    def sub(c):
        return str(sub_row[c]).strip().upper() if 0 <= c < len(sub_row) else ""

    groups = []  # (day, run_c, out_c, rej_c)
    for gi, (c0, day) in enumerate(starts):
        c1 = starts[gi + 1][0] if gi + 1 < len(starts) else max(len(date_row), len(sub_row))
        run_c = out_c = rej_c = -1
        for c in range(c0, c1):
            h = sub(c)
            if run_c < 0 and "RUN" in h:
                run_c = c
            elif out_c < 0 and "OUTPUT" in h:
                out_c = c
            elif rej_c < 0 and "REJECT" in h:
                rej_c = c
        groups.append((day, run_c, out_c, rej_c))

    # Machine label column: a header cell == "MACHINE" or containing "M/C NO".
    mc_c = -1
    for r in values[:date_row_idx + 2]:
        for c, v in enumerate(r[:first_group_col]):
            u = str(v).strip().upper()
            if u == "MACHINE" or "M/C NO" in u:
                mc_c = c
                break
        if mc_c >= 0:
            break
    if mc_c < 0:
        mc_c = 1

    recs: List[Record] = []
    for row in values[date_row_idx + 2:]:
        label = str(row[mc_c]).strip() if mc_c < len(row) else ""
        if not label or label.upper() in _DAILY_SKIP_LABELS or label.upper().startswith("PART"):
            continue
        machine = f"{plant} {label}".strip()
        for day, run_c, out_c, rej_c in groups:
            run = num(row[run_c]) if 0 <= run_c < len(row) else 0.0
            out = num(row[out_c]) if 0 <= out_c < len(row) else 0.0
            rej = num(row[rej_c]) if 0 <= rej_c < len(row) else 0.0
            if run <= 0 and out <= 0:
                continue  # day not yet produced — don't fabricate
            recs.append(Record(
                grain="daily",
                period=year_month,
                date=f"{year_month}-{day:02d}",
                plant=plant,
                segment=segment,
                unit=unit,
                machine=machine,
                actual_hours=run,
                total_count=out,
                reject_count=rej,
                source_family=segment,
                source_file=source_file,
                source_tab=source_tab,
            ))
    return recs


def parse_daily_blocks(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    year_month: str,
    source_file: str,
    source_tab: str,
    machine: str,
    date_col=("eq", "DATE"),
) -> List[Record]:
    """Parse ONE per-machine 'block' tab (one tab == one machine) into daily Records.

    Used for GARDEN / HDPE daily workbooks, where each machine has its own tab
    ("MACHINE 2", "MACHINE 3", ...). Layout: a header row carries ``DATE`` plus a
    production column named ``KG`` or ``TOTAL(KG)`` or similar. Some tabs add a
    second sub-header row; others put all column names on the same row as DATE.
    One row per date; an item/size may split a date across several rows, so days
    are summed.

    Only output (any column whose header contains ``KG`` but is not a rate unit)
    and rejection (any ``REJECT*`` column) are read — these tabs carry NO run
    hours, so utilisation/efficiency are left unset (hidden honestly downstream)
    rather than shown as 0%. Columns are detected from the header band, never
    assumed; an unrecognised layout returns ``[]`` (caller reports parse failure,
    distinct from a genuine no-production period).
    """
    if not values:
        return []

    header_idx = -1
    date_c = -1
    for i, row in enumerate(values[:12]):
        for c, v in enumerate(row):
            if _match_header(v, date_col):
                header_idx, date_c = i, c
                break
        if header_idx >= 0:
            break
    if header_idx < 0:
        return []

    # Output and rejection columns are detected from the header band.  Some tabs
    # put all column names on the same row as DATE ("single-row header"); others
    # use a second sub-header row below it.
    # A block tab has a TWO-ROW header band: a label row ("TOTAL") above a unit
    # row ("KG"). Read both as one logical column name so the cumulative output
    # column ("TOTAL" / "KG") is told apart from two decoys that also mention KG:
    #   - a per-metre weight column ("KG" / "MTR")  -> NOT output
    #   - a raw-material consumption column ("RP CONSUMPTION" / "KG") -> NOT output
    # The cumulative-total column is preferred; otherwise the last plain KG column
    # wins, since the date band lists per-unit columns before the running total.
    sub_row = values[header_idx + 1] if header_idx + 1 < len(values) else []
    width = max(len(values[header_idx]), len(sub_row))

    out_c = -1
    rej_c = -1
    out_is_total = False
    for c in range(width):
        head = str(values[header_idx][c]).strip().upper() if c < len(values[header_idx]) else ""
        sub = str(sub_row[c]).strip().upper() if c < len(sub_row) else ""
        combined = f"{head} {sub}".strip()
        if rej_c < 0 and "REJECT" in combined:
            rej_c = c
            continue
        is_rate = any(x in combined for x in ("KG/H", "/KG", "RATE", "PER KG"))
        is_per_mtr = "KG" in head and "MTR" in sub          # per-metre weight
        is_consumption = any(x in combined for x in ("CONSUM", "RP ", "RAW", " RM"))
        if "KG" in combined and not (is_rate or is_per_mtr or is_consumption):
            prefers = "TOTAL" in combined
            if out_c < 0 or (prefers and not out_is_total):
                out_c, out_is_total = c, prefers
    if out_c < 0:
        return []

    # Data starts at the first row after the header band where the date cell
    # holds a valid day number.  Searching forward up to 4 rows handles both
    # 1-row and 2-row header bands without hard-coding an offset.
    data_start = -1
    for i in range(header_idx + 1, min(header_idx + 5, len(values))):
        if _long_date_day(values[i][date_c] if date_c < len(values[i]) else "") is not None:
            data_start = i
            break
    if data_start < 0:
        return []

    agg: dict = {}
    for row in values[data_start:]:
        day = _long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        a = agg.setdefault(day, {"out": 0.0, "rej": 0.0})
        a["out"] += num(row[out_c]) if out_c < len(row) else 0.0
        a["rej"] += num(row[rej_c]) if 0 <= rej_c < len(row) else 0.0

    recs: List[Record] = []
    for day, a in sorted(agg.items()):
        if a["out"] <= 0 and a["rej"] <= 0:
            continue  # day not yet produced — don't fabricate
        recs.append(Record(
            grain="daily",
            period=year_month,
            date=f"{year_month}-{day:02d}",
            plant=plant,
            segment=segment,
            unit=unit,
            machine=machine,
            total_count=a["out"],
            reject_count=a["rej"],
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


def parse_tank_prod(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    year_month: str,
    source_file: str,
    source_tab: str,
) -> List[Record]:
    """Parse the TANK 'PROD. REPORT' (per-item production log) into daily Records.

    The Tank workbook records production per ITEM (size/colour), not per machine:
    one row per (date, item code) with PRODUCTION IN PCS and REJECTION IN PCS.
    There is no machine dimension, so emitted Records carry ``machine=""`` (plant
    + item level only) and ``unit="pcs"``; the item code is kept as the ``mould``
    so it browses as item detail. No run hours exist, so utilisation/efficiency
    stay hidden. Columns are detected from the header; an unrecognised layout
    returns ``[]``.
    """
    if not values:
        return []

    header_idx = -1
    cols: dict = {}
    for i, row in enumerate(values[:12]):
        U = [str(c).strip().upper() for c in row]
        if "DATE" in U and any("ITEM CODE" in u for u in U):
            header_idx = i
            for c, u in enumerate(U):
                if u == "DATE" and "date" not in cols:
                    cols["date"] = c
                elif "ITEM CODE" in u and "item" not in cols:
                    cols["item"] = c
                elif u == "SIZE" and "size" not in cols:
                    cols["size"] = c
                elif u in ("COLOR", "COLOUR") and "color" not in cols:
                    cols["color"] = c
                elif "PRODUCTION IN PC" in u and "out" not in cols:
                    cols["out"] = c
                elif "REJECTION IN PCS" in u and "rej" not in cols:
                    cols["rej"] = c
            break
    if header_idx < 0 or "date" not in cols or "out" not in cols:
        return []

    date_c = cols["date"]
    out_c = cols["out"]
    rej_c = cols.get("rej", -1)
    item_c = cols.get("item", -1)
    size_c = cols.get("size", -1)
    color_c = cols.get("color", -1)

    def g(row, c):
        return row[c] if 0 <= c < len(row) else ""

    agg: dict = {}
    for row in values[header_idx + 1:]:
        day = _long_date_day(g(row, date_c))
        if day is None:
            continue
        item = str(g(row, item_c)).strip()
        size = str(g(row, size_c)).strip()
        color = str(g(row, color_c)).strip()
        label = item or size or "Item"
        key = (day, label)
        a = agg.get(key)
        if a is None:
            a = {"out": 0.0, "rej": 0.0, "size": size, "color": color}
            agg[key] = a
        a["out"] += num(g(row, out_c))
        a["rej"] += num(g(row, rej_c)) if rej_c >= 0 else 0.0

    recs: List[Record] = []
    for (day, label), a in sorted(agg.items()):
        if a["out"] <= 0 and a["rej"] <= 0:
            continue  # nothing produced — don't fabricate
        recs.append(Record(
            grain="daily",
            period=year_month,
            date=f"{year_month}-{day:02d}",
            plant=plant,
            segment=segment,
            unit=unit,
            machine="",          # tank workbook has no machine dimension
            mould=label,
            material=a["color"],
            total_count=a["out"],
            reject_count=a["rej"],
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


def parse_matrix_summary_col(
    values: List[list],
    *,
    header_spec,
    mc_header_spec=("contains", "M/C NO"),
) -> dict:
    """Map ``machine label -> numeric value`` from a per-machine summary column.

    Some daily matrix tabs carry a per-machine MONTHLY figure alongside the
    per-date groups — e.g. PTMT ``Report-5`` has an ``IDEAL HOUR`` column. This
    reads that single summary column keyed by the machine-id column so the value
    can be used as a utilisation baseline. Returns ``{}`` if either column is not
    found (caller then falls back to the next baseline source). Deterministic.
    """
    if not values:
        return {}
    hdr_idx = -1
    val_c = -1
    for i, row in enumerate(values[:10]):
        for c, v in enumerate(row):
            if _match_header(v, header_spec):
                hdr_idx, val_c = i, c
                break
        if hdr_idx >= 0:
            break
    if hdr_idx < 0 or val_c < 0:
        return {}
    mc_c = -1
    for c, v in enumerate(values[hdr_idx]):
        if _match_header(v, mc_header_spec):
            mc_c = c
            break
    if mc_c < 0:
        return {}
    out: dict = {}
    for row in values[hdr_idx + 1:]:
        label = str(row[mc_c]).strip() if mc_c < len(row) else ""
        if not label or label.upper() in _DAILY_SKIP_LABELS:
            continue
        val = num(row[val_c]) if val_c < len(row) else 0.0
        if val > 0:
            out[label] = val
    return out


def grid_total_output(values: List[list]) -> Optional[float]:
    """Sum the OUTPUT columns on the grid's TOTAL row, for reconciliation.

    Returns None if the grid shape isn't recognised.
    """
    sub_idx = None
    for i, row in enumerate(values[:8]):
        ups = [str(c).strip().upper() for c in row]
        if ups.count("HOURS") >= 1 and ups.count("OUTPUT") >= 1:
            sub_idx = i
            break
    if sub_idx is None:
        return None
    out_cols = [c for c, v in enumerate(values[sub_idx]) if str(v).strip().upper() == "OUTPUT"]
    if not out_cols:
        return None

    total_row = None
    for row in values:
        for cell in row[:2]:
            if str(cell).strip().upper() == "TOTAL":
                total_row = row
                break
        if total_row is not None:
            break
    if total_row is None:
        return None

    vals = [num(total_row[c]) if c < len(total_row) else 0.0 for c in out_cols]
    if not vals:
        return None

    # Some family grids carry a trailing grand-total machine group, so one
    # OUTPUT column equals the sum of the per-machine columns. Detect it and
    # use that single grand total instead of double-counting it.
    largest = max(vals)
    rest = sum(vals) - largest
    if largest > 0 and rest > 0 and abs(largest - rest) / largest <= 0.01:
        return largest

    return sum(vals)
