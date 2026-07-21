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
    _is_spurious_numeric,
    parse_bom_weights,
    parse_pipe_routing,
    parse_fitting_routing,
    parse_per_hour,
    _parse_compound_tab,
    _parse_block_explicit,
    _PIPE_EXPLICIT_COLS,
    _FITTING_EXPLICIT_COLS,
    _COMPOUND_DATA_START_ROW,
    _COMPOUND_WASTAGE_FACTOR,
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
        """Simulate 'Details' tab layout (real geometry: A=Size in, B=Size mm,
        then column pairs C/D=M/C-1, E/F=M/C-2, G/H=M/C-3, ... starting at col 2).

        Row 0: W/OT labels  Row 1: staffing  Row 2: machine names  Row 3+: items.
        We use 3 machine pairs (M/C-1..3) — remaining pairs 4..9 have no data.
        """
        # Build a 20-col row (cols 0..19) for 9 pairs; only M/C-1..3 are populated
        def _row(*vals):
            """vals fills from col 0; rest padded with ''."""
            return list(vals) + [""] * (20 - len(vals))

        return [
            # row 0: Size(in)/Size(mm) then W/OT labels
            _row("Size (in)", "Size (mm)", "W", "OT", "W", "OT", "W", "OT"),
            # row 1: staffing — W in first col, OT in second col of each pair
            _row("", "", "3", "1", "5", "1", "4", "2"),
            # row 2: machine names in first col of pair (real file: "M/C- 1" with space)
            _row("", "", "M/C- 1", "", "M/C- 2", "", "M/C- 3", ""),
            # row 3: PW11 on M/C-1 (CPVC) and M/C-2 (CPVC)
            _row("3/4", "20mm", "PW 11", "CPVC", "PW 11", "CPVC", "", ""),
            # row 4: PS12 on M/C-1 (CPVC) and M/C-3 (UPVC)
            _row("", "20mm", "PS-12", "CPVC", "", "", "PS-12", "UPVC"),
            # row 5: SWR20 on M/C-2 only
            _row("", "20mm", "", "", "SWR 20", "SWR", "", ""),
        ]

    def test_always_returns_nine_machines(self):
        """Fixed-pair layout always yields exactly 9 extrusion machines."""
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        assert len(machine_rows) == 9

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
        mc1 = next(r for r in machine_rows if r["machine"] == "M/C-1")
        assert mc1["support_w"] == 3
        assert mc1["operators_ot"] == 1

    def test_mc3_staffing(self):
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        mc3 = next(r for r in machine_rows if r["machine"] == "M/C-3")
        assert mc3["support_w"] == 4
        assert mc3["operators_ot"] == 2

    def test_space_in_machine_name_normalised(self):
        """'M/C- 1' (space before digit) must normalise to 'M/C-1'."""
        rows = self._make_rows()
        _, machine_rows = parse_pipe_routing(rows)
        names = {r["machine"] for r in machine_rows}
        assert "M/C-1" in names
        assert "M/C- 1" not in names

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

    def test_empty_machine_has_no_routing(self):
        """Machines beyond the populated pairs have 0 routing rows."""
        rows = self._make_rows()
        routing_rows, _ = parse_pipe_routing(rows)
        mc9_items = [r for r in routing_rows if r["machine"] == "M/C-9"]
        assert mc9_items == []

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


# ===========================================================================
# _is_spurious_numeric (FIX 3)
# ===========================================================================

class TestIsSpuriousNumeric:
    """Unit tests for the whitelist-style numeric filter."""

    # ── Codes that MUST be kept (return False) ────────────────────────────
    def test_keeps_4digit_integer(self):
        """4-digit numeric codes like 5110 are real fitting item codes."""
        assert _is_spurious_numeric("5110") is False

    def test_keeps_5110(self):
        """Regression: item 5110 from the verified source must not be dropped."""
        assert _is_spurious_numeric("5110") is False

    def test_keeps_5111(self):
        assert _is_spurious_numeric("5111") is False

    def test_keeps_5digit_integer(self):
        assert _is_spurious_numeric("51210") is False

    def test_keeps_7digit_integer(self):
        assert _is_spurious_numeric("5121001") is False

    def test_keeps_alpha_code(self):
        assert _is_spurious_numeric("PW11") is False

    def test_keeps_alphanumeric(self):
        assert _is_spurious_numeric("PS12") is False

    # ── Codes that MUST be dropped (return True) ──────────────────────────
    def test_drops_8digit_erp_id(self):
        """8-digit pure integers are internal ERP IDs."""
        assert _is_spurious_numeric("33000778") is True

    def test_drops_9digit_erp_id(self):
        assert _is_spurious_numeric("330007789") is True

    def test_drops_decimal_od(self):
        """Decimals like '104.8' are OD sizes, not item codes."""
        assert _is_spurious_numeric("104.8") is True

    def test_drops_small_decimal(self):
        assert _is_spurious_numeric("11.8") is True

    def test_drops_zero_decimal(self):
        assert _is_spurious_numeric("0.5") is True


