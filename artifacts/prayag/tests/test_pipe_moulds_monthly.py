"""Month-grain regression coverage for the Management Report 13 download."""
from __future__ import annotations

import mgmt_pipe_moulds_summary as summary
import parsers
from reports.model import ReportModel
from reports.serialisers import serial_pipe_moulds
from reports.xlsx import render_workbook


def _cumulative_values():
    return [
        ["S.NO", "MOULD", "", "", "", "", "", "", "", ""],
        ["", "", "PRODUCTION IN PCS", "PRODUCTION IN KG", "GROSS KG",
         "MOULD UTILISATION IN HOURS", "PRODUCTION IN PCS",
         "PRODUCTION IN KG", "GROSS KG", "MOULD UTILISATION IN HOURS"],
        ["TOTAL", "", 100, 10, 0, 5, 250, 25, 0, 12],
        [1, "M-1", 100, 10, 0, 5, 200, 20, 0, 10],
        [2, "M-2", 0, 0, 0, 0, 50, 5, 0, 2],
    ]


def _annual_values():
    return [
        ["S.NO", "MOULD", "PRODUCTION IN PCS", "PRODUCTION IN KG", "GROSS KG",
         "MOULD UTILISATION IN HOURS", "PRODUCTION IN PCS",
         "PRODUCTION IN KG", "GROSS KG", "MOULD UTILISATION IN HOURS",
         "PRODUCTION IN PCS", "PRODUCTION IN KG", "GROSS KG",
         "MOULD UTILISATION IN HOURS", "PRODUCTION IN PCS",
         "PRODUCTION IN KG", "GROSS KG", "MOULD UTILISATION IN HOURS"],
        ["TOTAL", "", 10, 1, 0, 1, 20, 2, 0, 2, 30, 3, 0, 3, 40, 4, 0, 4],
        [1, "A-1", 10, 1, 0, 1, 20, 2, 0, 2, 30, 3, 0, 3, 40, 4, 0, 4],
    ]


def test_cumulative_parser_preserves_each_month_before_aggregating():
    result = parsers.parse_cumulative_mould_fy(
        _cumulative_values(), group="CPVC", n_months=2
    )

    assert result is not None
    assert [(r["total_pcs"], r["total_kg"], r["n_run"])
            for r in result["months"]] == [(100, 10, 1), (250, 25, 2)]
    assert result["total_pcs"] == 350
    assert result["total_kg"] == 35
    assert result["sheet_total_pcs"] == 350


def test_annual_parser_preserves_apr_to_jul_month_rows():
    result = parsers.parse_annual_mould_summary_apr_jul(
        _annual_values(), group="CPVC"
    )

    assert result is not None
    assert [r["total_kg"] for r in result["months"]] == [1, 2, 3, 4]
    assert result["total_kg"] == 10


def test_builder_and_material_download_include_months_and_fy_totals(monkeypatch):
    current = parsers.parse_cumulative_mould_fy(
        _cumulative_values(), group="CPVC", n_months=2
    )
    prior = parsers.parse_annual_mould_summary_apr_jul(
        _annual_values(), group="CPVC"
    )

    class FakeSheets:
        @staticmethod
        def load_pipe_moulds_fy(_fy):
            return {
                "available": True, "n_months": 2, "latest_ym": "2026-05",
                "groups": [current], "missing": [],
            }

        @staticmethod
        def load_pipe_moulds_annual_2526():
            return {"available": True, "groups": [prior], "missing": []}

    monkeypatch.setattr(summary, "_sh", lambda: FakeSheets)

    built = summary.build_pipe_moulds_summary()
    current_block, prior_block = built["blocks"]
    assert [r["month"] for r in current_block["month_rows"]] == ["Apr,26", "May,26"]
    assert [r["month"] for r in prior_block["month_rows"]] == [
        "Apr,25", "May,25", "Jun,25", "Jul,25"
    ]
    assert current_block["rows"][0]["kg"] == 35
    assert "formula hours differ" in built["hours_note"]

    sheets, _flags = serial_pipe_moulds("2026-07")
    cpvc = next(sheet for sheet in sheets if sheet.name.startswith("CPVC"))
    assert [row["month"] for row in cpvc.sections[0].rows] == ["Apr,26", "May,26"]
    assert cpvc.sections[0].total_row["month"].endswith("total")

    workbook = render_workbook(ReportModel(
        rid="pipe_moulds", label="Pipe Moulds", plant="KH", ym="2026-07",
        month_disp="Jul 2026", sheets=sheets, flags=[],
    ))
    worksheet = next(ws for ws in workbook.worksheets if ws.title.startswith("CPVC"))
    worksheet_rows = [
        tuple(cell.value for cell in row[:3])
        for row in worksheet.iter_rows()
        if row[0].value
    ]
    assert ("Apr,26", "CPVC", None) in worksheet_rows
    assert any(str(row[0]).endswith("total") for row in worksheet_rows)