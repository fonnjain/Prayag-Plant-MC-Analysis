"""Tests for Phase 2C parsers: parse_maintenance and parse_manpower.

Inline synthetic fixtures designed to match the real sheet layouts:
  PIPE Report-16, PTMT Report-8  → parse_maintenance
  PIPE Report-22 (A/B)           → parse_manpower  plant=PIPE  shift=all
  PTMT Report-6 (A/B/C)         → parse_manpower  plant=PTMT  shift=1st/2nd/3rd

INVARIANT enforced here: PTMT ManpowerRecord.man_hours is always 0.0
(Report-6 is a shift staffing roster, never a production-output record).
"""
import sys
import os
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from parsers import parse_maintenance, parse_manpower
import planning


# ---- helpers ----------------------------------------------------------------

def _pad(row, width=20, fill=""):
    return row + [fill] * max(0, width - len(row))


# =============================================================================
# Maintenance fixtures
# =============================================================================

# PIPE Report-16: pre-header rows 0–1, header at row 2, data from row 3.
# Column layout mirrors the real sheet (header text is the key).
_PIPE_MAINT_HDR = _pad([
    "",
    "Sr.",
    "Machine Name",
    "Make",
    "Date of Purchase",
    "Cost",
    "AMC Applicable",
    "Monthly Preventive Maintenance Required",
    "Monthly Check Points",
    "Spare to be Keep in Stock",
    "",          # blank col K
    "Service Engineer Name",
    "Mobile No.",
    "Service Engineer Location",
    "Lead Time to Reach factory",
])

PIPE_MAINT_VALUES = [
    _pad([]),                  # row 0 — blank preamble
    _pad([]),                  # row 1 — blank preamble
    _PIPE_MAINT_HDR,           # row 2 — header
    _pad(["", "1", "Injection M/C 01", "Battenfeld", "Jan-10", "2500000", "YES",
          "Monthly", "12 pts", "Hydraulic Oil", "", "Rajesh Kumar", "9876543210",
          "Ahmedabad", "2"]),
    _pad(["", "2", "Injection M/C 02", "Haitian", "15-03-2015", "3500000", "NO",
          "Monthly", "15 pts", "Hydraulic Pump", "", "Pradeep Singh", "9876543211",
          "Mumbai", "3"]),
    _pad(["", "3", "Blow Moulding M/C", "Bekum", "Apr-18", "4500000", "YES",
          "Monthly", "10 pts", "Screw Set", "", "Vikram Patel", "9876543212",
          "Surat", "1"]),
]

# PTMT Report-8: same column names, machine name at col 1 (B).
_PTMT_MAINT_HDR = _pad([
    "Sr.",
    "Machine Name",
    "Make",
    "Date of Purchase",
    "Cost",
    "AMC Applicable",
    "Monthly Preventive Maintenance Required",
    "Monthly Check Points",
    "Spare to be Keep in Stock",
    "",
    "Service Engineer Name",
    "Mobile No.",
    "Service Engineer Location",
    "Lead Time to Reach factory",
])

PTMT_MAINT_VALUES = [
    _pad([]),
    _pad([]),
    _PTMT_MAINT_HDR,           # row 2 — header
    _pad(["1", "Injection Moulding 01", "JSW", "Feb-12", "1800000", "YES",
          "Bi-Monthly", "8 pts", "Tie Rod", "", "Anand Mehta", "9876540001",
          "Vadodara", "1.5"]),
    _pad(["2", "Injection Moulding 02", "Haitian", "Jun-16", "2200000", "NO",
          "Monthly", "10 pts", "Hydraulic Filter", "", "Suresh Bhai",
          "9876540002", "Ahmedabad", "2"]),
]