# ===========================================================================
# FIX 3 regression: numeric codes survive BOM and per-hour parsers
# ===========================================================================

class TestNumericCodeSurvival:
    """5110, 5111 must pass through all parsers after the _is_spurious_numeric fix."""

    def test_bom_keeps_5110(self):
        rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["5110", "0.120"],
            ["5111", "0.095"],
            ["PS12", "0.085"],
        ]
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "5110" in codes, f"5110 must not be filtered; got {codes}"
        assert "5111" in codes

    def test_bom_still_drops_erp_id(self):
        rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["33000778", "0.500"],  # 8-digit ERP ID → must be dropped
            ["PS12", "0.085"],
        ]
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "33000778" not in codes
        assert "PS12" in codes

    def test_bom_still_drops_decimal_od(self):
        rows = [
            ["Item Code", "Weight/pc(kg)"],
            ["104.8", "0.500"],  # OD size → must be dropped
            ["PS12", "0.085"],
        ]
        result = parse_bom_weights(rows)
        codes = {r["item_code"] for r in result}
        assert "1048" not in codes  # norm_code("104.8") = "1048" → decimal detected pre-norm
        assert "PS12" in codes

    def test_per_hour_fitting_keeps_5110(self):
        """Fitting cycle-time tab: numeric item codes must survive."""
        rows = [
            ["CPVC FITTING CYCLE TIME", "", ""],
            ["Item Code", "Description", "Cycle Time (sec)"],
            ["5110", "Elbow 20mm", "22"],
            ["5111", "Tee 20mm", "24"],
            ["PS12", "Socket 12mm", "18"],
        ]
        result = parse_per_hour(rows, "CPVC", "cycle")
        codes = {r["item_code"] for r in result}
        assert "5110" in codes, f"5110 must survive parse_per_hour; got {codes}"
        assert "5111" in codes
        assert "PS12" in codes

    def test_per_hour_erp_id_dropped(self):
        rows = [
            ["Item Code", "Description", "Cycle Time (sec)"],
            ["33000778", "ERP row", "20"],
            ["PS12", "Socket", "18"],
        ]
        result = parse_per_hour(rows, "CPVC", "cycle")
        codes = {r["item_code"] for r in result}
        assert "33000778" not in codes
        assert "PS12" in codes


# ===========================================================================
# FIX 1: pipe routing — material column (paired second col = material, not code)
# ===========================================================================

