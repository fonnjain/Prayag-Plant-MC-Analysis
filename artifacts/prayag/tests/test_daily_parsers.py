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

from parsers import (
    parse_daily_long, parse_daily_blocks, parse_daily_matrix,
    parse_mc_detail, parse_tank_prod, _long_date_day,
)
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


def test_long_parser_shifted_header_band_still_finds_rejection():
    # Report-12 Jul-2026 layout quirk: the row-label headers (DATE / Moulding
    # Machine) shifted DOWN into the sub-row while measure headers (Actual
    # Rejection Weight) stayed one row ABOVE the DATE row. The parser must
    # still find the rejection column by scanning that row too — otherwise
    # rejection silently parses as 0 while output still reads correctly.
    values = [
        ["", "", "Output Production", "", "Actual Rejection Weight (in Kgs)"],
        ["DATE", "Moulding Machine", " Pc ", " Wt in Kgs ", ""],
        ["Jul 1, 2026", "A02(U-150)", "1800", "160.20", "1.78"],
        ["Jul 1, 2026", "A03(U-150)", "2290", "76.72", "0.74"],
    ]
    recs = parse_daily_long(
        values,
        plant="MOULDING", segment="MOULDING", unit="kg", year_month="2026-07",
        source_file="f", source_tab="Report-12",
        date_col=("eq", "DATE"), machine_col=("startswith", "MOULDING MACHI"),
        out_col=("contains", "WT IN KGS"), rej_col=("contains", "ACTUAL REJECTION"),
        machine_prefix="MOULDING ",
    )
    assert len(recs) == 2, f"expected 2 records, got {len(recs)}"
    by_m = {r.machine: r for r in recs}
    assert by_m["MOULDING A02(U-150)"].reject_count == 1.78
    assert by_m["MOULDING A02(U-150)"].total_count == 160.20
    assert by_m["MOULDING A03(U-150)"].reject_count == 0.74


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
# parse_tank_prod — TANK 'PROD. REPORT' per-item log
# ---------------------------------------------------------------------------

def _tank_values(rows):
    # Mirrors the real TANK 'PROD. REPORT' header (note the "SIZE (LTR.)" column,
    # whose label contains "LTR" and must NOT be read as the litres OUTPUT column).
    header = ["", "DATE", "ITEM CODE", "SIZE (LTR.)", "COLOR", "TANK WEIGHT",
              "PRODUCTION HOURS", "NO. OF CYCLE", "PRODUCTION IN PCS.",
              "PRODUCTION IN LTR.", "REJECTION IN PCS.", "REJECTION IN KG.",
              "PRODUCTION IN KG."]
    return [[""], ["", "DAILY PRODUCTION"], [], header] + rows


def _tank_kwargs():
    return dict(plant="TANK", segment="Tanks (KH)", unit="Ltr",
                year_month="2026-06", source_file="f", source_tab="PROD. REPORT")


def test_tank_size_ltr_column_not_read_as_litres_output():
    # The litres OUTPUT column is blank; production is logged in pcs + kg. The
    # parser must (a) not mistake "SIZE (LTR.)" for the litres column, and
    # (b) fall through to pcs as the primary unit (litres present-but-empty).
    rows = [
        # date, item, size, color, weight, hrs, cycles, PCS, LTR, rejP, rejK, KG
        ["", "Jun 1, 2026", "WT-3LL-10", "", "WHITE", "21", "", "7", "40", "", "", "", "840"],
        ["", "Jun 2, 2026", "WT-3LL-05", "", "WHITE", "11", "", "",  "18", "", "", "", "198"],
    ]
    recs = parse_tank_prod(_tank_values(rows), **_tank_kwargs())
    assert recs, "expected TANK records when pcs/kg carry production"
    assert {r.unit for r in recs} == {"pcs"}, f"primary unit must be pcs, got {[r.unit for r in recs]}"
    assert sum(r.total_count for r in recs) == 58, "pcs total 40+18"
    # kg kept as a secondary display count; litres (blank) is not fabricated.
    kg = sum(r.secondary_counts.get("kg", 0) for r in recs)
    assert kg == 1038, f"kg secondary 840+198, got {kg}"
    assert all("Ltr" not in r.secondary_counts for r in recs), "blank litres must not appear"
    print("PASS: TANK 'SIZE (LTR.)' not read as litres; pcs primary when litres blank")


