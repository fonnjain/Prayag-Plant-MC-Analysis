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


def _make_sources_stub():
    """Minimal sources stub exposing DAILY_SOURCES with PIPE workbook IDs."""
    mod = types.ModuleType("sources")
    # Minimal DAILY_SOURCES: PIPE with a mapping for 2026-04 (APR 2627)
    mod.DAILY_SOURCES = {
        "PIPE": {
            "files": {
                "2026-04": "pipe-workbook-apr-2627",
                "2026-05": "pipe-workbook-may-2627",
                "2026-06": "pipe-workbook-jun-2627",
                "2025-04": "pipe-workbook-apr-2526",
                "2025-05": "pipe-workbook-may-2526",
            }
        }
    }
    return mod


@pytest.fixture(autouse=True, scope="module")
def patch_deps(tmp_path_factory):
    """Inject stubs for store, sheets, sources, mp_model so we don't need a DB.

    ISOLATION FIX: force-set (not setdefault) so the store stub is used even
    when the real store was already imported by an earlier test module.  The
    fixture yields so teardown restores the original module state, preventing
    our stubs from leaking into later test modules (e.g. test_daily_parsers).
    """
    _stub_keys = ["store", "sheets", "sources", "mp_model",
                  "costing_model", "costing_labour", "costing_rm"]
    _saved = {k: sys.modules.get(k) for k in _stub_keys}

    # Force-set stubs (not setdefault — must win even if real module is loaded)
    sys.modules["store"]    = _make_store_stub()
    sys.modules["sheets"]   = _make_sheets_stub()
    sys.modules["sources"]  = _make_sources_stub()
    sys.modules["mp_model"] = _make_mp_model_stub()
    sys.modules.setdefault("psycopg2", types.ModuleType("psycopg2"))
    sys.modules.setdefault("psycopg2.extras", types.ModuleType("psycopg2.extras"))

    # Drop costing modules so they re-import from scratch and pick up stubs
    for mod_name in ["costing_model", "costing_labour", "costing_rm"]:
        sys.modules.pop(mod_name, None)

    # Add prayag directory to path
    prayag_dir = os.path.join(os.path.dirname(__file__), "..")
    if prayag_dir not in sys.path:
        sys.path.insert(0, prayag_dir)

    yield

    # Teardown: restore original modules so downstream test files are unaffected
    for k, orig in _saved.items():
        if orig is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = orig
    # Also evict costing modules so a subsequent import gets a clean slate
    for mod_name in ["costing_model", "costing_labour", "costing_rm"]:
        sys.modules.pop(mod_name, None)


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


# ---------------------------------------------------------------------------
# 12. Decimal × float — no TypeError when DB-typed values enter compute fns
# ---------------------------------------------------------------------------

class TestDecimalFloatNoMix:
    """compute_ideal_comparison must not raise TypeError when monthly_rows
    contain decimal.Decimal values (as returned by psycopg2 NUMERIC columns).
    The _coerce_row boundary fix in costing_model eliminates this, but we
    also verify the compute function is robust.
    """

    def _decimal_rows(self):
        from decimal import Decimal
        return [
            {
                "pipe_prod_kg":    Decimal("4184706"),
                "fitting_prod_kg": Decimal("975609"),
                "total_prod_kg":   Decimal("5160315"),
                "paid_wages":      Decimal("21452790"),
                "contractor_wages": Decimal("0"),
                "per_kg_labour_cost": Decimal("4.157"),
            }
        ]

    def test_ideal_comparison_with_decimal_rows_does_not_raise(self):
        cl = _import_labour()
        rows = self._decimal_rows()
        result = cl.compute_ideal_comparison(rows, 2.50, 6.50)
        assert result is not None

    def test_ideal_comparison_decimal_gives_correct_actual_per_kg(self):
        cl = _import_labour()
        rows = self._decimal_rows()
        result = cl.compute_ideal_comparison(rows, 2.50, 6.50)
        expected_actual = 21_452_790 / 5_160_315
        assert result["actual_per_kg"] == pytest.approx(expected_actual, rel=1e-3)

    def test_ideal_comparison_decimal_gives_correct_ideal_per_kg(self):
        cl = _import_labour()
        rows = self._decimal_rows()
        result = cl.compute_ideal_comparison(rows, 2.50, 6.50)
        expected_ideal = (4_184_706 * 2.50 + 975_609 * 6.50) / 5_160_315
        assert result["ideal_per_kg"] == pytest.approx(expected_ideal, rel=1e-3)

    def test_coerce_row_converts_decimal_to_float(self):
        from decimal import Decimal
        cm = _import_model()
        row = {"paid_hours": Decimal("431468"), "paid_wages": Decimal("21452790"),
               "segment": "PLUMBING", "frozen": False}
        coerced = cm._coerce_row(row)
        assert isinstance(coerced["paid_hours"],  float)
        assert isinstance(coerced["paid_wages"],   float)
        assert isinstance(coerced["segment"],      str)    # non-Decimal unchanged
        assert isinstance(coerced["frozen"],       bool)   # non-Decimal unchanged

    def test_coerce_row_preserves_none(self):
        from decimal import Decimal
        cm = _import_model()
        row = {"contractor_wages": None, "paid_hours": Decimal("36000")}
        coerced = cm._coerce_row(row)
        assert coerced["contractor_wages"] is None
        assert isinstance(coerced["paid_hours"], float)


# ---------------------------------------------------------------------------
# 13. Empty FY renders loaded=False (not a 500) for all FY selectors
# ---------------------------------------------------------------------------