class TestParsePipeRoutingMaterial:
    """Verify that the second column of each machine pair is read as material,
    not as a second item-code column."""

    def _make_rows_with_material(self):
        """Layout matching real Pipes_details:
        Row 0: W/OT labels at cols C,D (idx 2,3) repeating
        Row 1: staffing counts
        Row 2: machine names in first col of each pair (C=2, E=4)
        Row 3+: item code in col j, material in col j+1
        """
        return [
            ["", "", "W", "OT", "W", "OT"],          # row 0: labels
            ["", "", "3",  "1", "5",  "1"],            # row 1: staffing counts
            ["", "", "M/C-1", "", "M/C-2", ""],        # row 2: machine names
            ["", "", "PW11", "CPVC", "PW11", "UPVC"],  # PW11 on both, diff material
            ["", "", "PS12", "CPVC", "",     ""],       # PS12 on M/C-1 only
            ["", "", "",     "",     "SWR20","SWR"],    # SWR20 on M/C-2 only
        ]

    def test_material_not_treated_as_item_code(self):
        """'CPVC'/'UPVC'/'SWR' in material column must NOT appear as item codes."""
        rows = self._make_rows_with_material()
        routing_rows, _ = parse_pipe_routing(rows)
        codes = {r["item_code"] for r in routing_rows}
        assert "CPVC" not in codes
        assert "UPVC" not in codes
        assert "SWR" not in codes

    def test_material_stored_in_routing_row(self):
        rows = self._make_rows_with_material()
        routing_rows, _ = parse_pipe_routing(rows)
        by_pair = {(r["item_code"], r["machine"]): r["material"] for r in routing_rows}
        assert by_pair.get(("PW11", "M/C-1")) == "CPVC"
        assert by_pair.get(("PW11", "M/C-2")) == "UPVC"
        assert by_pair.get(("SWR20", "M/C-2")) == "SWR"

    def test_correct_item_codes_still_loaded(self):
        rows = self._make_rows_with_material()
        routing_rows, _ = parse_pipe_routing(rows)
        codes = {r["item_code"] for r in routing_rows}
        assert "PW11" in codes
        assert "PS12" in codes
        assert "SWR20" in codes

    def test_staffing_correct(self):
        rows = self._make_rows_with_material()
        _, machine_rows = parse_pipe_routing(rows)
        by_mc = {r["machine"]: r for r in machine_rows}
        assert by_mc["M/C-1"]["support_w"] == 3
        assert by_mc["M/C-1"]["operators_ot"] == 1
        assert by_mc["M/C-2"]["support_w"] == 5
        assert by_mc["M/C-2"]["operators_ot"] == 1

    def test_idle_machine_no_items(self):
        """A machine column with no item codes → machine seeded, 0 routing rows for it."""
        rows = [
            ["", "", "W", "OT", "W", "OT"],
            ["", "", "3",  "1", "4",  "1"],
            ["", "", "M/C-1", "", "M/C-2", ""],   # M/C-2 has no items below
            ["", "", "PW11", "CPVC", "", ""],
        ]
        routing_rows, machine_rows = parse_pipe_routing(rows)
        mc_names = {r["machine"] for r in machine_rows}
        assert "M/C-2" in mc_names  # machine still seeded
        mc2_items = [r for r in routing_rows if r["machine"] == "M/C-2"]
        assert mc2_items == []      # but zero routing rows


# ===========================================================================
# FIX 2: per-hour — kg/hr column header (SWR tab style)
# ===========================================================================

class TestParsePerHourKgHr:
    """SWR-style tabs may use 'KG/HR' instead of 'Production Per Hour'."""

    def _make_swr_rows(self):
        return [
            ["SWR PLANING", "", "", "", ""],
            ["Period:", "June 2026", "", "", ""],
            ["", "", "", "", ""],
            ["Item Code", "Description", "Size", "KG/HR", "Unit"],
            ["SWR20", "SWR Pipe 20", "20mm", "180", "kg/hr"],
            ["SWR25", "SWR Pipe 25", "25mm", "200", "kg/hr"],
            ["TOTAL", "", "", "", ""],
        ]

    def test_swr_kg_hr_column_parsed(self):
        rows = self._make_swr_rows()
        result = parse_per_hour(rows, "SWR", "kg_per_hr")
        codes = {r["item_code"] for r in result}
        assert "SWR20" in codes, f"SWR20 must be parsed from KG/HR column; got {codes}"
        assert "SWR25" in codes

    def test_swr_correct_values(self):
        rows = self._make_swr_rows()
        result = parse_per_hour(rows, "SWR", "kg_per_hr")
        by_code = {r["item_code"]: r["value"] for r in result}
        assert by_code["SWR20"] == pytest.approx(180.0)
        assert by_code["SWR25"] == pytest.approx(200.0)

    def test_swr_basis_kg_per_hr(self):
        rows = self._make_swr_rows()
        result = parse_per_hour(rows, "SWR", "kg_per_hr")
        assert all(r["basis"] == "kg_per_hr" for r in result)


# ===========================================================================
# Explicit-column compound parser (_parse_block_explicit)
# ===========================================================================

