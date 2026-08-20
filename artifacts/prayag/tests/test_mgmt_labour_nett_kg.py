"""tests/test_mgmt_labour_nett_kg.py

Focused unit tests for mgmt_labour_power production-aggregation logic.

Tests cover the cardinal rule: every figure is our own recomputation.
Specifically verifies that nett kg = total_count − reject_count for
PTMT / Garden Pipe / HDPE Pipe, and that Tank uses secondary_counts['kg'].

These tests are self-contained (no Sheets calls, no Flask context).
"""
import sys
import os
from types import SimpleNamespace

# Allow importing from the prayag app directory without installing it
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import mgmt_labour_power as mlp


# ── Minimal stub that matches the Record interface used by accum_record_kg ────

class _R:
    """Minimal Record stub for unit testing."""
    def __init__(self, plant, total_count=0.0, reject_count=0.0,
                 secondary_counts=None):
        self.plant          = plant
        self.total_count    = float(total_count)
        self.reject_count   = float(reject_count)
        self.secondary_counts = secondary_counts or {}


# ── accum_record_kg ───────────────────────────────────────────────────────────

class TestAccumRecordKg:
    """Unit tests for the per-record nett kg extraction."""

    def test_ptmt_nett_subtracts_reject(self):
        """PTMT nett = total_count − reject_count (not gross)."""
        r = _R("PTMT", total_count=10_000.0, reject_count=500.0)
        assert mlp.accum_record_kg(r, "PTMT") == 9_500.0

    def test_garden_nett_subtracts_reject(self):
        """Garden Pipe nett = total_count − reject_count."""
        r = _R("GARDEN", total_count=5_000.0, reject_count=200.0)
        assert mlp.accum_record_kg(r, "Garden Pipe") == 4_800.0

    def test_garden_wb_nett(self):
        """GARDEN_WB also subtracts reject under the Garden Pipe segment."""
        r = _R("GARDEN_WB", total_count=3_000.0, reject_count=100.0)
        assert mlp.accum_record_kg(r, "Garden Pipe") == 2_900.0

    def test_hdpe_nett_subtracts_reject(self):
        """HDPE nett = total_count − reject_count."""
        r = _R("HDPE", total_count=8_000.0, reject_count=400.0)
        assert mlp.accum_record_kg(r, "HDPE Pipe") == 7_600.0

    def test_nett_never_goes_negative(self):
        """Nett is clamped at 0 if reject exceeds total (data anomaly guard)."""
        r = _R("PTMT", total_count=100.0, reject_count=200.0)
        assert mlp.accum_record_kg(r, "PTMT") == 0.0

    def test_zero_reject_means_same_as_total(self):
        """No rejection: nett == total_count."""
        r = _R("PTMT", total_count=50_000.0, reject_count=0.0)
        assert mlp.accum_record_kg(r, "PTMT") == 50_000.0

    def test_tank_uses_secondary_counts_kg_not_total(self):
        """Tank nett comes from secondary_counts['kg'], ignoring total_count."""
        r = _R("TANK", total_count=999_999.0, reject_count=0.0,
               secondary_counts={"kg": 1_234.5})
        assert mlp.accum_record_kg(r, "Tank") == 1_234.5

    def test_tank_vn_uses_secondary_counts_kg(self):
        """TANK_VN also routes through secondary_counts['kg']."""
        r = _R("TANK_VN", total_count=50.0, reject_count=5.0,
               secondary_counts={"kg": 888.0})
        assert mlp.accum_record_kg(r, "Tank") == 888.0

    def test_tank_wb_uses_secondary_counts_kg(self):
        """TANK_WB also routes through secondary_counts['kg']."""
        r = _R("TANK_WB", total_count=100.0, reject_count=10.0,
               secondary_counts={"kg": 555.0})
        assert mlp.accum_record_kg(r, "Tank") == 555.0

    def test_tank_missing_kg_key_returns_zero(self):
        """Tank with no 'kg' in secondary_counts yields 0.0 (no litres fallback)."""
        r = _R("TANK", total_count=100.0, secondary_counts={"ltr": 1000.0})
        assert mlp.accum_record_kg(r, "Tank") == 0.0

    def test_record_without_reject_count_attr(self):
        """Graceful when a Record stub lacks reject_count (old pickle / test stub)."""
        class _NR:
            plant = "PTMT"
            total_count = 12_000.0
            secondary_counts = {}
            # no reject_count attribute

        assert mlp.accum_record_kg(_NR(), "PTMT") == 12_000.0


