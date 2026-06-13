"""
Data Confirmation & Audit Layer.

Before any figure is published, run a four-tier confirmation against the master
lists and each sheet's own totals, then surface the result so a blank month is
never silently shown as a real low and impossible values never masquerade as KPIs.

All detection here is DETERMINISTIC Python. No figure is ever read or computed by
an AI model. Claude is used by the caller (app.py) ONLY to:
  * fuzzy-match a data machine code to a master code (names, not numbers), and
  * write a plain-English summary of the already-computed issue list.

The four tiers
--------------
  Tier 1  Completeness  — workbooks found, every master entity present, every
                          expected month populated, required cells non-blank.
  Tier 2  Reconciliation — detail rows reconcile to the sheet's own TOTAL
                          (plant == Σ machines), and the engine total == row sum.
  Tier 3  Validity      — impossible values: downtime > shift, reject > output,
                          ratio > 100%, negatives.
  Tier 4  Plausibility  — duplicates, sudden zeros, output outliers, unit mismatch.

Severity → gating
-----------------
  error   → blocks the affected figure ("needs review")
  warning → show the figure with a flag
  pass    → publish normally
"""
from __future__ import annotations

import datetime
import re
from typing import Callable, Dict, List, Optional, Tuple

from metrics import Record, compute_metrics

import sources

# Tier labels for the UI.
TIER_LABELS = {
    1: "Completeness",
    2: "Reconciliation",
    3: "Validity",
    4: "Plausibility",
}

ERROR = "error"
WARNING = "warning"

# Plausibility thresholds (deterministic, documented).
OUTLIER_HIGH = 6.0       # machine output > 6× the plant median → flag
OUTLIER_LOW = 1.0 / 6.0  # machine output < 1/6 the plant median → flag
MIN_PLANT_MACHINES_FOR_OUTLIER = 3

_MC_NUM_RE = re.compile(r"(\d+)")


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------
def _issue(
    tier: int,
    severity: str,
    message: str,
    *,
    plant: str = "",
    machine: str = "",
    month: str = "",
    file: str = "",
    sheet: str = "",
) -> dict:
    return {
        "tier": tier,
        "tier_label": TIER_LABELS.get(tier, str(tier)),
        "severity": severity,
        "message": message,
        "plant": plant,
        "machine": machine,
        "month": month,
        "file": file,
        "sheet": sheet,
    }


def _norm_code(label: str) -> str:
    """Case/space-insensitive machine-code key for exact matching."""
    return re.sub(r"\s+", "", str(label or "")).upper().replace("/", "")


def _num_key(label: str) -> Optional[int]:
    """Trailing number of a machine code (M/C-7 → 7) for numeric matching."""
    nums = _MC_NUM_RE.findall(str(label or ""))
    return int(nums[-1]) if nums else None


# ---------------------------------------------------------------------------
# Masters
# ---------------------------------------------------------------------------
def build_masters(master_rows: List[Record]) -> dict:
    """The known-universe roster derived from the full-FY monthly grid.

    The monthly grid is the authoritative roster of which machines / segments /
    moulds exist for each plant this FY. A blank in a later period is therefore a
    completeness *gap* against this roster — never an implicit zero.
    """
    machines: Dict[str, set] = {}
    segments: Dict[str, set] = {}
    moulds: Dict[str, set] = {}
    units: Dict[str, set] = {}
    for r in master_rows:
        if not r.plant:
            continue
        if r.machine:
            machines.setdefault(r.plant, set()).add(r.machine)
        if r.segment:
            segments.setdefault(r.plant, set()).add(r.segment)
        if r.mould:
            moulds.setdefault(r.plant, set()).add(r.mould)
        if r.unit:
            units.setdefault(r.plant, set()).add(r.unit)
    return {
        "machines": machines,
        "segments": segments,
        "moulds": moulds,
        "units": units,
    }


