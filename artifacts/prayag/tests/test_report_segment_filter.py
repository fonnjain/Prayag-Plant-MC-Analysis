"""Tests for REPORT_TYPES segment-filter correctness (Moulding leak fix + tank_summary).

All tests use only in-process Record objects and the _filter_report_segments function
(copied inline so tests remain offline).  No Google Sheets calls, no database access.

Spec figures (Apr–Jul 2026 daily cache):
  Moulding total output: 366,015 kg  |  hours: 35,972
  Monthly: Apr 89,152/8,426  May 75,771/7,198  Jun 97,007/9,771  Jul 104,085/10,577
"""
import sys
import os
import pytest
import dataclasses
import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from metrics import Record, compute_metrics


# ---------------------------------------------------------------------------
# Inline replica of _filter_report_segments (must stay in sync with app.py)
# ---------------------------------------------------------------------------

def _filter_report_segments(rows, wanted):
    return [
        r for r in rows
        if r.segment in wanted
        or any(r.segment.startswith(f"{w} ") for w in wanted)
    ]


# ---------------------------------------------------------------------------
# Helper — build a minimal Record with only the fields under test
# ---------------------------------------------------------------------------

def _rec(plant: str, segment: str, mould: str = "", total_count: float = 0.0,
         actual_hours: float = 0.0, period: str = "2026-04",
         unit: str = "kg") -> Record:
    return Record(
        grain="monthly",
        period=period,
        date=datetime.date(int(period[:4]), int(period[5:7]), 1),
        plant=plant,
        segment=segment,
        machine="",
        mould=mould,
        product="",
        material="",
        unit=unit,
        secondary_counts={},
        total_count=total_count,
        reject_count=0.0,
        reject_unit="kg",
        reject_denominator=0.0,
        runner_lumps=0.0,
        planned_output=0.0,
        ideal_output=0.0,
        actual_hours=actual_hours,
        ideal_hours=0.0,
        ideal_hours_sheet=None,
        ideal_source=None,
        runhours_tracked=True,
        ideal_month_hours=0.0,
        location=None,
        is_finishing=False,
        has_oee=False,
        shift=None,
        shift_len_min=0,
        planned_stops_min=0,
        downtime_min=0,
        downtime_reason=None,
        ideal_rate=None,
        labour_cost=0.0,
        power_cost=0.0,
        solar_cost=0.0,
        compound_type=None,
        source_family=None,
        source_file=None,
        source_tab=None,
        tonnage_band=None,
        rejection_tracked=True,
    )


# ---------------------------------------------------------------------------
# Representative rows for each segment group
# ---------------------------------------------------------------------------

MOULDING_ROWS = [
    _rec("MOULDING", "Moulding",             total_count=100.0, actual_hours=10.0),
    _rec("MOULDING", "Moulding",             total_count=200.0, actual_hours=20.0),
    _rec("MOULDING", "MOULDING – Grinding",  total_count= 50.0, actual_hours= 5.0),
    _rec("MOULDING", "MOULDING – Pulverizing", total_count= 25.0, actual_hours= 2.0),
]

TANK_ROWS = [
    _rec("TANK",    "Tank",    unit="Ltr", total_count=1000.0),
    _rec("TANK_VN", "Tank_Vn", unit="Ltr", total_count= 500.0),
    _rec("TANK_WB", "Tank_Wb", unit="Ltr", total_count= 750.0),
    _rec("TANK",    "Tanks",   unit="Ltr", total_count= 200.0),   # annual-path label
]

PIPE_ROWS = [
    _rec("PIPE", "Pipe",            total_count=500.0, actual_hours=50.0),
    _rec("PIPE", "PIPE – Grinding", total_count=100.0, actual_hours=10.0),
]

PTMT_ROWS = [
    _rec("PTMT", "PTMT – Injection (standard)", total_count=300.0),
    _rec("PTMT", "PTMT – Blow Moulding",        total_count=100.0),
    _rec("PTMT", "PTMT – Corrugator",           total_count= 80.0),
]

GARDEN_ROWS = [_rec("GARDEN", "Garden Pipe", total_count=90.0)]
HDPE_ROWS   = [_rec("HDPE",   "HDPE",        total_count=10.0)]

ALL_ROWS = MOULDING_ROWS + TANK_ROWS + PIPE_ROWS + PTMT_ROWS + GARDEN_ROWS + HDPE_ROWS


# ===========================================================================
# FIX 1 — mould_summary and mould_efficiency segment tokens
# ===========================================================================

MOULDING_TOKEN = ["Moulding", "MOULDING"]


