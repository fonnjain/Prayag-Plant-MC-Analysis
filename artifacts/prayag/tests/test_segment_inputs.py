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
    test_complete_when_all_entered()
    print("\nALL segment_inputs tests passed")