def expected_files_for(period_months: List[str], daily_used: bool) -> List[dict]:
    """Configured workbooks expected to contribute to this period.

    Monthly grain → the annual M/C summary workbooks.
    Daily grain   → the per-month daily workbooks of daily-capable plants
                    (those that also have a monthly grid baseline) for the
                    selected months.
    """
    out: List[dict] = []
    if daily_used:
        grid_plants = {s["plant"] for s in sources.ANNUAL_SOURCES}
        for plant, cfg in sources.DAILY_SOURCES.items():
            if plant not in grid_plants:
                continue
            for ym, fid in (cfg.get("files") or {}).items():
                if ym in period_months:
                    out.append({
                        "plant": plant,
                        "file_id": fid,
                        "grain": "daily",
                        "month": ym,
                        "label": f"{sources.PLANT_NAMES.get(plant, plant)} daily ({ym})",
                    })
    else:
        for s in sources.ANNUAL_SOURCES:
            out.append({
                "plant": s["plant"],
                "file_id": s["file_id"],
                "grain": "monthly",
                "month": "",
                "label": s["title"],
            })
    return out


# ---------------------------------------------------------------------------
# Tier 1 — Completeness
# ---------------------------------------------------------------------------
def _machine_match(data_code: str, master_codes: set, master_norm: dict,
                   master_num: dict) -> Optional[str]:
    """Deterministic match of a data machine code to a master code.

    Exact (normalised) first, then trailing-number. Returns the master code or
    None. Fuzzy name-matching by Claude (caller) only runs on what stays None.
    """
    if data_code in master_codes:
        return data_code
    nk = _norm_code(data_code)
    if nk in master_norm:
        return master_norm[nk]
    num = _num_key(data_code)
    if num is not None and num in master_num:
        return master_num[num]
    return None


def _scope_plants(period_rows: List[Record], masters: dict, daily_used: bool) -> set:
    """Plants whose master roster we hold the period accountable to.

    Daily grain only covers daily-capable plants, so we only expect those.
    Monthly grain expects every plant in the master roster.
    """
    if daily_used:
        return {r.plant for r in period_rows}
    return set(masters["machines"].keys())


