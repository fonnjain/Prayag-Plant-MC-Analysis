"""Read-only Data Verification — expose what the app computed, with provenance.

This module changes nothing about how data is read or computed. It takes the
already-loaded deterministic Records (monthly summary grid + true daily rows)
and *surfaces* them with their source provenance, plus three reconciliation
checks, so a person — or Claude reading the same Google Sheets independently —
can confirm the app's numbers match the source cells.

Guardrails (enforced by construction):
  * Pure and network-free. No model is in the data path; no value is recomputed
    through anything but this deterministic arithmetic.
  * Read-only. Nothing here writes to or edits the underlying figures.
  * A mismatch is surfaced and LOCATED (which plant/machine, the two numbers),
    never silently reconciled or auto-corrected.

The three checks (each PASS / FAIL / NA against a small tolerance):
  1. Daily vs summary — sum the true daily rows to the month and compare to the
     monthly summary grid, per plant and (where the machine join holds) per
     machine.
  2. Row vs total — the summed machine detail rows vs the sheet's own TOTAL row.
  3. Plant vs machines — the plant total vs the sum of its machines (catches
     output recorded against no machine).
"""
from __future__ import annotations

import datetime
import re
from typing import Dict, List, Optional

# Mirror sheets._mc_key exactly so the daily<->monthly machine join here matches
# the one the ingestion engine uses. Auxiliary/die rows (SOCKET-n, Grinder-1,
# mould codes like A02) return None and are never mis-joined by trailing number.
_MC_RE = re.compile(r"M\s*/?\s*C\s*-?\s*(\d+)", re.I)
_MACHINE_RE = re.compile(r"\bMACHINE\s*-?\s*(\d+)\b", re.I)

PASS = "PASS"
FAIL = "FAIL"
NA = "NA"


def _machine_num(label) -> Optional[int]:
    m = _MC_RE.search(str(label))
    if m:
        return int(m.group(1))
    m = _MACHINE_RE.search(str(label))
    if m:
        return int(m.group(1))
    return None


def _pct(a: float, b: float) -> float:
    """Percent difference of ``a`` from baseline ``b`` (0 when both ~0)."""
    if abs(b) < 1e-9:
        return 0.0 if abs(a) < 1e-9 else 100.0
    return abs(a - b) / abs(b) * 100.0


def _verdict(a: float, b: float, tol_pct: float) -> str:
    return PASS if _pct(a, b) <= tol_pct else FAIL


