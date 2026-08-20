"""
Management Report (B) — Moulding M/C Summary
Route: /management-reports/moulding-summary

SECTION 1 (SUMMARY worksheet):  per-machine FY26-27 recomputed from daily records.
SECTION 2 (SUMMARY-1 worksheet): grouped by tonnage band, YoY comparison.
  FY26-27 — fully recomputed from daily records (Cardinal Rule).
  FY25-26 — read from the closed annual SUMMARY-1 tab as-is (R-03 exception).

Sources:
  Output, rejection, runner_produce — Record.total_count, reject_count, runner_lumps
    (MOULDING daily path: Report-12 "Wt in Kgs" via PIPE key, R-38).
  Run hours — Record.actual_hours (joined from Report-5 via r5_runhours, R-38).
  Tonnage band — derived from Record.mould via _mould_to_band(); Record.tonnage_band
    is blank for MOULDING records (parser does not populate it from this source path).
  Machine roster (27 machines incl. idle) — SUMMARY tab rows, cols 0/2/3.
  FY25-26 by-band summary — SUMMARY-1 tab, closed-annual rows.

Avg. Per Output on Hours in the TOTAL row is the SUM of the per-band averages,
NOT a weighted mean of total_output ÷ total_hours.  The sheet convention:
  5.38 + 9.18 + 11.91 + 12.63 + 15.22 + 21.79 = 76.11 ≈ 76.10 (rounding).
Do NOT "correct" this to total_output / total_hours = ~10.17 — it would break the
match and contradict the sheet.  Report 2 (Pipe) used the same convention (65.71%).

Ideal Hours = 500 per machine per month (R-16 flat placeholder, NOT from
baselines.json and must NOT be used to populate baselines.json).
"""
from __future__ import annotations

import re
import time
import logging
import dataclasses
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# ── Source identifiers ────────────────────────────────────────────────────────
MOULDING_WB_FILE_ID = "1ZCHZp5io1ctdvm92xlHI7x5FtBC-nLoAb2FRMkfXzBI"
SUMMARY_TAB         = "SUMMARY"
SUMMARY1_TAB        = "SUMMARY-1"
IDEAL_HRS_PER_MC_PER_MONTH = 500   # R-16 flat placeholder — do not source elsewhere

BAND_ORDER = ["150", "200", "250", "275", "350", "450"]

# FY → sorted list of period strings
_FY_YM = {
    "2627": [
        "2026-04", "2026-05", "2026-06", "2026-07",
        "2026-08", "2026-09", "2026-10", "2026-11",
        "2026-12", "2027-01", "2027-02", "2027-03",
    ],
}

# Management reports are published only through a reviewed month.  Exports pass
# an explicit selected month; the direct page uses this approved default.
MOULDING_REPORT_DEFAULT_THROUGH = {
    "2627": "2026-07",
}

# ── In-process cache (10-minute TTL) ─────────────────────────────────────────
_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 600   # seconds


# ── Small helpers ─────────────────────────────────────────────────────────────

_BAND_RE    = re.compile(r'\b(150|200|250|275|350|450)\b')
_MC_NUM_RE  = re.compile(r'M/C\s*[-–]\s*(\d+)', re.I)


def _mould_to_band(mould: str) -> str:
    """Extract tonnage band (150|200|250|275|350|450) from a mould code.

    Works for codes like 'C-150-A', 'U-250-B', 'NU-200', 'A-150-2' etc.
    The three-digit band number is the only distinguishing component.
    """
    m = _BAND_RE.search(mould or "")
    return m.group(1) if m else ""


def _mc_key(raw: str) -> str:
    """Canonical machine key from any label: 'MOULDING M/C - 1' → 'M/C - 1'."""
    m = _MC_NUM_RE.search(raw or "")
    return f"M/C - {m.group(1)}" if m else (raw or "").upper().strip()


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").upper().strip())


def _mould_id_key(raw: str) -> str:
    """Stable key for a mould ID in either roster or daily Report-12 labels."""
    value = _norm(raw)
    value = re.sub(r"^MOULDING\s+", "", value)
    return re.sub(r"\s+", "", value)


