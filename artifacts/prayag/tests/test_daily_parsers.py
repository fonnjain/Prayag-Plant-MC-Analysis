"""Offline unit tests for the daily ingestion path added for sub-monthly windows.

Covers the long (row-per-date) parser used by PIPE ``Report-11`` and Moulding
``Report-12``, plus the machine-name join key ``_mc_key`` used to map daily
labels onto the monthly roster. Pure / no network — fixture rows only.

Run: cd artifacts/prayag && python3 -m tests.test_daily_parsers
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_daily_long
from sheets import _mc_key


def test_long_parser_aggregates_machine_day_and_drops_empty():
    # Header + data. M/C-1 appears twice on the same day (two item rows) and
    # must be summed; TOTAL is a subtotal label; M/C-3 produced nothing.
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
    assert ("TOTAL", "2026-06-03") not in by, "subtotal label must be skipped"
    assert ("M/C-3", "2026-06-04") not in by, "zero-production day must not be fabricated"
    print("PASS: long parser sums item rows per machine/day and drops empty/subtotal rows")


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
    assert all(r.actual_hours == 0.0 for r in recs), "no run column → run hours must stay 0, not fabricated"
    assert sum(r.total_count for r in recs) == 700.0
    print("PASS: long parser without a run column emits output and leaves hours zero")


def test_mc_key_joins_main_machines_only():
    # Main extruders map to their number; auxiliary/die rows must NOT mis-join.
    assert _mc_key("M/C-1") == 1
    assert _mc_key("M / C - 12") == 12
    assert _mc_key("MACHINE-3") == 3
    assert _mc_key("SOCKET-2") is None, "auxiliary line must not join onto a numbered machine"
    assert _mc_key("Grinder-1") is None
    assert _mc_key("A02") is None, "mould codes have no roster join"
    print("PASS: _mc_key joins only M/C-n and MACHINE-n, ignoring auxiliary/die rows")


if __name__ == "__main__":
    test_long_parser_aggregates_machine_day_and_drops_empty()
    test_long_parser_without_run_col_is_no_baseline_safe()
    test_mc_key_joins_main_machines_only()
    print("\nAll daily parser/normalization unit tests passed.")
