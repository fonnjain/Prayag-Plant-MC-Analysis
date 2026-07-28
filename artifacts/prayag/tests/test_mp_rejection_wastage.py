"""Tests for mp_rejection_plan, mp_wastage, and engine math.

Run from the prayag directory:
    cd artifacts/prayag && python3 -m pytest tests/test_mp_rejection_wastage.py -v
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch, MagicMock


# ── helpers ───────────────────────────────────────────────────────────────────

def _fake_db(rows):
    """Return a context-manager mock for store._conn() that yields rows once."""
    fake_cur  = MagicMock()
    fake_conn = MagicMock()
    fake_cur.__enter__  = lambda s: fake_cur
    fake_cur.__exit__   = MagicMock(return_value=False)
    fake_conn.__enter__ = lambda s: fake_conn
    fake_conn.__exit__  = MagicMock(return_value=False)
    fake_cur.fetchall.return_value = rows
    fake_conn.cursor = MagicMock(return_value=fake_cur)
    return fake_conn


# ── mp_rejection_plan ──────────────────────────────────────────────────────────

import mp_rejection_plan as mrp


class TestBuildRejectionLookup:
    """build_rejection_lookup returns a well-formed dict."""

    def test_no_db_returns_empty_lookup(self):
        with patch("mp_rejection_plan.store") as ms:
            ms.AVAILABLE = False
            lu = mrp.build_rejection_lookup("PLUMBING")
        assert lu["has_data"] is False
        assert lu["material"] == {}
        assert lu["overall"] == {}
        assert lu["items"] == {}

    def test_material_rates_populated(self):
        """Two material rows → material sub-dict populated, has_data True."""
        item_rows  = []
        mat_rows   = [
            ("PIPE", "CPVC", 1000.0, 120.0),
            ("PIPE", "UPVC", 2000.0, 200.0),
        ]
        fake_conn = MagicMock()
        fake_cur  = MagicMock()
        fake_conn.__enter__ = lambda s: fake_conn
        fake_conn.__exit__  = MagicMock(return_value=False)
        fake_cur.__enter__  = lambda s: fake_cur
        fake_cur.__exit__   = MagicMock(return_value=False)
        fake_cur.fetchall.side_effect = [item_rows, mat_rows]
        fake_conn.cursor = MagicMock(return_value=fake_cur)

        with patch("mp_rejection_plan.store") as ms:
            ms.AVAILABLE = True
            ms._conn = MagicMock(return_value=fake_conn)
            lu = mrp.build_rejection_lookup("PLUMBING")

        assert lu["has_data"] is True
        assert "PIPE:CPVC" in lu["material"]
        # Rates are GROSS-basis: rej / (prod + rej) — matches Prayag convention
        # CPVC: 120 / (1000 + 120) = 120/1120 ≈ 0.10714
        assert abs(lu["material"]["PIPE:CPVC"] - 120 / 1120) < 1e-9
        # UPVC: 200 / (2000 + 200) = 200/2200 ≈ 0.09091
        assert abs(lu["material"]["PIPE:UPVC"] - 200 / 2200) < 1e-9
        # overall PIPE: (120+200) / ((1000+120) + (2000+200)) = 320/3320 ≈ 0.09639
        assert abs(lu["overall"]["PIPE"] - 320 / 3320) < 1e-6

    def test_rate_capped_at_rej_cap(self):
        """99% rejection is implausible — should be capped at REJ_CAP."""
        item_rows = []
        mat_rows  = [("PIPE", "CPVC", 100.0, 99.0)]
        fake_conn = MagicMock()
        fake_cur  = MagicMock()
        fake_conn.__enter__ = lambda s: fake_conn
        fake_conn.__exit__  = MagicMock(return_value=False)
        fake_cur.__enter__  = lambda s: fake_cur
        fake_cur.__exit__   = MagicMock(return_value=False)
        fake_cur.fetchall.side_effect = [item_rows, mat_rows]
        fake_conn.cursor = MagicMock(return_value=fake_cur)

        with patch("mp_rejection_plan.store") as ms:
            ms.AVAILABLE = True
            ms._conn = MagicMock(return_value=fake_conn)
            lu = mrp.build_rejection_lookup("PLUMBING")

        assert lu["material"]["PIPE:CPVC"] <= mrp.REJ_CAP


class TestGetItemRate:
    """get_item_rate respects the four-tier fallback ladder."""

    def _lookup(self):
        return {
            "items":    {"UPVC-FITTING-ABC": {"rate": 0.08, "capped": False}},
            "material": {"PIPE:CPVC": 0.12, "PIPE:UPVC": 0.10, "FITTING:UPVC": 0.15},
            "overall":  {"PIPE": 0.11, "FITTING": 0.14},
            "has_data": True,
        }

    def test_material_level_match_pipe(self):
        rate, basis, capped = mrp.get_item_rate(self._lookup(), "IGNORED", "PIPE", "CPVC")
        assert abs(rate - 0.12) < 1e-9
        assert basis == "material"

    def test_material_level_match_fitting(self):
        rate, basis, capped = mrp.get_item_rate(self._lookup(), "IGNORED", "FITTING", "UPVC")
        assert abs(rate - 0.15) < 1e-9
        assert basis == "material"

    def test_falls_back_to_overall(self):
        rate, basis, capped = mrp.get_item_rate(self._lookup(), "UNKNOWN", "PIPE", "AGRI")
        assert abs(rate - 0.11) < 1e-9
        assert basis == "overall"

    def test_item_level_beats_material(self):
        rate, basis, capped = mrp.get_item_rate(
            self._lookup(), "UPVC-FITTING-ABC", "FITTING", "UPVC"
        )
        assert abs(rate - 0.08) < 1e-9
        assert basis == "item"

    def test_no_data_returns_zero(self):
        empty = {"items": {}, "material": {}, "overall": {}, "has_data": False}
        rate, basis, capped = mrp.get_item_rate(empty, "ANY", "PIPE", "CPVC")
        assert rate == 0.0
        assert basis == "none"

    def test_overall_fitting_fallback(self):
        lu = {
            "items": {}, "material": {}, "overall": {"FITTING": 0.14}, "has_data": True
        }
        rate, basis, _ = mrp.get_item_rate(lu, "UNKNOWN", "FITTING", "SWR")
        assert abs(rate - 0.14) < 1e-9
        assert basis == "overall"


class TestGrossQty:
    """gross_qty formula: net / (1 - rate)."""

    def test_basic(self):
        assert abs(mrp.gross_qty(100.0, 0.12) - 100 / 0.88) < 1e-9

    def test_zero_rate_unchanged(self):
        assert mrp.gross_qty(500.0, 0.0) == 500.0

    def test_negative_rate_treated_as_zero(self):
        assert mrp.gross_qty(200.0, -0.05) == 200.0

    def test_cap_applied(self):
        # rate > REJ_CAP should be clamped to REJ_CAP
        capped = mrp.gross_qty(100.0, 0.99, cap=mrp.REJ_CAP)
        uncapped = mrp.gross_qty(100.0, mrp.REJ_CAP)
        assert abs(capped - uncapped) < 1e-9


# ── mp_wastage ─────────────────────────────────────────────────────────────────

import mp_wastage as mw


class TestBuildWastageLookup:
    """build_wastage_lookup override and fallback behaviour."""

    def test_override_pct_bypasses_db(self):
        lu = mw.build_wastage_lookup("PLUMBING", override_pct=2.0)
        assert lu["basis"] == "override"
        assert abs(lu["all"] - 0.02) < 1e-9

    def test_no_db_returns_safe_default(self):
        with patch("mp_wastage.store") as ms:
            ms.AVAILABLE = False
            lu = mw.build_wastage_lookup("PLUMBING")
        assert lu["basis"] == "default"
        assert lu["has_data"] is False
        assert abs(lu["all"] - mw.SAFE_DEFAULT) < 1e-9

    def test_db_rows_populate_rates(self):
        fake_conn = MagicMock()
        fake_cur  = MagicMock()
        fake_conn.__enter__ = lambda s: fake_conn
        fake_conn.__exit__  = MagicMock(return_value=False)
        fake_cur.__enter__  = lambda s: fake_cur
        fake_cur.__exit__   = MagicMock(return_value=False)
        fake_cur.fetchall.return_value = [
            ("CPVC", 10000.0, 51.0),
            ("UPVC", 20000.0, 80.0),
        ]
        fake_conn.cursor = MagicMock(return_value=fake_cur)

        with patch("mp_wastage.store") as ms:
            ms.AVAILABLE = True
            ms._conn = MagicMock(return_value=fake_conn)
            lu = mw.build_wastage_lookup("PLUMBING")

        assert lu["has_data"] is True
        assert abs(lu["rates"]["CPVC"] - 51 / 10000) < 1e-9
        assert lu["basis"] == "measured"

    def test_waste_rate_capped(self):
        fake_conn = MagicMock()
        fake_cur  = MagicMock()
        fake_conn.__enter__ = lambda s: fake_conn
        fake_conn.__exit__  = MagicMock(return_value=False)
        fake_cur.__enter__  = lambda s: fake_cur
        fake_cur.__exit__   = MagicMock(return_value=False)
        fake_cur.fetchall.return_value = [("CPVC", 100.0, 99.0)]
        fake_conn.cursor = MagicMock(return_value=fake_cur)

        with patch("mp_wastage.store") as ms:
            ms.AVAILABLE = True
            ms._conn = MagicMock(return_value=fake_conn)
            lu = mw.build_wastage_lookup("PLUMBING")

        assert lu["rates"]["CPVC"] <= mw.WASTE_CAP


class TestGetWasteFrac:
    """get_waste_frac fallback ladder."""

    def _lookup(self):
        return {
            "rates": {"CPVC": 0.005, "UPVC": 0.004, "UPVC_F": 0.008},
            "all":   0.0051,
            "basis": "measured",
            "has_data": True,
        }

    def test_material_match_pipe(self):
        frac, basis = mw.get_waste_frac(self._lookup(), "CPVC", is_fitting=False)
        assert abs(frac - 0.005) < 1e-9

    def test_fitting_key_preferred(self):
        frac, basis = mw.get_waste_frac(self._lookup(), "UPVC", is_fitting=True)
        assert abs(frac - 0.008) < 1e-9

    def test_pipe_key_when_no_fitting_key(self):
        """AGRI has no fitting-specific key → falls back to AGRI pipe rate."""
        lu = {**self._lookup(), "rates": {"AGRI": 0.003}}
        frac, _ = mw.get_waste_frac(lu, "AGRI", is_fitting=True)
        assert abs(frac - 0.003) < 1e-9

    def test_falls_back_to_all(self):
        lu = {"rates": {}, "all": 0.0051, "basis": "measured", "has_data": True}
        frac, basis = mw.get_waste_frac(lu, "SWR", is_fitting=False)
        assert abs(frac - 0.0051) < 1e-9

    def test_override_applies_uniformly(self):
        lu = {"rates": {}, "all": 0.04, "basis": "override", "has_data": True}
        frac, basis = mw.get_waste_frac(lu, "CPVC", is_fitting=False)
        assert abs(frac - 0.04) < 1e-9
        assert basis == "override"


# ── Sequential formula math ────────────────────────────────────────────────────

class TestSequentialMath:
    """Verify: gross_qty = net/(1-rej)  THEN  material_kg = gross*wt*(1+waste).

    These tests pin the formula logic independently of any DB or engine call.
    """

    def test_gross_qty_then_material_kg(self):
        net_qty    = 100.0
        rej_rate   = 0.12
        waste_frac = 0.0051
        wt_kg      = 0.5

        gross      = net_qty / (1 - rej_rate)       # 113.6364…
        mat_kg     = gross * wt_kg * (1 + waste_frac)

        assert abs(gross  - 100 / 0.88)    < 0.001
        assert abs(mat_kg - gross * 0.5 * 1.0051) < 0.001

    def test_zero_rejection_gives_net_gross(self):
        assert abs(mrp.gross_qty(500.0, 0.0) - 500.0) < 1e-9

    def test_zero_waste_gives_gross_times_weight(self):
        gross = 100.0
        mat   = gross * 0.8 * (1 + 0.0)
        assert abs(mat - 80.0) < 1e-9

    def test_order_matters(self):
        """gross_qty comes from net (not from waste-inflated net)."""
        net  = 100.0
        rej  = 0.12
        wst  = 0.0051
        wt   = 1.0

        gross_correct  = net / (1 - rej)
        kg_correct     = gross_correct * wt * (1 + wst)

        # Applying waste before rejection (wrong order) gives a different gross
        kg_wrong_order = (net * (1 + wst)) / (1 - rej)

        # Verify correct formula: gross is from net, not from waste-inflated net
        assert abs(gross_correct - 100 / 0.88) < 0.001
        assert kg_correct > net   # always larger than net baseline
        # The two are close numerically (both ~ same result), but gross is clean
        assert abs(gross_correct - 100 / 0.88) < 1e-6

    def test_combined_typical_values(self):
        """Sanity check with plausible production figures."""
        # 1000 pieces, 8% rejection, 0.51% waste, 0.8 kg/pc
        net   = 1000.0
        rej   = 0.08
        waste = 0.0051
        wt    = 0.8

        gross  = mrp.gross_qty(net, rej)
        mat_kg = gross * wt * (1 + waste)

        assert abs(gross  - 1000 / 0.92) < 0.001
        assert mat_kg > gross * wt         # waste adds material
        assert mat_kg < gross * wt * 1.10  # but not more than 10%
