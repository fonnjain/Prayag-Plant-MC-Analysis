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

from parsers import parse_daily_long, parse_daily_blocks, parse_daily_matrix, _long_date_day
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


def test_blocks_parser_picks_total_kg_over_per_metre_and_consumption_decoys():
    # The REAL HDPE/GARDEN layout has TWO decoy columns that also say "KG":
    #   - a per-metre weight column  (header "KG"  / sub "MTR", e.g. 0.188)
    #   - a raw-material consumption (header "RP CONSUMPTION" / sub "KG")
    # The true output is the cumulative column (header "TOTAL" / sub "KG").
    # Grabbing the first "KG" picked the per-metre decoy and collapsed HDPE
    # May from 1,369 KG to ~1 and GARDEN to a tiny fraction of its real output.
    hdpe = [
        ["HDPE PIPE PRODUCTION REPORTS"],
        [],
        ["MACHINE NO : 001"],
        ["DATE", "SIZE", "LENGTH", "NOS", "TOTAL", "KG", "TOTAL", "TOTAL"],
        ["",     "",     "MTR",    "PCS", "MTR",   "MTR", "REJECTION", "KG"],
        ["May 1, 2026", "PE100PN8DN32", "300", "14", "4200", "0.188", "", "789.60"],
        ["May 1, 2026", "PE100PN10DN32", "300", "3", "900", "0.23", "", "207.00"],
        ["May 2, 2026", "PE100PN10DN25", "300", "3", "900", "0.144", "5", "129.60"],
    ]
    recs = parse_daily_blocks(
        hdpe,
        plant="HDPE", segment="HDPE", unit="kg", year_month="2026-05",
        source_file="f", source_tab="MACHINE 1", machine="HDPE M/C - 1",
    )
    total = sum(r.total_count for r in recs)
    assert abs(total - (789.60 + 207.00 + 129.60)) < 1e-6, \
        f"output must read the TOTAL/KG column (got {total}, expected 1126.2)"
    rej = sum(r.reject_count for r in recs)
    assert rej == 5.0, f"rejection must read the TOTAL/REJECTION column, got {rej}"

    garden = [
        ["GARDEN PIPE PRODUCTION REPORTS"],
        ["MACHINE NO : 001"],
        ["DATE", "CODE", "SIZE", "LENGTH", "NOS", "TOTAL", "KG", "TOTAL", "RP CONSUMPTION"],
        ["",     "",     "",     "MTR",    "PCS", "MTR",   "MTR", "KG",    "KG"],
        ["May 1, 2026", "G1", "20mm", "300", "10", "3000", "0.1", "1500.0", "1600.0"],
        ["May 2, 2026", "G2", "25mm", "300", "8",  "2400", "0.2", "1200.0", "1300.0"],
    ]
    g = parse_daily_blocks(
        garden,
        plant="GARDEN", segment="Garden", unit="kg", year_month="2026-05",
        source_file="f", source_tab="MACHINE 1", machine="GARDEN M/C - 1",
    )
    gtotal = sum(r.total_count for r in g)
    assert abs(gtotal - 2700.0) < 1e-6, \
        f"GARDEN output must be the TOTAL/KG column, not RP CONSUMPTION, got {gtotal}"
    print("PASS: blocks parser selects TOTAL/KG over per-metre KG/MTR and "
          "RP-CONSUMPTION decoys")


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

def test_matrix_mc_header_spec_picks_canonical_over_alias():
    # HDPE "Daily Report" has an alias machine column to the LEFT of the canonical
    # "MACHINE" column. Here the alias header ("M/C NO.") is one the generic
    # heuristic would mis-match — so without a spec the parser would key off the
    # alias and silently break the in-sheet ideal-rate/hours join. With the
    # layout's mc_header_spec=("eq","MACHINE") the canonical column must win.
    values = [
        ["M/C NO.", "MACHINE", "Apr, 1", "", "", "Apr, 2", "", ""],
        ["",        "",        "RUN HRS", "OUTPUT", "REJECT", "RUN HRS", "OUTPUT", "REJECT"],
        ["A1",      "M/C-1",   "8", "1000", "10", "8", "1100", "12"],
        ["A2",      "M/C-2",   "8",  "900",  "5", "8",  "950",  "6"],
    ]
    common = dict(
        plant="HDPE", segment="HDPE", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Daily Report",
    )
    spec_recs = parse_daily_matrix(values, mc_header_spec=("eq", "MACHINE"), **common)
    machines = {r.machine for r in spec_recs}
    assert machines == {"HDPE M/C-1", "HDPE M/C-2"}, \
        f"spec must select canonical MACHINE column, got {machines}"

    # Without the spec, the generic heuristic mis-matches the alias header and
    # keys off the alias column instead — the regression this guards against.
    fallback_recs = parse_daily_matrix(values, **common)
    fb_machines = {r.machine for r in fallback_recs}
    assert fb_machines == {"HDPE A1", "HDPE A2"}, \
        f"fallback heuristic keys off alias header here, got {fb_machines}"
    print("PASS: matrix mc_header_spec selects the canonical MACHINE column over a "
          "decoy alias column the generic heuristic would mis-match")