def month_label(month: str) -> str:
    """``2026-05`` -> ``May 2026`` (falls back to the raw string)."""
    try:
        return datetime.datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def build_verification(
    month: str,
    monthly_rows: List,
    monthly_reports: List[dict],
    daily_rows: List,
    daily_reports: List[dict],
    tol: float = 0.005,
) -> dict:
    """Assemble the verification view for a single calendar month.

    ``monthly_rows`` is the authoritative per plant->machine->month summary
    (the published figures). ``daily_rows`` are the true day-level rows for the
    same month (may be empty). All arguments are deterministic Records / report
    dicts already produced by the readers; nothing is fetched or recomputed
    through a model here.
    """
    tol_pct = tol * 100.0
    monthly_rows = [r for r in (monthly_rows or []) if r.period == month or not r.period]
    daily_rows = list(daily_rows or [])

    # ---- §1 per plant -> machine -> month figures, with provenance ----
    groups: Dict[tuple, dict] = {}
    for r in monthly_rows:
        key = (r.plant, r.machine)
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "plant": r.plant,
                "machine": r.machine,
                "year_month": month,
                "unit": r.unit,
                "output": 0.0,
                "output_kg": 0.0,
                "output_pcs": 0.0,
                "reject": 0.0,
                "run_hours": 0.0,
                "breakdown_hours": 0.0,
                "ideal_hours_used": 0.0,
                "ideal_source": r.ideal_source,
                "source_file_id": r.source_file,
                "source_sheet": r.source_tab,
                # We do not track per-cell A1 addresses; the honest locator a
                # human follows in the tab is the machine's own row label.
                "source_ref": r.machine or "(plant total)",
            }
        g["output"] += r.total_count
        if (r.unit or "").lower() == "kg":
            g["output_kg"] += r.total_count
        elif (r.unit or "").lower() in ("pcs", "nos", "no", "pieces"):
            g["output_pcs"] += r.total_count
        g["reject"] += r.reject_count
        g["run_hours"] += r.actual_hours
        g["breakdown_hours"] += getattr(r, "downtime_min", 0.0) / 60.0
        g["ideal_hours_used"] += r.ideal_hours
        if r.ideal_source and r.ideal_source != "none":
            g["ideal_source"] = r.ideal_source

    rows = sorted(groups.values(), key=lambda g: (g["plant"], str(g["machine"])))

    # ---- roll-ups ----
    plant_roll: Dict[str, dict] = {}
    for g in rows:
        p = plant_roll.setdefault(
            g["plant"],
            {"plant": g["plant"], "output": 0.0, "reject": 0.0,
             "run_hours": 0.0, "machines": 0},
        )
        p["output"] += g["output"]
        p["reject"] += g["reject"]
        p["run_hours"] += g["run_hours"]
        p["machines"] += 1
    plant_rollups = sorted(plant_roll.values(), key=lambda p: p["plant"])
    grand = {
        "output": sum(p["output"] for p in plant_rollups),
        "reject": sum(p["reject"] for p in plant_rollups),
        "run_hours": sum(p["run_hours"] for p in plant_rollups),
        "n_rows": len(rows),
    }

    # ---- Check 1: daily vs summary ----
    check_daily = _check_daily_vs_summary(monthly_rows, daily_rows, tol_pct)
    # ---- Check 2: row vs total (sheet TOTAL row) ----
    check_row_total = _check_row_vs_total(monthly_reports, tol_pct)
    # ---- Check 3: plant vs machines ----
    check_plant_mc = _check_plant_vs_machines(monthly_rows, tol_pct)

    checks = {
        "daily_vs_summary": check_daily,
        "row_vs_total": check_row_total,
        "plant_vs_machines": check_plant_mc,
    }
    passed = sum(1 for c in checks.values() if c["status"] == PASS)
    failed = sum(1 for c in checks.values() if c["status"] == FAIL)

    return {
        "month": month,
        "month_label": month_label(month),
        "rows": rows,
        "plant_rollups": plant_rollups,
        "grand": grand,
        "checks": checks,
        "checks_passed": passed,
        "checks_failed": failed,
        "tolerance_pct": tol_pct,
        "has_daily": bool(daily_rows),
    }


def _rollup(status_entries: List[dict]) -> str:
    if not status_entries:
        return NA
    if any(e["status"] == FAIL for e in status_entries):
        return FAIL
    return PASS


