"""
Fixture-backed tests for parse_material_stock (Phase 2B material readiness).

All tests run offline against captured fixtures — no network.
Acceptance item counts from the Phase 2B spec:
    PIPE  RM   42 / BOP 31 / PACK 15
    PTMT  BOP  53 / PACK 32 / RM  21
"""
import json
import pytest
from pathlib import Path
from typing import Optional

FIXTURES = Path(__file__).parent / "fixtures"

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import parsers
import planning


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipe_rm_values():
    return json.loads((FIXTURES / "pipe_material_rm_2026_06.json").read_text())


@pytest.fixture(scope="module")
def pipe_bop_values():
    return json.loads((FIXTURES / "pipe_material_bop_2026_06.json").read_text())


@pytest.fixture(scope="module")
def pipe_pack_values():
    return json.loads((FIXTURES / "pipe_material_pack_2026_06.json").read_text())


@pytest.fixture(scope="module")
def ptmt_bop_values():
    return json.loads((FIXTURES / "ptmt_material_bop_2026_06.json").read_text())


@pytest.fixture(scope="module")
def ptmt_pack_values():
    return json.loads((FIXTURES / "ptmt_material_pack_2026_06.json").read_text())


@pytest.fixture(scope="module")
def ptmt_rm_values():
    return json.loads((FIXTURES / "ptmt_material_rm_2026_06.json").read_text())


# ---------------------------------------------------------------------------
# C1 — PIPE Report-2 (Raw Material, 42 items)
# ---------------------------------------------------------------------------

