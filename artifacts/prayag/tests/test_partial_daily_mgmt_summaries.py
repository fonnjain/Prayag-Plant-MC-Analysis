"""Partial daily-read handling for the three management summary builders.

Covers the shared contract when get_daily_records surfaces failed (plant, ym)
pairs via its sentinel report dict:

  * mgmt_garden_summary.build_garden_summary
  * mgmt_tank_summary.build_tank_summary
  * mgmt_labour_power.get_segment_prod_kg / build_mgmt_report_data

Each builder must extract _failed_pairs, withhold the affected quantitative
figures, expose failed_months / warning state, and refuse to cache a report
built from an incomplete read (R-06 Failure Mode #9).

Run: cd artifacts/prayag && python3 -m pytest -q tests/test_partial_daily_mgmt_summaries.py
"""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mgmt_garden_summary as mgs
import mgmt_tank_summary as mts
import mgmt_labour_power as mlp


# ── Record stub ──────────────────────────────────────────────────────────────

def _rec(plant, period, total_count=0.0, reject_count=0.0, **extra):
    base = dict(
        plant=plant,
        period=period,
        total_count=float(total_count),
        reject_count=float(reject_count),
        actual_hours=extra.pop("actual_hours", 0.0),
        reject_denominator=extra.pop("reject_denominator", 0.0),
        machine=extra.pop("machine", "M/C-1"),
        mould=extra.pop("mould", ""),
        secondary_counts=extra.pop("secondary_counts", {}) or {},
        is_finishing=extra.pop("is_finishing", False),
    )
    base.update(extra)
    return SimpleNamespace(**base)


def _reports_with_failed(pairs):
    """Mimic the sentinel dict get_daily_records appends to its reports list."""
    return [{"_failed_pairs": list(pairs)}]


# ── Garden summary ───────────────────────────────────────────────────────────

class TestGardenPartial:
    def test_failed_garden_month_is_exposed_and_not_cached(self, monkeypatch):
        import sheets

        mgs.invalidate_cache("2627")
        recs = [_rec("GARDEN", "2026-04", total_count=1000.0, reject_count=10.0)]
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms, **_kwargs: (
                recs, _reports_with_failed([("GARDEN", "2026-05")]), []
            ),
        )
        monkeypatch.setattr(sheets, "_get_access_token", lambda: "tok")
        monkeypatch.setattr(mlp, "load_segment_tabs", lambda fy, tok: {})

        data = mgs.build_garden_summary("2627")

        assert data["failed_months"] == ["2026-05"]
        # Result built from a partial read must not be cached.
        assert mgs._cache.get("2627") is None
        mgs.invalidate_cache("2627")

    def test_non_garden_failures_are_ignored(self, monkeypatch):
        import sheets

        mgs.invalidate_cache("2627")
        recs = [_rec("GARDEN", "2026-04", total_count=1000.0)]
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms, **_kwargs: (
                recs, _reports_with_failed([("PTMT", "2026-05")]), []
            ),
        )
        monkeypatch.setattr(sheets, "_get_access_token", lambda: "tok")
        monkeypatch.setattr(mlp, "load_segment_tabs", lambda fy, tok: {})

        data = mgs.build_garden_summary("2627")

        assert data["failed_months"] == []
        # No failures relevant to us → result IS cached.
        assert mgs._cache.get("2627") is not None
        mgs.invalidate_cache("2627")


# ── Tank summary ─────────────────────────────────────────────────────────────

