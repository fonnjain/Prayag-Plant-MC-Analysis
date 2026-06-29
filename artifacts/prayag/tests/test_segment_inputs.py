"""Unit tests for the pure Group-B segment-inputs assembly (segment_inputs.py).

Guards:
  * a field with no entered value stays None and is flagged "awaiting input";
  * an entered value flows through and clears the awaiting flag;
  * per-kg power cost is None until BOTH grid power AND a kg figure exist, then
    equals power / kg;
  * unit-specific fields only appear for their units (2nd solar = Unit-2 only).

Run: cd artifacts/prayag && python3 -m tests.test_segment_inputs
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import segment_inputs as si


def _row(view, month, unit):
    for r in view["rows"]:
        if r["month"] == month and r["unit"] == unit:
            return r
    raise AssertionError(f"row {month}/{unit} not found")


def test_awaiting_when_empty():
    view = si.build_segment_inputs(["2026-04"], {}, {})
    r = _row(view, "2026-04", "UNIT-1")
    assert r["cells"]["jvvl_power"]["awaiting"] is True
    assert r["cells"]["jvvl_power"]["value"] is None
    assert r["per_kg_power"] is None
    # Every applicable field for every unit is awaiting.
    assert view["n_awaiting"] == view["n_fields_total"] > 0
    assert view["complete"] is False
    print("ok: empty inputs -> all awaiting, nothing fabricated")


def test_entered_value_and_per_kg_power():
    inputs = {("2026-04", "UNIT-2"): {"jvvl_power": 200000.0, "set_by": "Asha"}}
    prod = {("2026-04", "UNIT-2"): {"kg": 50000.0}}
    view = si.build_segment_inputs(["2026-04"], inputs, prod)
    r = _row(view, "2026-04", "UNIT-2")
    assert r["cells"]["jvvl_power"]["awaiting"] is False
    assert r["cells"]["jvvl_power"]["value"] == 200000.0
    assert r["set_by"] == "Asha"
    # 200000 / 50000 = 4.0 ₹/kg
    assert abs(r["per_kg_power"] - 4.0) < 1e-9
    print("ok: entered power + kg production -> per-kg power = 4.00")


def test_per_kg_power_none_without_production():
    inputs = {("2026-04", "UNIT-1"): {"jvvl_power": 100000.0}}
    view = si.build_segment_inputs(["2026-04"], inputs, {})  # no production
    r = _row(view, "2026-04", "UNIT-1")
    assert r["cells"]["jvvl_power"]["awaiting"] is False
    assert r["per_kg_power"] is None  # power entered but no kg figure
    print("ok: power without kg production -> per-kg power stays None (no guess)")


def test_unit_specific_fields():
    view = si.build_segment_inputs(["2026-04"], {}, {})
    by_unit = {u["key"]: u for u in view["by_unit"]}
    u1_keys = {f["key"] for f in by_unit["UNIT-1"]["fields"]}
    u2_keys = {f["key"] for f in by_unit["UNIT-2"]["fields"]}
    # 2nd solar + 11.5 rate are not in Unit-1.
    assert "solar_gen2" not in u1_keys
    assert "rate_115" not in u1_keys
    # 2nd solar is Unit-2 only; 11.5 rate is in Unit-2.
    assert "solar_gen2" in u2_keys
    assert "rate_115" in u2_keys
    print("ok: unit-specific fields restricted correctly")


def test_per_kg_power_trend_skips_awaiting_months():
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-2"): {"jvvl_power": 100000.0},
        ("2026-05", "UNIT-2"): {"jvvl_power": 120000.0},
        # 2026-06 power not entered -> excluded from trend (never fabricated).
    }
    prod = {
        ("2026-04", "UNIT-2"): {"kg": 50000.0},
        ("2026-05", "UNIT-2"): {"kg": 40000.0},
        ("2026-06", "UNIT-2"): {"kg": 30000.0},
    }
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    trend = by_unit["UNIT-2"]["trend"]
    # Only the two months with BOTH power and kg appear, in order.
    assert [p["month"] for p in trend] == ["2026-04", "2026-05"]
    assert abs(trend[0]["value"] - 2.0) < 1e-9  # 100000 / 50000
    assert abs(trend[1]["value"] - 3.0) < 1e-9  # 120000 / 40000
    # A unit with no captured power has an empty trend (no fabricated points).
    assert by_unit["UNIT-1"]["trend"] == []
    print("ok: per-kg power trend includes only months with power+kg, in order")


def test_spike_alert_latest_consecutive_pair():
    # Apr→May per-kg power goes 2.0 → 3.0 (+50%), so the latest consecutive
    # change must flag a spike (exceeds the 15% threshold), direction "up".
    months = ["2026-04", "2026-05"]
    inputs = {
        ("2026-04", "UNIT-2"): {"jvvl_power": 100000.0},
        ("2026-05", "UNIT-2"): {"jvvl_power": 120000.0},
    }
    prod = {
        ("2026-04", "UNIT-2"): {"kg": 50000.0},  # 2.0 ₹/kg
        ("2026-05", "UNIT-2"): {"kg": 40000.0},  # 3.0 ₹/kg
    }
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    sp = by_unit["UNIT-2"]["spike"]
    assert sp is not None
    assert sp["exceeds"] is True
    assert sp["direction"] == "up"
    assert abs(sp["pct"] - 50.0) < 1e-9
    assert sp["prev_month"] == "2026-04" and sp["month"] == "2026-05"
    print("ok: +50% per-kg power MoM -> spike alert (up, exceeds)")


def test_spike_below_threshold_not_flagged():
    # 2.0 → 2.2 is only +10%, under the 15% threshold: spike present but
    # exceeds is False (the template shows nothing).
    months = ["2026-04", "2026-05"]
    inputs = {
        ("2026-04", "UNIT-2"): {"jvvl_power": 100000.0},
        ("2026-05", "UNIT-2"): {"jvvl_power": 110000.0},
    }
    prod = {
        ("2026-04", "UNIT-2"): {"kg": 50000.0},  # 2.0 ₹/kg
        ("2026-05", "UNIT-2"): {"kg": 50000.0},  # 2.2 ₹/kg
    }
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    sp = by_unit["UNIT-2"]["spike"]
    assert sp is not None
    assert abs(sp["pct"] - 10.0) < 1e-9
    assert sp["exceeds"] is False
    print("ok: +10% per-kg power MoM -> no spike (under threshold)")


def test_spike_never_bridges_awaiting_gap():
    # Apr has a value, May is awaiting (no power), Jun has a value. The Apr→Jun
    # change must NOT be computed as a MoM spike (the awaiting May breaks the
    # chain). With no consecutive valued pair, spike stays None.
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-2"): {"jvvl_power": 100000.0},
        # 2026-05 power not entered -> awaiting gap
        ("2026-06", "UNIT-2"): {"jvvl_power": 300000.0},
    }
    prod = {
        ("2026-04", "UNIT-2"): {"kg": 50000.0},  # 2.0 ₹/kg
        ("2026-05", "UNIT-2"): {"kg": 50000.0},
        ("2026-06", "UNIT-2"): {"kg": 50000.0},  # 6.0 ₹/kg
    }
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    assert by_unit["UNIT-2"]["spike"] is None
    print("ok: awaiting month between values -> no bridged spike (None)")


def test_spike_none_with_fewer_than_two_valued_months():
    months = ["2026-04"]
    inputs = {("2026-04", "UNIT-2"): {"jvvl_power": 100000.0}}
    prod = {("2026-04", "UNIT-2"): {"kg": 50000.0}}
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    assert by_unit["UNIT-2"]["spike"] is None
    print("ok: single valued month -> no spike (nothing fabricated)")


def test_spike_uses_latest_consecutive_pair():
    # Three consecutive valued months: 2.0 -> 4.0 (+100%) -> 4.2 (+5%). The
    # alert must reflect the LATEST pair (May->Jun, +5%, under threshold), not
    # the older big jump.
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-2"): {"jvvl_power": 100000.0},
        ("2026-05", "UNIT-2"): {"jvvl_power": 200000.0},
        ("2026-06", "UNIT-2"): {"jvvl_power": 210000.0},
    }
    prod = {
        ("2026-04", "UNIT-2"): {"kg": 50000.0},  # 2.0
        ("2026-05", "UNIT-2"): {"kg": 50000.0},  # 4.0
        ("2026-06", "UNIT-2"): {"kg": 50000.0},  # 4.2
    }
    view = si.build_segment_inputs(months, inputs, prod)
    by_unit = {u["key"]: u for u in view["by_unit"]}
    sp = by_unit["UNIT-2"]["spike"]
    assert sp["prev_month"] == "2026-05" and sp["month"] == "2026-06"
    assert abs(sp["pct"] - 5.0) < 1e-9
    assert sp["exceeds"] is False
    print("ok: latest consecutive pair drives the alert, not the older jump")


def test_solar_share_computed_only_when_both_present():
    # Share = solar / (grid + solar). Computed only when BOTH grid and solar are
    # entered; an awaiting half leaves it None (never fabricated).
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 600.0, "solar_gen": 400.0},  # 0.4
        ("2026-05", "UNIT-1"): {"elec_gen": 800.0},  # solar awaiting -> None
        ("2026-06", "UNIT-1"): {"solar_gen": 500.0},  # grid awaiting -> None
    }
    view = si.build_segment_inputs(months, inputs, {})
    assert abs(_row(view, "2026-04", "UNIT-1")["solar_share"] - 0.4) < 1e-9
    assert _row(view, "2026-05", "UNIT-1")["solar_share"] is None
    assert _row(view, "2026-06", "UNIT-1")["solar_share"] is None
    by_unit = {u["key"]: u for u in view["by_unit"]}
    # Only the one valued month appears in the solar trend.
    assert [p["month"] for p in by_unit["UNIT-1"]["solar_trend"]] == ["2026-04"]
    print("ok: solar share computed only when both grid & solar present")


def test_solar_alert_flags_sharp_drop():
    # Apr 50% -> May 30% is a 20-point drop, beyond the 10-pt threshold -> flagged.
    months = ["2026-04", "2026-05"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0},  # 0.50
        ("2026-05", "UNIT-1"): {"elec_gen": 700.0, "solar_gen": 300.0},  # 0.30
    }
    view = si.build_segment_inputs(months, inputs, {})
    sa = {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"]
    assert sa is not None
    assert sa["exceeds"] is True
    assert abs(sa["drop_pts"] - 20.0) < 1e-9
    assert abs(sa["prev_share"] - 0.5) < 1e-9 and abs(sa["share"] - 0.3) < 1e-9
    assert sa["prev_month"] == "2026-04" and sa["month"] == "2026-05"
    print("ok: 20-pt solar share drop -> alert (exceeds)")


def test_solar_alert_rise_not_flagged():
    # A RISING solar share is good news, never an alert: drop_pts is negative and
    # exceeds stays False.
    months = ["2026-04", "2026-05"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 700.0, "solar_gen": 300.0},  # 0.30
        ("2026-05", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0},  # 0.50
    }
    view = si.build_segment_inputs(months, inputs, {})
    sa = {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"]
    assert sa is not None
    assert sa["exceeds"] is False
    assert sa["drop_pts"] < 0
    print("ok: rising solar share -> no alert (drop negative)")


def test_solar_alert_small_drop_not_flagged():
    # 50% -> 45% is a 5-pt drop, under the 10-pt threshold: present but not exceeds.
    months = ["2026-04", "2026-05"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0},  # 0.50
        ("2026-05", "UNIT-1"): {"elec_gen": 550.0, "solar_gen": 450.0},  # 0.45
    }
    view = si.build_segment_inputs(months, inputs, {})
    sa = {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"]
    assert sa is not None
    assert abs(sa["drop_pts"] - 5.0) < 1e-9
    assert sa["exceeds"] is False
    print("ok: 5-pt solar share drop -> no alert (under threshold)")


def test_solar_alert_never_bridges_awaiting_gap():
    # Apr valued, May awaiting (no solar), Jun valued. Apr->Jun must NOT be a MoM
    # comparison; with no consecutive valued pair the alert stays None.
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0},  # 0.50
        ("2026-05", "UNIT-1"): {"elec_gen": 800.0},  # solar awaiting
        ("2026-06", "UNIT-1"): {"elec_gen": 900.0, "solar_gen": 100.0},  # 0.10
    }
    view = si.build_segment_inputs(months, inputs, {})
    assert {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"] is None
    print("ok: awaiting month between values -> no bridged solar alert (None)")


def test_solar_alert_none_with_fewer_than_two_valued_months():
    months = ["2026-04"]
    inputs = {("2026-04", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0}}
    view = si.build_segment_inputs(months, inputs, {})
    assert {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"] is None
    print("ok: single valued month -> no solar alert (nothing fabricated)")


def test_solar_alert_uses_latest_consecutive_pair():
    # Three valued months: 0.50 -> 0.20 (big drop) -> 0.18 (small drop). The alert
    # reflects the LATEST pair (May->Jun, 2-pt drop, under threshold), not the older
    # collapse.
    months = ["2026-04", "2026-05", "2026-06"]
    inputs = {
        ("2026-04", "UNIT-1"): {"elec_gen": 500.0, "solar_gen": 500.0},  # 0.50
        ("2026-05", "UNIT-1"): {"elec_gen": 800.0, "solar_gen": 200.0},  # 0.20
        ("2026-06", "UNIT-1"): {"elec_gen": 820.0, "solar_gen": 180.0},  # 0.18
    }
    view = si.build_segment_inputs(months, inputs, {})
    sa = {u["key"]: u for u in view["by_unit"]}["UNIT-1"]["solar_alert"]
    assert sa["prev_month"] == "2026-05" and sa["month"] == "2026-06"
    assert abs(sa["drop_pts"] - 2.0) < 1e-9
    assert sa["exceeds"] is False
    print("ok: latest consecutive pair drives the solar alert, not the older drop")


def test_complete_when_all_entered():
    months = ["2026-04"]
    inputs = {}
    for u in si.UNITS:
        vals = {f["key"]: 1.0 for f in si.fields_for_unit(u["key"])}
        inputs[("2026-04", u["key"])] = vals
    view = si.build_segment_inputs(months, inputs, {})
    assert view["n_awaiting"] == 0
    assert view["complete"] is True
    print("ok: all fields entered -> complete, zero awaiting")


if __name__ == "__main__":
    test_awaiting_when_empty()
    test_entered_value_and_per_kg_power()
    test_per_kg_power_none_without_production()
    test_unit_specific_fields()
    test_per_kg_power_trend_skips_awaiting_months()
    test_spike_alert_latest_consecutive_pair()
    test_spike_below_threshold_not_flagged()
    test_spike_never_bridges_awaiting_gap()
    test_spike_none_with_fewer_than_two_valued_months()
    test_spike_uses_latest_consecutive_pair()
    test_solar_share_computed_only_when_both_present()
    test_solar_alert_flags_sharp_drop()
    test_solar_alert_rise_not_flagged()
    test_solar_alert_small_drop_not_flagged()
    test_solar_alert_never_bridges_awaiting_gap()
    test_solar_alert_none_with_fewer_than_two_valued_months()
    test_solar_alert_uses_latest_consecutive_pair()
    test_complete_when_all_entered()
    print("\nALL segment_inputs tests passed")
