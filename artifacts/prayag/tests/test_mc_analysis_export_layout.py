"""Regression coverage for management M/C analysis export layouts.

The management pages and their downloads share builder payloads.  These tests
assert the serializers preserve the page grids rather than reducing paired
machine/month cells to a single number.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mgmt_gom_summary
import mgmt_moulding_summary
import mgmt_pipe_summary
from reports import serialisers


def _sheet(sheets, name):
    return next(sheet for sheet in sheets if sheet.name == name)


def _keys(sheet):
    return [column.key for column in sheet.sections[0].columns]


def test_gom_export_keeps_both_fys_and_month_value_triplets(monkeypatch):
    """SUMMARY reads its two builder FY lists; month grids retain Hrs/KG/Avg."""
    payload = {
        "fy_label": "FY 2026-27",
        "section1": {
            "fy2627_label": "FY 2026-27",
            "fy2627": [{
                "band": "150", "mc_count": 2, "ideal_hrs": 1_000,
                "actual_hrs": 120, "output_kg": 1_500, "reject_kg": 20,
                "runner_kg": 5, "avg_hr": 12.5, "util_pct": 12,
            }, {
                "band": "TOTAL", "mc_count": 2, "ideal_hrs": 1_000,
                "actual_hrs": 120, "output_kg": 1_500, "reject_kg": 20,
                "runner_kg": 5, "avg_hr": 76.1, "avg_hr_weighted": 12.5,
                "avg_hr_sheet": 76.1, "util_pct": 12, "is_total": True,
            }],
            "fy2526_label": "FY 2025-26",
            "fy2526": [{
                "band": "150", "mc_count": 2, "ideal_hrs": 1_000,
                "actual_hrs": 100, "output_kg": 1_100, "reject_kg": 10,
                "runner_kg": 4, "avg_hr": 11, "util_pct": 10,
            }],
        },
        "section2": {
            "band_rows": [{
                "band": "150", "mc_count": 2,
                "total": {"hrs": 120, "gross_kg": 1_520, "avg_hr": 12.67},
                "months": {
                    "2026-04": {"hrs": 40, "gross_kg": 500, "avg_hr": 12.5},
                    "2026-05": {"hrs": 80, "gross_kg": 1_020, "avg_hr": 12.75},
                },
            }],
            "total_row": {
                "total": {"hrs": 120, "gross_kg": 1_520, "avg_hr": 12.67},
                "months": {
                    "2026-04": {"hrs": 40, "gross_kg": 500, "avg_hr": 12.5},
                    "2026-05": {"hrs": 80, "gross_kg": 1_020, "avg_hr": 12.75},
                },
            },
        },
        "section3": {
            "by_band": {
                "150": {
                    "machine_rows": [{
                        "band_mc_num": 1, "global_mc": "M/C - 1", "mould_id": "A-150",
                        "total": {"hrs": 120, "gross_kg": 1_520, "avg_hr": 12.67},
                        "months": {
                            "2026-04": {"hrs": 40, "gross_kg": 500, "avg_hr": 12.5},
                            "2026-05": {"hrs": 80, "gross_kg": 1_020, "avg_hr": 12.75},
                        },
                    }],
                    "total_row": {
                        "total": {"hrs": 120, "gross_kg": 1_520, "avg_hr": 12.67},
                    },
                },
            },
        },
    }
    monkeypatch.setattr(mgmt_gom_summary, "build_gom_summary", lambda fy: payload)

    sheets, _ = serialisers.serial_gom("2026-05")

    summary = _sheet(sheets, "SUMMARY")
    assert [section.heading for section in summary.sections] == ["FY 2026-27", "FY 2025-26"]
    assert summary.sections[0].rows[0]["output_kg"] == payload["section1"]["fy2627"][0]["output_kg"]
    assert summary.sections[1].rows[0]["output_kg"] == payload["section1"]["fy2526"][0]["output_kg"]
    assert summary.sections[0].total_row["avg_hr"] == payload["section1"]["fy2627"][1]["avg_hr_weighted"]
    assert summary.sections[0].total_row["avg_hr_sheet"] == payload["section1"]["fy2627"][1]["avg_hr_sheet"]

    expected_month_keys = [
        "band", "mc_count",
        "2026-04__hrs", "2026-04__gross_kg", "2026-04__avg_hr",
        "2026-05__hrs", "2026-05__gross_kg", "2026-05__avg_hr",
        "_total_hrs", "_total_kg", "_avg_hr",
    ]
    summary_1 = _sheet(sheets, "SUMMARY-1")
    assert _keys(summary_1) == expected_month_keys
    assert summary_1.sections[0].rows[0]["2026-05__gross_kg"] == 1_020
    assert summary_1.sections[0].rows[0]["2026-05__avg_hr"] == 12.75

    band = _sheet(sheets, "150")
    assert _keys(band) == [
        "band_mc_num", "global_mc", "mould_id",
        "2026-04__hrs", "2026-04__gross_kg", "2026-04__avg_hr",
        "2026-05__hrs", "2026-05__gross_kg", "2026-05__avg_hr",
        "_total_hrs", "_total_kg", "_avg_hr",
    ]
    assert band.sections[0].rows[0]["2026-04__hrs"] == 40
    assert band.sections[0].rows[0]["2026-04__gross_kg"] == 500
    assert band.sections[0].rows[0]["2026-04__avg_hr"] == 12.5


def test_pipe_mc_wise_export_keeps_hours_and_output_pairs(monkeypatch):
    """Pipe M/C WISE mirrors each page machine's Hours | Output pair."""
    payload = {
        "fy_label": "FY 2026-27",
        "section4": {
            "machines": ["M/C-1", "M/C-2"],
            "month_rows": [{
                "month_lbl": "APR",
                "cols": {
                    "M/C-1": {"hrs": 41, "out": 5_100},
                    "M/C-2": {"hrs": 32, "out": 3_900},
                },
            }],
            "total_cols": {
                "M/C-1": {"hrs": 41, "out": 5_100},
                "M/C-2": {"hrs": 32, "out": 3_900},
            },
        },
    }
    monkeypatch.setattr(mgmt_pipe_summary, "build_pipe_summary", lambda fy: payload)

    sheets, _ = serialisers.serial_pipe("2026-05")

    mc_wise = _sheet(sheets, "MC WISE")
    assert _keys(mc_wise) == [
        "month_lbl", "M/C-1__hrs", "M/C-1__out", "M/C-2__hrs", "M/C-2__out",
    ]
    assert mc_wise.sections[0].rows[0]["M/C-1__hrs"] == 41
    assert mc_wise.sections[0].rows[0]["M/C-1__out"] == 5_100
    assert mc_wise.sections[0].total_row["M/C-2__out"] == 3_900