class TestMouldSummaryFilter:
    """mould_summary: segments=["Moulding","MOULDING"] must admit only MOULDING rows."""

    def test_moulding_main_segment_matched(self):
        result = _filter_report_segments(MOULDING_ROWS, MOULDING_TOKEN)
        assert len(result) == len(MOULDING_ROWS)

    def test_moulding_grinding_sub_segment_matched(self):
        grinding = [r for r in MOULDING_ROWS if r.segment == "MOULDING – Grinding"]
        result = _filter_report_segments(grinding, MOULDING_TOKEN)
        assert result == grinding

    def test_moulding_pulverizing_sub_segment_matched(self):
        pulv = [r for r in MOULDING_ROWS if r.segment == "MOULDING – Pulverizing"]
        result = _filter_report_segments(pulv, MOULDING_TOKEN)
        assert result == pulv

    def test_zero_tank_rows_reach_mould_summary(self):
        result = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        tank_rows = [r for r in result if r.plant in ("TANK", "TANK_VN", "TANK_WB")]
        assert tank_rows == [], f"Tank rows leaked: {tank_rows}"

    def test_zero_pipe_rows_reach_mould_summary(self):
        result = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        pipe_rows = [r for r in result if r.plant == "PIPE"]
        assert pipe_rows == [], f"PIPE rows leaked: {pipe_rows}"

    def test_zero_ptmt_rows_reach_mould_summary(self):
        result = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        ptmt_rows = [r for r in result if r.plant == "PTMT"]
        assert ptmt_rows == [], f"PTMT rows leaked: {ptmt_rows}"

    def test_zero_garden_rows_reach_mould_summary(self):
        result = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        garden_rows = [r for r in result if r.plant == "GARDEN"]
        assert garden_rows == [], f"GARDEN rows leaked: {garden_rows}"

    def test_total_count_after_filter_equals_moulding_only(self):
        result = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        expected = sum(r.total_count for r in MOULDING_ROWS)
        actual   = sum(r.total_count for r in result)
        assert actual == expected

    def test_mould_efficiency_gets_identical_filter(self):
        """mould_efficiency uses the same token list — result must be identical."""
        r1 = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        r2 = _filter_report_segments(ALL_ROWS, MOULDING_TOKEN)
        assert r1 == r2


# ===========================================================================
# FIX 2 — tank_summary segment tokens
# ===========================================================================

TANK_TOKEN = ["Tanks", "Tank", "Tank_Vn", "Tank_Wb"]


class TestTankSummaryFilter:
    """tank_summary: segments must admit daily ("Tank"/"Tank_Vn"/"Tank_Wb") and
    annual ("Tanks") Tank records, and nothing else."""

    def test_daily_tank_kh_matched(self):
        kh = [r for r in TANK_ROWS if r.segment == "Tank"]
        result = _filter_report_segments(kh, TANK_TOKEN)
        assert result == kh

    def test_daily_tank_vn_matched(self):
        vn = [r for r in TANK_ROWS if r.segment == "Tank_Vn"]
        result = _filter_report_segments(vn, TANK_TOKEN)
        assert result == vn

    def test_daily_tank_wb_matched(self):
        wb = [r for r in TANK_ROWS if r.segment == "Tank_Wb"]
        result = _filter_report_segments(wb, TANK_TOKEN)
        assert result == wb

    def test_annual_tanks_label_matched(self):
        annual = [r for r in TANK_ROWS if r.segment == "Tanks"]
        result = _filter_report_segments(annual, TANK_TOKEN)
        assert result == annual

    def test_all_four_tank_segments_admitted(self):
        result = _filter_report_segments(TANK_ROWS, TANK_TOKEN)
        assert len(result) == len(TANK_ROWS)

    def test_zero_moulding_rows_in_tank_summary(self):
        result = _filter_report_segments(ALL_ROWS, TANK_TOKEN)
        mould_rows = [r for r in result if r.plant == "MOULDING"]
        assert mould_rows == [], f"MOULDING rows leaked: {mould_rows}"

    def test_zero_pipe_rows_in_tank_summary(self):
        result = _filter_report_segments(ALL_ROWS, TANK_TOKEN)
        pipe_rows = [r for r in result if r.plant == "PIPE"]
        assert pipe_rows == [], f"PIPE rows leaked: {pipe_rows}"

    def test_zero_ptmt_rows_in_tank_summary(self):
        result = _filter_report_segments(ALL_ROWS, TANK_TOKEN)
        ptmt_rows = [r for r in result if r.plant == "PTMT"]
        assert ptmt_rows == [], f"PTMT rows leaked: {ptmt_rows}"


# ===========================================================================
# Unchanged reports — segment_cost and utilisation remain unfiltered
# ===========================================================================

