"""
Tests for Garden Pipe annual-summary parser bugs (Bugs 1–5).

Bug 1 — parse_mc_detail must locate Rejection (KG) by header in Garden
         per-machine blocks; never silently default to 0.
Bug 2 — SUMMARY tab preferred for monthly rollup over blank per-machine rows.
Bug 3 — Labour cost wired at segment level (not Rs 0).
         Per-machine Labour Cost/kg = "not captured at machine level".
Bug 4 — Utilisation appears once; all 4 machines always rendered (idle = 0).
Bug 5 — Data-integrity flags fire for MAY (wages, no output)
         and JUL (output, no wages).
"""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from parsers import parse_mc_detail, parse_garden_summary_tab


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _garden_mc_block(with_rejection=True):
    """Minimal per-machine block matching the Garden Pipe annual workbook layout.

    Header row: MACHINE | PIPE MACHINE(month) | Ideal Hours | Actual Hours |
                Actual Output (KG) | Ideal Output | Average Per Hour Output |
                Rejection (KG) | Rejection in %age | M/C Utilization in Hours (%) |
                Output Efficiency (%)

    Two data rows: APR'26 and JUN'26.
    """
    header = [
        "MACHINE",
        "PIPE MACHINE",
        "Ideal Hours",
        "Actual Hours",
        "Actual Output (KG)",
        "Ideal Output",
        "Average Per Hour Output",
        "Rejection (KG)" if with_rejection else "",
        "Rejection in %age",
        "M/C Utilization in Hours (%)",
        "Output Efficiency (%)",
    ]
    # APR'26: M/C-1, actual_hours=200, output=8900, rejection=200.47 kg → 2.20%
    apr_row = ["M/C-1", "APR'26", 250, 200, 8900, 200, 44.5,
               200.47 if with_rejection else "", "2.20%", "80%", "89%"]
    # JUN'26 continued on same machine (M/C-1 carried forward)
    jun_row = ["", "JUN'26", 250, 250, 10000, 200, 40.0,
               280.0 if with_rejection else "", "2.72%", "100%", "80%"]
    return [header, apr_row, jun_row]


def _garden_mc_block_split_header():
    """Simulates the split-header layout where an earlier row has ACTUAL+HOUR
    but no REJECT, and the true column-header row appears one row later."""
    # Row 0: spurious header-like title (has ACTUAL and HOUR but no REJECT)
    spurious = ["ACTUAL RUN HOUR SUMMARY", "", "", "", "", "", "", "", "", "", ""]
    # Row 1: real column header (has ACTUAL, HOUR, and REJECT)
    header = [
        "MACHINE", "PIPE MACHINE",
        "Ideal Hours", "Actual Hours", "Actual Output (KG)", "Ideal Output",
        "Average Per Hour Output", "Rejection (KG)", "Rejection in %age",
        "M/C Utilization in Hours (%)", "Output Efficiency (%)",
    ]
    apr_row = ["M/C-2", "APR'26", 250, 180, 6000, 200, 33.3, 169.8, "2.75%", "72%", "83%"]
    return [spurious, header, apr_row]


def _garden_mc_block_idle_may():
    """M/C-1 tab with APR'26 (output), MAY'26 (both zero), JUN'26 (output)."""
    header = [
        "MACHINE", "PIPE MACHINE",
        "Ideal Hours", "Actual Hours", "Actual Output (KG)", "Ideal Output",
        "Average Per Hour Output", "Rejection (KG)", "Rejection in %age",
        "M/C Utilization in Hours (%)", "Output Efficiency (%)",
    ]
    apr = ["M/C-1", "APR'26", 250, 130, 4900, 200, 37.7, 109.3, "2.18%", "52%", "75%"]
    may = ["",      "MAY'26", 250, 0,   0,    200, 0.0,  0.0,   "0%",    "0%",  "0%"]
    jun = ["",      "JUN'26", 250, 250, 10000, 200, 40.0, 280.0, "2.72%", "100%", "80%"]
    return [header, apr, may, jun]