def tier1_completeness(
    period_months: List[str],
    period_rows: List[Record],
    source_reports: List[dict],
    masters: dict,
    fy_months_with_data: List[str],
    daily_used: bool,
    as_of: datetime.date,
    matcher: Optional[Callable[[List[str], List[str]], Dict[str, str]]] = None,
) -> Tuple[List[dict], dict]:
    """Returns (issues, score). Score = files/machines/months found vs expected."""
    issues: List[dict] = []

    # --- Files found vs expected ---
    expected_files = expected_files_for(period_months, daily_used)
    loaded_by_file = {}
    for rep in source_reports or []:
        fid = rep.get("file_id")
        if fid:
            loaded_by_file.setdefault(fid, 0)
            loaded_by_file[fid] += rep.get("record_count", 0) or 0
    files_found = 0
    for ef in expected_files:
        if loaded_by_file.get(ef["file_id"], 0) > 0:
            files_found += 1
        else:
            issues.append(_issue(
                1, WARNING,
                f"No data read from {ef['label']} — workbook returned nothing for "
                "this period.",
                plant=ef["plant"], month=ef["month"], file=ef["file_id"],
            ))
    files_expected = len(expected_files)

    # --- Machines present (master roster, both ways) ---
    scope = _scope_plants(period_rows, masters, daily_used)
    machines_expected = 0
    machines_present = 0
    present_by_plant: Dict[str, set] = {}
    for r in period_rows:
        if r.machine:
            present_by_plant.setdefault(r.plant, set()).add(r.machine)

    for plant in sorted(scope):
        master_codes = masters["machines"].get(plant, set())
        machines_expected += len(master_codes)
        if not master_codes:
            continue
        master_norm = {_norm_code(c): c for c in master_codes}
        master_num = {}
        for c in master_codes:
            n = _num_key(c)
            if n is not None:
                master_num.setdefault(n, c)

        present = present_by_plant.get(plant, set())
        matched_master: set = set()
        unmatched_data: List[str] = []
        for dc in present:
            mc = _machine_match(dc, master_codes, master_norm, master_num)
            if mc is not None:
                matched_master.add(mc)
            else:
                unmatched_data.append(dc)

        # Claude fuzzy-match only the leftovers (names → master names).
        if unmatched_data and matcher is not None:
            try:
                mapping = matcher(unmatched_data, sorted(master_codes)) or {}
            except Exception:
                mapping = {}
            still: List[str] = []
            for dc in unmatched_data:
                tgt = mapping.get(dc)
                if tgt in master_codes:
                    matched_master.add(tgt)
                else:
                    still.append(dc)
            unmatched_data = still

        machines_present += len(matched_master)

        missing = master_codes - matched_master
        for mc in sorted(missing):
            issues.append(_issue(
                1, WARNING,
                f"Machine {mc} is in the master roster but has no data this period "
                "(shown as a gap, not a zero).",
                plant=plant, machine=mc,
            ))
        for dc in sorted(unmatched_data):
            issues.append(_issue(
                1, WARNING,
                f"Machine {dc} appears in the data but is not in the master roster "
                "— could not be matched to a known machine.",
                plant=plant, machine=dc,
            ))

    # --- Segments & moulds present vs master roster (both ways) ---
    seg_by_plant: Dict[str, set] = {}
    mould_by_plant: Dict[str, set] = {}
    for r in period_rows:
        if r.segment:
            seg_by_plant.setdefault(r.plant, set()).add(r.segment)
        if r.mould:
            mould_by_plant.setdefault(r.plant, set()).add(r.mould)

    for plant in sorted(scope):
        m_segs = masters["segments"].get(plant, set())
        present_segs = seg_by_plant.get(plant, set())
        for s in sorted(m_segs - present_segs):
            issues.append(_issue(
                1, WARNING,
                f"Segment/line '{s}' is in the master roster but has no data this "
                "period (shown as a gap, not a zero).",
                plant=plant,
            ))
        for s in sorted(present_segs - m_segs):
            issues.append(_issue(
                1, WARNING,
                f"Segment/line '{s}' appears in the data but is not in the master "
                "roster.",
                plant=plant,
            ))

        m_moulds = masters["moulds"].get(plant, set())
        present_moulds = mould_by_plant.get(plant, set())
        for mo in sorted(m_moulds - present_moulds):
            issues.append(_issue(
                1, WARNING,
                f"Mould '{mo}' is in the master roster but has no data this period "
                "(shown as a gap, not a zero).",
                plant=plant,
            ))
        for mo in sorted(present_moulds - m_moulds):
            issues.append(_issue(
                1, WARNING,
                f"Mould '{mo}' appears in the data but is not in the master roster.",
                plant=plant,
            ))

    # --- Months populated (FY coverage) + overdue period months ---
    fy_have = set(fy_months_with_data)
    months_populated = len([m for m in sources.FY_MONTHS if m in fy_have])
    months_expected = len(sources.FY_MONTHS)

    as_of_ym = f"{as_of.year:04d}-{as_of.month:02d}"
    for m in period_months:
        if m <= as_of_ym and m not in fy_have:
            issues.append(_issue(
                1, WARNING,
                f"Month {m} is within the requested period and overdue, but holds "
                "no data yet.",
                month=m,
            ))

    # --- Required cells non-blank (don't treat a blank as zero) ---
    for r in period_rows:
        label_month = r.period or r.date
        if r.total_count > 0 and r.ideal_hours <= 0 and r.grain == "monthly":
            issues.append(_issue(
                1, WARNING,
                f"{r.machine or r.mould}: output recorded but Ideal Hours is blank — "
                "efficiency cannot be confirmed.",
                plant=r.plant, machine=r.machine, month=label_month,
            ))
        if r.actual_hours > 0 and r.total_count <= 0 and r.grain == "monthly":
            issues.append(_issue(
                1, WARNING,
                f"{r.machine or r.mould}: run hours recorded but Output is blank — "
                "a blank output is not assumed to be zero.",
                plant=r.plant, machine=r.machine, month=label_month,
            ))

    score = {
        "files": (files_found, files_expected),
        "machines": (machines_present, machines_expected),
        "months": (months_populated, months_expected),
    }
    return issues, score


