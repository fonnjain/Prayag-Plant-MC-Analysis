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
    mc_header_spec=None,
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

    # Machine label column. When the layout names its canonical machine-id header
    # (e.g. HDPE "MACHINE"), match it exactly via ``mc_header_spec`` so this daily
    # parser and the in-sheet summary reader (``parse_matrix_summary_col``) always
    # select the SAME column — an alias column to its left can never be picked by
    # accident, which would silently break the sheet_rate/sheet_hours key join.
    # Otherwise fall back to the generic heuristic ("MACHINE" or "M/C NO").
    mc_c = -1
    for r in values[:date_row_idx + 2]:
        for c, v in enumerate(r[:first_group_col]):
            if mc_header_spec is not None:
                hit = _match_header(v, mc_header_spec)
            else:
                u = str(v).strip().upper()
                hit = u == "MACHINE" or "M/C NO" in u
            if hit:
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
            if run <= 0 and out <= 0 and rej <= 0:
                # Day not yet produced — don't fabricate. But a day carrying ONLY
                # rejection is real data, not a blank: PTMT/HDPE matrices append a
                # single monthly "Actual Rejection Weight" column inside the LAST
                # date-group's span, so the whole month's rejection lands on the
                # last day's row. A machine that didn't run on that last day still
                # owns that rejection — skipping the row here would silently drop
                # it (the machine-month reject would read 0 even though the sheet
                # has it), so keep any row whose rejection is non-zero.
                continue
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
    one row per (date, item code) reporting the same run in THREE units —
    PRODUCTION IN LTR., IN PCS. and IN KG. Litres is Tank's primary headline unit
    (the 'TANK Ltr. Summary'), so the emitted Record carries ``unit="Ltr"`` and
    ``total_count`` from the litre column, while pcs/kg are kept in
    ``secondary_counts`` (display-only, never summed or compared with the kg
    plants). If a workbook lacks a litre column the parser falls back to pcs then
    kg as the primary and sets ``unit`` accordingly. There is no machine
    dimension, so emitted Records carry ``machine=""`` and the item code as the
    ``mould`` so it browses as item detail. No run hours exist, so
    utilisation/efficiency stay hidden. Columns are detected from the header; an
    unrecognised layout returns ``[]``.
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
                elif "SIZE" in u and "size" not in cols:
                    cols["size"] = c    # "SIZE (LTR.)" must NOT be read as litres output
                elif u in ("COLOR", "COLOUR") and "color" not in cols:
                    cols["color"] = c
                elif "REJECT" in u:
                    if "LTR" in u and "rej_ltr" not in cols:
                        cols["rej_ltr"] = c
                    elif "PC" in u and "rej_pcs" not in cols:
                        cols["rej_pcs"] = c
                    elif "KG" in u and "rej_kg" not in cols:
                        cols["rej_kg"] = c
                elif "LTR" in u and "ltr" not in cols:
                    cols["ltr"] = c
                elif "PC" in u and "pcs" not in cols:
                    cols["pcs"] = c
                elif "KG" in u and "kg" not in cols:
                    cols["kg"] = c
            break
    if header_idx < 0 or "date" not in cols:
        return []
    date_c = cols["date"]

    def g(row, c):
        return row[c] if 0 <= c < len(row) else ""

    def _col_total(c):
        if c is None or c < 0:
            return 0.0
        return sum(num(g(row, c)) for row in values[header_idx + 1:]
                   if _long_date_day(g(row, date_c)) is not None)

    # Primary output unit by precedence: litres → pcs → kg, but only a column that
    # ACTUALLY carries data. Some TANK workbooks publish the litres column header
    # yet log production only in pcs/kg (the litres column left blank); picking an
    # all-empty litres column as primary would silently drop every row, so fall
    # through to the next unit that has real values. Never fabricates a figure.
    out_c = prim_unit = prim_key = None
    for _k, _lbl in (("ltr", "Ltr"), ("pcs", "pcs"), ("kg", "kg")):
        if _k in cols and _col_total(cols[_k]) > 0:
            out_c, prim_unit, prim_key = cols[_k], _lbl, _k
            break
    if out_c is None:
        return []     # no unit column carries production — genuinely empty

    # Reject in the SAME unit as the primary output (never a cross-unit reject %).
    rej_c = cols.get(f"rej_{prim_key}", -1)
    if rej_c < 0:
        rej_c = cols.get("rej_ltr", cols.get("rej_pcs", cols.get("rej_kg", -1)))
    item_c = cols.get("item", -1)
    size_c = cols.get("size", -1)
    color_c = cols.get("color", -1)
    # Secondary (non-primary) production unit columns, kept for display only.
    sec_cols = {u: cols[u] for u in ("ltr", "pcs", "kg") if u in cols and u != prim_key}

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
            a = {"out": 0.0, "rej": 0.0, "size": size, "color": color,
                 "sec": {u: 0.0 for u in sec_cols}}
            agg[key] = a
        a["out"] += num(g(row, out_c))
        a["rej"] += num(g(row, rej_c)) if rej_c >= 0 else 0.0
        for u, c in sec_cols.items():
            a["sec"][u] += num(g(row, c))

    # Display labels for the secondary units (pcs → "pcs", kg → "kg").
    _sec_label = {"pcs": "pcs", "kg": "kg", "ltr": "Ltr"}
    recs: List[Record] = []
    for (day, label), a in sorted(agg.items()):
        if a["out"] <= 0 and a["rej"] <= 0:
            continue  # nothing produced — don't fabricate
        secondary = {_sec_label[u]: v for u, v in a["sec"].items() if v}
        recs.append(Record(
            grain="daily",
            period=year_month,
            date=f"{year_month}-{day:02d}",
            plant=plant,
            segment=segment,
            unit=prim_unit,
            secondary_counts=secondary,
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


def parse_pipe_run5(values: List[list]) -> dict:
    """Map ``machine label -> (ideal_run_hour_per_day, run_days, run_hours)`` from
    PIPE ``Report-5``.

    Report-5 is a multi-family monthly summary whose header is SPLIT across two
    rows: ``Ideal Run Hour Per Day`` and ``Total Run Days`` sit one row above the
    ``MACHINE`` / ``RUN HOURS`` headers, so the same-row
    :func:`parse_matrix_summary_col` cannot read it. This scans the first few rows
    for each header independently, then reads per machine row a dict of
    ``{per_day, run_days, run_hours, output, reject, ideal_out}``:

      * **Col D — Ideal Run Hour Per Day** is per machine TYPE (22 for pipe/moulding
        lines, 12 for grinders/pulverizers); it is read PER ROW, never assumed a
        constant.
      * **Col E — Total Run Days** is the data-driven count of days the machine ran.
      * **Col F — Run Hours** is the machine's actual run hours for the month.

    Utilisation = Run Hours / (Ideal Run Hour Per Day × Total Run Days) — a RUN-DAY
    basis, not calendar days. ``TOTAL`` / blank rows are skipped (their Col D is a
    column sum, not a machine baseline). The sheet lists Mixer / Pipe / Moulding /
    Grinder / Pulverizer families; every machine row with a positive Col D is
    returned and the caller joins to the daily machines it actually has. Rows with
    NO daily tab (grinders, pulverizers, sockets, mixers) are synthesised into
    month-grain records by the caller so they still surface. Returns ``{}`` if the
    machine or ideal header is absent. Deterministic; no network.
    """
    if not values:
        return {}
    val_c = days_c = hrs_c = mc_c = -1
    last_hdr = 0
    for i, row in enumerate(values[:8]):
        for c, v in enumerate(row):
            s = str(v).strip().lower()
            if not s:
                continue
            if val_c < 0 and "ideal run hour" in s:
                val_c, last_hdr = c, max(last_hdr, i)
            if days_c < 0 and "run days" in s:
                days_c, last_hdr = c, max(last_hdr, i)
            # "RUN HOURS" (plural) is a distinct column from "Ideal Run Hour Per
            # Day"; match it exactly so the ideal label never steals this slot.
            if hrs_c < 0 and s in ("run hours", "running hours"):
                hrs_c, last_hdr = c, max(last_hdr, i)
            if mc_c < 0 and s == "machine":
                mc_c, last_hdr = c, max(last_hdr, i)
    if val_c < 0 or mc_c < 0:
        return {}
    # OUTPUT (KG), REJ (KG) and Ideal Output Per Hour sit immediately to the right
    # of RUN HOURS (cols G/H/I after F). They are read by fixed offset from RUN
    # HOURS because the sheet carries THREE "output"-named headers (Output, Ideal
    # Output Per Hour, Avg per hour output) that a text match cannot disambiguate.
    # Ideal Output Per Hour can be BLANK (e.g. Grinder-3, whose in-sheet efficiency
    # is #DIV/0!) — it is returned as 0.0 so the caller hides efficiency rather than
    # dividing by zero.
    out_c = hrs_c + 1 if hrs_c >= 0 else -1
    rej_c = hrs_c + 2 if hrs_c >= 0 else -1
    io_c = hrs_c + 3 if hrs_c >= 0 else -1
    out: dict = {}
    for row in values[last_hdr + 1:]:
        label = str(row[mc_c]).strip() if mc_c < len(row) else ""
        if not label or label.upper() in _DAILY_SKIP_LABELS or "TOTAL" in label.upper():
            continue
        per_day = num(row[val_c]) if val_c < len(row) else 0.0
        if per_day <= 0:
            continue
        out[label] = {
            "per_day": per_day,
            "run_days": num(row[days_c]) if (0 <= days_c < len(row)) else 0.0,
            "run_hours": num(row[hrs_c]) if (0 <= hrs_c < len(row)) else 0.0,
            "output": num(row[out_c]) if (0 <= out_c < len(row)) else 0.0,
            "reject": num(row[rej_c]) if (0 <= rej_c < len(row)) else 0.0,
            "ideal_out": num(row[io_c]) if (0 <= io_c < len(row)) else 0.0,
        }
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


# ===========================================================================
# NEW PARSERS — Group-of-Moulding, Tank Annual, Segment Labour
# ===========================================================================

# ---------------------------------------------------------------------------
# Group-of-Moulding grid parser
# ---------------------------------------------------------------------------
# GOM tab layout: machine codes (C-150-1, C-200-1 …) across the top row;
# month labels (APR'26, MAY'26 …) down column B; paired HOURS / OUTPUT cols
# per machine. The tab also contains a "SUMMARY-1" sub-tab with band totals.
# We parse at the machine level and assign tonnage bands via sources.gom_band.

def _gom_band_from_prefix(label: str) -> str:
    """Resolve tonnage band from a GOM machine column header label."""
    import sources as _sources
    return _sources.gom_band(label)


def parse_gom_grid(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    source_file: str,
    source_tab: str,
) -> List[Record]:
    """Parse a Group-of-Moulding grid tab (machine cols × month rows).

    Layout: row 0 = machine code headers (C-150-1 … C-450-5); col 0 or A =
    row labels (APR'26, TOTAL …). Each machine occupies 2 columns: HOURS then
    OUTPUT. Paired columns are detected by the sub-header row (row 1) which
    carries 'HOURS' / 'OUTPUT' labels under each machine code. Months are
    detected by parse_month_label on col B (or the leftmost non-empty col).

    Returns one Record per (machine, month) with actual_hours + total_count.
    Ideal hours are left at 0 (the 500h sheet placeholder is suppressed by the
    existing baseline rule — utilisation stays hidden, raw hours still publish).
    """
    if not values:
        return []

    # Find the machine-header row (contains "C-150" or "C-200" etc.)
    mc_row_idx = -1
    for i, row in enumerate(values[:5]):
        joined = " ".join(str(c).strip().upper() for c in row)
        if "C-150" in joined or "C-200" in joined or "C-275" in joined:
            mc_row_idx = i
            break
    if mc_row_idx < 0:
        return []

    mc_row = [str(c).strip() for c in values[mc_row_idx]]

    # Sub-header row immediately below machine row: HOURS / OUTPUT labels.
    sub_row_idx = mc_row_idx + 1
    sub_row = [str(c).strip().upper() for c in values[sub_row_idx]] if sub_row_idx < len(values) else []

    # Build (col_idx, machine_label, "hours"|"output") mapping.
    # Carry the last machine label forward for paired blank header cols.
    col_map: list = []  # list of (col, machine, "hours"|"output")
    last_mc = ""
    for c, label in enumerate(mc_row):
        if label and label.upper() not in ("", "MACHINE", "MONTH", "MONTHS"):
            u = label.upper()
            if u.startswith("C-"):
                last_mc = label
        if not last_mc:
            continue
        sub = sub_row[c] if c < len(sub_row) else ""
        if "HOUR" in sub:
            col_map.append((c, last_mc, "hours"))
        elif "OUTPUT" in sub:
            col_map.append((c, last_mc, "output"))

    if not col_map:
        return []

    # Find the month column (first col whose data rows hold month labels).
    month_c = 0
    for c in range(min(3, len(mc_row))):
        if any(
            parse_month_label(values[r][c] if c < len(values[r]) else "")
            for r in range(sub_row_idx + 1, min(sub_row_idx + 5, len(values)))
        ):
            month_c = c
            break

    # Aggregate per (machine, month).
    agg: dict = {}  # (machine, ym) -> {hours, output}
    for row in values[sub_row_idx + 1:]:
        ym = parse_month_label(row[month_c] if month_c < len(row) else "")
        if not ym:
            continue
        for col, mc_label, kind in col_map:
            val = num(row[col]) if col < len(row) else 0.0
            if val <= 0:
                continue
            key = (mc_label, ym)
            a = agg.setdefault(key, {"hours": 0.0, "output": 0.0})
            a[kind] += val

    recs: List[Record] = []
    for (mc_label, ym), a in agg.items():
        if a["hours"] <= 0 and a["output"] <= 0:
            continue
        band = _gom_band_from_prefix(mc_label)
        machine = f"GOM {mc_label}".strip()
        recs.append(Record(
            grain="monthly",
            period=ym,
            date=ym,
            plant=plant,
            segment=segment,
            unit=unit,
            machine=machine,
            actual_hours=a["hours"],
            total_count=a["output"],
            # ideal_hours deliberately left 0: the sheet's 500h/machine is a
            # placeholder, not a real baseline. Utilisation is suppressed
            # downstream (ideal_source="none") — raw hours still publish.
            ideal_hours=0.0,
            ideal_source="none",
            tonnage_band=band,
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


# ---------------------------------------------------------------------------
# Tank annual summary parsers (25-26 SUMMARY (LTR) and 26-27 Sheet1)
# ---------------------------------------------------------------------------

def parse_tank_annual_2526(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    source_file: str,
    source_tab: str,
    location: str = "",
) -> List[Record]:
    """Parse Tank annual summary tab in the 25-26 layout (SUMMARY (LTR) tab).

    Layout: row 0 = month column headers (APR, MAY … MAR or APR'25 …);
    each item occupies 2 rows: Production row then Rejection row.
    The first column carries the item description.
    Returns one Record per (item, month) with production + rejection.
    """
    if not values:
        return []

    # Find the header row: the one with the most month-like labels.
    header_idx = -1
    best = 1
    for i, row in enumerate(values[:6]):
        cnt = sum(1 for c in row if parse_month_label(c) is not None
                  or str(c).strip().upper()[:3] in _MONTHS3)
        if cnt > best:
            best, header_idx = cnt, i

    if header_idx < 0:
        # Try: look for a row that contains 'APR' somewhere
        for i, row in enumerate(values[:8]):
            joined = " ".join(str(c).strip().upper() for c in row)
            if "APR" in joined and ("MAY" in joined or "JUN" in joined):
                header_idx = i
                break
    if header_idx < 0:
        return []

    header_row = values[header_idx]

    # Build month columns: (col, YYYY-MM).
    # The header may use "APR", "APR'25", "APR 25" etc.
    month_cols: list = []
    for c, cell in enumerate(header_row):
        s = str(cell).strip().upper()
        ym = parse_month_label(s)
        if ym is None:
            # Try bare 3-letter month name — assume current FY year context.
            mon = _MONTHS3.get(s[:3])
            if mon:
                # 25-26 FY: Apr-Dec = 2025, Jan-Mar = 2026
                yr = 2025 if mon >= 4 else 2026
                ym = f"{yr:04d}-{mon:02d}"
        if ym:
            month_cols.append((c, ym))

    if not month_cols:
        return []

    # Parse data rows. Items are in pairs: PRODUCTION / REJECTION.
    # The item description is in col 0 (or first non-empty col).
    recs: List[Record] = []
    i = header_idx + 1
    while i < len(values):
        row = values[i]
        item_label = str(row[0]).strip() if row else ""
        # Skip blank or header-like rows.
        if not item_label or item_label.upper() in ("", "ITEM", "DESCRIPTION", "S.NO", "S.NO."):
            i += 1
            continue
        u_label = item_label.upper()
        if "TOTAL" in u_label or "GRAND" in u_label:
            i += 1
            continue

        # Check if this row is a PRODUCTION row (look ahead for REJECTION).
        prod_row = row
        rej_row = values[i + 1] if i + 1 < len(values) else []
        rej_label = str(rej_row[0]).strip().upper() if rej_row else ""

        for col, ym in month_cols:
            prod = num(prod_row[col]) if col < len(prod_row) else 0.0
            rej = num(rej_row[col]) if ("REJECT" in rej_label and col < len(rej_row)) else 0.0
            if prod <= 0 and rej <= 0:
                continue
            recs.append(Record(
                grain="monthly",
                period=ym,
                date=ym,
                plant=plant,
                segment=segment,
                unit=unit,
                machine="",
                mould=item_label,
                total_count=prod,
                reject_count=rej,
                location=location,
                source_family=segment,
                source_file=source_file,
                source_tab=source_tab,
            ))

        # Advance: if we consumed a PRODUCTION+REJECTION pair, skip 2; else 1.
        if "REJECT" in rej_label:
            i += 2
        else:
            i += 1

    return recs


def parse_tank_annual_2627(
    values: List[list],
    *,
    plant: str,
    segment: str,
    unit: str,
    source_file: str,
    source_tab: str,
    location: str = "",
) -> List[Record]:
    """Parse Tank annual summary tab in the 26-27 layout (Sheet1).

    Layout: row 2 = header (S.NO. / CODE / LTR. / DESCRIPTION / COLOUR /
    TOTAL PCS / then per-month pairs Production / Rejection).
    Row 3+ = one item per row with monthly production + rejection values.
    Month headers are in the header row, e.g. APR'26, MAY'26 …
    """
    if not values:
        return []

    # Find the header row: contains 'DESCRIPTION' or 'CODE' AND month labels.
    header_idx = -1
    for i, row in enumerate(values[:8]):
        joined = " ".join(str(c).strip().upper() for c in row)
        if ("DESCRIPTION" in joined or "CODE" in joined) and "APR" in joined:
            header_idx = i
            break
    if header_idx < 0:
        return []

    header_row = values[header_idx]

    # Build (col, YYYY-MM, "prod"|"rej") mapping from the header.
    # Month pairs: each month appears twice — Production then Rejection.
    month_col_map: list = []  # (col, ym, "prod"|"rej")
    last_ym = None
    prod_seen = False
    for c, cell in enumerate(header_row):
        s = str(cell).strip().upper()
        ym = parse_month_label(s)
        if ym:
            last_ym = ym
            prod_seen = False
            continue
        if last_ym:
            if "PROD" in s or s in ("", " ") and not prod_seen:
                month_col_map.append((c, last_ym, "prod"))
                prod_seen = True
            elif "REJECT" in s:
                month_col_map.append((c, last_ym, "rej"))
                prod_seen = False
                last_ym = None  # consumed the pair

    if not month_col_map:
        # Fallback: header row has month labels directly in col headers;
        # look for month label columns then assume next col = rejection.
        for c, cell in enumerate(header_row):
            ym = parse_month_label(str(cell).strip())
            if ym:
                month_col_map.append((c, ym, "prod"))
                if c + 1 < len(header_row):
                    month_col_map.append((c + 1, ym, "rej"))

    if not month_col_map:
        return []

    # Find description column (first col with "DESCRIPTION" or "CODE").
    desc_c = 0
    for c, cell in enumerate(header_row):
        s = str(cell).strip().upper()
        if "DESCRIPTION" in s or s == "CODE":
            desc_c = c
            break

    # Parse data rows.
    prod_by: dict = {}  # (item, ym) -> {prod, rej}
    for row in values[header_idx + 1:]:
        item_label = str(row[desc_c]).strip() if desc_c < len(row) else ""
        if not item_label:
            continue
        u = item_label.upper()
        if "TOTAL" in u or "GRAND" in u or u in ("ITEM", "DESCRIPTION"):
            continue
        for col, ym, kind in month_col_map:
            val = num(row[col]) if col < len(row) else 0.0
            if val <= 0:
                continue
            key = (item_label, ym)
            a = prod_by.setdefault(key, {"prod": 0.0, "rej": 0.0})
            a[kind] += val

    recs: List[Record] = []
    for (item_label, ym), a in prod_by.items():
        if a["prod"] <= 0 and a["rej"] <= 0:
            continue
        recs.append(Record(
            grain="monthly",
            period=ym,
            date=ym,
            plant=plant,
            segment=segment,
            unit=unit,
            machine="",
            mould=item_label,
            total_count=a["prod"],
            reject_count=a["rej"],
            location=location,
            source_family=segment,
            source_file=source_file,
            source_tab=source_tab,
        ))
    return recs


# ---------------------------------------------------------------------------
# Segment Labour parser (UNIT-1 / UNIT-2 / UNIT-3 tabs)
# ---------------------------------------------------------------------------
# Layout (both FYs): each tab = one unit.
# Row 0 = blank; Row 1 = header (SEGMENT / MONTH / Labour / Solar / Power /
# Total); Row 2 = TOTAL row; Rows 3+ = monthly data, with SEGMENT in col B
# as a merged cell carried across its months.

def parse_segment_labour(
    values: List[list],
    *,
    unit_label: str,
    source_file: str,
    source_tab: str,
) -> List[dict]:
    """Parse one UNIT tab of the Segment Labour workbook.

    Returns a list of dicts:
      {unit, segment, month (YYYY-MM), labour, solar, power, total}

    We don't emit Records here (labour cost is not production data); the
    app builds its own view table from these dicts.
    """
    if not values:
        return []

    # Find header row (contains SEGMENT or MONTH and LABOUR).
    header_idx = -1
    for i, row in enumerate(values[:5]):
        joined = " ".join(str(c).strip().upper() for c in row)
        if ("SEGMENT" in joined or "MONTH" in joined) and "LABOUR" in joined:
            header_idx = i
            break
    if header_idx < 0:
        return []

    header = [str(c).strip().upper() for c in values[header_idx]]

    def hcol(needle: str) -> int:
        for c, h in enumerate(header):
            if needle in h:
                return c
        return -1

    seg_c = hcol("SEGMENT")
    month_c = hcol("MONTH")
    labour_c = hcol("LABOUR")
    solar_c = hcol("SOLAR")
    power_c = hcol("POWER")
    total_c = hcol("TOTAL")

    if month_c < 0:
        return []

    rows_out: list = []
    carry_seg = ""
    for row in values[header_idx + 1:]:
        def g(c):
            return row[c] if 0 <= c < len(row) else ""

        # Carry segment label forward (merged cells).
        sv = str(g(seg_c)).strip() if seg_c >= 0 else ""
        if sv and sv.upper() not in ("", "TOTAL", "GRAND TOTAL", "SEGMENT"):
            carry_seg = sv

        ym = parse_month_label(g(month_c))
        if not ym:
            continue

        rows_out.append({
            "unit": unit_label,
            "segment": carry_seg,
            "month": ym,
            "labour": num(g(labour_c)),
            "solar": num(g(solar_c)) if solar_c >= 0 else 0.0,
            "power": num(g(power_c)) if power_c >= 0 else 0.0,
            "total": num(g(total_c)) if total_c >= 0 else 0.0,
        })
    return rows_out


# ---------------------------------------------------------------------------
# Per-workbook "Index" tab — authoritative tab metadata.
#
# Every PTMT and Pipe & Fitting monthly workbook ships an Index sheet whose rows
# describe each Report-N tab:  S.No | Reports | Frequency | Types | Include |
# By Whom | Action Taken By  (usually with a leading blank column A).
#
# The same bare report NUMBER means different things across workbooks
# (Report-12 = Wastage in PTMT but Moulding M/C production in Pipe), so the
# dashboard must key tabs by DESCRIPTION, not number. This parser turns the
# Index into structured, description-keyed metadata. Real-world quirks handled:
#   * leading "N." / blank column A,
#   * Frequency is a MERGED cell — a blank inherits the value above,
#   * a continuation row (blank "Reports" cell) is a SUB-BLOCK of the report
#     above (Report-5 lists Pipe M/C / Mixer,Grinder,Pulverizer / Moulding M/C
#     as three owners — this is why Pulverizer was missing),
#   * a frequency token can be mis-typed into the Types column (PTMT Report-12).
# Pure; no network.
# ---------------------------------------------------------------------------
_INDEX_HEADERS = {
    "sno": "sno", "sno.": "sno", "s.no": "sno", "s.no.": "sno", "s. no.": "sno",
    "s no": "sno", "s. no": "sno",
    "reports": "reports", "report": "reports",
    "frequency": "frequency",
    "types": "types", "type": "types",
    "include": "include",
    "by whom": "by_whom", "bywhom": "by_whom",
    "action taken by": "action_taken_by",
}

# Recognised frequency tokens (longest first so "every monday & thursday" wins
# over "every monday"). Used both for the Frequency column and the quirk where a
# frequency leaks into the Types column.
_FREQ_TOKENS = [
    "every monday & thursday", "every monday and thursday",
    "every monday", "every week", "weekly", "fortnightly",
    "monthly", "daily", "quarterly", "yearly", "annually",
]


def _index_norm(s) -> str:
    """Lower-case, collapse whitespace; '' for blank/None."""
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _index_freq_class(freq: str) -> tuple[str, bool]:
    """(frequency_class, sliceable) from a raw frequency string.

    Only ``daily`` tabs hold true per-day rows and therefore support
    daily / weekly / month-to-date slicing. ``weekly`` ("Every Monday") and
    ``monthly`` tabs are PERIOD SNAPSHOTS — they must never be summed as if they
    were daily. Unknown/blank → not sliceable (treated as a snapshot).
    """
    f = _index_norm(freq)
    if "daily" in f:
        return "daily", True
    if "monday" in f or "thursday" in f or "week" in f or "fortnight" in f:
        return "weekly", False
    if "month" in f:
        return "monthly", False
    if "quarter" in f:
        return "quarterly", False
    if "year" in f or "annual" in f:
        return "yearly", False
    return "", False


def _index_units(desc: str) -> tuple[list[str], str]:
    """(units, primary_unit) inferred from a report's description text.

    'in KG & Pcs' → both kg+pcs (pick KG for kg metrics); 'Ltr' → Ltr (Tank);
    Reports marked only 'Pcs' → pcs. Empty when the description names no unit.
    """
    d = _index_norm(desc)
    units: list[str] = []
    if "ltr" in d or "litre" in d or "liter" in d:
        units.append("Ltr")
    if re.search(r"\bkg\b", d) or "in kgs" in d or "wt in kg" in d:
        units.append("kg")
    if re.search(r"\bpcs\b", d) or "pieces" in d or "in pcs" in d:
        units.append("pcs")
    primary = units[0] if units else ""
    # Prefer kg as the primary metric unit when a tab carries both kg & pcs.
    if "kg" in units:
        primary = "kg"
    if "Ltr" in units:
        primary = "Ltr"
    return units, primary


def parse_index(rows: List[list]) -> List[dict]:
    """Parse an Index tab value-matrix → list of report-metadata dicts.

    Each dict: ``report`` (raw, e.g. "Report-5"), ``report_key`` (normalised,
    e.g. "report-5"), ``sno``, ``frequency``, ``frequency_class``,
    ``sliceable``, ``types``, ``include``, ``description`` (Types + Include),
    ``owner`` (By Whom), ``action_taken_by``, ``units``, ``unit`` (primary),
    ``sub_blocks`` (continuation rows: [{include, owner, types}]).
    Returns [] when no recognisable header row is found.
    """
    if not rows:
        return []

    # Locate the header row: the first row carrying both "Reports" and
    # "Frequency" cells (column A is often blank, so scan every cell).
    hdr_i = -1
    col: dict[str, int] = {}
    for i, row in enumerate(rows[:8]):
        m: dict[str, int] = {}
        for j, cell in enumerate(row):
            key = _INDEX_HEADERS.get(_index_norm(cell))
            if key and key not in m:
                m[key] = j
        if "reports" in m and "frequency" in m:
            hdr_i, col = i, m
            break
    if hdr_i < 0:
        return []

    def cell(row: list, key: str) -> str:
        j = col.get(key, -1)
        if 0 <= j < len(row):
            return str(row[j]).strip()
        return ""

    reports: List[dict] = []
    last_freq = ""
    for row in rows[hdr_i + 1:]:
        if not any(str(c).strip() for c in row):
            continue
        rep = cell(row, "reports")
        types = cell(row, "types")
        include = cell(row, "include")
        by_whom = cell(row, "by_whom")
        action = cell(row, "action_taken_by")

        # Continuation row (blank "Reports") → a sub-block / extra owner of the
        # report above. Report-5 uses these for its three machine families.
        if not rep:
            if reports and (include or by_whom or types):
                reports[-1]["sub_blocks"].append({
                    "include": include, "owner": by_whom, "types": types,
                })
            continue

        # Frequency: prefer the Frequency column; inherit the merged value above
        # when blank; finally fall back to a frequency token mis-typed into the
        # Types column (PTMT Report-12 carries "Every Monday" there).
        freq = cell(row, "frequency")
        if not freq:
            blob = _index_norm(f"{types} {include}")
            for tok in _FREQ_TOKENS:
                if tok in blob:
                    freq = tok.title()
                    break
        if freq:
            last_freq = freq
        else:
            freq = last_freq

        # If the frequency came from the Types column, don't double-count it as
        # part of the description.
        if _index_norm(types) in {_index_norm(freq)} | {t for t in _FREQ_TOKENS}:
            types = ""

        desc = " — ".join([p for p in (types, include) if p])
        fclass, sliceable = _index_freq_class(freq)
        units, primary = _index_units(desc)
        reports.append({
            "report": rep,
            # Space-insensitive key so "Report-8 (A)" and "Report-8(A)" map to
            # one identity — prevents false added/removed change-flags across
            # months from cosmetic spacing edits in the Index.
            "report_key": re.sub(r"\s+", "", _index_norm(rep)),
            "sno": cell(row, "sno"),
            "frequency": freq,
            "frequency_class": fclass,
            "sliceable": sliceable,
            "types": types,
            "include": include,
            "description": desc,
            "owner": by_whom,
            "action_taken_by": action,
            "units": units,
            "unit": primary,
            "sub_blocks": [],
        })
    return reports


# ---------------------------------------------------------------------------
# Compound mixer-logbook parsers (Pipe & Fitting daily workbook)
# ---------------------------------------------------------------------------
# Each pipe/fitting compound type has its own "Mixer Logbook - Daily" tab
# (Report-6=CPVC, 7=UPVC, 8(A)=AGRI, 8(B)=SWR, 9=UPVC Fittings,
# 10=SWR/AGRI Fittings, plus the separate FC tab). The layout is:
#   title rows -> header row (Date | ...chems... | Pulvizer X |
#   Total Batch Weight in KG | Total Material out of Mixer |
#   Total Compound given to Pipe Plant/Fitting | Total Weight Loss at Mixer |
#   Av. Weight Loss %age | Compound Floor Stock) -> a chemical-NAME sub-row ->
#   a TOTAL row -> WEEK sub-totals + daily date rows.
# CPVC Fittings (CG 122) uses a purchase / issue / balance layout instead.
#
# Every figure is summed from the per-day rows (daily-first, verified to
# reconcile to the sheet's own TOTAL row); the TOTAL row is a reconciliation
# reference only. Weight-loss % is always recomputed (loss / batch), never read.

_LOGBOOK_DATE_FORMATS = (
    "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%B-%Y",
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y",
)


def _cnorm(s) -> str:
    """Collapse whitespace, lower-case; '' for blank/None."""
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def _logbook_date(cell):
    """Parse a mixer-logbook date label -> datetime.date, else None."""
    import datetime as _dt
    s = str(cell or "").strip()
    if not s:
        return None
    for fmt in _LOGBOOK_DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_mixer_logbook(rows: List[list]) -> Optional[dict]:
    """Parse a mixer-logbook compound tab -> daily balance + chemical breakdown.

    Returns ``None`` if no recognisable header row is found. The result:
      ``opening``        month opening stock (kg)
      ``given_label``    the sheet's own "...given to Pipe Plant/Fitting" label
      ``days``           list of per-day dicts (date, day, batch, material,
                         given, loss, closing, pulvizer, chems{name: kg})
      ``chem_names``     chemical column names in sheet order (excl. pulvizer)
      ``pulvizer_names`` pulvizer column names
      ``total_chems``    summed kg per chemical across all days
    """
    if not rows:
        return None
    hdr_i = None
    for i, row in enumerate(rows[:10]):
        cells = [_cnorm(c) for c in row]
        if any(c == "date" for c in cells) and any("total batch weight" in c for c in cells):
            hdr_i = i
            break
    if hdr_i is None:
        return None
    raw_hdr = rows[hdr_i]
    hdr = [_cnorm(c) for c in raw_hdr]
    sub = rows[hdr_i + 1] if hdr_i + 1 < len(rows) else []

    def find(*subs):
        for j, c in enumerate(hdr):
            for s in subs:
                if s in c:
                    return j
        return None

    col_date = next((j for j, c in enumerate(hdr) if c == "date"), 1)
    col_batch = find("total batch weight")
    col_material = find("total material out")
    col_given = find("total compound given")
    col_loss = find("total weight loss")
    col_closing = find("compound floor stock", "closing stock")
    if col_batch is None:
        return None
    given_label = str(raw_hdr[col_given]).strip() if col_given is not None and col_given < len(raw_hdr) else ""

    col_first = next((j for j, c in enumerate(hdr) if c == "1st"), 6)
    chem_cols, pulv_cols = [], []
    for j in range(col_first, col_batch):
        h = hdr[j] if j < len(hdr) else ""
        name = ""
        if j < len(sub) and str(sub[j]).strip():
            name = str(sub[j]).strip()
        elif j < len(raw_hdr) and str(raw_hdr[j]).strip():
            name = str(raw_hdr[j]).strip()
        if not name:
            continue
        if "pulviz" in h or "pulvyz" in h:
            pulv_cols.append((j, name))
        else:
            chem_cols.append((j, name))

    opening = 0.0
    for i in range(hdr_i):
        done = False
        for j, c in enumerate(rows[i]):
            if _cnorm(c) in ("op. stock", "op stock", "opening stock"):
                vr = rows[i + 1] if i + 1 < len(rows) else []
                if j < len(vr):
                    opening = num(vr[j])
                done = True
                break
        if done:
            break

    def cell(row, j):
        return num(row[j]) if (j is not None and j < len(row)) else 0.0

    days, total_chems = [], {}
    for i in range(hdr_i + 1, len(rows)):
        row = rows[i]
        if col_date >= len(row):
            continue
        lbl = str(row[col_date]).strip()
        if not lbl or lbl.upper().startswith(("TOTAL", "WEEK", "RATIO", "PART", "%")):
            continue
        d = _logbook_date(row[col_date])
        if d is None:
            continue
        chems = {}
        for j, nm in chem_cols:
            v = cell(row, j)
            if v:
                chems[nm] = chems.get(nm, 0.0) + v
                total_chems[nm] = total_chems.get(nm, 0.0) + v
        days.append({
            "date": d.isoformat(),
            "day": d.day,
            "batch": cell(row, col_batch),
            "material": cell(row, col_material),
            "given": cell(row, col_given),
            "loss": cell(row, col_loss),
            "closing": cell(row, col_closing),
            "pulvizer": sum(cell(row, j) for j, _ in pulv_cols),
            "chems": chems,
        })

    return {
        "opening": opening,
        "given_label": given_label,
        "days": days,
        "chem_names": [nm for _, nm in chem_cols],
        "pulvizer_names": [nm for _, nm in pulv_cols],
        "total_chems": total_chems,
    }


def parse_cg_logbook(rows: List[list]) -> Optional[dict]:
    """Parse the CPVC-Fittings (CG 122) purchase/issue/balance tab.

    Returns ``None`` if no header found. Result: ``opening`` (month op. stock),
    ``days`` [{date, day, purchase, issue, balance}].
    """
    if not rows:
        return None
    hdr_i = None
    for i, row in enumerate(rows[:8]):
        cells = [_cnorm(c) for c in row]
        if any(c == "date" for c in cells) and any("balance" in c for c in cells):
            hdr_i = i
            break
    if hdr_i is None:
        return None
    hdr = [_cnorm(c) for c in rows[hdr_i]]

    def find(*subs):
        for j, c in enumerate(hdr):
            for s in subs:
                if s in c:
                    return j
        return None

    col_date = next((j for j, c in enumerate(hdr) if c == "date"), 1)
    col_purchase = find("purchase")
    col_issue = find("issue")
    col_balance = find("balance")
    col_op = find("op. stock", "op stock")

    def cell(row, j):
        return num(row[j]) if (j is not None and j < len(row)) else 0.0

    opening = 0.0
    total_row = rows[hdr_i + 1] if hdr_i + 1 < len(rows) else []
    if col_op is not None:
        opening = cell(total_row, col_op)

    days = []
    for i in range(hdr_i + 1, len(rows)):
        row = rows[i]
        if col_date >= len(row):
            continue
        lbl = str(row[col_date]).strip()
        if not lbl or lbl.upper().startswith(("TOTAL", "WEEK")):
            continue
        d = _logbook_date(row[col_date])
        if d is None:
            continue
        days.append({
            "date": d.isoformat(),
            "day": d.day,
            "purchase": cell(row, col_purchase),
            "issue": cell(row, col_issue),
            "balance": cell(row, col_balance),
        })
    return {"opening": opening, "days": days}


# Map "Compound 6-10" rollup column headers -> compound keys (longest first so
# "CPVC F" wins over "CPVC"). Used only for the reconciliation badge.
_ROLLUP_COL_KEYS = [
    ("cpvc f", "CPVC_F"), ("upvc f", "UPVC_F"), ("swr f", "SWR_F"),
    ("agri f", "SWR_F"), ("cpvc", "CPVC"), ("upvc", "UPVC"),
    ("agri", "AGRI"), ("swr", "SWR"),
]
_ROLLUP_ROW_KEYS = [
    ("opening stock", "opening"), ("pulvizer", "pulvizer"),
    ("total batch weight", "batch"), ("total material out", "material"),
    ("total compound given", "given"), ("total weight loss", "loss"),
    ("closing stock", "closing"),
]


def parse_compound_rollup(rows: List[list]) -> dict:
    """Parse the in-sheet "Compound 6-10" monthly rollup -> {key: {field: kg}}.

    Reconciliation reference ONLY (never a headline figure). Returns {} if the
    TYPES header row isn't found.
    """
    if not rows:
        return {}
    hdr_i = None
    for i, row in enumerate(rows[:6]):
        if any(_cnorm(c) == "types" for c in row):
            hdr_i = i
            break
    if hdr_i is None:
        return {}
    hdr = rows[hdr_i]
    col_key = {}
    for j, c in enumerate(hdr):
        cc = _cnorm(c)
        for token, key in _ROLLUP_COL_KEYS:
            if cc == token:
                col_key[j] = key
                break
    out: dict = {}
    for i in range(hdr_i + 1, len(rows)):
        row = rows[i]
        if not row or len(row) < 2:
            continue
        label = _cnorm(row[1]) if len(row) > 1 else ""
        field = None
        for token, fk in _ROLLUP_ROW_KEYS:
            if token in label:
                field = fk
                break
        if not field:
            continue
        for j, key in col_key.items():
            if j < len(row):
                out.setdefault(key, {})[field] = num(row[j])
    return out


# ---------------------------------------------------------------------------
# (D) Pipe Moulds Summary — mould-wise working reports (Report-17..20)
# ---------------------------------------------------------------------------
# Each of Report-17 (CPVC), Report-18 (UPVC), Report-19 (SWR), Report-20 (AGRI)
# is a per-MOULD working table living inside the monthly PIPE workbook. Columns
# are located by header TEXT across the two-row header band (never fixed index),
# and the stored TOTAL row is IGNORED for the headline — every group total is
# RECOMPUTED by summing the mould detail rows, then reconciled against that
# stored TOTAL as a cross-check. The sheet's own month label ("APRIL'26") is a
# stale template artefact; the production block is the WORKBOOK's month, so the
# caller stamps the requested period, never this label.

def parse_mould_working(values: List[list], *, group: str) -> Optional[dict]:
    """Parse one mould-working tab (Report-17..20) into a recomputed summary.

    Returns ``{group, moulds:[...], total_pcs, total_kg, total_util_hrs,
    n_total, n_run, sheet_total_pcs, sheet_total_kg, sheet_total_util_hrs}`` or
    ``None`` when the tab has no recognisable header (e.g. an older-FY workbook
    without this report). ``total_*`` are the RECOMPUTED sums of the detail rows;
    ``sheet_total_*`` are the sheet's own stored TOTAL row (used only to
    reconcile, never as the headline).
    """
    if not values:
        return None

    # Header band = the first ~6 rows. The column labels are split across two
    # rows: the identity row (S.NO / MOULD / CAVITY / ...) and the production row
    # (PRODUCTION IN PCS / PRODUCTION IN KG / MOULD UTILISATION IN HOURS).
    band = values[:6]

    def find_col(pred) -> int:
        for row in band:
            for c, v in enumerate(row):
                if pred(str(v).strip().upper()):
                    return c
        return -1

    sno_c = find_col(lambda h: "S.NO" in h or h == "SNO")
    mould_c = find_col(lambda h: h == "MOULD")
    cavity_c = find_col(lambda h: "CAVITY" in h)
    pcs_c = find_col(lambda h: "PRODUCTION IN PCS" in h)
    kg_c = find_col(lambda h: "PRODUCTION IN KG" in h)
    util_c = find_col(lambda h: "UTILISATION" in h or "UTILIZATION" in h)
    if mould_c < 0 or kg_c < 0 or pcs_c < 0:
        return None

    # Locate the TOTAL row (its label sits in the S.NO column) and the first
    # detail row after it. The detail body begins right after TOTAL.
    total_idx = -1
    for i, row in enumerate(values[:8]):
        cell = str(row[sno_c]).strip().upper() if 0 <= sno_c < len(row) else ""
        if cell == "TOTAL":
            total_idx = i
            break

    def g(row, c):
        return row[c] if 0 <= c < len(row) else ""

    sheet_pcs = sheet_kg = sheet_util = 0.0
    if total_idx >= 0:
        trow = values[total_idx]
        sheet_pcs = num(g(trow, pcs_c))
        sheet_kg = num(g(trow, kg_c))
        sheet_util = num(g(trow, util_c)) if util_c >= 0 else 0.0

    start = total_idx + 1 if total_idx >= 0 else 1
    moulds: List[dict] = []
    total_pcs = total_kg = total_util = 0.0
    n_total = n_run = 0
    for row in values[start:]:
        code = str(g(row, mould_c)).strip()
        u = code.upper()
        if not code or u == "TOTAL" or "GRAND" in u:
            continue
        pcs = num(g(row, pcs_c))
        kg = num(g(row, kg_c))
        util = num(g(row, util_c)) if util_c >= 0 else 0.0
        cavity = str(g(row, cavity_c)).strip() if cavity_c >= 0 else ""
        n_total += 1
        run = pcs > 0 or kg > 0
        if run:
            n_run += 1
        total_pcs += pcs
        total_kg += kg
        total_util += util
        moulds.append({
            "mould": code,
            "cavity": cavity,
            "pcs": pcs,
            "kg": kg,
            "util_hours": util,
            "run": run,
        })

    return {
        "group": group,
        "moulds": moulds,
        "total_pcs": total_pcs,
        "total_kg": total_kg,
        "total_util_hours": total_util,
        "n_total": n_total,
        "n_run": n_run,
        "sheet_total_pcs": sheet_pcs,
        "sheet_total_kg": sheet_kg,
        "sheet_total_util_hours": sheet_util,
    }