# ── accumulate_monthly ────────────────────────────────────────────────────────

class TestAccumulateMonthly:
    """Tests for the per-month multi-record aggregation helper."""

    def test_ptmt_aggregates_nett_across_records(self):
        """Multiple PTMT records: sum of nett (total−reject) values."""
        records = [
            _R("PTMT", total_count=100_000.0, reject_count=1_000.0),
            _R("PTMT", total_count=50_000.0,  reject_count=500.0),
        ]
        result = mlp.accumulate_monthly(records, mlp._SEG_PLANTS)
        assert result["PTMT"] == pytest_approx(148_500.0)

    def test_mixed_plants_routed_correctly(self):
        """Records from different plants go to the right segments."""
        records = [
            _R("PTMT",      total_count=100.0, reject_count=10.0),
            _R("GARDEN",    total_count=200.0, reject_count=20.0),
            _R("HDPE",      total_count=300.0, reject_count=30.0),
            _R("TANK",      total_count=999.0, secondary_counts={"kg": 50.0}),
        ]
        result = mlp.accumulate_monthly(records, mlp._SEG_PLANTS)
        assert result["PTMT"]        == 90.0
        assert result["Garden Pipe"] == 180.0
        assert result["HDPE Pipe"]   == 270.0
        assert result["Tank"]        == 50.0

    def test_irrelevant_plants_ignored(self):
        """Records from CP / Plumbing plants are not accumulated."""
        records = [_R("CP_UNKNOWN_PLANT", total_count=9999.0)]
        result = mlp.accumulate_monthly(records, mlp._SEG_PLANTS)
        for v in result.values():
            assert v == 0.0

    def test_empty_records_returns_zeros(self):
        """No records → all segments zero (caller converts 0 → None)."""
        result = mlp.accumulate_monthly([], mlp._SEG_PLANTS)
        assert all(v == 0.0 for v in result.values())

    def test_garden_wb_counted_under_garden_pipe(self):
        """GARDEN_WB contributes to Garden Pipe, not a separate key."""
        records = [
            _R("GARDEN",    total_count=1000.0, reject_count=50.0),
            _R("GARDEN_WB", total_count=500.0,  reject_count=25.0),
        ]
        result = mlp.accumulate_monthly(records, mlp._SEG_PLANTS)
        assert result["Garden Pipe"] == pytest_approx(1_425.0)

    def test_ptmt_fy_total_example(self):
        """Acceptance figure from spec: PTMT FY ≈ 537,109 kg.

        Uses one representative record whose nett matches the April spec figure
        to confirm the arithmetic path is correct.  The full FY total depends on
        all 12 months' daily data from Google Sheets — verified in integration.
        """
        # Apr'26 spec: ≈99,262 kg nett. Simulate: gross 100,000 reject 738
        records = [_R("PTMT", total_count=100_000.0, reject_count=738.0)]
        result = mlp.accumulate_monthly(records, mlp._SEG_PLANTS)
        assert result["PTMT"] == pytest_approx(99_262.0)


# ── Report 1 Part B daily production path ─────────────────────────────────────

def _daily_record(
    plant, period="2026-04", total_count=0.0, reject_count=0.0,
    secondary_counts=None, machine="", is_finishing=False,
):
    return SimpleNamespace(
        plant=plant,
        period=period,
        total_count=float(total_count),
        reject_count=float(reject_count),
        secondary_counts=secondary_counts or {},
        machine=machine,
        is_finishing=is_finishing,
    )


