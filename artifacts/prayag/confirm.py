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

import calendar
import datetime
import hashlib
import json
import math
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
INFO = "info"

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
    quarantined: bool = False,
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
        # A quarantined hard-error row is held aside and excluded from the
        # published metrics. It is surfaced as a NOTE, never as a sign-off blocker.
        "quarantined": quarantined,
    }


def _calendar_hours(period_ym: str) -> float:
    """Physical maximum run-hours in the calendar month of ``period_ym``.

    ``days_in_month × 24`` (e.g. May = 31×24 = 744). Returns 0.0 when the label
    cannot be parsed, in which case the calendar-ceiling check is skipped.
    """
    try:
        parts = str(period_ym).split("-")
        y, mo = int(parts[0]), int(parts[1])
        return calendar.monthrange(y, mo)[1] * 24.0
    except Exception:
        return 0.0


def _month_due(ym: str, as_of: datetime.date) -> bool:
    """True if the calendar month has fully ended on/before ``as_of``.

    The current (in-progress) month and any future month are NOT due.
    """
    try:
        y, mo = int(str(ym)[:4]), int(str(ym)[5:7])
        last = datetime.date(y, mo, calendar.monthrange(y, mo)[1])
        return last < as_of
    except Exception:
        return False


def _median(values: List[float]) -> float:
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


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
    Daily grain   → the per-month daily workbooks of every plant that has one,
                    for the selected months (a plant need not have a monthly grid
                    baseline to contribute daily run hours + output).
    """
    out: List[dict] = []
    if daily_used:
        for plant, cfg in sources.DAILY_SOURCES.items():
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

        # Daily data sometimes reports by a different machine-identity system than
        # the monthly roster (e.g. Moulding logs by mould code, not M/C number).
        # When NOTHING matches we can't do a per-machine completeness check against
        # the grid — say so once and count the reporting machines so the score
        # isn't tanked, rather than emitting dozens of unmatched/missing warnings.
        if daily_used and present and not matched_master:
            machines_present += min(len(present), len(master_codes))
            issues.append(_issue(
                1, WARNING,
                f"{plant}: daily data is reported by a machine code that doesn't map "
                f"to the monthly roster ({len(present)} reporting) — per-machine "
                "completeness against the grid isn't available for this view.",
                plant=plant,
            ))
            continue

        machines_present += len(matched_master)

        missing = master_codes - matched_master
        if daily_used:
            # In a sub-monthly window a roster machine simply may not have run —
            # that's normal, not a data gap. Collapse to one summary line.
            if missing:
                issues.append(_issue(
                    1, WARNING,
                    f"{plant}: {len(missing)} of {len(master_codes)} machine(s) had "
                    "no run in this window (normal for a short window, not a data gap).",
                    plant=plant,
                ))
            if unmatched_data:
                issues.append(_issue(
                    1, WARNING,
                    f"{plant}: {len(unmatched_data)} daily machine code(s) could not be "
                    "matched to the monthly roster.",
                    plant=plant,
                ))
        else:
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

    # --- Months populated vs months DUE ---
    # An in-progress (current) month and any future month are not yet due, so a
    # blank there is expected and must NOT count against completeness. The score
    # denominator is the count of FY months that have actually ended.
    fy_have = set(fy_months_with_data)
    due_months = [m for m in sources.FY_MONTHS if _month_due(m, as_of)]
    months_expected = len(due_months)
    months_populated = len([m for m in due_months if m in fy_have])

    as_of_ym = f"{as_of.year:04d}-{as_of.month:02d}"
    for m in period_months:
        if m in fy_have:
            continue
        if m == as_of_ym:
            # Current period — still in progress. Informational, never a blocker.
            issues.append(_issue(
                1, INFO,
                f"Month {m} is the current period and still in progress — partial "
                "or no data is expected.",
                month=m,
            ))
        elif _month_due(m, as_of):
            # Ended but no data present — a genuine completeness gap.
            issues.append(_issue(
                1, WARNING,
                f"Month {m} has ended but holds no data yet.",
                month=m,
            ))
        # Future months (not yet due, not current) are not expected — no issue.

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
# Tier 3 — Validity (physical possibility, not the planned baseline)
# ---------------------------------------------------------------------------
# Two distinct classes:
#   HARD ERROR  — physically impossible. The row is QUARANTINED: held aside with
#                 its raw value + provenance and excluded from published metrics.
#                 (actual hours > calendar-month hours, reject > output, negatives,
#                 a required numeric cell that is not a number.)
#   WARNING     — possible but worth review; shown, NOT quarantined, NOT a blocker.
#                 (ran above the planned ideal baseline — utilisation/efficiency
#                 over 100% but still within the calendar ceiling.)
# The app never auto-corrects a flagged value — corrections happen in the source
# sheet and the next pull resolves them.
def tier3_row_classify(
    period_rows: List[Record],
) -> Tuple[List[Record], List[Record], List[dict]]:
    """Pure row-level Tier-3 classification.

    Returns ``(clean_rows, quarantined_rows, issues)``. A row is quarantined when
    it carries at least one physically-impossible value. Warnings never quarantine.
    """
    clean: List[Record] = []
    quarantined: List[Record] = []
    issues: List[dict] = []

    for r in period_rows:
        m = r.period or r.date
        loc = dict(plant=r.plant, machine=r.machine or r.mould, month=m,
                   file=r.source_file, sheet=r.source_tab)
        hard: List[str] = []
        warn_msg: Optional[str] = None

        # Non-numeric required cell (a NaN that slipped past the parsers).
        if any(isinstance(x, float) and math.isnan(x)
               for x in (r.total_count, r.reject_count, r.actual_hours, r.downtime_min)):
            hard.append("A required numeric cell is not a number.")

        if r.downtime_min < 0:
            hard.append(f"Negative downtime ({r.downtime_min:.0f} min).")
        if r.total_count < 0:
            hard.append(f"Negative output ({r.total_count:.0f}).")
        if r.reject_count < 0:
            hard.append(f"Negative reject count ({r.reject_count:.0f}).")
        if r.reject_count > r.total_count and r.reject_count > 0:
            hard.append(
                f"Rejects ({r.reject_count:.0f}) exceed output ({r.total_count:.0f}).")

        ppt = r.shift_len_min - r.planned_stops_min
        if r.grain == "daily" and r.shift_len_min > 0 and r.downtime_min > ppt > 0:
            hard.append(
                f"Downtime ({r.downtime_min:.0f} min) exceeds planned production time "
                f"({ppt:.0f} min).")

        # Monthly hours: the physical ceiling is the calendar month, NOT the
        # planned ideal. Above-calendar = impossible (quarantine); above-ideal but
        # within calendar = utilisation over 100% (warning only).
        if r.grain == "monthly" and r.actual_hours > 0:
            cal = _calendar_hours(m)
            if cal > 0 and r.actual_hours > cal * 1.001:
                hard.append(
                    f"Actual hours ({r.actual_hours:.0f}) exceed the calendar maximum "
                    f"for {m} ({cal:.0f}h) — physically impossible.")
            elif r.ideal_hours > 0 and r.actual_hours > r.ideal_hours * 1.001:
                warn_msg = (
                    f"Utilisation over 100% — ran above the planned baseline "
                    f"(ideal {r.ideal_hours:.0f}h, actual {r.actual_hours:.0f}h). "
                    "Verify the ideal-hours baseline or the logged hours.")

        for hm in hard:
            issues.append(_issue(3, ERROR, hm, quarantined=True, **loc))
        if warn_msg:
            issues.append(_issue(3, WARNING, warn_msg, **loc))

        if hard:
            quarantined.append(r)
        else:
            clean.append(r)

    return clean, quarantined, issues


def tier3_aggregate(computed) -> List[dict]:
    """Aggregate ratio checks on the PUBLISHED (post-quarantine) metrics.

    A ratio over 100% within the calendar ceiling means the line ran above its
    planned baseline — possible, so a WARNING, never a hard error.
    """
    issues: List[dict] = []
    if computed.oee_available and computed.performance_raw > 1.0:
        issues.append(_issue(
            3, WARNING,
            f"Performance ({computed.performance_raw * 100:.1f}%) exceeds 100% — ran "
            "above the planned baseline; verify the ideal rates."))
    if computed.utilisation > 1.0:
        issues.append(_issue(
            3, WARNING,
            f"Utilisation ({computed.utilisation_pct:.1f}%) exceeds 100% — ran above "
            "the planned ideal hours; verify the ideal-hours baseline."))
    if computed.output_efficiency > 1.0:
        issues.append(_issue(
            3, WARNING,
            f"Output efficiency ({computed.output_efficiency_pct:.1f}%) exceeds 100% — "
            "output above the planned ideal; verify the ideal-output baseline."))
    return issues


# ---------------------------------------------------------------------------
# Tier 4 — Plausibility
# ---------------------------------------------------------------------------
def tier4_plausibility(
    period_rows: List[Record],
    master_rows: List[Record],
    period_months: List[str],
    masters: dict,
    daily_used: bool = False,
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
    # Tempering: a structurally high/low machine (consistently so across its own
    # prior months) is not a data error. For monthly-grain views we additionally
    # compare each machine to ITS OWN prior-month baseline and only flag when it is
    # an outlier on BOTH the plant median AND its own history — this cuts false
    # positives for machines that are simply bigger or smaller than their peers.
    own_monthly: Dict[tuple, List[float]] = {}   # (plant,mc) -> prior monthly outputs
    if not daily_used:
        for r in master_rows:
            if r.period in period_months:
                continue
            if r.machine and r.total_count > 0:
                own_monthly.setdefault((r.plant, r.machine), []).append(r.total_count)
    months_present: Dict[tuple, set] = {}        # (plant,mc) -> in-period months seen
    for r in period_rows:
        if r.machine:
            months_present.setdefault((r.plant, r.machine), set()).add(r.period or r.date)

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
            if not (ratio > OUTLIER_HIGH or ratio < OUTLIER_LOW):
                continue
            # Temper against the machine's own prior-month baseline (monthly grain).
            own = own_monthly.get((plant, mc))
            if own:
                own_med = _median(own)
                n_mon = max(len(months_present.get((plant, mc), ())), 1)
                expected = own_med * n_mon
                if expected > 0:
                    own_ratio = v / expected
                    if OUTLIER_LOW <= own_ratio <= OUTLIER_HIGH:
                        # Consistent with its own history — structurally high/low,
                        # not a data error.
                        continue
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


def baseline_confirm(period_rows: List[Record]) -> List[dict]:
    """Surface the planned-hours baseline provenance for monthly machines.

    Utilisation and output-efficiency are measured against a per-machine
    planned-hours baseline (config). A machine still on the sheet's flat
    placeholder (``ideal_source == 'sheet'``) has no real target set, so its
    utilisation/efficiency is only indicative — flagged per plant as a WARNING.
    Machines whose denominator comes from config are noted (INFO) so the
    provenance is explicit. Deterministic; never invents a baseline.
    """
    issues: List[dict] = []
    sheet_by_plant: Dict[str, set] = {}
    config_machines: List[tuple] = []
    placeholder_by_plant: Dict[str, float] = {}
    for r in period_rows:
        if r.grain != "monthly" or not r.machine:
            continue
        if getattr(r, "ideal_source", "sheet") == "config":
            config_machines.append((r.plant, r.machine))
        else:
            sheet_by_plant.setdefault(r.plant, set()).add(r.machine)
            placeholder_by_plant.setdefault(
                r.plant, getattr(r, "ideal_hours_sheet", r.ideal_hours) or 0.0
            )

    for plant in sorted(sheet_by_plant):
        n = len(sheet_by_plant[plant])
        ph = placeholder_by_plant.get(plant, 0.0)
        issues.append(_issue(
            1, WARNING,
            f"{plant}: {n} machine(s) have no planned-hours baseline set — "
            f"utilisation/efficiency use the sheet placeholder "
            f"({ph:,.0f} h). Set them in baselines.json for a real target.",
            plant=plant,
        ))

    if config_machines:
        names = ", ".join(sorted({m for _p, m in config_machines}))
        issues.append(_issue(
            1, INFO,
            f"Planned-hours baseline applied from config for: {names}.",
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

    # Tier 3 row-level: split physically-impossible rows into quarantine. The
    # clean rows are what publish; the quarantined ones are surfaced as notes.
    # ``computed`` is expected to already be the post-quarantine (published)
    # metrics so the self-reconcile and aggregate checks agree with clean_rows.
    clean_rows, _quarantined_rows, t3_rows = tier3_row_classify(period_rows)

    t1, score = tier1_completeness(
        period_months, period_rows, source_reports, masters,
        fy_months_with_data, daily_used, as_of, matcher=matcher,
    )
    t2 = tier2_reconciliation(source_reports, clean_rows, computed)
    t3 = t3_rows + tier3_aggregate(computed)
    t4 = tier4_plausibility(clean_rows, master_rows, period_months, masters, daily_used)

    # Planned-hours baseline provenance (config vs sheet placeholder).
    t1 = t1 + baseline_confirm(period_rows)

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

    # Stable per-issue identity so a manager can acknowledge an individual issue
    # and have that acknowledgement survive figure drift. ``issues`` and the
    # per-tier lists hold the same dict objects, so this stamps both.
    for i in issues:
        i["key"] = issue_key(i)

    # A quarantined hard error is a NOTE, not a blocker (the row is already held
    # aside). Only un-quarantined errors gate sign-off. INFO never affects status.
    blocking = sum(1 for i in issues if i["severity"] == ERROR and not i.get("quarantined"))
    quarantined_n = sum(1 for i in issues if i.get("quarantined"))
    warn = sum(1 for i in issues if i["severity"] == WARNING)
    info = sum(1 for i in issues if i["severity"] == INFO)
    status = ERROR if blocking else (WARNING if (warn or quarantined_n) else "pass")

    return {
        "status": status,
        "score": score,
        "score_label": _score_label(score),
        "issues": issues,
        "tiers": tiers,
        "counts": {
            "error": blocking,
            "warning": warn,
            "quarantined": quarantined_n,
            "info": info,
            "total": len(issues),
        },
        "reconciled": status == "pass",
        "summary": None,
    }


def issue_key(issue: dict) -> str:
    """Stable identity for a single issue, robust to changing magnitudes.

    Built from the issue's structural location (tier, plant, machine, month,
    sheet, file) plus its message with every number normalised out. A recurring
    known anomaly — e.g. PIPE's by-design reconcile offset, or a line that runs
    above its planned baseline (utilisation > 100%) — therefore keeps the SAME
    key as its exact figures drift from period to period, so a manager's
    acknowledgement of it is not silently lost on the next data pull. Pure and
    network-free.
    """
    msg = re.sub(r"[0-9][0-9.,%]*", "#", str(issue.get("message", "")))
    ident = "|".join(
        str(x)
        for x in (
            issue.get("tier", ""),
            issue.get("plant", ""),
            issue.get("machine", ""),
            issue.get("month", ""),
            issue.get("sheet", ""),
            issue.get("file", ""),
            msg,
        )
    )
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:16]


def confirmation_fingerprint(confirmation: dict) -> str:
    """A stable, deterministic hash of the data state a manager would sign off on.

    Built from the overall status and every issue's identity (tier, severity,
    plant, machine, month, sheet, file, message). If the underlying data later
    changes — an error is fixed, a new gap appears — the fingerprint changes, so
    a prior sign-off no longer applies and the figures re-gate automatically.
    Pure and network-free.
    """
    ident = sorted(
        (
            i.get("tier"), i.get("severity"), i.get("plant", ""),
            i.get("machine", ""), i.get("month", ""), i.get("sheet", ""),
            i.get("file", ""), i.get("message", ""),
        )
        for i in confirmation.get("issues", [])
    )
    payload = json.dumps(
        {"status": confirmation.get("status"), "issues": ident},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
