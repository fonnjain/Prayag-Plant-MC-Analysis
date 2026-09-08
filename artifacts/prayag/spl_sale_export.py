"""Reproducible Spl Sale stock-to-machine planning export.

The stock workbook keeps its original item-code spelling for display, while every
cross-source match uses mp_seed.norm_code.  This module is export-only: it reads
planning inputs and never persists a plan run or edits mp_routing.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import mp_engine
import mp_model
import mp_scheduler
from mp_seed import norm_code


OUT_OF_SCOPE_CATEGORIES = {
    "WATER TANK", "CPVC-TRADING", "AGRI-TRADING", "TRADING",
}


@dataclasses.dataclass(frozen=True)
class StockRow:
    source_row: int
    raw_code: str
    item_name: str
    category: str
    packing: float
    qty_pcs: float
    file_weight_per_pc_kg: float
    file_total_weight_kg: float

    @property
    def match_code(self) -> str:
        return norm_code(self.raw_code)


@dataclasses.dataclass(frozen=True)
class ClassifiedRow:
    stock: StockRow
    status: str
    reason: str
    routing_code: str = ""
    routes: tuple[dict, ...] = ()


@dataclasses.dataclass
class SplSalePlan:
    source_rows: list[StockRow]
    scheduled: list[ClassifiedRow]
    unscheduled: list[ClassifiedRow]
    pipe_demand: list[mp_engine.DemandItem]
    fitting_demand: list[mp_engine.FittingDemandItem]
    pipe_engine: mp_engine.EngineResult
    fitting_engine: mp_engine.FittingEngineResult
    pipe_schedule: mp_scheduler.ScheduleResult
    fitting_schedule: mp_scheduler.ScheduleResult


class StockCodeAmbiguityError(ValueError):
    """Raised when multiple in-scope stock rows share one comparison key."""


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def read_stock(path: str | Path) -> list[StockRow]:
    ws = load_workbook(path, read_only=True, data_only=True)["Pending Prod."]
    rows: list[StockRow] = []
    for source_row, values in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
        raw_code = str(values[0] or "").strip()
        if not raw_code:
            continue
        rows.append(StockRow(
            source_row=source_row,
            raw_code=raw_code,
            item_name=str(values[1] or "").strip(),
            category=str(values[2] or "").strip(),
            packing=_number(values[3]),
            qty_pcs=_number(values[4]),
            file_weight_per_pc_kg=_number(values[5]) / 1000.0,
            file_total_weight_kg=_number(values[6]),
        ))
    return rows


def classify_stock(
    stock_rows: Iterable[StockRow],
    routing_rows: Iterable[dict],
) -> tuple[list[ClassifiedRow], list[ClassifiedRow]]:
    """Classify stock using normalised comparison keys; never rewrite raw codes."""
    route_index: dict[str, list[dict]] = defaultdict(list)
    route_spellings: dict[str, set[str]] = defaultdict(set)
    for route in routing_rows:
        if not route.get("capable", True):
            continue
        key = norm_code(route.get("item_code", ""))
        if not key:
            continue
        route_index[key].append(route)
        route_spellings[key].add(str(route.get("item_code", "")).strip())

    in_scope_keys: dict[str, list[StockRow]] = defaultdict(list)
    stock_rows = list(stock_rows)
    for stock in stock_rows:
        if (
            stock.category
            and stock.category.upper() not in OUT_OF_SCOPE_CATEGORIES
            and stock.qty_pcs > 0
        ):
            in_scope_keys[stock.match_code].append(stock)
    duplicate_keys = {
        key: rows for key, rows in in_scope_keys.items() if len(rows) > 1
    }
    if duplicate_keys:
        details = "; ".join(
            f"{key}: " + ", ".join(
                f"row {row.source_row} {row.raw_code!r}" for row in rows
            )
            for key, rows in sorted(duplicate_keys.items())
        )
        raise StockCodeAmbiguityError(
            f"Multiple stock rows share a normalised item code: {details}"
        )

    scheduled: list[ClassifiedRow] = []
    unscheduled: list[ClassifiedRow] = []
    for stock in stock_rows:
        category = stock.category.upper()
        if not stock.category:
            unscheduled.append(ClassifiedRow(
                stock, "unscheduled",
                "Blank-category subtotal/non-item row; column E is zero",
            ))
            continue
        if category in OUT_OF_SCOPE_CATEGORIES:
            unscheduled.append(ClassifiedRow(
                stock, "unscheduled", "Outside Plumbing planning scope",
            ))
            continue
        if stock.qty_pcs <= 0:
            unscheduled.append(ClassifiedRow(
                stock, "unscheduled", "Column E quantity is zero",
            ))
            continue

        matches = route_index.get(stock.match_code, [])
        spellings = sorted(route_spellings.get(stock.match_code, set()))
        if not matches:
            unscheduled.append(ClassifiedRow(
                stock, "unscheduled", "No capable Plumbing machine route",
            ))
        elif len(spellings) > 1:
            unscheduled.append(ClassifiedRow(
                stock, "unscheduled",
                "Ambiguous normalised routing match: " + ", ".join(spellings),
            ))
        else:
            scheduled.append(ClassifiedRow(
                stock, "scheduled", "", spellings[0], tuple(matches),
            ))
    return scheduled, unscheduled


def _material(row: ClassifiedRow) -> str:
    values = {
        str(route.get("material") or "").strip().upper()
        for route in row.routes if route.get("material")
    }
    return sorted(values)[0] if values else row.stock.category.split("-")[0].upper()


def build_plan(
    source_path: str | Path,
    *,
    segment: str = "PLUMBING",
    effective_month: str = "2026-08",
) -> SplSalePlan:
    source_rows = read_stock(source_path)
    routing = mp_model.get_routing(segment, effective_month)
    scheduled, unscheduled = classify_stock(source_rows, routing)

    pipe_demand: list[mp_engine.DemandItem] = []
    fitting_demand: list[mp_engine.FittingDemandItem] = []
    for row in scheduled:
        stock = row.stock
        item_code = norm_code(stock.raw_code)
        material = _material(row)
        if "PIPE" in stock.category.upper():
            pipe_demand.append(mp_engine.DemandItem(
                item_code=item_code,
                raw_code=stock.raw_code,
                material=material,
                qty_pcs=stock.qty_pcs,
                week_qty={1: stock.qty_pcs},
                first_requested_week=1,
            ))
        else:
            fitting_demand.append(mp_engine.FittingDemandItem(
                item_code=item_code,
                raw_code=stock.raw_code,
                material=material,
                qty_pcs=stock.qty_pcs,
            ))

    pipe_engine = mp_engine.run_engine(pipe_demand, effective_month, segment)
    fitting_engine = mp_engine.run_fitting_engine(
        fitting_demand, effective_month, segment,
    )
    pipe_schedule = mp_scheduler.run_shift_schedule(
        pipe_engine.items, pipe_demand,
        segment=segment, effective_month=effective_month,
    )
    fitting_schedule = mp_scheduler.run_fitting_schedule(
        fitting_engine.items, fitting_demand,
        segment=segment, effective_month=effective_month,
    )
    return SplSalePlan(
        source_rows, scheduled, unscheduled, pipe_demand, fitting_demand,
        pipe_engine, fitting_engine, pipe_schedule, fitting_schedule,
    )


def _plan_dates(total_days: int, effective_month: str) -> dict[int, dt.date]:
    year, month = (int(part) for part in effective_month.split("-"))
    current = dt.date(year, month, 1)
    dates: list[dt.date] = []
    while len(dates) < total_days:
        if current.weekday() != 6:
            dates.append(current)
        current += dt.timedelta(days=1)
    return {index + 1: value for index, value in enumerate(dates)}


def _add_sheet(
    workbook: Workbook,
    title: str,
    headers: list[str],
    rows: Iterable[Iterable[Any]],
) -> Any:
    ws = workbook.create_sheet(title)
    ws.append(headers)
    for row in rows:
        ws.append(list(row))
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_view.showGridLines = False
    for index, header in enumerate(headers, 1):
        width = max(12, min(45, len(header) + 4))
        ws.column_dimensions[get_column_letter(index)].width = width
    return ws


def export_plan(plan: SplSalePlan, output_path: str | Path) -> Path:
    """Write a traceable workbook from an in-memory, non-persisted plan."""
    item_results: dict[str, tuple[str, Any]] = {}
    for kind, result in [
        ("Pipe", plan.pipe_engine), ("Fitting", plan.fitting_engine),
    ]:
        for item in result.items:
            item_results[norm_code(item.item_code)] = (kind, item)

    unfinished = (
        list(plan.pipe_schedule.unfinished)
        + list(plan.fitting_schedule.unfinished)
    )
    unfinished_by_code = {
        norm_code(item.item_code): item for item in unfinished
    }
    target_hours: dict[str, float] = {}
    for code, (_, item) in item_results.items():
        remaining = float(
            getattr(unfinished_by_code.get(code), "remaining_hours", 0.0) or 0.0
        )
        target_hours[code] = max(0.0, float(item.machine_hrs) - remaining)
    hours_left = dict(target_hours)

    total_days = max(
        sum(plan.pipe_schedule.week_days),
        sum(plan.fitting_schedule.week_days),
    )
    day_dates = _plan_dates(total_days, plan.pipe_engine.effective_month)
    block_rows: list[list[Any]] = []
    completion_day = 0
    for kind, schedule in [
        ("Pipe", plan.pipe_schedule), ("Fitting", plan.fitting_schedule),
    ]:
        for block in schedule.blocks:
            if block.is_idle or not block.item_code:
                continue
            code = norm_code(block.item_code)
            available = max(0.0, block.planned_hours - block.excess_hours)
            productive = min(available, max(0.0, hours_left.get(code, 0.0)))
            if productive <= 0:
                continue
            hours_left[code] -= productive
            _, item = item_results[code]
            pieces = item.qty_pcs * productive / item.machine_hrs
            material_kg = item.material_kg * productive / item.machine_hrs
            completion_day = max(completion_day, block.day)
            block_rows.append([
                block.day, day_dates[block.day], block.week, kind,
                block.machine, block.shift, item.raw_code, item.item_code,
                item.material, block.planned_hours, productive,
                block.planned_hours - productive, pieces, material_kg,
                block.excess_hours,
            ])

    workbook = Workbook()
    workbook.remove(workbook.active)
    unfinished_codes = set(unfinished_by_code)
    missing_input_codes = {
        norm_code(code)
        for code in (
            list(plan.pipe_engine.coverage_gaps.no_weight)
            + list(plan.pipe_engine.coverage_gaps.no_machine)
            + list(plan.fitting_engine.coverage_gaps.no_weight)
            + list(plan.fitting_engine.coverage_gaps.no_machine)
        )
    }
    blocked_codes = missing_input_codes | unfinished_codes
    fully_scheduled = [
        row for row in plan.scheduled if row.stock.match_code not in blocked_codes
    ]
    # Validate before export.  Padded block time is reported as excess; missing
    # block time is never filled or adjusted to force a match.
    uncovered = {
        code: remaining for code, remaining in hours_left.items()
        if remaining > 1e-7 and code not in missing_input_codes
    }
    if uncovered:
        details = ", ".join(
            f"{code}: {hours:.4f} h" for code, hours in sorted(uncovered.items())
        )
        raise ValueError(f"Schedule blocks do not cover required hours: {details}")
    rows_by_code: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(block_rows):
        rows_by_code[norm_code(row[7])].append(index)
    for row in fully_scheduled:
        code = row.stock.match_code
        indices = rows_by_code.get(code, [])
        if not indices:
            raise ValueError(f"Scheduled item {row.stock.raw_code} has no export blocks")
        _, item = item_results[code]
        if abs(sum(block_rows[i][12] for i in indices) - float(item.qty_pcs)) > 1e-7:
            raise ValueError(f"Piece reconciliation failed for {row.stock.raw_code}")
        if abs(sum(block_rows[i][13] for i in indices) - float(item.material_kg)) > 1e-7:
            raise ValueError(f"Material reconciliation failed for {row.stock.raw_code}")
    planned_pieces_by_code: dict[str, float] = defaultdict(float)
    for row in block_rows:
        planned_pieces_by_code[norm_code(row[7])] += float(row[12])
    partially_scheduled = [
        row for row in plan.scheduled
        if row.stock.match_code in unfinished_codes
        and planned_pieces_by_code.get(row.stock.match_code, 0.0) > 0
    ]
    physically_scheduled_pieces = sum(planned_pieces_by_code.values())
    summary_rows = [
        ("Routed items", len(plan.scheduled)),
        ("Fully scheduled items", len(fully_scheduled)),
        ("Partially scheduled items", len(partially_scheduled)),
        ("Routed pieces", sum(row.stock.qty_pcs for row in plan.scheduled)),
        ("Physically scheduled pieces", sum(
            [physically_scheduled_pieces]
        )),
        ("Routed items blocked by missing planning input", len(blocked_codes)),
        ("Recovered spelling-mismatch items", sum(
            row.stock.raw_code != row.routing_code for row in plan.scheduled
        )),
        ("Unscheduled rows", len(plan.unscheduled)),
        ("Completion planning day", completion_day),
        ("Completion date", day_dates.get(completion_day, "")),
        ("Persistence", "Export only; no plan or routing rows written"),
    ]
    _add_sheet(workbook, "Summary", ["Metric", "Value"], summary_rows)
    _add_sheet(workbook, "Daily Machine Plan", [
        "Plan Day", "Date", "Week", "Plan Type", "Machine", "Shift",
        "Source Item Code", "Match Key", "Material", "Block Hours",
        "Productive Hours", "Excess Hours", "Planned Pieces",
        "Planner Material kg", "Scheduler-reported Excess Hours",
    ], block_rows)
    _add_sheet(workbook, "Item Summary", [
        "Plan Type", "Source Item Code", "Match Key", "Routing Code",
        "Item Name", "Category", "Column E Pieces", "File Total Weight kg",
        "Capable Machines", "Status",
    ], (
        [
            item_results[row.stock.match_code][0], row.stock.raw_code,
            row.stock.match_code, row.routing_code, row.stock.item_name,
            row.stock.category, row.stock.qty_pcs,
            row.stock.file_total_weight_kg,
            " | ".join(sorted({
                str(route.get("machine") or "") for route in row.routes
            })),
            (
                (
                    "Partially scheduled — "
                    f"{planned_pieces_by_code.get(row.stock.match_code, 0.0):,.3f} "
                    f"of {row.stock.qty_pcs:,.3f} pcs; "
                    f"{float(unfinished_by_code[row.stock.match_code].remaining_pcs):,.3f} "
                    "pcs remain"
                )
                if row.stock.match_code in unfinished_codes
                else "Routed; not scheduled — missing BOM weight or machine input"
                if row.stock.match_code in missing_input_codes
                else "Scheduled in full"
            ),
        ]
        for row in plan.scheduled
    ))
    _add_sheet(workbook, "Unscheduled", [
        "Source Row", "Item Code", "Item Name", "Category",
        "Column E Pieces", "File Total Weight kg", "Reason",
    ], (
        [
            row.stock.source_row, row.stock.raw_code, row.stock.item_name,
            row.stock.category, row.stock.qty_pcs,
            row.stock.file_total_weight_kg, row.reason,
        ]
        for row in plan.unscheduled
    ))
    _add_sheet(workbook, "Source Stock", [
        "Source Row", "Item Code", "Match Key", "Item Name", "Category",
        "Column E Pieces", "File Weight/Pc kg", "File Total Weight kg",
    ], (
        [
            row.source_row, row.raw_code, row.match_code, row.item_name,
            row.category, row.qty_pcs, row.file_weight_per_pc_kg,
            row.file_total_weight_kg,
        ]
        for row in plan.source_rows
    ))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--month", default="2026-08")
    parser.add_argument("--segment", default="PLUMBING")
    args = parser.parse_args()
    plan = build_plan(
        args.source, segment=args.segment, effective_month=args.month,
    )
    export_plan(plan, args.output)


if __name__ == "__main__":
    main()