def test_moulding_mc_wise_export_adds_complete_machine_pivot(monkeypatch):
    """Moulding now has the complete paired pivot in addition to HOURS."""
    payload = {
        "fy_label": "FY 2026-27",
        "section4": {
            "machines": ["M/C - 1", "M/C - 2"],
            "month_rows": [{
                "month_lbl": "APR",
                "cols": {
                    "M/C - 1": {"hrs": 50, "out": 625},
                    "M/C - 2": {"hrs": 20, "out": 180},
                },
            }],
            "total_cols": {
                "M/C - 1": {"hrs": 50, "out": 625},
                "M/C - 2": {"hrs": 20, "out": 180},
            },
        },
    }
    monkeypatch.setattr(mgmt_moulding_summary, "build_moulding_summary", lambda fy: payload)

    sheets, _ = serialisers.serial_moulding("2026-05")

    pivot = _sheet(sheets, "PIVOT")
    assert _keys(pivot) == [
        "month_lbl", "M/C - 1__hrs", "M/C - 1__out", "M/C - 2__hrs", "M/C - 2__out",
    ]
    assert pivot.sections[0].rows[0]["M/C - 1__hrs"] == 50
    assert pivot.sections[0].rows[0]["M/C - 1__out"] == 625
    assert pivot.sections[0].total_row["M/C - 2__out"] == 180