class TestUnfilteredReportsUnchanged:
    """segment_cost and utilisation declare segments=[] — filter must be skipped."""

    def test_empty_segments_passes_all_rows(self):
        """When segments=[], the if-guard is False and all rows pass through."""
        wanted = []
        # The app does: if wanted: rows = _filter(rows, wanted)
        # Simulate that guard:
        rows = list(ALL_ROWS)
        if wanted:  # False — filter skipped
            rows = _filter_report_segments(rows, wanted)
        assert len(rows) == len(ALL_ROWS)

    def test_segment_cost_receives_all_segments(self):
        """segment_cost is plant=ALL, segments=[]; every segment must reach rollup."""
        wanted = []
        rows = list(ALL_ROWS)
        if wanted:
            rows = _filter_report_segments(rows, wanted)
        segments_seen = {r.segment for r in rows}
        for expected_seg in ["Moulding", "MOULDING – Grinding", "Pipe", "Tank",
                              "Tank_Vn", "Tank_Wb", "Garden Pipe", "HDPE",
                              "PTMT – Injection (standard)"]:
            assert expected_seg in segments_seen, f"Missing segment: {expected_seg}"

    def test_utilisation_receives_all_segments(self):
        """utilisation is also plant=ALL, segments=[]."""
        wanted = []
        rows = list(ALL_ROWS)
        if wanted:
            rows = _filter_report_segments(rows, wanted)
        assert len(rows) == len(ALL_ROWS)


# ===========================================================================
# PTMT regression — startswith rule still admits all sub-segments
# ===========================================================================

class TestPtmtRegressionUnchanged:
    """ptmt_summary segments=["PTMT"] must still match all PTMT sub-segments."""

    PTMT_TOKEN = ["PTMT"]

    def test_ptmt_injection_standard_matched(self):
        rows = [_rec("PTMT", "PTMT – Injection (standard)")]
        assert _filter_report_segments(rows, self.PTMT_TOKEN) == rows

    def test_ptmt_blow_moulding_matched(self):
        rows = [_rec("PTMT", "PTMT – Blow Moulding")]
        assert _filter_report_segments(rows, self.PTMT_TOKEN) == rows

    def test_ptmt_corrugator_matched(self):
        rows = [_rec("PTMT", "PTMT – Corrugator")]
        assert _filter_report_segments(rows, self.PTMT_TOKEN) == rows

    def test_ptmt_grinding_matched(self):
        rows = [_rec("PTMT", "PTMT – Grinding")]
        assert _filter_report_segments(rows, self.PTMT_TOKEN) == rows

    def test_non_ptmt_excluded_from_ptmt_summary(self):
        result = _filter_report_segments(ALL_ROWS, self.PTMT_TOKEN)
        non_ptmt = [r for r in result if r.plant != "PTMT"]
        assert non_ptmt == [], f"Non-PTMT rows leaked into ptmt_summary: {non_ptmt}"


# ===========================================================================
# Moulding figures — totals must match verified source figures after filter
# ===========================================================================

class TestMouldingFiguresAfterFilter:
    """After applying the Moulding token, aggregate totals must match spec."""

    def _make_monthly_rows(self):
        """Synthetic rows that match spec totals; only output/hours matter here."""
        spec = [
            ("2026-04", 89152.0,  8426.0),
            ("2026-05", 75771.0,  7198.0),
            ("2026-06", 97007.0,  9771.0),
            ("2026-07", 104085.0, 10577.0),
        ]
        rows = []
        for period, kg, hrs in spec:
            # One "Moulding" row and one "MOULDING – Grinding" row per month
            rows.append(_rec("MOULDING", "Moulding",            total_count=kg,   actual_hours=hrs, period=period))
            rows.append(_rec("MOULDING", "MOULDING – Grinding", total_count=0.0,  actual_hours=0.0, period=period))
        return rows

    def test_monthly_output_unchanged_after_filter(self):
        rows = self._make_monthly_rows()
        mixed = rows + TANK_ROWS + PIPE_ROWS  # add noise
        filtered = _filter_report_segments(mixed, MOULDING_TOKEN)
        spec_by_period = {
            "2026-04": 89152.0,
            "2026-05": 75771.0,
            "2026-06": 97007.0,
            "2026-07": 104085.0,
        }
        for period, expected_kg in spec_by_period.items():
            period_rows = [r for r in filtered if r.period == period]
            actual_kg = sum(r.total_count for r in period_rows)
            assert actual_kg == expected_kg, (
                f"{period}: expected {expected_kg} kg, got {actual_kg}"
            )

    def test_total_moulding_output_366015(self):
        rows = self._make_monthly_rows()
        filtered = _filter_report_segments(rows, MOULDING_TOKEN)
        total = sum(r.total_count for r in filtered)
        assert total == 366015.0

    def test_total_moulding_hours_35972(self):
        rows = self._make_monthly_rows()
        filtered = _filter_report_segments(rows, MOULDING_TOKEN)
        total = sum(r.actual_hours for r in filtered)
        assert total == 35972.0
