"""Offline, committed source fixtures for the audited FY26-27 report figures.

These tests deliberately patch daily loaders. They must never need a Google
Sheets connection to prove the reconciliation and plant-basis contracts.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

PRAYAG_DIR = os.path.join(os.path.dirname(__file__), "..")
if PRAYAG_DIR not in sys.path:
    sys.path.insert(0, PRAYAG_DIR)

import mgmt_labour_power as labour_power
import mgmt_moulding_summary as moulding
import mgmt_pipe_summary as pipe_summary
from metrics import Record, compute_metrics, gross_output, net_output
import pipe_reconcile
import sheets


PIPE_HOURS_BY_MACHINE = (1_982, 855, 1_117, 863, 1_107, 22, 0, 561)
PIPE_REPORT5_KG = 1_277_974.20
PIPE_REPORT11_ONLY_KG = 858.00
PIPE_REPORT11_MATCHED_MAXIMA_KG = 4_467.95
PIPE_RECONCILED_KG = 1_283_300.15

MOULDING_KG = 366_015.39
MOULDING_HOURS = 35_972.0
GARDEN_KG = 262_818.23
HDPE_KG = 23_817.24
HDPE_JULY_MC1_KG = 21_931.28
HDPE_JULY_MC2_KG = 516.76


def _pipe_record(period, machine, hours, output):
    return SimpleNamespace(
        plant="PIPE",
        period=period,
        machine=f"PIPE M/C - {machine}",
        actual_hours=hours,
        total_count=output,
        reject_count=0.0,
        is_finishing=False,
    )


def _stub_pipe_daily_loader(monkeypatch, records):
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "fixture-token")
    monkeypatch.setattr(sheets, "get_daily_records", lambda _months: (records, [], []))
    monkeypatch.setattr(
        sheets,
        "get_records",
        lambda _months: pytest.fail("Pipe fixture must not use annual records"),
    )
    monkeypatch.setattr(sheets, "read_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        pipe_summary, "_read_pipeline_wages", lambda *_args, **_kwargs: None,
    )


def test_pipe_reconciliation_fixture_pins_components_and_summary(monkeypatch):
    """R5, R11-only, and matched-date maxima must compose the Pipe headline."""
    r5_values = (
        200_000.00,
        190_000.00,
        180_000.00,
        170_000.00,
        160_000.00,
        150_000.00,
        120_000.00,
        107_974.20,
    )
    assert sum(r5_values) == PIPE_REPORT5_KG
    assert sum(PIPE_HOURS_BY_MACHINE) == 6_507

    r5 = {
        (machine, "2026-04-01"): {"out": output, "rej": 0.0}
        for machine, output in enumerate(r5_values, start=1)
    }
    r11 = {
        # Same machine-date as R5, but Report-11 is higher by the audited uplift.
        (1, "2026-04-01"): {
            "out": r5_values[0] + PIPE_REPORT11_MATCHED_MAXIMA_KG,
            "rej": 0.0,
            "by_type": {},
        },
        # A Report-11-only row must remain in the monthly result with zero hours.
        (8, "2026-04-02"): {
            "out": PIPE_REPORT11_ONLY_KG,
            "rej": 0.0,
            "by_type": {},
        },
    }
    reconciled, _audit = pipe_reconcile.reconcile(r5, r11)
    reconciled_kg = sum(row["out"] for row in reconciled.values())

    assert reconciled_kg == PIPE_RECONCILED_KG
    assert reconciled_kg == (
        PIPE_REPORT5_KG
        + PIPE_REPORT11_ONLY_KG
        + PIPE_REPORT11_MATCHED_MAXIMA_KG
    )

    records = [
        _pipe_record(
            "2026-04", machine, PIPE_HOURS_BY_MACHINE[machine - 1],
            reconciled[(machine, "2026-04-01")]["out"],
        )
        for machine in range(1, 9)
    ]
    records.append(
        _pipe_record(
            "2026-04-02", 8, 0.0,
            reconciled[(8, "2026-04-02")]["out"],
        )
    )
    _stub_pipe_daily_loader(monkeypatch, records)
    pipe_summary._cache.clear()
    try:
        result = pipe_summary.build_pipe_summary("2627", through_ym="2026-07")
    finally:
        pipe_summary._cache.clear()

    total = result["section2"]["fy2627"][-1]
    assert total["actual_hrs"] == 6_507
    assert total["actual_out_kg"] == PIPE_RECONCILED_KG


def test_moulding_fixture_pins_daily_output_and_report5_hours(monkeypatch):
    """Moulding's Report-12 output and joined Report-5 hours stay exact offline."""
    roster = [{"band": "150", "mould_id": "A05(U-150)", "mc_key": "M/C - 1"}]
    records = [
        sheets.Record(
            grain="daily",
            period="2026-04",
            date="2026-04-01",
            plant="MOULDING",
            machine="MOULDING A05(U-150)",
            actual_hours=MOULDING_HOURS,
            total_count=MOULDING_KG,
            reject_count=0.0,
        )
    ]
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "fixture-token")
    monkeypatch.setattr(sheets, "get_daily_records", lambda _months: (records, [], []))
    monkeypatch.setattr(
        sheets,
        "get_records",
        lambda _months: pytest.fail("Moulding fixture must not use annual records"),
    )
    monkeypatch.setattr(
        sheets,
        "batch_get",
        lambda *_args, **_kwargs: {
            moulding.SUMMARY_TAB: [[1]],
            moulding.SUMMARY1_TAB: [[1]],
        },
    )
    monkeypatch.setattr(moulding, "_parse_summary_roster", lambda _values: (roster, []))
    monkeypatch.setattr(moulding, "_parse_s1_tab", lambda _values: {})
    moulding._cache.clear()
    try:
        result = moulding.build_moulding_summary("2627", through_ym="2026-07")
    finally:
        moulding._cache.clear()

    total = next(row for row in result["section2"]["fy2627"] if row["is_total"])
    assert total["output_kg"] == MOULDING_KG
    assert total["actual_hrs"] == MOULDING_HOURS