class TestParseMaintenance:

    # ---- PIPE ---------------------------------------------------------------

    def test_pipe_returns_three_records(self):
        recs = parse_maintenance(PIPE_MAINT_VALUES, "PIPE")
        assert len(recs) == 3

    def test_pipe_machine_names_present(self):
        recs = parse_maintenance(PIPE_MAINT_VALUES, "PIPE")
        names = [r.machine for r in recs]
        assert "Injection M/C 01" in names
        assert "Blow Moulding M/C" in names

    def test_pipe_plant_tag(self):
        recs = parse_maintenance(PIPE_MAINT_VALUES, "PIPE")
        assert all(r.plant == "PIPE" for r in recs)

    def test_pipe_amc_yes(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].amc_applicable.upper() in ("YES", "Y")

    def test_pipe_amc_no(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 02"].amc_applicable.upper() in ("NO", "N")

    def test_pipe_cost_numeric(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].cost == 2_500_000.0

    def test_pipe_lead_time(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 02"].service_lead_time_days == 3.0

    def test_pipe_machine_age_over_ten(self):
        """'Jan-10' purchase → age > 10 years from 2026."""
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        age = by["Injection M/C 01"].machine_age_years
        assert age is not None and age > 10.0

    def test_pipe_make_captured(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].make == "Battenfeld"

    def test_pipe_service_engineer(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].service_engineer == "Rajesh Kumar"

    def test_pipe_service_mobile(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].service_mobile == "9876543210"

    def test_pipe_service_location(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert by["Injection M/C 01"].service_location == "Ahmedabad"

    def test_pipe_pm_required_text(self):
        by = {r.machine: r for r in parse_maintenance(PIPE_MAINT_VALUES, "PIPE")}
        assert "Monthly" in by["Injection M/C 01"].pm_required

    # ---- PTMT ---------------------------------------------------------------

    def test_ptmt_returns_two_records(self):
        recs = parse_maintenance(PTMT_MAINT_VALUES, "PTMT")
        assert len(recs) == 2

    def test_ptmt_machine_names(self):
        recs = parse_maintenance(PTMT_MAINT_VALUES, "PTMT")
        names = [r.machine for r in recs]
        assert "Injection Moulding 01" in names

    def test_ptmt_plant_tag(self):
        recs = parse_maintenance(PTMT_MAINT_VALUES, "PTMT")
        assert all(r.plant == "PTMT" for r in recs)

    def test_ptmt_amc_yes(self):
        by = {r.machine: r for r in parse_maintenance(PTMT_MAINT_VALUES, "PTMT")}
        assert by["Injection Moulding 01"].amc_applicable.upper() in ("YES", "Y")

    def test_ptmt_pm_text(self):
        by = {r.machine: r for r in parse_maintenance(PTMT_MAINT_VALUES, "PTMT")}
        assert "Bi-Monthly" in by["Injection Moulding 01"].pm_required

    def test_ptmt_age_over_ten(self):
        by = {r.machine: r for r in parse_maintenance(PTMT_MAINT_VALUES, "PTMT")}
        age = by["Injection Moulding 01"].machine_age_years
        assert age is not None and age > 10.0

    # ---- Edge cases ---------------------------------------------------------

    def test_empty_input(self):
        assert parse_maintenance([], "PIPE") == []

    def test_no_header_row_returns_empty(self):
        bad = [["col1", "col2", "col3"]] * 8
        assert parse_maintenance(bad, "PIPE") == []

    def test_header_echo_rows_skipped(self):
        data = list(PIPE_MAINT_VALUES) + [_pad(["", "", "Machine Name", "Make"])]
        recs = parse_maintenance(data, "PIPE")
        assert all(r.machine.upper() != "MACHINE NAME" for r in recs)

    def test_sparse_rows_skipped(self):
        """Rows with < 3 non-empty cells are silently skipped."""
        data = list(PIPE_MAINT_VALUES) + [_pad(["", "", "X"])]
        recs = parse_maintenance(data, "PIPE")
        # "X" alone has < 3 filled cells → skipped
        assert all(r.machine != "X" for r in recs)


# =============================================================================
# Manpower fixtures
# =============================================================================

def _build_pipe_mp(n_machines=2, dates=("2026-06-01", "2026-06-02")):
    """Construct a synthetic PIPE Report-22 values array.

    Row 0: title row (ignored)
    Row 1: date header — col 0=M/C, col 3=REQUIREMENT OF MANPOWER, col 4+=dates
    Row 2: sub-headers — TOTAL MANPOWER / TOTAL HOURS per date pair
    Row 3+: machine data
    """
    width = 4 + len(dates) * 2

    rows = [_pad(["Prayag Production"], width)]  # row 0

    # Row 1 — date header
    date_hdr = ["M/C", "Explanation", "Name of Employee", "REQUIREMENT OF MANPOWER"]
    for d in dates:
        date_hdr += [d, ""]
    rows.append(_pad(date_hdr, width))

    # Row 2 — sub-headers
    sub = ["", "", "", ""]
    for _ in dates:
        sub += ["TOTAL MANPOWER", "TOTAL HOURS"]
    rows.append(_pad(sub, width))

    # Data rows
    for i in range(n_machines):
        mc = f"Injection M/C {i + 1:02d}"
        req = 4 + i
        row = [mc, "Production", "Operator", str(req)]
        for _ in dates:
            row += [str(req), str(req * 8)]
        rows.append(_pad(row, width))

    return rows


def _build_ptmt_mp(n_machines=2, dates=("2026-06-01", "2026-06-02"), shift="1st"):
    """Construct a synthetic PTMT Report-6 values array.

    Row 0–1: preamble (ignored)
    Row 2: date header — col 2=MOULDING M/C, col 3+=dates
    Row 3: sub-headers — shift-label / Type per date pair
    Row 4+: machine data
    """
    width = 3 + len(dates) * 2

    rows = [
        _pad(["PTMT Mfg"], width),   # row 0
        _pad([], width),              # row 1
    ]

    # Row 2 — date header
    date_hdr = ["", "", "MOULDING M/C"]
    for d in dates:
        date_hdr += [d, ""]
    rows.append(_pad(date_hdr, width))

    # Row 3 — sub-headers
    sub = ["", "", ""]
    for _ in dates:
        sub += [f"{shift} Shift", "Type"]
    rows.append(_pad(sub, width))

    # Data rows
    for i in range(n_machines):
        mc = f"Faucet M/C {i + 1:02d}"
        row = [str(i + 1), "", mc]
        for _ in dates:
            row += [str(3 + i), "P"]
        rows.append(_pad(row, width))

    return rows


class TestParseManpowerPIPE:

    def test_two_machines_two_dates_four_records(self):
        vals = _build_pipe_mp(n_machines=2, dates=("2026-06-01", "2026-06-02"))
        recs = parse_manpower(vals, "PIPE", "all", "2026-06")
        assert len(recs) == 4

    def test_machine_names_present(self):
        recs = parse_manpower(_build_pipe_mp(), "PIPE", "all")
        machines = {r.machine for r in recs}
        assert "Injection M/C 01" in machines
        assert "Injection M/C 02" in machines

    def test_plant_tag(self):
        recs = parse_manpower(_build_pipe_mp(), "PIPE", "all")
        assert all(r.plant == "PIPE" for r in recs)

    def test_shift_is_all(self):
        recs = parse_manpower(_build_pipe_mp(), "PIPE", "all")
        assert all(r.shift == "all" for r in recs)

    def test_actual_manpower_spot_check(self):
        """Machine 1: required=4, actual=4, man_hours=32 for each date."""
        recs = parse_manpower(_build_pipe_mp(n_machines=1, dates=("2026-06-01",)),
                              "PIPE", "all")
        assert len(recs) == 1
        assert recs[0].required_manpower == 4.0
        assert recs[0].actual_manpower == 4.0
        assert recs[0].man_hours == 32.0

    def test_man_hours_populated(self):
        recs = parse_manpower(_build_pipe_mp(), "PIPE", "all")
        assert all(r.man_hours > 0 for r in recs)

    def test_type_flag_empty_for_pipe(self):
        recs = parse_manpower(_build_pipe_mp(), "PIPE", "all")
        assert all(r.type_flag == "" for r in recs)

    def test_date_iso_preserved(self):
        recs = parse_manpower(_build_pipe_mp(dates=("2026-06-15",)), "PIPE", "all")
        assert all(r.date == "2026-06-15" for r in recs)

    def test_ym_filter_excludes_other_month(self):
        recs = parse_manpower(_build_pipe_mp(dates=("2026-06-01",)), "PIPE", "all",
                              ym="2026-05")
        assert recs == []

    def test_ym_filter_passes_correct_month(self):
        recs = parse_manpower(_build_pipe_mp(dates=("2026-06-01",)), "PIPE", "all",
                              ym="2026-06")
        assert len(recs) > 0

    def test_zero_mp_rows_excluded(self):
        """Data row with 0 manpower and 0 hours is omitted from output."""
        vals = _build_pipe_mp(n_machines=1, dates=("2026-06-01",))
        # zero out the data row (row 3): col 4=mp, col 5=hours
        vals[3][4] = "0"
        vals[3][5] = "0"
        recs = parse_manpower(vals, "PIPE", "all")
        assert recs == []

    def test_empty_input(self):
        assert parse_manpower([], "PIPE", "all") == []

    def test_no_mc_header_row_returns_empty(self):
        bad = [["Random", "stuff", "here"]] * 8
        assert parse_manpower(bad, "PIPE", "all") == []

    def test_multiple_dates_all_captured(self):
        dates = ("2026-06-01", "2026-06-02", "2026-06-03")
        recs = parse_manpower(_build_pipe_mp(n_machines=1, dates=dates), "PIPE", "all",
                              ym="2026-06")
        assert len(recs) == 3


class TestParseManpowerPTMT:

    def test_two_machines_two_dates_four_records(self):
        vals = _build_ptmt_mp(n_machines=2, dates=("2026-06-01", "2026-06-02"))
        recs = parse_manpower(vals, "PTMT", "1st", "2026-06")
        assert len(recs) == 4

    def test_machine_names_present(self):
        recs = parse_manpower(_build_ptmt_mp(), "PTMT", "1st")
        machines = {r.machine for r in recs}
        assert "Faucet M/C 01" in machines

    def test_plant_tag(self):
        recs = parse_manpower(_build_ptmt_mp(), "PTMT", "1st")
        assert all(r.plant == "PTMT" for r in recs)

    def test_shift_flag_first(self):
        recs = parse_manpower(_build_ptmt_mp(shift="1st"), "PTMT", "1st")
        assert all(r.shift == "1st" for r in recs)

    def test_shift_flag_second(self):
        recs = parse_manpower(_build_ptmt_mp(shift="2nd"), "PTMT", "2nd")
        assert all(r.shift == "2nd" for r in recs)

    def test_shift_flag_third(self):
        recs = parse_manpower(_build_ptmt_mp(shift="3rd"), "PTMT", "3rd")
        assert all(r.shift == "3rd" for r in recs)

    def test_type_flag_preserved(self):
        """P/C type flag from sub-col of each date pair is stored."""
        recs = parse_manpower(_build_ptmt_mp(), "PTMT", "1st")
        assert all(r.type_flag == "P" for r in recs)

    def test_actual_manpower_spot_check(self):
        """Machine 1 (Faucet M/C 01): count=3, type_flag='P'."""
        recs = parse_manpower(_build_ptmt_mp(n_machines=1, dates=("2026-06-01",)),
                              "PTMT", "1st")
        assert len(recs) == 1
        assert recs[0].actual_manpower == 3.0
        assert recs[0].type_flag == "P"

    def test_man_hours_zero_invariant(self):
        """CRITICAL: PTMT Report-6 is a shift roster — man_hours MUST be 0.

        This guard prevents Report-6 from ever being mistaken for
        a production-output record.
        """
        recs = parse_manpower(_build_ptmt_mp(), "PTMT", "1st")
        assert all(r.man_hours == 0.0 for r in recs), (
            "INVARIANT VIOLATED: PTMT ManpowerRecord.man_hours must be 0.0 "
            "(Report-6 is a staffing roster, not production output)"
        )

    def test_required_manpower_zero_for_ptmt(self):
        """PTMT has no static required-manpower column."""
        recs = parse_manpower(_build_ptmt_mp(), "PTMT", "1st")
        assert all(r.required_manpower == 0.0 for r in recs)

    def test_date_iso_preserved(self):
        recs = parse_manpower(_build_ptmt_mp(dates=("2026-06-10",)), "PTMT", "1st")
        assert all(r.date == "2026-06-10" for r in recs)

    def test_ym_filter_excludes_other_month(self):
        recs = parse_manpower(_build_ptmt_mp(dates=("2026-06-01",)), "PTMT", "1st",
                              ym="2026-05")
        assert recs == []

    def test_ym_filter_passes_correct_month(self):
        recs = parse_manpower(_build_ptmt_mp(dates=("2026-06-01",)), "PTMT", "1st",
                              ym="2026-06")
        assert len(recs) > 0

    def test_empty_input(self):
        assert parse_manpower([], "PTMT", "1st") == []

    def test_no_moulding_mc_header_returns_empty(self):
        bad = [["Random", "stuff", "here"]] * 8
        assert parse_manpower(bad, "PTMT", "1st") == []

    def test_multiple_dates_all_captured(self):
        dates = ("2026-06-01", "2026-06-05", "2026-06-10")
        recs = parse_manpower(_build_ptmt_mp(n_machines=1, dates=dates), "PTMT", "1st",
                              ym="2026-06")
        assert len(recs) == 3


# =============================================================================
# compute_maintenance_metrics unit tests
# =============================================================================

class TestComputeMaintenanceMetrics:

    def _make_rec(self, purchase_date):
        return planning.MaintenanceRecord(
            plant="PIPE", machine="Test M/C", make="Test", purchase_date=purchase_date,
            cost=0.0, amc_applicable="", pm_required="", check_points="", spares="",
            service_engineer="", service_mobile="", service_location="",
            service_lead_time_days=0.0,
        )

    def test_jan_2010_format(self):
        rec = self._make_rec("Jan-10")
        planning.compute_maintenance_metrics(rec)
        assert rec.machine_age_years is not None and rec.machine_age_years > 10.0

    def test_dd_mm_yyyy_format(self):
        rec = self._make_rec("15-03-2015")
        planning.compute_maintenance_metrics(rec)
        assert rec.machine_age_years is not None and rec.machine_age_years > 5.0

    def test_empty_date_leaves_none(self):
        rec = self._make_rec("")
        planning.compute_maintenance_metrics(rec)
        assert rec.machine_age_years is None

    def test_future_date_leaves_none(self):
        rec = self._make_rec("01-01-2090")
        planning.compute_maintenance_metrics(rec)
        # delta_days < 0 so age is not set (guarded by `if delta_days > 0`)
        assert rec.machine_age_years is None

    def test_returns_same_object(self):
        rec = self._make_rec("Jan-10")
        ret = planning.compute_maintenance_metrics(rec)
        assert ret is rec
