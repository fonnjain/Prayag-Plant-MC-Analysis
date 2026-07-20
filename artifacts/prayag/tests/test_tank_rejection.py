"""Tests for Tank rejection-accounting correctness across all three locations.

Every tank stream (KH / VN / WB) must:
  - emit ``reject_unit = "kg"`` regardless of the production primary unit.
  - accumulate kg rejection in ``reject_count`` (mouth-lid + plain KG columns).
  - store the KG denominator (production KG for the same row group) in
    ``reject_denominator`` so compute_metrics divides correctly.
  - keep pcs-only rejection in ``secondary_counts["rej_pcs"]`` (never in
    ``reject_count``), so a stream with no KG rejection columns shows 0.0 kg
    and the caller can surface a piece-basis percentage separately.

Fully offline: synthetic cell arrays feed ``parsers.parse_tank_prod`` directly.

Run: cd artifacts/prayag && python3 -m pytest tests/test_tank_rejection.py
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_tank_prod
from metrics import compute_metrics


# ---------------------------------------------------------------------------
# Minimal cell-grid builders (no real Google Sheets reads needed)
# ---------------------------------------------------------------------------
def _kh_values():
    """KH-style layout: pcs primary, no KG rejection column — only REJECT IN PCS.

    Mimics a workbook where the source logs rejection only in pcs (as KH does
    for some months).  The production KG column IS present so reject_denominator
    can be set, but reject_count must stay 0.0 (no kg rejection).
    """
    header = ["DATE", "ITEM CODE", "PRODUCTION IN LTR.", "PRODUCTION IN PCS",
              "PRODUCTION IN KG", "REJECT IN PCS"]
    # Ltr column is zero → parser falls through to pcs as primary unit.
    # _long_date_day requires a recognisable date string, not a bare integer.
    rows = [header, ["01-Jun-2026", "ITEM-A", 0, 1_781, 500, 90]]
    return rows


def _vn_values():
    """VN-style layout: Ltr primary, KG rejection column present."""
    header = ["DATE", "ITEM CODE", "PRODUCTION IN LTR.", "PRODUCTION IN PCS",
              "PRODUCTION IN KG", "REJECT IN KG"]
    rows = [header, ["01-Jun-2026", "ITEM-A", 22_000, 20, 5_960.8, 129.7]]
    return rows


def _wb_values():
    """WB-style layout: Ltr primary, both REJECTION MOUTH LID IN KG and REJECT IN KG."""
    header = ["DATE", "ITEM CODE", "PRODUCTION IN LTR.", "PRODUCTION IN PCS",
              "PRODUCTION IN KG", "REJECTION MOUTH LID IN KG", "REJECT IN KG"]
    rows = [header, ["01-Jun-2026", "ITEM-A", 18_500, 20, 19_249, 471.0, 464.5]]
    return rows


_COMMON = dict(plant="TANK", segment="TANK", unit="Ltr",
               year_month="2026-06", source_file="FID", source_tab="PROD. REPORT")


# ---------------------------------------------------------------------------
# Helper: assert close within tolerance
# ---------------------------------------------------------------------------
def _close(actual, expected, tol=0.001):
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / abs(expected) <= tol


# ---------------------------------------------------------------------------
# A2 guard — PLANTS_WITHOUT_RUNHOURS includes all three tank plants
# ---------------------------------------------------------------------------
def test_plants_without_runhours_covers_all_tank_locations():
    import ideal_hours
    for plant in ("TANK", "TANK_VN", "TANK_WB"):
        assert plant in ideal_hours.PLANTS_WITHOUT_RUNHOURS, (
            f"{plant} missing from PLANTS_WITHOUT_RUNHOURS — "
            "utilisation will be fabricated 0% instead of suppressed")


# ---------------------------------------------------------------------------
# KH — pcs-only rejection
# ---------------------------------------------------------------------------
def test_kh_reject_unit_is_always_kg():
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    assert recs, "KH parse returned no records"
    for r in recs:
        assert r.reject_unit == "kg", (
            f"KH record reject_unit={r.reject_unit!r}, expected 'kg'")


def test_kh_reject_count_is_zero_when_only_pcs_rejection():
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    total_kg_rej = sum(r.reject_count for r in recs)
    assert total_kg_rej == 0.0, (
        f"KH kg rejection expected 0.0, got {total_kg_rej}")


def test_kh_pcs_rejection_in_secondary_counts():
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    total_pcs_rej = sum(r.secondary_counts.get("rej_pcs", 0.0) for r in recs)
    assert _close(total_pcs_rej, 90), (
        f"KH rej_pcs in secondary_counts expected 90, got {total_pcs_rej}")


def test_kh_reject_denominator_is_prod_kg():
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    total_denom = sum(r.reject_denominator for r in recs)
    assert _close(total_denom, 500), (
        f"KH reject_denominator (prod_kg) expected 500, got {total_denom}")


def test_kh_compute_metrics_reject_pct_reflects_kg_basis():
    """compute_metrics must give 0% (not pcs-basis %) when kg rejection is 0."""
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    result = compute_metrics(recs)
    # rejection_pct is kg-basis; 0 kg rejection / 500 kg produced = 0%
    assert result.rejection_pct == 0.0, (
        f"KH kg-basis rejection_pct expected 0.0, got {result.rejection_pct}")
    # The pcs count is accessible for a "piece-based" fallback display
    total_pcs_rej = sum(r.secondary_counts.get("rej_pcs", 0.0) for r in recs)
    assert total_pcs_rej > 0, "KH secondary_counts must carry non-zero rej_pcs"


# ---------------------------------------------------------------------------
# VN — KG rejection (rej_kg column only)
# ---------------------------------------------------------------------------
def test_vn_reject_unit_is_always_kg():
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    assert recs, "VN parse returned no records"
    for r in recs:
        assert r.reject_unit == "kg", (
            f"VN record reject_unit={r.reject_unit!r}, expected 'kg'")


def test_vn_reject_count_equals_rej_kg():
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    total = sum(r.reject_count for r in recs)
    assert _close(total, 129.7), (
        f"VN reject_count expected 129.7 kg, got {total}")


def test_vn_reject_denominator_is_prod_kg():
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    denom = sum(r.reject_denominator for r in recs)
    assert _close(denom, 5_960.8), (
        f"VN reject_denominator expected 5960.8 kg, got {denom}")


def test_vn_rejection_pct_approx_2_18_percent():
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    # rejection % = 129.7 / 5960.8 ≈ 2.18%
    total_rej = sum(r.reject_count for r in recs)
    total_den = sum(r.reject_denominator for r in recs)
    pct = total_rej / total_den
    assert _close(pct, 0.0218, tol=0.005), (
        f"VN rejection % expected ≈2.18%, got {pct:.4%}")


def test_vn_no_ltr_unit_in_reject():
    """Rejection must never leak into the Ltr/pcs channels."""
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    for r in recs:
        sc = r.secondary_counts or {}
        assert "rej_ltr" not in sc, "VN: rej_ltr must not appear in secondary_counts"
        assert sc.get("rej_pcs", 0.0) == 0.0, "VN: rej_pcs must be 0 (has plain kg column)"


# ---------------------------------------------------------------------------
# WB — KG rejection with MOUTH LID + BASE columns
# ---------------------------------------------------------------------------
def test_wb_reject_unit_is_always_kg():
    recs = parse_tank_prod(_wb_values(), **_COMMON)
    assert recs, "WB parse returned no records"
    for r in recs:
        assert r.reject_unit == "kg", (
            f"WB record reject_unit={r.reject_unit!r}, expected 'kg'")


def test_wb_reject_count_sums_mouth_and_base():
    recs = parse_tank_prod(_wb_values(), **_COMMON)
    total = sum(r.reject_count for r in recs)
    assert _close(total, 935.5), (
        f"WB reject_count expected 935.5 kg (471+464.5), got {total}")


def test_wb_reject_denominator_is_prod_kg():
    recs = parse_tank_prod(_wb_values(), **_COMMON)
    denom = sum(r.reject_denominator for r in recs)
    assert _close(denom, 19_249), (
        f"WB reject_denominator expected 19249 kg, got {denom}")


def test_wb_rejection_pct_approx_4_86_percent():
    recs = parse_tank_prod(_wb_values(), **_COMMON)
    total_rej = sum(r.reject_count for r in recs)
    total_den = sum(r.reject_denominator for r in recs)
    pct = total_rej / total_den
    assert _close(pct, 0.0486, tol=0.005), (
        f"WB rejection % expected ≈4.86%, got {pct:.4%}")


# ---------------------------------------------------------------------------
# No cross-unit contamination: Ltr rejection must never carry a kg value
# ---------------------------------------------------------------------------
def test_no_ltr_unit_for_reject_count_in_any_stream():
    """reject_count must always be KG regardless of the production primary unit."""
    for label, vals in [("KH", _kh_values()), ("VN", _vn_values()), ("WB", _wb_values())]:
        recs = parse_tank_prod(vals, **_COMMON)
        for r in recs:
            assert r.reject_unit == "kg", (
                f"{label}: reject_unit leaked as {r.reject_unit!r}")
            # reject_denominator must be prod_kg (never Ltr)
            if r.reject_denominator > 0:
                sc = r.secondary_counts or {}
                prod_ltr = r.total_count if r.unit == "Ltr" else sc.get("Ltr", 0.0)
                assert r.reject_denominator != prod_ltr or prod_ltr == 0, (
                    f"{label}: reject_denominator equals Ltr production "
                    "— denominator must be KG not Ltr")


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append((t.__name__, e))
            print(f"FAIL: {t.__name__}: {e}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print(f"\nAll {len(tests)} tank-rejection tests passed.")