class TestPartBDailyFacts:
    """Report 1 Part B uses daily facts, with each plant's documented basis."""

    def test_daily_loader_uses_primary_pipe_and_plant_specific_net_basis(
        self, monkeypatch,
    ):
        import sheets

        records = [
            # Pipe / Moulding daily production is already net; rejection is
            # separate and gets added back exactly once for ideal-cost gross.
            _daily_record("PIPE", total_count=100, reject_count=10,
                          machine="PIPE M/C - 1"),
            _daily_record("MOULDING", total_count=120, reject_count=12),
            # Garden / HDPE are already net; PTMT daily matrix total is gross.
            _daily_record("GARDEN", total_count=100, reject_count=10),
            _daily_record("HDPE", total_count=200, reject_count=20),
            _daily_record("PTMT", total_count=300, reject_count=30),
            # Tank must retain its dedicated kg measurement rather than litres.
            _daily_record("TANK", total_count=9_999, reject_count=99,
                          secondary_counts={"kg": 50}),
            # Neither finishing nor a non-extrusion Pipe machine is production.
            _daily_record("PIPE", total_count=1_000, reject_count=100,
                          machine="PIPE Grinder-1", is_finishing=True),
            _daily_record("PIPE", total_count=2_000, reject_count=200,
                          machine="PIPE Auxiliary-2"),
        ]
        calls = []
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms: (calls.append(list(yms)) or (records, [], [])),
        )
        monkeypatch.setattr(
            sheets, "get_records",
            lambda _yms: (_ for _ in ()).throw(
                AssertionError("Part B must not read annual summary records")
            ),
        )

        totals = mlp._load_part_b_daily_totals("2627", ["2026-04"])

        assert calls == [["2026-04"]]
        assert totals["Pipe"]["2026-04"] == {"net": 100.0, "reject": 10.0}
        assert totals["Fittings"]["2026-04"] == {"net": 120.0, "reject": 12.0}
        assert totals["Garden"]["2026-04"] == {"net": 100.0, "reject": 10.0}
        assert totals["HDPE"]["2026-04"] == {"net": 200.0, "reject": 20.0}
        assert totals["PTMT"]["2026-04"] == {"net": 270.0, "reject": 30.0}
        assert totals["Tank"]["2026-04"] == {"net": 50.0, "reject": 0.0}

        ideal = mlp._build_ideal_cost_section(totals, "2627", "power")
        april = ideal["months"][0]["segs"]
        assert april["Pipe"]["net"] == 110.0
        assert april["Fittings"]["net"] == 132.0
        assert april["Garden"]["net"] == 110.0
        assert april["HDPE"]["net"] == 220.0

    def test_daily_loader_respects_selected_reporting_window(self, monkeypatch):
        import sheets

        april = _daily_record(
            "PIPE", total_count=100, machine="PIPE M/C - 1",
        )
        august = _daily_record(
            "PIPE", period="2026-08", total_count=999, machine="PIPE M/C - 1",
        )
        calls = []
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms: (calls.append(list(yms)) or ([april, august], [], [])),
        )

        totals = mlp._load_part_b_daily_totals("2627", ["2026-04"])

        assert calls == [["2026-04"]]
        assert totals["Pipe"]["2026-04"]["net"] == 100.0
        assert "2026-08" not in totals["Pipe"]

    def test_segment_labour_export_passes_its_selected_month_to_builder(
        self, monkeypatch,
    ):
        from reports import serialisers

        calls = []
        monkeypatch.setattr(
            mlp, "build_mgmt_report_data",
            lambda fy, through_ym=None: (
                calls.append((fy, through_ym)) or {"error": "test only"}
            ),
        )

        serialisers.serial_segment_labour("2026-07")

        assert calls == [("2627", "2026-07")]

    def test_invalidating_one_fy_clears_each_selected_month_variant(self):
        mlp._cache.clear()
        mlp._cache[("2627", "2026-07")] = (0.0, {})
        mlp._cache[("2627", None)] = (0.0, {})
        mlp._cache[("2526", "2025-07")] = (0.0, {})

        mlp.invalidate_cache("2627")

        assert ("2627", "2026-07") not in mlp._cache
        assert ("2627", None) not in mlp._cache
        assert ("2526", "2025-07") in mlp._cache
        mlp._cache.clear()

    def test_invalidating_without_fy_clears_every_report_variant(self):
        mlp._cache.clear()
        mlp._cache[("2627", "2026-07")] = (0.0, {})
        mlp._cache[("2526", "2025-07")] = (0.0, {})

        mlp.invalidate_cache()

        assert mlp._cache == {}

    def test_unsupported_fy_fails_instead_of_falling_back_to_2627(self):
        result = mlp.build_mgmt_report_data("2526", through_ym="2025-07")

        assert result["fy"] == "2526"
        assert result["units"] == []
        assert "FY2025-26" in result["error"]