def _roster_mould_map(roster: list[dict]) -> dict[str, str]:
    """Map roster mould IDs (A05(U-150)) to their global M/C identifiers."""
    return {
        _mould_id_key(entry.get("mould_id", "")): entry["mc_key"]
        for entry in roster
        if _mould_id_key(entry.get("mould_id", ""))
    }


def _record_roster_key(record, roster_by_mould: dict[str, str]) -> Optional[str]:
    """Resolve a daily record to the roster's global M/C key.

    Daily Report-12 calls a machine by its mould ID (for example,
    ``MOULDING A05(U-150)``), while the management workbook labels the same
    asset as ``M/C - 7``.  Annual/grid records may already carry an M/C label,
    so preserve that as a compatibility fallback.
    """
    for raw in (getattr(record, "mould", ""), getattr(record, "machine", "")):
        mapped = roster_by_mould.get(_mould_id_key(raw))
        if mapped:
            return mapped
    key = _mc_key(getattr(record, "machine", "") or "")
    return key if key in set(roster_by_mould.values()) else None


def _record_band(record) -> str:
    """Derive tonnage from the populated mould field or the daily machine label."""
    return _mould_to_band(
        getattr(record, "mould", "") or getattr(record, "machine", "")
    )


def _num(v) -> Optional[float]:
    try:
        return float(re.sub(r"[,\s%]", "", str(v).strip()))
    except (ValueError, TypeError):
        return None


def _safe_div(num: float, den: float) -> Optional[float]:
    if den and den != 0.0:
        return num / den
    return None


# ── SUMMARY tab: read the 27-machine roster ────────────────────────────────────

def _parse_summary_roster(values: list) -> tuple[list[dict], list[str]]:
    """Parse SUMMARY tab and return (roster, warnings).

    roster  — ordered list of machine descriptors:
               {band, mould_id, mc_key}  (mc_key canonical, e.g. 'M/C - 1').
               Band is carried forward across merged-cell rows.
    warnings — non-empty when a numeric band value in col 0 is not in BAND_ORDER;
               those machines are silently dropped and each unknown band is reported
               once (R-06: fail loudly rather than drop without notice).

    BAND_ORDER = ["150","200","250","275","350","450"] is a fixed list copied from
    the source SUMMARY tab by inspection, NOT read live.  If the workbook adds a
    new tonnage, it will be caught here and surfaced as a warning instead of being
    silently ignored.

    The SUMMARY tab contains TWO machine blocks:
      • FY26-27 rows (M/C-1 to M/C-27)  ← we want these
      • FY25-26 rows starting after a second TOTAL row
    We stop when we encounter a TOTAL row after already collecting some roster
    entries — that second TOTAL marks the FY25-26 block boundary.
    """
    roster: list[dict] = []
    warnings: list[str] = []
    current_band = ""
    unknown_bands_seen: set[str] = set()

    for row in values:
        if not row:
            continue
        c0 = str(row[0]).strip() if len(row) > 0 else ""
        c1 = str(row[1]).strip() if len(row) > 1 else ""
        c2 = str(row[2]).strip() if len(row) > 2 else ""
        c3 = str(row[3]).strip() if len(row) > 3 else ""

        # The second TOTAL row (c1 == 'TOTAL' after we already have roster entries)
        # signals the start of the FY25-26 block — stop here.
        if c1 == "TOTAL" and roster:
            break

        if c0 in BAND_ORDER:
            current_band = c0
        elif c0 and re.match(r"^\d+$", c0) and c0 not in unknown_bands_seen:
            # Numeric value in the band column that is not in BAND_ORDER —
            # machines in this group will be dropped (R-06: warn loudly).
            unknown_bands_seen.add(c0)
            warnings.append(
                f"Unknown tonnage band '{c0}' in SUMMARY tab col 0 — not in "
                f"BAND_ORDER {BAND_ORDER}. Machines in this band are excluded. "
                f"Add '{c0}' to BAND_ORDER in mgmt_moulding_summary.py to include them."
            )
            logger.warning(
                "_parse_summary_roster: unknown band '%s' not in BAND_ORDER %s — "
                "those machines are dropped",
                c0, BAND_ORDER,
            )

        if not current_band:
            continue
        if not _MC_NUM_RE.match(c3):
            continue

        roster.append({
            "band":     current_band,
            "mould_id": c2,
            "mc_key":   c3,   # already in canonical form from the tab
        })

    return roster, warnings