def test_tank_uses_litres_when_litres_has_data():
    # When the litres OUTPUT column actually carries data, it stays the primary.
    rows = [
        # PCS blank, LTR=500 (col 9), KG=840 — litres carries data so it stays primary
        ["", "Jun 1, 2026", "WT-3LL-10", "", "WHITE", "21", "", "7", "", "500", "", "", "840"],
    ]
    recs = parse_tank_prod(_tank_values(rows), **_tank_kwargs())
    assert recs and {r.unit for r in recs} == {"Ltr"}, "litres must win when it has data"
    assert sum(r.total_count for r in recs) == 500, "litres total"
    print("PASS: TANK keeps litres primary when the litres column carries data")


def test_tank_truly_empty_returns_no_rows():
    # No unit column carries any production → no records (never fabricate).
    rows = [
        ["", "Jun 1, 2026", "WT-3LL-10", "", "WHITE", "21", "", "", "", "", "", ""],
    ]
    recs = parse_tank_prod(_tank_values(rows), **_tank_kwargs())
    assert recs == [], f"expected [] for a blank production log, got {recs}"
    print("PASS: TANK with no production in any unit returns [] (no fabrication)")


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


# ---------------------------------------------------------------------------
# rejection-column-not-found warnings (layout drift must never show a silent 0%)
# ---------------------------------------------------------------------------

def test_long_parser_warns_when_configured_rej_col_not_matched():
    # Report-12 style: rej_col/runner_col are CONFIGURED but the header shifted
    # so neither matches. Output still parses — the note is the only signal.
    values = [
        ["DATE", "MOULDING MACHINE", "Wt in Kgs", "Some Other Col"],
        ["Jun 1, 2026", "A01(NU-200)", "1000", ""],
    ]
    notes = []
    recs = parse_daily_long(
        values,
        plant="MOULDING", segment="MOULDING", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-12",
        machine_col=("startswith", "MOULDING MACHI"),
        out_col=("contains", "WT IN KGS"),
        rej_col=("contains", "ACTUAL REJECTION"),
        runner_col=("startswith", "RUNNER PRODUCE"),
        machine_prefix="MOULDING ", notes=notes,
    )
    assert len(recs) == 1, "output must still parse"
    assert len(notes) == 2, f"expected rejection + runner notes, got {notes}"
    assert any("rejection column" in n and "Report-12" in n and "2026-06" in n
               for n in notes), notes
    assert any("runner column" in n for n in notes), notes
    print("PASS: long parser warns when configured rejection/runner columns "
          "are not matched")


def test_long_parser_no_warning_when_rej_col_matches_or_not_configured():
    values = [
        ["DATE", "MOULDING MACHINE", "Wt in Kgs", "Actual Rejection Weight",
         "Runner Produced"],
        ["Jun 1, 2026", "A01", "1000", "10", "5"],
    ]
    notes = []
    parse_daily_long(
        values,
        plant="MOULDING", segment="MOULDING", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-12",
        machine_col=("startswith", "MOULDING MACHI"),
        out_col=("contains", "WT IN KGS"),
        rej_col=("contains", "ACTUAL REJECTION"),
        runner_col=("startswith", "RUNNER PRODUCE"),
        notes=notes,
    )
    assert notes == [], f"matched columns must not warn: {notes}"
    # A source configured WITHOUT rejection/runner specs is genuinely
    # rejection-free and must stay silent.
    notes2 = []
    parse_daily_long(
        [["DATE", "MACHINE NO.", "PCS"],
         ["Jun 1, 2026", "M/C-1", "100"]],
        plant="PIPE", segment="PIPE", unit="kg", year_month="2026-06",
        source_file="f", source_tab="Report-11",
        machine_col=("contains", "MACHINE"), out_col=("eq", "PCS"),
        notes=notes2,
    )
    assert notes2 == [], f"unconfigured rejection spec must not warn: {notes2}"
    print("PASS: long parser stays silent when columns match or no spec is set")