def test_garden_and_hdpe_fixture_pins_card_and_part_b_net_totals(monkeypatch):
    """Garden/WB and HDPE block-tab net facts must agree in cards and Part B."""
    records = [
        SimpleNamespace(
            plant="GARDEN", period="2026-04", machine="M/C-1",
            total_count=250_000.00, reject_count=0.0, secondary_counts={},
            is_finishing=False,
        ),
        SimpleNamespace(
            plant="GARDEN_WB", period="2026-04", machine="M/C-1",
            total_count=12_818.23, reject_count=0.0, secondary_counts={},
            is_finishing=False,
        ),
        SimpleNamespace(
            plant="HDPE", period="2026-05", machine="M/C-1",
            total_count=1_369.20, reject_count=0.0, secondary_counts={},
            is_finishing=False,
        ),
        SimpleNamespace(
            plant="HDPE", period="2026-07", machine="M/C-1",
            total_count=HDPE_JULY_MC1_KG, reject_count=0.0, secondary_counts={},
            is_finishing=False,
        ),
        SimpleNamespace(
            plant="HDPE", period="2026-07", machine="M/C-2",
            total_count=HDPE_JULY_MC2_KG, reject_count=0.0, secondary_counts={},
            is_finishing=False,
        ),
    ]

    def daily_loader(months):
        wanted = set(months)
        return ([record for record in records if record.period in wanted], [], [])

    import costing_model

    monkeypatch.setattr(sheets, "get_daily_records", daily_loader)
    monkeypatch.setattr(costing_model, "get_labour_monthly", lambda *_args: [])

    cards = labour_power.get_segment_prod_kg("2627", through_ym="2026-07")
    part_b = labour_power._load_part_b_daily_totals(
        "2627", ["2026-04", "2026-05", "2026-06", "2026-07"],
    )

    assert sum(value or 0.0 for value in cards["Garden Pipe"].values()) == pytest.approx(
        GARDEN_KG, abs=0.000001,
    )
    assert sum(row["net"] for row in part_b["Garden"].values()) == pytest.approx(
        GARDEN_KG, abs=0.000001,
    )
    assert sum(value or 0.0 for value in cards["HDPE Pipe"].values()) == pytest.approx(
        HDPE_KG, abs=0.000001,
    )
    assert sum(row["net"] for row in part_b["HDPE"].values()) == pytest.approx(
        HDPE_KG, abs=0.000001,
    )
    assert cards["HDPE Pipe"]["2026-07"] == pytest.approx(
        HDPE_JULY_MC1_KG + HDPE_JULY_MC2_KG, abs=0.000001,
    )


def _oee_record(plant, total_count, reject_count, *, unit="kg", reject_unit=""):
    """One complete shift-log row so the OEE quality path is exercised offline."""
    return Record(
        grain="daily",
        period="2026-07",
        date="2026-07-01",
        plant=plant,
        machine="M/C-1",
        unit=unit,
        total_count=total_count,
        reject_count=reject_count,
        reject_unit=reject_unit,
        has_oee=True,
        shift_len_min=60.0,
        ideal_rate=200.0,
    )


def test_net_basis_output_and_oee_quality_fixture():
    """Garden output is net: quality must be net/gross, never a false 100%."""
    garden = _oee_record("GARDEN", total_count=100.0, reject_count=5.0)

    assert garden.output_basis == "net"
    assert net_output(garden) == 100.0
    assert gross_output(garden) == 105.0

    result = compute_metrics([garden])
    assert result.good_count == 100.0
    assert result.quality == pytest.approx(100.0 / 105.0)
    # R-28 scope: rejection percentage remains reject / source total_count.
    assert result.rejection_pct == pytest.approx(5.0 / 100.0)


def test_gross_basis_output_and_oee_quality_fixture():
    """PTMT output is gross: quality must be net/gross from the same contract."""
    ptmt = _oee_record("PTMT", total_count=105.0, reject_count=5.0)

    assert ptmt.output_basis == "gross"
    assert net_output(ptmt) == 100.0
    assert gross_output(ptmt) == 105.0

    result = compute_metrics([ptmt])
    assert result.good_count == 100.0
    assert result.quality == pytest.approx(100.0 / 105.0)
    assert result.rejection_pct == pytest.approx(5.0 / 105.0)


def test_unit_mismatch_never_combines_tank_rejection_fixture():
    """R-09: Tank primary litres cannot be combined with kg rejection."""
    tank = _oee_record(
        "TANK", total_count=100.0, reject_count=5.0,
        unit="Ltr", reject_unit="kg",
    )

    assert net_output(tank) == 100.0
    assert gross_output(tank) == 100.0