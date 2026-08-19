"""Month-grain regression coverage for the Management Report 13 download."""
from __future__ import annotations

import mgmt_pipe_moulds_summary as summary
import parsers
import sheets as live_sheets
import source_registry
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


def _cumulative_values_with_months(n_months: int):
    """Recognisable cumulative layout with one completed block per month."""
    header = ["S.NO", "MOULD"]
    total = ["TOTAL", ""]
    mould_a = [1, "M-1"]
    mould_b = [2, "M-2"]
    for month in range(1, n_months + 1):
        header.extend([
            "PRODUCTION IN PCS", "PRODUCTION IN KG", "GROSS KG",
            "MOULD UTILISATION IN HOURS",
        ])
        total.extend([month * 100, month * 10, 0, month])
        mould_a.extend([month * 60, month * 6, 0, month])
        mould_b.extend([month * 40, month * 4, 0, 0])
    return [
        ["S.NO", "MOULD"],
        header,
        total,
        mould_a,
        mould_b,
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


def test_cumulative_parser_marks_unfinished_trailing_block_without_counting_it():
    values = _cumulative_values_with_months(2)
    # A new month begins, but only its PCS heading has been entered.  Its
    # figure must not be published as a completed third fiscal month.
    values[1].append("PRODUCTION IN PCS")
    for row in values[2:]:
        row.append(999)

    result = parsers.parse_cumulative_mould_fy(values, group="CPVC", n_months=3)

    assert result is not None
    assert result["complete_month_indexes"] == [0, 1]
    assert result["partial_month_indexes"] == [2]
    assert result["missing_month_indexes"] == []
    assert result["complete_n_months"] == 2
    assert result["total_kg"] == 30


def test_loader_uses_latest_registered_workbook_and_reports_missing_blocks(monkeypatch):
    fiscal_months = [
        "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
    ]
    raw = {
        tab: _cumulative_values_with_months(2)
        for tab in live_sheets._PIPE_MOULD_FY_TABS
    }
    live_sheets._pipe_mould_fy_cache.clear()
    monkeypatch.setattr(live_sheets.sources, "FY_MONTHS", fiscal_months)
    monkeypatch.setattr(
        live_sheets, "_pipe_mould_file_id",
        lambda ym: "aug-workbook" if ym == "2026-08" else None,
    )
    monkeypatch.setattr(live_sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(live_sheets, "batch_get", lambda _fid, _tabs, _token: raw)

    data = live_sheets.load_pipe_moulds_fy("2627")

    assert data["latest_ym"] == "2026-08"
    assert data["months"] == fiscal_months
    assert data["n_months"] == 5
    assert data["complete_n_months"] == 2
    assert [entry["month"] for entry in data["missing_months"]] == [
        "2026-06", "2026-07", "2026-08",
    ]
    assert all(len(entry["materials"]) == 5 for entry in data["missing_months"])
    live_sheets._pipe_mould_fy_cache.clear()


def test_loader_does_not_use_latest_file_fallback_for_future_fiscal_months(monkeypatch):
    fiscal_months = [
        "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
        "2026-09", "2026-10", "2026-11", "2026-12",
    ]
    raw = {
        tab: _cumulative_values_with_months(5)
        for tab in live_sheets._PIPE_MOULD_FY_TABS
    }
    live_sheets._pipe_mould_fy_cache.clear()
    monkeypatch.setattr(live_sheets.sources, "FY_MONTHS", fiscal_months)
    monkeypatch.setitem(
        live_sheets.sources.DAILY_SOURCES["PIPE"], "files",
        {"2026-08": "aug-workbook"},
    )
    monkeypatch.setattr(source_registry, "get_pipe_file_id", lambda _ym: None)
    monkeypatch.setattr(live_sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(live_sheets, "batch_get", lambda _fid, _tabs, _token: raw)

    data = live_sheets.load_pipe_moulds_fy("2627")

    assert data["latest_ym"] == "2026-08"
    assert data["months"] == fiscal_months[:5]
    assert data["n_months"] == 5
    live_sheets._pipe_mould_fy_cache.clear()


def test_loader_excludes_month_completed_by_only_some_materials(monkeypatch):
    fiscal_months = ["2026-04", "2026-05", "2026-06"]
    raw = {
        tab: _cumulative_values_with_months(3)
        for tab in live_sheets._PIPE_MOULD_FY_TABS
    }
    raw["Report-21"] = _cumulative_values_with_months(2)
    live_sheets._pipe_mould_fy_cache.clear()
    monkeypatch.setattr(live_sheets.sources, "FY_MONTHS", fiscal_months)
    monkeypatch.setattr(
        live_sheets, "_pipe_mould_file_id",
        lambda ym: "jun-workbook" if ym == "2026-06" else None,
    )
    monkeypatch.setattr(live_sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(live_sheets, "batch_get", lambda _fid, _tabs, _token: raw)

    data = live_sheets.load_pipe_moulds_fy("2627")
    cpvc = next(result for result in data["groups"] if result["group"] == "CPVC")

    assert data["complete_months"] == ["2026-04", "2026-05"]
    assert data["complete_n_months"] == 2
    assert cpvc["total_kg"] == 30
    assert [row["month_index"] for row in cpvc["months"]] == [0, 1]
    assert data["missing_months"] == [{
        "month": "2026-06", "materials": ["PPR"],
    }]
    live_sheets._pipe_mould_fy_cache.clear()


def test_loader_marks_source_unavailable_when_a_material_tab_is_absent(monkeypatch):
    fiscal_months = ["2026-04", "2026-05"]
    raw = {
        tab: _cumulative_values_with_months(2)
        for tab in live_sheets._PIPE_MOULD_FY_TABS
        if tab != "Report-21"
    }
    live_sheets._pipe_mould_fy_cache.clear()
    monkeypatch.setattr(live_sheets.sources, "FY_MONTHS", fiscal_months)
    monkeypatch.setattr(
        live_sheets, "_pipe_mould_file_id",
        lambda ym: "may-workbook" if ym == "2026-05" else None,
    )
    monkeypatch.setattr(live_sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(live_sheets, "batch_get", lambda _fid, _tabs, _token: raw)

    data = live_sheets.load_pipe_moulds_fy("2627")

    assert not data["available"]
    assert data["complete_months"] == []
    assert data["complete_n_months"] == 0
    assert data["missing"] == ["PPR"]
    assert data["missing_months"] == [
        {"month": "2026-04", "materials": ["PPR"]},
        {"month": "2026-05", "materials": ["PPR"]},
    ]
    live_sheets._pipe_mould_fy_cache.clear()


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


def test_builder_and_download_extend_to_latest_month_and_flag_partial_sources(monkeypatch):
    current = parsers.parse_cumulative_mould_fy(
        _cumulative_values_with_months(5), group="CPVC", n_months=5
    )
    prior = parsers.parse_annual_mould_summary_apr_jul(
        _annual_values(), group="CPVC"
    )

    class FakeSheets:
        @staticmethod
        def load_pipe_moulds_fy(_fy):
            return {
                "available": True, "n_months": 5, "complete_n_months": 5,
                "months": [
                    "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
                ],
                "latest_ym": "2026-08", "groups": [current], "missing": [],
                "missing_months": [], "partial_months": [],
            }

        @staticmethod
        def load_pipe_moulds_annual_2526():
            return {"available": True, "groups": [prior], "missing": []}

    monkeypatch.setattr(summary, "_sh", lambda: FakeSheets)

    built = summary.build_pipe_moulds_summary()
    current_block = built["blocks"][0]
    assert current_block["period_label"] == "Apr,26 – Aug,26"
    assert [r["month"] for r in current_block["month_rows"]] == [
        "Apr,26", "May,26", "Jun,26", "Jul,26", "Aug,26",
    ]
    baseline = built["baseline_block"]
    assert baseline is not None
    assert baseline["period_label"] == "Apr,26 – Jul,26"
    assert [r["month"] for r in baseline["month_rows"]] == [
        "Apr,26", "May,26", "Jun,26", "Jul,26",
    ]
    assert baseline["rows"][0]["kg"] == 100
    assert baseline["rows"][0]["n_run"] == 2
    assert baseline["total_row"]["kg"] == 100
    assert built["recon"]
    assert all(check["status"] == "WARN" for check in built["recon"])
    assert "primary cumulative period" in built["recon_note"]

    sheets, flags = serial_pipe_moulds("2026-08")
    summary_sheet = next(sheet for sheet in sheets if sheet.name == "SUMMARY")
    assert "PRIMARY cumulative subtotal" in summary_sheet.sections[0].heading
    assert "APR–JUL audit baseline subtotal" in summary_sheet.sections[1].heading
    assert "APR–JUL anchor reconciliation" in summary_sheet.sections[2].heading
    cpvc = next(sheet for sheet in sheets if sheet.name.startswith("CPVC"))
    assert [row["month"] for row in cpvc.sections[0].rows] == [
        "Apr,26", "May,26", "Jun,26", "Jul,26", "Aug,26",
    ]
    assert "audit baseline subtotal" in cpvc.sections[1].heading
    assert not [flag for flag in flags if flag.rule == "Incomplete-Month"]

    workbook = render_workbook(ReportModel(
        rid="pipe_moulds", label="Pipe Moulds", plant="KH", ym="2026-08",
        month_disp="Aug 2026", sheets=sheets, flags=flags,
    ))
    summary_ws = workbook["SUMMARY"]
    summary_text = {
        str(cell.value)
        for row in summary_ws.iter_rows()
        for cell in row
        if cell.value is not None
    }
    assert any("PRIMARY cumulative subtotal" in text for text in summary_text)
    assert any("APR–JUL audit baseline subtotal" in text for text in summary_text)
    assert any("APR–JUL anchor reconciliation" in text for text in summary_text)


def test_builder_and_download_flag_partial_month_blocks(monkeypatch):
    current = parsers.parse_cumulative_mould_fy(
        _cumulative_values_with_months(2), group="CPVC", n_months=3
    )
    prior = parsers.parse_annual_mould_summary_apr_jul(
        _annual_values(), group="CPVC"
    )

    class FakeSheets:
        @staticmethod
        def load_pipe_moulds_fy(_fy):
            return {
                "available": True, "n_months": 3, "complete_n_months": 2,
                "months": ["2026-04", "2026-05", "2026-06"],
                "complete_months": ["2026-04", "2026-05"],
                "latest_ym": "2026-06", "groups": [current], "missing": [],
                "missing_months": [{
                    "month": "2026-06", "materials": ["CPVC"],
                }],
                "partial_months": [],
            }

        @staticmethod
        def load_pipe_moulds_annual_2526():
            return {"available": True, "groups": [prior], "missing": []}

    monkeypatch.setattr(summary, "_sh", lambda: FakeSheets)

    built = summary.build_pipe_moulds_summary()
    current_block = built["blocks"][0]
    assert current_block["incomplete"]
    assert current_block["month_issues"][0]["note"] == "Missing month block: Jun,26 (CPVC)"
    assert [row["month"] for row in current_block["month_rows"]] == [
        "Apr,26", "May,26",
    ]

    _sheets, flags = serial_pipe_moulds("2026-06")
    assert any(
        flag.rule == "Incomplete-Month"
        and flag.month == "2026-06"
        and "Jun,26" in flag.note
        for flag in flags
    )


def test_download_renders_when_the_current_block_has_no_complete_source(monkeypatch):
    prior = parsers.parse_annual_mould_summary_apr_jul(
        _annual_values(), group="CPVC"
    )

    class FakeSheets:
        @staticmethod
        def load_pipe_moulds_fy(_fy):
            return {
                "available": False, "n_months": 2, "complete_n_months": 0,
                "months": ["2026-04", "2026-05"], "complete_months": [],
                "latest_ym": "2026-05", "groups": [], "missing": ["PPR"],
                "missing_months": [
                    {"month": "2026-04", "materials": ["PPR"]},
                    {"month": "2026-05", "materials": ["PPR"]},
                ],
                "partial_months": [],
            }

        @staticmethod
        def load_pipe_moulds_annual_2526():
            return {"available": True, "groups": [prior], "missing": []}

    monkeypatch.setattr(summary, "_sh", lambda: FakeSheets)

    sheets, flags = serial_pipe_moulds("2026-05")
    assert any(flag.rule == "Unavailable" for flag in flags)
    workbook = render_workbook(ReportModel(
        rid="pipe_moulds", label="Pipe Moulds", plant="KH", ym="2026-05",
        month_disp="May 2026", sheets=sheets, flags=flags,
    ))
    assert workbook.worksheets