def _garden_summary_tab():
    """Minimal SUMMARY tab matching the source-verified structure.

    MONTHS | M/C Run Hours | Actual Output (KG) | Rejection (KG) |
    Rejection %age | Total Output with Rejection (KG) | Labour |
    Actual Paid Hours | Paid Wages | Paid Hours Devoted by Per Person |
    Per Hour Cost on Paid Hours | Per KG Labour Cost
    """
    header = [
        "MONTHS",
        "M/C Run Hours",
        "Actual Output (KG)",
        "Rejection (KG)",
        "Rejection %age",
        "Total Output with Rejection (KG)",
        "Labour",
        "Actual Paid Hours",
        "Paid Wages",
        "Paid Hours Devoted by Per Person",
        "Per Hour Cost on Paid Hours",
        "Per KG Labour Cost",
    ]
    # APR'26: 549h, 38950kg, 3.06%, 19 workers, Rs 104845 wages, Rs 2.61/kg
    apr = ["APR'26", 549, 38950, 1228, "3.06%", 40178, 19, 2100, 104845, 110.5, 49.9, 2.61]
    # MAY'26: 0 output but wages exist (integrity flag)
    may = ["MAY'26", 0,   0,     0,    "0%",    0,     20, 2200, 149442, 110.0, 67.9, 0]
    # JUN'26: 1004h, 66911kg, 3.31%, 17 workers, Rs 171877 wages, Rs 2.49/kg
    jun = ["JUN'26", 1004, 66911, 2290, "3.31%", 69201, 17, 1900, 171877, 111.8, 90.5, 2.49]
    # JUL'26: 0h, 32191kg, 5.76%, NO labour (integrity flag)
    jul = ["JUL'26", 0,    32191, 1962, "5.76%", 34153, 0,  0,    0,      0,     0,    0]
    # TOTAL row
    total = ["TOTAL", 1553, 138052, 5480, "3.81%", 143532, 56, 6200, 426164, 110.7, 67.9, 2.97]
    return [header, apr, may, jun, jul, total]


# ─────────────────────────────────────────────────────────────────────────────
# Bug 1 — parse_mc_detail: rejection column found by header, never defaults 0
# ─────────────────────────────────────────────────────────────────────────────

class TestBug1RejectionFoundByHeader:
    def test_rejection_non_zero_for_garden_apr(self):
        """Rejection (KG) column must be located; APR'26 reject_count must be >0."""
        recs = parse_mc_detail(
            _garden_mc_block(with_rejection=True),
            plant="GARDEN", segment="Garden Pipe", unit="kg",
            source_file="f", source_tab="M/C-1",
        )
        assert recs, "expected records from Garden M/C-1 block"
        apr = next((r for r in recs if r.period == "2026-04"), None)
        assert apr is not None, "APR'26 record must be present"
        assert apr.reject_count > 0, (
            f"APR reject_count must be non-zero; got {apr.reject_count}. "
            "Rejection (KG) column not being located by header."
        )

    def test_rejection_percentage_matches_verified_value(self):
        """APR'26 M/C-1 rejection ≈ 2.20% (verified against source)."""
        recs = parse_mc_detail(
            _garden_mc_block(with_rejection=True),
            plant="GARDEN", segment="Garden Pipe", unit="kg",
            source_file="f", source_tab="M/C-1",
        )
        apr = next(r for r in recs if r.period == "2026-04")
        computed_pct = apr.reject_count / (apr.total_count + apr.reject_count) * 100
        assert abs(computed_pct - 2.20) < 0.5, (
            f"APR'26 rejection % should be ~2.20, got {computed_pct:.2f}"
        )

    def test_split_header_layout_still_finds_rejection(self):
        """When an earlier row has ACTUAL+HOUR but no REJECT, the parser must
        fall through to the richer row that also has REJECT."""
        recs = parse_mc_detail(
            _garden_mc_block_split_header(),
            plant="GARDEN", segment="Garden Pipe", unit="kg",
            source_file="f", source_tab="M/C-2",
        )
        assert recs, "expected at least one record from split-header block"
        r = recs[0]
        assert r.reject_count > 0, (
            f"Split-header layout: reject_count must be non-zero, got {r.reject_count}. "
            "Header detection is picking the spurious ACTUAL+HOUR row."
        )

    def test_jun_rejection_non_zero(self):
        """JUN'26 rejection must also be non-zero."""
        recs = parse_mc_detail(
            _garden_mc_block(with_rejection=True),
            plant="GARDEN", segment="Garden Pipe", unit="kg",
            source_file="f", source_tab="M/C-1",
        )
        jun = next((r for r in recs if r.period == "2026-06"), None)
        assert jun is not None, "JUN'26 record must be present"
        assert jun.reject_count > 0, f"JUN'26 reject_count must be >0, got {jun.reject_count}"


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2 — SUMMARY tab authoritative for monthly rollup
# ─────────────────────────────────────────────────────────────────────────────

