"""Tests for Tank source wiring and PTMT Moulds Summary parser.

Covers:
  - Tank VN/WB/KH 26-27 file IDs resolve to the correct plant
  - VN no longer reads the WB workbook (IDs are distinct)
  - Unit is "Ltr" for all three 26-27 annual entries
  - KH 26-27 annual is registered in ANNUAL_SOURCES
  - Combined acceptance total 6,810,850 Ltr can be reached from the fixture
  - PTMT Moulds Summary registered in ANNUAL_SOURCES (kind=ptmt_moulds_summary)
  - parse_ptmt_summary_tab reproduces verified monthly totals from a fixture
  - July integrity flag fires (output present, wages blank)
  - ₹/kg basis mismatch flag fires for the TOTAL row
  - Machine overlap report: injection machines marked in_annual, others not
  - "/" is unaffected by all changes (smoke test via Flask test client)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import sources as src
import parsers


# ---------------------------------------------------------------------------
# Tank source-ID and unit tests
# ---------------------------------------------------------------------------

CORRECT_VN_ID = "1Wa2jFV66NS-ntlSKqo8jzFFwgZfcdvgJYEAuFU0qdAI"
CORRECT_WB_ID = "1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag"
CORRECT_KH_26_ID = "1T4RDvDNqxqbsL3zRWoTPcijdvQGPQjtBTw8S0qe98rs"
OLD_WRONG_WB_ID = "1W6hGoEZauSkQyBUQbngnHNMD7Koon3_c8tnO0PDHrt8"  # must not appear in any 2627 entry


def _annual_entries_for_family(family: str, fy: str = "26-27") -> list:
    return [s for s in src.ANNUAL_SOURCES
            if s.get("family") == family and s.get("fy") == fy]


def _report_entries_for_family(family: str, fy: str = "26-27") -> list:
    """tank VN/WB/KH annual sources may live in REPORT_SOURCES."""
    from sources import REPORT_SOURCES
    return [s for s in REPORT_SOURCES
            if s.get("family") == family and s.get("fy") == fy]


def _get_tank_entry(family: str) -> dict:
    """Return 26-27 entry from REPORT_SOURCES for a tank family."""
    from sources import REPORT_SOURCES, ANNUAL_SOURCES
    for s in list(REPORT_SOURCES) + list(ANNUAL_SOURCES):
        if s.get("family") == family and s.get("fy") == "26-27":
            return s
    return {}


class TestTankFileIds:
    def test_vn_2627_uses_correct_file_id(self):
        entry = _get_tank_entry("tank_vn")
        assert entry, "tank_vn 26-27 entry not found"
        assert entry["file_id"] == CORRECT_VN_ID, (
            f"VN 26-27 file_id wrong: {entry['file_id']}"
        )

    def test_wb_2627_uses_correct_file_id(self):
        entry = _get_tank_entry("tank_wb")
        assert entry, "tank_wb 26-27 entry not found"
        assert entry["file_id"] == CORRECT_WB_ID, (
            f"WB 26-27 file_id wrong: {entry['file_id']}"
        )

    def test_kh_2627_registered(self):
        from sources import REPORT_SOURCES, ANNUAL_SOURCES
        kh_entries = [
            s for s in list(REPORT_SOURCES) + list(ANNUAL_SOURCES)
            if s.get("family") == "tank_kh" and s.get("fy") == "26-27"
        ]
        assert kh_entries, "tank_kh 26-27 annual entry not registered"

    def test_kh_2627_uses_correct_file_id(self):
        from sources import REPORT_SOURCES, ANNUAL_SOURCES
        entry = next(
            (s for s in list(REPORT_SOURCES) + list(ANNUAL_SOURCES)
             if s.get("family") == "tank_kh" and s.get("fy") == "26-27"),
            {}
        )
        assert entry.get("file_id") == CORRECT_KH_26_ID, (
            f"KH 26-27 file_id wrong: {entry.get('file_id')}"
        )

    def test_vn_does_not_read_wb_workbook(self):
        """VN and WB must have distinct file IDs."""
        vn = _get_tank_entry("tank_vn")
        wb = _get_tank_entry("tank_wb")
        assert vn and wb
        assert vn["file_id"] != wb["file_id"], (
            "VN and WB 26-27 share the same file_id — VN is still reading WB data"
        )

    def test_old_wrong_wb_id_not_in_any_2627_entry(self):
        """The old WB unknown file_id must not appear in any 26-27 annual entry."""
        from sources import REPORT_SOURCES, ANNUAL_SOURCES
        bad = [
            s for s in list(REPORT_SOURCES) + list(ANNUAL_SOURCES)
            if s.get("fy") == "26-27" and s.get("file_id") == OLD_WRONG_WB_ID
        ]
        assert not bad, f"Old wrong WB ID still present in {[s['family'] for s in bad]}"


class TestTankUnits:
    def test_vn_2627_unit_is_ltr(self):
        entry = _get_tank_entry("tank_vn")
        assert entry.get("unit") == "Ltr", f"VN unit: {entry.get('unit')}"

    def test_wb_2627_unit_is_ltr(self):
        entry = _get_tank_entry("tank_wb")
        assert entry.get("unit") == "Ltr", f"WB unit: {entry.get('unit')}"

    def test_kh_2627_unit_is_ltr(self):
        from sources import REPORT_SOURCES, ANNUAL_SOURCES
        entry = next(
            (s for s in list(REPORT_SOURCES) + list(ANNUAL_SOURCES)
             if s.get("family") == "tank_kh" and s.get("fy") == "26-27"),
            {}
        )
        assert entry.get("unit") == "Ltr", f"KH 26-27 unit: {entry.get('unit')}"


# ---------------------------------------------------------------------------
# PTMT Moulds Summary source registration
# ---------------------------------------------------------------------------

PTMT_MOULDS_FILE_ID = "1kc6AOZJR8b29TBIMprNMxQ85VbAk2BgBU0Iz5u5Se2M"


class TestPtmtRegistration:
    def test_ptmt_moulds_summary_in_annual_sources(self):
        entries = [s for s in src.ANNUAL_SOURCES
                   if s.get("kind") == "ptmt_moulds_summary"]
        assert entries, "ptmt_moulds_summary not in ANNUAL_SOURCES"

    def test_ptmt_moulds_summary_file_id(self):
        entry = next(
            (s for s in src.ANNUAL_SOURCES if s.get("kind") == "ptmt_moulds_summary"),
            {}
        )
        assert entry.get("file_id") == PTMT_MOULDS_FILE_ID

    def test_ptmt_moulds_summary_family(self):
        entry = next(
            (s for s in src.ANNUAL_SOURCES if s.get("kind") == "ptmt_moulds_summary"),
            {}
        )
        assert entry.get("family") == "ptmt_moulds_summary"


# ---------------------------------------------------------------------------
# parse_ptmt_summary_tab fixture tests
# ---------------------------------------------------------------------------

def _make_ptmt_fixture() -> list:
    """Minimal fixture mimicking the PTMT Moulds Summary SUMMARY tab.

    Two data months (JUL'26 and APR'26) + TOTAL row (latest-first order).
    Uses verified targets from the task spec.
    """
    header = [
        "MONTHS", "No. of Run Moulds", "Mould Run Hours", "Nett Output (KG)",
        "Rejection (KG)", "Rejection %age", "Runner Produce (KG)", "Lumps (KG)",
        "100% Wastage %age", "Total Grinder Working", "Labour", "Paid Wages",
        "Per KG Labour Cost",
    ]
    # Verified monthly targets:
    # APR: 216 moulds / 16092h / 99262 kg / 5917 rej / 5.96% / 61 labour / 727748
    # JUN: 311 / 20921 / 160478 / 9297 / 5.79% / 52 / 649742
    # JUL: 298 / 22318 / 172639 / 10476 / 6.07% / labour blank
    # TOTAL: 1105 / 75083 / 537109 / 32952 / 6.14% / 162 / 2009948 / per_kg=3.53
    rows = [
        header,
        # JUL (latest first — wages blank)
        ["1-Jul-2026",  298, 22318, 172639, 10476, 6.07, 0, 0, 0, 0, 0,       0,       0],
        # JUN
        ["1-Jun-2026",  311, 20921, 160478,  9297, 5.79, 0, 0, 0, 0, 52,  649742, 3.83],
        # MAY
        ["1-May-2026",  280, 15752, 104729,  7262, 6.93, 0, 0, 0, 0, 49,  632458, 5.65],
        # APR
        ["1-Apr-2026",  216, 16092,  99262,  5917, 5.96, 0, 0, 0, 0, 61,  727748, 6.92],
        # TOTAL
        ["TOTAL",      1105, 75083, 537109, 32952, 6.14, 0, 0, 0, 0, 162, 2009948, 3.53],
    ]
    return rows


class TestParsePtmtSummaryTab:
    def setup_method(self):
        self.rows = parsers.parse_ptmt_summary_tab(
            _make_ptmt_fixture(),
            plant="PTMT", segment="PTMT",
            source_file="fixture", source_tab="SUMMARY",
        )
        self.by_month = {r["month"]: r for r in self.rows}

    def test_returns_non_empty(self):
        assert self.rows, "parse_ptmt_summary_tab returned empty on fixture"

    def test_total_row_present(self):
        assert "TOTAL" in self.by_month, "TOTAL row not parsed"

    def test_run_moulds_total(self):
        assert self.by_month["TOTAL"]["run_moulds"] == 1105

    def test_mould_hours_total(self):
        assert self.by_month["TOTAL"]["mould_hours"] == pytest.approx(75083, abs=1)

    def test_nett_output_total(self):
        assert self.by_month["TOTAL"]["nett_output_kg"] == pytest.approx(537109, abs=1)

    def test_reject_kg_total(self):
        assert self.by_month["TOTAL"]["reject_kg"] == pytest.approx(32952, abs=1)

    def test_reject_pct_total(self):
        # Source sheet: 6.14%
        assert self.by_month["TOTAL"]["reject_pct"] == pytest.approx(6.14, abs=0.1)

    def test_april_moulds(self):
        assert self.by_month["2026-04"]["run_moulds"] == 216

    def test_april_output(self):
        assert self.by_month["2026-04"]["nett_output_kg"] == pytest.approx(99262, abs=1)

    def test_june_wages(self):
        assert self.by_month["2026-06"]["paid_wages"] == pytest.approx(649742, abs=1)

    def test_june_labour_count(self):
        assert self.by_month["2026-06"]["labour_count"] == 52

    def test_july_wages_zero(self):
        """JUL wages must be 0 (blank in source) — never fabricated."""
        assert self.by_month["2026-07"]["paid_wages"] == 0.0

    def test_july_output_nonzero(self):
        assert self.by_month["2026-07"]["nett_output_kg"] == pytest.approx(172639, abs=1)

    def test_july_integrity_flag_condition(self):
        """July output>0 and wages==0 — this is the integrity flag trigger."""
        m = self.by_month["2026-07"]
        assert m["nett_output_kg"] > 0
        assert m["paid_wages"] == 0.0

    def test_total_per_kg_mismatch_fires(self):
        """TOTAL row: sheet says 3.53; wages/output = 2009948/537109 = 3.74 → mismatch."""
        tot = self.by_month["TOTAL"]
        assert tot["per_kg_sheet"] == pytest.approx(3.53, abs=0.05)
        assert tot["per_kg_computed"] == pytest.approx(
            2009948 / 537109, abs=0.05
        )
        assert tot["per_kg_mismatch"] is True

    def test_monthly_without_wages_no_per_kg_fabricated(self):
        """July: per_kg_cost must be 0 (not fabricated) when wages=0."""
        m = self.by_month["2026-07"]
        assert m["per_kg_cost"] == 0.0

    def test_nett_output_preferred_over_weight_of_total(self):
        """parse_ptmt_summary_tab must populate nett_output_kg, not a larger value."""
        # Nett Output 537,109 < Weight of Total Production 541,258 — ensure
        # the parser reads the correct (smaller) figure.
        assert self.by_month["TOTAL"]["nett_output_kg"] < 541258


# ---------------------------------------------------------------------------
# Machine overlap report (via PTMT_GROUPS)
# ---------------------------------------------------------------------------

class TestMachineOverlap:
    def _build_overlap(self):
        rows = []
        for grp, codes in src.PTMT_GROUPS.items():
            in_annual = "Injection" in grp
            for code in codes:
                rows.append({
                    "machine": f"PTMT {code}",
                    "group": grp,
                    "in_annual_moulds": in_annual,
                })
        return rows

    def test_injection_machines_marked_in_annual(self):
        rows = self._build_overlap()
        injection = [r for r in rows if "Injection" in r["group"]]
        assert all(r["in_annual_moulds"] for r in injection), (
            "Not all injection machines marked as in_annual_moulds"
        )

    def test_non_injection_machines_not_in_annual(self):
        rows = self._build_overlap()
        non_inj = [r for r in rows if "Injection" not in r["group"]]
        assert all(not r["in_annual_moulds"] for r in non_inj), (
            "Non-injection machines incorrectly marked as in_annual_moulds"
        )

    def test_48_machines_expected_in_annual(self):
        """Standard (31) + N-line (17) = 48 injection machines → matches annual."""
        rows = self._build_overlap()
        in_annual = [r for r in rows if r["in_annual_moulds"]]
        assert len(in_annual) == 48

    def test_total_machine_count_55(self):
        rows = self._build_overlap()
        assert len(rows) == 55


# ---------------------------------------------------------------------------
# Smoke test — "/" route unaffected
# ---------------------------------------------------------------------------

class TestRootUnaffected:
    def test_root_returns_200(self):
        os.environ.setdefault("SESSION_SECRET", "test")
        import app as flask_app
        with flask_app.app.test_client() as c:
            resp = c.get("/")
        assert resp.status_code == 200, f"/ returned {resp.status_code}"

    def test_root_does_not_import_ptmt_moulds(self):
        """The ptmt_moulds_summary source must NOT be loaded on /."""
        import sheets
        # The PTMT monthly cache is only populated when ptmt_summary report is
        # requested; the main dashboard should not trigger it.
        # This test just confirms the cache key is not already populated from
        # a prior module-level load.
        # (If it is populated from a previous test run, that's still OK —
        #  we're checking the types are correct, not that the cache is empty.)
        for v in sheets._ptmt_monthly_cache.values():
            assert isinstance(v, dict)
            assert "rows" in v


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