# ---------------------------------------------------------------------------
# Tier 2 — Reconciliation
# ---------------------------------------------------------------------------
def tier2_reconciliation(
    source_reports: List[dict],
    period_rows: List[Record],
    computed,
) -> List[dict]:
    issues: List[dict] = []

    # Detail rows vs the sheet's own TOTAL row (plant == Σ machines).
    for rep in source_reports or []:
        recon = rep.get("reconcile")
        if not recon:
            continue
        if not recon.get("ok", True):
            issues.append(_issue(
                2, WARNING,
                f"{rep.get('title', rep.get('plant', ''))}: detail rows sum to "
                f"{recon['detail_total']:.0f} but the sheet TOTAL is "
                f"{recon['grid_total']:.0f} ({recon['diff_pct']:.1f}% off).",
                plant=rep.get("plant", ""), file=rep.get("file_id", ""),
                sheet=rep.get("tab", ""),
            ))

    # Hierarchy reconciliation: segment == Σ its lines/machines, and the plant
    # rolls up cleanly. Output recorded outside any segment, or a machine split
    # across segments, means a segment total will not reconcile to its machines.
    mc_segments: Dict[tuple, set] = {}
    orphan_by_plant: Dict[str, float] = {}
    seg_total: Dict[tuple, float] = {}
    mc_total: Dict[tuple, float] = {}
    for r in period_rows:
        if r.total_count <= 0:
            continue
        if r.segment:
            seg_total[(r.plant, r.segment)] = (
                seg_total.get((r.plant, r.segment), 0.0) + r.total_count
            )
        else:
            orphan_by_plant[r.plant] = orphan_by_plant.get(r.plant, 0.0) + r.total_count
        if r.machine:
            mc_segments.setdefault((r.plant, r.machine), set()).add(r.segment)
            if r.segment:
                mc_total[(r.plant, r.segment, r.machine)] = (
                    mc_total.get((r.plant, r.segment, r.machine), 0.0) + r.total_count
                )

    for (plant, mc), segs in mc_segments.items():
        real = {s for s in segs if s}
        if len(real) > 1:
            issues.append(_issue(
                2, WARNING,
                f"{mc} is split across segments {', '.join(sorted(real))} — segment "
                "totals cannot reconcile cleanly to machine totals.",
                plant=plant, machine=mc,
            ))

    for (plant, seg), tot in seg_total.items():
        lines_sum = sum(
            v for (p, s, _m), v in mc_total.items() if p == plant and s == seg
        )
        if abs(tot - lines_sum) > 0.01:
            issues.append(_issue(
                2, WARNING,
                f"Segment '{seg}' total ({tot:,.0f}) does not equal the sum of its "
                f"lines ({lines_sum:,.0f}).",
                plant=plant,
            ))

    for plant, orphan in orphan_by_plant.items():
        if orphan > 0:
            issues.append(_issue(
                2, WARNING,
                f"{orphan:,.0f} units of output have no segment/line assigned — they "
                "roll up to the plant but not to any segment.",
                plant=plant,
            ))

    # Engine self-reconcile: published total must equal the row sum exactly.
    src_total = sum(r.total_count for r in period_rows)
    src_reject = sum(r.reject_count for r in period_rows)
    if abs(computed.total_count - src_total) > 0.01:
        issues.append(_issue(
            2, ERROR,
            f"Internal mismatch: published output {computed.total_count:.0f} ≠ row "
            f"sum {src_total:.0f}.",
        ))
    if abs(computed.reject_count - src_reject) > 0.01:
        issues.append(_issue(
            2, ERROR,
            f"Internal mismatch: published rejects {computed.reject_count:.0f} ≠ row "
            f"sum {src_reject:.0f}.",
        ))
    return issues