def test_blocks_parser_warns_when_reject_header_exists_but_unmatched():
    # A REJECT header exists in the sheet's top rows but NOT in the header band
    # the parser matches (DATE row + sub-row) → layout drift, must warn.
    values = [
        ["", "REJECTION (KG)", "", ""],   # stranded above the band
        ["", "", "", ""],
        ["DATE", "SIZE", "TOTAL(KG)", ""],
        ["01/06/2026", "100mm", "1500", ""],
    ]
    notes = []
    recs = parse_daily_blocks(
        values,
        plant="GARDEN", segment="Garden", unit="kg", year_month="2026-06",
        source_file="f", source_tab="MACHINE 2", machine="GARDEN M/C - 2",
        notes=notes,
    )
    assert len(recs) == 1, "output must still parse"
    assert len(notes) == 1 and "rejection" in notes[0] and "MACHINE 2" in notes[0], notes
    print("PASS: blocks parser warns when a reject header exists but is unmatched")


def test_blocks_parser_silent_when_genuinely_rejection_free():
    values = [
        ["DATE", "SIZE", "TOTAL(KG)"],
        ["01/06/2026", "100mm", "1500"],
    ]
    notes = []
    recs = parse_daily_blocks(
        values,
        plant="GARDEN", segment="Garden", unit="kg", year_month="2026-06",
        source_file="f", source_tab="MACHINE 2", machine="GARDEN M/C - 2",
        notes=notes,
    )
    assert len(recs) == 1
    assert notes == [], f"no REJECT header anywhere → must not warn: {notes}"
    print("PASS: blocks parser stays silent on genuinely rejection-free tabs")


def test_mc_detail_warns_when_only_reject_ratio_column_exists():
    # Header mentions rejection only as a stored %age ratio — the absolute
    # rejection column is gone (layout drift) → must warn, but still parse.
    values = [
        ["S.NO", "MACHINE", "MONTH", "IDEAL HOURS", "ACTUAL HOURS",
         "OUTPUT (KG)", "REJECTION %AGE"],
        ["1", "M/C-1", "Apr-26", "500", "400", "9000", "1.5"],
    ]
    notes = []
    recs = parse_mc_detail(
        values, plant="PIPE", segment="PIPE", unit="kg",
        source_file="f", source_tab="M/C-1", notes=notes,
    )
    assert len(recs) == 1, "rows must still parse"
    assert len(notes) == 1 and "rejection" in notes[0] and "M/C-1" in notes[0], notes
    print("PASS: mc_detail warns when only a rejection ratio column exists")


def test_mc_detail_silent_when_rej_matched_or_absent():
    # Absolute rejection column present → silent.
    with_rej = [
        ["S.NO", "MACHINE", "MONTH", "IDEAL HOURS", "ACTUAL HOURS",
         "OUTPUT (KG)", "REJECTION (KG)"],
        ["1", "M/C-1", "Apr-26", "500", "400", "9000", "12"],
    ]
    notes = []
    recs = parse_mc_detail(
        with_rej, plant="PIPE", segment="PIPE", unit="kg",
        source_file="f", source_tab="M/C-1", notes=notes,
    )
    assert len(recs) == 1 and recs[0].reject_count == 12.0
    assert notes == [], notes
    # No REJECT header at all → genuinely rejection-free layout, silent.
    no_rej = [
        ["S.NO", "MACHINE", "MONTH", "IDEAL HOURS", "ACTUAL HOURS",
         "OUTPUT (KG)"],
        ["1", "M/C-1", "Apr-26", "500", "400", "9000"],
    ]
    notes2 = []
    parse_mc_detail(
        no_rej, plant="GARDEN", segment="Garden", unit="kg",
        source_file="f", source_tab="M/C-1", notes=notes2,
    )
    assert notes2 == [], notes2
    print("PASS: mc_detail stays silent when rejection matches or is absent")


# ---------------------------------------------------------------------------
# parse_daily_matrix rejection-column-drift warnings
# ---------------------------------------------------------------------------

