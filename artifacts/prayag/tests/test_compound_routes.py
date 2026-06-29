"""Route-level regression tests for /reports/compound_compilation.

Exercises the Flask wiring offline by monkeypatching the compound data loader
and the daily-records reader so nothing touches the network. Guards:
  * the report renders 200 with the mass-balance table, reconciliation badge,
    yield panel and raw-material breakdown,
  * a PASS reconciliation shows the PASS badge,
  * a read OUTAGE (loader raises SheetReadError) degrades to the honest
    sheet-error page, NOT a silent "no data" success.

Run: cd artifacts/prayag && python3 -m tests.test_compound_routes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from sheets import SheetReadError


def _fake_data():
    return {
        "by_compound": {
            "CPVC": [{"opening": 1000.0, "given_label": "Total Compound given", "days": [
                {"batch": 1000.0, "material": 990.0, "given": 950.0, "loss": 10.0,
                 "pulvizer": 5.0, "chems": {"Resin K-67": 600.0, "CACO-3": 390.0}},
            ]}],
            "UPVC": [{"opening": 500.0, "given_label": "Total Compound given", "days": [
                {"batch": 2000.0, "material": 1980.0, "given": 1900.0, "loss": 20.0,
                 "pulvizer": 0.0, "chems": {"Resin K-67": 1200.0}},
            ]}],
        },
        "rollup": {"2026-06": {
            "CPVC": {"batch": 1000.0, "material": 990.0, "given": 950.0},
            "UPVC": {"batch": 2000.0, "material": 1980.0, "given": 1900.0},
        }},
        "months": ["2026-06"],
    }


def _install(loader=None, daily_output=53000.0):
    appmod.load_compound_data = loader or (lambda months: _fake_data())
    appmod.get_daily_records = lambda months: ([], [], [])
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_compound_report_renders_pass():
    client = _install()
    resp = client.get("/reports/compound_compilation?period=6")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "Compound Compilation" in body
    assert "Compound Mass-Balance" in body
    assert "Reconciliation vs in-sheet rollup" in body
    assert "PASS" in body                       # exact recompute reconciles
    assert "Raw-Material Breakdown" in body
    assert "Resin K-67" in body
    print("ok: /reports/compound_compilation -> 200, table + PASS badge")


def _dated_data():
    """Two dated logbook days in June so a single-date window can be isolated."""
    return {
        "by_compound": {
            "CPVC": [{"opening": 1000.0, "given_label": "Total Compound given", "days": [
                {"date": "2026-06-10", "batch": 8000.0, "material": 7777.0, "given": 7000.0,
                 "loss": 10.0, "pulvizer": 5.0, "chems": {"Resin K-67": 7000.0}},
                {"date": "2026-06-20", "batch": 4000.0, "material": 3333.0, "given": 3000.0,
                 "loss": 5.0, "pulvizer": 0.0, "chems": {"Resin K-67": 3000.0}},
            ]}],
        },
        "rollup": {"2026-06": {"CPVC": {"batch": 12000.0, "material": 11110.0, "given": 10000.0}}},
        "months": ["2026-06"],
    }


def test_compound_report_daily_window_isolates_one_day():
    client = _install(loader=lambda months: _dated_data())
    resp = client.get("/reports/compound_compilation?period=2026-06-10")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    # Only the 2026-06-10 day is summed (material 7,777) — NOT both days (11,110).
    assert "7,777" in body, "windowed-day figure missing"
    assert "11,110" not in body, "window leaked the out-of-window day into the total"
    # Flow view: explicit daily-flow note + N/A reconciliation (the monthly
    # rollup cannot reconcile a partial window).
    assert "Daily flow view" in body
    assert "N/A" in body
    assert "PASS" not in body
    # Yield is whole-month, so it is suppressed (—) in a window view rather than
    # shown as a misleading window-given ÷ month-output ratio.
    assert "Conversion" in body and "11,110" not in body
    print("ok: ?period=<iso-date> -> only that day's figures, flow N/A reconciliation")


def _trend_data():
    """Two ym-tagged months so month_trend yields a real FY series.

    The same dataset is requested two ways below: a whole-FY view (trend chart
    populated) vs a single-date window (trend chart suppressed). Using one
    dataset for both proves the *route's* window branch is what blanks the
    chart — not an empty trend.
    """
    return {
        "by_compound": {
            "CPVC": [
                {"ym": "2026-05", "opening": 1000.0, "given_label": "Total Compound given", "days": [
                    {"date": "2026-05-15", "batch": 5000.0, "material": 4900.0, "given": 4800.0,
                     "loss": 50.0, "pulvizer": 0.0, "chems": {"Resin K-67": 4800.0}},
                ]},
                {"ym": "2026-06", "opening": 1200.0, "given_label": "Total Compound given", "days": [
                    {"date": "2026-06-15", "batch": 6000.0, "material": 5900.0, "given": 5800.0,
                     "loss": 60.0, "pulvizer": 0.0, "chems": {"Resin K-67": 5800.0}},
                ]},
            ],
        },
        "rollup": {
            "2026-05": {"CPVC": {"batch": 5000.0, "material": 4900.0, "given": 4800.0}},
            "2026-06": {"CPVC": {"batch": 6000.0, "material": 5900.0, "given": 5800.0}},
        },
        "months": ["2026-05", "2026-06"],
    }


def test_compound_report_whole_month_shows_trend():
    client = _install(loader=lambda months: _trend_data())
    resp = client.get("/reports/compound_compilation?period=current_fy")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    # Whole-FY view: the month-over-month trend chart IS rendered (the card only
    # appears when trend_labels is a non-empty JSON array).
    assert "Compound Trend" in body, "whole-month view should render the trend chart"
    assert 'id="compTrend"' in body, "whole-month view should emit the trend canvas"
    print("ok: whole-FY view -> populated month-over-month trend chart")


def test_compound_report_window_hides_trend():
    client = _install(loader=lambda months: _trend_data())
    # Single-date window over a day that DOES have logbook data, so the page
    # renders the flow slice (the same dataset shows a trend for current_fy).
    resp = client.get("/reports/compound_compilation?period=2026-06-15")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    # The trend chart is cleanly suppressed for a sub-monthly window: the card
    # heading and its canvas are both absent (trend series emptied by the route).
    assert "Compound Trend" not in body, "window view must hide the trend chart"
    assert 'id="compTrend"' not in body, "window view must not emit the trend canvas"
    # ...and the page switches to the flow-only validation: an N/A reconciliation
    # badge plus the daily-flow note explaining a window shows flow only.
    assert "Daily flow view" in body, "window view should show the flow-only note"
    assert "N/A" in body
    assert "PASS" not in body
    print("ok: sub-monthly window -> trend chart hidden, flow-only validation")


def test_compound_report_read_outage_is_honest():
    def boom(months):
        raise SheetReadError("temporary Google Sheets limit")
    client = _install(loader=boom)
    resp = client.get("/reports/compound_compilation?period=current_fy")
    # Honest failure page (200 body, but the error message — never a fake empty).
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "temporary Google Sheets limit" in body
    assert "Compound Mass-Balance" not in body
    print("ok: read outage -> honest sheet-error page, not silent no-data")


if __name__ == "__main__":
    test_compound_report_renders_pass()
    test_compound_report_daily_window_isolates_one_day()
    test_compound_report_whole_month_shows_trend()
    test_compound_report_window_hides_trend()
    test_compound_report_read_outage_is_honest()
    print("all compound route tests passed")