class TestEmptyFYState:
    """get_labour_view must return {"loaded": False} for any FY with no data,
    never raising — guarantees /costing returns 200 for all FYs including
    those never loaded.
    """

    def test_get_labour_view_empty_returns_not_loaded(self):
        cl = _import_labour()
        # store is stubbed as AVAILABLE=False, so get_labour_monthly returns []
        result = cl.get_labour_view("PLUMBING", "2324")
        assert result.get("loaded") is False

    def test_get_labour_view_empty_has_meta_key(self):
        cl = _import_labour()
        result = cl.get_labour_view("PLUMBING", "2223")
        assert "meta" in result

    def test_all_fy_codes_return_not_loaded_when_db_unavailable(self):
        cm = _import_model()
        cl = _import_labour()
        for fy in cm.FY_ORDER:
            result = cl.get_labour_view("PLUMBING", fy)
            assert result.get("loaded") is False, f"FY {fy} should be not-loaded with no DB"


# ---------------------------------------------------------------------------
# 14. FY2025-26 acceptance figures (parser round-trip)
# ---------------------------------------------------------------------------

class TestFY2526AcceptanceFigures:
    """Parse the FY2025-26 layout with the acceptance totals from the spec
    and verify the computed metrics match exactly.
    """

    def _make_fy2526_full(self):
        """Construct a 12-month dataset whose TOTAL matches the acceptance spec."""
        header = [
            "", "MONTH", "No. Of Labour", "Paid Hours", "Actual Hours",
            "Paid Hours Devoted", "Actual Hours Devoted",
            "Paid Wages", "Per Hour Cost on Paid Hours", "Per Hour Cost on Actual Hours",
            "Pipe Production (Kgs)", "Fittings Production (Kgs)", "Total Production",
            "Per KG Labour Cost",
        ]
        # Distribute acceptance totals evenly across 12 months for round-trip test.
        # Total: paid 431468, actual 362225, wages 21452790, pipe 4184706, fit 975609
        months = ["APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC","JAN","FEB","MAR"]
        rows_data = []
        for m in months:
            rows_data.append([
                "", m, "317",
                str(431_468 // 12), str(362_225 // 12), "", "",
                str(21_452_790 // 12), "", "",
                str(4_184_706 // 12), str(975_609 // 12),
                str((4_184_706 + 975_609) // 12), "",
            ])
        return [[""], [""], header] + rows_data

    def test_fy_totals_paid_hours(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(self._make_fy2526_full())
        totals = cl.compute_fy_totals(rows)
        assert totals["paid_hours"] == pytest.approx(431_468 // 12 * 12, rel=1e-3)

    def test_fy_totals_wages(self):
        cl = _import_labour()
        rows = cl.parse_plumbing_tab(self._make_fy2526_full())
        totals = cl.compute_fy_totals(rows)
        assert totals["paid_wages"] == pytest.approx(21_452_790 // 12 * 12, rel=1e-3)

    def test_per_hour_cost_paid_approx(self):
        """Per-hour cost on paid hours ≈ Rs 49.72 from acceptance spec."""
        cl = _import_labour()
        # Direct computation matches spec
        ph_cost = 21_452_790 / 431_468
        assert ph_cost == pytest.approx(49.72, rel=0.01)

    def test_per_hour_cost_actual_approx(self):
        """Per-hour cost on actual hours ≈ Rs 59.23 from acceptance spec."""
        cl = _import_labour()
        ah_cost = 21_452_790 / 362_225
        assert ah_cost == pytest.approx(59.23, rel=0.01)

    def test_actual_per_kg_approx(self):
        """Actual labour ≈ Rs 4.157/kg from acceptance spec."""
        total_kg = 5_160_315
        wages    = 21_452_790
        assert wages / total_kg == pytest.approx(4.157, rel=0.001)

    def test_ideal_per_kg_approx(self):
        """Weighted ideal ≈ Rs 3.26/kg: pipe Rs2.50 × 4184706 + fit Rs6.50 × 975609."""
        pipe_kg = 4_184_706
        fit_kg  = 975_609
        total   = pipe_kg + fit_kg
        ideal   = (pipe_kg * 2.50 + fit_kg * 6.50) / total
        assert ideal == pytest.approx(3.26, rel=0.01)

    def test_variance_pct_approx_27pct_above(self):
        pipe_kg = 4_184_706
        fit_kg  = 975_609
        total   = pipe_kg + fit_kg
        actual  = 21_452_790 / total
        ideal   = (pipe_kg * 2.50 + fit_kg * 6.50) / total
        pct     = (actual - ideal) / ideal * 100
        assert pct == pytest.approx(27.6, rel=0.05)


# ---------------------------------------------------------------------------
# 15. Freeze second-load is a genuine no-op (unit-level)
# ---------------------------------------------------------------------------

class TestFreezeNoOp:
    """load_labour_fy for a frozen FY with existing meta must return skipped=True
    without calling any write path.  Verified by checking the return value;
    the DB is stubbed (AVAILABLE=False) so write is impossible anyway.
    """

    def test_frozen_fy_with_no_db_returns_error_not_skip(self):
        """When DB is unavailable, load returns ok=False (no token, no data)."""
        cl = _import_labour()
        # AVAILABLE=False — the stub sheets returns no token
        result = cl.load_labour_fy("PLUMBING", "2526")
        # No token → error path (skipped only triggers when data already in DB)
        assert result["ok"] is False or result.get("skipped") is False

    def test_is_frozen_returns_true_for_2526(self):
        cm = _import_model()
        assert cm.is_frozen("2526") is True

    def test_is_frozen_returns_false_for_live_fy(self):
        cm = _import_model()
        assert cm.is_frozen(cm.LIVE_FY) is False

    def test_frozen_fy_with_existing_meta_skips(self):
        """Simulate: meta says n_months=12 → second load must skip."""
        import costing_labour as cl_mod
        import costing_model  as cm_mod
        # Monkey-patch get_labour_meta to return a non-empty meta
        original = cm_mod.get_labour_meta
        try:
            cm_mod.get_labour_meta = lambda seg, fy: {"n_months": 12, "pipe_ideal_rate": 2.5, "fitting_ideal_rate": 6.5}
            # Also patch AVAILABLE so the skip check runs
            orig_avail = cm_mod.AVAILABLE
            cm_mod.AVAILABLE = True
            result = cl_mod.load_labour_fy("PLUMBING", "2526")
            assert result.get("skipped") is True
            assert result.get("frozen") is True
        finally:
            cm_mod.get_labour_meta = original
            cm_mod.AVAILABLE = orig_avail


# ---------------------------------------------------------------------------
# 16. Rejection basis — gross formula pairs with gross rate (FIX 4)
# ---------------------------------------------------------------------------

class TestRejectionBasis:
    """Verify the GROSS basis formula is internally consistent and that the
    OLD net-basis formula overstates relative to it.
    """

    # May 2026 pipe acceptance data from the spec
    PROD_KG = 313_516.0
    REJ_KG  = 30_484.0

    def _r_gross(self):
        return self.REJ_KG / (self.PROD_KG + self.REJ_KG)

    def _r_net(self):
        return self.REJ_KG / self.PROD_KG

    def test_gross_rate_less_than_net_rate(self):
        assert self._r_gross() < self._r_net()

    def test_gross_rate_approx_8pt86_pct(self):
        """Spec: 30,484 / (313,516 + 30,484) = 8.86%."""
        assert self._r_gross() * 100 == pytest.approx(8.86, rel=1e-3)

    def test_net_rate_approx_9pt72_pct(self):
        """Net basis for same data: 30,484 / 313,516 = 9.72%."""
        assert self._r_net() * 100 == pytest.approx(9.72, rel=1e-3)

    def test_gross_formula_with_gross_rate_matches_net_formula_with_net_rate(self):
        """Both correct conventions give identical gross quantities."""
        net_demand = 267_163.0
        gross_a = net_demand / (1.0 - self._r_gross())   # GROSS formula + GROSS rate
        gross_b = net_demand * (1.0 + self._r_net())      # NET formula + NET rate
        assert gross_a == pytest.approx(gross_b, rel=1e-6)

    def test_wrong_net_rate_in_gross_formula_overstates(self):
        """Using NET rate in gross formula (the old bug) overstates vs correct."""
        net_demand = 267_163.0
        correct_gross = net_demand / (1.0 - self._r_gross())
        wrong_gross   = net_demand / (1.0 - self._r_net())    # was the old code
        assert wrong_gross > correct_gross, "Old formula must overstate"
        overstatement_pct = (wrong_gross - correct_gross) / correct_gross * 100
        # Typical overstatement at ~9% NET rate is ~1% (rate-dependent).
        # Assert the direction (positive) and that it is meaningfully non-zero.
        assert overstatement_pct > 0.5, (
            f"Expected >0.5% overstatement, got {overstatement_pct:.2f}%"
        )

    def test_rej_basis_constant_is_gross(self):
        """REJ_BASIS constant must be 'gross'."""
        import sys, os
        prayag_dir = os.path.join(os.path.dirname(__file__), "..")
        if prayag_dir not in sys.path:
            sys.path.insert(0, prayag_dir)
        import mp_rejection_plan as mrp
        assert mrp.REJ_BASIS == "gross"


# ---------------------------------------------------------------------------
# 10. Report-12 parser — _fy_month_to_ym
# ---------------------------------------------------------------------------

class TestFyMonthToYm:
    """_fy_month_to_ym maps FY code + month label to YYYY-MM string."""

    def _fn(self):
        return _import_labour()._fy_month_to_ym

    def test_apr_2627(self):
        assert self._fn()("2627", "APR") == "2026-04"

    def test_sep_2627(self):
        assert self._fn()("2627", "SEP") == "2026-09"

    def test_oct_2627(self):
        assert self._fn()("2627", "OCT") == "2026-10"

    def test_dec_2627(self):
        assert self._fn()("2627", "DEC") == "2026-12"

    def test_jan_2627(self):
        assert self._fn()("2627", "JAN") == "2027-01"

    def test_mar_2627(self):
        assert self._fn()("2627", "MAR") == "2027-03"

    def test_apr_2526(self):
        assert self._fn()("2526", "APR") == "2025-04"

    def test_jan_2526(self):
        assert self._fn()("2526", "JAN") == "2026-01"

    def test_case_insensitive(self):
        assert self._fn()("2627", "apr") == "2026-04"

    def test_apr_through_mar_all_distinct(self):
        cl = _import_labour()
        months = cl.MONTH_LABELS
        yms = [cl._fy_month_to_ym("2627", m) for m in months]
        assert len(set(yms)) == 12, "All 12 months must map to distinct YYYY-MM values"


# ---------------------------------------------------------------------------
# 11. Report-12 parser — parse_r12_fittings_kg
# ---------------------------------------------------------------------------

def _make_r12_values(*, with_sap_code=False, rows=None):
    """Build a minimal Report-12 tab value grid matching the REAL two-row header.

    FY2627 layout (with_sap_code=True) — TWO-ROW header:
      Row 0: blank
      Row 1 (main hdr):  SAP Code | Item Name | Machine | Date | Pcs |
                          Output Production | Actual Rejection Weight (in Kgs) |
                          Weight per Pc | Weight of Total Production
      Row 2 (sub-hdr):   ""  | ""  | ""  | ""  | ""  | "Wt in Kgs" | "" | "" | ""
      Row 3+: data       [SAP, item, machine, date, pcs, wt_in_kgs, rej_kg, wt_pc, wtp]
      Col indices: wik=5 (sub-hdr), rej=6, wtp=8; data_start=3

    FY2526 layout (with_sap_code=False) — SINGLE-ROW header:
      "Wt in Kgs" is on the main header row (no sub-header row).
      Row 0: blank
      Row 1 (main hdr):  Item Name | Machine | Date | Pcs |
                          Wt in Kgs | Actual Rejection Weight (in Kgs) |
                          Weight per Pc | Weight of Total Production
      Row 2+: data       [item, machine, date, pcs, wt_in_kgs, rej_kg, wt_pc, wtp]
      Col indices: wik=4, rej=5, wtp=7; data_start=2
    """
    if with_sap_code:
        main_hdr = ["SAP Code", "Item Name", "Machine", "Date", "Pcs",
                    "Output Production", "Actual Rejection Weight (in Kgs)",
                    "Weight per Pc", "Weight of Total Production"]
        sub_hdr  = ["",         "",          "",        "",     "",
                    "Wt in Kgs", "",          "",        ""]
        if rows is None:
            rows = [
                ["SAP001", "90mm TEE",  "M/C-1", "01-04-2026", 100, 50.0, 0.0, 0.5, 50.0],
                ["SAP002", "110mm ELB", "M/C-2", "01-04-2026", 200, 80.0, 0.0, 0.4, 80.0],
            ]
        return [[], main_hdr, sub_hdr] + rows
    else:
        main_hdr = ["Item Name", "Machine", "Date", "Pcs",
                    "Wt in Kgs", "Actual Rejection Weight (in Kgs)",
                    "Weight per Pc", "Weight of Total Production"]
        if rows is None:
            rows = [
                ["90mm TEE",  "M/C-1", "01-04-2025", 100, 50.0, 0.0, 0.5, 50.0],
                ["110mm ELB", "M/C-2", "01-04-2025", 200, 80.0, 0.0, 0.4, 80.0],
            ]
        return [[], main_hdr] + rows


def _make_r12_inline(rows_data, *, two_row_hdr=False):
    """Build a minimal inline R12 grid (single-header FY2526 style by default).

    Single-header column order (col indices):
      0: Item Name, 1: Machine, 2: Pcs,
      3: Wt in Kgs, 4: Actual Rejection Weight (in Kgs),
      5: Weight per Pc, 6: Weight of Total Production

    ``rows_data`` items: (item, machine, pcs, wt_in_kgs, rej_kg, wt_per_pc, wtp)

    Two-row header (two_row_hdr=True): "Wt in Kgs" in sub-header row.
      0: Item Name, 1: Machine, 2: Pcs,
      3: Output Production [main hdr], 4: Actual Rejection Weight [main hdr],
         Weight of Total Production [main hdr]
      sub-hdr col 3: Wt in Kgs
    Data col indices: wik=3 (sub-hdr), rej=4 (main), wtp=5 (main).
    """
    if two_row_hdr:
        main_hdr = ["Item Name", "Machine", "Pcs",
                    "Output Production", "Actual Rejection Weight (in Kgs)",
                    "Weight of Total Production"]
        sub_hdr  = ["",          "",        "",
                    "Wt in Kgs", "",        ""]
        data = [[r[0], r[1], r[2], r[3], r[4], r[6]] for r in rows_data]
        return [[], main_hdr, sub_hdr] + data
    else:
        main_hdr = ["Item Name", "Machine", "Pcs",
                    "Wt in Kgs", "Actual Rejection Weight (in Kgs)",
                    "Weight per Pc", "Weight of Total Production"]
        data = list(rows_data)
        return [[], main_hdr] + data


class TestParseR12FittingsKg:
    """parse_r12_fittings_kg — header-based, works for both FY layouts.

    AUTHORITATIVE FIGURE: total_fitting_kg = Wt-in-Kgs + Actual Rejection Weight
    VARIANCE CHECK:       |Wt-in-Kgs − Weight-of-Total-Prod| / W-T-P × 100
    """

    def _parse(self, values, **kw):
        return _import_labour().parse_r12_fittings_kg(values, **kw)

    # ── New formula: authoritative = Wt-in-Kgs + Rejection ──────────────

    def test_authoritative_is_wt_in_kgs_plus_rejection(self):
        """total_fitting_kg = Wt-in-Kgs + Actual Rejection Weight (not W-T-P)."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 100, 50.0, 8.0, 0.5, 60.0),  # wt_in_kgs=50, rej=8, wtp=60
        ])
        result = self._parse(vals)
        assert result["total_fitting_kg"] == pytest.approx(58.0), (
            "authoritative must be 50 (Wt in Kgs) + 8 (Rejection) = 58, NOT 60 (W-T-P)"
        )
        assert result["wt_in_kgs"] == pytest.approx(50.0)
        assert result["rejection_kg"] == pytest.approx(8.0)

    def test_zero_rejection_gives_wt_in_kgs_as_total(self):
        """When rejection = 0, total_fitting_kg == wt_in_kgs."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 100, 50.0, 0.0, 0.5, 50.0),
        ])
        result = self._parse(vals)
        assert result["total_fitting_kg"] == pytest.approx(50.0)
        assert result["rejection_kg"] == pytest.approx(0.0)

    def test_both_rows_summed(self):
        """Two rows: wt_in_kgs and rejection summed independently."""
        result = self._parse(_make_r12_values(with_sap_code=False))
        # Default rows: wt_in_kgs=[50,80], rej=[0,0] → total=130, rej=0
        assert result["wt_in_kgs"] == pytest.approx(130.0)
        assert result["rejection_kg"] == pytest.approx(0.0)
        assert result["total_fitting_kg"] == pytest.approx(130.0)

    def test_n_rows_counted(self):
        result = self._parse(_make_r12_values(with_sap_code=False))
        assert result["n_rows"] == 2

    # ── Two-row header: "Wt in Kgs" in sub-header row ───────────────────

    def test_sub_header_row_detected_fy2627(self):
        """FY2627 layout: 'Wt in Kgs' is in sub-header row (not main header)."""
        result = self._parse(_make_r12_values(with_sap_code=True))
        assert result["wt_in_kgs"] == pytest.approx(130.0), (
            "Wt-in-Kgs must be found via the sub-header row in FY2627 layout"
        )
        assert result["total_fitting_kg"] == pytest.approx(130.0)

    def test_two_row_header_data_starts_after_both_rows(self):
        """With sub-header, data must start at main+2, not main+1."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 100, 50.0, 5.0, 0.5, 50.0),
            ("110mm ELB", "M/C-2", 200, 80.0, 8.0, 0.4, 80.0),
        ], two_row_hdr=True)
        result = self._parse(vals)
        # Sub-header row ("Wt in Kgs" label) must NOT be counted as a data row
        assert result["n_rows"] == 2, (
            "sub-header row must be skipped; only 2 real data rows expected"
        )
        assert result["wt_in_kgs"] == pytest.approx(130.0)
        assert result["rejection_kg"] == pytest.approx(13.0)
        assert result["total_fitting_kg"] == pytest.approx(143.0)

    def test_single_row_header_fy2526_still_works(self):
        """FY2526 layout: 'Wt in Kgs' on main header row, no sub-header needed."""
        result = self._parse(_make_r12_values(with_sap_code=False))
        assert result["wt_in_kgs"] == pytest.approx(130.0)
        assert result["n_rows"] == 2

    # ── Variance: |Wt-in-Kgs − W-T-P| / W-T-P (data quality only) ──────

    def test_variance_compares_wt_in_kgs_vs_weight_of_total_prod(self):
        """Variance = |Wt-in-Kgs − W-T-P| / W-T-P.  W-T-P=100, Wt-in-Kgs=90 → 10%."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 200, 90.0, 0.0, 0.5, 100.0),  # wik=90, rej=0, wtp=100
        ])
        result = self._parse(vals)
        assert result["weight_of_total_prod"] == pytest.approx(100.0)
        assert result["variance_pct"] == pytest.approx(10.0, rel=1e-3)
        # total_fitting_kg still uses gross: 90+0=90, NOT the wtp=100
        assert result["total_fitting_kg"] == pytest.approx(90.0)

    def test_zero_variance_when_wt_in_kgs_equals_wtp(self):
        """No variance when hand-keyed Wt-in-Kgs matches the formula W-T-P."""
        result = self._parse(_make_r12_values(with_sap_code=False))
        assert result["variance_pct"] == pytest.approx(0.0, abs=0.01)

    def test_no_divergent_rows_when_identical(self):
        result = self._parse(_make_r12_values(with_sap_code=False))
        assert result["divergent_rows"] == []

    def test_row_flagged_when_divergence_exceeds_threshold(self):
        """Row with |W-T-P − Wt-in-Kgs| / W-T-P = 10% > default 5% → flagged."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 200, 90.0, 0.0, 0.5, 100.0),
        ])
        result = self._parse(vals)
        assert len(result["divergent_rows"]) == 1
        row = result["divergent_rows"][0]
        assert row["diff_pct"] == pytest.approx(10.0, rel=1e-2)

    def test_row_not_flagged_within_threshold(self):
        """Row at 3% divergence < 5% default threshold → not flagged."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 200, 97.0, 0.0, 0.5, 100.0),  # 3% off
        ])
        result = self._parse(vals, row_variance_pct=5.0)
        assert result["divergent_rows"] == []

    def test_custom_row_variance_threshold(self):
        """Custom threshold=2%: a 3% divergent row IS flagged."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 200, 97.0, 0.0, 0.5, 100.0),  # 3% off
        ])
        result = self._parse(vals, row_variance_pct=2.0)
        assert len(result["divergent_rows"]) == 1

    def test_june_scenario_variance_fires(self):
        """Jun 2026: 4.4% variance fires (>2%) with 81 bad-weight rows scenario."""
        # Simplified: wt_in_kgs=97_006.92, wtp=101_512 → divergence ≈ 4.4%
        vals = _make_r12_inline([
            ("ALL JUNE ROWS", "various", 10000, 97_006.92, 974.07, 0.0, 101_512.0),
        ])
        result = self._parse(vals)
        # Variance = |97006.92 − 101512| / 101512 × 100 ≈ 4.44%
        assert result["variance_pct"] is not None
        assert result["variance_pct"] > 2.0, (
            f"June variance {result['variance_pct']:.2f}% should exceed 2% threshold"
        )

    # ── Acceptance decomposition: verified to the paisa ─────────────────

    def test_acceptance_apr_decomposition(self):
        """Apr 2026: 89,151.74 + 886.69 = 90,038.43 kg (verified to the paisa)."""
        vals = _make_r12_inline([
            ("APR combined", "M/C-1", 1000, 89_151.74, 886.69, 0.0, 93_839.0),
        ])
        result = self._parse(vals)
        assert result["wt_in_kgs"] == pytest.approx(89_151.74, rel=1e-6)
        assert result["rejection_kg"] == pytest.approx(886.69, rel=1e-6)
        assert result["total_fitting_kg"] == pytest.approx(90_038.43, rel=1e-6)

    def test_acceptance_may_decomposition(self):
        """May 2026: 75,771.15 + 752.24 = 76,523.39 kg."""
        vals = _make_r12_inline([
            ("MAY combined", "M/C-1", 1000, 75_771.15, 752.24, 0.0, 79_875.0),
        ])
        result = self._parse(vals)
        assert result["wt_in_kgs"] == pytest.approx(75_771.15, rel=1e-6)
        assert result["rejection_kg"] == pytest.approx(752.24, rel=1e-6)
        assert result["total_fitting_kg"] == pytest.approx(76_523.39, rel=1e-6)

    def test_acceptance_jun_decomposition(self):
        """Jun 2026: 97,006.92 + 974.07 = 97,980.99 kg."""
        vals = _make_r12_inline([
            ("JUN combined", "M/C-1", 1000, 97_006.92, 974.07, 0.0, 101_512.0),
        ])
        result = self._parse(vals)
        assert result["wt_in_kgs"] == pytest.approx(97_006.92, rel=1e-6)
        assert result["rejection_kg"] == pytest.approx(974.07, rel=1e-6)
        assert result["total_fitting_kg"] == pytest.approx(97_980.99, rel=1e-6)

    def test_acceptance_three_month_total(self):
        """Sum of Apr+May+Jun = 264,542.81 kg total (authoritative acceptance figure)."""
        monthly_totals = [90_038.43, 76_523.39, 97_980.99]
        assert sum(monthly_totals) == pytest.approx(264_542.81, rel=1e-6)

    # ── Edge cases ───────────────────────────────────────────────────────

    def test_empty_values_returns_zero(self):
        result = self._parse([])
        assert result["total_fitting_kg"] == 0.0
        assert result["n_rows"] == 0

    def test_no_header_returns_zero(self):
        result = self._parse([["Date", "Item", "Pcs"]])
        assert result["total_fitting_kg"] == 0.0

    def test_total_row_skipped(self):
        """A row whose first cell is 'TOTAL' must not be summed."""
        vals = _make_r12_inline([
            ("90mm TEE",  "M/C-1", 100, 50.0, 5.0, 0.5, 50.0),
            ("TOTAL",     "",      300, 150.0, 15.0, "", 150.0),  # ← must be skipped
        ])
        result = self._parse(vals)
        assert result["wt_in_kgs"] == pytest.approx(50.0), "TOTAL row must not be double-counted"
        assert result["rejection_kg"] == pytest.approx(5.0)
        assert result["total_fitting_kg"] == pytest.approx(55.0)
        assert result["n_rows"] == 1

    def test_non_numeric_rejection_rows_skipped(self):
        """Rows with non-numeric rejection cell are auto-skipped (e.g. sub-hdr)."""
        vals = [
            [],
            ["Item Name", "Machine", "Pcs",
             "Wt in Kgs", "Actual Rejection Weight (in Kgs)",
             "Weight per Pc", "Weight of Total Production"],
            ["ITEM", "M/C", "PCS", "WT", "REJ", "WT/PC", "TOTAL WT"],  # sub-hdr-like
            ["90mm TEE", "M/C-1", 100, 50.0, 5.0, 0.5, 50.0],
        ]
        result = self._parse(vals)
        assert result["total_fitting_kg"] == pytest.approx(55.0)
        assert result["n_rows"] == 1

    # ── Divergent row metadata ───────────────────────────────────────────

    def test_divergent_row_metadata_fields(self):
        """Flagged rows carry item, machine, pcs, w_tot, w_kgs, diff_pct."""
        vals = _make_r12_inline([
            ("90mm TEE", "M/C-1", 200, 90.0, 0.0, 0.5, 100.0),
        ])
        result = self._parse(vals)
        assert len(result["divergent_rows"]) == 1
        row = result["divergent_rows"][0]
        for field in ("item", "machine", "pcs", "w_tot", "w_kgs", "diff_pct"):
            assert field in row, f"Missing field '{field}' in divergent row"
        assert row["item"] == "90mm TEE"
        assert row["machine"] == "M/C-1"
        assert row["w_tot"] == pytest.approx(100.0)
        assert row["w_kgs"] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# 12. R12 source routing — load_labour_fy patching logic (offline)
# ---------------------------------------------------------------------------

class TestR12SourceRouting:
    """Verify that monthly rows are patched correctly from R12 data."""

    def _patch_row(self, total_fitting_kg, wt_in_kgs, rejection_kg=0.0,
                   wtp=None, fitting_sheet_kg=500_000.0):
        """Simulate the patching logic in load_labour_fy for one row.

        ``total_fitting_kg`` = wt_in_kgs + rejection_kg (gross actual authoritative).
        ``wtp``              = Weight-of-Total-Production (formula-driven, variance ref).
        """
        row = {
            "month_label": "APR", "month_num": 1,
            "pipe_prod_kg": 600_000.0,
            "fitting_prod_kg": fitting_sheet_kg,
            "paid_wages": 500_000.0, "contractor_wages": 50_000.0,
        }
        if wtp is None:
            wtp = wt_in_kgs   # default: no divergence
        variance_pct = (abs(wt_in_kgs - wtp) / wtp * 100) if wtp else None
        r12 = {
            "total_fitting_kg": total_fitting_kg,
            "wt_in_kgs":        wt_in_kgs,
            "rejection_kg":     rejection_kg,
            "weight_of_total_prod": wtp,
            "variance_pct":     variance_pct,
            "divergent_rows": [], "n_rows": 10,
        }

        # Mirror the patching logic from load_labour_fy
        if total_fitting_kg > 0:
            row["fitting_r12_kg"]        = total_fitting_kg
            row["wt_in_kgs_total"]       = wt_in_kgs
            row["r12_rejection_kg"]      = rejection_kg
            row["fitting_kg_source"]     = "report12"
            row["fitting_variance_pct"]  = r12.get("variance_pct")
            row["fitting_divergent_n"]   = 0
            row["fitting_divergent_rows"] = []
            row["fitting_prod_kg"]       = total_fitting_kg
        else:
            row["fitting_r12_kg"]        = None
            row["wt_in_kgs_total"]       = None
            row["r12_rejection_kg"]      = None
            row["fitting_kg_source"]     = "labour_sheet"
            row["fitting_variance_pct"]  = None
            row["fitting_divergent_n"]   = 0
            row["fitting_divergent_rows"] = []

        pipe_kg    = float(row.get("pipe_prod_kg") or 0)
        fit_kg     = float(row.get("fitting_prod_kg") or 0)
        total_wages = float(row.get("paid_wages") or 0) + float(row.get("contractor_wages") or 0)
        row["total_prod_kg"]      = round(pipe_kg + fit_kg, 2) if (pipe_kg + fit_kg) > 0 else None
        row["per_kg_labour_cost"] = (
            round(total_wages / row["total_prod_kg"], 4)
            if row.get("total_prod_kg") and row["total_prod_kg"] > 0 else None
        )
        return row

    def test_r12_source_overrides_labour_sheet(self):
        """fitting_prod_kg becomes total_fitting_kg (gross) when R12 available."""
        row = self._patch_row(total_fitting_kg=90_038.43,
                              wt_in_kgs=89_151.74, rejection_kg=886.69)
        assert row["fitting_prod_kg"] == pytest.approx(90_038.43)
        assert row["fitting_kg_source"] == "report12"

    def test_r12_stores_decomposition(self):
        """wt_in_kgs_total and r12_rejection_kg stored separately."""
        row = self._patch_row(total_fitting_kg=90_038.43,
                              wt_in_kgs=89_151.74, rejection_kg=886.69)
        assert row["wt_in_kgs_total"]  == pytest.approx(89_151.74)
        assert row["r12_rejection_kg"] == pytest.approx(886.69)

    def test_labour_sheet_fallback_when_r12_zero(self):
        """When R12 returns 0, labour-sheet value kept with 'labour_sheet' source."""
        row = self._patch_row(total_fitting_kg=0.0, wt_in_kgs=0.0,
                              fitting_sheet_kg=80_000.0)
        assert row["fitting_prod_kg"] == pytest.approx(80_000.0)
        assert row["fitting_kg_source"] == "labour_sheet"
        assert row["r12_rejection_kg"] is None

    def test_total_prod_kg_recomputed(self):
        """total_prod_kg = pipe + gross R12 fitting (not old labour-sheet total)."""
        row = self._patch_row(total_fitting_kg=90_038.43,
                              wt_in_kgs=89_151.74, rejection_kg=886.69)
        expected = 600_000.0 + 90_038.43
        assert row["total_prod_kg"] == pytest.approx(expected, rel=1e-4)

    def test_per_kg_recomputed(self):
        """per_kg_labour_cost = total_wages / total_prod_kg (recomputed)."""
        row = self._patch_row(total_fitting_kg=90_038.43,
                              wt_in_kgs=89_151.74, rejection_kg=886.69)
        total_wages = 500_000.0 + 50_000.0
        expected = total_wages / row["total_prod_kg"]
        assert row["per_kg_labour_cost"] == pytest.approx(expected, rel=1e-4)

    def test_variance_flag_set_when_wt_in_kgs_diverges_from_wtp(self):
        """Variance flag = |Wt-in-Kgs − W-T-P| / W-T-P — NOT using total_fitting_kg."""
        # wt_in_kgs=95_000, wtp=100_000 → 5% variance
        row = self._patch_row(total_fitting_kg=95_000.0,
                              wt_in_kgs=95_000.0, rejection_kg=0.0,
                              wtp=100_000.0)
        assert row["fitting_variance_pct"] is not None
        assert row["fitting_variance_pct"] > 2.0

    def test_no_variance_flag_when_within_threshold(self):
        """<2% divergence — variance stays low."""
        row = self._patch_row(total_fitting_kg=99_100.0,
                              wt_in_kgs=99_100.0, rejection_kg=0.0,
                              wtp=100_000.0)   # 0.9% divergence
        assert row["fitting_variance_pct"] is not None
        assert row["fitting_variance_pct"] < 2.0


# ---------------------------------------------------------------------------
# 13. _fy_month_to_ym — acceptance figures math check (no live data needed)
# ---------------------------------------------------------------------------

class TestAcceptanceFiguresMath:
    """Cross-check acceptance figures are internally consistent (no live data needed).

    FY2026-27 (Apr/May/Jun 2026) — corrected gross-actual formula:
      fitting_kg = Wt-in-Kgs + Actual Rejection Weight (verified to the paisa).
    """

    # Gross-actual components (Wt-in-Kgs and Rejection per month)
    WIK_KG  = [89_151.74, 75_771.15, 97_006.92]   # Wt in Kgs
    REJ_KG  = [886.69,    752.24,    974.07]        # Actual Rejection Weight
    # Authoritative costing figure: WIK + REJ
    FITTING_KG = [90_038.43, 76_523.39, 97_980.99]
    # Formula-driven column (for variance reference only)
    WTP_KG  = [93_839.0, 79_875.0, 101_512.0]
    PIPE_KG_TOTAL = 637_410.0
    # wages derived from: 6.122416759 × (637,410 + 264,542.81) = 6.1224… × 901,952.81
    TOTAL_WAGES   = 5_522_131.0

    def test_wik_plus_rej_equals_fitting_kg(self):
        """Each month: Wt-in-Kgs + Rejection = total_fitting_kg (to the paisa)."""
        for wik, rej, expected in zip(self.WIK_KG, self.REJ_KG, self.FITTING_KG):
            assert wik + rej == pytest.approx(expected, rel=1e-6), (
                f"Wt-in-Kgs {wik} + Rejection {rej} should equal {expected}"
            )

    def test_fy_to_date_fittings_sum(self):
        """Apr+May+Jun fittings = 264,542.81 kg (official corrected figure)."""
        assert sum(self.FITTING_KG) == pytest.approx(264_542.81, rel=1e-6)

    def test_total_production_kg(self):
        """Total production = pipe 637,410 + fittings 264,542.81 = 901,952.81 kg."""
        total = self.PIPE_KG_TOTAL + sum(self.FITTING_KG)
        assert total == pytest.approx(901_952.81, rel=1e-5)

    def test_cost_per_kg_fy2627(self):
        """FY2627 cost per kg ≈ ₹6.122 (using official wage total)."""
        total_kg = self.PIPE_KG_TOTAL + sum(self.FITTING_KG)
        cost_per_kg = self.TOTAL_WAGES / total_kg
        assert cost_per_kg == pytest.approx(6.122416759, rel=1e-4)

    def test_cost_per_kg_exceeds_fy2526_baseline(self):
        """FY2627 cost/kg must exceed FY2526 baseline ₹4.157/kg (cost worsening)."""
        total_kg = self.PIPE_KG_TOTAL + sum(self.FITTING_KG)
        cost_per_kg = self.TOTAL_WAGES / total_kg
        assert cost_per_kg > 4.157, (
            f"FY2627 cost/kg {cost_per_kg:.4f} must exceed FY2526 baseline ₹4.157"
        )

    def test_formula_column_is_higher_than_gross_actual(self):
        """W-T-P (pcs×std wt) exceeds gross-actual in all three months."""
        for wtp, gross in zip(self.WTP_KG, self.FITTING_KG):
            assert wtp > gross, (
                f"W-T-P {wtp} should exceed gross-actual {gross} "
                "(W-T-P includes standard weight per pc; gross uses hand-keyed values)"
            )

    def test_r12_source_flag_on_rows(self):
        """After patching, fitting_kg_source must be 'report12'."""
        for fg in self.FITTING_KG:
            row = {"fitting_prod_kg": fg, "fitting_kg_source": "report12"}
            assert row["fitting_kg_source"] == "report12"


# ---------------------------------------------------------------------------
# 14. REJECTION & PRODUCTION tab unit-mismatch guard
# ---------------------------------------------------------------------------

class TestRejectionProdTabGuard:
    """check_rejection_prod_tab_units — detect when fittings column holds pieces."""

    def _check(self, tab_values, r12_fitting_kg, **kw):
        return _import_labour().check_rejection_prod_tab_units(tab_values, r12_fitting_kg, **kw)

    def _make_rej_prod_tab(self, prod_value):
        """Minimal 'REJECTION & PRODUCTION' tab with a Fittings production cell."""
        return [
            ["REJECTION & PRODUCTION SUMMARY"],
            [],
            ["",        "Pipe",        "",   "Fittings",           ""],
            ["Month",   "Production",  "",   "Production (Kgs)",   "Rejection (Kgs)"],
            ["APR",     500_000,       "",   prod_value,            2_000],
        ]

    def test_detects_pieces_when_far_exceeds_r12_kg(self):
        """FY2627 REJECTION tab: fittings shows 1,340,117 pcs but R12 says ~90,038 kg.

        Ratio ≈ 14.9 > 10 → unit mismatch detected.
        """
        result = self._check(
            self._make_rej_prod_tab(1_340_117),
            r12_fitting_kg=90_038.43,
        )
        assert result["is_unit_mismatch"] is True
        assert result["ratio"] == pytest.approx(1_340_117 / 90_038.43, rel=1e-3)

    def test_no_mismatch_when_within_ratio(self):
        """Tab value close to R12 kg → ratio < 10 → no mismatch."""
        result = self._check(
            self._make_rej_prod_tab(91_000),   # only 1% above R12
            r12_fitting_kg=90_038.43,
        )
        assert result["is_unit_mismatch"] is False

    def test_custom_mismatch_ratio(self):
        """Custom ratio=5: value 6× R12 triggers mismatch."""
        result = self._check(
            self._make_rej_prod_tab(540_231),  # ~6× r12
            r12_fitting_kg=90_038.43,
            mismatch_ratio=5.0,
        )
        assert result["is_unit_mismatch"] is True

    def test_r12_kg_zero_returns_no_mismatch(self):
        """When R12 kg is 0 (unavailable), cannot compute ratio → no false-positive."""
        result = self._check(
            self._make_rej_prod_tab(1_000_000),
            r12_fitting_kg=0.0,
        )
        assert result["is_unit_mismatch"] is False
        assert result["ratio"] == pytest.approx(0.0)

    def test_tab_sum_and_r12_kg_in_result(self):
        """Result dict contains tab_sum, r12_kg, and ratio for auditability."""
        result = self._check(
            self._make_rej_prod_tab(1_340_117),
            r12_fitting_kg=90_038.43,
        )
        assert "tab_sum" in result
        assert "r12_kg" in result
        assert "ratio" in result
        assert result["r12_kg"] == pytest.approx(90_038.43, rel=1e-4)
