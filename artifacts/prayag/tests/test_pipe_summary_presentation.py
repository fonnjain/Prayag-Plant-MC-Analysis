"""Presentation-only regression coverage for the Pipe M/C Summary page."""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS


PRAYAG_DIR = os.path.join(os.path.dirname(__file__), "..")
if PRAYAG_DIR not in sys.path:
    sys.path.insert(0, PRAYAG_DIR)

import app as appmod
from flask import render_template


def _machine_row(*, is_total: bool = False) -> NS:
    return NS(
        is_total=is_total,
        machine="TOTAL" if is_total else "M/C-1",
        pipe_type="",
        ideal_hrs=22_500 if is_total else 2_000,
        actual_hrs=7_754 if is_total else 833,
        actual_out_kg=1_523_241 if is_total else 190_494,
        ideal_rate=120,
        avg_hr=228.7,
        util_pct=41.7,
        out_eff_pct=190.6,
    )


def test_pipe_summary_keeps_payroll_provenance_non_clickable_and_tables_full_width():
    payroll_url = "https://payroll.example.test/kh-1-apr"
    wages_source = NS(
        url=payroll_url,
        file_id="payroll-apr-file",
        label="KH-1 payroll · CPVC / PIPELINE",
    )
    def month_row(
        month_disp: str,
        run_hrs: int,
        gross_output_kg: int,
        *,
        awaiting: bool = False,
        source=wages_source,
    ) -> NS:
        return NS(
            month_disp=month_disp,
            run_hrs=run_hrs,
            gross_output_kg=gross_output_kg,
            labour=25,
            paid_hrs=7_502,
            wages=473_293,
            devoted_per_person=300.1,
            per_hour_cost=63.1,
            per_kg_cost=2.48,
            awaiting=awaiting,
            wages_source=source,
        )
    total_row = NS(
        run_hrs=7_754,
        gross_output_kg=1_523_241,
        labour=128,
        paid_hrs=37_619,
        wages=1_092_748,
        devoted_per_person=293.9,
        per_hour_cost=29.05,
        per_kg_cost=0.72,
        awaiting=True,
        parsed_wage_months=["APR'26", "MAY'26", "JUN'26", "JUL'26"],
        awaiting_months=["AUG'26"],
    )
    data = NS(
        fy_label="FY 2026-27",
        build_time_s=0.1,
        failed_months=["2026-05", "2026-06"],
        error=None,
        section1=NS(
            warnings=[],
            total_row=total_row,
            month_rows=[
                month_row("APR'26", 833, 190_494),
                month_row("MAY'26", 1_832, 344_000, awaiting=True),
                month_row("JUN'26", 1_008, 183_635),
                month_row("JUL'26", 2_834, 565_171, awaiting=True),
                month_row("AUG'26", 1_247, 239_941, source=None, awaiting=True),
                NS(
                    month_disp="SEP'26",
                    run_hrs=None,
                    gross_output_kg=None,
                    labour=None,
                    paid_hrs=None,
                    wages=None,
                    devoted_per_person=None,
                    per_hour_cost=None,
                    per_kg_cost=None,
                    awaiting=False,
                    wages_source=None,
                ),
            ],
        ),
        section2=NS(
            n_months=4,
            warnings=["Pipe M/C 25-26 tab read failed: Google Sheets API error (429)."],
            fy2627_label="Apr,26 – Jul,26 (FY 2026-27)",
            fy2627=[_machine_row(), _machine_row(is_total=True)],
            fy2526_label="Apr,25 – Jul,25 (FY 2025-26)",
            fy2526=[],
        ),
        section3=None,
        section4=None,
        report_yms=[
            "2026-04", "2026-05", "2026-06", "2026-07", "2026-08",
        ],
    )

    with appmod.app.test_request_context("/management-reports/pipe-summary"):
        html = render_template(
            "report_mgmt_pipe_summary.html",
            data=data,
            today_disp="20 Aug 2026",
            last_synced={},
        )

    assert payroll_url not in html
    assert "KH-1 payroll · CPVC / PIPELINE" not in html
    assert "Payroll provenance: wages come from the registered monthly KH-1 payroll workbooks" in html
    assert 'data-testid="partial-payroll-note"' in html
    assert 'data-testid="payroll-parse-footnote"' in html
    assert "UNAVAILABLE<sup>†</sup>" in html
    assert "AWAITING" in html
    assert "a registered KH-1 payroll workbook could not be parsed" in html
    assert "TOTAL" in html
    assert "<span class=\"text-[10px] font-medium text-gray-500\">(partial)</span>" in html
    assert "Partial payroll through APR&#39;26, MAY&#39;26, JUN&#39;26, JUL&#39;26" in html
    for figure in (
        "7,754", "1,523,241", "128", "37,619",
        "833", "190,494", "1,832", "344,000", "1,008", "183,635",
        "2,834", "565,171", "1,247", "239,941", "22,500",
    ):
        assert figure in html
    assert html.count("table-layout:fixed") >= 2
    assert 'class="space-y-4"' in html
    assert '<tr class="opacity-50"' in html
    assert "temporarily unavailable because the source tab could not be read" in html
    assert "Google Sheets API error (429)" in html