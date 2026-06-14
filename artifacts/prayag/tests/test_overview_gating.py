"""UI regression test: when confirmation.status == 'error', the Overview must
block (withhold) the headline figure and all of its visual forms — the numeric
headline, the doughnut gauge, the A/P/Q (or utilisation/efficiency) bars, the
monthly trend chart, and the per-plant summary — so no blocked metric value
appears anywhere in the rendered DOM or inline chart data.

Run: cd artifacts/prayag && python3 -m tests.test_overview_gating
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import render_template
from app import app


def _ctx(status):
    o = {
        "headline": 73.4, "headline_rating": "amber", "headline_label": "Fair",
        "oee_available": False,
        "util_available": True, "eff_available": True, "headline_available": True,
        "availability": 90.0, "performance": 88.0, "quality": 95.0,
        "utilisation": 80.0, "output_efficiency": 73.4,
        "total_count": 12345.0, "attainment": 88.0,
        "rejection_pct": 1.2, "reject_count": 148.0, "downtime_min": 320.0,
    }
    return {
        "overall_dict": o,
        "confirmation": {
            "status": status,
            "counts": {"error": 3, "total": 7},
            "score_label": "4/4 files · 35/35 machines · 2/12 months",
        },
        "by_plant": {"PIPE": o},
        "plant_names": {"PIPE": "Pipe Plant"},
        "trend_label": "Output Efficiency",
        "trend_labels": ["Apr", "May"], "trend_values": [70.0, 73.4],
        "plant_labels": ["PIPE"], "plant_oee": [73.4],
        "narrative": "",
        "period": "current_fy", "period_label": "Current FY",
        "plant_filter": "", "segment_filter": "", "machine_filter": "",
        "grain_banner": "", "rows": [1], "demo_mode": False,
        "glossary_data": {},
    }


# The literal headline number must never appear in the blocked DOM/chart data.
HEADLINE_TOKEN = "73.4"
FIGURE_TOKENS = ["oeeGauge", "trendChart", "plantChart", "const oeeVal",
                 "const doee", "const pOEE", HEADLINE_TOKEN]


def test_overview_blocks_all_headline_figures_on_error():
    with app.test_request_context("/?period=current_fy"):
        html = render_template("overview.html", **_ctx("error"))
    assert "Needs review" in html, "blocked state must show a 'Needs review' label"
    assert "Figure withheld" in html, "blocked headline must show a withheld placeholder"
    for tok in FIGURE_TOKENS:
        assert tok not in html, f"blocked figure token leaked into DOM: {tok!r}"
    print("PASS: error state withholds the headline figure and all its visuals")


def test_overview_publishes_figures_when_reconciled():
    with app.test_request_context("/?period=current_fy"):
        html = render_template("overview.html", **_ctx("pass"))
    assert "oeeGauge" in html, "clean state must render the gauge"
    assert "trendChart" in html, "clean state must render the trend chart"
    assert HEADLINE_TOKEN in html, "clean state must publish the headline figure"
    assert "Figure withheld" not in html, "clean state must not show a withheld placeholder"
    print("PASS: reconciled state publishes the headline figure and charts")


def test_overview_shows_no_baseline_when_unavailable():
    ctx = _ctx("pass")
    o = ctx["overall_dict"]
    o["util_available"] = False
    o["eff_available"] = False
    o["headline_available"] = False
    o["headline_rating"] = "red"
    with app.test_request_context("/?period=7d"):
        html = render_template("overview.html", **ctx)
    assert "No baseline set" in html, "no-baseline state must show the 'No baseline set' placeholder"
    assert "No baseline" in html, "no-baseline bars must show a 'No baseline' label, not 0%"
    print("PASS: no-baseline state hides utilisation/efficiency and shows a baseline notice")


if __name__ == "__main__":
    test_overview_blocks_all_headline_figures_on_error()
    test_overview_publishes_figures_when_reconciled()
    test_overview_shows_no_baseline_when_unavailable()
    print("\nAll overview gating regression tests passed.")