class TestPipeMaterialRM:

    def test_item_count(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        assert len(recs) == 42

    def test_plant_and_category(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        assert all(r.plant == "PIPE" for r in recs)
        assert all(r.category == "RM" for r in recs)

    def test_first_item_code(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        assert recs[0].item_code == "PLR32"

    def test_first_item_closing_stock(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        assert recs[0].closing_stock == pytest.approx(221018.0, abs=1.0)

    def test_first_item_lead_time(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        assert recs[0].lead_time_days == pytest.approx(40.0, abs=0.5)

    def test_days_of_cover_recomputed(self, pipe_rm_values):
        """days_of_cover must be derived from closing / (avg_consumption/30), not the sheet cell."""
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        r = recs[0]
        if r.avg_consumption_month > 0 and r.closing_stock >= 0:
            expected = r.closing_stock / (r.avg_consumption_month / 30.0)
            assert r.days_of_cover == pytest.approx(expected, rel=1e-3)

    def test_reorder_flag_logic(self, pipe_rm_values):
        """reorder_flag must be True iff days_of_cover <= lead_time_days (both known)."""
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        for r in recs:
            if r.days_of_cover is not None and r.lead_time_days > 0:
                expected_flag = r.days_of_cover <= r.lead_time_days
                assert r.reorder_flag == expected_flag, (
                    f"{r.item_code}: cover={r.days_of_cover:.1f} lt={r.lead_time_days} "
                    f"flag={r.reorder_flag} expected={expected_flag}"
                )

    def test_pipe_has_stock_days_sheet(self, pipe_rm_values):
        """PIPE RM Report-2 has a pre-computed 'Stock Days' column."""
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        sheet_day_items = [r for r in recs if r.stock_days_sheet is not None]
        assert len(sheet_day_items) > 0, "Expected at least some PIPE RM items with stock_days_sheet"

    def test_stock_days_sheet_positive(self, pipe_rm_values):
        """Where present, stock_days_sheet must be > 0."""
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        for r in recs:
            if r.stock_days_sheet is not None:
                assert r.stock_days_sheet > 0

    def test_cover_not_taken_from_sheet(self, pipe_rm_values):
        """days_of_cover must NOT equal stock_days_sheet blindly — it is always recomputed."""
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        # PLR32: recomputed ≈154, sheet=102 — they should NOT be equal
        plr32 = next((r for r in recs if r.item_code == "PLR32"), None)
        if plr32 and plr32.stock_days_sheet is not None and plr32.days_of_cover is not None:
            # Recomputed should differ from sheet (different formula used in the spreadsheet)
            assert abs(plr32.days_of_cover - plr32.stock_days_sheet) > 1.0, (
                "days_of_cover should be recomputed, not copied from sheet"
            )

    def test_suggested_purchase_on_reorder(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        for r in recs:
            if r.reorder_flag:
                assert r.suggested_purchase >= 0
                if r.ideal_stock > 0 or r.min_batch > 0:
                    assert r.suggested_purchase > 0

    def test_no_total_rows_in_items(self, pipe_rm_values):
        recs = parsers.parse_material_stock(pipe_rm_values, "PIPE", "RM")
        for r in recs:
            assert "TOTAL" not in r.item_code.upper()


# ---------------------------------------------------------------------------
# C2 — PIPE Report-3 (BOP, 31 items; uses "Buffer Stock in Days")
# ---------------------------------------------------------------------------

class TestPipeMaterialBOP:

    def test_item_count(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        assert len(recs) == 31

    def test_plant_and_category(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        assert all(r.plant == "PIPE" and r.category == "BOP" for r in recs)

    def test_first_item_code(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        assert recs[0].item_code == "PLB3"

    def test_ideal_stock_derived_from_buffer_days(self, pipe_bop_values):
        """BOP tab uses 'Buffer Stock (in Days)' — ideal_stock must be converted to units."""
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        # Items with avg_consumption > 0 should have ideal_stock > 0
        items_with_consumption = [r for r in recs if r.avg_consumption_month > 0]
        if items_with_consumption:
            # At least some should have a non-zero ideal_stock derived from buffer days
            ideal_nonzero = [r for r in items_with_consumption if r.ideal_stock > 0]
            assert len(ideal_nonzero) > 0

    def test_reorder_count(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        reorder = sum(1 for r in recs if r.reorder_flag)
        assert reorder == 3

    def test_pipe_bop_has_stock_days_sheet(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        sheet_day_items = [r for r in recs if r.stock_days_sheet is not None]
        assert len(sheet_day_items) > 0

    def test_days_of_cover_non_negative(self, pipe_bop_values):
        recs = parsers.parse_material_stock(pipe_bop_values, "PIPE", "BOP")
        for r in recs:
            if r.days_of_cover is not None:
                assert r.days_of_cover >= 0


# ---------------------------------------------------------------------------
# C3 — PIPE Report-4 (Packaging, 15 items)
# ---------------------------------------------------------------------------

class TestPipeMaterialPACK:

    def test_item_count(self, pipe_pack_values):
        recs = parsers.parse_material_stock(pipe_pack_values, "PIPE", "PACK")
        assert len(recs) == 15

    def test_plant_and_category(self, pipe_pack_values):
        recs = parsers.parse_material_stock(pipe_pack_values, "PIPE", "PACK")
        assert all(r.plant == "PIPE" and r.category == "PACK" for r in recs)

    def test_first_item_code(self, pipe_pack_values):
        recs = parsers.parse_material_stock(pipe_pack_values, "PIPE", "PACK")
        assert recs[0].item_code == "PLB102"

    def test_reorder_count(self, pipe_pack_values):
        recs = parsers.parse_material_stock(pipe_pack_values, "PIPE", "PACK")
        reorder = sum(1 for r in recs if r.reorder_flag)
        assert reorder == 6

    def test_first_item_reorder(self, pipe_pack_values):
        """First PACK item has closing_stock=0, so reorder should be True."""
        recs = parsers.parse_material_stock(pipe_pack_values, "PIPE", "PACK")
        r = recs[0]
        assert r.closing_stock == 0.0
        assert r.reorder_flag is True


# ---------------------------------------------------------------------------
# C4 — PTMT Report-2 (BOP, 53 items)
# ---------------------------------------------------------------------------

class TestPtmtMaterialBOP:

    def test_item_count(self, ptmt_bop_values):
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        assert len(recs) == 53

    def test_plant_and_category(self, ptmt_bop_values):
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        assert all(r.plant == "PTMT" and r.category == "BOP" for r in recs)

    def test_first_item_code(self, ptmt_bop_values):
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        assert recs[0].item_code == "PBR36"

    def test_no_stock_days_sheet_for_ptmt(self, ptmt_bop_values):
        """PTMT has no pre-computed 'Stock Days' column — all stock_days_sheet must be None."""
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        assert all(r.stock_days_sheet is None for r in recs)

    def test_reorder_count(self, ptmt_bop_values):
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        reorder = sum(1 for r in recs if r.reorder_flag)
        assert reorder == 22

    def test_reorder_flag_logic(self, ptmt_bop_values):
        recs = parsers.parse_material_stock(ptmt_bop_values, "PTMT", "BOP")
        for r in recs:
            if r.days_of_cover is not None and r.lead_time_days > 0:
                assert r.reorder_flag == (r.days_of_cover <= r.lead_time_days)


# ---------------------------------------------------------------------------
# C5 — PTMT Report-3 (Packaging, 32 items)
# ---------------------------------------------------------------------------

class TestPtmtMaterialPACK:

    def test_item_count(self, ptmt_pack_values):
        recs = parsers.parse_material_stock(ptmt_pack_values, "PTMT", "PACK")
        assert len(recs) == 32

    def test_plant_and_category(self, ptmt_pack_values):
        recs = parsers.parse_material_stock(ptmt_pack_values, "PTMT", "PACK")
        assert all(r.plant == "PTMT" and r.category == "PACK" for r in recs)

    def test_first_item_code(self, ptmt_pack_values):
        recs = parsers.parse_material_stock(ptmt_pack_values, "PTMT", "PACK")
        assert recs[0].item_code == "PBP170"

    def test_no_stock_days_sheet_for_ptmt(self, ptmt_pack_values):
        recs = parsers.parse_material_stock(ptmt_pack_values, "PTMT", "PACK")
        assert all(r.stock_days_sheet is None for r in recs)

    def test_reorder_count(self, ptmt_pack_values):
        recs = parsers.parse_material_stock(ptmt_pack_values, "PTMT", "PACK")
        reorder = sum(1 for r in recs if r.reorder_flag)
        assert reorder == 19


# ---------------------------------------------------------------------------
# C6 — PTMT Report-4 (RM, 21 items; uses "CODE" not "ITEM CODE")
# ---------------------------------------------------------------------------

class TestPtmtMaterialRM:

    def test_item_count(self, ptmt_rm_values):
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        assert len(recs) == 21

    def test_plant_and_category(self, ptmt_rm_values):
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        assert all(r.plant == "PTMT" and r.category == "RM" for r in recs)

    def test_first_item_code(self, ptmt_rm_values):
        """Report-4 uses 'CODE' header — parser must handle both 'ITEM CODE' and 'CODE'."""
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        assert recs[0].item_code == "RMG5"

    def test_no_stock_days_sheet_for_ptmt(self, ptmt_rm_values):
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        assert all(r.stock_days_sheet is None for r in recs)

    def test_reorder_count(self, ptmt_rm_values):
        """PTMT RM has no reorder items in June 2026."""
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        reorder = sum(1 for r in recs if r.reorder_flag)
        assert reorder == 0

    def test_first_item_closing_and_cover(self, ptmt_rm_values):
        recs = parsers.parse_material_stock(ptmt_rm_values, "PTMT", "RM")
        r = recs[0]
        assert r.closing_stock == pytest.approx(18200.0, abs=1.0)
        # Cover should be positive (large closing stock)
        if r.days_of_cover is not None:
            assert r.days_of_cover > 0


# ---------------------------------------------------------------------------
# C7 — compute_material_metrics unit tests
# ---------------------------------------------------------------------------

class TestComputeMaterialMetrics:

    def _make(self, **kw):
        defaults = dict(
            plant="PIPE", category="RM", item_code="X1", item_name="Item X",
            item_type="", avg_price=100.0, avg_consumption_month=300.0,
            ideal_stock=400.0, min_batch=100.0, lead_time_days=15.0,
            opening_stock=50.0, closing_stock=200.0, purchase_till=100.0,
            consumption_till=250.0, stock_days_sheet=None, days_of_cover=None,
            as_of_date="",
        )
        defaults.update(kw)
        return planning.MaterialRecord(**defaults)

    def test_days_of_cover_computed(self):
        r = self._make(closing_stock=150.0, avg_consumption_month=300.0)
        planning.compute_material_metrics(r)
        # 150 / (300/30) = 150 / 10 = 15
        assert r.days_of_cover == pytest.approx(15.0, rel=1e-3)

    def test_days_of_cover_none_when_zero_consumption(self):
        r = self._make(avg_consumption_month=0.0)
        planning.compute_material_metrics(r)
        assert r.days_of_cover is None

    def test_reorder_true_when_cover_lte_lead(self):
        r = self._make(closing_stock=100.0, avg_consumption_month=300.0, lead_time_days=15.0)
        planning.compute_material_metrics(r)
        # cover = 100 / 10 = 10 <= 15 → reorder
        assert r.reorder_flag is True

    def test_reorder_false_when_cover_gt_lead(self):
        r = self._make(closing_stock=600.0, avg_consumption_month=300.0, lead_time_days=15.0)
        planning.compute_material_metrics(r)
        # cover = 600 / 10 = 60 > 15 → no reorder
        assert r.reorder_flag is False

    def test_suggested_purchase_on_reorder(self):
        r = self._make(closing_stock=50.0, ideal_stock=400.0, min_batch=100.0,
                       avg_consumption_month=300.0, lead_time_days=15.0)
        planning.compute_material_metrics(r)
        # cover = 50/10 = 5 <= 15 → reorder; shortfall=350, min_batch=100 → suggested=350
        assert r.reorder_flag is True
        assert r.suggested_purchase == pytest.approx(350.0)

    def test_suggested_purchase_uses_min_batch_when_shortfall_smaller(self):
        r = self._make(closing_stock=50.0, ideal_stock=100.0, min_batch=500.0,
                       avg_consumption_month=300.0, lead_time_days=15.0)
        planning.compute_material_metrics(r)
        # shortfall=50, min_batch=500 → suggested=500
        assert r.suggested_purchase == pytest.approx(500.0)

    def test_no_suggested_purchase_when_no_reorder(self):
        r = self._make(closing_stock=600.0, avg_consumption_month=300.0, lead_time_days=15.0)
        planning.compute_material_metrics(r)
        assert r.suggested_purchase == 0.0

    def test_reorder_false_when_lead_time_zero(self):
        """No reorder flag when lead_time is unknown (0)."""
        r = self._make(closing_stock=0.0, avg_consumption_month=300.0, lead_time_days=0.0)
        planning.compute_material_metrics(r)
        assert r.reorder_flag is False
