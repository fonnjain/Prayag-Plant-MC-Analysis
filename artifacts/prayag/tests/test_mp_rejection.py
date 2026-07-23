"""Tests for mp_rejection — parsers and query helpers.

All sheet reads are mocked so no network access is required.
All DB calls are mocked so no database is required.
"""
import sys
import os
import datetime
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import mp_rejection as rej

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# R11: Report-11 pipe production journal
R11_HEADER = [
    "DATE", "MACHINE NO.", "TYPES", "Pcs", "WEIGHT",
    "Ideal Weight (KG)", "FC", "ACTUAL WT (KG)"
]

# Use a realistic date format (_long_date_day handles "01-Jun-26")
_DATE = "01-Jun-26"
_DATE2 = "02-Jun-26"


def _r11_row(date, mc, typ, weight, rej_wt):
    return [date, mc, typ, "", str(weight), "", "", str(rej_wt)]


def _r11_vals(data_rows):
    return [R11_HEADER] + data_rows


# R12: Report-12 moulding production journal
R12_HEADER = [
    "DATE", "MOULDING MACHINE", "Pcs", "WT IN KGS", "ACTUAL REJECTION WEIGHT"
]


def _r12_row(date, mc, pcs, wt, rej_wt):
    return [date, mc, str(pcs), str(wt), str(rej_wt)]


def _r12_vals(data_rows):
    return [R12_HEADER] + data_rows


# DB mock factory
def _db_mock(fetchall=None, fetchone=None):
    """Return (mock_conn, mock_cur) ready for `with store._conn() as conn, conn.cursor() as cur:`."""
    mock_cur = MagicMock()
    mock_cur.fetchall.return_value = fetchall if fetchall is not None else []
    mock_cur.fetchone.return_value = fetchone  # None is a valid sentinel

    mock_cur_ctx = MagicMock()
    mock_cur_ctx.__enter__ = lambda s: mock_cur
    mock_cur_ctx.__exit__ = MagicMock(return_value=False)

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.cursor.return_value = mock_cur_ctx
    return mock_conn, mock_cur


# ─── _parse_r11_by_type ──────────────────────────────────────────────────────