def test_matrix_warns_when_reject_header_exists_but_unmatched():
    # The sheet has "REJECTION" in the date-row area (stranded in the machine-label
    # region or shifted out of any date-group span) so no date group picks it up.
    # The parser must warn rather than silently book 0% rejection.
    values = [
        # "REJECTION" sits between the machine col and the first date — outside
        # every date-group span, so rej_c stays -1 for all groups.
        ["MACHINE", "REJECTION", "Apr, 1", "", "Apr, 2", ""],
        ["",         "",          "RUN HRS", "OUTPUT", "RUN HRS", "OUTPUT"],
        ["M/C-1",    "5.0",       "8", "100", "8", "120"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="PIPE", segment="PIPE", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Report-5",
        notes=notes,
    )
    assert len(recs) > 0, "output must still parse"
    assert all(r.reject_count == 0.0 for r in recs), "no rej column → 0"
    assert len(notes) == 1, f"expected exactly one drift note, got {notes}"
    assert "rejection" in notes[0] and "Report-5" in notes[0] and "2026-04" in notes[0], notes
    print("PASS: matrix warns when a rejection header exists but no date group matched it")


def test_matrix_silent_when_genuinely_rejection_free():
    # No REJECT text anywhere in the sheet → genuinely rejection-free, no note.
    values = [
        ["MACHINE", "Apr, 1", "", "Apr, 2", ""],
        ["",        "RUN HRS", "OUTPUT", "RUN HRS", "OUTPUT"],
        ["M/C-1",   "8", "100", "8", "120"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="HDPE", segment="HDPE", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Daily Report",
        notes=notes,
    )
    assert len(recs) > 0
    assert notes == [], f"no REJECT header → must not warn: {notes}"
    print("PASS: matrix stays silent when no rejection header exists anywhere")


def test_matrix_silent_when_reject_column_matched():
    # REJECT sub-header is properly matched in every date group → no note.
    values = [
        ["MACHINE", "Apr, 1", "", "", "Apr, 2", "", ""],
        ["",        "RUN HRS", "OUTPUT", "REJECT", "RUN HRS", "OUTPUT", "REJECT"],
        ["M/C-1",   "8", "100", "5", "8", "120", "3"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Report-5",
        notes=notes,
    )
    assert len(recs) > 0
    assert sum(r.reject_count for r in recs) == 8.0, "rejection must be read"
    assert notes == [], f"matched columns must not warn: {notes}"
    print("PASS: matrix stays silent when rejection sub-columns are properly matched")


# ---------------------------------------------------------------------------
# PTMT Report-5 real-world layout: trailing monthly reject column inside the
# last date-group span.
#
# PTMT Report-5 has NO per-day rejection sub-column.  Instead the LAST date
# group's span is extended so it also contains an "Actual Rejection Weight
# (in Kgs)" column — the whole month's rejection lands on the last day's row.
# The drift detector must:
#   (a) stay silent when that trailing column contains "REJECT" (it is matched),
#   (b) stay silent when NO column contains "REJECT" (genuinely free layout),
#   (c) fire when a "REJECTION" sub-header exists but is stranded outside every
#       date-group's span (e.g. in the machine-label/ideal-hours region).
# ---------------------------------------------------------------------------

def test_matrix_ptmt_trailing_actual_rejection_weight_silent():
    # Real PTMT Report-5 shape: two date groups; the second group's span
    # extends to include "Actual Rejection Weight (in Kgs)" and
    # "100 % Wastage" columns.  The trailing column name contains "REJECT",
    # so the parser assigns rej_c for that group → any_rej_matched is True →
    # no drift note should fire.
    values = [
        # date row
        ["MACHINE",  "Apr, 1",  "",        "Apr, 2",  "",       "",                              ""],
        # sub-header row — only the last group has a REJECT-bearing column
        ["",         "RUN HRS", "OUTPUT",  "RUN HRS", "OUTPUT", "Actual Rejection Weight (in Kgs)", "100 % Wastage"],
        # data row: machine ran day 1, was idle on day 2, but owns monthly reject
        ["80-1",     "8",       "120",     "0",       "0",      "45.5",                          "0"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Report-5",
        notes=notes,
    )
    assert len(recs) > 0, "records must be produced"
    monthly_rej = sum(r.reject_count for r in recs)
    assert monthly_rej == 45.5, \
        f"trailing monthly reject must be read; got {monthly_rej}"
    assert notes == [], \
        f"trailing 'Actual Rejection Weight' is matched → no drift note, got {notes}"
    print("PASS: PTMT trailing 'Actual Rejection Weight' column is matched; "
          "no drift note fires")


def test_matrix_ptmt_trailing_actual_weight_genuinely_free():
    # Same layout, but the trailing column is renamed to "Actual Weight" — no
    # "REJECT" text appears anywhere in the date/sub rows.  The layout is
    # genuinely rejection-free, so the drift detector must stay silent.
    values = [
        ["MACHINE",  "Apr, 1",  "",        "Apr, 2",  "",       "",              ""],
        ["",         "RUN HRS", "OUTPUT",  "RUN HRS", "OUTPUT", "Actual Weight", "100 % Wastage"],
        ["80-1",     "8",       "120",     "0",       "0",      "0",             "0"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-04",
        source_file="f", source_tab="Report-5",
        notes=notes,
    )
    assert len(recs) > 0, "records must still parse"
    assert notes == [], \
        f"no REJECT text anywhere → genuinely free layout → no drift note, got {notes}"
    print("PASS: PTMT with renamed trailing column ('Actual Weight') stays silent — "
          "genuinely rejection-free")


def test_matrix_ptmt_rejection_outside_group_span_warns():
    # "REJECTION" appears in the sub-header row, but at a column that is to the
    # LEFT of the first date group (in the machine-label / ideal-hours region).
    # The parser's group-span loop never visits that column, so rej_c stays -1
    # for every group.  The drift detector scans both header rows for "REJECT"
    # text, finds it, and MUST emit a warning note.
    #
    # This mirrors what would happen if the sheet were restructured so that the
    # monthly rejection column drifted out of the last date-group's span.
    values = [
        # date row — "IDEAL HRS" occupies col 1 (left of the first date group)
        ["MACHINE",  "IDEAL HRS", "Apr, 1",  "",        "Apr, 2",  "",       ""],
        # sub row — "REJECTION" lands at col 1 (outside every date-group span)
        ["",         "REJECTION", "RUN HRS", "OUTPUT",  "RUN HRS", "OUTPUT", "Actual Weight"],
        ["80-1",     "40",        "8",       "120",     "8",       "90",     "0"],
    ]
    notes: list = []
    recs = parse_daily_matrix(
        values,
        plant="PTMT", segment="PTMT", unit="kg", year_month="2026-05",
        source_file="f", source_tab="Report-5",
        notes=notes,
    )
    assert len(recs) > 0, "output must still parse"
    assert all(r.reject_count == 0.0 for r in recs), \
        "no rej column inside any span → reject reads 0"
    assert len(notes) == 1, \
        f"exactly one drift note expected, got {notes}"
    assert "rejection" in notes[0] and "Report-5" in notes[0] and "2026-05" in notes[0], notes
    print("PASS: PTMT 'REJECTION' stranded outside every date-group span "
          "→ drift note fires correctly")


if __name__ == "__main__":
    test_long_parser_aggregates_machine_day_and_drops_empty()
    test_long_parser_drops_total_variant_labels()
    test_long_parser_without_run_col_is_no_baseline_safe()
    test_long_parser_shifted_header_band_still_finds_rejection()
    test_blocks_parser_single_row_header_total_kg_column()
    test_blocks_parser_two_row_header_plain_kg()
    test_blocks_parser_picks_total_kg_over_per_metre_and_consumption_decoys()
    test_blocks_parser_no_output_column_returns_empty()
    test_long_date_day_all_formats()
    test_matrix_mc_header_spec_picks_canonical_over_alias()
    test_mc_key_joins_main_machines_only()
    test_matrix_keeps_reject_only_last_day_when_machine_idle()
    test_matrix_no_reject_column_does_not_fabricate_zero_rows()
    test_long_parser_warns_when_configured_rej_col_not_matched()
    test_long_parser_no_warning_when_rej_col_matches_or_not_configured()
    test_blocks_parser_warns_when_reject_header_exists_but_unmatched()
    test_blocks_parser_silent_when_genuinely_rejection_free()
    test_mc_detail_warns_when_only_reject_ratio_column_exists()
    test_mc_detail_silent_when_rej_matched_or_absent()
    test_matrix_warns_when_reject_header_exists_but_unmatched()
    test_matrix_silent_when_genuinely_rejection_free()
    test_matrix_silent_when_reject_column_matched()
    test_matrix_ptmt_trailing_actual_rejection_weight_silent()
    test_matrix_ptmt_trailing_actual_weight_genuinely_free()
    test_matrix_ptmt_rejection_outside_group_span_warns()
    print("\nAll daily parser/normalisation unit tests passed.")