# ── SUMMARY-1 tab: parse FY25-26 closed-annual block ─────────────────────────

def _parse_s1_tab(values: list) -> dict:
    """Parse SUMMARY-1 tab into FY26-27 and FY25-26 blocks.

    Returns: {
        '2627': {'total_row': [...], 'band_rows': {'150': [...], ...}},
        '2526': {'total_row': [...], 'band_rows': {'150': [...], ...}},
    }
    Tab layout:
      Row  2: FY26-27 TOTAL (col 3 = '27')
      Rows 3-8: FY26-27 band rows
      Row 10: FY25-26 TOTAL (col 3 = '25')
      Rows 11-16: FY25-26 band rows
    """
    result: dict = {}
    current_fy: Optional[str] = None
    current_block: dict = {}

    for row in values:
        if not row:
            continue
        c = [str(x).strip() for x in row] + [""] * 12   # safe padding

        if c[1] == "TOTAL":
            # Save previous block if any
            if current_fy:
                result[current_fy] = current_block
            mc_count_raw = c[3].replace(",", "")
            try:
                mc = int(float(mc_count_raw))
            except (ValueError, TypeError):
                mc = 0
            if mc == 27:
                current_fy = "2627"
            elif mc == 25:
                current_fy = "2526"
            else:
                current_fy = None
            current_block = {"total_row": row, "band_rows": {}}
            continue

        # Band data row: col 2 is the band code
        band = c[2]
        if current_fy and band in BAND_ORDER:
            current_block["band_rows"][band] = row

    if current_fy and current_fy not in result:
        result[current_fy] = current_block

    return result


def _s1_row_to_dict(row: list, band: str, is_total: bool = False) -> dict:
    """Extract one band/total row from SUMMARY-1 tab into a result dict.

    Column layout (0-indexed):
      0: (blank or FY label)
      1: 'TOTAL' or FY date label
      2: band (150/200/…) or blank for TOTAL
      3: machine count
      4: Ideal Hours
      5: Actual Hours
      6: Output (KG)
      7: Rejection (Kgs)
      8: Runner Produce (Kgs)
      9: Avg. Per Output on Hours
     10: M/C Utilization in Hours (%)
    """
    c = [str(x).strip() for x in row] + [""] * 12
    return {
        "band":       band,
        "mc_count":   _num(c[3].replace(",", "")),
        "ideal_hrs":  _num(c[4].replace(",", "")),
        "actual_hrs": _num(c[5].replace(",", "")),
        "output_kg":  _num(c[6].replace(",", "")),
        "reject_kg":  _num(c[7].replace(",", "")),
        "runner_kg":  _num(c[8].replace(",", "")),
        "avg_hr":     _num(c[9].replace(",", "")),
        "util_pct":   _num(c[10].replace(",", "").replace("%", "")),
        "is_total":   is_total,
    }


# ── Section 1: per-machine FY26-27 recomputed ────────────────────────────────

