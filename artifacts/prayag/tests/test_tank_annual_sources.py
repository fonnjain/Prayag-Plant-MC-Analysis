"""
Tests for Tank FY26-27 annual source wiring fix + CP location label.

Covers:
- VN FY26-27 points at the correct Drive file (not WB's file)
- WB FY26-27 points at the correct Drive file (not the deleted one)
- KH FY26-27 is registered
- All three FY26-27 Tank entries have unit == "Ltr" and tab == "SUMMARY (LTR)"
- kind == "tank_annual_2526" on all three (same parser as FY25-26 SUMMARY tab)
- FY25-26 entries are untouched
- CP location corrected to Bhiwari
- Named error surfaced when SUMMARY (LTR) tab is found but returns 0 records
"""

import sys
import os
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sources


def _tank_report_src(family: str, fy: str) -> dict | None:
    """Return the REPORT_SOURCES entry for the given tank family and FY, or None."""
    for s in sources.REPORT_SOURCES:
        if s.get("family") == family and s.get("fy") == fy:
            return s
    return None


# ---------------------------------------------------------------------------
# File-ID correctness
# ---------------------------------------------------------------------------

class TestTankVN2627FileId(unittest.TestCase):
    def test_vn_2627_points_at_vn_file(self):
        """TANK_VN FY26-27 must NOT point at WB's file (1_ugk2V3…)."""
        src = _tank_report_src("tank_vn", "26-27")
        self.assertIsNotNone(src, "tank_vn FY26-27 entry missing from REPORT_SOURCES")
        # The old (wrong) value was WB's file — assert it is corrected.
        WB_FILE = "1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag"
        self.assertNotEqual(
            src["file_id"], WB_FILE,
            "TANK_VN FY26-27 still pointing at WB's file — ID not repointed"
        )
        self.assertEqual(
            src["file_id"], "1Wa2jFV66NS-ntlSKqo8jzFFwgZfcdvgJYEAuFU0qdAI",
            "TANK_VN FY26-27 file_id should be the VN workbook"
        )

    def test_wb_2627_points_at_wb_file(self):
        """TANK_WB FY26-27 must point at WB's file (1_ugk2V3…, formerly in VN slot)."""
        src = _tank_report_src("tank_wb", "26-27")
        self.assertIsNotNone(src, "tank_wb FY26-27 entry missing from REPORT_SOURCES")
        # The old (wrong) value was the deleted file 1W6hGoEZ…
        DELETED_FILE = "1W6hGoEZauSkQyBUQbngnHNMD7Koon3_c8tnO0PDHrt8"
        self.assertNotEqual(
            src["file_id"], DELETED_FILE,
            "TANK_WB FY26-27 still pointing at the deleted/404 file — not fixed"
        )
        self.assertEqual(
            src["file_id"], "1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag",
            "TANK_WB FY26-27 file_id should be the WB workbook"
        )


# ---------------------------------------------------------------------------
# KH FY26-27 registered
# ---------------------------------------------------------------------------

class TestTankKH2627Registered(unittest.TestCase):
    def test_kh_2627_entry_exists(self):
        """TANK (KH) FY26-27 annual entry must now be registered."""
        src = _tank_report_src("tank_kh", "26-27")
        self.assertIsNotNone(src, "tank_kh FY26-27 entry missing — not registered")
        self.assertEqual(src["plant"], "TANK")
        self.assertEqual(
            src["file_id"], "1T4RDvDNqxqbsL3zRWoTPcijdvQGPQjtBTw8S0qe98rs",
            "KH FY26-27 file_id mismatch"
        )


# ---------------------------------------------------------------------------
# Unit and tab for all three FY26-27 Tank entries
# ---------------------------------------------------------------------------

class TestTank2627UnitAndTab(unittest.TestCase):
    def _all_tank_2627(self):
        return [s for s in sources.REPORT_SOURCES if s.get("fy") == "26-27"
                and s.get("family") in ("tank_vn", "tank_wb", "tank_kh")]

    def test_unit_ltr_on_all_three(self):
        entries = self._all_tank_2627()
        self.assertEqual(len(entries), 3, f"Expected 3 FY26-27 Tank entries, got {len(entries)}")
        for s in entries:
            self.assertEqual(
                s["unit"], "Ltr",
                f"{s['title']!r}: unit={s['unit']!r}, expected 'Ltr'"
            )

    def test_tab_summary_ltr_on_all_three(self):
        entries = self._all_tank_2627()
        self.assertEqual(len(entries), 3, f"Expected 3 FY26-27 Tank entries, got {len(entries)}")
        for s in entries:
            self.assertEqual(
                s["tab"], "SUMMARY (LTR)",
                f"{s['title']!r}: tab={s['tab']!r}, expected 'SUMMARY (LTR)'"
            )

    def test_kind_tank_annual_2526_on_all_three(self):
        """All three FY26-27 Tank annual entries should use tank_annual_2526 kind
        (same parser as FY25-26 SUMMARY tab — not the Sheet1 parser)."""
        entries = self._all_tank_2627()
        self.assertEqual(len(entries), 3, f"Expected 3 FY26-27 Tank entries, got {len(entries)}")
        for s in entries:
            self.assertEqual(
                s["kind"], "tank_annual_2526",
                f"{s['title']!r}: kind={s['kind']!r}, expected 'tank_annual_2526'"
            )