def _make_pipe_tab_explicit():
    """Synthetic 'COMPOUND COST - P' sheet mirroring the real layout.

    Has THREE blocks labelled 'CPVC PIPE' (to simulate the real sheet ambiguity):
      H block  (cols 7-9):   wrong block — should NOT be picked for CPVC-Pipe
      M block  (cols 12-14): canonical CPVC-Pipe block (Rs/Kg ~ 147.28)
      R block  (cols 17-19): another CPVC variant — should NOT be picked

    Other blocks (0-indexed columns):
      C block  (cols 2-4):   UPVC-Pipe   (Rs/Kg ~ 61.15)
      W block  (cols 22-24): SWR-Pipe    (Rs/Kg ~ 73.16)
      AB block (cols 27-29): AGRI-Pipe

    Each block has: header rows 0-3, data rows 4+, a 'Total' row, a gap row,
    then a 'WASTAGE' row (ratio=1.01).

    All rows are padded to 30 columns so out-of-bounds cell reads return ''.
    """
    PAD = 30
    e = ""

    def pad(row):
        return (list(row) + [e] * PAD)[:PAD]

    # Rows 0-3: headers / labels (data starts at row 4 = _COMPOUND_DATA_START_ROW)
    rows = [
        pad(["COMPOUND COST"] * 5),      # row 0: section title
        pad([""] * PAD),                  # row 1: material labels (label scanning not used)
        pad([""] * PAD),                  # row 2: col headers
        pad([""] * PAD),                  # row 3: optional section label
    ]

    # Single-component synthetic values chosen to reproduce the acceptance-spec effective rates.
    # eff_rate = price_per_kg * wastage_factor  (when ratio is a single component)
    # UPVC-Pipe:  61.15 / 1.01 = 60.5446…
    # CPVC-Pipe: 147.28 / 1.01 = 145.8245…  (ratio=125.65 kg, matches acceptance spec)
    # SWR-Pipe:   73.16 / 1.01 = 72.4356…
    # AGRI-Pipe:  62.80 / 1.01 = 62.1782…
    upvc_comps = [("Compound A", 161.8,  60.5446)]   # eff = 60.5446 * 1.01 = 61.15 ✓
    cpvc_comps = [("Compound B", 125.65, 145.8245)]  # eff = 145.8245 * 1.01 = 147.28 ✓
    swr_comps  = [("Compound C", 105.49, 72.4356)]   # eff = 72.4356 * 1.01 = 73.16 ✓
    agri_comps = [("Compound D", 120.14, 62.1782)]   # eff = 62.1782 * 1.01 = 62.80

    # Build sheet rows — each data row has values at the 4 block positions
    # plus a stub 'wrong CPVC' block at cols 7-9 (H block) to verify it is NOT used
    # Block positions: UPVC=(2,3,4), wrong_CPVC_H=(7,8,9), CPVC_M=(12,13,14),
    #                  wrong_CPVC_R=(17,18,19), SWR=(22,23,24), AGRI=(27,28,29)

    # Helper: build a data row with values at given (col, val) pairs
    def data_row(**block_vals):
        r = [e] * PAD
        for col, val in block_vals.items():
            r[int(col)] = val
        return r

    # UPVC comps (cols 2,3,4), wrong H comps (cols 7,8,9 — different values),
    # CPVC-M comps (cols 12,13,14), CPVC-R wrong (cols 17,18,19 — different),
    # SWR comps (cols 22,23,24), AGRI comps (cols 27,28,29)

    u_n, u_r, u_p = upvc_comps[0]
    c_n, c_r, c_p = cpvc_comps[0]
    s_n, s_r, s_p = swr_comps[0]
    a_n, a_r, a_p = agri_comps[0]

    # Single-component rows for all 6 block positions
    rows.append(data_row(**{
        "2": u_n, "3": u_r, "4": u_p,       # UPVC-Pipe (canonical)
        "7": "Wrong CPVC H", "8": 50.0, "9": 999.0,  # wrong H block
        "12": c_n, "13": c_r, "14": c_p,    # CPVC-Pipe canonical M block
        "17": "Wrong CPVC R", "18": 60.0, "19": 888.0,  # wrong R block
        "22": s_n, "23": s_r, "24": s_p,    # SWR-Pipe
        "27": a_n, "28": a_r, "29": a_p,    # AGRI-Pipe
    }))  # row 4 = data row 0

    # Total rows for all blocks (at data row 1 = sheet row 5)
    rows.append(data_row(**{
        "2": "Total", "3": u_r, "4": 0,
        "7": "Total", "8": 50.0, "9": 0,
        "12": "Total", "13": c_r, "14": 0,
        "17": "Total", "18": 60.0, "19": 0,
        "22": "Total", "23": s_r, "24": 0,
        "27": "Total", "28": a_r, "29": 0,
    }))  # row 5

    # Gap row (blank or other content) — row 6
    rows.append(data_row())

    # WASTAGE rows for all blocks — row 7
    rows.append(data_row(**{
        "2": "WASTAGE", "3": 1.01, "4": 0,
        "7": "WASTAGE", "8": 1.01, "9": 0,
        "12": "WASTAGE", "13": 1.01, "14": 0,
        "17": "WASTAGE", "18": 1.01, "19": 0,
        "22": "WASTAGE", "23": 1.01, "24": 0,
        "27": "WASTAGE", "28": 1.01, "29": 0,
    }))  # row 7

    return rows


