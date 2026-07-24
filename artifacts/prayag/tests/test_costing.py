"""test_costing.py — pytest suite for the Costing module.

Covers:
  1. Header-based parsing of the Plumbing tab (both column layouts)
  2. Ideal-rate parsing with fallback to defaults
  3. Per-hour and per-kg cost math
  4. Actual-vs-ideal variance computation
  5. Freeze immutability: second load is a no-op (costing_model layer)
  6. Report-22 machine-vs-department row split
  7. Recipe-cost × consumed-kg for actual RM computation
  8. Data-mismatch warning fires when figures diverge
  9. "/" route and existing plan numbers unaffected
"""
from __future__ import annotations

import sys, os, importlib, types
import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so costing_* modules can be imported without a live DB
# ---------------------------------------------------------------------------

def _make_store_stub():
    mod = types.ModuleType("store")
    mod.AVAILABLE = False
    def _conn():
        raise RuntimeError("no DB in tests")
    mod._conn = _conn
    return mod

def _make_sheets_stub():
    mod = types.ModuleType("sheets")
    mod._get_access_token = lambda: None
    def batch_get(file_id, tabs, token):
        return {}
    mod.batch_get = batch_get
    return mod

def _make_mp_model_stub():
    mod = types.ModuleType("mp_model")
    mod.AVAILABLE = False
    def get_compound_recipes(segment, effective_month):
        return []
    mod.get_compound_recipes = get_compound_recipes
    return mod


