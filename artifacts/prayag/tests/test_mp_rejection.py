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


# R12: Report-12 moulding/fitting production journal — FY2025-26 layout
# (header-text-based parser; item column required for row identification)
R12_HEADER = [
    "DATE", "MOULDING MACHINE", "Item", "Material",
    "Weight of Total Production",
    "Actual Rejection Weight (in Kgs)",
]

# FY2026-27 layout: extra "SAP Code" shifts columns; sub-header row added below header
R12_HEADER_FY27 = [
    "DATE", "MOULDING MACHINE", "Item Code", "SAP Code", "Material",
    "Weight of Total Production",
    "Actual Rejection Weight (in Kgs)",
]

# Row for FY2025-26 fixture: (date, mc, wt, rej, item="ITEM001", mat="CPVC")
def _r12_row(date, mc, wt, rej_wt, item="ITEM001", mat="CPVC"):
    return [date, mc, item, mat, str(wt), str(rej_wt)]

# Row for FY2026-27 fixture: one extra column (SAP Code) after Item Code
def _r12_row_fy27(date, mc, wt, rej_wt, item="ITEM001", sap="SAP001", mat="CPVC"):
    return [date, mc, item, sap, mat, str(wt), str(rej_wt)]

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
            _r12_row(_DATE,  "MOULDING MACHINE 1", 200, 2),
            _r12_row(_DATE2, "MOULDING MACHINE 1", 300, 3),
            _r12_row(_DATE,  "MOULDING MACHINE 2", 100, 1),
        ]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(out, 600.0)
        self.assertAlmostEqual(rej_val, 6.0)

    def test_zero_rows_skipped(self):
        rows = [
            _r12_row(_DATE,  "MOULDING MACHINE 1", 0, 0),
            _r12_row(_DATE2, "MOULDING MACHINE 1", 100, 1),
        ]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(out, 100.0)
        self.assertAlmostEqual(rej_val, 1.0)

    def test_rejection_rate(self):
        rows = [_r12_row(_DATE, "MOULDING MACHINE 1", 1000, 10)]
        out, rej_val, _ = rej._parse_r12_stats(_r12_vals(rows), "2026-04")
        self.assertAlmostEqual(rej_val / out * 100, 1.0, places=5)

    def test_non_date_rows_skipped(self):
        rows = [
            ["TOTAL", "MOULDING MACHINE 1", "ITEM001", "CPVC", "9000", "90"],
            _r12_row(_DATE, "MOULDING MACHINE 1", 200, 2),
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


# ─── _parse_r12_stats — layout-specific (FY2025-26 and FY2026-27) ─────────────

class TestParseR12StatsLayouts(unittest.TestCase):
    """Verify header-text-based column detection across both financial-year layouts.

    FY2025-26  Item col = "Item"       prod col = L ("Weight of Total Production")
               rej col  = Y ("Actual Rejection Weight (in Kgs)")
    FY2026-27  Item col = "Item Code"  SAP Code shifts cols right by one
               prod col = M ("Weight of Total Production")
               rej col  = Z ("Actual Rejection Weight (in Kgs)")
               Sub-header at row 5 has no valid date → skipped automatically
    """

    # ── FY2025-26 ──────────────────────────────────────────────────────────

    def _fy26_values(self, data_rows, include_ideal=False):
        """Build a minimal FY2025-26 R12 sheet with optional Ideal Rejection col."""
        if include_ideal:
            hdr = [
                "DATE", "MOULDING MACHINE", "Item", "Material",
                "Weight of Total Production",
                "Ideal Rejection Weight (in Kgs)",
                "Actual Rejection Weight (in Kgs)",
            ]
            data = [row[:6] + [row[6]] for row in data_rows]
        else:
            hdr = R12_HEADER  # DATE, MOULDING MACHINE, Item, Material, WtOfTotProd, ActRej
            data = data_rows
        preamble = [
            ["", "REPORT 12 — MOULDING PRODUCTION"],
            [],
            ["", "For the Month of April 2025"],
            [],
        ]
        return preamble + [hdr] + data

    def test_fy26_layout_basic(self):
        """FY2025-26: finds correct production and rejection totals."""
        values = self._fy26_values([
            _r12_row(_DATE,  "MC-1", 500, 5,  item="FIT001", mat="CPVC"),
            _r12_row(_DATE,  "MC-1", 300, 3,  item="FIT002", mat="UPVC"),
            _r12_row(_DATE2, "MC-1", 200, 2,  item="FIT001", mat="CPVC"),
        ])
        out, rej_val, by_mat = rej._parse_r12_stats(values, "2025-04")
        self.assertAlmostEqual(out, 1000.0)
        self.assertAlmostEqual(rej_val, 10.0)
        self.assertAlmostEqual(by_mat["CPVC"]["out"], 700.0)
        self.assertAlmostEqual(by_mat["UPVC"]["rej"],   3.0)

    def test_fy26_item_col_is_item_not_item_code(self):
        """FY2025-26: header 'Item' (not 'Item Code') is detected as the item column."""
        values = self._fy26_values([
            _r12_row(_DATE, "MC-1", 400, 4, item="FIT003", mat="SWR"),
        ])
        out, rej_val, _ = rej._parse_r12_stats(values, "2025-06")
        self.assertAlmostEqual(out, 400.0)
        self.assertAlmostEqual(rej_val, 4.0)

    def test_fy26_total_rows_skipped(self):
        """Rows where item is 'TOTAL' or 'GRAND TOTAL' are not counted."""
        values = self._fy26_values([
            _r12_row(_DATE,  "MC-1",    500, 5, item="FIT001", mat="CPVC"),
            _r12_row(_DATE,  "MC-1",  9_000, 90, item="TOTAL",  mat=""),
            _r12_row(_DATE,  "MC-1", 15_000, 150, item="GRAND TOTAL", mat=""),
        ])
        out, rej_val, _ = rej._parse_r12_stats(values, "2025-04")
        self.assertAlmostEqual(out, 500.0)
        self.assertAlmostEqual(rej_val, 5.0)

    def test_fy26_ideal_rejection_not_used(self):
        """The 'Ideal Rejection Weight' column is NEVER used; only 'Actual Rejection'."""
        # Header has both Ideal and Actual columns; Ideal has huge values.
        hdr_with_ideal = [
            "DATE", "MOULDING MACHINE", "Item", "Material",
            "Weight of Total Production",
            "Ideal Rejection Weight (in Kgs)",   # col 5 — must NOT be used
            "Actual Rejection Weight (in Kgs)",  # col 6 — must be used
        ]
        data_row = [_DATE, "MC-1", "FIT001", "CPVC", "1000", "999", "7"]
        values = [hdr_with_ideal, data_row]
        out, rej_val, _ = rej._parse_r12_stats(values, "2025-04")
        self.assertAlmostEqual(out, 1000.0)
        # Ideal rej = 999; actual rej = 7 — parser must pick 7
        self.assertAlmostEqual(rej_val, 7.0, msg="Parser used 'Ideal Rejection' instead of 'Actual'")

    def test_fy26_by_material_breakdown(self):
        """Material breakdown is correct across CPVC / UPVC / SWR / AGRI."""
        rows = [
            _r12_row(_DATE, "MC-1", 1000, 8,  item="F1", mat="CPVC"),
            _r12_row(_DATE, "MC-1",  800, 9,  item="F2", mat="UPVC"),
            _r12_row(_DATE, "MC-2",  600, 7,  item="F3", mat="SWR"),
            _r12_row(_DATE, "MC-2",  400, 5,  item="F4", mat="AGRI"),
        ]
        values = self._fy26_values(rows)
        _, _, by_mat = rej._parse_r12_stats(values, "2025-05")
        for mat in ("CPVC", "UPVC", "SWR", "AGRI"):
            self.assertIn(mat, by_mat, msg=f"{mat} missing from material breakdown")
        self.assertAlmostEqual(by_mat["CPVC"]["out"], 1000.0)
        self.assertAlmostEqual(by_mat["AGRI"]["rej"],    5.0)

    # ── FY2026-27 ──────────────────────────────────────────────────────────

    def _fy27_values(self, data_rows):
        """Build a minimal FY2026-27 R12 sheet with sub-header row after the main header."""
        # Main header row (row 4 in real sheet)
        header = R12_HEADER_FY27  # includes "Item Code" and "SAP Code"
        # Sub-header row (row 5 in real sheet) — date cell is a label, not a date
        sub_header = ["Output Production", "Wt in Kgs", "", "", "", "", ""]
        preamble = [
            ["", "REPORT 12 — MOULDING PRODUCTION"],
            [],
            ["", "For the Month of April 2026"],
            [],
        ]
        return preamble + [header, sub_header] + data_rows

    def test_fy27_layout_sub_header_skipped(self):
        """FY2026-27: sub-header row (row 5) has no valid date and is skipped."""
        values = self._fy27_values([
            _r12_row_fy27(_DATE,  "MC-1", 600, 6, item="FIT001", mat="CPVC"),
            _r12_row_fy27(_DATE2, "MC-1", 400, 4, item="FIT002", mat="UPVC"),
        ])
        out, rej_val, by_mat = rej._parse_r12_stats(values, "2026-04")
        self.assertAlmostEqual(out, 1000.0)
        self.assertAlmostEqual(rej_val, 10.0)
        self.assertIn("CPVC", by_mat)
        self.assertIn("UPVC", by_mat)

    def test_fy27_item_code_col_used(self):
        """FY2026-27: 'Item Code' column is preferred over 'Item' for row detection."""
        values = self._fy27_values([
            _r12_row_fy27(_DATE, "MC-1", 800, 8, item="FIT010", sap="S001", mat="SWR"),
        ])
        out, rej_val, _ = rej._parse_r12_stats(values, "2026-05")
        self.assertAlmostEqual(out, 800.0)
        self.assertAlmostEqual(rej_val, 8.0)

    def test_fy27_sap_code_not_used_as_item(self):
        """FY2026-27: 'SAP Code' column must NOT be identified as the item column.

        A row whose SAP Code is blank but Item Code is set must still be processed.
        A row with both blank means no item → should be skipped.
        """
        values = self._fy27_values([
            _r12_row_fy27(_DATE, "MC-1", 500, 5, item="FIT020", sap="", mat="AGRI"),
            _r12_row_fy27(_DATE, "MC-1", 0,   0, item="",       sap="X", mat="AGRI"),
        ])
        out, rej_val, _ = rej._parse_r12_stats(values, "2026-06")
        # Only first row counts (has Item Code); second row has blank Item Code → skip
        self.assertAlmostEqual(out, 500.0)
        self.assertAlmostEqual(rej_val, 5.0)

    def test_fy27_weight_of_total_production_col_used(self):
        """FY2026-27: 'Weight of Total Production' at col M is used, not 'Wt in Kgs' at col J."""
        # Build a header where 'Wt in Kgs' appears BEFORE 'Weight of Total Production'
        hdr = [
            "DATE", "MOULDING MACHINE", "Item Code", "SAP Code", "Material",
            "Wt in Kgs",                      # col 5 — sub-group header, must NOT be used
            "Weight of Total Production",     # col 6 — correct production column
            "Actual Rejection Weight (in Kgs)",  # col 7
        ]
        data_row = [_DATE, "MC-1", "FIT001", "SAP1", "CPVC", "9999", "1200", "12"]
        values = [hdr, data_row]
        out, rej_val, _ = rej._parse_r12_stats(values, "2026-04")
        # "Wt in Kgs" = 9999; "Weight of Total Production" = 1200
        self.assertAlmostEqual(out, 1200.0, msg="Parser used 'Wt in Kgs' instead of 'Weight of Total Production'")
        self.assertAlmostEqual(rej_val, 12.0)

    def test_fy27_ideal_rejection_not_used(self):
        """FY2026-27: 'Ideal Rejection Weight' col is never used."""
        hdr = [
            "DATE", "MOULDING MACHINE", "Item Code", "SAP Code", "Material",
            "Weight of Total Production",
            "Ideal Rejection Weight (in Kgs)",  # must NOT be used
            "Actual Rejection Weight (in Kgs)",
        ]
        data_row = [_DATE, "MC-1", "FIT001", "SAP1", "CPVC", "2000", "888", "15"]
        values = [hdr, data_row]
        out, rej_val, _ = rej._parse_r12_stats(values, "2026-05")
        self.assertAlmostEqual(out, 2000.0)
        self.assertAlmostEqual(rej_val, 15.0, msg="Parser used 'Ideal Rejection' instead of 'Actual'")

    # ── Zero-month safeguard (feeds template warning) ──────────────────────

    def test_recompute_returns_zero_fitting_months_when_r12_empty(self):
        """When all R12 reads yield no data, fitting_months == 0 in the result dict.

        This feeds the template warning banner so it must be accurate.
        """
        with patch.object(rej.store, "AVAILABLE", False), \
             patch("sheets.get_raw_values", return_value=[]):
            result = rej.recompute_rejection("PLUMBING")
        self.assertEqual(result["fitting_months"], 0,
                         "fitting_months must be 0 when R12 yields no rows")

    def test_recompute_returns_positive_fitting_months_when_r12_has_data(self):
        """When R12 yields data for at least one workbook, fitting_months > 0."""
        # Build a minimal valid R12 sheet
        r12_sheet = [R12_HEADER,
                     _r12_row(_DATE, "MC-1", 500, 5, item="FIT001", mat="CPVC")]

        def _mock_raw(file_id, tab_name):
            if tab_name == "Report-12":
                return r12_sheet
            return []  # R11 returns nothing → pipe_months stays 0

        with patch.object(rej.store, "AVAILABLE", False), \
             patch("sheets.get_raw_values", side_effect=_mock_raw):
            result = rej.recompute_rejection("PLUMBING")
        self.assertGreater(result["fitting_months"], 0,
                           "fitting_months must be > 0 when R12 has data rows")


if __name__ == "__main__":
    unittest.main()