# ── per-kg computation helpers ────────────────────────────────────────────────

class TestSafeDiv:
    def test_normal(self):
        assert mlp._safe_div(1_000.0, 500.0) == pytest_approx(2.0)

    def test_zero_denominator(self):
        assert mlp._safe_div(1_000.0, 0.0) is None

    def test_none_numerator(self):
        assert mlp._safe_div(None, 500.0) is None

    def test_none_denominator(self):
        assert mlp._safe_div(1_000.0, None) is None

    def test_both_none(self):
        assert mlp._safe_div(None, None) is None

    def test_garden_per_kg_labour(self):
        """Garden Pipe spec: wages ≈220,797; prod ≈109,242 kg → per-kg ≈2.02."""
        wages = 220_797.0
        prod  = 109_242.0
        result = mlp._safe_div(wages, prod)
        assert result == pytest_approx(2.02, rel=0.02)


# ── module constants ─────────────────────────────────────────────────────────

def test_unit_segments_coverage():
    """Every expected segment is in exactly one unit."""
    all_segs = [s for segs in mlp.UNIT_SEGMENTS.values() for s in segs]
    assert "PTMT"       in all_segs
    assert "Plumbing"   in all_segs
    assert "Tank"       in all_segs
    assert "Garden Pipe" in all_segs
    assert "HDPE Pipe"  in all_segs
    assert "CP"         in all_segs
    # No duplicates
    assert len(all_segs) == len(set(all_segs))


def test_plant_sets_correct():
    """_SEG_PLANTS must include all three Tank variants."""
    assert "TANK"    in mlp._SEG_PLANTS["Tank"]
    assert "TANK_VN" in mlp._SEG_PLANTS["Tank"]
    assert "TANK_WB" in mlp._SEG_PLANTS["Tank"]
    assert "PTMT"    in mlp._SEG_PLANTS["PTMT"]
    assert "GARDEN"  in mlp._SEG_PLANTS["Garden Pipe"]
    assert "GARDEN_WB" in mlp._SEG_PLANTS["Garden Pipe"]
    assert "HDPE"    in mlp._SEG_PLANTS["HDPE Pipe"]


def test_fy_month_maps_complete():
    """FY2627 map must cover all 12 FY months in the correct YYYY-MM format."""
    ym_map = mlp._FY_YM["2627"]
    assert set(ym_map.keys()) == set(mlp.MONTH_LABELS)
    for lbl, ym in ym_map.items():
        year, month = ym.split("-")
        assert 2026 <= int(year) <= 2027
        assert 1 <= int(month) <= 12


def test_solar_key_in_blank_row():
    """blank rows must include a 'solar' key so the template never KeyErrors."""
    blank = mlp._blank_seg("UNIT-3", "Garden Pipe", "2627")
    assert "solar" in blank["total_row"]
    assert all("solar" in r for r in blank["month_rows"])


# ── pytest_approx shim ────────────────────────────────────────────────────────

class _ApproxSingle:
    def __init__(self, expected, rel=1e-6, abs=None):
        self._e = expected
        self._rel = rel
        self._abs = abs or (abs or 1e-6)

    def __eq__(self, actual):
        if actual is None:
            return False
        tol = max(self._rel * abs(self._e), self._abs)
        return abs(actual - self._e) <= tol

    def __repr__(self):
        return f"≈{self._e}"


def pytest_approx(expected, rel=1e-4, abs=None):
    return _ApproxSingle(expected, rel=rel, abs=abs)


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback
    tests = [
        TestAccumRecordKg(),
        TestAccumulateMonthly(),
        TestSafeDiv(),
    ]
    fns = [
        test_unit_segments_coverage,
        test_plant_sets_correct,
        test_fy_month_maps_complete,
        test_solar_key_in_blank_row,
    ]
    passed = failed = 0
    for obj in tests:
        for name in [m for m in dir(obj) if m.startswith("test_")]:
            try:
                getattr(obj, name)()
                print(f"  PASS  {type(obj).__name__}.{name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {type(obj).__name__}.{name}: {exc}")
                traceback.print_exc()
                failed += 1
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {fn.__name__}: {exc}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed + failed} tests: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