def _build_section1(records: list, n_months: int, roster: list[dict]) -> dict:
    """Recompute per-machine figures from daily records.

    Ideal Hours = 500 per machine per month (R-16 flat placeholder).
    Avg. Per Output on Hours = output / actual_hours per machine.
    Utilisation = actual_hours / ideal_hours × 100 — NOT capped (M/C-4 > 100%).
    """
    # Accumulate from records
    roster_by_mould = _roster_mould_map(roster)
    mc_acc: dict = {}
    for r in records:
        if r.plant != "MOULDING":
            continue
        key = _record_roster_key(r, roster_by_mould)
        if not key:
            continue
        if key not in mc_acc:
            mc_acc[key] = {"hrs": 0.0, "out": 0.0, "rej": 0.0, "runner": 0.0}
        mc_acc[key]["hrs"]    += float(r.actual_hours  or 0.0)
        mc_acc[key]["out"]    += float(r.total_count   or 0.0)
        mc_acc[key]["rej"]    += float(r.reject_count  or 0.0)
        mc_acc[key]["runner"] += float(r.runner_lumps  or 0.0)

    ideal_per_mc = IDEAL_HRS_PER_MC_PER_MONTH * n_months

    rows = []
    sum_ideal = sum_actual = sum_out = sum_rej = sum_runner = 0.0

    for entry in roster:
        mc_key = entry["mc_key"]
        acc    = mc_acc.get(mc_key, {"hrs": 0.0, "out": 0.0, "rej": 0.0, "runner": 0.0})
        hrs    = acc["hrs"]
        out    = acc["out"]
        rej    = acc["rej"]
        runner = acc["runner"]
        avg_hr = _safe_div(out, hrs)
        util   = _safe_div(hrs, ideal_per_mc)
        if util is not None:
            util *= 100.0

        rows.append({
            "band":       entry["band"],
            "mould_id":   entry["mould_id"],
            "machine":    mc_key,
            "ideal_hrs":  ideal_per_mc,
            "actual_hrs": hrs,
            "output_kg":  out,
            "reject_kg":  rej,
            "runner_kg":  runner,
            "avg_hr":     round(avg_hr, 2) if avg_hr is not None else None,
            "util_pct":   round(util, 2)   if util is not None else None,
            "is_total":   False,
        })
        sum_ideal  += ideal_per_mc
        sum_actual += hrs
        sum_out    += out
        sum_rej    += rej
        sum_runner += runner

    total_avg   = _safe_div(sum_out, sum_actual)
    total_util  = _safe_div(sum_actual, sum_ideal)
    if total_util is not None:
        total_util *= 100.0

    rows.append({
        "band":       "",
        "mould_id":   "",
        "machine":    "TOTAL",
        "ideal_hrs":  sum_ideal,
        "actual_hrs": sum_actual,
        "output_kg":  sum_out,
        "reject_kg":  sum_rej,
        "runner_kg":  sum_runner,
        "avg_hr":     round(total_avg, 2)  if total_avg  is not None else None,
        "util_pct":   round(total_util, 2) if total_util is not None else None,
        "is_total":   True,
    })

    return {"rows": rows, "warnings": [], "n_months": n_months}


# ── Section 2: band rollup FY26-27 recomputed ────────────────────────────────

