"""Tests for Tank rejection-accounting correctness across all three locations.

Phase 1 spec: rejection headline is **Ltr = Σ (REJECTION IN PCS. × SIZE (LTR.))
per row**.  The two KG columns (REJECTION MOUTH LID IN KG, REJECTION IN KG) are
kept in secondary_counts for display only — they are never summed into the
headline and never labelled as Ltr.

Every tank stream (KH / VN / WB) must:
  - emit ``reject_unit = "Ltr"`` (not "kg").
  - compute ``reject_count`` as pcs × size per row, summed per (day, item).
  - set ``reject_denominator`` to the production Ltr total (same unit as reject),
    so compute_metrics yields rejection % = rej_ltr / prod_ltr.
  - raise ``TankRejectionColumnError`` when SIZE (LTR.) or REJECTION IN PCS.
    are absent — never fall back silently to a KG column.
  - leave production totals unchanged (output in Ltr / Pcs / KG unaffected).

Fully offline: synthetic cell arrays feed ``parsers.parse_tank_prod`` directly.

Run: cd artifacts/prayag && python3 -m pytest tests/test_tank_rejection.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from parsers import parse_tank_prod, TankRejectionColumnError
from metrics import compute_metrics


# ---------------------------------------------------------------------------
# Minimal cell-grid builders (no real Google Sheets reads needed)
# ---------------------------------------------------------------------------

def _header(*extra_cols):
    """Return a header row starting with mandatory columns plus *extra_cols."""
    return ["DATE", "ITEM CODE", "SIZE (LTR.)", "COLOR",
            "PRODUCTION IN PCS.", "PRODUCTION IN LTR.", "PRODUCTION IN KG."] + list(extra_cols)


def _kh_values():
    """KH-style layout: production only, zero rejection for the test month.

    KH workbook: no rejection in some months (rej pcs = 0 in every row).
    SIZE (LTR.) and REJECTION IN PCS. are present (required for the guard).
    """
    header = _header("REJECTION IN PCS.", "REJECTION MOUTH LID IN KG.", "REJECTION IN KG.")
    # Production: 4 tanks of 5000 Ltr each; zero rejection.
    rows = [
        header,
        ["Jun 1, 2026", "WT-KH-50", "5000", "WHITE", "4", "20000", "600.0", "0", "", ""],
    ]
    return rows


def _vn_values():
    """VN-style layout: Ltr primary, rejection in pcs × size.

    Mirrors the real VN June structure with two item types.
    - Row 1: 2 × 1000L tanks rejected  → 2,000 Ltr rejection
    - Row 2: 1 × 500L tank rejected    →   500 Ltr rejection
    Total rejection: 2,500 Ltr.  Production: (14+12) = 26 pcs / (14,000+6,000) = 20,000 Ltr.
    """
    header = _header("REJECTION IN PCS.", "REJECTION MOUTH LID IN KG.", "REJECTION IN KG.")
    rows = [
        header,
        # date, item, size, color, prod_pcs, prod_ltr, prod_kg, rej_pcs, rej_mouth_kg, rej_kg
        ["Jun 1, 2026", "WT-3LL-10", "1000", "WHITE", "14", "14000", "315.0", "2", "0",   "46"],
        ["Jun 1, 2026", "WT-3LL-05", "500",  "WHITE", "12", "6000",  "153.6", "1", "10.5",""],
    ]
    return rows


def _vn_june_fixture():
    """Minimal fixture reproducing the known VN June verified figure: 8,500 Ltr rejection.

    9 pcs total: 2+1+1+1+2+2 = 9 pcs across various 1000L and 500L items.
    8×1000 + 1×500 = 8,500 Ltr.
    """
    header = _header("REJECTION IN PCS.", "REJECTION MOUTH LID IN KG.", "REJECTION IN KG.")
    rows = [
        header,
        # 8 tanks of 1000 L rejected across different dates / items
        ["Jun 1, 2026",  "WT-3LL-10", "1000", "WHITE", "14", "14000", "315.0", "2", "",      "46"],
        ["Jun 2, 2026",  "WT-3LL-10", "1000", "WHITE",  "7",  "7000", "158.0", "1", "",      "27"],
        ["Jun 5, 2026",  "WT-4LL-10", "1000", "WHITE",  "7",  "7000", "185.5", "1", "",      "27"],
        ["Jun 10, 2026", "WT-3LL-10", "1000", "WHITE",  "8",  "8000", "180.0", "1", "",        ""],
        ["Jun 15, 2026", "WT-3LL-10", "1000", "WHITE",  "8",  "8000", "180.0", "2", "",        ""],
        ["Jun 20, 2026", "WT-3LL-10", "1000", "WHITE",  "8",  "8000", "180.0", "1", "",        ""],
        # 1 tank of 500 L rejected
        ["Jun 1, 2026",  "WT-3LL-05", "500",  "WHITE", "12",  "6000", "153.6", "1", "10.5", ""],
    ]
    return rows


def _wb_values():
    """WB-style layout: Ltr primary, rejection in pcs × size.

    - Row 1: 3 × 2000L tanks rejected  → 6,000 Ltr rejection
    Mouth-lid and base KG columns present but must NOT contribute to headline.
    """
    header = _header("REJECTION IN PCS.", "REJECTION MOUTH LID IN KG.", "REJECTION IN KG.")
    rows = [
        header,
        # date, item, size, color, prod_pcs, prod_ltr, prod_kg, rej_pcs, rej_mouth_kg, rej_kg
        ["Jul 1, 2026", "WT-WB-20", "2000", "BLUE", "20", "40000", "1200.0", "3", "471.0", "464.5"],
    ]
    return rows


_COMMON = dict(plant="TANK", segment="TANK", unit="Ltr",
               year_month="2026-06", source_file="FID", source_tab="PROD. REPORT")

_COMMON_JUL = dict(plant="TANK_WB", segment="Tank_Wb", unit="Ltr",
                   year_month="2026-07", source_file="FID", source_tab="PROD. REPORT")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _close(actual, expected, tol=0.001):
    if expected == 0:
        return actual == 0
    return abs(actual - expected) / abs(expected) <= tol


# ---------------------------------------------------------------------------
# A2 guard — PLANTS_WITHOUT_RUNHOURS is empty (R-25); Tank utilisation is
# suppressed via ideal_source=SRC_NOT_SET, not via set membership.
# ---------------------------------------------------------------------------
def test_plants_without_runhours_is_empty():
    """R-25 closed PLANTS_WITHOUT_RUNHOURS to frozenset().

    All three Tank variants now manage ``runhours_tracked`` at record level
    (date-wise max of DAILY REPORT + PROD. REPORT hours, R-39).  The old
    set-membership gate must stay empty — if any plant sneaks back in it would
    suppress run-hour tracking that is now happening correctly.
    """
    import ideal_hours
    assert ideal_hours.PLANTS_WITHOUT_RUNHOURS == frozenset(), (
        "PLANTS_WITHOUT_RUNHOURS must be empty (R-25); "
        "Tank run-hour suppression is now done at record level via "
        "runhours_tracked, not via set membership")


def test_tank_utilisation_suppressed_via_src_not_set():
    """Tank plants have no APP_DEFAULT_IDEAL_HOURS entry.

    When no override and no sheet value are provided, resolve() must return
    (None, SRC_NOT_SET) for every Tank location — utilisation stays suppressed
    without needing PLANTS_WITHOUT_RUNHOURS membership.
    """
    import ideal_hours
    for plant in ("TANK", "TANK_VN", "TANK_WB"):
        hours, src = ideal_hours.resolve(
            override=None, sheet_value=None, plant=plant
        )
        assert hours is None, (
            f"{plant}: expected ideal_hours=None, got {hours!r} — "
            "a default was added to APP_DEFAULT_IDEAL_HOURS without a "
            "corresponding utilisation denominator check (FM #14)")
        assert src == ideal_hours.SRC_NOT_SET, (
            f"{plant}: expected src=SRC_NOT_SET, got {src!r} — "
            "utilisation may be fabricated instead of suppressed")


# ---------------------------------------------------------------------------
# Reject unit is always Ltr
# ---------------------------------------------------------------------------
def test_reject_unit_is_ltr_for_all_streams():
    for label, vals, kw in [("KH",  _kh_values(), _COMMON),
                             ("VN",  _vn_values(), _COMMON),
                             ("WB", _wb_values(), _COMMON_JUL)]:
        recs = parse_tank_prod(vals, **kw)
        assert recs, f"{label}: parse returned no records"
        for r in recs:
            assert r.reject_unit == "Ltr", (
                f"{label}: reject_unit={r.reject_unit!r}, expected 'Ltr'")


# ---------------------------------------------------------------------------
# Rejection = pcs × size, not the KG columns
# ---------------------------------------------------------------------------
def test_vn_rejection_ltr_is_pcs_times_size():
    """VN fixture: 2×1000 + 1×500 = 2,500 Ltr rejection."""
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    total_rej = sum(r.reject_count for r in recs)
    assert _close(total_rej, 2_500.0), (
        f"VN reject_count expected 2,500 Ltr (pcs×size), got {total_rej}")


def test_wb_rejection_ltr_is_pcs_times_size():
    """WB fixture: 3×2000 = 6,000 Ltr rejection."""
    recs = parse_tank_prod(_wb_values(), **_COMMON_JUL)
    total_rej = sum(r.reject_count for r in recs)
    assert _close(total_rej, 6_000.0), (
        f"WB reject_count expected 6,000 Ltr (pcs×size), got {total_rej}")


def test_kh_rejection_is_zero_when_no_pcs_rejected():
    """KH fixture: zero pcs rejected → reject_count = 0 Ltr."""
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    total_rej = sum(r.reject_count for r in recs)
    assert total_rej == 0.0, (
        f"KH reject_count expected 0.0 Ltr, got {total_rej}")


# ---------------------------------------------------------------------------
# KG columns are never summed into the headline
# ---------------------------------------------------------------------------
def test_kg_columns_not_in_reject_count():
    """REJECTION MOUTH LID IN KG and REJECTION IN KG must never reach reject_count."""
    # WB fixture: mouth_kg=471.0, base_kg=464.5 → old wrong total = 935.5
    # New correct: 3×2000 = 6,000 Ltr.  The kg sum (935.5) must not appear.
    recs = parse_tank_prod(_wb_values(), **_COMMON_JUL)
    total_rej = sum(r.reject_count for r in recs)
    assert not _close(total_rej, 935.5, tol=0.001), (
        "WB: reject_count equals the old KG sum 935.5 — KG columns are leaking "
        "into the rejection headline")
    assert _close(total_rej, 6_000.0), (
        f"WB: expected 6,000 Ltr, got {total_rej}")


def test_kg_values_appear_only_in_secondary_counts():
    """KG rejection detail must appear in secondary_counts, never in reject_count."""
    recs = parse_tank_prod(_wb_values(), **_COMMON_JUL)
    assert recs, "WB parse returned no records"
    for r in recs:
        sc = r.secondary_counts or {}
        # KG values must be in secondary if the columns were non-zero
        # (either rej_mouth_kg or rej_base_kg key).
        has_kg_secondary = sc.get("rej_mouth_kg", 0) > 0 or sc.get("rej_base_kg", 0) > 0
        assert has_kg_secondary, (
            "WB: non-zero KG rejection columns must appear in secondary_counts")


# ---------------------------------------------------------------------------
# Reject denominator is production Ltr (same unit as reject_count)
# ---------------------------------------------------------------------------
def test_reject_denominator_equals_production_ltr():
    """reject_denominator must equal total_count (both in Ltr) for Ltr-primary records."""
    for label, vals, kw in [("VN", _vn_values(), _COMMON),
                             ("WB", _wb_values(), _COMMON_JUL)]:
        recs = parse_tank_prod(vals, **kw)
        for r in recs:
            if r.unit == "Ltr":
                assert _close(r.reject_denominator, r.total_count), (
                    f"{label}: reject_denominator {r.reject_denominator} != "
                    f"total_count {r.total_count} — rejection % will be wrong")


def test_rejection_pct_is_ltr_over_ltr():
    """compute_metrics rejection % = rej_ltr / prod_ltr (no cross-unit division)."""
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    # 2,500 Ltr rejected / 20,000 Ltr produced = 12.5%
    result = compute_metrics(recs)
    expected_pct = 2_500 / 20_000
    assert abs(result.rejection_pct - expected_pct) < 0.001, (
        f"VN rejection_pct expected {expected_pct:.4%}, got {result.rejection_pct:.4%}")


# ---------------------------------------------------------------------------
# VN June verified figure: 8,500 Ltr
# ---------------------------------------------------------------------------
def test_vn_june_verified_total_8500_ltr():
    """Source-verified: VN June total rejection = 8,500 Ltr (9 tanks × their sizes)."""
    recs = parse_tank_prod(_vn_june_fixture(), **_COMMON)
    total_rej = sum(r.reject_count for r in recs)
    assert _close(total_rej, 8_500.0), (
        f"VN June: expected 8,500 Ltr rejection, got {total_rej}")


# ---------------------------------------------------------------------------
# Production is unchanged by the rejection fix
# ---------------------------------------------------------------------------
def test_vn_production_unchanged():
    """Rejection fix must not alter production totals."""
    recs = parse_tank_prod(_vn_values(), **_COMMON)
    total_ltr = sum(r.total_count for r in recs)
    assert _close(total_ltr, 20_000.0), (
        f"VN production expected 20,000 Ltr, got {total_ltr}")


def test_wb_production_unchanged():
    recs = parse_tank_prod(_wb_values(), **_COMMON_JUL)
    total_ltr = sum(r.total_count for r in recs)
    assert _close(total_ltr, 40_000.0), (
        f"WB production expected 40,000 Ltr, got {total_ltr}")


# ---------------------------------------------------------------------------
# Named error when required columns are absent
# ---------------------------------------------------------------------------
def test_named_error_when_size_column_missing():
    """TankRejectionColumnError when SIZE (LTR.) is absent."""
    header_no_size = ["DATE", "ITEM CODE", "COLOR",
                      "PRODUCTION IN PCS.", "PRODUCTION IN LTR.", "PRODUCTION IN KG.",
                      "REJECTION IN PCS.", "REJECTION IN KG."]
    vals = [header_no_size,
            ["Jun 1, 2026", "ITEM-A", "WHITE", "10", "10000", "300", "1", "25"]]
    with pytest.raises(TankRejectionColumnError, match="SIZE"):
        parse_tank_prod(vals, **_COMMON)


def test_named_error_when_rej_pcs_column_missing():
    """TankRejectionColumnError when REJECTION IN PCS. is absent."""
    header_no_rej_pcs = ["DATE", "ITEM CODE", "SIZE (LTR.)", "COLOR",
                         "PRODUCTION IN PCS.", "PRODUCTION IN LTR.", "PRODUCTION IN KG.",
                         "REJECTION IN KG."]
    vals = [header_no_rej_pcs,
            ["Jun 1, 2026", "ITEM-A", "1000", "WHITE", "10", "10000", "300", "25"]]
    with pytest.raises(TankRejectionColumnError, match="REJECTION IN PCS"):
        parse_tank_prod(vals, **_COMMON)


def test_named_error_when_both_columns_missing():
    """TankRejectionColumnError lists all missing columns."""
    header_neither = ["DATE", "ITEM CODE", "COLOR",
                      "PRODUCTION IN PCS.", "PRODUCTION IN LTR.",
                      "REJECTION IN KG."]
    vals = [header_neither,
            ["Jun 1, 2026", "ITEM-A", "WHITE", "10", "10000", "25"]]
    with pytest.raises(TankRejectionColumnError):
        parse_tank_prod(vals, **_COMMON)


def test_no_error_raised_when_no_actual_rejection():
    """Guard must NOT raise when SIZE and REJECTION IN PCS. exist but pcs=0."""
    recs = parse_tank_prod(_kh_values(), **_COMMON)
    # KH fixture has SIZE and REJECTION IN PCS. columns, zero pcs.
    # No exception; zero reject_count returned.
    total_rej = sum(r.reject_count for r in recs)
    assert total_rej == 0.0


# ---------------------------------------------------------------------------
# No hours or labour fabricated
# ---------------------------------------------------------------------------
def test_no_hours_or_labour_fabricated():
    """Tank records must carry zero actual_hours and zero labour_cost."""
    for label, vals, kw in [("KH", _kh_values(), _COMMON),
                             ("VN", _vn_values(), _COMMON),
                             ("WB", _wb_values(), _COMMON_JUL)]:
        recs = parse_tank_prod(vals, **kw)
        for r in recs:
            assert r.actual_hours == 0.0, (
                f"{label}: fabricated actual_hours {r.actual_hours}")
            assert r.labour_cost == 0.0, (
                f"{label}: fabricated labour_cost {r.labour_cost}")


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
        except (AssertionError, Exception) as e:
            failures.append((t.__name__, e))
            print(f"FAIL: {t.__name__}: {e}")
    if failures:
        print(f"\n{len(failures)} FAILED")
        sys.exit(1)
    print(f"\nAll {len(tests)} tank-rejection tests passed.")
