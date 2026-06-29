"""Route-level smoke tests for the Group-B segment-input surface.

Exercises the Flask wiring offline by monkeypatching the daily-records reader and
the store so nothing touches the network or the DB. Guards:
  * /segment-input renders 200 with the capture form for all three units;
  * an empty store shows "awaiting input";
  * /reports/segment_labour renders the manual-inputs panel with the validation
    of "awaiting input" cells and a per-kg power figure once inputs + production
    exist;
  * /reports/gom_summary and /reports/tank_vn carry their advisory badge.

Run: cd artifacts/prayag && python3 -m tests.test_segment_input_routes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import store


class _Rec:
    def __init__(self, plant, total_count, unit="kg", period="2026-04"):
        self.plant = plant
        self.total_count = total_count
        self.unit = unit
        self.grain = "daily"
        self.period = period
        self.date = period + "-05"


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_segment_input_page_awaiting():
    store.seg_inputs_for = lambda months: {}
    appmod.get_daily_records = lambda months: ([], [], [])
    client = _client()
    resp = client.get("/segment-input")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "Segment manual inputs" in body
    assert "UNIT-1" in body and "UNIT-2" in body and "UNIT-3" in body
    assert "awaiting input" in body
    assert "JVVL Power" in body
    print("ok: /segment-input -> 200, capture form, awaiting input")


def test_segment_labour_shows_per_kg_power():
    # Power entered for UNIT-2; recompute 50,000 kg production for UNIT-2 plants.
    store.seg_inputs_for = lambda months: {
        (months[0], "UNIT-2"): {"jvvl_power": 200000.0, "set_by": "Asha"}
    } if months else {}
    appmod.get_daily_records = lambda months: (
        [_Rec("PIPE", 50000.0, period=months[0])] if months else [], [], [])
    client = _client()
    resp = client.get("/reports/segment_labour?period=4")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "Manual monthly inputs" in body
    assert "awaiting input" in body          # other units/fields still awaiting
    assert "4.00" in body                    # 200000 / 50000 = ₹4.00/kg
    print("ok: /reports/segment_labour -> manual panel + per-kg power 4.00")


def _fill_all(month):
    """Every applicable field for every unit, for one month — a fully-captured month."""
    import segment_inputs as si
    out = {}
    for uk in si.UNIT_KEYS:
        out[(month, uk)] = {f["key"]: 1.0 for f in si.fields_for_unit(uk)}
    return out


def test_seg_input_summary_lists_awaiting_months():
    import segment_inputs as si

    store.AVAILABLE = True
    # Fully capture only the first current-FY month; every other month awaits.
    filled_month = appmod.FY_MONTHS[0]
    store.seg_inputs_for = lambda months: _fill_all(filled_month)

    summary = appmod._seg_input_summary()
    cur = summary[0]  # current_fy is first

    months = [m["month"] for m in cur["awaiting_months"]]
    assert filled_month not in months, months          # captured month omitted
    # Every other current-FY month present, in FY order.
    expected = [m for m in appmod.FY_MONTHS if m != filled_month]
    assert months == expected, (months, expected)
    # disp is the short month name (no year).
    assert all(len(m["disp"]) <= 3 for m in cur["awaiting_months"])
    print("ok: _seg_input_summary lists awaiting months, omits captured")


def test_reports_banner_shows_awaiting_months():
    store.AVAILABLE = True
    store.seg_inputs_for = lambda months: {}
    appmod.get_daily_records = lambda months: ([], [], [])
    client = _client()
    resp = client.get("/reports")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "awaiting" in body
    # The first current-FY month's short name appears in the banner month list.
    short = appmod._month_short(appmod.FY_MONTHS[0])
    assert short in body, short
    print("ok: /reports banner surfaces specific awaiting months")


def test_gom_and_tank_have_validation_badge():
    client = _client()
    g = client.get("/reports/gom_summary?period=prior_fy")
    assert g.status_code == 200
    assert "Recomputed from source grid" in g.get_data(as_text=True)
    t = client.get("/reports/tank_vn?period=prior_fy")
    assert t.status_code == 200
    assert "Annual summary source" in t.get_data(as_text=True)
    print("ok: gom + tank advisory validation badges present")


if __name__ == "__main__":
    test_segment_input_page_awaiting()
    test_segment_labour_shows_per_kg_power()
    test_gom_and_tank_have_validation_badge()
    print("\nALL segment_input route tests passed")
