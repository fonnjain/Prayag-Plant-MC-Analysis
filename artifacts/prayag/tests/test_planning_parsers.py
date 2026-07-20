"""
Fixture-backed tests for the planning parsers.

All tests run offline against the captured June 2026 fixtures — no network.
Acceptance totals come from live sheet header rows probed during implementation.
"""
import json
import pytest
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import parsers
import planning


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def pipe_cpvc_values():
    return json.loads((FIXTURES / "pipe_report1_cpvc_2026_06.json").read_text())


@pytest.fixture(scope="module")
def ptmt_r7_values():
    return json.loads((FIXTURES / "ptmt_report7_2026_06.json").read_text())


@pytest.fixture(scope="module")
def ptmt_master_values():
    return json.loads((FIXTURES / "ptmt_master_2026_06.json").read_text())


# ---------------------------------------------------------------------------
# B1 — parse_pipe_report1 (CPVC)
# ---------------------------------------------------------------------------

class TestParsePipeReport1:

    def test_item_count(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        assert len(recs) == 75

    def test_first_item_code(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        # First data item after the "CPVC PIPE" section title
        assert recs[0].item_code == "PS-2"

    def test_first_item_name(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        assert "SDR 11" in recs[0].item_name.upper() or "3/4" in recs[0].item_name

    def test_produced_figures(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        first = recs[0]
        # From acceptance spec: produced=9198, produce_required≈34516
        assert abs(first.produced - 9198) < 5
        assert first.produce_required > 30_000

    def test_plant_and_family(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        assert all(r.plant == "PIPE" for r in recs)
        assert all(r.family == "CPVC" for r in recs)

    def test_as_of_date(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        # Closing-stock date from the June 2026 snapshot
        assert "Jun" in recs[0].as_of_date and "30" in recs[0].as_of_date

    def test_net_requirement_computed(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        # net_requirement ≥ 0 for all items
        assert all(r.net_requirement >= 0 for r in recs)

    def test_days_of_cover_non_negative(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        for r in recs:
            if r.days_of_cover is not None:
                assert r.days_of_cover >= 0

    def test_no_section_title_rows(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        # "CPVC PIPE" or "CPVC FITTING" must not appear as item codes
        for r in recs:
            assert "PIPE" not in r.item_code.upper() or len(r.item_code) < 6

    def test_category_populated(self, pipe_cpvc_values):
        recs = parsers.parse_pipe_report1(pipe_cpvc_values, "CPVC", "2026-06")
        # At least some items must have a non-empty category
        cats = {r.category for r in recs}
        assert any(c for c in cats)


# ---------------------------------------------------------------------------
# B2(a) — parse_ptmt_report7
# ---------------------------------------------------------------------------

class TestParsePtmtReport7:

    def test_available(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        assert res["available"] is True

    def test_data_row_count(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        # 1735 data rows in the June 2026 workbook
        assert res["n_rows"] == 1735

    def test_production_dates(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        assert res["n_dates"] == 29

    def test_total_pcs(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        # Stored header total = 7,018,009; parsed sum may differ by rounding
        assert abs(res["total_pcs"] - 7_018_009) < 200

    def test_total_kg(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        # Stored header total = 160,477.92 kg
        assert abs(res["total_kg"] - 160_477.92) < 5.0

    def test_machine_count(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        assert len(res["machines"]) >= 40

    def test_machine_pcs_positive(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        for mc, d in res["machines"].items():
            assert d["pcs"] >= 0
            assert d["kg"] >= 0

    def test_machine_pcs_sum(self, ptmt_r7_values):
        res = parsers.parse_ptmt_report7(ptmt_r7_values, "2026-06")
        machine_sum = sum(d["pcs"] for d in res["machines"].values())
        assert abs(machine_sum - res["total_pcs"]) < 1.0


# ---------------------------------------------------------------------------
# B2(b) — parse_ptmt_master
# ---------------------------------------------------------------------------

class TestParsePtmtMaster:

    def test_item_count(self, ptmt_master_values):
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        # Expected ~611-612 rows with non-empty item codes
        assert 605 <= len(stds) <= 620

    def test_first_item(self, ptmt_master_values):
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        first = stds[0]
        assert first.item_code == "PSF-190"
        assert first.item_name == "HANDLE-O"
        assert first.mould_cavity == 8
        assert abs(first.cycle_time_secs - 55.0) < 0.1
        assert abs(first.cycle_time_per_pcs - 6.88) < 0.05

    def test_theoretical_pcs_hr(self, ptmt_master_values):
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        first = stds[0]
        # PSF-190: 3600 / 6.875 ≈ 523
        assert abs(first.theoretical_pcs_hr - 523) < 5

    def test_most_have_machine(self, ptmt_master_values):
        """Some MASTER items genuinely have no machine assigned in the sheet."""
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        with_machine = [s for s in stds if s.machine_name]
        # Expect ≥90 % have a machine name
        assert len(with_machine) / len(stds) >= 0.90

    def test_all_cavities_positive(self, ptmt_master_values):
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        assert all(s.mould_cavity >= 1 for s in stds)

    def test_theoretical_rate_populated(self, ptmt_master_values):
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        with_rate = [s for s in stds if s.theoretical_pcs_hr > 0]
        assert len(with_rate) > 500

    def test_cycle_time_seconds(self, ptmt_master_values):
        """Cycle time must be in seconds (1–600s range), not minutes."""
        stds = parsers.parse_ptmt_master(ptmt_master_values)
        for s in stds:
            if s.cycle_time_secs > 0:
                # Reasonable seconds range (0.5 s to 10 min)
                assert 0.5 <= s.cycle_time_secs <= 600


# ---------------------------------------------------------------------------
# planning.compute_plan_metrics unit tests
# ---------------------------------------------------------------------------

class TestComputePlanMetrics:

    def _make(self, **kw):
        defaults = dict(
            plant="PIPE", family="CPVC", category="", item_code="X1",
            item_name="Item X", wt_kg=0.5, ideal_qty=1000.0, avg_sale_90d=900.0,
            per_hour_output=50.0, produce_required=800.0, produced=600.0,
            closing_stock=300.0, opening_stock=100.0, as_of_date="Jun, 30",
        )
        defaults.update(kw)
        return planning.PlanRecord(**defaults)

    def test_net_req_from_produce_gap(self):
        r = self._make(produce_required=800, produced=600, ideal_qty=500, closing_stock=400)
        planning.compute_plan_metrics(r)
        # max(800-600, 500-400) = max(200, 100) = 200
        assert r.net_requirement == 200.0

    def test_net_req_from_stock_gap(self):
        r = self._make(produce_required=0, produced=0, ideal_qty=1000, closing_stock=600)
        planning.compute_plan_metrics(r)
        # max(0, 1000-600) = 400
        assert r.net_requirement == 400.0

    def test_days_of_cover(self):
        r = self._make(closing_stock=90.0, avg_sale_90d=900.0)
        planning.compute_plan_metrics(r)
        # daily_sale = 900/90 = 10; days_cover = 90/10 = 9
        assert abs(r.days_of_cover - 9.0) < 0.01

    def test_no_days_cover_when_zero_avg(self):
        r = self._make(avg_sale_90d=0.0)
        planning.compute_plan_metrics(r)
        assert r.days_of_cover is None

    def test_net_req_never_negative(self):
        r = self._make(produce_required=100, produced=500, ideal_qty=200, closing_stock=600)
        planning.compute_plan_metrics(r)
        assert r.net_requirement == 0.0