# ---------------------------------------------------------------------------
# Tier 3 — Validity (impossible values)
# ---------------------------------------------------------------------------
def tier3_validity(period_rows: List[Record], computed) -> List[dict]:
    issues: List[dict] = []
    for r in period_rows:
        m = r.period or r.date
        loc = dict(plant=r.plant, machine=r.machine or r.mould, month=m,
                   file=r.source_file, sheet=r.source_tab)
        if r.downtime_min < 0:
            issues.append(_issue(3, ERROR, f"Negative downtime ({r.downtime_min:.0f} min).", **loc))
        if r.total_count < 0:
            issues.append(_issue(3, ERROR, f"Negative output ({r.total_count:.0f}).", **loc))
        if r.reject_count < 0:
            issues.append(_issue(3, ERROR, f"Negative reject count ({r.reject_count:.0f}).", **loc))
        if r.reject_count > r.total_count and r.reject_count > 0:
            issues.append(_issue(
                3, ERROR,
                f"Rejects ({r.reject_count:.0f}) exceed output ({r.total_count:.0f}).",
                **loc,
            ))
        ppt = r.shift_len_min - r.planned_stops_min
        if r.grain == "daily" and r.shift_len_min > 0 and r.downtime_min > ppt > 0:
            issues.append(_issue(
                3, ERROR,
                f"Downtime ({r.downtime_min:.0f} min) exceeds planned production time "
                f"({ppt:.0f} min).",
                **loc,
            ))
        if r.grain == "monthly" and r.ideal_hours > 0 and r.actual_hours > r.ideal_hours * 1.001:
            issues.append(_issue(
                3, ERROR,
                f"Actual hours ({r.actual_hours:.0f}) exceed ideal ({r.ideal_hours:.0f}) "
                "— utilisation over 100% (impossible).",
                **loc,
            ))

    # Aggregate impossible ratios (a ratio over 100% is an invalid value).
    if computed.oee_available and computed.performance_raw > 1.0:
        issues.append(_issue(3, ERROR,
            f"Performance ({computed.performance_raw * 100:.1f}%) exceeds 100% — check ideal rates."))
    if computed.utilisation > 1.0:
        issues.append(_issue(3, ERROR,
            f"Utilisation ({computed.utilisation_pct:.1f}%) exceeds 100% — actual hours "
            "exceed ideal."))
    if computed.output_efficiency > 1.0:
        issues.append(_issue(3, ERROR,
            f"Output efficiency ({computed.output_efficiency_pct:.1f}%) exceeds 100% — "
            "actual output exceeds ideal."))
    return issues