def _build_section2_fy2627(
    records: list, n_months: int, roster: list[dict]
) -> list[dict]:
    """Recompute Section 2 FY26-27 by tonnage band.

    Uses rollup_by_tonnage_band() after populating Record.tonnage_band from the
    mould field via _mould_to_band() — the field is blank as-stored because the
    MOULDING daily parser does not set it from the Report-12 source path.

    Avg. Per Output on Hours in the TOTAL row = SUM of per-band avg/hr values
    (NOT total_output ÷ total_hours).  This is the sheet's own convention.
    """
    from metrics import rollup_by_tonnage_band

    # Derive band from mould and stamp onto record copies
    derived = []
    for r in records:
        if r.plant != "MOULDING":
            continue
        band = _record_band(r)
        derived.append(dataclasses.replace(r, tonnage_band=band))

    band_results = rollup_by_tonnage_band(derived)

    # Machine count per band from roster (handles idle machines)
    band_mc_count: dict[str, int] = {}
    for entry in roster:
        b = entry["band"]
        band_mc_count[b] = band_mc_count.get(b, 0) + 1

    rows      = []
    sum_ideal = sum_actual = sum_out = sum_rej = sum_runner = 0.0
    sum_avg   = 0.0   # accumulator for TOTAL avg/hr = Σ band avg/hr

    for band in BAND_ORDER:
        mr       = band_results.get(band)
        mc_count = band_mc_count.get(band, 0)
        ideal    = mc_count * IDEAL_HRS_PER_MC_PER_MONTH * n_months

        actual = mr.actual_hours if mr else 0.0
        out    = mr.total_count  if mr else 0.0
        rej    = mr.reject_count if mr else 0.0
        runner = mr.runner_lumps if mr else 0.0
        avg_hr = _safe_div(out, actual)
        util   = _safe_div(actual, ideal)
        if util is not None:
            util *= 100.0

        rows.append({
            "band":       band,
            "mc_count":   mc_count,
            "ideal_hrs":  ideal,
            "actual_hrs": actual,
            "output_kg":  out,
            "reject_kg":  rej,
            "runner_kg":  runner,
            "avg_hr":     round(avg_hr, 2) if avg_hr is not None else 0.0,
            "util_pct":   round(util, 2)   if util  is not None else 0.0,
            "is_total":   False,
        })
        sum_ideal  += ideal
        sum_actual += actual
        sum_out    += out
        sum_rej    += rej
        sum_runner += runner
        sum_avg    += round(avg_hr, 2) if avg_hr is not None else 0.0

    total_util = _safe_div(sum_actual, sum_ideal)
    if total_util is not None:
        total_util *= 100.0

    rows.append({
        "band":       "TOTAL",
        "mc_count":   sum(band_mc_count.values()),
        "ideal_hrs":  sum_ideal,
        "actual_hrs": sum_actual,
        "output_kg":  sum_out,
        "reject_kg":  sum_rej,
        "runner_kg":  sum_runner,
        # TOTAL avg/hr = SUM of band avg/hr values (sheet convention, not weighted mean)
        "avg_hr":     round(sum_avg, 2),
        "util_pct":   round(total_util, 2) if total_util is not None else 0.0,
        "is_total":   True,
    })

    return rows


def _parse_s1_block_to_rows(block: dict, fy_label: str, source_label: str) -> list[dict]:
    """Convert a parsed SUMMARY-1 tab block into structured rows."""
    rows = []
    for band in BAND_ORDER:
        row_raw = block.get("band_rows", {}).get(band)
        if row_raw:
            d = _s1_row_to_dict(row_raw, band, is_total=False)
        else:
            d = {
                "band": band, "mc_count": None, "ideal_hrs": None,
                "actual_hrs": None, "output_kg": None, "reject_kg": None,
                "runner_kg": None, "avg_hr": None, "util_pct": None,
                "is_total": False,
            }
        rows.append(d)

    total_raw = block.get("total_row")
    if total_raw:
        rows.append(_s1_row_to_dict(total_raw, "TOTAL", is_total=True))
    return rows


# ── Month label helpers ───────────────────────────────────────────────────────

MONTH_LABELS = ["APR", "MAY", "JUN", "JUL", "AUG", "SEP",
                "OCT", "NOV", "DEC", "JAN", "FEB", "MAR"]

_FY_DISP = {
    "2627": {
        "APR": "APR'26", "MAY": "MAY'26", "JUN": "JUN'26", "JUL": "JUL'26",
        "AUG": "AUG'26", "SEP": "SEP'26", "OCT": "OCT'26", "NOV": "NOV'26",
        "DEC": "DEC'26", "JAN": "JAN'27", "FEB": "FEB'27", "MAR": "MAR'27",
    },
}

_FY_YM_DICT = {
    "2627": {
        "APR": "2026-04", "MAY": "2026-05", "JUN": "2026-06", "JUL": "2026-07",
        "AUG": "2026-08", "SEP": "2026-09", "OCT": "2026-10", "NOV": "2026-11",
        "DEC": "2026-12", "JAN": "2027-01", "FEB": "2027-02", "MAR": "2027-03",
    },
}


