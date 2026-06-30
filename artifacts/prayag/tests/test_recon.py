"""Pure unit tests for recon (standardized daily-first vs summary-grid badge).

The monthly summary grid undercounts for every plant (documented core invariant),
so daily-first exceeding the grid is EXPECTED, not a failure. The only genuine
concern is a cell where daily-first falls SHORT of the grid (a daily data gap).

Covers:
- expected-undercount total alone → info (never a red fail).
- shortfall cell(s) must NOT be downgraded to info even when the total is a
  positive expected undercount (the architect-flagged regression).
- clean match → ok; no grid → honest "recomputed only" info, never a fake 0%.

No network. Run: cd artifacts/prayag && python3 -m pytest tests/test_recon.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import recon


def test_expected_undercount_total_is_info_not_fail():
    # Daily-first far above the grid, every cell at/above grid → expected.
    b = recon.reconcile(
        156899.0, 38950.0,
        rows=[("M/C-1", 100000.0, 25000.0, True), ("M/C-2", 56899.0, 13950.0, True)],
        unit="kg", expect_exceeds=True,
    )
    assert b["status"] == "info"
    assert b["n_flagged"] == 0
    assert "expected" in b["label"].lower()


def test_shortfall_cell_not_downgraded_to_info():
    # Total is a positive expected undercount, but one cell is BELOW grid by >3%.
    # That cell is the only real signal and must surface as a warning, not info.
    b = recon.reconcile(
        120000.0, 90000.0,
        rows=[
            ("M/C-1", 110000.0, 40000.0, True),   # daily >> grid (expected)
            ("M/C-2", 10000.0, 50000.0, True),    # daily << grid (shortfall!)
        ],
        unit="kg", expect_exceeds=True,
    )
    assert b["status"] == "warn", b
    assert b["n_flagged"] == 1
    assert b["flagged"][0] == "M/C-2"
    # The expected-undercount context is still mentioned, but it is a warning.
    assert "below grid" in b["label"].lower()


def test_clean_match_is_ok():
    b = recon.reconcile(
        1000.0, 995.0,
        rows=[("A", 500.0, 500.0, False), ("B", 500.0, 495.0, False)],
        unit="kg", expect_exceeds=False, tol=0.03,
    )
    assert b["status"] == "ok"
    assert b["n_flagged"] == 0


def test_no_grid_is_honest_info_never_fake_zero():
    b = recon.reconcile(
        50000.0, None, unit="Ltr", expect_exceeds=True,
        no_final_note="No annual summary grid is wired for TANK — recomputed only.",
    )
    assert b["status"] == "info"
    assert b["available"] is False
    assert b["final_total"] is None          # never a fabricated 0
    assert "TANK" in b["note"]


def test_genuine_shortfall_total_without_expect_is_fail():
    # No expect_exceeds and daily-first materially below the grid → real mismatch.
    b = recon.reconcile(800.0, 1000.0, unit="kg", expect_exceeds=False)
    assert b["status"] == "fail"


if __name__ == "__main__":
    test_expected_undercount_total_is_info_not_fail()
    test_shortfall_cell_not_downgraded_to_info()
    test_clean_match_is_ok()
    test_no_grid_is_honest_info_never_fake_zero()
    test_genuine_shortfall_total_without_expect_is_fail()
    print("\nALL recon tests passed.")