class TestTankPartial:
    def test_failed_tank_month_exposed_and_divergence_suppressed(self, monkeypatch):
        import sheets

        mts.invalidate_cache("TANK", "2627")
        recs = [
            _rec("TANK", "2026-04", total_count=500_000.0, reject_count=0.0,
                 mould="WCT-2LL-05"),
        ]
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms, **_kwargs: (
                recs, _reports_with_failed([("TANK", "2026-06")]), []
            ),
        )
        monkeypatch.setattr(sheets, "load_report_records", lambda fam: [])

        data = mts.build_tank_summary("TANK", "2627")

        assert data["failed_months"] == ["2026-06"]
        assert mts._cache.get(("TANK", "2627")) is None
        mts.invalidate_cache("TANK", "2627")

    def test_other_plant_failure_does_not_mark_tank_partial(self, monkeypatch):
        import sheets

        mts.invalidate_cache("TANK", "2627")
        recs = [
            _rec("TANK", "2026-04", total_count=500_000.0, mould="WCT-2LL-05"),
        ]
        monkeypatch.setattr(
            sheets, "get_daily_records",
            lambda yms, **_kwargs: (
                recs, _reports_with_failed([("TANK_VN", "2026-06")]), []
            ),
        )
        monkeypatch.setattr(sheets, "load_report_records", lambda fam: [])

        data = mts.build_tank_summary("TANK", "2627")

        assert data["failed_months"] == []
        assert mts._cache.get(("TANK", "2627")) is not None
        mts.invalidate_cache("TANK", "2627")

    def test_divergence_suppressed_for_failed_month(self):
        our = {"2026-06": {"prod": 100.0, "rej": None}}
        sheet = {"2026-06": {"prod": 900.0, "rej": None}}
        # Without suppression this month would produce a large divergence.
        divs = mts._compute_divergences(
            our, sheet, {"2026-06": "JUN'26"}, failed_yms={"2026-06"},
        )
        assert divs == []


# ── Labour power (Report 1) ──────────────────────────────────────────────────

class TestLabourPartial:
    def test_segment_prod_withholds_failed_plant_month(self, monkeypatch):
        import sheets
        import costing_model

        def fake_daily(yms):
            (ym,) = yms
            if ym == "2026-04":
                return (
                    [_rec("GARDEN", "2026-04", total_count=1000.0)],
                    _reports_with_failed([("GARDEN", "2026-04")]),
                    [],
                )
            return ([], [], [])

        monkeypatch.setattr(sheets, "get_daily_records", fake_daily)
        monkeypatch.setattr(costing_model, "get_labour_monthly", lambda *a: [])

        cards = mlp.get_segment_prod_kg("2627", through_ym="2026-04")

        # Even though a positive figure was accumulated, the failed pair forces
        # the cell to None so the partial month never looks like a real value.
        assert cards["Garden Pipe"]["2026-04"] is None
        assert getattr(cards, "failed_pairs", None) == [("GARDEN", "2026-04")]

    def test_segment_prod_keeps_good_month(self, monkeypatch):
        import sheets
        import costing_model

        def fake_daily(yms):
            (ym,) = yms
            if ym == "2026-04":
                return ([_rec("GARDEN", "2026-04", total_count=1000.0)], [], [])
            return ([], [], [])

        monkeypatch.setattr(sheets, "get_daily_records", fake_daily)
        monkeypatch.setattr(costing_model, "get_labour_monthly", lambda *a: [])

        cards = mlp.get_segment_prod_kg("2627", through_ym="2026-04")

        assert cards["Garden Pipe"]["2026-04"] == 1000.0
        assert getattr(cards, "failed_pairs", None) == []

    def test_partial_report_is_not_cached(self, monkeypatch):
        import sheets

        mlp._cache.clear()
        monkeypatch.setattr(sheets, "_get_access_token", lambda: "tok")
        monkeypatch.setattr(mlp, "load_segment_tabs", lambda fy, tok: {})
        monkeypatch.setattr(
            mlp, "get_segment_prod_kg",
            lambda fy, through_ym=None: mlp._SegmentProdKg(
                {}, failed_pairs=[("GARDEN", "2026-05")],
            ),
        )
        # Part B loader surfaces the same partial warning shape.
        monkeypatch.setattr(
            mlp, "_load_part_b_daily_totals",
            lambda fy, yms: mlp._PartBDailyTotals(
                {}, partial_warnings=["GARDEN 2026-05: daily source ..."],
            ),
        )

        data = mlp.build_mgmt_report_data("2627")

        assert data["daily_partial_warnings"]
        assert any("GARDEN 2026-05" in w for w in data["daily_partial_warnings"])
        # Partial output must NOT be cached — next request retries fresh.
        assert mlp._cache.get(("2627", None)) is None
        mlp._cache.clear()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