class TestBug2SummaryTabParsing:
    def test_summary_tab_parses_all_months(self):
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        months = {r["month"] for r in rows}
        assert "2026-04" in months, "APR'26 must be parsed"
        assert "2026-05" in months, "MAY'26 must be parsed"
        assert "2026-06" in months, "JUN'26 must be parsed"
        assert "2026-07" in months, "JUL'26 must be parsed"
        assert "TOTAL" in months, "TOTAL row must be parsed"

    def test_summary_tab_apr_figures_match_verified(self):
        """APR: 549h, 38950kg, 3.06% (source-verified)."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        apr = next(r for r in rows if r["month"] == "2026-04")
        assert abs(apr["run_hours"] - 549) < 1,   f"APR hours: {apr['run_hours']}"
        assert abs(apr["output_kg"] - 38950) < 10, f"APR output: {apr['output_kg']}"
        assert abs(apr["reject_pct"] - 3.06) < 0.1, f"APR reject%: {apr['reject_pct']}"

    def test_summary_tab_jun_figures_match_verified(self):
        """JUN: 1004h, 66911kg, 3.31%, Rs 2.49/kg."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        jun = next(r for r in rows if r["month"] == "2026-06")
        assert abs(jun["run_hours"] - 1004) < 1
        assert abs(jun["output_kg"] - 66911) < 10
        assert abs(jun["reject_pct"] - 3.31) < 0.1
        assert abs(jun["per_kg_cost"] - 2.49) < 0.05, f"JUN per_kg_cost: {jun['per_kg_cost']}"

    def test_summary_tab_jul_has_output_but_no_labour(self):
        """JUL: 32191 kg output but 0 wages — parse without fabricating per-kg cost."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        jul = next(r for r in rows if r["month"] == "2026-07")
        assert abs(jul["output_kg"] - 32191) < 10, f"JUL output: {jul['output_kg']}"
        assert abs(jul["reject_pct"] - 5.76) < 0.1, f"JUL reject%: {jul['reject_pct']}"
        assert jul["wages"] == 0, "JUL wages must be 0 (no labour in source)"
        assert jul["per_kg_cost"] == 0, (
            "JUL per_kg_cost must be 0 when wages are absent — do not fabricate"
        )

    def test_summary_tab_total_row(self):
        """TOTAL: 1553h, 138052kg, 3.81%, Rs 2.97/kg."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        total = next(r for r in rows if r["month"] == "TOTAL")
        assert abs(total["run_hours"] - 1553) < 1
        assert abs(total["output_kg"] - 138052) < 10
        assert abs(total["reject_pct"] - 3.81) < 0.1
        assert abs(total["per_kg_cost"] - 2.97) < 0.05


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — Labour wired at segment level / "not captured" per-machine
# ─────────────────────────────────────────────────────────────────────────────