def _mc_monthly_moulding(records, fy: str, roster: list) -> dict:
    """Accumulate {mc_key: {ym: {hrs, output_kg, reject_kg, runner_kg}}} from MOULDING records."""
    fy_ym_d = _FY_YM_DICT.get(fy, _FY_YM_DICT["2627"])
    all_yms = set(fy_ym_d.values())
    mc_keys = {item["mc_key"] for item in roster}
    roster_by_mould = _roster_mould_map(roster)

    result: dict = {}
    for mc_key in mc_keys:
        result[mc_key] = {ym: {"hrs": 0.0, "output_kg": 0.0, "reject_kg": 0.0, "runner_kg": 0.0}
                          for ym in all_yms}

    for r in records:
        if r.plant != "MOULDING":
            continue
        ym = getattr(r, "period", None)
        if not ym or ym not in all_yms:
            continue
        mk = _record_roster_key(r, roster_by_mould)
        if mk not in result:
            continue
        result[mk][ym]["hrs"]       += float(r.actual_hours or 0.0)
        result[mk][ym]["output_kg"] += float(r.total_count or 0.0)
        result[mk][ym]["reject_kg"] += float(r.reject_count or 0.0)
        result[mk][ym]["runner_kg"] += float(r.runner_lumps or 0.0) if hasattr(r, "runner_lumps") else 0.0
    return result


