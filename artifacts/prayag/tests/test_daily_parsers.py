"""Offline unit tests for the daily ingestion path.

Covers:
- parse_daily_long  (PIPE Report-11 / Moulding Report-12): aggregation,
  subtotal-row filtering (including TOTAL-variant labels like "GRAND TOTAL",
  "M/C-1 TOTAL"), empty-day dropping, no-run-column safety.
- parse_daily_blocks (GARDEN / HDPE per-machine tabs): TOTAL(KG) column
  detection, single-row header band, sub-header band, multi-row aggregation.
- _long_date_day: all date formats that appear in real workbooks (month-name,
  day-first, numeric DD/MM/YYYY, ISO YYYY-MM-DD).
- _mc_key: main-machine join, auxiliary/die rejection.

Pure / no network — fixture rows only.

Run: cd artifacts/prayag && python3 -m tests.test_daily_parsers
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_daily_long, parse_daily_blocks, _long_date_day
from sheets import _mc_key


# ---------------------------------------------------------------------------
# parse_daily_long
# ---------------------------------------------------------------------------

def test_long_parser_aggregates_machine_day_and_drops_empty():
    # Header + data. M/C-1 appears twice on the same day (two item rows) and
    # must be summed; exact "TOTAL" is a subtotal label; M/C-3 produced nothing.
    values = [
        ["DATE", "MACHINE NO.", "Running Hours", "Pcs", "Rejection"],
        ["Jun 1, 2026", "M/C-1", "20", "1,000", "10"],
        ["Jun 1, 2026", "M/C-1", "0", "500", "5"],
        ["Jun 2, 2026", "M/C-2", "22", "2000", "20"],
        ["Jun 3, 2026", "TOTAL", "100", "5000", "50"],
        ["Jun 4, 2026", "M/C-3", "0", "0", "0"],
    ]
    recs = parse_daily_long(
        values,
        plant="PIPE", segment="PIPE", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-11",
        date_col=("eq", "DATE"), machine_col=("contains", "MACHINE"),
        out_col=("eq", "PCS"), run_col=("contains", "RUNNING"),
        rej_col=("contains", "REJECT"),
    )
    by = {(r.machine, r.date): r for r in recs}
    assert len(recs) == 2, f"expected 2 records (TOTAL + empty dropped), got {len(recs)}"
    one = by[("M/C-1", "2026-06-01")]
    assert one.actual_hours == 20.0, one.actual_hours
    assert one.total_count == 1500.0, "two item rows on the same machine/day must sum"
    assert one.reject_count == 15.0, one.reject_count
    assert ("M/C-2", "2026-06-02") in by
    assert ("TOTAL", "2026-06-03") not in by, "exact TOTAL must be skipped"
    assert ("M/C-3", "2026-06-04") not in by, "zero-production day must not be fabricated"
    print("PASS: long parser sums item rows per machine/day and drops empty/subtotal rows")


def test_long_parser_drops_total_variant_labels():
    # The live PIPE workbook has rows whose machine label is "GRAND TOTAL",
    # "M/C-1 TOTAL", "NET TOTAL", etc. — these are month-end summaries that
    # repeat the same output already counted in the detail rows.  All must be
    # skipped, or the plant output is double-counted (+31.8% for PIPE).
    values = [
        ["DATE", "MACHINE NO.", "Running Hours", "Weight", "Rejection"],
        ["Jun 1, 2026", "M/C-1",       "8",   "1000", "10"],
        ["Jun 2, 2026", "M/C-1",       "8",   "1100", "11"],
        # Subtotal rows — should all be excluded
        ["Jun 30, 2026", "GRAND TOTAL",  "0",  "50000", "0"],
        ["Jun 30, 2026", "M/C-1 TOTAL",  "0",  "25000", "0"],
        ["Jun 30, 2026", "NET TOTAL",     "0",  "25000", "0"],
        ["Jun 30, 2026", "TOTAL OUTPUT",  "0",  "50000", "0"],
        ["Jun 30, 2026", "TOTAL",         "0",  "50000", "0"],
    ]
    recs = parse_daily_long(
        values,
        plant="PIPE", segment="PIPE", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-11",
        date_col=("eq", "DATE"), machine_col=("contains", "MACHINE"),
        out_col=("eq", "WEIGHT"), run_col=("contains", "RUNNING"),
        rej_col=("contains", "REJECT"),
    )
    machines = {r.machine for r in recs}
    assert machines == {"M/C-1"}, f"expected only M/C-1, got {machines}"
    total_out = sum(r.total_count for r in recs)
    assert total_out == 2100.0, f"expected 2100 (detail only), got {total_out}"
    print("PASS: long parser drops all TOTAL-variant subtotal labels (GRAND TOTAL, "
          "M/C-1 TOTAL, NET TOTAL, TOTAL OUTPUT)")


def test_long_parser_without_run_col_is_no_baseline_safe():
    # Moulding (Report-12) has no run-hours column and uses mould codes; the
    # parser must still emit output records (run hours stay 0 → no-baseline),
    # prefixing the logical plant name onto each label.
    values = [
        ["DATE", "M/C NO :", "Output", "Rej"],
        ["Jun 1, 2026", "A02", "300", "3"],
        ["Jun 1, 2026", "A03", "400", "4"],
    ]
    recs = parse_daily_long(
        values,
        plant="MOULDING", segment="MOULDING", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-12",
        date_col=("eq", "DATE"), machine_col=("contains", "M/C"),
        out_col=("eq", "OUTPUT"), rej_col=("contains", "REJ"),
        machine_prefix="MOULDING ",
    )
    assert len(recs) == 2, f"expected 2 moulding records, got {len(recs)}"
    machines = sorted(r.machine for r in recs)
    assert machines == ["MOULDING A02", "MOULDING A03"], machines
    assert all(r.actual_hours == 0.0 for r in recs), \
        "no run column → run hours must stay 0, not fabricated"
    assert sum(r.total_count for r in recs) == 700.0
    print("PASS: long parser without a run column emits output and leaves hours zero")


# ---------------------------------------------------------------------------
# parse_daily_blocks (GARDEN / HDPE per-machine tabs)
# ---------------------------------------------------------------------------

def test_blocks_parser_single_row_header_total_kg_column():
    # GARDEN MACHINE tabs: all column names on the same row as DATE.
    # Output column is "TOTAL(KG)" — exact "KG" match would miss it.
    # Data starts immediately on the next row (no sub-header).
    values = [
        ["DATE", "SIZE", "PRODUCTION PIECES", "REJECTION", "TOTAL(KG)", "REJECT KG"],
        ["01/06/2026", "100mm", "200", "5", "1500", "40"],
        ["01/06/2026", "75mm",  "150", "3",  "800", "20"],
        ["02/06/2026", "100mm", "210", "6", "1600", "48"],
        ["",           "",       "",    "",     "",   ""],   # blank — must skip
    ]
    recs = parse_daily_blocks(
        values,
        plant="GARDEN", segment="Garden", unit="kg", year_month="2026-06",
        source_file="f", source_tab="MACHINE 2", machine="GARDEN M/C - 2",
    )
    by_day = {}
    for r in recs:
        by_day[r.date] = by_day.get(r.date, 0.0) + r.total_count
    assert "2026-06-01" in by_day, "day 1 must be present"
    assert by_day["2026-06-01"] == 2300.0, \
        f"two rows on day 1 must sum: expected 2300, got {by_day.get('2026-06-01')}"
    assert by_day.get("2026-06-02") == 1600.0, by_day
    print("PASS: blocks parser handles single-row header with TOTAL(KG) column "
          "and DD/MM/YYYY dates")


def test_blocks_parser_two_row_header_plain_kg():
    # Some GARDEN / HDPE tabs use a sub-header row: DATE row + KG sub-row.
    # Backward-compat: plain "KG" must still be found.
    values = [
        ["DATE", "", "PRODUCTION", "REJECTION"],
        ["",     "", "KG",        "KG"],           # sub-header
        ["Jun 1, 2026", "", "1200", "15"],
        ["Jun 2, 2026", "", "1300", "18"],
    ]
    recs = parse_daily_blocks(
        values,
        plant="HDPE", segment="HDPE", unit="kg", year_month="2026-06",
        source_file="f", source_tab="MACHINE 1", machine="HDPE M/C - 1",
    )
    assert len(recs) == 2, f"expected 2 records, got {len(recs)}"
    assert sum(r.total_count for r in recs) == 2500.0
    assert sum(r.reject_count for r in recs) == 33.0
    print("PASS: blocks parser backward-compat: two-row header with plain KG sub-header")


def test_blocks_parser_no_output_column_returns_empty():
    # If no KG column is present the parser returns [] (parse failure, not
    # "no production") so the caller can report an honest layout error.
    values = [
        ["DATE", "PIECES", "REJECTION"],
        ["Jun 1, 2026", "200", "5"],
    ]
    recs = parse_daily_blocks(
        values,
        plant="GARDEN", segment="Garden", unit="kg", year_month="2026-06",
        source_file="f", source_tab="MACHINE 3", machine="GARDEN M/C - 3",
    )
    assert recs == [], f"expected [] for unrecognised layout, got {recs}"
    print("PASS: blocks parser returns [] (not empty-production) when no KG column found")


# ---------------------------------------------------------------------------
# _long_date_day — date format coverage
# ---------------------------------------------------------------------------

def test_long_date_day_all_formats():
    assert _long_date_day("Jun 1, 2026")  == 1,  "month-name first"
    assert _long_date_day("Jun 15, 2026") == 15, "month-name first, double-digit day"
    assert _long_date_day("01-Jun-26")    == 1,  "day-first with 3-letter month"
    assert _long_date_day("01-Apr-26")    == 1,  "day-first with 3-letter month (Apr)"
    assert _long_date_day("Apr, 1")       == 1,  "month-comma format"
    assert _long_date_day("01/06/2026")   == 1,  "DD/MM/YYYY numeric"
    assert _long_date_day("01-06-2026")   == 1,  "DD-MM-YYYY numeric"
    assert _long_date_day("2026-06-01")   == 1,  "ISO YYYY-MM-DD"
    assert _long_date_day("15/06/2026")   == 15, "DD/MM/YYYY numeric, day 15"
    assert _long_date_day("")             is None, "blank → None"
    assert _long_date_day("TOTAL")        is None, "label → None"
    assert _long_date_day("GRAND TOTAL")  is None, "label → None"
    assert _long_date_day("MAY 2026")     is None, "year-month label → None"
    print("PASS: _long_date_day handles all real-workbook date formats and rejects labels")


# ---------------------------------------------------------------------------
# _mc_key
# ---------------------------------------------------------------------------

def test_mc_key_joins_main_machines_only():
    assert _mc_key("M/C-1") == 1
    assert _mc_key("M / C - 12") == 12
    assert _mc_key("MACHINE-3") == 3
    assert _mc_key("SOCKET-2") is None, "auxiliary line must not join onto a numbered machine"
    assert _mc_key("Grinder-1") is None
    assert _mc_key("A02") is None, "mould codes have no roster join"
    print("PASS: _mc_key joins only M/C-n and MACHINE-n, ignoring auxiliary/die rows")


if __name__ == "__main__":
    test_long_parser_aggregates_machine_day_and_drops_empty()
    test_long_parser_drops_total_variant_labels()
    test_long_parser_without_run_col_is_no_baseline_safe()
    test_blocks_parser_single_row_header_total_kg_column()
    test_blocks_parser_two_row_header_plain_kg()
    test_blocks_parser_no_output_column_returns_empty()
    test_long_date_day_all_formats()
    test_mc_key_joins_main_machines_only()
    print("\nAll daily parser/normalisation unit tests passed.")
