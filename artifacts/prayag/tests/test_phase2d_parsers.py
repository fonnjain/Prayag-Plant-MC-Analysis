"""
Phase 2D fixture-backed parser tests.
No network calls — all data is inline.

Covers:
- parse_yield_report15: per-type kg production/wastage/pulverizer + yield_pct
- parse_yield_report13: daily pcs pipe types + target_pcs from row above header
- parse_yield_report14: daily pcs fittings types (generic detector)
- parse_mixer_batch: batch logs, mixer_availability computed
- parse_toolroom: ~24 job rows, date carry-forward
- parse_wastage: 33 rows, unit preserved, NEVER sum across units
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import parsers
import planning


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _iso(row):
    return row.date


# ===========================================================================
# parse_yield_report15 — Report-15 kg pivot
# ===========================================================================

_R15 = [
    ["", "", "", "", "", "", "", "", "", "", ""],              # row 0  preamble
    ["", "", "", "", "", "", "", "", "", "", ""],              # row 1  preamble
    ["", "", "", "", "", "", "", "", "", "", ""],              # row 2  preamble
    # header row (row 3)
    ["Date",
     "Production CPVC", "Wastage CPVC", "Pulverizer Consumed",
     "Production UPVC", "Wastage UPVC", "Pulverizer Consumed",
     "Production SWR",  "Wastage SWR",  "Pulverizer Consumed",
     "Production AGRI", "Wastage AGRI", "Pulverizer Consumed"],
    # data rows
    ["01-06-2026", 1000.0, 50.0, 20.0, 800.0, 40.0, 15.0, 600.0, 30.0, 10.0, 400.0, 20.0, 8.0],
    ["02-06-2026", 1100.0, 55.0, 22.0, 850.0, 42.0, 16.0, 620.0, 31.0, 11.0, 410.0, 21.0, 8.5],
    ["",           "",    "",    "",    "",    "",    "",   "",    "",   "",    "",    "",   ""],   # blank
]


def test_r15_row_count():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-06")
    # 2 data rows × 4 types = 8 records
    assert len(recs) == 8, f"expected 8, got {len(recs)}"


def test_r15_types():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-06")
    types = {r.type for r in recs}
    assert "CPVC" in types
    assert "UPVC" in types
    assert "SWR"  in types
    assert "AGRI" in types


def test_r15_yield_pct_computed():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-06")
    cpvc = [r for r in recs if r.type == "CPVC" and r.date == "2026-06-01"][0]
    # 1000 / (1000+50) * 100 = 95.24
    assert cpvc.yield_pct is not None
    assert abs(cpvc.yield_pct - (1000.0 / 1050.0 * 100)) < 0.1


def test_r15_pulverizer_captured():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-06")
    cpvc = [r for r in recs if r.type == "CPVC" and r.date == "2026-06-01"][0]
    assert cpvc.pulverizer_consumed_kg == 20.0


def test_r15_source_tag():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-06")
    assert all(r.source == "R15_kg" for r in recs)


def test_r15_ym_filter():
    recs = parsers.parse_yield_report15(_R15, "PIPE", "2026-07")
    assert recs == []


# ===========================================================================
# parse_yield_report13 — Report-13 pipe daily pcs
# ===========================================================================

_R13 = [
    ["", "", "", "", "", "", "", "", "", ""],                  # row 0
    ["", "", "", "", "", "", "", "", "", ""],                  # row 1
    # row 2 = TARGET band (row above header)
    ["", "TARGET",  500.0,  400.0,  300.0,  200.0, 1400.0, "", "", ""],
    # row 3 = header
    ["", "DATE", "CPVC", "UPVC", "SWR", "AGRI", "PER DAY", "ACCUMULATED PROD", "LEFT PRODUCTION", "AVERAGE PER DAY"],
    # data rows
    ["", "01-06-2026", 480.0, 390.0, 290.0, 195.0, 1355.0, 1355.0, 45.0, 1355.0],
    ["", "02-06-2026", 495.0, 405.0, 305.0, 200.0, 1405.0, 2760.0, 40.0, 1380.0],
    ["", "",            "",    "",    "",    "",     "",     "",     "",   ""],    # blank
]


def test_r13_row_count():
    recs = parsers.parse_yield_report13(_R13, "PIPE", "2026-06")
    # 2 data rows × 4 types = 8 records
    assert len(recs) == 8, f"expected 8, got {len(recs)}"


def test_r13_target_captured():
    recs = parsers.parse_yield_report13(_R13, "PIPE", "2026-06")
    cpvc = [r for r in recs if r.type == "CPVC" and r.date == "2026-06-01"][0]
    assert cpvc.target_pcs == 500.0


def test_r13_production_pcs():
    recs = parsers.parse_yield_report13(_R13, "PIPE", "2026-06")
    cpvc = [r for r in recs if r.type == "CPVC" and r.date == "2026-06-01"][0]
    assert cpvc.production_pcs == 480.0


def test_r13_source_tag():
    recs = parsers.parse_yield_report13(_R13, "PIPE", "2026-06")
    assert all(r.source == "R13_pcs" for r in recs)


def test_r13_skips_per_day_col():
    recs = parsers.parse_yield_report13(_R13, "PIPE", "2026-06")
    # "PER_DAY" should NOT be a type column
    types = {r.type for r in recs}
    assert "PER_DAY" not in types
    assert "PER DAY" not in types
    assert "PER" not in types


# ===========================================================================
# parse_yield_report14 — Report-14 fittings daily pcs
# ===========================================================================

_R14 = [
    ["", "", "", "", ""],
    ["", "", "", "", ""],
    # row 2 = TARGET band
    ["", "TARGET", 300.0, 250.0, 1400.0],
    # row 3 = header  (fittings type names)
    ["", "DATE", "UPVC_F", "SWR_F", "PER DAY"],
    # data rows
    ["", "01-06-2026", 280.0, 240.0, 520.0],
    ["", "02-06-2026", 295.0, 255.0, 550.0],
]


def test_r14_row_count():
    recs = parsers.parse_yield_report14(_R14, "PIPE", "2026-06")
    # 2 rows × 2 types (UPVC_F, SWR_F)
    assert len(recs) == 4, f"expected 4, got {len(recs)}"


def test_r14_fittings_types():
    recs = parsers.parse_yield_report14(_R14, "PIPE", "2026-06")
    types = {r.type for r in recs}
    assert "UPVC_F" in types
    assert "SWR_F"  in types


def test_r14_source_tag():
    recs = parsers.parse_yield_report14(_R14, "PIPE", "2026-06")
    assert all(r.source == "R14_pcs" for r in recs)


def test_r14_target_captured():
    recs = parsers.parse_yield_report14(_R14, "PIPE", "2026-06")
    upvc_f = [r for r in recs if r.type == "UPVC_F" and r.date == "2026-06-01"][0]
    assert upvc_f.target_pcs == 300.0


# ===========================================================================
# parse_mixer_batch — Report-5(A) mixer batch log
# ===========================================================================

_MIXER_A = [
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["", "", "", "", "", "", "", "", "", "", ""],
    ["", "", "", "", "", "", "", "", "", "", ""],
    # header row (row 3)
    ["Date", "Batch Type", "Batch Size", "No. of Batches", "Shift",
     "Total Compound", "Batch Cycle Time", "Running Hours", "Breakdown Hours", "Remarks"],
    # data rows
    ["01-06-2026", "CPVC Compound", 100.0, 5.0, "A", 500.0, 30.0, 8.0, 0.5, ""],
    ["02-06-2026", "UPVC Compound", 100.0, 6.0, "B", 600.0, 30.0, 7.5, 1.0, "Minor stop"],
    ["",           "",              "",    "",  "",  "",    "",    "",  "",  ""],   # blank
]


def test_mixer_row_count():
    recs = parsers.parse_mixer_batch(_MIXER_A, "PIPE", "A", "2026-06")
    assert len(recs) == 2, f"expected 2, got {len(recs)}"


def test_mixer_availability():
    recs = parsers.parse_mixer_batch(_MIXER_A, "PIPE", "A", "2026-06")
    r = [x for x in recs if x.date == "2026-06-01"][0]
    # 8.0 / (8.0 + 0.5) = 0.9412
    assert r.mixer_availability is not None
    assert abs(r.mixer_availability - (8.0 / 8.5)) < 0.001


def test_mixer_compound_kg():
    recs = parsers.parse_mixer_batch(_MIXER_A, "PIPE", "A", "2026-06")
    assert recs[0].total_compound_kg == 500.0
    assert recs[1].total_compound_kg == 600.0


def test_mixer_id_assigned():
    recs = parsers.parse_mixer_batch(_MIXER_A, "PIPE", "A", "2026-06")
    assert all(r.mixer_id == "A" for r in recs)


def test_mixer_ym_filter():
    recs = parsers.parse_mixer_batch(_MIXER_A, "PIPE", "A", "2026-07")
    assert recs == []


# ===========================================================================
# parse_toolroom — Report-21 toolroom job log
# ===========================================================================

def _make_toolroom_rows():
    rows = [
        ["", "", "", "", "", "", "", ""],    # row 0 preamble
        ["", "", "", "", "", "", "", ""],    # row 1 preamble
        ["", "", "", "", "", "", "", ""],    # row 2 preamble
        ["", "", "", "", "", "", "", ""],    # row 3 preamble
        # header row (row 4)
        ["DATE", "SR. NO.", "MACHINE NAME", "ITEM NAME", "WORK DETAIL",
         "REMARKS", "TOTAL MANPOWER", "WORKING HOURS"],
    ]
    # 24 data rows — some with date carry-forward
    import datetime
    base = datetime.date(2026, 6, 1)
    for i in range(24):
        date_str = (base + datetime.timedelta(days=i // 3)).strftime("%d-%m-%Y")
        date_cell = date_str if i % 3 == 0 else ""   # carry-forward on 2nd/3rd row
        rows.append([
            date_cell,
            str(i + 1),
            f"Machine-{i+1:02d}",
            f"Part-{i+1}",
            f"Work detail {i+1}",
            "",
            2.0,
            1.5 + (i * 0.1),
        ])
    return rows


def test_toolroom_row_count():
    rows = _make_toolroom_rows()
    recs = parsers.parse_toolroom(rows, "PIPE", "2026-06")
    assert len(recs) == 24, f"expected 24, got {len(recs)}"


def test_toolroom_date_carryforward():
    rows = _make_toolroom_rows()
    recs = parsers.parse_toolroom(rows, "PIPE", "2026-06")
    # rows 0-2 (indices 0,1,2 in recs) should all have the same date
    assert recs[0].date == recs[1].date == recs[2].date


def test_toolroom_working_hours():
    rows = _make_toolroom_rows()
    recs = parsers.parse_toolroom(rows, "PIPE", "2026-06")
    assert recs[0].working_hours == 1.5


def test_toolroom_manpower():
    rows = _make_toolroom_rows()
    recs = parsers.parse_toolroom(rows, "PIPE", "2026-06")
    assert all(r.manpower == 2.0 for r in recs)


# ===========================================================================
# parse_wastage — PTMT Report-10 scrap/wastage master
# ===========================================================================

def _make_wastage_rows():
    rows = [
        ["", "", "", "", "", "", "", ""],    # row 0
        ["", "", "", "", "", "", "", ""],    # row 1
        # header row (row 2)
        ["DEPARTMENT", "WASTE ITEM",
         "RESPONSIBLE PERSON FOR MANAGEMENT",
         "UNIT OF WASTE (KG/PCS/LTR)",
         "AV. WASTE CREATE IN A WEEK",
         "CYCLE OF DISPOSE",
         "RESPONSIBLE PERSON FOR DISPOSE",
         "APROX. SALE VALUE"],
    ]
    # 33 data rows across 3 departments with mixed units
    depts = ["Injection", "Assembly", "Maintenance"]
    units = ["KG", "PCS", "LTR"]
    for i in range(33):
        dept = depts[i % 3] if i % 3 == 0 else ""   # carry-forward
        unit = units[i % 3]
        rows.append([
            dept,
            f"Waste Item {i+1}",
            f"Manager {i+1}",
            unit,
            float(10 + i),
            "Weekly",
            f"Dispose Person {i+1}",
            float(100 * (i+1)),
        ])
    return rows


def test_wastage_row_count():
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    assert len(recs) == 33, f"expected 33, got {len(recs)}"


def test_wastage_unit_preserved():
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    units = {r.unit for r in recs}
    assert "KG"  in units
    assert "PCS" in units
    assert "LTR" in units


def test_wastage_never_sum_across_units():
    """Verify no single record mixes units (unit is a string, not a number)."""
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    for r in recs:
        assert isinstance(r.unit, str), f"unit should be str, got {type(r.unit)}"


def test_wastage_dept_carryforward():
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    # Row 1 (i=1) is dept "" → should carry forward "Injection"
    assert recs[1].department == "Injection"


def test_wastage_approx_sale_value():
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    assert recs[0].approx_sale_value == 100.0


def test_wastage_responsible_person_dispose():
    rows = _make_wastage_rows()
    recs = parsers.parse_wastage(rows, "PTMT")
    # Prefer DISPOSE person
    assert recs[0].responsible_person == "Dispose Person 1"


# ===========================================================================
# compute_yield_metrics — standalone unit tests
# ===========================================================================

def test_compute_yield_metrics_basic():
    r = planning.YieldRecord(
        plant="PIPE", date="2026-06-01", type="CPVC",
        production_kg=1000.0, wastage_kg=50.0, source="R15_kg")
    planning.compute_yield_metrics(r)
    assert r.yield_pct is not None
    assert abs(r.yield_pct - (1000.0 / 1050.0 * 100)) < 0.01


def test_compute_yield_metrics_zero_prod():
    r = planning.YieldRecord(
        plant="PIPE", date="2026-06-01", type="CPVC",
        production_kg=0.0, wastage_kg=50.0, source="R15_kg")
    planning.compute_yield_metrics(r)
    assert r.yield_pct is None


def test_compute_mixer_metrics():
    r = planning.CompoundBatchRecord(
        plant="PIPE", date="2026-06-01", mixer_id="A",
        batch_type="CPVC", batch_size=100.0, num_batches=5.0,
        total_compound_kg=500.0, running_hours=8.0, breakdown_hours=2.0, shift="A")
    planning.compute_mixer_metrics(r)
    assert r.mixer_availability == 0.8


def test_compute_mixer_metrics_zero_total():
    r = planning.CompoundBatchRecord(
        plant="PIPE", date="2026-06-01", mixer_id="A",
        batch_type="CPVC", batch_size=100.0, num_batches=5.0,
        total_compound_kg=500.0, running_hours=0.0, breakdown_hours=0.0, shift="A")
    planning.compute_mixer_metrics(r)
    assert r.mixer_availability is None