def _build_section3_per_machine(moulding_records, n_months: int, roster: list, fy: str = "2627") -> dict:
    """Per-machine monthly detail (27 machines, including idle ones as zero rows).

    Columns per row: Month | Ideal Hrs | Actual Hrs | Output (KG) | Rejection (KG) |
                     Runner (KG) | Avg/Hr | Util%

    Idle machines always render 0, not blank.
    """
    fy_ym_d = _FY_YM_DICT.get(fy, _FY_YM_DICT["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    monthly = _mc_monthly_moulding(moulding_records, fy, roster)

    machines: list = []
    for item in roster:
        mc  = item["mc_key"]
        band = item.get("band", "")
        mould_id = item.get("mould_id", "")

        t_h = t_out = t_rej = t_run = 0.0
        month_rows: list = []

        for lbl in MONTH_LABELS:
            ym = fy_ym_d[lbl]
            d  = monthly.get(mc, {}).get(ym, {"hrs": 0.0, "output_kg": 0.0,
                                              "reject_kg": 0.0, "runner_kg": 0.0})
            h    = d["hrs"]
            out  = d["output_kg"]
            rej  = d["reject_kg"]
            run  = d["runner_kg"]
            avg  = _safe_div(out, h) if h else None
            util = _safe_div(h, IDEAL_HRS_PER_MC_PER_MONTH) * 100 if h else None

            month_rows.append({
                "month_lbl": lbl, "month_disp": fy_disp[lbl],
                "ideal_hrs": IDEAL_HRS_PER_MC_PER_MONTH,
                "actual_hrs": h, "output_kg": out,
                "reject_kg": rej, "runner_kg": run,
                "avg_hr": avg, "util_pct": util,
            })
            t_h += h; t_out += out; t_rej += rej; t_run += run

        ideal_tot = n_months * IDEAL_HRS_PER_MC_PER_MONTH
        machines.append({
            "mc_key": mc, "band": band, "mould_id": mould_id,
            "month_rows": month_rows,
            "total_row": {
                "ideal_hrs": ideal_tot,
                "actual_hrs": t_h, "output_kg": t_out,
                "reject_kg": t_rej, "runner_kg": t_run,
                "avg_hr": _safe_div(t_out, t_h) if t_h else None,
                "util_pct": _safe_div(t_h, ideal_tot) * 100 if ideal_tot else None,
            },
        })
    return {"machines": machines}


def _build_section4_mould_pivot(moulding_records, n_months: int, roster: list, fy: str = "2627") -> dict:
    """Moulding M/C 26-27 pivot: months as rows, machines grouped by band as column-pairs (HOURS|OUTPUT)."""
    fy_ym_d = _FY_YM_DICT.get(fy, _FY_YM_DICT["2627"])
    fy_disp = _FY_DISP.get(fy, _FY_DISP["2627"])
    monthly = _mc_monthly_moulding(moulding_records, fy, roster)

    # Ordered unique machines from roster (preserves band grouping)
    mc_list = [item["mc_key"] for item in roster]
    mc_bands = {item["mc_key"]: item.get("band", "") for item in roster}

    month_rows: list = []
    t_mc: dict[str, dict] = {mc: {"hrs": 0.0, "out": 0.0} for mc in mc_list}

    for lbl in MONTH_LABELS:
        ym   = fy_ym_d[lbl]
        cols = {}
        for mc in mc_list:
            d = monthly.get(mc, {}).get(ym, {"hrs": 0.0, "output_kg": 0.0})
            h = d["hrs"]; o = d["output_kg"]
            cols[mc] = {"hrs": h, "out": o}
            t_mc[mc]["hrs"] += h
            t_mc[mc]["out"] += o
        month_rows.append({"month_lbl": lbl, "month_disp": fy_disp[lbl], "cols": cols})

    total_cols = {mc: t_mc[mc] for mc in mc_list}
    return {
        "machines": mc_list,
        "mc_bands": mc_bands,
        "month_rows": month_rows,
        "total_cols": total_cols,
    }


# ── Top-level builder ─────────────────────────────────────────────────────────

def build_moulding_summary(
    fy: str = "2627", through_ym: Optional[str] = None
) -> dict:
    """Build the full Moulding M/C Summary report data dict.

    Cached in-process for CACHE_TTL seconds.  Returns:
    {
        'fy', 'fy_label', 'error',
        'section1': {'rows': [...], 'warnings': [], 'n_months': int},
        'section2': {
            'fy2627': list[dict],  'fy2627_label': str,
            'fy2526': list[dict],  'fy2526_label': str,
            'warnings': [],
        },
        'build_time_s': float,
    }
    """
    fy_yms = _FY_YM.get(fy, _FY_YM["2627"])
    selected_through = through_ym or MOULDING_REPORT_DEFAULT_THROUGH.get(fy)
    if selected_through not in fy_yms:
        selected_through = MOULDING_REPORT_DEFAULT_THROUGH.get(fy, fy_yms[-1])
    report_yms = [ym for ym in fy_yms if ym <= selected_through]
    cache_key = f"moulding_summary_{fy}_{selected_through}"
    with _cache_lock:
        entry = _cache.get(cache_key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]

    t0 = time.time()

    import sheets as _sh
    token = _sh._get_access_token()

    fy_label = f"FY 20{fy[:2]}-{fy[2:]}"

    try:
        # ── 1. Fetch SUMMARY and SUMMARY-1 tabs ──────────────────────────────
        matrices = _sh.batch_get(
            MOULDING_WB_FILE_ID,
            [SUMMARY_TAB, SUMMARY1_TAB],
            token,
        )
        summary_vals  = matrices.get(SUMMARY_TAB, [])
        summary1_vals = matrices.get(SUMMARY1_TAB, [])

        if not summary_vals or not summary1_vals:
            raise RuntimeError("Could not load SUMMARY or SUMMARY-1 tab")

        # ── 2. Machine roster from SUMMARY tab ───────────────────────────────
        roster, roster_warnings = _parse_summary_roster(summary_vals)
        if not roster:
            raise RuntimeError(
                "No machine rows found in SUMMARY tab — layout may have changed"
            )

        # ── 3. Authoritative daily records for the selected FY window ────────
        # Report-12 is the production source; Report-5 supplies its joined
        # run-hours.  The annual grid is layout/verification only.
        records_raw, daily_reports, _ = _sh.get_daily_records(report_yms)
        roster_by_mould = _roster_mould_map(roster)
        moulding_records = [
            r for r in records_raw
            if r.plant == "MOULDING"
            and getattr(r, "period", None) in report_yms
            and _record_roster_key(r, roster_by_mould)
        ]
        unmapped_active = sorted({
            str(getattr(r, "machine", "") or "")
            for r in records_raw
            if r.plant == "MOULDING"
            and getattr(r, "period", None) in report_yms
            and not _record_roster_key(r, roster_by_mould)
            and (
                float(getattr(r, "actual_hours", 0.0) or 0.0) != 0.0
                or float(getattr(r, "total_count", 0.0) or 0.0) != 0.0
                or float(getattr(r, "reject_count", 0.0) or 0.0) != 0.0
            )
        })

        # n_months = distinct selected months with any Moulding data.
        active_periods = sorted({r.period for r in moulding_records if r.period})
        n_months = max(len(active_periods), 1)

        # ── 4. Section 1: per-machine recomputed ─────────────────────────────
        section1 = _build_section1(moulding_records, n_months, roster)

        # ── 5. Section 2 FY26-27: band rollup recomputed ─────────────────────
        s2_fy2627 = _build_section2_fy2627(moulding_records, n_months, roster)

        # ── 6. Section 2 FY25-26: closed annual from SUMMARY-1 tab (R-03) ───
        s2_warnings: list[str] = []
        if unmapped_active:
            s2_warnings.append(
                "Daily Moulding record(s) with output/hours did not map to the "
                f"SUMMARY roster and were excluded: {', '.join(unmapped_active)}"
            )
        s1_blocks = _parse_s1_tab(summary1_vals)

        block_2526 = s1_blocks.get("2526")
        if block_2526:
            s2_fy2526 = _parse_s1_block_to_rows(
                block_2526,
                fy_label="FY 2025-26",
                source_label="closed annual SUMMARY-1",
            )
        else:
            s2_fy2526 = []
            s2_warnings.append(
                "FY25-26 block not found in SUMMARY-1 tab — "
                "expected a TOTAL row with machine count 25"
            )

        # ── 7. FY26-27 period label ───────────────────────────────────────────
        if active_periods:
            first = active_periods[0]
            last  = active_periods[-1]
            def _ym_lbl(ym):
                import datetime
                dt = datetime.datetime.strptime(ym, "%Y-%m")
                return dt.strftime("%b,%y")
            s2_fy2627_label = f"{_ym_lbl(first)} – {_ym_lbl(last)} (FY 2026-27) — recomputed"
        else:
            s2_fy2627_label = f"{fy_label} — recomputed"

        # ── 8. Sections 3 and 4 ───────────────────────────────────────────────
        section3 = _build_section3_per_machine(moulding_records, n_months, roster, fy)
        section4 = _build_section4_mould_pivot(moulding_records, n_months, roster, fy)

        data = {
            "fy":        fy,
            "fy_label":  fy_label,
            "error":     None,
            "roster_warnings": roster_warnings,   # unknown-band alerts (R-06)
            "section1":  section1,
            "section2": {
                "fy2627":       s2_fy2627,
                "fy2627_label": s2_fy2627_label,
                "fy2526":       s2_fy2526,
                "fy2526_label": "Apr,25 – Jul,25 (FY 2025-26) — closed annual",
                "warnings":     s2_warnings,
            },
            "section3":  section3,
            "section4":  section4,
            "through_ym": selected_through,
            "report_yms": report_yms,
            "build_time_s": round(time.time() - t0, 2),
        }

    except Exception as exc:
        logger.exception("build_moulding_summary failed: %s", exc)
        data = {
            "fy":       fy,
            "fy_label": fy_label,
            "error":    str(exc),
            "roster_warnings": [],
            "section1": {"rows": [], "warnings": [], "n_months": 0},
            "section2": {
                "fy2627": [], "fy2627_label": "",
                "fy2526": [], "fy2526_label": "",
                "warnings": [],
            },
            "section3": {"machines": []},
            "section4": {"machines": [], "mc_bands": {}, "month_rows": [], "total_cols": {}},
            "build_time_s": round(time.time() - t0, 2),
        }

    with _cache_lock:
        _cache[cache_key] = {"ts": time.time(), "data": data}

    return data
