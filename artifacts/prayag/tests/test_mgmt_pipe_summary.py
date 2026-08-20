"""Regression tests for Pipe M/C Summary payroll-source handling."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

PRAYAG_DIR = os.path.join(os.path.dirname(__file__), "..")
if PRAYAG_DIR not in sys.path:
    sys.path.insert(0, PRAYAG_DIR)

import mgmt_pipe_summary as pipe_summary
import sheets


JULY_WAGES_SOURCE = "1Rzs9I_Ua6ij1Es65S-T-xEBAk8o9E0wrpiB5h9JQrSM"


def _pipe_record(
    period: str,
    hours: float,
    output: float,
    reject: float = 0.0,
    machine: str = "PIPE M/C - 1",
):
    return SimpleNamespace(
        plant="PIPE",
        period=period,
        actual_hours=hours,
        total_count=output,
        reject_count=reject,
        machine=machine,
    )


def _row(summary: dict, month_label: str) -> dict:
    return next(
        row for row in summary["month_rows"] if row["month_lbl"] == month_label
    )


def test_july_pipeline_payroll_is_registered_parsed_and_included(monkeypatch):
    """A parsed July source clears its row and contributes to the partial FY total."""
    assert pipe_summary.PIPE_WAGES["2627"]["2026-07"] == JULY_WAGES_SOURCE

    wages = {
        "2026-04": 473_293.0,
        "2026-07": 619_455.0,
    }
    monkeypatch.setattr(
        pipe_summary,
        "_read_pipeline_wages",
        lambda ym, _fid, _token: wages.get(ym),
    )

    summary = pipe_summary._build_section1(
        "2627",
        [
            _pipe_record("2026-04", 833, 190_494),
            _pipe_record("2026-07", 2_834, 564_695),
        ],
        {
            "APR": {"labour": 25, "paid_hrs": 7_502.5},
            "JUL": {"labour": 38, "paid_hrs": 10_732.0},
        },
        token="test",
    )

    july = _row(summary, "JUL")
    assert july["awaiting"] is False
    assert july["wages"] == 619_455.0
    assert july["per_hour_cost"] == pytest.approx(619_455 / 10_732)
    assert july["per_kg_cost"] == pytest.approx(619_455 / 564_695)
    assert july["wages_source"]["file_id"] == JULY_WAGES_SOURCE
    assert july["wages_source"]["parsed"] is True

    # The future months stay visibly partial, but the total includes every
    # source that successfully parsed, including July.
    assert summary["total_row"]["wages"] == 1_092_748.0
    assert summary["total_row"]["per_hour_cost"] == pytest.approx(
        1_092_748 / (7_502.5 + 10_732)
    )
    assert summary["total_row"]["per_kg_cost"] == pytest.approx(
        1_092_748 / (190_494 + 564_695)
    )
    assert summary["total_row"]["parsed_wage_months"] == ["APR'26", "JUL'26"]
    assert summary["total_row"]["awaiting"] is True
    assert "AUG'26" in summary["total_row"]["awaiting_months"]


def test_unregistered_future_wage_source_stays_awaiting(monkeypatch):
    """A genuinely unavailable future source never becomes a zero-cost month."""
    monkeypatch.setattr(
        pipe_summary,
        "_read_pipeline_wages",
        lambda ym, _fid, _token: 619_455.0 if ym == "2026-07" else None,
    )

    summary = pipe_summary._build_section1(
        "2627",
        [_pipe_record("2026-07", 2_834, 564_695)],
        {"JUL": {"labour": 38, "paid_hrs": 10_732.0}},
        token="test",
    )

    august = _row(summary, "AUG")
    assert august["awaiting"] is True
    assert august["wages"] is None
    assert august["per_hour_cost"] is None
    assert august["per_kg_cost"] is None
    assert august["wages_source"] is None
    assert summary["total_row"]["wages"] == 619_455.0
    assert "AUG'26" in summary["total_row"]["awaiting_months"]


def test_registered_wage_source_must_parse_before_it_enters_total(monkeypatch):
    """A broken source remains awaiting and never contributes a fabricated zero."""
    monkeypatch.setattr(
        pipe_summary,
        "_read_pipeline_wages",
        lambda _ym, _fid, _token: None,
    )

    summary = pipe_summary._build_section1(
        "2627",
        [_pipe_record("2026-07", 2_834, 564_695)],
        {"JUL": {"labour": 38, "paid_hrs": 10_732.0}},
        token="test",
    )

    july = _row(summary, "JUL")
    assert july["awaiting"] is True
    assert july["wages"] is None
    assert july["wages_source"]["file_id"] == JULY_WAGES_SOURCE
    assert july["wages_source"]["parsed"] is False
    assert summary["total_row"]["wages"] is None
    assert any("JUL: registered KH-1 payroll source" in w for w in summary["warnings"])


def _stub_pipe_builder_sources(monkeypatch, daily_result):
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "test-token")
    monkeypatch.setattr(sheets, "get_daily_records", lambda _months: daily_result)
    monkeypatch.setattr(
        sheets,
        "get_records",
        lambda _months: pytest.fail("Pipe summary must not read annual records"),
    )
    monkeypatch.setattr(sheets, "read_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        pipe_summary,
        "_read_pipeline_wages",
        lambda _ym, _fid, _token: None,
    )


def test_builder_uses_reconciled_daily_pipe_records_not_annual_grid(monkeypatch):
    daily_record = _pipe_record("2026-04", hours=10, output=100, reject=10)
    _stub_pipe_builder_sources(monkeypatch, ([daily_record], [], []))
    pipe_summary._cache.clear()

    try:
        result = pipe_summary.build_pipe_summary("2627")
    finally:
        pipe_summary._cache.clear()

    april = _row(result["section1"], "APR")
    assert april["run_hrs"] == 10
    assert april["gross_output_kg"] == 110
    mc1 = next(row for row in result["section2"]["fy2627"] if row["machine"] == "M/C-1")
    assert mc1["actual_hrs"] == 10
    assert mc1["actual_out_kg"] == 110
    assert result["failed_months"] == []


def test_failed_daily_pipe_month_is_visible_and_not_counted_complete(monkeypatch):
    daily_record = _pipe_record("2026-04", hours=10, output=100)
    _stub_pipe_builder_sources(
        monkeypatch,
        (
            [daily_record],
            [{"_failed_pairs": [("PIPE", "2026-05")]}],
            [],
        ),
    )
    pipe_summary._cache.clear()

    try:
        result = pipe_summary.build_pipe_summary("2627")
    finally:
        pipe_summary._cache.clear()

    assert result["failed_months"] == ["2026-05"]
    assert result["section2"]["n_months"] == 1
    assert any(
        "MAY: PIPE daily workbook (2026-05)" in warning
        and "report is partial" in warning
        for warning in result["section1"]["warnings"]
    )


def test_builder_excludes_daily_pipe_records_outside_the_selected_fy(monkeypatch):
    april = _pipe_record("2026-04", hours=10, output=100)
    no_period = _pipe_record(None, hours=5, output=50)
    _stub_pipe_builder_sources(monkeypatch, ([april, no_period], [], []))
    pipe_summary._cache.clear()

    try:
        result = pipe_summary.build_pipe_summary("2627")
    finally:
        pipe_summary._cache.clear()

    assert result["section1"]["total_row"]["gross_output_kg"] == 100
    assert result["section2"]["fy2627"][-1]["actual_out_kg"] == 100
    assert any(
        "1 PIPE daily record(s) without a month in FY 2627 were excluded"
        in warning
        for warning in result["section1"]["warnings"]
    )