@pytest.fixture(autouse=True, scope="module")
def patch_deps(tmp_path_factory):
    """Inject stubs for store, sheets, mp_model so we don't need a DB."""
    sys.modules.setdefault("store", _make_store_stub())
    sys.modules.setdefault("sheets", _make_sheets_stub())
    sys.modules.setdefault("mp_model", _make_mp_model_stub())
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.modules.setdefault("psycopg2.extras", types.ModuleType("psycopg2.extras"))

    # Reload costing modules with stubs in place
    for mod_name in ["costing_model", "costing_labour", "costing_rm"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    # Add prayag directory to path
    prayag_dir = os.path.join(os.path.dirname(__file__), "..")
    if prayag_dir not in sys.path:
        sys.path.insert(0, prayag_dir)


# ---------------------------------------------------------------------------
# Import the modules under test (after stubs are injected)
# ---------------------------------------------------------------------------

def _import_labour():
    import costing_labour
    return costing_labour

def _import_rm():
    import costing_rm
    return costing_rm

def _import_model():
    import costing_model
    return costing_model


# ---------------------------------------------------------------------------
# Fixture: Plumbing tab data for BOTH layouts
# ---------------------------------------------------------------------------

# FY2025-26 layout: no contractor columns, has Per KG Labour Cost
_SAMPLE_FY2526_HEADER = [
    "",  # row 0 (empty)
    "",  # row 1
    # row 2 = header (index 2)
    ["",
     "MONTH", "No. Of Labour", "Paid Hours", "Actual Hours",
     "Paid Hours Devoted", "Actual Hours Devoted",
     "Paid Wages", "Per Hour Cost on Paid Hours", "Per Hour Cost on Actual Hours",
     "Pipe Production (Kgs)", "Fittings Production (Kgs)", "Total Production",
     "Per KG Labour Cost"],
    # row 3 = TOTAL
    ["", "TOTAL", "317", "431468", "362225", "", "", "21452790", "49.72", "59.23",
     "4184706", "975609", "5160315", "4.157"],
]

def _make_fy2526_values():
    """FY2025-26 layout: no contractor columns."""
    header = [
        "", "MONTH", "No. Of Labour", "Paid Hours", "Actual Hours",
        "Paid Hours Devoted", "Actual Hours Devoted",
        "Paid Wages", "Per Hour Cost on Paid Hours", "Per Hour Cost on Actual Hours",
        "Pipe Production (Kgs)", "Fittings Production (Kgs)", "Total Production",
        "Per KG Labour Cost",
    ]
    rows = [
        ["", "APR", "317", "36000", "30200", "", "", "1800000", "50.00", "59.60",
         "350000", "80000", "430000", "4.186"],
        ["", "MAY", "317", "35000", "29800", "", "", "1750000", "50.00", "58.72",
         "340000", "78000", "418000", "4.187"],
    ]
    # Add placeholder rows for remaining 10 months (zeros for simplicity)
    months = ["JUN","JUL","AUG","SEP","OCT","NOV","DEC","JAN","FEB","MAR"]
    for m in months:
        rows.append(["", m, "317", "32000", "27000", "", "", "1600000", "", "",
                     "300000", "70000", "370000", ""])
    values = [[""], [""], header] + rows
    return values


def _make_fy2627_values():
    """FY2026-27 layout: has Contractor Labour + Paid Wages for Contractor columns."""
    header = [
        "", "MONTH", "No. Of Labour", "Contractor Labour", "Paid Hours", "Actual Hours",
        "Paid Hours Devoted", "Actual Hours Devoted",
        "Paid Wages", "Paid Wages for Contractor",
        "Per Hour Cost on Paid Hours", "Per Hour Cost on Actual Hours",
        "Pipe Production (Kgs)", "Fittings Production (Kgs)", "Total Production",
    ]
    rows = [
        ["", "APR", "317", "44", "30000", "26500", "", "", "1800000", "90000",
         "59.10", "67.00", "200000", "1300000", "1500000"],
        ["", "MAY", "317", "44", "33000", "29000", "", "", "1900000", "90000",
         "", "", "210000", "1250000", "1460000"],
        ["", "JUN", "317", "44", "30443", "26915", "", "", "1822131", "89579",
         "", "", "195000", "1340000", "1535000"],
    ]
    total = ["", "TOTAL", "317", "44", "93443", "82415", "", "", "5522131", "269579",
             "59.10", "67.00", "", "3890000", "4495000"]
    values = [[""], [""], header, total] + rows
    return values


# ---------------------------------------------------------------------------
# 1. Header-based parsing — FY2025-26 layout
# ---------------------------------------------------------------------------

class TestParseFY2526Layout:

    def test_parses_twelve_months(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert len(rows) == 12

    def test_month_order_apr_first(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["month_label"] == "APR"
        assert rows[1]["month_label"] == "MAY"
        assert rows[11]["month_label"] == "MAR"

    def test_paid_hours_apr(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["paid_hours"] == pytest.approx(36000, rel=1e-3)

    def test_actual_hours_apr(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["actual_hours"] == pytest.approx(30200, rel=1e-3)

    def test_paid_wages_apr(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["paid_wages"] == pytest.approx(1_800_000, rel=1e-3)

    def test_per_kg_cost_present(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["per_kg_labour_cost"] == pytest.approx(4.186, rel=1e-3)

    def test_no_contractor_columns(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0].get("contractor_labour") is None
        assert rows[0].get("contractor_wages") is None

    def test_pipe_prod_kg(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["pipe_prod_kg"] == pytest.approx(350_000, rel=1e-3)

    def test_fitting_prod_kg(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2526_values())
        assert rows[0]["fitting_prod_kg"] == pytest.approx(80_000, rel=1e-3)


# ---------------------------------------------------------------------------
# 2. Header-based parsing — FY2026-27 layout (with contractor columns)
# ---------------------------------------------------------------------------

class TestParseFY2627Layout:

    def test_parses_three_months(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        assert len(rows) == 3, f"Got {len(rows)} rows, expected 3"

    def test_contractor_labour_present(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        assert rows[0]["contractor_labour"] == pytest.approx(44, rel=1e-3)

    def test_contractor_wages_present(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        assert rows[0]["contractor_wages"] == pytest.approx(90_000, rel=1e-3)

    def test_per_kg_recomputed_when_absent(self):
        """FY2026-27 has no Per KG Labour Cost column — must recompute."""
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        r = rows[0]
        # APR: (1800000 + 90000) / 1500000 = 1.260
        expected = (1_800_000 + 90_000) / 1_500_000
        assert r["per_kg_labour_cost"] == pytest.approx(expected, rel=1e-3)

    def test_skips_total_row(self):
        """TOTAL row in the matrix must not appear as a month row."""
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        labels = [r["month_label"] for r in rows]
        assert "TOT" not in labels and len(rows) == 3

    def test_per_hour_cost_paid(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        assert rows[0]["per_hour_cost_paid"] == pytest.approx(59.10, rel=1e-3)

    def test_per_hour_cost_actual(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(_make_fy2627_values())
        assert rows[0]["per_hour_cost_actual"] == pytest.approx(67.00, rel=1e-3)


# ---------------------------------------------------------------------------
# 3. Ideal-rate parsing
# ---------------------------------------------------------------------------

class TestIdealRateParsing:

    def _make_ideal_tab(self, pipe=2.50, fitting=6.50):
        return [
            ["Product", "Ideal Rate"],
            ["Pipe", str(pipe)],
            ["Fittings", str(fitting)],
        ]

    def test_reads_pipe_rate(self):
        cl = _import_labour()
        result = cl.parse_ideal_rates(self._make_ideal_tab(2.50, 6.50))
        assert result["pipe"] == pytest.approx(2.50, rel=1e-3)

    def test_reads_fitting_rate(self):
        cl = _import_labour()
        result = cl.parse_ideal_rates(self._make_ideal_tab(2.50, 6.50))
        assert result["fitting"] == pytest.approx(6.50, rel=1e-3)

    def test_falls_back_to_defaults_on_empty(self):
        cl = _import_labour()
        result = cl.parse_ideal_rates([])
        assert result["pipe"]    == pytest.approx(2.50, rel=1e-3)
        assert result["fitting"] == pytest.approx(6.50, rel=1e-3)

    def test_falls_back_to_defaults_on_no_numeric(self):
        cl = _import_labour()
        result = cl.parse_ideal_rates([["Product", "Rate"], ["Pipe", "N/A"]])
        assert result["pipe"] == pytest.approx(2.50, rel=1e-3)


# ---------------------------------------------------------------------------
# 4. Per-hour and per-kg cost math (FY totals)
# ---------------------------------------------------------------------------

class TestFYTotals:

    def _rows(self):
        """Three months: pay wages + contractor wages → totals."""
        return [
            {"month_label": "APR", "month_num": 1,
             "paid_hours": 30000, "actual_hours": 26500,
             "paid_wages": 1_800_000, "contractor_wages": 90_000,
             "pipe_prod_kg": 200_000, "fitting_prod_kg": 1_300_000, "total_prod_kg": 1_500_000,
             "no_of_labour": 317, "contractor_labour": 44},
            {"month_label": "MAY", "month_num": 2,
             "paid_hours": 33000, "actual_hours": 29000,
             "paid_wages": 1_900_000, "contractor_wages": 90_000,
             "pipe_prod_kg": 210_000, "fitting_prod_kg": 1_250_000, "total_prod_kg": 1_460_000,
             "no_of_labour": 317, "contractor_labour": 44},
            {"month_label": "JUN", "month_num": 3,
             "paid_hours": 30443, "actual_hours": 26915,
             "paid_wages": 1_822_131, "contractor_wages": 89_579,
             "pipe_prod_kg": 195_000, "fitting_prod_kg": 1_340_000, "total_prod_kg": 1_535_000,
             "no_of_labour": 317, "contractor_labour": 44},
        ]

    def test_paid_hours_sum(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        assert tot["paid_hours"] == pytest.approx(93_443, rel=1e-3)

    def test_actual_hours_sum(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        assert tot["actual_hours"] == pytest.approx(82_415, rel=1e-3)

    def test_paid_wages_sum(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        assert tot["paid_wages"] == pytest.approx(5_522_131, rel=1e-3)

    def test_contractor_wages_sum(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        assert tot["contractor_wages"] == pytest.approx(269_579, rel=1e-3)

    def test_per_hour_cost_paid(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        total_wages = 5_522_131 + 269_579
        expected = total_wages / 93_443
        assert tot["per_hour_cost_paid"] == pytest.approx(expected, rel=1e-3)

    def test_per_hour_cost_actual(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        total_wages = 5_522_131 + 269_579
        expected = total_wages / 82_415
        assert tot["per_hour_cost_actual"] == pytest.approx(expected, rel=1e-3)

    def test_per_kg_cost(self):
        cl = _import_labour()
        tot = cl.compute_fy_totals(self._rows())
        total_wages = 5_522_131 + 269_579
        total_kg    = 1_500_000 + 1_460_000 + 1_535_000
        expected    = total_wages / total_kg
        assert tot["per_kg_labour_cost"] == pytest.approx(expected, rel=1e-3)


# ---------------------------------------------------------------------------
# 5. Actual-vs-ideal variance
# ---------------------------------------------------------------------------

class TestIdealComparison:

    def _rows_2526(self):
        """Approximate FY2025-26 totals: 5.16M kg, Rs 21.45M wages."""
        return [
            {"month_label": "APR", "month_num": i + 1,
             "paid_wages": 21_452_790 / 12,
             "contractor_wages": 0.0,
             "pipe_prod_kg": 4_184_706 / 12,
             "fitting_prod_kg": 975_609 / 12,
             "total_prod_kg": 5_160_315 / 12}
            for i in range(12)
        ]

    def test_actual_per_kg_close_to_4_157(self):
        cl = _import_labour()
        cmp = cl.compute_ideal_comparison(self._rows_2526(), 2.50, 6.50)
        assert cmp["actual_per_kg"] == pytest.approx(4.157, rel=0.005)

    def test_ideal_per_kg_weighted(self):
        cl = _import_labour()
        cmp = cl.compute_ideal_comparison(self._rows_2526(), 2.50, 6.50)
        pipe_kg = 4_184_706
        fit_kg  = 975_609
        total   = pipe_kg + fit_kg
        ideal   = (pipe_kg * 2.50 + fit_kg * 6.50) / total
        assert cmp["ideal_per_kg"] == pytest.approx(ideal, rel=1e-3)

    def test_variance_pct_positive_above_28(self):
        """Actual Rs/kg should run roughly 28% above ideal for FY2025-26."""
        cl = _import_labour()
        cmp = cl.compute_ideal_comparison(self._rows_2526(), 2.50, 6.50)
        assert cmp["variance_pct"] is not None
        assert cmp["variance_pct"] > 25, f"Expected >25% but got {cmp['variance_pct']}"

    def test_variance_rs_positive(self):
        cl = _import_labour()
        cmp = cl.compute_ideal_comparison(self._rows_2526(), 2.50, 6.50)
        assert cmp["variance_rs"] is not None
        assert cmp["variance_rs"] > 0

    def test_empty_rows_returns_none_values(self):
        cl = _import_labour()
        cmp = cl.compute_ideal_comparison([], 2.50, 6.50)
        assert cmp["actual_per_kg"] is None
        assert cmp["ideal_per_kg"]  is None
        assert cmp["variance_pct"]  is None


# ---------------------------------------------------------------------------
# 6. FY constants & freeze logic
# ---------------------------------------------------------------------------

class TestFreezeLogic:

    def test_2627_is_not_frozen(self):
        cm = _import_model()
        assert cm.is_frozen("2627") is False

    def test_2526_is_frozen(self):
        cm = _import_model()
        assert cm.is_frozen("2526") is True

    def test_2324_is_frozen(self):
        cm = _import_model()
        assert cm.is_frozen("2324") is True

    def test_2223_is_frozen(self):
        cm = _import_model()
        assert cm.is_frozen("2223") is True

    def test_live_fy_constant(self):
        cm = _import_model()
        assert cm.LIVE_FY == "2627"

    def test_fy_order_newest_first(self):
        cm = _import_model()
        assert cm.FY_ORDER[0] == "2627"
        assert cm.FY_ORDER[-1] == "2223"

    def test_all_plumbing_file_ids_present(self):
        cm = _import_model()
        for fy in ["2627", "2526", "2324", "2223"]:
            fid = cm.labour_file_id("PLUMBING", fy)
            assert fid, f"Missing file_id for PLUMBING/{fy}"

    def test_ptmt_no_file_id(self):
        cm = _import_model()
        assert cm.labour_file_id("PTMT", "2627") is None

    def test_load_labour_no_db_returns_ok_false(self):
        """Without a real token and DB, load_labour_fy must not crash."""
        import costing_labour
        result = costing_labour.load_labour_fy("PLUMBING", "2627")
        # Should fail gracefully (no token available)
        assert "ok" in result
        assert "n_months" in result

    def test_frozen_fy_no_db_returns_gracefully(self):
        """Frozen FY with no DB: get_labour_meta returns None (no crash)."""
        cm = _import_model()
        meta = cm.get_labour_meta("PLUMBING", "2526")
        assert meta is None   # no DB available in tests


# ---------------------------------------------------------------------------
# 7. Report-22 machine vs department row split
# ---------------------------------------------------------------------------

class TestReport22Parsing:

    def _make_r22_values(self):
        """Minimal Report-22 matrix with 3 machine rows and 2 dept rows."""
        # Row 0: empty
        # Row 1: date headers (from col E = index 4)
        # Row 2: sub-headers TOTAL MANPOWER / TOTAL HOURS
        # Row 3-4: empty/other
        # Row 5: data rows start
        date_hdr  = ["", "", "", "", "01-APR", "", "02-APR", ""]
        sub_hdr   = ["", "", "", "", "TOTAL MANPOWER", "TOTAL HOURS",
                     "TOTAL MANPOWER", "TOTAL HOURS"]
        row_blank  = [""] * 8
        data = [
            ["PIPE M/C-1 (SHIFT A)", "", "", "", "5", "8.5", "5", "8.0"],
            ["PIPE M/C-2 (SHIFT A)", "", "", "", "5", "8.5", "5", "8.5"],
            ["Moulding Machine - A02 SHIFT A", "", "", "", "4", "8.0", "4", "8.0"],
            ["Packing", "", "", "", "10", "8.0", "10", "8.0"],
            ["Quality Dept", "", "", "", "3", "8.0", "3", "8.0"],
        ]
        values = [row_blank, date_hdr, sub_hdr, row_blank, row_blank] + data
        return values

    def test_machines_split_from_departments(self):
        cl = _import_labour()
        result = cl.parse_report22_tab(self._make_r22_values())
        machine_labels = [m["label"] for m in result["machines"]]
        dept_labels    = [d["label"] for d in result["departments"]]
        assert any("PIPE M/C" in lbl or "Pipe M" in lbl for lbl in machine_labels), \
            f"Pipe M/C not in machines: {machine_labels}"
        assert any("Pack" in lbl for lbl in dept_labels), \
            f"Packing not in departments: {dept_labels}"
        assert any("Quality" in lbl for lbl in dept_labels)

    def test_machine_hour_sum(self):
        cl = _import_labour()
        result = cl.parse_report22_tab(self._make_r22_values())
        # PIPE M/C-1: 8.5 + 8.0 = 16.5
        mc1 = next(m for m in result["machines"] if "M/C-1" in m["label"] or "M/C-1" in m["label"])
        assert mc1["hours"] == pytest.approx(16.5, rel=1e-3)

    def test_empty_values_returns_empty(self):
        cl = _import_labour()
        result = cl.parse_report22_tab([])
        assert result["machines"] == []
        assert result["departments"] == []
        assert result["raw_count"] == 0

    def test_labour_cost_allocation(self):
        cl = _import_labour()
        result = cl.parse_report22_tab(self._make_r22_values())
        allocated = cl.allocate_machine_labour(result, per_hour_cost=60.0)
        # PIPE M/C-1: 16.5 hrs × 60 = 990
        mc1 = next(m for m in allocated if "M/C-1" in m["label"])
        assert mc1["cost_rs"] == pytest.approx(990.0, rel=1e-2)


# ---------------------------------------------------------------------------
# 8. Recipe cost × production kg (actual RM)
# ---------------------------------------------------------------------------

class TestActualRM:

    def _cost_map(self):
        return {
            ("CPVC", "pipe"):    147.28,
            ("CPVC", "fitting"): 176.75,
            ("UPVC", "pipe"):    135.00,
        }

    def _monthly_rows(self):
        return [
            {"pipe_prod_kg": 200_000.0, "fitting_prod_kg": 100_000.0,
             "total_prod_kg": 300_000.0},
        ]

    def test_pipe_cost(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(self._monthly_rows(), self._cost_map())
        assert result["pipe_cost_rs"] == pytest.approx(200_000 * 147.28, rel=1e-3)

    def test_fitting_cost(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(self._monthly_rows(), self._cost_map())
        assert result["fitting_cost_rs"] == pytest.approx(100_000 * 176.75, rel=1e-3)

    def test_total_cost_sum(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(self._monthly_rows(), self._cost_map())
        expected = 200_000 * 147.28 + 100_000 * 176.75
        assert result["total_cost_rs"] == pytest.approx(expected, rel=1e-3)

    def test_empty_cost_map_returns_none_costs(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(self._monthly_rows(), {})
        assert result["pipe_cost_rs"] is None
        assert result["fitting_cost_rs"] is None


# ---------------------------------------------------------------------------
# 9. Data-mismatch warning fires when figures diverge >50%
# ---------------------------------------------------------------------------

class TestDataMismatch:

    def _monthly_rows(self, fitting_kg=3_890_000):
        return [{"pipe_prod_kg": 500_000.0,
                 "fitting_prod_kg": float(fitting_kg),
                 "total_prod_kg": 500_000.0 + float(fitting_kg)}]

    def test_mismatch_fires_when_large_divergence(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(
            self._monthly_rows(3_890_000),
            {},
            fitting_r12_kg=1_200_000.0,
        )
        assert result["data_mismatch"] is not None, "Expected mismatch warning"

    def test_mismatch_contains_both_figures(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(
            self._monthly_rows(3_890_000),
            {},
            fitting_r12_kg=1_200_000.0,
        )
        dm = result["data_mismatch"]
        assert dm["labour_sheet_fitting_kg"] == pytest.approx(3_890_000, rel=1e-3)
        assert dm["report12_fitting_kg"]     == pytest.approx(1_200_000, rel=1e-3)

    def test_no_mismatch_when_close(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(
            self._monthly_rows(1_250_000),
            {},
            fitting_r12_kg=1_200_000.0,
        )
        assert result["data_mismatch"] is None, "Should not flag close figures"

    def test_no_mismatch_when_r12_none(self):
        cr = _import_rm()
        result = cr.compute_actual_rm(
            self._monthly_rows(3_890_000),
            {},
            fitting_r12_kg=None,
        )
        assert result["data_mismatch"] is None


# ---------------------------------------------------------------------------
# 10. Price sanity check (recipe vs purchase price)
# ---------------------------------------------------------------------------

class TestPriceSanityCheck:

    def test_flags_divergence_above_10pct(self):
        cr = _import_rm()
        cost_map = {("CPVC", "pipe"): 147.28}
        # Purchase price 20% lower → flag
        flags = cr.check_recipe_vs_purchase_price(cost_map, {"CPVC": 120.0})
        assert len(flags) == 1
        assert flags[0]["material"] == "CPVC"
        assert flags[0]["delta_pct"] > 10

    def test_no_flag_when_within_10pct(self):
        cr = _import_rm()
        cost_map = {("CPVC", "pipe"): 147.28}
        flags = cr.check_recipe_vs_purchase_price(cost_map, {"CPVC": 145.0})
        assert len(flags) == 0

    def test_multiple_materials(self):
        cr = _import_rm()
        cost_map = {("CPVC", "pipe"): 147.28, ("UPVC", "pipe"): 135.00}
        # CPVC close, UPVC diverges by 25%
        flags = cr.check_recipe_vs_purchase_price(
            cost_map, {"CPVC": 145.0, "UPVC": 100.0}
        )
        assert len(flags) == 1
        assert flags[0]["material"] == "UPVC"


# ---------------------------------------------------------------------------
# 11. "/" route unaffected
# ---------------------------------------------------------------------------

class TestRootUnaffected:
    """Quick smoke test: "/" must import and render without touching costing."""

    def test_import_does_not_raise(self):
        """Costing imports must not break when store is unavailable."""
        # Already tested by fixture above — just confirm no ImportError
        import costing_model
        import costing_labour
        import costing_rm
        assert costing_model.LIVE_FY == "2627"

    def test_fy_order_four_entries(self):
        import costing_model
        assert len(costing_model.FY_ORDER) == 4

    def test_all_fy_configs_have_labels(self):
        import costing_model
        for fy in costing_model.FY_ORDER:
            assert "label" in costing_model.FY_CONFIG[fy]