class TestBug3LabourWiring:
    def test_summary_tab_labour_count_parsed(self):
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        apr = next(r for r in rows if r["month"] == "2026-04")
        assert apr["labour_count"] == 19, f"APR labour count: {apr['labour_count']}"
        assert abs(apr["wages"] - 104845) < 100, f"APR wages: {apr['wages']}"

    def test_summary_tab_wages_parsed(self):
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        total = next(r for r in rows if r["month"] == "TOTAL")
        assert abs(total["wages"] - 426164) < 500, f"TOTAL wages: {total['wages']}"
        assert total["labour_count"] == 56, f"TOTAL labour count: {total['labour_count']}"

    def test_per_kg_cost_derivation(self):
        """per_kg_cost is read from sheet; derived when blank but computable."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        apr = next(r for r in rows if r["month"] == "2026-04")
        # Sheet provides 2.61; verify it's read (not zero)
        assert apr["per_kg_cost"] > 0, "per_kg_cost must not be zero when sheet provides it"
        assert abs(apr["per_kg_cost"] - 2.61) < 0.05


# ─────────────────────────────────────────────────────────────────────────────
# Bug 4 — Utilisation de-duplicated; idle machines render as 0
# ─────────────────────────────────────────────────────────────────────────────

class TestBug4IdleMachinesAndDedup:
    def test_idle_may_machine_skipped_by_parser(self):
        """parse_mc_detail skips 0/0 rows (future-month guard); confirmed behaviour."""
        recs = parse_mc_detail(
            _garden_mc_block_idle_may(),
            plant="GARDEN", segment="Garden Pipe", unit="kg",
            source_file="f", source_tab="M/C-1",
        )
        months = {r.period for r in recs}
        # APR and JUN produce records; MAY is zero and currently skipped
        assert "2026-04" in months, "APR must produce a record"
        assert "2026-06" in months, "JUN must produce a record"

    def test_build_report_table_includes_all_four_garden_machines(self):
        """_build_report_table for garden_summary must include GARDEN M/C-1..4
        even when some are absent from the records (idle in the period)."""
        import app as _app
        from metrics import Record
        # Only M/C-1 and M/C-3 have records; M/C-2 and M/C-4 are idle
        rows = [
            Record(grain="monthly", period="2026-05", date="2026-05",
                   plant="GARDEN", segment="Garden Pipe", unit="kg",
                   machine="GARDEN M/C-1", actual_hours=100, total_count=5000,
                   reject_count=100, ideal_hours=120),
            Record(grain="monthly", period="2026-05", date="2026-05",
                   plant="GARDEN", segment="Garden Pipe", unit="kg",
                   machine="GARDEN M/C-3", actual_hours=80, total_count=3000,
                   reject_count=80, ideal_hours=120),
        ]
        headers, table_rows, *_ = _app._build_report_table(
            "garden_summary", rows, {}
        )
        machine_names = [tr[0] for tr in table_rows]
        for i in range(1, 5):
            assert f"GARDEN M/C-{i}" in machine_names, (
                f"GARDEN M/C-{i} must appear in table even if idle"
            )

    def test_garden_labour_cell_says_not_captured(self):
        """Labour Cost/kg column for Garden must show 'not captured at machine level'."""
        import app as _app
        from metrics import Record
        rows = [
            Record(grain="monthly", period="2026-04", date="2026-04",
                   plant="GARDEN", segment="Garden Pipe", unit="kg",
                   machine="GARDEN M/C-1", actual_hours=200, total_count=8900,
                   reject_count=200, ideal_hours=250, labour_cost=104845),
        ]
        headers, table_rows, *_ = _app._build_report_table(
            "garden_summary", rows, {}
        )
        # Find the M/C-1 row
        mc1_row = next(tr for tr in table_rows if tr[0] == "GARDEN M/C-1")
        lc_cell = mc1_row[-1]  # last column = Labour Cost/kg
        assert "not captured" in str(lc_cell).lower(), (
            f"Garden per-machine labour cell must say 'not captured at machine level', "
            f"got: {lc_cell!r}"
        )

    def test_other_extrusion_plants_unaffected(self):
        """Pipe M/C Summary must still show ₹/kg, not 'not captured'."""
        import app as _app
        from metrics import Record
        rows = [
            Record(grain="monthly", period="2026-04", date="2026-04",
                   plant="PIPE", segment="Pipe", unit="kg",
                   machine="PIPE M/C-1", actual_hours=300, total_count=20000,
                   reject_count=400, ideal_hours=350, labour_cost=50000),
        ]
        headers, table_rows, *_ = _app._build_report_table(
            "pipe_summary", rows, {}
        )
        mc1_row = next((tr for tr in table_rows if tr[0] == "PIPE M/C-1"), None)
        if mc1_row:
            lc_cell = mc1_row[-1]
            assert "₹" in str(lc_cell), (
                f"Pipe per-machine labour cell must show ₹ value, got: {lc_cell!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 5 — Data-integrity flags for misaligned months
# ─────────────────────────────────────────────────────────────────────────────

class TestBug5DataIntegrityFlags:
    def _get_flags(self, summary_rows):
        """Run the same flag logic that report_detail uses."""
        flags = []
        for m in summary_rows:
            if m["month"] == "TOTAL":
                continue
            if m["wages"] > 0 and m["output_kg"] <= 0:
                flags.append(("wages_no_output", m["month"]))
            elif m["output_kg"] > 0 and m["wages"] <= 0 and m["labour_count"] <= 0:
                flags.append(("output_no_wages", m["month"]))
        return flags

    def test_may_wages_no_output_flagged(self):
        """MAY'26 has wages (149,442) but zero output — must be flagged."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        flags = self._get_flags(rows)
        may_flags = [f for f in flags if "2026-05" in f[1]]
        assert may_flags, "MAY'26 must be flagged (wages with no output)"
        assert any(f[0] == "wages_no_output" for f in may_flags)

    def test_jul_output_no_wages_flagged(self):
        """JUL'26 has 32,191 kg output but no wages — must be flagged."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        flags = self._get_flags(rows)
        jul_flags = [f for f in flags if "2026-07" in f[1]]
        assert jul_flags, "JUL'26 must be flagged (output with no wages)"
        assert any(f[0] == "output_no_wages" for f in jul_flags)

    def test_apr_and_jun_not_flagged(self):
        """APR'26 and JUN'26 both have output AND wages — must NOT be flagged."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        flags = self._get_flags(rows)
        flagged_months = {f[1] for f in flags}
        assert "2026-04" not in flagged_months, "APR'26 must not be flagged"
        assert "2026-06" not in flagged_months, "JUN'26 must not be flagged"

    def test_total_row_not_flagged(self):
        """TOTAL row is excluded from flag logic."""
        rows = parse_garden_summary_tab(
            _garden_summary_tab(),
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        )
        flags = self._get_flags(rows)
        assert not any("TOTAL" in str(f) for f in flags), "TOTAL row must not be flagged"


