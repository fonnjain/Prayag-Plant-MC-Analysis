"""tests/test_costing_analysis.py — Unit tests for costing_analysis.py.

Covers: build_cost_stack, build_fy_total, build_hours_analysis,
        build_mom_trends, build_cost_bridge, build_volume_sensitivity,
        build_warnings.

Uses pre-computed accepted figures for FY2026-27 Q1 (APR/MAY/JUN):
  Labour excl contractor : Rs 6.12/kg  (incl Rs 6.43/kg)
  Labour ideal           : Rs 3.67/kg  (pipe 2.50 × 637,410 + fitting 6.50 × 264,543) / 901,953
  Power actual (all-plant): Rs 8.24/kg
  Power ideal  (all-plant): Rs 4.86/kg
  Combined actual         : Rs 14.67/kg
  Combined ideal          : Rs 8.53/kg (≈ 72% over)
  Hours gap               : 11,028 h (11.8% of paid 93,443 h)
"""
from __future__ import annotations

import sys
import os
import math

import pytest

# Add prayag dir to path so the module can be imported without Flask context
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import costing_analysis as ca


# ── Fixture data ───────────────────────────────────────────────────────────────

# FY2627 Q1 labour rows (from DB / costing_labour_monthly)
LABOUR_APR = {
    "month_label": "APR", "month_num": 1,
    "pipe_prod_kg": 170063.0, "fitting_prod_kg": 90038.0,
    "paid_wages": 1_817_263.0, "contractor_wages": None,
    "paid_hours": 30_550.0, "actual_hours": 27_248.0,
}
LABOUR_MAY = {
    "month_label": "MAY", "month_num": 2,
    "pipe_prod_kg": 230073.0, "fitting_prod_kg": 91033.0,
    "paid_wages": 1_939_523.0, "contractor_wages": None,
    "paid_hours": 31_476.0, "actual_hours": 27_724.0,
}
LABOUR_JUN = {
    "month_label": "JUN", "month_num": 3,
    "pipe_prod_kg": 237274.0, "fitting_prod_kg": 83472.0,
    "paid_wages": 1_765_345.0, "contractor_wages": None,
    "paid_hours": 31_417.0, "actual_hours": 27_443.0,
}
LABOUR_ROWS = [LABOUR_APR, LABOUR_MAY, LABOUR_JUN]

# FY2627 Q1 power rows (UNIT-2 + Ideal Power Cost tab)
POWER_APR = {
    "month_label": "APR", "month_num": 1,
    "contractor_wages_u2": 180_447.0,
    "pipe_ideal_labour_rate": 2.50, "fitting_ideal_labour_rate": 6.50,
    "pipe_ideal_power_rate": 4.00,  "fitting_ideal_power_rate": 8.00,
    "ideal_kg_power": 4.80, "actual_kg_power": 8.94,
    "ideal_power_total": 1_332_870.0, "actual_power_total": 2_491_073.0,
    "jvvl_amount": 2_533_643.0, "total_kwh": 335_780.0,
    "per_unit_cost": 9.20, "solar1_kwh": 60_380.0, "solar2_kwh": 0.0,
    "kwh_per_kg": 1.29,
    "rate_708_rs": 352_540.0, "rate_1150_rs": 0.0,
    "total_power_708": 2_961_133.0, "total_power_1150": 3_228_013.0,
    "per_kg_power_708": 11.38, "per_kg_power_1150": 12.41,
}
POWER_MAY = {
    "month_label": "MAY", "month_num": 2,
    "contractor_wages_u2": 96_833.0,
    "pipe_ideal_labour_rate": 2.50, "fitting_ideal_labour_rate": 6.50,
    "pipe_ideal_power_rate": 4.00,  "fitting_ideal_power_rate": 8.00,
    "ideal_kg_power": 4.89, "actual_kg_power": 5.51,
    "ideal_power_total": 1_581_330.0, "actual_power_total": 1_787_437.0,
    "jvvl_amount": 1_907_328.0, "total_kwh": 286_155.0,
    "per_unit_cost": 8.36, "solar1_kwh": 58_140.0, "solar2_kwh": 0.0,
    "kwh_per_kg": 0.88,
    "rate_708_rs": 372_540.0, "rate_1150_rs": 0.0,
    "total_power_708": 1_783_538.0, "total_power_1150": 2_046_438.0,
    "per_kg_power_708": 5.51, "per_kg_power_1150": 6.13,
}
POWER_JUN = {
    "month_label": "JUN", "month_num": 3,
    "contractor_wages_u2": 0.0,
    "pipe_ideal_labour_rate": 2.50, "fitting_ideal_labour_rate": 6.50,
    "pipe_ideal_power_rate": 4.00,  "fitting_ideal_power_rate": 8.00,
    "ideal_kg_power": 4.85, "actual_kg_power": 14.78,
    "ideal_power_total": 1_547_660.0, "actual_power_total": 4_753_399.0,
    "jvvl_amount": 2_949_559.0, "total_kwh": 384_641.0,
    "per_unit_cost": 8.70, "solar1_kwh": 45_566.0, "solar2_kwh": 0.0,
    "kwh_per_kg": 1.19,
    "rate_708_rs": 436_649.0, "rate_1150_rs": 0.0,
    "total_power_708": 4_807_588.0, "total_power_1150": 5_003_068.0,
    "per_kg_power_708": 14.78, "per_kg_power_1150": 15.69,
}
POWER_ROWS = [POWER_APR, POWER_MAY, POWER_JUN]


