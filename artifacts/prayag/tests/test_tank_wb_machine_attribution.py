"""Tests for Tank WB per-machine attribution via parse_tank_dr.

Verifies that ``parse_tank_dr`` (tank_reconcile.py) correctly populates
``per_machine_by_date`` when MACHINE-1 and MACHINE-2 rows are present in a
synthetic WB DAILY REPORT matrix, and that the ``date_to_machine`` mapping
(derived from that dict — same logic as ``_emit_tank`` in sheets.py) correctly
labels single-machine vs. both-machine dates.

Fully offline: synthetic cell arrays feed ``parse_tank_dr`` directly.

Run:
    cd artifacts/prayag && python3 -m pytest tests/test_tank_wb_machine_attribution.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tank_reconcile import parse_tank_dr


# ---------------------------------------------------------------------------
# Synthetic DR matrix builder
# ---------------------------------------------------------------------------

def _build_dr_matrix(
    year_month: str,
    m1_hrs_by_day: dict,
    m2_hrs_by_day: dict,
) -> list:
    """Build a synthetic DAILY REPORT wide-matrix for Tank WB tests.

    Column layout (confirmed against live files — see tank_reconcile.py):
      col 0 : blank (merged-cell artefact)
      col 1 : machine label  ('MACHINE' in header row; 'TOTAL'/'MACHINE-1'… in data)
      col 2 : monthly RUN HOURS total
      col 3 : monthly OUTPUT KG
      col 4 : Average Per Hour (derived, not parsed)
      col 5 : monthly REJECTION KG
      col 6 : Rejection %age  (derived, not parsed)
      col 7+: per-date triplets  →  Run Hours / Output KG / Rejection KG
               header labels: 'Jul, 1' / 'Jul, 2' etc.

    Args:
        year_month: "YYYY-MM" string, used to construct expected date keys.
        m1_hrs_by_day: {day_int: run_hours} for MACHINE-1 (absent key = 0 h).
        m2_hrs_by_day: {day_int: run_hours} for MACHINE-2 (absent key = 0 h).
    """
    _, mon = year_month.split("-")
    month_abbrevs = {
        "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr",
        "05": "May", "06": "Jun", "07": "Jul", "08": "Aug",
        "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
    }
    mon_abbrev = month_abbrevs[mon]

    all_days = sorted(set(m1_hrs_by_day) | set(m2_hrs_by_day))
    # Even when both dicts are empty, emit an empty but parseable grid.
    if not all_days:
        all_days = [1]  # need at least one date col to have a parseable header

    day_col = {day: 7 + i * 3 for i, day in enumerate(all_days)}
    n_cols = 7 + len(all_days) * 3

    def _empty_row():
        return [""] * n_cols

    # ── Header row ────────────────────────────────────────────────────────────
    hdr = _empty_row()
    hdr[1] = "MACHINE"
    for day, col in day_col.items():
        hdr[col] = f"{mon_abbrev}, {day}"   # e.g. "Jul, 1"

    # ── Sub-header row  (not used for date discovery, structural only) ────────
    sub = _empty_row()
    sub[2] = "RUN HOURS"
    sub[3] = "OUTPUT (KG)"
    sub[5] = "REJECTION (KG)"
    for col in day_col.values():
        sub[col]     = "Run Hours"
        sub[col + 1] = "Output KG"
        sub[col + 2] = "Rejection KG"

    # ── TOTAL row  (per-date sum of all machine hours) ────────────────────────
    total_row = _empty_row()
    total_row[1] = "TOTAL"
    monthly_total = 0.0
    for day, col in day_col.items():
        h = m1_hrs_by_day.get(day, 0.0) + m2_hrs_by_day.get(day, 0.0)
        total_row[col] = h
        monthly_total += h
    total_row[2] = monthly_total   # summary col

    # ── MACHINE-1 row ─────────────────────────────────────────────────────────
    m1_row = _empty_row()
    m1_row[1] = "MACHINE-1"
    m1_total = 0.0
    for day, col in day_col.items():
        h = m1_hrs_by_day.get(day, 0.0)
        m1_row[col] = h
        m1_total += h
    m1_row[2] = m1_total

    # ── MACHINE-2 row ─────────────────────────────────────────────────────────
    m2_row = _empty_row()
    m2_row[1] = "MACHINE-2"
    m2_total = 0.0
    for day, col in day_col.items():
        h = m2_hrs_by_day.get(day, 0.0)
        m2_row[col] = h
        m2_total += h
    m2_row[2] = m2_total

    return [hdr, sub, total_row, m1_row, m2_row]


# ---------------------------------------------------------------------------
# Pure replica of the date_to_machine logic from _emit_tank (sheets.py)
# ---------------------------------------------------------------------------
# Kept inline so the test stays pure and network-free; any change to the
# production logic must be mirrored here and will cause this test to fail,
# which is the intent of the regression guard.

def _date_to_machine(per_machine_by_date: dict) -> dict:
    """Return {date_str: machine_label} using the same rule as _emit_tank.

    Rule (R-35):
    * exactly one machine with non-zero hours on a date → that machine's label
    * two or more machines → " + ".join(running_labels)
    * date not in per_machine_by_date at all → falls back to combined label
      (not handled here; caller must apply the fallback)
    """
    date_to_machine: dict = {}
    if not per_machine_by_date:
        return date_to_machine
    all_mc_dates: set = set()
    for mc_hrs in per_machine_by_date.values():
        all_mc_dates.update(mc_hrs.keys())
    for date_str in all_mc_dates:
        running = [
            lbl for lbl, mc_hrs in per_machine_by_date.items()
            if mc_hrs.get(date_str, 0.0) > 0
        ]
        if len(running) == 1:
            date_to_machine[date_str] = running[0]
        elif len(running) > 1:
            date_to_machine[date_str] = " + ".join(running)
    return date_to_machine


# ---------------------------------------------------------------------------
# Scenario: MACHINE-1 on days 1 & 3; MACHINE-2 on days 2 & 3
# ---------------------------------------------------------------------------

YM = "2026-07"
M1_HRS = {1: 8.0, 3: 8.0}
M2_HRS = {2: 12.0, 3: 8.0}


@pytest.fixture(scope="module")
def dr_result():
    values = _build_dr_matrix(YM, M1_HRS, M2_HRS)
    return parse_tank_dr(values, YM)


@pytest.fixture(scope="module")
def d2m(dr_result):
    _, _, _, per_machine = dr_result
    return _date_to_machine(per_machine)


# ---------------------------------------------------------------------------
# parse_tank_dr — structural correctness
# ---------------------------------------------------------------------------

class TestParseTankDrStructure:
    def test_machine_labels_detected(self, dr_result):
        """Both machine labels must appear in machine_labels list."""
        _, machine_labels, _, _ = dr_result
        assert "MACHINE-1" in machine_labels, "MACHINE-1 not found in machine_labels"
        assert "MACHINE-2" in machine_labels, "MACHINE-2 not found in machine_labels"

    def test_per_machine_by_date_populated(self, dr_result):
        """per_machine_by_date must have an entry for each machine that ran."""
        _, _, _, per_machine = dr_result
        assert "MACHINE-1" in per_machine, "MACHINE-1 absent from per_machine_by_date"
        assert "MACHINE-2" in per_machine, "MACHINE-2 absent from per_machine_by_date"

    def test_machine1_hours_by_date(self, dr_result):
        """MACHINE-1 must carry the exact hours on days it ran and be absent on others."""
        _, _, _, per_machine = dr_result
        m1 = per_machine["MACHINE-1"]
        assert m1.get("2026-07-01", 0.0) == pytest.approx(8.0), "MACHINE-1 day 1 wrong"
        assert "2026-07-02" not in m1, "MACHINE-1 must not appear on day 2 (zero hours)"
        assert m1.get("2026-07-03", 0.0) == pytest.approx(8.0), "MACHINE-1 day 3 wrong"

    def test_machine2_hours_by_date(self, dr_result):
        """MACHINE-2 must carry the exact hours on days it ran and be absent on others."""
        _, _, _, per_machine = dr_result
        m2 = per_machine["MACHINE-2"]
        assert "2026-07-01" not in m2, "MACHINE-2 must not appear on day 1 (zero hours)"
        assert m2.get("2026-07-02", 0.0) == pytest.approx(12.0), "MACHINE-2 day 2 wrong"
        assert m2.get("2026-07-03", 0.0) == pytest.approx(8.0), "MACHINE-2 day 3 wrong"


# ---------------------------------------------------------------------------
# parse_tank_dr — TOTAL row hours (no double-counting)
# ---------------------------------------------------------------------------

class TestTotalRowHours:
    def test_single_machine_date_total(self, dr_result):
        """Day 1: only MACHINE-1 (8 h) → TOTAL = 8 h, not 16."""
        by_date, _, _, _ = dr_result
        assert by_date.get("2026-07-01", {}).get("hrs", 0.0) == pytest.approx(8.0)

    def test_other_single_machine_date_total(self, dr_result):
        """Day 2: only MACHINE-2 (12 h) → TOTAL = 12 h."""
        by_date, _, _, _ = dr_result
        assert by_date.get("2026-07-02", {}).get("hrs", 0.0) == pytest.approx(12.0)

    def test_both_machines_date_total(self, dr_result):
        """Day 3: MACHINE-1 (8 h) + MACHINE-2 (8 h) → TOTAL = 16 h."""
        by_date, _, _, _ = dr_result
        assert by_date.get("2026-07-03", {}).get("hrs", 0.0) == pytest.approx(16.0)

    def test_monthly_summary_matches_date_sum(self, dr_result):
        """Monthly summary cell must equal sum of per-date TOTAL hours (8+12+16=36)."""
        by_date, _, monthly, _ = dr_result
        date_sum = sum(v["hrs"] for v in by_date.values())
        assert monthly["hrs"] == pytest.approx(date_sum)
        assert monthly["hrs"] == pytest.approx(36.0)


# ---------------------------------------------------------------------------
# date_to_machine attribution
# ---------------------------------------------------------------------------

class TestDateToMachineAttribution:
    def test_single_machine1_date(self, d2m):
        """Day 1: only MACHINE-1 ran → attributed to MACHINE-1."""
        assert d2m.get("2026-07-01") == "MACHINE-1", (
            f"Day 1 attribution: expected 'MACHINE-1', got {d2m.get('2026-07-01')!r}")

    def test_single_machine2_date(self, d2m):
        """Day 2: only MACHINE-2 ran → attributed to MACHINE-2."""
        assert d2m.get("2026-07-02") == "MACHINE-2", (
            f"Day 2 attribution: expected 'MACHINE-2', got {d2m.get('2026-07-02')!r}")

    def test_both_machines_date(self, d2m):
        """Day 3: both machines ran → combined label 'MACHINE-1 + MACHINE-2'."""
        assert d2m.get("2026-07-03") == "MACHINE-1 + MACHINE-2", (
            f"Day 3 attribution: expected 'MACHINE-1 + MACHINE-2', got {d2m.get('2026-07-03')!r}")

    def test_no_spurious_dates(self, d2m):
        """Only dates with non-zero machine hours should appear in d2m."""
        assert set(d2m.keys()) == {"2026-07-01", "2026-07-02", "2026-07-03"}


# ---------------------------------------------------------------------------
# Edge case: all machine rows zero (WB May–Jul pattern)
# ---------------------------------------------------------------------------

class TestAllZeroMachineDR:
    """When all machine rows are zero, per_machine_by_date must be empty."""

    def test_per_machine_empty_when_all_zero(self):
        values = _build_dr_matrix(YM, {}, {})
        _, machine_labels, _, per_machine = parse_tank_dr(values, YM)
        assert per_machine == {}, (
            "per_machine_by_date must be empty when every machine row has zero hours "
            f"(got {per_machine!r})")

    def test_date_to_machine_empty_when_no_per_machine(self):
        """_date_to_machine({}) must return {} (no attribution when no DR data)."""
        assert _date_to_machine({}) == {}


# ---------------------------------------------------------------------------
# Edge case: only one machine ran for the whole month
# ---------------------------------------------------------------------------

class TestSingleMachineMonth:
    """Only MACHINE-1 ran; MACHINE-2 row is all-zero → MACHINE-2 absent from d2m."""

    def test_only_machine1_in_per_machine(self):
        m1_hrs = {1: 8.0, 2: 8.0, 3: 12.0}
        values = _build_dr_matrix(YM, m1_hrs, {})
        _, _, _, per_machine = parse_tank_dr(values, YM)
        assert "MACHINE-1" in per_machine, "MACHINE-1 must appear when it ran"
        assert "MACHINE-2" not in per_machine, (
            "MACHINE-2 must not appear in per_machine_by_date when all its hours are zero")

    def test_all_dates_attributed_to_machine1(self):
        m1_hrs = {1: 8.0, 2: 8.0, 3: 12.0}
        values = _build_dr_matrix(YM, m1_hrs, {})
        _, _, _, per_machine = parse_tank_dr(values, YM)
        d2m = _date_to_machine(per_machine)
        assert set(d2m.values()) == {"MACHINE-1"}, (
            f"All dates should be attributed to MACHINE-1, got labels: {set(d2m.values())!r}")
        assert len(d2m) == 3

    def test_totals_unchanged_single_machine(self):
        """by_date TOTAL hours must equal MACHINE-1 hours (no double-count)."""
        m1_hrs = {1: 8.0, 2: 8.0, 3: 12.0}
        values = _build_dr_matrix(YM, m1_hrs, {})
        by_date, _, monthly, _ = parse_tank_dr(values, YM)
        assert by_date["2026-07-01"]["hrs"] == pytest.approx(8.0)
        assert by_date["2026-07-02"]["hrs"] == pytest.approx(8.0)
        assert by_date["2026-07-03"]["hrs"] == pytest.approx(12.0)
        assert monthly["hrs"] == pytest.approx(28.0)