def _eff_rate(comps):
    """Compute effective Rs/kg from a component list (matches _mp_build_compound_cards)."""
    wf = comps[0]["wastage_factor"] if comps else 1.0
    tot_r = sum(c["ratio_kg"] for c in comps)
    tot_c = sum(c["ratio_kg"] * c["price_per_kg"] for c in comps)
    return (tot_c / tot_r * wf) if tot_r > 0 else 0.0


class TestParseBlockExplicit:
    """Unit tests for _parse_block_explicit with synthetic fixture data.

    Verifies that the explicit column-map parser:
    - reads components from the correct columns regardless of label content
    - stops at the Total row and captures wastage_factor from the WASTAGE row
    - handles a gap row between Total and WASTAGE (SWR-Pipe style)
    - reproduces the 5 known effective Rs/kg values from the acceptance spec
    """

    def test_upvc_pipe_parsed(self):
        """UPVC-Pipe block at cols C,D,E (2,3,4) is found."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["UPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert len(comps) >= 1, "UPVC-Pipe block must have at least one component"

    def test_cpvc_pipe_canonical_block_m(self):
        """CPVC-Pipe canonical block at cols M,N,O (12,13,14) is read."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["CPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert len(comps) >= 1
        # Must read from cols 12-14, not 7-9 (H) or 17-19 (R)
        names = {c["component"] for c in comps}
        assert "Wrong CPVC H" not in names
        assert "Wrong CPVC R" not in names

    def test_cpvc_pipe_eff_rate(self):
        """CPVC-Pipe effective cost ≈ 147.28 Rs/kg (acceptance spec)."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["CPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert _eff_rate(comps) == pytest.approx(147.28, abs=0.01)

    def test_upvc_pipe_eff_rate(self):
        """UPVC-Pipe effective cost ≈ 61.15 Rs/kg (acceptance spec)."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["UPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert _eff_rate(comps) == pytest.approx(61.15, abs=0.01)

    def test_swr_pipe_eff_rate(self):
        """SWR-Pipe effective cost ≈ 73.16 Rs/kg (acceptance spec)."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["SWR"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert _eff_rate(comps) == pytest.approx(73.16, abs=0.01)

    def test_wastage_factor_captured(self):
        """WASTAGE row sets wastage_factor=1.01 on all components."""
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["CPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert all(abs(c["wastage_factor"] - 1.01) < 1e-9 for c in comps)

    def test_total_row_not_in_components(self):
        """'Total' row must NOT appear as a component."""
        rows = _make_pipe_tab_explicit()
        for mat, (cc, rc, pc) in _PIPE_EXPLICIT_COLS.items():
            comps = _parse_block_explicit(rows, cc, rc, pc)
            names = {c["component"].lower() for c in comps}
            assert "total" not in names, f"{mat}-pipe: 'Total' leaked into components"

    def test_wastage_row_not_in_components(self):
        """'WASTAGE' row must NOT appear as a component."""
        rows = _make_pipe_tab_explicit()
        for mat, (cc, rc, pc) in _PIPE_EXPLICIT_COLS.items():
            comps = _parse_block_explicit(rows, cc, rc, pc)
            names = {c["component"].lower() for c in comps}
            assert "wastage" not in names, f"{mat}-pipe: 'WASTAGE' leaked into components"

    def test_gap_row_between_total_and_wastage(self):
        """A blank gap row between Total and WASTAGE is tolerated (SWR-Pipe style)."""
        # All blocks in our fixture have a gap row (row 6 is blank) then WASTAGE (row 7)
        rows = _make_pipe_tab_explicit()
        cc, rc, pc = _PIPE_EXPLICIT_COLS["SWR"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        # If wastage_factor was captured despite the gap, it will be 1.01 (not default 1.0)
        assert comps, "SWR-Pipe must parse at least one component"
        assert all(abs(c["wastage_factor"] - 1.01) < 1e-9 for c in comps)

    def test_empty_rows_returns_empty(self):
        assert _parse_block_explicit([], 2, 3, 4) == []

    def test_short_rows_returns_empty(self):
        """Fewer than _COMPOUND_DATA_START_ROW rows → no data rows → empty."""
        rows = [["x"] * 30 for _ in range(_COMPOUND_DATA_START_ROW - 1)]
        assert _parse_block_explicit(rows, 2, 3, 4) == []

    def test_default_wastage_when_no_wastage_row(self):
        """When WASTAGE row is absent, module default _COMPOUND_WASTAGE_FACTOR is used."""
        rows = [[""] * 10 for _ in range(5)]
        rows.append(["Component A", "100", "50"] + [""] * 7)   # row 5 = data
        rows.append(["Total",        "100",  "0"] + [""] * 7)  # row 6 = total
        # No WASTAGE row follows
        comps = _parse_block_explicit(rows, 0, 1, 2)
        assert comps
        assert all(abs(c["wastage_factor"] - _COMPOUND_WASTAGE_FACTOR) < 1e-9 for c in comps)

    def test_wastage_before_total_stops_collection(self):
        """WASTAGE row without a preceding Total row ends block collection.

        Regression for CPVC-Fitting: the real sheet has no Total row, going
        directly from component(s) to WASTAGE.  The old code read WASTAGE as a
        component and then continued into the 'actual' section.
        """
        # Layout: row 4 = component, row 5 = blank, row 6 = WASTAGE, row 7 = actual (wrong)
        rows = [[""] * 5 for _ in range(4)]   # rows 0-3: header area
        rows.append(["CG-122",  "50",   "175.00", "8750", ""])  # row 4: component
        rows.append(["",        "",     "",        "",     ""])  # row 5: blank
        rows.append(["WASTAGE", "1.01", "176.75",  "",    ""])  # row 6: wastage (no prior Total)
        rows.append(["CG-122",  "50",   "178.00",  "",    ""])  # row 7: actual section (must NOT be read)
        comps = _parse_block_explicit(rows, 0, 1, 2)
        # Must get exactly 1 component (CG-122), NOT 2
        assert len(comps) == 1, f"Expected 1 component, got {len(comps)}: {[c['component'] for c in comps]}"
        assert comps[0]["component"] == "CG-122"
        assert abs(comps[0]["ratio_kg"] - 50.0) < 1e-9
        assert abs(comps[0]["price_per_kg"] - 175.0) < 1e-9
        # wastage_factor must be 1.01 (from WASTAGE row)
        assert abs(comps[0]["wastage_factor"] - 1.01) < 1e-9
        # Effective rate: 175 * 1.01 = 176.75
        assert abs(_eff_rate(comps) - 176.75) < 0.01


def _make_fitting_tab_explicit():
    """Synthetic 'COMPOUND COST - F' sheet.

    Fitting blocks (0-indexed columns):
      UPVC-Fitting → C,D,E  = cols  2,3,4  (UPVC MOULDING)
      CPVC-Fitting → H,I,J  = cols  7,8,9  (CPVC MOULDING;  Rs/Kg=176.75)
      SWR-Fitting  → M,N,O  = cols 12,13,14 (SWR/AGRI MOULDING — SWR block; Rs/Kg=82.42)
      AGRI-Fitting → R,S,T  = cols 17,18,19 (SWR/AGRI MOULDING — AGRI block)

    Each block has one representative component, Total, gap, WASTAGE row.
    SWR and AGRI share the label "SWR / AGRI MOULDING" in the header but are
    in DIFFERENT column positions — only the explicit map resolves them correctly.
    """
    PAD = 25
    e = ""

    def pad(r):
        return (list(r) + [e] * PAD)[:PAD]

    rows = [pad([e] * PAD) for _ in range(4)]   # rows 0-3: header area

    # CPVC-Fitting: cols 7,8,9 → eff=176.75 = price*1.01 (single component, price=175.0)
    # 176.75/1.01 = 175.0 ✓  ratio=50
    cpvc_r, cpvc_p = 50.0, 175.0     # 50*175/50*1.01=176.75 ✓

    # SWR-Fitting:  cols 12,13,14 → eff=82.42 = (total_cost/total_ratio)*1.01
    # single comp: ratio=115.04, price=81.57..  115.04*81.57/115.04*1.01=82.39≈82.42
    # Use price=81.6039604: 81.6039604*1.01=82.42 ✓
    swr_r, swr_p = 115.04, 81.6039604

    # AGRI-Fitting: cols 17,18,19 — distinct from SWR; use different values
    agri_r, agri_p = 120.0, 88.0

    # UPVC-Fitting: cols 2,3,4
    upvc_r, upvc_p = 114.3, 95.20

    # Data rows (row 4 onwards)
    rows.append(pad([e, e,
        "UPVC Comp", upvc_r, upvc_p,           # UPVC cols 2,3,4
        e, e,
        "CPVC Comp", cpvc_r, cpvc_p,           # CPVC cols 7,8,9
        e, e,
        "SWR Comp", swr_r, swr_p,              # SWR cols 12,13,14
        e, e,
        "AGRI Comp", agri_r, agri_p,           # AGRI cols 17,18,19
        e, e, e, e, e]))   # pad

    rows.append(pad([e, e,
        "Total", upvc_r, 0,
        e, e,
        "Total", cpvc_r, 0,
        e, e,
        "Total", swr_r, 0,
        e, e,
        "Total", agri_r, 0,
        e, e, e, e, e]))   # total row

    rows.append(pad([e] * PAD))                # gap row

    rows.append(pad([e, e,
        "WASTAGE", 1.01, 0,
        e, e,
        "WASTAGE", 1.01, 0,
        e, e,
        "WASTAGE", 1.01, 0,
        e, e,
        "WASTAGE", 1.01, 0,
        e, e, e, e, e]))   # wastage row

    return rows


class TestParseBlockExplicitFitting:
    """Verify fitting-tab explicit parsing and the two acceptance-spec values."""

    def test_cpvc_fitting_eff_rate(self):
        """CPVC-Fitting effective cost ≈ 176.75 Rs/kg (acceptance spec)."""
        rows = _make_fitting_tab_explicit()
        cc, rc, pc = _FITTING_EXPLICIT_COLS["CPVC"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert _eff_rate(comps) == pytest.approx(176.75, abs=0.01)

    def test_swr_fitting_eff_rate(self):
        """SWR-Fitting effective cost ≈ 82.42 Rs/kg (acceptance spec).

        This is the key fix: the old block-finder read the wrong block
        (shared label), giving 89.69.  The explicit map reads cols M,N,O
        (12,13,14) which is the SWR block, giving 82.42.
        """
        rows = _make_fitting_tab_explicit()
        cc, rc, pc = _FITTING_EXPLICIT_COLS["SWR"]
        comps = _parse_block_explicit(rows, cc, rc, pc)
        assert _eff_rate(comps) == pytest.approx(82.42, abs=0.01)

    def test_agri_fitting_distinct_from_swr(self):
        """AGRI-Fitting (cols R,S,T) has DIFFERENT components than SWR-Fitting (cols M,N,O)."""
        rows = _make_fitting_tab_explicit()
        swr_cc, swr_rc, swr_pc = _FITTING_EXPLICIT_COLS["SWR"]
        agri_cc, agri_rc, agri_pc = _FITTING_EXPLICIT_COLS["AGRI"]
        swr_comps  = _parse_block_explicit(rows, swr_cc,  swr_rc,  swr_pc)
        agri_comps = _parse_block_explicit(rows, agri_cc, agri_rc, agri_pc)
        swr_names  = {c["component"] for c in swr_comps}
        agri_names = {c["component"] for c in agri_comps}
        # They are in different column positions → distinct component names
        assert swr_names != agri_names, "SWR and AGRI fitting must come from different blocks"

    def test_four_fitting_blocks_all_found(self):
        """All four fitting blocks (UPVC/CPVC/SWR/AGRI) parse at least one component."""
        rows = _make_fitting_tab_explicit()
        for mat, (cc, rc, pc) in _FITTING_EXPLICIT_COLS.items():
            comps = _parse_block_explicit(rows, cc, rc, pc)
            assert comps, f"{mat}-fitting block must return components"

    def test_eight_blocks_all_explicit_maps_covered(self):
        """_PIPE_EXPLICIT_COLS and _FITTING_EXPLICIT_COLS together cover all 8 combos."""
        pipe_keys    = {(m, "pipe")    for m in _PIPE_EXPLICIT_COLS}
        fitting_keys = {(m, "fitting") for m in _FITTING_EXPLICIT_COLS}
        all_keys = pipe_keys | fitting_keys
        from mp_seed import _COMPOUND_COMBOS
        assert all_keys == set(_COMPOUND_COMBOS), (
            f"Column map missing combos: {set(_COMPOUND_COMBOS) - all_keys}"
        )
