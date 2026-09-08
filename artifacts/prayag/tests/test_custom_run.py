import io
import inspect
import types
from unittest.mock import patch

from openpyxl import Workbook

from spl_sale_export import StockRow, build_plan_from_rows, read_custom_demand


def _workbook(headers, rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(headers)
    for row in rows:
        worksheet.append(row)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_custom_demand_accepts_attached_stock_layout():
    rows, warnings = read_custom_demand(
        _workbook(
            ["Item Code", "Item Name", "Category", " Stock"],
            [["CM21", "Wall mixture", "CPVC-FG", 284]],
        ),
        "CPVC-FG",
    )
    assert warnings == []
    assert len(rows) == 1
    assert rows[0].raw_code == "CM21"
    assert rows[0].qty_pcs == 284
    assert rows[0].category == "CPVC-FG"


def test_custom_demand_accepts_minimal_code_and_required_quantity():
    rows, warnings = read_custom_demand(
        _workbook(
            ["Code", "Required Quantity"],
            [["PS-2", 250], ["PS-2S", 125]],
        ),
        "UPVC-FG",
    )
    assert warnings == []
    assert [row.raw_code for row in rows] == ["PS-2", "PS-2S"]
    assert [row.qty_pcs for row in rows] == [250, 125]


def test_custom_demand_skips_rows_from_another_category():
    rows, warnings = read_custom_demand(
        _workbook(
            ["Item Code", "Category", "Qty"],
            [["C1", "CPVC-FG", 10], ["U1", "UPVC-FG", 20]],
        ),
        "CPVC-FG",
    )
    assert [row.raw_code for row in rows] == ["C1"]
    assert "U1" in warnings[0]


def test_custom_plan_passes_current_assumptions_to_engines_and_schedulers():
    rows = [
        StockRow(2, "P1", "Pipe", "CPVC-PIPE", 0, 100, 0, 0),
        StockRow(3, "F1", "Fitting", "CPVC-FG", 0, 200, 0, 0),
    ]
    routes = [
        {"item_code": "P1", "machine": "P-MC", "material": "CPVC", "capable": True},
        {"item_code": "F1", "machine": "F-MC", "material": "CPVC", "capable": True},
    ]
    pipe_result = types.SimpleNamespace(items=[])
    fitting_result = types.SimpleNamespace(items=[])
    pipe_schedule = types.SimpleNamespace()
    fitting_schedule = types.SimpleNamespace()
    rej_lookup = {"has_data": True, "exact": {"P1": 2.5}}
    wastage_lookup = {"has_data": True, "material": {"CPVC": 1.5}}
    downtime = [types.SimpleNamespace(machine="P-MC")]

    with patch("spl_sale_export.mp_model.get_routing", return_value=routes), \
         patch("spl_sale_export.mp_engine.run_engine", return_value=pipe_result) as pipe_engine, \
         patch("spl_sale_export.mp_engine.run_fitting_engine", return_value=fitting_result) as fit_engine, \
         patch("spl_sale_export.mp_scheduler.run_shift_schedule", return_value=pipe_schedule) as pipe_sched, \
         patch("spl_sale_export.mp_scheduler.run_fitting_schedule", return_value=fitting_schedule) as fit_sched:
        plan = build_plan_from_rows(
            rows,
            effective_month="2026-08",
            rej_lookup=rej_lookup,
            wastage_lookup=wastage_lookup,
            downtime_records=downtime,
        )

    assert plan.pipe_engine is pipe_result
    assert plan.fitting_engine is fitting_result
    assert pipe_engine.call_args.kwargs["rej_lookup"] is rej_lookup
    assert pipe_engine.call_args.kwargs["wastage_lookup"] is wastage_lookup
    assert fit_engine.call_args.kwargs["rej_lookup"] is rej_lookup
    assert fit_engine.call_args.kwargs["wastage_lookup"] is wastage_lookup
    assert pipe_sched.call_args.kwargs["downtime_records"] is downtime
    assert fit_sched.call_args.kwargs["downtime_records"] is downtime


def test_custom_route_source_has_no_plan_persistence_or_normal_session_sink():
    import app

    source = inspect.getsource(app.mp_custom_run)
    forbidden = (
        "_mp2_store_run", "insert_plan_run", "update_plan_run_file_path",
        "_MP2_RUN_CACHE", "session[", "_UPLOADS_DIR", "open(",
        "init_mp_tables",
    )
    for token in forbidden:
        assert token not in source