def _check_daily_vs_summary(monthly_rows, daily_rows, tol_pct: float) -> dict:
    label = ("Daily facts summed to the month vs the monthly summary grid, "
             "per plant and (where the machine join holds) per machine.")
    if not daily_rows:
        return {"label": label, "status": NA, "entries": [],
                "note": "No daily workbook rows for this month to cross-check."}

    # Monthly summary keyed by plant, machine label, and machine number.
    m_plant: Dict[str, float] = {}
    m_label: Dict[tuple, float] = {}
    m_num: Dict[tuple, float] = {}
    for r in monthly_rows:
        m_plant[r.plant] = m_plant.get(r.plant, 0.0) + r.total_count
        m_label[(r.plant, r.machine)] = m_label.get((r.plant, r.machine), 0.0) + r.total_count
        n = _machine_num(r.machine)
        if n is not None:
            m_num[(r.plant, n)] = m_num.get((r.plant, n), 0.0) + r.total_count

    d_plant: Dict[str, float] = {}
    d_label: Dict[tuple, float] = {}
    d_num: Dict[tuple, float] = {}
    for r in daily_rows:
        d_plant[r.plant] = d_plant.get(r.plant, 0.0) + r.total_count
        d_label[(r.plant, r.machine)] = d_label.get((r.plant, r.machine), 0.0) + r.total_count
        n = _machine_num(r.machine)
        if n is not None:
            d_num[(r.plant, n)] = d_num.get((r.plant, n), 0.0) + r.total_count

    entries: List[dict] = []
    for plant in sorted(set(d_plant) | {p for p in m_plant if p in d_plant}):
        summary_value = m_plant.get(plant, 0.0)
        daily_sum = d_plant.get(plant, 0.0)
        note = ""
        if plant == "PIPE":
            note = ("PIPE daily output is net-of-rejection while the monthly "
                    "grid is gross — a difference here is expected by design.")
        entries.append({
            "scope": "plant", "plant": plant, "machine": "",
            "daily_sum": round(daily_sum, 1),
            "summary_value": round(summary_value, 1),
            "difference": round(daily_sum - summary_value, 1),
            "pct": round(_pct(daily_sum, summary_value), 2),
            "status": _verdict(daily_sum, summary_value, tol_pct),
            "note": note,
        })

    # Per-machine, only where a monthly counterpart exists (exact label, else
    # by machine number). Mould-code / auxiliary daily rows have no counterpart
    # and are covered by the plant-level line above.
    seen: set = set()
    for (plant, label_), dval in sorted(d_label.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        sval = m_label.get((plant, label_))
        if sval is None:
            n = _machine_num(label_)
            if n is None or (plant, n) not in m_num:
                continue
            sval = m_num[(plant, n)]
        if (plant, label_) in seen:
            continue
        seen.add((plant, label_))
        entries.append({
            "scope": "machine", "plant": plant, "machine": label_,
            "daily_sum": round(dval, 1),
            "summary_value": round(sval, 1),
            "difference": round(dval - sval, 1),
            "pct": round(_pct(dval, sval), 2),
            "status": _verdict(dval, sval, tol_pct),
            "note": "",
        })

    return {"label": label, "status": _rollup(entries), "entries": entries, "note": ""}


def _check_row_vs_total(monthly_reports, tol_pct: float) -> dict:
    label = ("Summed machine detail rows vs the sheet's own TOTAL row, "
             "for each summary workbook.")
    entries: List[dict] = []
    for rep in monthly_reports or []:
        recon = rep.get("reconcile")
        if not recon:
            continue
        detail = float(recon.get("detail_total", 0.0))
        grid = float(recon.get("grid_total", 0.0))
        entries.append({
            "plant": rep.get("plant", ""),
            "title": rep.get("title", rep.get("plant", "")),
            "source_file_id": rep.get("file_id", ""),
            "source_sheet": rep.get("tab", ""),
            "daily_sum": round(detail, 1),       # reuse the two-number columns
            "summary_value": round(grid, 1),
            "difference": round(detail - grid, 1),
            "pct": round(_pct(detail, grid), 2),
            "status": _verdict(detail, grid, tol_pct),
        })
    return {"label": label, "status": _rollup(entries), "entries": entries, "note": ""}


def _check_plant_vs_machines(monthly_rows, tol_pct: float) -> dict:
    label = "Plant total vs the sum of its machines (catches output with no machine assigned)."
    plant_total: Dict[str, float] = {}
    mc_total: Dict[str, float] = {}
    for r in monthly_rows:
        plant_total[r.plant] = plant_total.get(r.plant, 0.0) + r.total_count
        if (r.machine or "").strip():
            mc_total[r.plant] = mc_total.get(r.plant, 0.0) + r.total_count
    entries: List[dict] = []
    for plant in sorted(plant_total):
        ptot = plant_total[plant]
        msum = mc_total.get(plant, 0.0)
        entries.append({
            "plant": plant,
            "daily_sum": round(msum, 1),         # machines sum
            "summary_value": round(ptot, 1),     # plant total
            "difference": round(msum - ptot, 1),
            "pct": round(_pct(msum, ptot), 2),
            "status": _verdict(msum, ptot, tol_pct),
        })
    return {"label": label, "status": _rollup(entries), "entries": entries, "note": ""}


CSV_HEADER = [
    "plant", "machine", "year_month",
    "output", "unit", "output_kg", "output_pcs",
    "reject", "run_hours", "breakdown_hours",
    "ideal_hours_used", "ideal_source",
    "source_file_id", "source_sheet", "source_ref",
]


def rows_to_csv(result: dict) -> str:
    """Render the §1 table as CSV text (provenance included for spot-checks)."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADER)
    for g in result["rows"]:
        w.writerow([
            g["plant"], g["machine"], g["year_month"],
            f"{g['output']:.1f}", g["unit"],
            f"{g['output_kg']:.1f}", f"{g['output_pcs']:.1f}",
            f"{g['reject']:.1f}", f"{g['run_hours']:.1f}",
            f"{g['breakdown_hours']:.1f}",
            f"{g['ideal_hours_used']:.1f}", g["ideal_source"],
            g["source_file_id"], g["source_sheet"], g["source_ref"],
        ])
    return buf.getvalue()