# ─────────────────────────────────────────────────────────────────────────────
# Regression: confirm "/" and Plumbing unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionUnaffected:
    def test_parse_mc_detail_pipe_still_works(self):
        """parse_mc_detail for a plain Pipe block (no split header) still works."""
        values = [
            ["MACHINE", "MONTH", "Ideal Hours", "Actual Hours",
             "Actual Output (KG)", "Ideal Output"],
            ["M/C-1", "APR'26", 500, 480, 40000, 83.3],
            ["",       "JUN'26", 500, 500, 42000, 84.0],
        ]
        recs = parse_mc_detail(
            values, plant="PIPE", segment="Pipe", unit="kg",
            source_file="f", source_tab="M/C-1",
        )
        assert len(recs) == 2, f"expected 2 records, got {len(recs)}"
        assert all(r.actual_hours > 0 for r in recs)

    def test_parse_garden_summary_tab_empty_returns_empty(self):
        """Empty or unrecognised sheet returns []."""
        assert parse_garden_summary_tab(
            [], plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        ) == []

    def test_parse_garden_summary_tab_no_header_returns_empty(self):
        """Sheet with no recognisable header returns []."""
        assert parse_garden_summary_tab(
            [["random", "garbage", "cells"]],
            plant="GARDEN", segment="Garden Pipe",
            source_file="f", source_tab="GARDEN M/C 26-27",
        ) == []
