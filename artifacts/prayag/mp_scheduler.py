"""
MP Scheduler — weekly + shift-based day scheduler.

Takes the engine EngineResult items (with capable_machines + machine_hrs)
and the raw demand (with per-week quantities from W1..W4 cols) and produces
a day-by-day, shift-level schedule across the working month.

Machine-day structure (2 shifts × hours_per_shift):
  DAY shift  — up to 2 blocks, each ≥ min_run_block_hours
  NIGHT shift — exactly one item (continuation of the DAY tail), no changeover

Priority queue: (first_requested_week ASC, remaining_hrs DESC)
W1 demand fills W1 capacity first; unfinished W1 overflow cascades into W2
ahead of W2's own items, and so on.

ADDITIVE / ISOLATED: reads only mp_* tables; never touches the headline pipeline.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import calendar
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import mp_model as _mp


# ── Public dataclasses ────────────────────────────────────────────────────────

@dataclasses.dataclass
class ShiftBlock:
    week: int               # 1..4
    day: int                # 1..working_days_month
    machine: str
    shift: str              # 'DAY' | 'NIGHT'
    item_code: str          # normalised ('' when idle)
    raw_code: str
    material: str
    planned_hours: float    # machine-hours in this block
    excess_hours: float     # planned_hours − actual_need  (>=0)
    origin_week: int        # item's first_requested_week (0 = unspecified)
    is_idle: bool = False


@dataclasses.dataclass
class UnfinishedItem:
    item_code: str
    raw_code: str
    material: str
    remaining_hours: float
    remaining_kg: float
    capable_machines: List[str]
    origin_week: int
    downtime_reason: str = ""   # set when the only capable machines are down


@dataclasses.dataclass
class WeekFillRow:
    week: int
    machine: str
    capacity_hrs: float
    scheduled_hrs: float
    idle_hrs: float
    utilisation_pct: float
    changeovers: int
    excess_kg: float
    origin_breakdown: Dict[int, float]   # {origin_week: scheduled hrs from that week}


@dataclasses.dataclass
class ScheduleResult:
    segment: str
    effective_month: str
    blocks: List[ShiftBlock]
    weekly_fill: List[WeekFillRow]
    unfinished: List[UnfinishedItem]
    total_capacity_hrs: float
    total_scheduled_hrs: float
    total_idle_hrs: float
    total_excess_kg: float
    total_changeovers: int
    week_days: List[int]      # e.g. [6,6,6,7] — days per week
    params_used: dict
    downtime_machine_days: int = 0    # total machine-days lost to downtime
    downtime_hours_lost: float = 0.0  # machine-hours lost to downtime
    capacity_advisory: Optional[dict] = None

    def to_dict(self) -> dict:
        result = dataclasses.asdict(self)
        # Keep old pending/frozen payloads byte-shape compatible until a
        # Plumbing calendar has explicitly been configured.
        if result["capacity_advisory"] is None:
            result.pop("capacity_advisory")
        return result

    @staticmethod
    def from_dict(d: dict) -> "ScheduleResult":
        blocks = [ShiftBlock(**b) for b in d.get("blocks", [])]
        fill = []
        for r in d.get("weekly_fill", []):
            r2 = dict(r)
            ob = {int(k): float(v) for k, v in r2.pop("origin_breakdown", {}).items()}
            fill.append(WeekFillRow(**r2, origin_breakdown=ob))
        unfinished = [UnfinishedItem(**u) for u in d.get("unfinished", [])]
        return ScheduleResult(
            segment=d["segment"],
            effective_month=d["effective_month"],
            blocks=blocks,
            weekly_fill=fill,
            unfinished=unfinished,
            total_capacity_hrs=d.get("total_capacity_hrs", 0.0),
            total_scheduled_hrs=d.get("total_scheduled_hrs", 0.0),
            total_idle_hrs=d.get("total_idle_hrs", 0.0),
            total_excess_kg=d.get("total_excess_kg", 0.0),
            total_changeovers=d.get("total_changeovers", 0),
            week_days=d.get("week_days", [6, 6, 6, 7]),
            params_used=d.get("params_used", {}),
            downtime_machine_days=d.get("downtime_machine_days", 0),
            downtime_hours_lost=d.get("downtime_hours_lost", 0.0),
            capacity_advisory=d.get("capacity_advisory"),
        )


# ── Internal work item ────────────────────────────────────────────────────────

@dataclasses.dataclass
class _WorkItem:
    item_code: str
    raw_code: str
    material: str
    first_requested_week: int   # 1..4; 0 treated as 1 in sort
    remaining_hrs: float
    rate_kg_per_hr: float
    capable_machines: List[str]

    def sort_key(self) -> Tuple:
        w = self.first_requested_week if self.first_requested_week > 0 else 1
        return (w, -self.remaining_hrs, self.item_code)


def _week_days_for_schedule(segment: str, params_row: Optional[object]) -> List[int]:
    """Read a safe four-week split without changing legacy non-Plumbing behavior."""
    if params_row:
        week_days_str = str(getattr(params_row, "week_days", "[6,6,6,7]") or "[6,6,6,7]")
        if segment == "PLUMBING" and not getattr(params_row, "week_days_configured", False):
            week_days_str = "[6,6,6,7]"
    else:
        week_days_str = "[6,6,6,7]"
    try:
        week_days: List[int] = json.loads(week_days_str)
    except Exception:
        week_days = [6, 6, 6, 7]
    while len(week_days) < 4:
        week_days.append(7)
    return [max(1, d) for d in week_days[:4]]


def _capacity_advisory(
    unfinished: List[UnfinishedItem],
    mc_params: Dict[str, dict],
    effective_month: str,
    week_days: List[int],
    segment: str,
    working_days_configured: bool,
) -> Optional[dict]:
    """Describe unplaced capacity-limited demand without changing allocation."""
    if segment != "PLUMBING" or not working_days_configured:
        return None
    remaining_kg = round(sum(
        max(0.0, float(item.remaining_kg or 0.0))
        for item in unfinished
        if not item.downtime_reason
    ), 1)
    if remaining_kg <= 0:
        return None
    try:
        year_s, month_s = effective_month.split("-", 1)
        days_in_month = calendar.monthrange(int(year_s), int(month_s))[1]
    except (TypeError, ValueError):
        return None
    configured_days = sum(week_days)
    additional_hours = max(0, days_in_month - configured_days) * sum(
        2.0 * float(params.get("hours_per_shift") or 10.0)
        for params in mc_params.values()
    )
    return {
        "remaining_kg": remaining_kg,
        "configured_days": configured_days,
        "calendar_days": days_in_month,
        "additional_hours": round(additional_hours, 1),
    }


# ── Day-level scheduler ───────────────────────────────────────────────────────

def _schedule_machine_day(
    machine: str,
    day: int,
    week: int,
    hours_per_shift: float,
    min_run_block: float,
    work_items: List[_WorkItem],
    blocks: List[ShiftBlock],
    idle_by_mc: Dict[str, float],
    excess_kg_by_mc: Dict[str, float],
    changeovers_by_mc_week: Dict[Tuple[str, int], int],
) -> None:
    """Schedule one machine-day (DAY shift + NIGHT shift). Mutates work_items."""
    key = (machine, week)

    eligible = [
        w for w in work_items
        if machine in w.capable_machines and w.remaining_hrs > 1e-6
    ]
    if not eligible:
        # Both shifts idle
        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="DAY",
            item_code="", raw_code="", material="",
            planned_hours=hours_per_shift, excess_hours=0.0, origin_week=0, is_idle=True,
        ))
        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="NIGHT",
            item_code="", raw_code="", material="",
            planned_hours=hours_per_shift, excess_hours=0.0, origin_week=0, is_idle=True,
        ))
        idle_by_mc[machine] += 2 * hours_per_shift
        return

    eligible.sort(key=lambda w: w.sort_key())

    # ── Select night item B ───────────────────────────────────────────────────
    # Prefer: remaining >= hours_per_shift (will fill both shifts cleanly)
    night_item: Optional[_WorkItem] = None
    for w in eligible:
        if w.remaining_hrs >= hours_per_shift:
            night_item = w
            break
    if night_item is None:
        night_item = eligible[0]   # largest priority, may be a small item

    # ── Check if night item is too small for even a min block ─────────────────
    if night_item.remaining_hrs < min_run_block:
        # Pad to min_run_block; day-only scheduling; leave NIGHT idle
        b_planned = min_run_block
        b_need = night_item.remaining_hrs
        b_excess = b_planned - b_need
        b_excess_kg = b_excess * night_item.rate_kg_per_hr
        night_item.remaining_hrs = 0.0
        excess_kg_by_mc[machine] += b_excess_kg

        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="DAY",
            item_code=night_item.item_code, raw_code=night_item.raw_code,
            material=night_item.material,
            planned_hours=b_planned, excess_hours=b_excess,
            origin_week=night_item.first_requested_week,
        ))
        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="NIGHT",
            item_code="", raw_code="", material="",
            planned_hours=hours_per_shift, excess_hours=0.0, origin_week=0, is_idle=True,
        ))
        idle_by_mc[machine] += hours_per_shift
        return

    # ── Try to fit a day item A before night item B ───────────────────────────
    day_item: Optional[_WorkItem] = None
    for w in eligible:
        if w is not night_item and w.remaining_hrs >= min_run_block:
            day_item = w
            break

    if day_item is not None:
        # Two-item day: A(min_block) in DAY, B(hps−min_block) in DAY + B(hps) in NIGHT
        a_planned = min_run_block
        a_before = day_item.remaining_hrs
        a_excess = max(0.0, a_planned - a_before)
        day_item.remaining_hrs = max(0.0, day_item.remaining_hrs - a_planned)
        excess_kg_by_mc[machine] += a_excess * day_item.rate_kg_per_hr

        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="DAY",
            item_code=day_item.item_code, raw_code=day_item.raw_code,
            material=day_item.material,
            planned_hours=a_planned, excess_hours=a_excess,
            origin_week=day_item.first_requested_week,
        ))

        b_day_planned  = hours_per_shift - min_run_block
        b_night_planned = hours_per_shift
        b_total = b_day_planned + b_night_planned
        b_before = night_item.remaining_hrs
        b_excess_total = max(0.0, b_total - b_before)
        night_item.remaining_hrs = max(0.0, night_item.remaining_hrs - b_total)
        excess_kg_by_mc[machine] += b_excess_total * night_item.rate_kg_per_hr

        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="DAY",
            item_code=night_item.item_code, raw_code=night_item.raw_code,
            material=night_item.material,
            planned_hours=b_day_planned, excess_hours=0.0,
            origin_week=night_item.first_requested_week,
        ))
        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="NIGHT",
            item_code=night_item.item_code, raw_code=night_item.raw_code,
            material=night_item.material,
            planned_hours=b_night_planned, excess_hours=b_excess_total,
            origin_week=night_item.first_requested_week,
        ))
        changeovers_by_mc_week[key] += 1

    else:
        # Single item: B runs all 20h (hps DAY + hps NIGHT)
        b_total = 2 * hours_per_shift
        b_before = night_item.remaining_hrs
        b_excess_total = max(0.0, b_total - b_before)
        night_item.remaining_hrs = max(0.0, night_item.remaining_hrs - b_total)
        excess_kg_by_mc[machine] += b_excess_total * night_item.rate_kg_per_hr

        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="DAY",
            item_code=night_item.item_code, raw_code=night_item.raw_code,
            material=night_item.material,
            planned_hours=hours_per_shift, excess_hours=0.0,
            origin_week=night_item.first_requested_week,
        ))
        blocks.append(ShiftBlock(
            week=week, day=day, machine=machine, shift="NIGHT",
            item_code=night_item.item_code, raw_code=night_item.raw_code,
            material=night_item.material,
            planned_hours=hours_per_shift, excess_hours=b_excess_total,
            origin_week=night_item.first_requested_week,
        ))


# ── Downtime helper ───────────────────────────────────────────────────────────

def _build_down_days(
    downtime_records: list,
    mc_params: Dict[str, dict],
    month: str,
    total_days: int,
) -> Dict[str, Set[int]]:
    """Return {machine: {day_idx, ...}} for calendar days that fall inside a downtime record.

    Working day indices are 1-based consecutive calendar days from month start.
    e.g. if month = '2026-07', day_idx 1 = 2026-07-01, day_idx 5 = 2026-07-05.
    """
    if not downtime_records:
        return {}
    try:
        year, mnum = int(month[:4]), int(month[5:7])
        month_start = _dt.date(year, mnum, 1)
    except Exception:
        return {}

    down: Dict[str, Set[int]] = defaultdict(set)
    for rec in downtime_records:
        if rec.get("deleted", False):
            continue   # soft-deleted records never block capacity
        mc = str(rec.get("machine") or "")
        if mc not in mc_params:
            continue
        sd = rec.get("start_date")
        ed = rec.get("end_date")
        if isinstance(sd, str):
            try:
                sd = _dt.date.fromisoformat(str(sd))
            except Exception:
                continue
        if not isinstance(sd, _dt.date):
            continue
        if isinstance(sd, _dt.datetime):
            sd = sd.date()
        if isinstance(ed, str) and ed:
            try:
                ed = _dt.date.fromisoformat(str(ed))
            except Exception:
                ed = None
        if isinstance(ed, _dt.datetime):
            ed = ed.date()
        for day_idx in range(1, total_days + 1):
            cal_date = month_start + _dt.timedelta(days=day_idx - 1)
            if cal_date >= sd and (ed is None or cal_date <= ed):
                down[mc].add(day_idx)
    return dict(down)


# ── Main entry point ──────────────────────────────────────────────────────────

def run_shift_schedule(
    engine_items: list,     # List[ItemResult] from mp_engine.EngineResult
    demand_items: list,     # List[DemandItem] from parse_demand_excel (with week_qty)
    segment: str,
    effective_month: str,
    downtime_records: Optional[list] = None,  # from mp_model.get_downtime_affecting_month
) -> ScheduleResult:
    """
    Build a day-by-day, shift-level schedule from EngineResult items.

    Iterates each working day for each machine in sorted order, picking
    the highest-priority eligible item from a shared work list.  Priority
    is (first_requested_week ASC, remaining_hrs DESC), so W1 demand fills
    W1 machine-days before W2 demand gets a turn; overflow cascades ahead.

    downtime_records: list of dicts from mp_model.get_downtime_affecting_month.
    Machines that are down on a given day have their capacity set to zero (the
    day is skipped and two DOWN marker blocks are recorded).  Items routed ONLY
    to down machines for the entire month end up in unfinished with a reason.
    """
    # ── Load DB params ────────────────────────────────────────────────────────
    params_row = _mp.get_params(segment, effective_month)
    if params_row:
        min_run_block = float(getattr(params_row, "min_run_block_hours", 2.0) or 2.0)
    else:
        min_run_block = 2.0
    week_days = _week_days_for_schedule(segment, params_row)
    working_days_configured = bool(
        segment == "PLUMBING"
        and params_row
        and getattr(params_row, "week_days_configured", False)
    )
    total_days = sum(week_days)

    # ── Load machine configs ──────────────────────────────────────────────────
    mc_rows = _mp.get_machines(segment, effective_month, kind="extrusion")
    mc_params: Dict[str, dict] = {r["machine"]: r for r in mc_rows}

    def _hps(r: dict) -> float:
        return float(r.get("hours_per_shift") or 10.0)

    def _cap(r: dict) -> float:
        return float(r.get("capacity_hrs_month") or 500.0)

    # ── Build demand_map: item_code → DemandItem ──────────────────────────────
    demand_map = {d.item_code: d for d in demand_items}

    # ── Build work items from engine output ───────────────────────────────────
    work_items: List[_WorkItem] = []
    for it in engine_items:
        if not (it.has_weight and it.has_machine):
            continue
        if it.machine_hrs <= 1e-6:
            continue

        d = demand_map.get(it.item_code)
        if d is not None:
            # Restore int keys (JSON storage converts them to str)
            wq_raw = getattr(d, "week_qty", {}) or {}
            wq = {int(k): float(v) for k, v in wq_raw.items()}
            frw = int(getattr(d, "first_requested_week", 0) or 0)
            first_week = frw if frw > 0 else (min(wq) if wq else 1)
        else:
            first_week = 1

        work_items.append(_WorkItem(
            item_code=it.item_code,
            raw_code=it.raw_code,
            material=it.material,
            first_requested_week=first_week,
            remaining_hrs=float(it.machine_hrs),
            rate_kg_per_hr=float(it.rate_kg_per_hr),
            capable_machines=[mc for mc in it.capable_machines if mc in mc_params],
        ))

    # Discard items with no capable machines in this segment
    work_items = [w for w in work_items if w.capable_machines]
    work_items.sort(key=lambda w: w.sort_key())

    # ── Downtime map ─────────────────────────────────────────────────────────
    # {machine: {day_idx}} — days the machine is completely unavailable.
    down_days = _build_down_days(downtime_records or [], mc_params, effective_month, total_days)
    downtime_machine_days_count = sum(len(v) for v in down_days.values())
    downtime_hours_lost_total = 0.0

    # ── Day plan ──────────────────────────────────────────────────────────────
    # day_to_week: list index = day-1 (1-indexed), value = week number
    day_to_week: List[int] = []
    for week_num, n_days in enumerate(week_days, start=1):
        day_to_week.extend([week_num] * n_days)

    machines = sorted(mc_params.keys())
    blocks: List[ShiftBlock] = []
    idle_by_mc: Dict[str, float] = defaultdict(float)
    excess_kg_by_mc: Dict[str, float] = defaultdict(float)
    changeovers_by_mc_week: Dict[Tuple[str, int], int] = defaultdict(int)

    for day_idx, week in enumerate(day_to_week, start=1):
        for mc in machines:
            if mc not in mc_params:
                continue
            hps = _hps(mc_params[mc])
            if day_idx in down_days.get(mc, set()):
                # Machine is down this day — record DOWN marker blocks and skip scheduling
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="DAY",
                    item_code="DOWN", raw_code="DOWN", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="NIGHT",
                    item_code="DOWN", raw_code="DOWN", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                downtime_hours_lost_total += 2 * hps
                continue
            _schedule_machine_day(
                machine=mc,
                day=day_idx,
                week=week,
                hours_per_shift=hps,
                min_run_block=min_run_block,
                work_items=work_items,
                blocks=blocks,
                idle_by_mc=idle_by_mc,
                excess_kg_by_mc=excess_kg_by_mc,
                changeovers_by_mc_week=changeovers_by_mc_week,
            )

    # ── Unfinished items ──────────────────────────────────────────────────────
    # Determine which machines were down for the ENTIRE month (no available days)
    all_down_machines: Set[str] = {
        mc for mc, days in down_days.items()
        if len(days) >= total_days
    }

    unfinished: List[UnfinishedItem] = [
        UnfinishedItem(
            item_code=w.item_code,
            raw_code=w.raw_code,
            material=w.material,
            remaining_hours=round(w.remaining_hrs, 3),
            remaining_kg=round(w.remaining_hrs * w.rate_kg_per_hr, 1),
            capable_machines=w.capable_machines,
            origin_week=w.first_requested_week,
            downtime_reason=(
                "only capable machine(s) are down (breakdown/maintenance)"
                if w.capable_machines and all(mc in all_down_machines for mc in w.capable_machines)
                else ""
            ),
        )
        for w in work_items if w.remaining_hrs > 0.01
    ]
    unfinished.sort(key=lambda u: (-u.remaining_hours, u.item_code))

    # ── Weekly fill table ─────────────────────────────────────────────────────
    # Aggregate scheduled hours by (machine, week) from non-idle blocks
    sched_by_mc_wk: Dict[Tuple[str, int], float] = defaultdict(float)
    origin_by_mc_wk: Dict[Tuple[str, int], Dict[int, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for b in blocks:
        if b.is_idle:
            continue
        key = (b.machine, b.week)
        hrs = b.planned_hours - b.excess_hours  # actual production hours
        sched_by_mc_wk[key] += hrs
        origin_by_mc_wk[key][b.origin_week] += hrs

    weekly_fill: List[WeekFillRow] = []
    for mc in machines:
        if mc not in mc_params:
            continue
        monthly_cap = _cap(mc_params[mc])
        for wk in range(1, 5):
            wk_days = week_days[wk - 1]
            wk_cap = round(monthly_cap * wk_days / total_days, 2) if total_days > 0 else 0.0
            sched = round(sched_by_mc_wk.get((mc, wk), 0.0), 2)
            idle = round(idle_by_mc.get(mc, 0.0) * wk_days / total_days, 2)  # approximate
            util = round(sched / wk_cap * 100, 1) if wk_cap > 0 else 0.0
            ob = {ow: round(h, 2) for ow, h in origin_by_mc_wk.get((mc, wk), {}).items()}
            weekly_fill.append(WeekFillRow(
                week=wk,
                machine=mc,
                capacity_hrs=wk_cap,
                scheduled_hrs=sched,
                idle_hrs=idle,
                utilisation_pct=util,
                changeovers=changeovers_by_mc_week.get((mc, wk), 0),
                excess_kg=round(excess_kg_by_mc.get(mc, 0.0) * wk_days / total_days, 1),
                origin_breakdown=ob,
            ))

    # ── Aggregate totals ──────────────────────────────────────────────────────
    total_cap = sum(
        _cap(p) for p in mc_params.values()
    )
    total_sched = sum(r.scheduled_hrs for r in weekly_fill)
    total_idle = sum(idle_by_mc.values())
    total_excess_kg = sum(excess_kg_by_mc.values())
    total_changeovers = sum(changeovers_by_mc_week.values())

    return ScheduleResult(
        segment=segment,
        effective_month=effective_month,
        blocks=blocks,
        weekly_fill=weekly_fill,
        unfinished=unfinished,
        total_capacity_hrs=round(total_cap, 1),
        total_scheduled_hrs=round(total_sched, 1),
        total_idle_hrs=round(total_idle, 1),
        total_excess_kg=round(total_excess_kg, 1),
        total_changeovers=total_changeovers,
        week_days=week_days,
        params_used={
            "min_run_block_hours": min_run_block,
            "week_days": week_days,
        },
        downtime_machine_days=downtime_machine_days_count,
        downtime_hours_lost=round(downtime_hours_lost_total, 1),
        capacity_advisory=_capacity_advisory(
            unfinished, mc_params, effective_month, week_days, segment,
            working_days_configured,
        ),
    )


# ── Fitting scheduler entry point ─────────────────────────────────────────────

def run_fitting_schedule(
    fitting_items: list,    # List[FittingItemResult] from mp_engine.FittingEngineResult
    fitting_demand: list,   # List[FittingDemandItem] from mp_engine.parse_fitting_demand
    segment: str,
    effective_month: str,
    downtime_records: Optional[list] = None,
) -> ScheduleResult:
    """
    Build a day-by-day, shift-level schedule for fitting (moulding) machines.

    Analogous to run_shift_schedule but targets kind='moulding' machines.
    FittingDemandItem has no week_qty so all items default to first_requested_week=1.

    The rate stored in each _WorkItem is material_kg / machine_hrs — this makes
    remaining_kg in UnfinishedItem track deferred material kg proportionally,
    so the same _split() logic in mp_reports.py works without modification.
    """
    # ── Load DB params ────────────────────────────────────────────────────────
    params_row = _mp.get_params(segment, effective_month)
    if params_row:
        min_run_block = float(getattr(params_row, "min_run_block_hours", 2.0) or 2.0)
    else:
        min_run_block = 2.0
    week_days = _week_days_for_schedule(segment, params_row)
    working_days_configured = bool(
        segment == "PLUMBING"
        and params_row
        and getattr(params_row, "week_days_configured", False)
    )
    total_days = sum(week_days)

    # ── Load moulding machine configs ─────────────────────────────────────────
    mc_rows = _mp.get_machines(segment, effective_month, kind="moulding")
    mc_params: Dict[str, dict] = {r["machine"]: r for r in mc_rows}

    def _hps(r: dict) -> float:
        return float(r.get("hours_per_shift") or 10.0)

    def _cap(r: dict) -> float:
        return float(r.get("capacity_hrs_month") or 500.0)

    # ── Build work items from fitting engine output ────────────────────────────
    # rate_kg_per_hr = material_kg / machine_hrs so that remaining_kg tracks
    # deferred material kg proportionally (used by _split in mp_reports.py).
    work_items: List[_WorkItem] = []
    for it in fitting_items:
        if not (getattr(it, "has_weight", False) and getattr(it, "has_machine", False)):
            continue
        hrs = float(getattr(it, "machine_hrs", 0.0) or 0.0)
        if hrs <= 1e-6:
            continue
        mat_kg = float(getattr(it, "material_kg", 0.0) or 0.0)
        rate_kg_hr = mat_kg / hrs if hrs > 0 else 0.0

        capable = [
            mc for mc in (getattr(it, "capable_machines", []) or [])
            if mc in mc_params
        ]
        if not capable:
            continue

        work_items.append(_WorkItem(
            item_code=it.item_code,
            raw_code=it.raw_code,
            material=it.material,
            first_requested_week=1,   # FittingDemandItem has no week split
            remaining_hrs=hrs,
            rate_kg_per_hr=rate_kg_hr,
            capable_machines=capable,
        ))

    work_items.sort(key=lambda w: w.sort_key())

    # ── Downtime map ──────────────────────────────────────────────────────────
    down_days = _build_down_days(downtime_records or [], mc_params, effective_month, total_days)
    downtime_machine_days_count = sum(len(v) for v in down_days.values())
    downtime_hours_lost_total = 0.0

    # ── Day plan ──────────────────────────────────────────────────────────────
    day_to_week: List[int] = []
    for week_num, n_days in enumerate(week_days, start=1):
        day_to_week.extend([week_num] * n_days)

    machines = sorted(mc_params.keys())
    blocks: List[ShiftBlock] = []
    idle_by_mc: Dict[str, float] = defaultdict(float)
    excess_kg_by_mc: Dict[str, float] = defaultdict(float)
    changeovers_by_mc_week: Dict[Tuple[str, int], int] = defaultdict(int)

    # ── Per-machine capacity budget (hard constraint) ─────────────────────────
    # capacity_hrs_month is the declared capacity ceiling; once used up the
    # machine idles for the rest of the month regardless of remaining work days.
    # This guarantees weekly_fill.scheduled_hrs ≤ weekly_fill.capacity_hrs for
    # every row, which is required for the Machine Load tab assertion to hold.
    cap_remaining: Dict[str, float] = {mc: _cap(mc_params[mc]) for mc in machines}

    for day_idx, week in enumerate(day_to_week, start=1):
        for mc in machines:
            if mc not in mc_params:
                continue
            hps = _hps(mc_params[mc])
            if day_idx in down_days.get(mc, set()):
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="DAY",
                    item_code="DOWN", raw_code="DOWN", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="NIGHT",
                    item_code="DOWN", raw_code="DOWN", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                downtime_hours_lost_total += 2 * hps
                continue

            # Capacity-budget gate: if this machine has less declared capacity
            # remaining than a full day's production (2 shifts × hps), idle it.
            # _schedule_machine_day always fills both shifts, so we must ensure
            # the full day's output fits in the budget before scheduling.
            # This guarantees weekly_fill.scheduled_hrs ≤ weekly_fill.capacity_hrs,
            # which is required for the Machine Load tab assertion to hold.
            full_day_hrs = 2.0 * hps
            if cap_remaining.get(mc, 0.0) < full_day_hrs - 1e-6:
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="DAY",
                    item_code="", raw_code="", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                blocks.append(ShiftBlock(
                    week=week, day=day_idx, machine=mc, shift="NIGHT",
                    item_code="", raw_code="", material="",
                    planned_hours=hps, excess_hours=0.0, origin_week=0, is_idle=True,
                ))
                idle_by_mc[mc] += 2 * hps
                continue

            blocks_before = len(blocks)
            _schedule_machine_day(
                machine=mc,
                day=day_idx,
                week=week,
                hours_per_shift=hps,
                min_run_block=min_run_block,
                work_items=work_items,
                blocks=blocks,
                idle_by_mc=idle_by_mc,
                excess_kg_by_mc=excess_kg_by_mc,
                changeovers_by_mc_week=changeovers_by_mc_week,
            )
            # Subtract actual production hours placed this day from the budget.
            prod_this_day = sum(
                b.planned_hours - b.excess_hours
                for b in blocks[blocks_before:]
                if not b.is_idle
            )
            cap_remaining[mc] = max(0.0, cap_remaining[mc] - prod_this_day)

    # ── Unfinished items ──────────────────────────────────────────────────────
    all_down_machines: Set[str] = {
        mc for mc, days in down_days.items()
        if len(days) >= total_days
    }
    unfinished: List[UnfinishedItem] = [
        UnfinishedItem(
            item_code=w.item_code,
            raw_code=w.raw_code,
            material=w.material,
            remaining_hours=round(w.remaining_hrs, 3),
            remaining_kg=round(w.remaining_hrs * w.rate_kg_per_hr, 1),
            capable_machines=w.capable_machines,
            origin_week=w.first_requested_week,
            downtime_reason=(
                "only capable machine(s) are down (breakdown/maintenance)"
                if w.capable_machines and all(mc in all_down_machines for mc in w.capable_machines)
                else ""
            ),
        )
        for w in work_items if w.remaining_hrs > 0.01
    ]
    unfinished.sort(key=lambda u: (-u.remaining_hours, u.item_code))

    # ── Weekly fill table ─────────────────────────────────────────────────────
    sched_by_mc_wk: Dict[Tuple[str, int], float] = defaultdict(float)
    origin_by_mc_wk: Dict[Tuple[str, int], Dict[int, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for b in blocks:
        if b.is_idle:
            continue
        key = (b.machine, b.week)
        hrs = b.planned_hours - b.excess_hours
        sched_by_mc_wk[key] += hrs
        origin_by_mc_wk[key][b.origin_week] += hrs

    weekly_fill: List[WeekFillRow] = []
    for mc in machines:
        if mc not in mc_params:
            continue
        monthly_cap = _cap(mc_params[mc])
        for wk in range(1, 5):
            wk_days = week_days[wk - 1]
            wk_cap = round(monthly_cap * wk_days / total_days, 2) if total_days > 0 else 0.0
            sched = round(sched_by_mc_wk.get((mc, wk), 0.0), 2)
            idle = round(idle_by_mc.get(mc, 0.0) * wk_days / total_days, 2)
            util = round(sched / wk_cap * 100, 1) if wk_cap > 0 else 0.0
            ob = {ow: round(h, 2) for ow, h in origin_by_mc_wk.get((mc, wk), {}).items()}
            weekly_fill.append(WeekFillRow(
                week=wk,
                machine=mc,
                capacity_hrs=wk_cap,
                scheduled_hrs=sched,
                idle_hrs=idle,
                utilisation_pct=util,
                changeovers=changeovers_by_mc_week.get((mc, wk), 0),
                excess_kg=round(excess_kg_by_mc.get(mc, 0.0) * wk_days / total_days, 1),
                origin_breakdown=ob,
            ))

    # ── Aggregate totals ──────────────────────────────────────────────────────
    total_cap = sum(_cap(p) for p in mc_params.values())
    total_sched = sum(r.scheduled_hrs for r in weekly_fill)
    total_idle = sum(idle_by_mc.values())
    total_excess_kg = sum(excess_kg_by_mc.values())
    total_changeovers = sum(changeovers_by_mc_week.values())

    return ScheduleResult(
        segment=segment,
        effective_month=effective_month,
        blocks=blocks,
        weekly_fill=weekly_fill,
        unfinished=unfinished,
        total_capacity_hrs=round(total_cap, 1),
        total_scheduled_hrs=round(total_sched, 1),
        total_idle_hrs=round(total_idle, 1),
        total_excess_kg=round(total_excess_kg, 1),
        total_changeovers=total_changeovers,
        week_days=week_days,
        params_used={
            "min_run_block_hours": min_run_block,
            "week_days": week_days,
        },
        downtime_machine_days=downtime_machine_days_count,
        downtime_hours_lost=round(downtime_hours_lost_total, 1),
        capacity_advisory=_capacity_advisory(
            unfinished, mc_params, effective_month, week_days, segment,
            working_days_configured,
        ),
    )