# ---------------------------------------------------------------------------
# FY25-26 entries unchanged
# ---------------------------------------------------------------------------

class TestTank2526EntriesUnchanged(unittest.TestCase):
    _EXPECTED = {
        "tank_vn": {
            "file_id": "1fe2ZgL8EcuUVkvjC3-mZ5Pr8WkXWQ5V70AiwkbDUh-0",
            "tab": "SUMMARY (LTR)",
            "kind": "tank_annual_2526",
            "unit": "Ltr",
        },
        "tank_wb": {
            "file_id": "1mtgkCbNsWsSrgjJfN2zc7SDb2ysHoH11xG3afr0oovc",
            "tab": "SUMMARY (LTR)",
            "kind": "tank_annual_2526",
            "unit": "Ltr",
        },
        "tank_kh": {
            "file_id": "1_6Foa8TXXP-xr0KIx04q7i8iigkrLjuT8r7uG62x8qQ",
            "tab": "SUMMARY (LTR)",
            "kind": "tank_annual_2526",
            "unit": "Ltr",
        },
    }

    def test_fy2526_entries_unchanged(self):
        for family, expected in self._EXPECTED.items():
            src = _tank_report_src(family, "25-26")
            self.assertIsNotNone(src, f"{family} FY25-26 entry missing")
            for key, val in expected.items():
                self.assertEqual(
                    src[key], val,
                    f"{family} FY25-26 {key!r}: got {src[key]!r}, expected {val!r} — "
                    "FY25-26 entry was modified when it should not have been"
                )


# ---------------------------------------------------------------------------
# CP location label
# ---------------------------------------------------------------------------

class TestCPLocationLabel(unittest.TestCase):
    def test_cp_location_is_bhiwari(self):
        self.assertEqual(
            sources.PLANT_LOCATIONS.get("CP"), "Bhiwari",
            "CP should be located at 'Bhiwari' (same plant as PTMT), not 'KH'"
        )

    def test_ptmt_location_unchanged(self):
        self.assertEqual(
            sources.PLANT_LOCATIONS.get("PTMT"), "Bhiwari",
            "PTMT location must remain 'Bhiwari' — do not regress"
        )


# ---------------------------------------------------------------------------
# Named error when SUMMARY (LTR) is found but parse returns 0 records
# ---------------------------------------------------------------------------

class TestTankSummaryNamedError(unittest.TestCase):
    """When the SUMMARY (LTR) tab exists but the parser returns 0 records,
    _load_annual_family must surface a 'TankSummaryParseFailure' warning
    rather than silently returning an empty source report (R-06)."""

    def _make_src(self, family="tank_vn", title="Tank VN Annual (26-27)"):
        return {
            "family": family,
            "title": title,
            "file_id": "FAKE_ID",
            "tab": "SUMMARY (LTR)",
            "kind": "tank_annual_2526",
            "segment": "Tanks",
            "plant": "TANK_VN",
            "unit": "Ltr",
            "fy": "26-27",
            "location": "VN",
            "grain": "summary-only",
        }

    def test_named_error_when_parse_returns_empty(self):
        """Simulate: tab found, parser returns [] → warning must contain
        'TankSummaryParseFailure' (not a silent empty result)."""
        import sheets

        with (
            patch("sheets.list_tabs", return_value=["DATA", "Sheet1", "SUMMARY (LTR)"]),
            patch("sheets.read_values", return_value=[]),
            patch("sheets.parsers.parse_tank_annual_2526", return_value=[]),
        ):
            records, report = sheets._load_annual_family(self._make_src(), token="FAKE")

        self.assertEqual(records, [])
        self.assertIn("warning", report, "source report must have a 'warning' key when parse fails")
        self.assertIn(
            "TankSummaryParseFailure", report["warning"],
            f"warning must contain 'TankSummaryParseFailure'; got: {report['warning']!r}"
        )
        self.assertNotIn(
            "Sheet1", report["warning"].lower().split("fallback")[0] if "fallback" in report["warning"] else "",
            "warning must not suggest Sheet1 fallback"
        )

    def test_no_warning_when_parse_returns_records(self):
        """If the parser does return records, no warning should be added."""
        import sheets
        from metrics import Record

        fake_record = Record(
            grain="monthly", period="2026-04", date="2026-04",
            plant="TANK_VN", segment="Tanks", unit="Ltr",
            machine="", mould="500 LTR",
            total_count=100.0, reject_count=0.0,
            location="VN", source_family="Tanks",
            source_file="FAKE_ID", source_tab="SUMMARY (LTR)",
        )
        with (
            patch("sheets.list_tabs", return_value=["DATA", "Sheet1", "SUMMARY (LTR)"]),
            patch("sheets.read_values", return_value=[]),
            patch("sheets.parsers.parse_tank_annual_2526", return_value=[fake_record]),
        ):
            records, report = sheets._load_annual_family(self._make_src(), token="FAKE")

        self.assertEqual(len(records), 1)
        self.assertNotIn("warning", report, "No warning should appear when records are returned")
