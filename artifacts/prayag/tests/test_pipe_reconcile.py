"""Pure unit tests for pipe_reconcile (PIPE Report-5 ↔ Report-11 reconciliation).

Covers:
- parse_report11: header-text column location (not fixed index), TYPES capture,
  TOTAL-row skipping, per-(machine, date) aggregation, picking the standalone
  "Weight" output column (not "Ideal Weight (KG)") and "Actual Wt (Kg)" reject.
- reconcile: date-wise MAX per cell over the UNION of both sources, pro-rata
  Report-11 type split scaled to the corrected output, and "untyped pickup" for
  cells with no Report-11 type signal.
- resolve_r11_label: alias table for legacy model-name labels, primary-key-fn
  precedence, and graceful None for unknown labels.

No network. Run: cd artifacts/prayag && python3 -m tests.test_pipe_reconcile
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipe_reconcile
from sheets import _mc_key


def test_parse_report11_header_based_and_typed():
    # Header is NOT row 0 (a title/blank band sits above), columns are out of the
    # "expected" order, and there is a decoy "Ideal Weight (KG)" before the real
    # standalone "Weight". The parser must locate columns by header text.
    values = [
        ["M/C & Item Wise Actual Production", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ["DATE", "", "MACHINE NO.", "TYPES", "Pcs", "Ideal Weight (KG)",
         "Weight", "Ideal Wt (Kg)", "Actual Wt (Kg)", "FC"],
        ["Apr 1, 2026", "", "PIPE M/C - 1", "CPVC", "100", "999", "1000", "8", "10", "1"],
        ["Apr 1, 2026", "", "PIPE M/C - 1", "SWR", "100", "999", "500", "8", "5", "1"],
        ["Apr 1, 2026", "", "GRAND TOTAL", "", "0", "0", "9999", "0", "9999", "0"],
        ["Apr 2, 2026", "", "PIPE M/C - 2", "UPVC", "100", "999", "2000", "8", "20", "1"],
    ]
    out = pipe_reconcile.parse_report11(values, "2026-04", _mc_key)
    # M/C-1 on Apr-1: two item rows summed; TOTAL row skipped.
    one = out[(1, "2026-04-01")]
    assert one["out"] == 1500.0, one["out"]
    assert one["rej"] == 15.0, one["rej"]
    assert one["by_type"] == {"CPVC": 1000.0, "SWR": 500.0}, one["by_type"]
    assert (2, "2026-04-02") in out
    assert out[(2, "2026-04-02")]["out"] == 2000.0
    # GRAND TOTAL must never become a machine.
    assert all(k[0] in (1, 2) for k in out), out.keys()
    print("PASS: parse_report11 header-based, typed, sums items, skips TOTAL")


def test_reconcile_date_wise_max_and_union():
    # Cell A: R5 higher; Cell B: R11 higher; Cell C: only R5; Cell D: only R11.
    r5 = {
        (1, "2026-04-01"): {"out": 1000.0, "rej": 10.0},   # R5 wins
        (1, "2026-04-02"): {"out": 200.0, "rej": 2.0},     # R11 wins
        (2, "2026-04-01"): {"out": 500.0, "rej": 5.0},     # R5 only
    }
    r11 = {
        (1, "2026-04-01"): {"out": 800.0, "rej": 12.0, "by_type": {"CPVC": 800.0}},
        (1, "2026-04-02"): {"out": 900.0, "rej": 1.0, "by_type": {"SWR": 600.0, "UPVC": 300.0}},
        (3, "2026-04-01"): {"out": 700.0, "rej": 7.0, "by_type": {"AGRI": 700.0}},  # R11 only
    }
    corrected, audit = pipe_reconcile.reconcile(r5, r11)

    # Output = per-cell max; rejection picked INDEPENDENTLY.
    a = corrected[(1, "2026-04-01")]
    assert a["out"] == 1000.0 and a["pick_out"] == "R5"
    assert a["rej"] == 12.0 and a["pick_rej"] == "R11"  # reject max is R11's
    b = corrected[(1, "2026-04-02")]
    assert b["out"] == 900.0 and b["pick_out"] == "R11"

    # Union of all four cells.
    assert audit["n_cells"] == 4
    assert audit["n_both"] == 2 and audit["n_r5_only"] == 1 and audit["n_r11_only"] == 1
    assert audit["out_total"] == 1000.0 + 900.0 + 500.0 + 700.0
    print("PASS: reconcile takes date-wise max over the union; out/rej picked independently")


def test_type_split_prorata_and_untyped_pickup():
    r5 = {
        (1, "2026-04-01"): {"out": 1000.0, "rej": 10.0},   # R5 higher than R11 typed
        (2, "2026-04-01"): {"out": 500.0, "rej": 5.0},     # R5 only → untyped pickup
    }
    r11 = {
        (1, "2026-04-01"): {"out": 800.0, "rej": 8.0,
                            "by_type": {"CPVC": 600.0, "SWR": 200.0}},  # 75% / 25%
    }
    corrected, audit = pipe_reconcile.reconcile(r5, r11)

    # Cell 1: corrected out = 1000 (R5). R11 proportions 75/25 scale to 1000.
    c1 = corrected[(1, "2026-04-01")]
    assert abs(c1["types"]["CPVC"] - 750.0) < 1e-6, c1["types"]
    assert abs(c1["types"]["SWR"] - 250.0) < 1e-6, c1["types"]
    assert c1["untyped"] == 0.0

    # Cell 2: no R11 type signal → entire corrected output is untyped pickup.
    c2 = corrected[(2, "2026-04-01")]
    assert c2["types"] == {} and c2["untyped"] == 500.0

    # Audit coherence: types + untyped == out_total (no KG lost or invented).
    alloc = sum(audit["type_totals"].values()) + audit["untyped_kg"]
    assert abs(alloc - audit["out_total"]) < 1e-6, (alloc, audit["out_total"])
    assert audit["untyped_kg"] == 500.0
    print("PASS: type split is pro-rata to corrected total; R5-only cell = untyped pickup")


def test_empty_and_missing_header_safe():
    assert pipe_reconcile.parse_report11([], "2026-04", _mc_key) == {}
    # No DATE header anywhere → empty (never a crash).
    assert pipe_reconcile.parse_report11(
        [["foo", "bar"], ["1", "2"]], "2026-04", _mc_key) == {}
    corrected, audit = pipe_reconcile.reconcile({}, {})
    assert corrected == {} and audit["n_cells"] == 0
    print("PASS: empty input / missing header degrade safely")


def test_resolve_r11_label_primary_fn_takes_precedence():
    """Primary key function is tried first; alias only applies when it returns None."""
    # Current label — primary fn resolves it; alias must not interfere.
    assert pipe_reconcile.resolve_r11_label("PIPE M/C - 4", _mc_key) == 4
    assert pipe_reconcile.resolve_r11_label("M/C-9", _mc_key) == 9
    print("PASS: resolve_r11_label — primary mc_key_fn takes precedence")


def test_resolve_r11_label_legacy_aliases():
    """All known FY2025-26 legacy labels resolve to the correct M/C number."""
    expected = {
        "CON-63-1":      1,
        "TTS-88-2":      2,
        "TTS-88-3":      3,
        "TTS-88-4":      4,
        "TTS-88-5":      5,
        "KABRA-72-28":   6,
        "2-KABRA-90-22": 9,
    }
    for label, mc_n in expected.items():
        result = pipe_reconcile.resolve_r11_label(label, _mc_key)
        assert result == mc_n, f"{label!r}: expected {mc_n}, got {result}"
    print("PASS: resolve_r11_label — all FY2025-26 legacy aliases resolve correctly")


def test_resolve_r11_label_unknown_returns_none():
    """An unrecognised label returns None so the row is skipped, not mis-mapped."""
    assert pipe_reconcile.resolve_r11_label("1-KABRA-90-22", _mc_key) is None
    assert pipe_reconcile.resolve_r11_label("UNKNOWN-MACHINE", _mc_key) is None
    assert pipe_reconcile.resolve_r11_label("", _mc_key) is None
    print("PASS: resolve_r11_label — unknown labels return None (safe skip)")


def test_parse_report11_with_legacy_labels():
    """parse_report11 resolves legacy labels via resolve_r11_label and joins correctly."""
    values = [
        ["M/C & Item Wise Actual Production"],
        [],
        [],
        [],
        [],
        ["DATE", "", "MACHINE NO.", "TYPES", "Pcs", "Ideal Weight (KG)",
         "Weight", "Ideal Wt (Kg)", "Actual Wt (Kg)", "FC"],
        # TTS-88-4 → M/C-4
        ["Apr 5, 2026", "", "TTS-88-4", "SWR", "200", "999", "3500", "8", "120", "1"],
        # KABRA-72-28 → M/C-6
        ["Apr 5, 2026", "", "KABRA-72-28", "UPVC", "100", "999", "1900", "8", "80", "1"],
        # 1-KABRA-90-22 → unmapped, must be skipped
        ["Apr 5, 2026", "", "1-KABRA-90-22", "AGRI", "50", "999", "500", "8", "20", "1"],
    ]
    alias_key = lambda lbl: pipe_reconcile.resolve_r11_label(lbl, _mc_key)
    out = pipe_reconcile.parse_report11(values, "2026-04", alias_key)

    assert (4, "2026-04-05") in out, f"TTS-88-4 not resolved: {list(out.keys())}"
    assert out[(4, "2026-04-05")]["out"] == 3500.0
    assert out[(4, "2026-04-05")]["by_type"] == {"SWR": 3500.0}

    assert (6, "2026-04-05") in out, f"KABRA-72-28 not resolved: {list(out.keys())}"
    assert out[(6, "2026-04-05")]["out"] == 1900.0

    # Unmapped label must not appear in output.
    assert len(out) == 2, f"Unexpected keys: {list(out.keys())}"
    print("PASS: parse_report11 resolves legacy labels; unmapped labels are safely skipped")


if __name__ == "__main__":
    test_parse_report11_header_based_and_typed()
    test_reconcile_date_wise_max_and_union()
    test_type_split_prorata_and_untyped_pickup()
    test_empty_and_missing_header_safe()
    test_resolve_r11_label_primary_fn_takes_precedence()
    test_resolve_r11_label_legacy_aliases()
    test_resolve_r11_label_unknown_returns_none()
    test_parse_report11_with_legacy_labels()
    print("\nALL pipe_reconcile tests passed.")