# ---------------------------------------------------------------------------
# Tier 4 — Plausibility
# ---------------------------------------------------------------------------
def tier4_plausibility(
    period_rows: List[Record],
    master_rows: List[Record],
    period_months: List[str],
    masters: dict,
) -> List[dict]:
    issues: List[dict] = []

    # Duplicates: the same machine/mould appearing twice for the same period/date.
    seen: Dict[tuple, int] = {}
    for r in period_rows:
        key = (r.plant, r.machine or r.mould, r.period, r.date, r.segment)
        seen[key] = seen.get(key, 0) + 1
    for key, n in seen.items():
        if n > 1 and (key[1]):
            issues.append(_issue(
                4, WARNING,
                f"Duplicate rows ({n}×) for {key[1]} in {key[2] or key[3]} — possible "
                "double-read.",
                plant=key[0], machine=key[1], month=key[2] or key[3],
            ))

    # Very high rejection rate (possible, but implausible — worth a look).
    total_out = sum(r.total_count for r in period_rows)
    total_rej = sum(r.reject_count for r in period_rows)
    if total_out > 0 and total_rej / total_out > 0.5:
        issues.append(_issue(
            4, WARNING,
            f"Very high overall rejection rate ({total_rej / total_out * 100:.1f}%) — "
            "confirm the reject figures are correct.",
        ))

    # Unit mismatch vs the plant's configured unit.
    plant_unit = {s["plant"]: s["unit"] for s in sources.ANNUAL_SOURCES}
    for r in period_rows:
        exp = plant_unit.get(r.plant)
        if exp and r.unit and r.unit != exp:
            issues.append(_issue(
                4, WARNING,
                f"{r.machine or r.mould}: unit '{r.unit}' does not match the plant's "
                f"expected unit '{exp}'.",
                plant=r.plant, machine=r.machine, month=r.period or r.date,
            ))

    # Output outliers: a machine far from its plant's median machine output.
    by_plant_machine: Dict[str, Dict[str, float]] = {}
    for r in period_rows:
        if not r.machine:
            continue
        by_plant_machine.setdefault(r.plant, {}).setdefault(r.machine, 0.0)
        by_plant_machine[r.plant][r.machine] += r.total_count
    for plant, mc_out in by_plant_machine.items():
        outs = [v for v in mc_out.values() if v > 0]
        if len(outs) < MIN_PLANT_MACHINES_FOR_OUTLIER:
            continue
        med = sorted(outs)[len(outs) // 2]
        if med <= 0:
            continue
        for mc, v in mc_out.items():
            if v <= 0:
                continue
            ratio = v / med
            if ratio > OUTLIER_HIGH or ratio < OUTLIER_LOW:
                issues.append(_issue(
                    4, WARNING,
                    f"{mc}: output {v:,.0f} is {ratio:.1f}× the plant median "
                    f"({med:,.0f}) — looks like an outlier.",
                    plant=plant, machine=mc,
                ))

    # Sudden zeros: a machine with prior-FY history but no output this period.
    hist_by_machine: Dict[tuple, float] = {}
    for r in master_rows:
        if r.period in period_months:
            continue
        if r.machine:
            hist_by_machine[(r.plant, r.machine)] = (
                hist_by_machine.get((r.plant, r.machine), 0.0) + r.total_count
            )
    present_out: Dict[tuple, float] = {}
    for r in period_rows:
        if r.machine:
            present_out[(r.plant, r.machine)] = (
                present_out.get((r.plant, r.machine), 0.0) + r.total_count
            )
    for (plant, mc), hist in hist_by_machine.items():
        if hist > 0 and present_out.get((plant, mc), 0.0) == 0.0 and (plant, mc) in present_out:
            issues.append(_issue(
                4, WARNING,
                f"{mc}: produced output in earlier months but zero this period — "
                "confirm the line was actually idle.",
                plant=plant, machine=mc,
            ))
    return issues


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _score_label(score: dict) -> str:
    f = score["files"]
    m = score["machines"]
    mo = score["months"]
    return f"{f[0]}/{f[1]} files · {m[0]}/{m[1]} machines · {mo[0]}/{mo[1]} months"


def full_confirm(
    period_months: List[str],
    period_rows: List[Record],
    source_reports: List[dict],
    master_rows: List[Record],
    fy_months_with_data: List[str],
    computed,
    daily_used: bool,
    as_of: datetime.date,
    extra_recon_warnings: Optional[List[str]] = None,
    matcher: Optional[Callable[[List[str], List[str]], Dict[str, str]]] = None,
) -> dict:
    """Run all four tiers and return a structured confirmation result.

    Deterministic. ``matcher`` (optional) is used only to fuzzy-map leftover
    machine *names* to master names — never to read or compute a figure.
    """
    masters = build_masters(master_rows)

    t1, score = tier1_completeness(
        period_months, period_rows, source_reports, masters,
        fy_months_with_data, daily_used, as_of, matcher=matcher,
    )
    t2 = tier2_reconciliation(source_reports, period_rows, computed)
    t3 = tier3_validity(period_rows, computed)
    t4 = tier4_plausibility(period_rows, master_rows, period_months, masters)

    # Read-time reconcile notes not already captured structurally.
    for w in extra_recon_warnings or []:
        t2.append(_issue(2, WARNING, w))

    tiers = {1: t1, 2: t2, 3: t3, 4: t4}
    issues = t1 + t2 + t3 + t4

    # No data at all for a requested, overdue period is an error-level gap.
    if not period_rows:
        gap = _issue(1, ERROR, "No data could be published for this period.")
        tiers[1].append(gap)
        issues.append(gap)

    err = sum(1 for i in issues if i["severity"] == ERROR)
    warn = sum(1 for i in issues if i["severity"] == WARNING)
    status = ERROR if err else (WARNING if warn else "pass")

    return {
        "status": status,
        "score": score,
        "score_label": _score_label(score),
        "issues": issues,
        "tiers": tiers,
        "counts": {"error": err, "warning": warn, "total": len(issues)},
        "reconciled": status == "pass",
        "summary": None,
    }
