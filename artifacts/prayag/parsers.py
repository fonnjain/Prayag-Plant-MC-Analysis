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
