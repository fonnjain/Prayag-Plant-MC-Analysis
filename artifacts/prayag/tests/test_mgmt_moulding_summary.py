"""Regression tests for the daily Moulding / GOM management-report path."""
from __future__ import annotations

import os
import sys

import pytest

PRAYAG_DIR = os.path.join(os.path.dirname(__file__), "..")
if PRAYAG_DIR not in sys.path:
    sys.path.insert(0, PRAYAG_DIR)

import mgmt_gom_summary as gom
import mgmt_moulding_summary as moulding
import sheets


ROSTER = [
    {"band": "150", "mould_id": "A05(U-150)", "mc_key": "M/C - 1"},
    {"band": "200", "mould_id": "B02(C-200)", "mc_key": "M/C - 2"},
]


def _record(period: str, machine: str, *, hours: float, output: float, reject: float = 0.0):
    return sheets.Record(
        grain="daily",
        period=period,
        date=f"{period}-01",
        plant="MOULDING",
        machine=machine,
        actual_hours=hours,
        total_count=output,
        reject_count=reject,
        runner_lumps=2.0,
    )


def test_daily_mould_ids_map_to_roster_and_idle_machines_stay_zero():
    records = [_record("2026-04", "MOULDING A05(U-150)", hours=10, output=100, reject=5)]

    section1 = moulding._build_section1(records, n_months=1, roster=ROSTER)
    mc1, mc2, total = section1["rows"]
    assert mc1["machine"] == "M/C - 1"
    assert mc1["actual_hrs"] == 10
    assert mc1["output_kg"] == 100
    assert mc2["machine"] == "M/C - 2"
    assert mc2["actual_hrs"] == 0
    assert mc2["output_kg"] == 0
    assert total["actual_hrs"] == 10
    assert total["output_kg"] == 100

    bands = moulding._build_section2_fy2627(records, n_months=1, roster=ROSTER)
    assert next(row for row in bands if row["band"] == "150")["output_kg"] == 100

    gom_bands = gom._build_section3(records, ["2026-04"], ROSTER)
    machine = gom_bands["by_band"]["150"]["machine_rows"][0]
    assert machine["global_mc"] == "M/C - 1"
    assert machine["total"]["hrs"] == 10
    assert machine["total"]["gross_kg"] == 105


def test_builder_uses_daily_records_and_excludes_months_after_selected_cutoff(monkeypatch):
    records = [
        _record("2026-04", "MOULDING A05(U-150)", hours=10, output=100),
        _record("2026-05", "MOULDING A05(U-150)", hours=10, output=100),
        _record("2026-06", "MOULDING A05(U-150)", hours=10, output=100),
        _record("2026-07", "MOULDING A05(U-150)", hours=10, output=100),
        _record("2026-08", "MOULDING A05(U-150)", hours=999, output=9_999),
    ]
    seen_months = []
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "test-token")
    monkeypatch.setattr(
        sheets,
        "get_daily_records",
        lambda months: (seen_months.append(months) or (records, [], [])),
    )
    monkeypatch.setattr(
        sheets,
        "get_records",
        lambda _months: pytest.fail("Moulding summary must not read annual records"),
    )
    monkeypatch.setattr(
        sheets,
        "batch_get",
        lambda *_args, **_kwargs: {moulding.SUMMARY_TAB: [[1]], moulding.SUMMARY1_TAB: [[1]]},
    )
    monkeypatch.setattr(moulding, "_parse_summary_roster", lambda _values: (ROSTER, []))
    monkeypatch.setattr(moulding, "_parse_s1_tab", lambda _values: {})
    moulding._cache.clear()

    try:
        result = moulding.build_moulding_summary("2627", through_ym="2026-07")
    finally:
        moulding._cache.clear()

    total = next(row for row in result["section1"]["rows"] if row["is_total"])
    assert seen_months == [["2026-04", "2026-05", "2026-06", "2026-07"]]
    assert result["through_ym"] == "2026-07"
    assert result["section1"]["n_months"] == 4
    assert total["actual_hrs"] == 40
    assert total["output_kg"] == 400