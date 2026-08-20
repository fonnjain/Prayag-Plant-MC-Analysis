"""Template-level regressions for management-report partial-source banners."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import render_template
import app


def _render(template: str, data: dict) -> str:
    with app.app.test_request_context("/management-reports/test"):
        return render_template(
            template,
            data=data,
            today_disp="20 Aug 2026",
            last_synced="just now",
        )


def test_gom_banner_marks_withheld_daily_month():
    html = _render(
        "report_mgmt_gom_summary.html",
        {
            "fy_label": "FY 2026-27", "failed_months": ["2026-06"],
            "warnings": [], "build_time_s": None, "band_order": [],
            "section1": {
                "fy2627_label": "", "fy2627": [],
                "fy2526_label": "", "fy2526": [], "warnings": [],
            },
            "section2": {"label": "", "band_rows": [], "total_row": {}},
            "section3": {"by_band": {}},
        },
    )
    assert 'data-testid="partial-daily-source-banner"' in html
    assert "Incomplete daily source" in html
    assert "2026-06" in html


def test_ptmt_banner_marks_withheld_daily_month():
    html = _render(
        "report_mgmt_ptmt_summary.html",
        {
            "fy_label": "FY 2026-27", "failed_months": ["2026-06"],
            "rows": [], "sheet_total_bugs": [], "r24_notes": {},
        },
    )
    assert 'data-testid="partial-daily-source-banner"' in html
    assert "Incomplete daily source" in html
    assert "2026-06" in html


def test_segment_labour_banner_marks_withheld_daily_month():
    html = _render(
        "report_mgmt_segment_labour.html",
        {
            "fy_label": "FY 2026-27", "error": None, "units": [],
            "daily_partial_warnings": [
                "PTMT 2026-06: daily source could not be read completely."
            ],
        },
    )
    assert 'data-testid="partial-daily-source-banner"' in html
    assert "Incomplete daily source" in html
    assert "PTMT 2026-06" in html


def test_labour_page_banner_marks_withheld_daily_month():
    with app.app.test_request_context("/labour"):
        html = render_template(
            "labour.html",
            plants=[],
            total_hrs=None,
            manual={},
            period="current_fy",
            period_label="FY 2026-27",
            partial_daily_pairs=[("PTMT", "2026-06")],
            today_disp="20 Aug 2026",
            last_synced="just now",
        )
    assert 'data-testid="partial-daily-source-banner"' in html
    assert "Incomplete daily source" in html


def test_pipe_banner_marks_withheld_daily_month():
    html = _render(
        "report_mgmt_pipe_summary.html",
        {
            "fy_label": "FY 2026-27",
            "failed_months": ["2026-06"],
            "error": "Source unavailable",
        },
    )
    assert 'data-testid="partial-daily-source-banner"' in html
    assert "Incomplete daily source" in html
    assert "2026-06" in html