class TestParseR11ByType(unittest.TestCase):

    def test_empty_returns_empty(self):
        self.assertEqual(rej._parse_r11_by_type([], "2026-04"), {})

    def test_no_header_returns_empty(self):
        self.assertEqual(rej._parse_r11_by_type([["FOO", "BAR", "BAZ"]], "2026-04"), {})

    def test_basic_aggregation(self):
        rows = [
            _r11_row(_DATE,  "PIPE M/C - 1", "CPVC", 100, 10),
            _r11_row(_DATE2, "PIPE M/C - 1", "CPVC", 200, 15),
            _r11_row(_DATE,  "PIPE M/C - 2", "UPVC", 300, 20),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        self.assertAlmostEqual(result["CPVC"]["out"], 300.0)
        self.assertAlmostEqual(result["CPVC"]["rej"], 25.0)
        self.assertAlmostEqual(result["UPVC"]["out"], 300.0)
        self.assertAlmostEqual(result["UPVC"]["rej"], 20.0)

    def test_total_machine_rows_skipped(self):
        rows = [
            _r11_row(_DATE, "PIPE M/C - 1", "CPVC", 100, 10),
            _r11_row(_DATE, "PIPE TOTAL",   "CPVC", 500, 50),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        self.assertAlmostEqual(result.get("CPVC", {}).get("out", 0), 100.0)

    def test_multiple_types_separate(self):
        rows = [
            _r11_row(_DATE, "PIPE M/C - 1", "CPVC", 100, 12),
            _r11_row(_DATE, "PIPE M/C - 1", "UPVC", 200, 10),
            _r11_row(_DATE, "PIPE M/C - 1", "SWR",  150,  8),
            _r11_row(_DATE, "PIPE M/C - 1", "AGRI",  50,  7),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        for t in ("CPVC", "UPVC", "SWR", "AGRI"):
            self.assertIn(t, result)
        self.assertAlmostEqual(result["CPVC"]["rej"], 12.0)

    def test_zero_output_and_rejection_rows_skipped(self):
        rows = [
            _r11_row(_DATE,  "PIPE M/C - 1", "CPVC",   0,  0),
            _r11_row(_DATE2, "PIPE M/C - 1", "CPVC", 100,  5),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        self.assertAlmostEqual(result["CPVC"]["out"], 100.0)

    def test_blank_type_skipped(self):
        rows = [
            _r11_row(_DATE, "PIPE M/C - 1", "",     100, 10),
            _r11_row(_DATE, "PIPE M/C - 1", "CPVC",  50,  5),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        self.assertNotIn("", result)
        self.assertIn("CPVC", result)

    def test_total_in_type_column_skipped(self):
        rows = [
            _r11_row(_DATE, "PIPE M/C - 1", "TOTAL", 1000, 100),
            _r11_row(_DATE, "PIPE M/C - 1", "CPVC",   100,  10),
        ]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        self.assertNotIn("TOTAL", result)
        self.assertIn("CPVC", result)

    def test_rejection_rate_computable(self):
        rows = [_r11_row(_DATE, "PIPE M/C - 1", "CPVC", 1000, 130)]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-04")
        pct = result["CPVC"]["rej"] / result["CPVC"]["out"] * 100
        self.assertAlmostEqual(pct, 13.0, places=5)

    def test_header_found_after_preamble_rows(self):
        values = [
            ["", "", ""],
            ["Title Row", "Pipe & Fitting", "Report-11"],
            [],
            R11_HEADER,
            _r11_row(_DATE, "PIPE M/C - 1", "UPVC", 500, 50),
        ]
        result = rej._parse_r11_by_type(values, "2026-05")
        self.assertIn("UPVC", result)

    def test_iso_date_format_accepted(self):
        rows = [_r11_row("01/06/2026", "PIPE M/C - 1", "CPVC", 200, 20)]
        result = rej._parse_r11_by_type(_r11_vals(rows), "2026-06")
        self.assertIn("CPVC", result)
        self.assertAlmostEqual(result["CPVC"]["out"], 200.0)


# ─── _parse_r12_stats ────────────────────────────────────────────────────────

class TestParseR12Stats(unittest.TestCase):

    def test_empty_returns_zeros(self):
        out, rej_val, by_mat = rej._parse_r12_stats([], "2026-04")
        self.assertEqual(out, 0.0)
        self.assertEqual(rej_val, 0.0)
        self.assertEqual(by_mat, {})

    def test_no_header_returns_zeros(self):
        out, rej_val, _ = rej._parse_r12_stats([["FOO"]], "2026-04")
        self.assertEqual(out, 0.0)

    def test_basic_aggregation(self):
        rows = [
            _r12_row(_DATE,  "MOULDING MACHINE 1", 10, 200, 2),
            _r12_row(_DATE2, "MOULDING MACHINE 1", 10, 300, 3),
            _r12_row(_DATE,  "MOULDING MACHINE 2",  5, 100, 1),
        ]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(out, 600.0)
        self.assertAlmostEqual(rej_val, 6.0)

    def test_zero_rows_skipped(self):
        rows = [
            _r12_row(_DATE,  "MOULDING MACHINE 1", 0, 0, 0),
            _r12_row(_DATE2, "MOULDING MACHINE 1", 5, 100, 1),
        ]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(out, 100.0)
        self.assertAlmostEqual(rej_val, 1.0)

    def test_rejection_rate(self):
        rows = [_r12_row(_DATE, "MOULDING MACHINE 1", 100, 1000, 10)]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(rej_val / out * 100, 1.0, places=5)

    def test_non_date_rows_skipped(self):
        rows = [
            ["TOTAL", "MOULDING MACHINE 1", "999", "9000", "90"],
            _r12_row(_DATE, "MOULDING MACHINE 1", 10, 200, 2),
        ]
        out, _, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(out, 200.0)


# ─── get_rejection_summary ───────────────────────────────────────────────────

class TestGetRejectionSummary(unittest.TestCase):

    def test_returns_empty_when_db_unavailable(self):
        with patch.object(rej.store, "AVAILABLE", False):
            result = rej.get_rejection_summary("PLUMBING")
        self.assertEqual(result, [])

    def test_pct_computed(self):
        mock_conn, mock_cur = _db_mock(
            fetchall=[("PIPE", "CPVC", 1000, 100, 3)]
        )
        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            result = rej.get_rejection_summary("PLUMBING")
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["pct"], 10.0)

    def test_zero_prod_gives_none_pct(self):
        mock_conn, mock_cur = _db_mock(fetchall=[("PIPE", "CPVC", 0, 0, 1)])
        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            result = rej.get_rejection_summary("PLUMBING")
        self.assertIsNone(result[0]["pct"])

    def test_multiple_rows_ordered(self):
        mock_conn, mock_cur = _db_mock(fetchall=[
            ("FITTING", "PP",   500, 10, 2),
            ("PIPE",    "CPVC", 800, 80, 3),
            ("PIPE",    "UPVC", 400, 20, 3),
        ])
        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            result = rej.get_rejection_summary("PLUMBING")
        self.assertEqual(len(result), 3)


# ─── get_rejection_meta ───────────────────────────────────────────────────────

class TestGetRejectionMeta(unittest.TestCase):

    def test_returns_none_when_db_unavailable(self):
        with patch.object(rej.store, "AVAILABLE", False):
            result = rej.get_rejection_meta("PLUMBING")
        self.assertIsNone(result)

    def test_returns_none_when_no_row(self):
        mock_conn, mock_cur = _db_mock(fetchone=None)
        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            result = rej.get_rejection_meta("PLUMBING")
        self.assertIsNone(result)

    def test_parses_months_list(self):
        import json
        months_json = json.dumps(["2025-04", "2025-05", "2025-06"])
        mock_conn, mock_cur = _db_mock(
            fetchone=(3, 3, months_json, datetime.datetime(2026, 7, 1))
        )
        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            result = rej.get_rejection_meta("PLUMBING")
        self.assertEqual(result["pipe_months"], 3)
        self.assertEqual(len(result["months_covered"]), 3)
        self.assertIn("2025-04", result["months_covered"])


# ─── get_rejection_items ─────────────────────────────────────────────────────

class TestGetRejectionItems(unittest.TestCase):

    def test_empty_when_db_unavailable(self):
        with patch.object(rej.store, "AVAILABLE", False):
            items, total = rej.get_rejection_items("PLUMBING")
        self.assertEqual(items, [])
        self.assertEqual(total, 0)

    def test_pagination_and_pct(self):
        mock_cur = MagicMock()
        # fetchone returns total count, fetchall returns the page rows
        mock_cur.fetchone.return_value = (100,)
        mock_cur.fetchall.return_value = [
            ("PIPE", "PIPE-CPVC", "CPVC", 500, 60, 3),
        ]
        mock_cur_ctx = MagicMock()
        mock_cur_ctx.__enter__ = lambda s: mock_cur
        mock_cur_ctx.__exit__ = MagicMock(return_value=False)
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur_ctx

        with patch.object(rej.store, "AVAILABLE", True), \
             patch.object(rej.store, "_conn", return_value=mock_conn):
            items, total = rej.get_rejection_items("PLUMBING", page=1, per_page=50)
        self.assertEqual(total, 100)
        self.assertEqual(len(items), 1)
        self.assertAlmostEqual(items[0]["pct"], 12.0)


# ─── recompute_rejection — mocked ───────────────────────────────────────────

class TestRecomputeRejection(unittest.TestCase):

    def test_no_db_returns_error_structure(self):
        with patch.object(rej.store, "AVAILABLE", False), \
             patch("sheets.get_raw_values", return_value=[]):
            result = rej.recompute_rejection("PLUMBING")
        self.assertFalse(result["ok"])
        self.assertIn("pipe_months", result)
        self.assertIn("fitting_months", result)

    def test_empty_sheets_no_db_zero_months(self):
        with patch.object(rej.store, "AVAILABLE", False), \
             patch("sheets.get_raw_values", return_value=[]):
            result = rej.recompute_rejection("PLUMBING")
        self.assertEqual(result["pipe_months"], 0)
        self.assertEqual(result["fitting_months"], 0)


if __name__ == "__main__":
    unittest.main()
