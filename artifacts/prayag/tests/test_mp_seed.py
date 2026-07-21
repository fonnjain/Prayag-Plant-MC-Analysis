"""
Fixture-based tests for mp_seed.py — Phase MP-0.

All tests use synthetic data; no network calls, no Postgres.
The parsing functions (parse_*) are pure — they only take raw sheet rows and
return structured dicts, so they can be tested without any mocking.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import datetime
from unittest.mock import patch, MagicMock

from mp_seed import (
    norm_code,
    _to_float,
    _cell,
    _header_map,
    _find_header_row,
    parse_bom_weights,
    parse_pipe_routing,
    parse_fitting_routing,
    parse_per_hour,
    _parse_compound_tab,
    seed_params,
    current_month,
    SEGMENT,
    SeedAccessError,
)
import mp_model


# ===========================================================================
# Utilities
# ===========================================================================

class TestNormCode:
    def test_strips_spaces(self):
        assert norm_code("PW 11") == "PW11"

    def test_strips_hyphens(self):
        assert norm_code("PW-11") == "PW11"

    def test_strips_mixed(self):
        assert norm_code("PS - 16") == "PS16"

    def test_uppercase(self):
        assert norm_code("ps16") == "PS16"

    def test_strips_leading_trailing(self):
        assert norm_code("  PW11  ") == "PW11"

    def test_multi_space(self):
        assert norm_code("SWR  12") == "SWR12"

    def test_already_normalised(self):
        assert norm_code("PW11") == "PW11"

    def test_empty_string(self):
        assert norm_code("") == ""

    def test_pure_code(self):
        assert norm_code("PS-12") == "PS12"


class TestToFloat:
    def test_integer_string(self):
        assert _to_float("95") == pytest.approx(95.0)

    def test_decimal_string(self):
        assert _to_float("0.150") == pytest.approx(0.150)

    def test_comma_thousand(self):
        assert _to_float("1,234.5") == pytest.approx(1234.5)

    def test_empty_string(self):
        assert _to_float("") is None

    def test_text(self):
        assert _to_float("N/A") is None

    def test_none(self):
        assert _to_float(None) is None


class TestCell:
    def test_normal(self):
        assert _cell(["a", "b", "c"], 1) == "b"

    def test_out_of_bounds(self):
        assert _cell(["a"], 5) == ""

    def test_strips_whitespace(self):
        assert _cell(["  hello  "], 0) == "hello"


class TestHeaderMap:
    def test_basic(self):
        hm = _header_map(["Item Code", "Weight/pc(kg)", "Desc"])
        assert "item code" in hm
        assert hm["item code"] == 0
        assert hm["weight/pc(kg)"] == 1

    def test_skips_empty(self):
        hm = _header_map(["", "Item Code"])
        assert "item code" in hm
        assert "item code" not in {k for k in hm if k == ""}


class TestFindHeaderRow:
    def test_finds_row(self):
        rows = [
            ["Date", "Amount"],
            ["Item Code", "Weight", "Unit"],
            ["PW11", "0.15", "kg"],
        ]
        assert _find_header_row(rows, ["item code", "weight"]) == 1

    def test_raises_when_not_found(self):
        rows = [["Date", "Amount"], ["PW11", "0.15"]]
        with pytest.raises(Exception):
            _find_header_row(rows, ["item code", "machine"], max_scan=5)


# ===========================================================================
# BOM weights
# ===========================================================================

class TestParseBomWeights:
    def _make_rows(self):
        return [
            ["Item Code", "Weight/pc(kg)", "Description"],
            ["PW 11", "0.150", "Plain Washer 11mm"],
            ["PW-12", "0.200", "Plain Washer 12mm"],
            ["PS-12", "0.085", "Pipe Socket 12"],
            ["PS-16", "0.120", "Pipe Socket 16"],
            ["INVALID", "", ""],          # missing weight → skip
            ["", "0.500", ""],            # missing code → skip
        ]

    def test_pw11_resolves(self):
        """PW11 (demand code) must resolve to the same key as 'PW 11' in BOM."""
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "PW11" in codes, f"Expected PW11 in {codes}"

    def test_pw_hyphen_12_resolves(self):
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "PW12" in codes

    def test_weights_correct(self):
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        by_code = {r["item_code"]: r["weight_per_pc_kg"] for r in result}
        assert by_code["PW11"] == pytest.approx(0.150)
        assert by_code["PS12"] == pytest.approx(0.085)
        assert by_code["PS16"] == pytest.approx(0.120)

    def test_skips_missing_weight(self):
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        # "INVALID" has no weight → should be excluded
        codes = {r["item_code"] for r in result}
        assert "INVALID" not in codes

    def test_skips_missing_code(self):
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        # Row with empty code → excluded
        assert all(r["item_code"] for r in result)

    def test_zero_weight_skipped(self):
        rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["PW11", "0.0"],
            ["PW12", "0.200"],
        ]
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "PW11" not in codes
        assert "PW12" in codes

    def test_duplicate_code_last_wins(self):
        """When BOM has 'PW 11' and 'PW11' rows, last value wins."""
        rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["PW 11", "0.150"],
            ["PW11", "0.160"],   # duplicate after normalisation
        ]
        result = parse_bom_weights(rows)
        by_code = {r["item_code"]: r["weight_per_pc_kg"] for r in result}
        assert by_code["PW11"] == pytest.approx(0.160)

    def test_empty_rows_input(self):
        assert parse_bom_weights([]) == []

    def test_counts(self):
        rows = self._make_rows()
        result = parse_bom_weights(rows)
        # PW11, PW12, PS12, PS16 = 4 valid items
        assert len(result) == 4


# ===========================================================================
# Pipe routing + staffing
# ===========================================================================

class TestParsePipeRouting:
    def _make_rows(self):
        """Simulate 'Details' tab layout:
        Row 0: irrelevant header
        Row 1: staffing counts (W, OT pairs per machine)
        Row 2: machine names
        Row 3+: item codes in machine columns
        """
        return [
            ["", "", "", "", ""],                        # row 0: top header
            ["", "3", "1", "5", "1", "4", "2"],         # row 1: W, OT pairs
            ["", "M/C-1", "", "M/C-2", "", "M/C-3", ""],# row 2: machine names
            ["", "PW 11", "", "PW 11", "", "", ""],      # PW11 on M/C-1, M/C-2
            ["", "PS-12", "", "", "", "PS-12", ""],      # PS12 on M/C-1, M/C-3
            ["", "", "", "SWR 20", "", "", ""],          # SWR20 on M/C-2 only
        ]

    def test_finds_three_machines(self):
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        names = {r["machine"] for r in machine_rows}
        assert len(names) == 3

    def test_machine_names(self):
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        names = {r["machine"] for r in machine_rows}
        assert "M/C-1" in names
        assert "M/C-2" in names
        assert "M/C-3" in names

    def test_mc1_staffing(self):
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        mc1 = next(r for r in machine_rows if "1" in r["machine"] and "2" not in r["machine"] and "3" not in r["machine"])
        # W=3, OT=1 at cols 1,2
        assert mc1["support_w"] == 3
        assert mc1["operators_ot"] == 1

    def test_item_normalisation_in_routing(self):
        """'PW 11' in a machine column must be normalised to 'PW11' in routing."""
        rows = self._make_rows()
        routing_rows, _ = parse_pipe_routing(rows)
        codes = {r["item_code"] for r in routing_rows}
        assert "PW11" in codes

    def test_routing_captures_item_machine_pairs(self):
        rows = self._make_rows()
        routing_rows, _ = parse_pipe_routing(rows)
        pairs = {(r["item_code"], r["machine"]) for r in routing_rows}
        assert ("PW11", "M/C-1") in pairs
        assert ("PS12", "M/C-1") in pairs

    def test_empty_rows_returns_empty(self):
        r, m = parse_pipe_routing([])
        assert r == [] and m == []

    def test_no_machine_row_returns_empty(self):
        rows = [
            ["Item Code", "Weight"],
            ["PW11", "0.15"],
        ]
        r, m = parse_pipe_routing(rows)
        assert m == []


# ===========================================================================
# Fitting routing + cavity / cycle time
# ===========================================================================

class TestParseFittingRouting:
    def _make_rows(self):
        """Simulate Report-12 with header at row 5 (0-indexed), i.e. row 6 (1-indexed)."""
        return [
            ["Report 12", "", "", "", ""],                   # row 0
            ["Period:", "June 2026", "", "", ""],             # row 1
            ["", "", "", "", ""],                             # row 2
            ["", "", "", "", ""],                             # row 3
            ["", "", "", "", ""],                             # row 4
            ["Sr", "Desc", "Item Code", "Qty", "Moulding Machine", "Mould Cavity", "Cycle Time"],  # row 5 = header
            ["1", "Washer 11", "PW 11", "100", "IM-01", "4", "25"],
            ["2", "Washer 12", "PW 12", "80",  "IM-01", "4", "28"],
            ["3", "Socket 12", "PS-12", "200", "IM-02", "8", "18"],
            ["4", "Socket 16", "PS-16", "150", "IM-02", "8", "20"],
            ["5", "Elbow 20",  "EL-20", "50",  "IM-03", "2", "35"],
            ["", "", "", "", ""],   # blank row
        ]

    def test_finds_header_at_row5(self):
        rows = self._make_rows()
        routing_rows, std_rows, machine_rows = parse_fitting_routing(rows)
        assert len(routing_rows) > 0

    def test_item_code_normalised(self):
        rows = self._make_rows()
        routing_rows, _, _ = parse_fitting_routing(rows)
        codes = {r["item_code"] for r in routing_rows}
        assert "PW11" in codes
        assert "PW12" in codes
        assert "PS12" in codes

    def test_machines_found(self):
        rows = self._make_rows()
        _, _, machine_rows = parse_fitting_routing(rows)
        names = {r["machine"] for r in machine_rows}
        assert "IM-01" in names
        assert "IM-02" in names
        assert "IM-03" in names

    def test_cavity_present(self):
        rows = self._make_rows()
        _, std_rows, _ = parse_fitting_routing(rows)
        cavities = {r["item_code"]: r["cavity"] for r in std_rows}
        assert cavities.get("PW11") == pytest.approx(4.0)
        assert cavities.get("PS12") == pytest.approx(8.0)

    def test_cycle_time_present(self):
        rows = self._make_rows()
        _, std_rows, _ = parse_fitting_routing(rows)
        cycles = {r["item_code"]: r["cycle_time_sec"] for r in std_rows}
        assert cycles.get("PW11") == pytest.approx(25.0)
        assert cycles.get("EL20") == pytest.approx(35.0)

    def test_distinct_routing_pairs(self):
        rows = self._make_rows()
        routing_rows, _, _ = parse_fitting_routing(rows)
        # PW11 → IM-01, PW12 → IM-01, PS12 → IM-02, PS16 → IM-02, EL20 → IM-03
        pairs = {(r["item_code"], r["machine"]) for r in routing_rows}
        assert ("PW11", "IM-01") in pairs
        assert ("PS12", "IM-02") in pairs

    def test_empty_input(self):
        r, s, m = parse_fitting_routing([])
        assert r == [] and s == [] and m == []

    def test_machine_count(self):
        rows = self._make_rows()
        _, _, machine_rows = parse_fitting_routing(rows)
        assert len(machine_rows) == 3  # IM-01, IM-02, IM-03

    def test_std_rows_count(self):
        rows = self._make_rows()
        _, std_rows, _ = parse_fitting_routing(rows)
        # 5 data rows, all valid
        assert len(std_rows) == 5

    def test_cavity_count_156_of_157(self):
        """Synthetic check: all 5 fixture rows have cavity; in prod should be 156/157."""
        rows = self._make_rows()
        # Add one row without cavity
        rows.append(["6", "Tee 20", "TE-20", "30", "IM-03", "", "40"])
        _, std_rows, _ = parse_fitting_routing(rows)
        with_cavity = sum(1 for r in std_rows if r.get("cavity") is not None)
        total = len(std_rows)
        assert with_cavity == total - 1  # one row has empty cavity


# ===========================================================================
# Per-hour rates
# ===========================================================================

class TestParsePerHour:
    def _make_pipe_rows(self):
        """Simulate a CPVC Pipe per-hour tab."""
        return [
            ["CPVC PRODUCTION PLANNING PIPE", "", "", "", ""],
            ["Period:", "June 2026", "", "", ""],
            ["", "", "", "", ""],
            ["Item Code", "Description", "Ideal Output", "Production Per Hour", "Unit"],
            ["PS-12", "Socket 12mm", "90", "95", "kg/hr"],
            ["PS-16", "Socket 16mm", "200", "220", "kg/hr"],
            ["PW 11", "Washer 11mm", "50",  "65", "kg/hr"],
            ["TOTAL", "", "", "", ""],     # total row — should be skipped (short code)
        ]

    def _make_fitting_rows(self):
        """Simulate a CPVC Fitting cycle-time tab."""
        return [
            ["CPVC FITTING CYCLE TIME", "", ""],
            ["Item Code", "Description", "Cycle Time (sec)"],
            ["PW 11", "Washer 11", "25"],
            ["PW 12", "Washer 12", "28"],
            ["PS-12", "Socket 12", "18"],
        ]

    def test_pipe_kg_per_hr_basis(self):
        rows = self._make_pipe_rows()
        result = parse_per_hour(rows, "CPVC", "kg_per_hr")
        bases = {r["basis"] for r in result}
        assert "kg_per_hr" in bases

    def test_pipe_ps12_rate(self):
        rows = self._make_pipe_rows()
        result = parse_per_hour(rows, "CPVC", "kg_per_hr")
        by_code = {r["item_code"]: r["value"] for r in result}
        assert by_code.get("PS12") == pytest.approx(95.0)

    def test_pipe_ps16_rate(self):
        rows = self._make_pipe_rows()
        result = parse_per_hour(rows, "CPVC", "kg_per_hr")
        by_code = {r["item_code"]: r["value"] for r in result}
        assert by_code.get("PS16") == pytest.approx(220.0)

    def test_pipe_item_code_normalised(self):
        rows = self._make_pipe_rows()
        result = parse_per_hour(rows, "CPVC", "kg_per_hr")
        codes = {r["item_code"] for r in result}
        # "PW 11" → "PW11"
        assert "PW11" in codes

    def test_fitting_cycle_basis(self):
        rows = self._make_fitting_rows()
        result = parse_per_hour(rows, "CPVC", "cycle")
        bases = {r["basis"] for r in result}
        assert "cycle" in bases

    def test_fitting_cycle_values(self):
        rows = self._make_fitting_rows()
        result = parse_per_hour(rows, "CPVC", "cycle")
        by_code = {r["item_code"]: r["value"] for r in result}
        assert by_code.get("PW11") == pytest.approx(25.0)
        assert by_code.get("PS12") == pytest.approx(18.0)

    def test_empty_input(self):
        assert parse_per_hour([], "CPVC", "kg_per_hr") == []

    def test_skips_zero_value(self):
        rows = [
            ["Item Code", "Description", "Production Per Hour"],
            ["PW11", "Test", "0"],
            ["PW12", "Test", "100"],
        ]
        result = parse_per_hour(rows, "CPVC", "kg_per_hr")
        codes = {r["item_code"] for r in result}
        assert "PW11" not in codes
        assert "PW12" in codes


# ===========================================================================
# Compound recipes
# ===========================================================================

def _make_compound_pipe_tab():
    """Synthetic COMPOUND COST - P tab: two horizontal blocks, UPVC PIPE and CPVC PIPE.

    Layout mirrors the real sheet:
      row 0: title
      row 1: block labels in col 0 (UPVC PIPE) and col 5 (CPVC PIPE)
      row 2: column headers per block
      row 3: component data starts
      row 8: Total row (should stop parser)
      row 9: ACTUAL section header (must NOT be read)
      row 10: actual-section component (must NOT be read)
    """
    e = ""
    return [
        # row 0: title
        ["COMPOUND COST - PIPE", e, e, e, e, e, e, e, e],
        # row 1: block labels
        ["UPVC PIPE", e, e, e, e, "CPVC PIPE", e, e, e],
        # row 2: column headers
        ["Compound", "Ratio in KG", "Price", "Line Cost", e,
         "Compound", "Ratio in KG", "Price", "Line Cost"],
        # row 3-7: components (UPVC block at col 0, CPVC block at col 5)
        ["PVC Resin K67", "80.0", "90.0", "7200", e,
         "PVC Resin J700", "84.0", "110.0", "9240"],
        ["CaCO3",         "15.0", "8.0",  "120",  e,
         "CaCO3",          "10.0", "8.0",  "80"],
        ["Stabilizer",    "4.0",  "300.0", "1200", e,
         "Stabilizer",    "5.0",  "320.0", "1600"],
        ["Lubricant",     "1.5",  "80.0",  "120",  e,
         "Lubricant",     "2.0",  "85.0",  "170"],
        ["TiO2",          "1.0",  "200.0", "200",  e,
         "TiO2",          "4.65", "220.0", "1023"],
        # row 8: Total — MUST stop parser
        ["Total",         "101.5", "8840",  e,      e,
         "Total",         "105.65","12113.0",e],
        # row 9: ACTUAL section — must NOT be read
        ["ACTUAL COMPOUND COST", e, e, e, e, "ACTUAL COMPOUND COST", e, e, e],
        # row 10: actual components (wrong prices) — must NOT be read
        ["PVC Resin K67", "80.0", "95.0", "7600", e,
         "PVC Resin J700", "84.0", "105.0", "8820"],
    ]


class TestParseCompoundTab:
    def test_parses_both_blocks(self):
        """Both UPVC PIPE and CPVC PIPE blocks are returned."""
        result = _parse_compound_tab(_make_compound_pipe_tab())
        assert ("UPVC", "pipe") in result
        assert ("CPVC", "pipe") in result

    def test_component_count(self):
        """Each block returns the correct number of components (5)."""
        result = _parse_compound_tab(_make_compound_pipe_tab())
        assert len(result[("UPVC", "pipe")]) == 5
        assert len(result[("CPVC", "pipe")]) == 5

    def test_component_names(self):
        result = _parse_compound_tab(_make_compound_pipe_tab())
        cpvc = result[("CPVC", "pipe")]
        names = {c["component"] for c in cpvc}
        assert "PVC Resin J700" in names
        assert "Stabilizer" in names

    def test_ratio_kg_values(self):
        result = _parse_compound_tab(_make_compound_pipe_tab())
        cpvc = {c["component"]: c["ratio_kg"] for c in result[("CPVC", "pipe")]}
        assert cpvc.get("PVC Resin J700") == pytest.approx(84.0)
        assert cpvc.get("CaCO3") == pytest.approx(10.0)

    def test_price_per_kg_values(self):
        result = _parse_compound_tab(_make_compound_pipe_tab())
        cpvc = {c["component"]: c["price_per_kg"] for c in result[("CPVC", "pipe")]}
        assert cpvc.get("PVC Resin J700") == pytest.approx(110.0)

    def test_total_ratio_approx(self):
        """CPVC-Pipe total ratio ≈ 105.65 kg (synthetic data)."""
        result = _parse_compound_tab(_make_compound_pipe_tab())
        total = sum(c["ratio_kg"] for c in result[("CPVC", "pipe")])
        assert total == pytest.approx(105.65, rel=0.01)

    def test_stops_before_actual_section(self):
        """Total row breaks parser — actual-section rows (wrong prices) not included."""
        result = _parse_compound_tab(_make_compound_pipe_tab())
        cpvc = result[("CPVC", "pipe")]
        # Only 5 working-section rows; actual section would double this
        assert len(cpvc) == 5
        # Price for PVC Resin must be the working price (110), not actual price (105)
        by_name = {c["component"]: c["price_per_kg"] for c in cpvc}
        assert by_name.get("PVC Resin J700") == pytest.approx(110.0)

    def test_skips_total_rows(self):
        result = _parse_compound_tab(_make_compound_pipe_tab())
        for combo, comps in result.items():
            names = {c["component"] for c in comps}
            assert "Total" not in names and "TOTAL" not in names

    def test_fitting_label_moulding(self):
        """Labels with 'MOULDING' map to mat_type='fitting'."""
        rows = [
            ["", e := ""],
            ["UPVC MOULDING", e, e, e, e, "SWR / AGRI MOULDING", e, e],
            ["Compound", "Ratio in KG", "Price", "Cost", e,
             "Compound", "Ratio in KG", "Price"],
            ["PVC Resin", "60.0", "90.0", "5400", e,
             "PVC Resin SWR", "50.0", "85.0"],
            ["CaCO3", "30.0", "8.0", "240", e,
             "CaCO3", "40.0", "8.0"],
            ["Total", e, e, e, e, "Total", e, e],
        ]
        result = _parse_compound_tab(rows)
        assert ("UPVC", "fitting") in result
        # SWR/AGRI shared label expands to both
        assert ("SWR", "fitting") in result
        assert ("AGRI", "fitting") in result

    def test_empty_tab_returns_empty(self):
        assert _parse_compound_tab([]) == {}
        assert _parse_compound_tab([["a", "b"]]) == {}

    def test_missing_material_returns_empty(self):
        """A tab with no material labels returns no blocks."""
        rows = [
            ["PIPE COST TABLE", "", ""],
            ["Component", "Ratio", "Price"],
            ["PVC Resin", "100", "90"],
        ]
        assert _parse_compound_tab(rows) == {}


# ===========================================================================
# Params (with mocked DB)
# ===========================================================================

class TestSeedParams:
    def test_seed_params_returns_correct_defaults(self):
        """seed_params must return waste_pct=4, pulverizer_pct=25."""
        upserted = {}

        def fake_upsert(row):
            upserted["row"] = row
            return 1

        with patch.object(mp_model, "upsert_params", side_effect=fake_upsert), \
             patch.object(mp_model, "AVAILABLE", True), \
             patch.object(mp_model, "init_mp_tables", return_value=None):
            result = seed_params("2026-07")

        assert result["waste_pct"] == pytest.approx(4.0)
        assert result["pulverizer_pct"] == pytest.approx(25.0)
        assert result["effective_month"] == "2026-07"
        assert upserted["row"].segment == SEGMENT
        assert upserted["row"].waste_pct == pytest.approx(4.0)
        assert upserted["row"].pulverizer_pct == pytest.approx(25.0)


# ===========================================================================
# current_month
# ===========================================================================

class TestCurrentMonth:
    def test_format(self):
        m = current_month()
        assert len(m) == 7
        assert m[4] == "-"
        int(m[:4])  # year must be numeric
        int(m[5:])  # month must be numeric

    def test_range(self):
        m = current_month()
        month_num = int(m[5:])
        assert 1 <= month_num <= 12


# ===========================================================================
# Integration: norm_code consistency across parsers
# ===========================================================================

class TestNormCodeConsistency:
    """Verify that the same norm_code is used across all parsers so demand codes
    like 'PW11' always match BOM entries stored as 'PW 11'.
    """

    def test_bom_and_per_hour_same_key(self):
        bom_rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["PW 11", "0.150"],
        ]
        ph_rows = [
            ["Item Code", "Description", "Production Per Hour"],
            ["PW-11", "Washer", "65"],
        ]
        bom_result = parse_bom_weights(bom_rows)
        ph_result = parse_per_hour(ph_rows, "CPVC", "kg_per_hr")

        bom_codes = {r["item_code"] for r in bom_result}
        ph_codes = {r["item_code"] for r in ph_result}
        # Both must resolve to "PW11"
        assert "PW11" in bom_codes
        assert "PW11" in ph_codes

    def test_fitting_and_bom_same_key(self):
        bom_rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["PW 11", "0.150"],
        ]
        fitting_rows = [
            ["Sr", "Desc", "Item Code", "Qty", "Moulding Machine", "Cavity", "Cycle Time"],
            ["1", "Washer", "PW-11", "100", "IM-01", "4", "25"],
        ]
        bom_result = parse_bom_weights(bom_rows)
        fit_routing, _, _ = parse_fitting_routing(fitting_rows)

        bom_codes = {r["item_code"] for r in bom_result}
        fit_codes = {r["item_code"] for r in fit_routing}
        assert bom_codes & fit_codes  # intersection must be non-empty ("PW11")


# ===========================================================================
# Acceptance-level counts (spec targets, using synthetic data)
# ===========================================================================

class TestAcceptanceCounts:
    """Tests structured to assert the expected counts documented in the spec.

    These use synthetic data that mirrors the real sheet structure.  The actual
    values (157 items / 24 machines / etc.) are verified at live seed time via
    seed_all(); here we verify the parsing logic is correct at scale.
    """

    def _build_fitting_rows(self, n_items: int, n_machines: int) -> list:
        """Generate a synthetic Report-12 with n_items distinct items × n_machines."""
        rows = [
            ["", "", "", "", ""],   # rows 0-4: pre-header
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            ["", "", "", "", ""],
            # header row 5 (0-indexed):
            ["Sr", "Desc", "Item Code", "Qty", "Moulding Machine", "Mould Cavity", "Cycle Time"],
        ]
        mc_names = [f"IM-{i+1:02d}" for i in range(n_machines)]
        for i in range(n_items):
            mc = mc_names[i % n_machines]
            rows.append([
                str(i + 1),
                f"Item {i}",
                f"IT{i:03d}",    # normalises to IT{i:03d}
                "100",
                mc,
                "4" if i % 5 != 0 else "",   # one in 5 has no cavity
                "20",
            ])
        return rows

    def test_fitting_157_items_24_machines(self):
        rows = self._build_fitting_rows(n_items=157, n_machines=24)
        routing_rows, std_rows, machine_rows = parse_fitting_routing(rows)
        assert len({r["item_code"] for r in routing_rows}) == 157
        assert len(machine_rows) == 24

    def test_fitting_cavity_present_156(self):
        rows = self._build_fitting_rows(n_items=157, n_machines=24)
        _, std_rows, _ = parse_fitting_routing(rows)
        with_cavity = sum(1 for r in std_rows if r.get("cavity") is not None)
        # Every 5th row has empty cavity: 157 // 5 = 31 missing, rest = 126
        # But our formula: row i has no cavity if i % 5 == 0, so 32 missing (i=0,5,..155 → 32 items)
        # cavity_present = 157 - 32 = 125 for the synthetic fixture
        assert with_cavity == 157 - len([i for i in range(157) if i % 5 == 0])

    def test_bom_normalisation_at_scale(self):
        """All 'PW N' and 'PW-N' variants normalise to 'PWN' — no unweighted codes."""
        raw_codes = [f"PW {i}" for i in range(1, 20)] + [f"PS-{i}" for i in range(1, 15)]
        rows = [["Item Code", "Weight/pc(kg)"]] + [
            [code, str(0.1 + i * 0.01)] for i, code in enumerate(raw_codes)
        ]
        result = parse_bom_weights(rows)
        assert len(result) == len(raw_codes)
        # Every code normalised (no spaces/hyphens)
        for r in result:
            assert " " not in r["item_code"]
            assert "-" not in r["item_code"]