# ── helpers ────────────────────────────────────────────────────────────────────

def _approx(a, b, tol=0.05):
    """Return True if a ≈ b within tol (absolute or relative ≤ tol)."""
    if a is None or b is None:
        return False
    if b == 0:
        return abs(a) < tol
    return abs(a - b) / abs(b) <= tol


# ── build_cost_stack ──────────────────────────────────────────────────────────

class TestBuildCostStack:

    def test_returns_three_months(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        assert len(stack) == 3

    def test_months_sorted_apr_to_jun(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        assert [r["month"] for r in stack] == ["APR", "MAY", "JUN"]

    def test_apr_total_kg(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        assert abs(apr["total_kg"] - (170063 + 90038)) < 1

    def test_labour_actual_incl_contractor_apr(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        expected = (1_817_263 + 180_447) / (170_063 + 90_038)
        assert _approx(apr["labour_actual_kg"], expected, tol=0.01)

    def test_labour_actual_excl_contractor_apr(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=False)
        apr = stack[0]
        expected = 1_817_263 / (170_063 + 90_038)
        assert _approx(apr["labour_actual_kg"], expected, tol=0.01)

    def test_labour_ideal_apr(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        expected = (170_063 * 2.50 + 90_038 * 6.50) / (170_063 + 90_038)
        assert _approx(apr["labour_ideal_kg"], expected, tol=0.01)

    def test_labour_ideal_pipe_heavy_rate(self):
        """Pipe-heavy month should have ideal rate closer to 2.50 than 6.50."""
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        for r in stack:
            assert 2.50 <= r["labour_ideal_kg"] <= 6.50, (
                f"{r['month']} ideal {r['labour_ideal_kg']} outside [2.50, 6.50]"
            )

    def test_power_actual_from_power_rows(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        assert _approx(apr["power_actual_kg"], 8.94, tol=0.01)

    def test_power_ideal_from_power_rows(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        assert _approx(apr["power_ideal_kg"], 4.80, tol=0.01)

    def test_combined_actual_sum(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        apr = stack[0]
        assert apr["combined_actual_kg"] is not None
        expected = apr["labour_actual_kg"] + apr["power_actual_kg"]
        assert _approx(apr["combined_actual_kg"], expected, tol=0.001)

    def test_labour_variance_pct_positive_when_over_ideal(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        for r in stack:
            if r["labour_variance_pct"] is not None:
                assert r["labour_variance_pct"] > 0, (
                    f"{r['month']}: labour should be over ideal but pct={r['labour_variance_pct']}"
                )

    def test_jun_contractor_zero(self):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)
        jun = stack[2]
        assert jun["contractor_wages"] == 0.0

    def test_empty_power_rows_graceful(self):
        """With no power rows, power fields should be None."""
        stack = ca.build_cost_stack(LABOUR_ROWS, [], incl_contractor=True)
        for r in stack:
            assert r["power_actual_kg"] is None
            assert r["power_ideal_kg"] is None

    def test_empty_labour_rows_returns_empty(self):
        stack = ca.build_cost_stack([], POWER_ROWS, incl_contractor=True)
        assert stack == []


# ── build_fy_total ────────────────────────────────────────────────────────────

class TestBuildFyTotal:

    def _stack(self, incl=True):
        return ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=incl)

    def test_fy_total_keys_present(self):
        tot = ca.build_fy_total(self._stack())
        for key in ("pipe_kg", "fitting_kg", "total_kg", "paid_wages",
                    "labour_actual_kg", "labour_ideal_kg", "n_months"):
            assert key in tot, f"Missing key: {key}"

    def test_total_volume(self):
        tot = ca.build_fy_total(self._stack())
        expected_pipe    = 170_063 + 230_073 + 237_274
        expected_fitting = 90_038  + 91_033  + 83_472
        assert abs(tot["pipe_kg"]    - expected_pipe)    < 2
        assert abs(tot["fitting_kg"] - expected_fitting) < 2
        assert abs(tot["total_kg"]   - (expected_pipe + expected_fitting)) < 2

    def test_total_wages_incl_contractor(self):
        tot = ca.build_fy_total(self._stack(incl=True))
        expected_wages = (1_817_263 + 1_939_523 + 1_765_345)
        expected_contr = (180_447  + 96_833   + 0)
        assert abs(tot["paid_wages"]       - expected_wages) < 2
        assert abs(tot["contractor_wages"] - expected_contr) < 2
        assert abs(tot["total_wages"]      - (expected_wages + expected_contr)) < 2

    def test_labour_excl_approx_6_12(self):
        """Labour excl contractor should be ≈ Rs 6.12/kg (acceptance figure)."""
        tot = ca.build_fy_total(self._stack(incl=True))
        assert tot["labour_excl_kg"] is not None
        assert _approx(tot["labour_excl_kg"], 6.12, tol=0.03)

    def test_labour_actual_approx_6_43(self):
        """Labour incl contractor should be ≈ Rs 6.43/kg (acceptance figure)."""
        tot = ca.build_fy_total(self._stack(incl=True))
        assert tot["labour_actual_kg"] is not None
        assert _approx(tot["labour_actual_kg"], 6.43, tol=0.03)

    def test_labour_ideal_approx_3_67(self):
        """Labour ideal should be ≈ Rs 3.67/kg (acceptance figure)."""
        tot = ca.build_fy_total(self._stack(incl=True))
        assert tot["labour_ideal_kg"] is not None
        assert _approx(tot["labour_ideal_kg"], 3.67, tol=0.05)

    def test_n_months(self):
        tot = ca.build_fy_total(self._stack())
        assert tot["n_months"] == 3

    def test_empty_stack_returns_empty(self):
        tot = ca.build_fy_total([])
        assert tot == {}

    def test_labour_variance_positive(self):
        tot = ca.build_fy_total(self._stack())
        assert tot.get("labour_variance_pct", 0) > 0


# ── build_hours_analysis ──────────────────────────────────────────────────────

class TestBuildHoursAnalysis:

    def test_totals(self):
        hrs = ca.build_hours_analysis(LABOUR_ROWS)
        expected_paid   = 30_550 + 31_476 + 31_417
        expected_actual = 27_248 + 27_724 + 27_443
        assert abs(hrs["paid_hours_fy"]   - expected_paid)   < 2
        assert abs(hrs["actual_hours_fy"] - expected_actual) < 2

    def test_diff_approx_11028(self):
        """Accepted: hours gap ≈ 11,028."""
        hrs = ca.build_hours_analysis(LABOUR_ROWS)
        assert _approx(hrs["hours_diff"], 11_028, tol=0.03)

    def test_diff_pct_approx_11_8(self):
        """Accepted: gap % ≈ 11.8%."""
        hrs = ca.build_hours_analysis(LABOUR_ROWS)
        assert _approx(hrs["hours_diff_pct"], 11.8, tol=0.05)

    def test_warning_flag_set(self):
        hrs = ca.build_hours_analysis(LABOUR_ROWS)
        assert hrs["warning"] is True

    def test_monthly_list_has_three(self):
        hrs = ca.build_hours_analysis(LABOUR_ROWS)
        assert len(hrs["monthly"]) == 3

    def test_no_hours_data_no_warning(self):
        rows = [{"month_label": "APR", "month_num": 1,
                 "paid_hours": 0.0, "actual_hours": 0.0}]
        hrs = ca.build_hours_analysis(rows)
        assert hrs["warning"] is False


# ── build_mom_trends ──────────────────────────────────────────────────────────

class TestBuildMomTrends:

    def _stack(self):
        return ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)

    def test_len_equals_stack(self):
        stack = self._stack()
        mom   = ca.build_mom_trends(stack)
        assert len(mom) == len(stack)

    def test_first_month_no_mom_delta(self):
        mom = ca.build_mom_trends(self._stack())
        first = mom[0]
        assert first.get("mom_labour_pct") is None
        assert first.get("mom_power_pct")  is None

    def test_second_month_has_mom_delta(self):
        mom = ca.build_mom_trends(self._stack())
        second = mom[1]
        assert "mom_labour_pct"   in second
        assert "mom_combined_pct" in second

    def test_empty_stack_returns_empty(self):
        assert ca.build_mom_trends([]) == []


# ── build_cost_bridge ─────────────────────────────────────────────────────────

class TestBuildCostBridge:

    # Scale prev FY labour rows to create a plausible prior year
    PREV_LABOUR_APR = {
        "month_label": "APR", "month_num": 1,
        "pipe_prod_kg": 155_000.0, "fitting_prod_kg": 80_000.0,
        "paid_wages": 1_650_000.0, "contractor_wages": 160_000.0,
        "paid_hours": 29_000.0, "actual_hours": 26_000.0,
    }
    PREV_LABOUR_MAY = {
        "month_label": "MAY", "month_num": 2,
        "pipe_prod_kg": 210_000.0, "fitting_prod_kg": 85_000.0,
        "paid_wages": 1_800_000.0, "contractor_wages": 90_000.0,
        "paid_hours": 30_000.0, "actual_hours": 27_000.0,
    }
    PREV_POWER_APR = {
        "month_label": "APR", "month_num": 1,
        "contractor_wages_u2": 160_000.0,
        "pipe_ideal_labour_rate": 2.50, "fitting_ideal_labour_rate": 6.50,
    }
    PREV_POWER_MAY = {
        "month_label": "MAY", "month_num": 2,
        "contractor_wages_u2": 90_000.0,
        "pipe_ideal_labour_rate": 2.50, "fitting_ideal_labour_rate": 6.50,
    }

    def test_available_when_prev_data_present(self):
        cb = ca.build_cost_bridge(
            LABOUR_ROWS[:2], POWER_ROWS[:2],
            [self.PREV_LABOUR_APR, self.PREV_LABOUR_MAY],
            [self.PREV_POWER_APR, self.PREV_POWER_MAY],
            incl_contractor=True,
        )
        assert cb["available"] is True

    def test_unavailable_with_no_prev_data(self):
        cb = ca.build_cost_bridge(LABOUR_ROWS, POWER_ROWS, [], [], incl_contractor=True)
        assert cb["available"] is False

    def test_rate_plus_volume_approx_total(self):
        """rate_effect + volume_effect + mix_effect ≈ total_change.

        The bridge is a 3-factor linear approximation; a small residual
        (≤ 5%) is expected due to cross-product interaction terms.
        """
        cb = ca.build_cost_bridge(
            LABOUR_ROWS[:2], POWER_ROWS[:2],
            [self.PREV_LABOUR_APR, self.PREV_LABOUR_MAY],
            [self.PREV_POWER_APR, self.PREV_POWER_MAY],
            incl_contractor=True,
        )
        reconstructed = cb["rate_effect"] + cb["volume_effect"]
        if cb.get("mix_effect") is not None:
            reconstructed += cb["mix_effect"]
        assert _approx(reconstructed, cb["total_change"], tol=0.05), (
            f"rate+volume+mix={reconstructed:.4f} total={cb['total_change']:.4f}"
        )

    def test_total_change_direction(self):
        cb = ca.build_cost_bridge(
            LABOUR_ROWS[:2], POWER_ROWS[:2],
            [self.PREV_LABOUR_APR, self.PREV_LABOUR_MAY],
            [self.PREV_POWER_APR, self.PREV_POWER_MAY],
            incl_contractor=True,
        )
        # curr_cost_kg and prev_cost_kg should differ by total_change
        implied = cb["curr_cost_kg"] - cb["prev_cost_kg"]
        assert _approx(implied, cb["total_change"], tol=0.001)


# ── build_volume_sensitivity ──────────────────────────────────────────────────

class TestBuildVolumeSensitivity:

    def _stack(self):
        return ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=True)

    def test_four_scenarios(self):
        vs = ca.build_volume_sensitivity(self._stack())
        assert len(vs["scenarios"]) == 4

    def test_first_scenario_is_current(self):
        vs = ca.build_volume_sensitivity(self._stack())
        assert vs["scenarios"][0]["label"] == "Current"
        assert vs["scenarios"][0]["mult"] == 1.0

    def test_scenario_labels_correct(self):
        vs = ca.build_volume_sensitivity(self._stack())
        labels = [s["label"] for s in vs["scenarios"]]
        assert labels == ["Current", "+10%", "+20%", "+30%"]

    def test_higher_volume_lower_cost_per_kg(self):
        """More volume → lower Rs/kg (fixed-cost absorption)."""
        vs = ca.build_volume_sensitivity(self._stack())
        labour_vals = [s["labour_kg"] for s in vs["scenarios"] if s["labour_kg"]]
        for i in range(1, len(labour_vals)):
            assert labour_vals[i] < labour_vals[i - 1], (
                f"Labour Rs/kg did not fall from scenario {i-1} to {i}: "
                f"{labour_vals[i-1]:.2f} → {labour_vals[i]:.2f}"
            )

    def test_base_vol_matches_stack_total(self):
        stack = self._stack()
        vs    = ca.build_volume_sensitivity(stack)
        total_kg = sum(r["total_kg"] for r in stack)
        assert abs(vs["base_vol_kg"] - total_kg) < 2

    def test_empty_stack_returns_empty(self):
        vs = ca.build_volume_sensitivity([])
        assert vs == {}


# ── build_warnings ────────────────────────────────────────────────────────────

class TestBuildWarnings:

    def _inputs(self, incl=True):
        stack = ca.build_cost_stack(LABOUR_ROWS, POWER_ROWS, incl_contractor=incl)
        tot   = ca.build_fy_total(stack)
        hrs   = ca.build_hours_analysis(LABOUR_ROWS)
        return stack, tot, hrs

    def test_returns_list(self):
        stack, tot, hrs = self._inputs()
        w = ca.build_warnings(stack, tot, hrs)
        assert isinstance(w, list)

    def test_at_least_one_warning(self):
        stack, tot, hrs = self._inputs()
        w = ca.build_warnings(stack, tot, hrs)
        assert len(w) >= 1

    def test_severity_values_valid(self):
        stack, tot, hrs = self._inputs()
        for item in ca.build_warnings(stack, tot, hrs):
            assert item["severity"] in ("red", "amber", "info")

    def test_red_warning_for_high_labour_variance(self):
        """FY2627 Q1 is +75% over ideal → should fire red."""
        stack, tot, hrs = self._inputs()
        warnings = ca.build_warnings(stack, tot, hrs)
        severities = [w["severity"] for w in warnings]
        assert "red" in severities, "Expected at least one red warning for +75% labour overage"

    def test_hours_warning_present_when_gap_over_10pct(self):
        """Gap of 11.8% should trigger hours warning."""
        stack, tot, hrs = self._inputs()
        warnings = ca.build_warnings(stack, tot, hrs)
        titles = [w["title"].lower() for w in warnings]
        assert any("hour" in t or "paid" in t for t in titles), (
            f"No hours warning found. Titles: {titles}"
        )

    def test_sorted_red_first(self):
        stack, tot, hrs = self._inputs()
        warnings = ca.build_warnings(stack, tot, hrs)
        order_map = {"red": 0, "amber": 1, "info": 2}
        orders = [order_map[w["severity"]] for w in warnings]
        assert orders == sorted(orders), "Warnings not sorted red→amber→info"

    def test_no_red_for_ideal_figures(self):
        """A stack where actual == ideal should produce no red warnings."""
        perfect_labour = [
            {**LABOUR_APR, "paid_wages": (170063 * 2.50 + 90038 * 6.50)},
        ]
        perfect_power = [
            {**POWER_APR, "actual_kg_power": POWER_APR["ideal_kg_power"],
             "actual_power_total": POWER_APR["ideal_power_total"]},
        ]
        perfect_hrs_row = [{**LABOUR_APR, "paid_hours": 27_248.0}]
        stack = ca.build_cost_stack(perfect_labour, perfect_power, incl_contractor=False)
        tot   = ca.build_fy_total(stack)
        hrs   = ca.build_hours_analysis(perfect_hrs_row)
        warnings = ca.build_warnings(stack, tot, hrs)
        reds = [w for w in warnings if w["severity"] == "red"]
        assert len(reds) == 0, f"Unexpected red warnings: {reds}"


# ── _safe_div and _pct helpers ────────────────────────────────────────────────

class TestHelpers:

    def test_safe_div_normal(self):
        assert ca._safe_div(10.0, 4.0) == 2.5

    def test_safe_div_zero_den(self):
        assert ca._safe_div(10.0, 0.0) is None

    def test_safe_div_none_den(self):
        assert ca._safe_div(10.0, None) is None

    def test_pct_positive(self):
        assert ca._pct(12.0, 10.0) == 20.0

    def test_pct_negative(self):
        assert ca._pct(8.0, 10.0) == -20.0

    def test_pct_zero_ideal(self):
        assert ca._pct(8.0, 0.0) is None

    def test_flt_none_returns_default(self):
        assert ca._flt(None) == 0.0
        assert ca._flt(None, 99.0) == 99.0

    def test_flt_converts_string(self):
        assert ca._flt("3.14") == pytest.approx(3.14)