def test_mc_key_joins_main_machines_only():
    assert _mc_key("M/C-1") == 1
    assert _mc_key("M / C - 12") == 12
    assert _mc_key("MACHINE-3") == 3
    assert _mc_key("SOCKET-2") is None, "auxiliary line must not join onto a numbered machine"
    assert _mc_key("Grinder-1") is None
    assert _mc_key("A02") is None, "mould codes have no roster join"
    print("PASS: _mc_key joins only M/C-n and MACHINE-n, ignoring auxiliary/die rows")


def test_matrix_keeps_reject_only_last_day_when_machine_idle():
    # PTMT/HDPE matrices append ONE monthly "Actual Rejection Weight" column
    # inside the LAST date-group's span, so the whole month's rejection lands on
    # the last day's row. A machine that did NOT run on that last day (run=0,
    # output=0 in the last group) still owns that rejection — the row must be
    # kept, not dropped as an empty day, or its machine-month reject reads 0.
    # This is the PTMT 80-1 May/June bug (Apr worked only because it produced on
    # the last day).
    values = [
        ["MACHINE", "Apr, 1", "", "Apr, 2", "", "", "", ""],
        ["",        "RUN HRS", "OUTPUT", "RUN HRS", "OUTPUT",
         "Wt in Kgs", "Actual Rejection Weight (in Kgs)", "100 % Wastage"],
        # IDLE-1 ran day 1, nothing on the last day, but carries monthly reject.
        ["IDLE-1",  "8", "100", "0", "0", "0", "55.1", "0"],
        # RAN-1 also produced on the last day — already worked before the fix.
        ["RAN-1",   "8", "120", "8", "90", "0", "23.3", "0"],
    ]
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-05",
        source_file="f", source_tab="Report-5",
    )
    from collections import defaultdict
    rej = defaultdict(float); out = defaultdict(float)
    for r in recs:
        rej[r.machine] += r.reject_count
        out[r.machine] += r.total_count
    assert rej["PTMT IDLE-1"] == 55.1, \
        f"idle-on-last-day machine must keep its lumped monthly reject, got {rej['PTMT IDLE-1']}"
    assert out["PTMT IDLE-1"] == 100.0, "output unchanged (only day 1 produced)"
    assert rej["PTMT RAN-1"] == 23.3, rej["PTMT RAN-1"]
    print("PASS: matrix keeps the reject-only last-day row when a machine is idle "
          "on the month's final day")


def test_matrix_no_reject_column_does_not_fabricate_zero_rows():
    # A group with NO reject column (rej_c=-1 → rej=0) must still drop a fully
    # empty day — the fix only retains rows whose rejection is genuinely non-zero.
    values = [
        ["MACHINE", "Apr, 1", "", "Apr, 2", ""],
        ["",        "RUN HRS", "OUTPUT", "RUN HRS", "OUTPUT"],
        ["M/C-1",   "8", "100", "0", "0"],   # day 2 fully empty → dropped
    ]
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Report-5",
    )
    dates = {r.date for r in recs}
    assert dates == {"2026-04-01"}, f"empty day must still be dropped, got {dates}"
    print("PASS: matrix still drops fully-empty days when no reject column exists")


if __name__ == "__main__":
    test_long_parser_aggregates_machine_day_and_drops_empty()
    test_long_parser_drops_total_variant_labels()
    test_long_parser_without_run_col_is_no_baseline_safe()
    test_blocks_parser_single_row_header_total_kg_column()
    test_blocks_parser_two_row_header_plain_kg()
    test_blocks_parser_picks_total_kg_over_per_metre_and_consumption_decoys()
    test_blocks_parser_no_output_column_returns_empty()
    test_long_date_day_all_formats()
    test_matrix_mc_header_spec_picks_canonical_over_alias()
    test_mc_key_joins_main_machines_only()
    test_matrix_keeps_reject_only_last_day_when_machine_idle()
    test_matrix_no_reject_column_does_not_fabricate_zero_rows()
    print("\nAll daily parser/normalisation unit tests passed.")
