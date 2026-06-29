"""Offline unit tests for the Compound Compilation engine (compound.py).

The Compound Compilation recomputes the Pipe plant's monthly / FY compound
mass-balance from the Pipe & Fitting daily Mixer-Logbook tabs (one per compound),
entirely from raw daily cells. The in-sheet "Compound 6-10" rollup is a
reconciliation reference only. Covers:
- the mixed-compound balance identity Closing = Opening + Material_out − Given,
- the CG-122 purchase/issue identity for CPVC Fittings,
- weight-loss % always recomputed (loss / batch),
- multi-month aggregation (opening = first month, closing = last month),
- FC excluded from the grand TOTAL and from reconciliation,
- raw-material item matrix rollup and ordering,
- the yield split (Pipe-extrusion compounds vs Fitting compounds),
- validate() PASS within tol, FAIL beyond it, NA when the rollup lacks a figure,
- validate() multi-month rollup summing.

Pure / no network — fixture parse dicts only.

Run: cd artifacts/prayag && python3 -m tests.test_compound
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import compound


def _mixer_month(opening, days):
    """A per-month mixer parse dict (as parse_mixer_logbook would emit)."""
    return {"opening": opening, "given_label": "Total Compound given", "days": days}


def _day(batch, material, given, loss, pulvizer=0.0, chems=None):
    return {"batch": batch, "material": material, "given": given, "loss": loss,
            "pulvizer": pulvizer, "chems": chems or {}}


def _cg_month(opening, days):
    return {"opening": opening, "days": days}


def _cg_day(purchase, issue):
    return {"purchase": purchase, "issue": issue}


def test_mixer_balance_identity():
    by = {"CPVC": [_mixer_month(1000.0, [
        _day(500.0, 495.0, 480.0, 5.0, pulvizer=10.0, chems={"Resin": 300.0, "CACO": 200.0}),
        _day(500.0, 497.0, 490.0, 3.0, pulvizer=20.0, chems={"Resin": 300.0, "CACO": 200.0}),
    ])]}
    comp = compound.build_compilation(by, ["2026-06"])
    col = next(c for c in comp["cols"] if c["key"] == "CPVC")
    assert col["opening"] == 1000.0
    assert col["batch"] == 1000.0
    assert col["material"] == 992.0
    assert col["given"] == 970.0
    assert col["loss"] == 8.0
    assert col["pulvizer"] == 30.0
    # Closing = Opening + Material_out − Given
    assert col["closing"] == 1000.0 + 992.0 - 970.0
    # loss_pct = loss / batch
    assert abs(col["loss_pct"] - (8.0 / 1000.0)) < 1e-9
    print("ok test_mixer_balance_identity")


def test_cg_purchase_issue_identity():
    by = {"CPVC_F": [_cg_month(9000.0, [_cg_day(5000.0, 4000.0), _cg_day(1000.0, 800.0)])]}
    comp = compound.build_compilation(by, ["2026-06"])
    col = next(c for c in comp["cols"] if c["key"] == "CPVC_F")
    assert col["kind"] == "cg"
    assert col["material"] == 6000.0   # purchase
    assert col["given"] == 4800.0      # issue
    assert col["loss"] == 0.0
    assert col["loss_pct"] is None
    # Closing = Opening + Purchase − Issue
    assert col["closing"] == 9000.0 + 6000.0 - 4800.0
    print("ok test_cg_purchase_issue_identity")


def test_multi_month_opening_and_closing():
    # Opening comes from the FIRST month; closing recomputes from running balance.
    by = {"UPVC": [
        _mixer_month(2000.0, [_day(1000.0, 990.0, 950.0, 10.0)]),
        _mixer_month(9999.0, [_day(1000.0, 980.0, 970.0, 20.0)]),  # 2nd opening ignored
    ]}
    comp = compound.build_compilation(by, ["2026-04", "2026-05"])
    col = next(c for c in comp["cols"] if c["key"] == "UPVC")
    assert col["opening"] == 2000.0       # first month only
    assert col["batch"] == 2000.0
    assert col["material"] == 1970.0
    assert col["given"] == 1920.0
    assert col["closing"] == 2000.0 + 1970.0 - 1920.0
    print("ok test_multi_month_opening_and_closing")


def test_fc_excluded_from_total():
    by = {
        "CPVC": [_mixer_month(0.0, [_day(100.0, 99.0, 95.0, 1.0)])],
        "FC":   [_mixer_month(0.0, [_day(50.0, 49.0, 45.0, 1.0)])],
    }
    comp = compound.build_compilation(by, ["2026-06"])
    # FC present as a column but NOT in the grand total.
    assert any(c["key"] == "FC" for c in comp["cols"])
    assert comp["total"]["batch"] == 100.0   # FC's 50 excluded
    assert comp["total"]["given"] == 95.0
    fc = next(c for c in comp["cols"] if c["key"] == "FC")
    assert fc["in_total"] is False
    print("ok test_fc_excluded_from_total")


def test_item_matrix_and_yield():
    by = {
        "CPVC": [_mixer_month(0.0, [_day(100.0, 99.0, 95.0, 1.0, chems={"Resin": 60.0, "CACO": 40.0})])],
        "UPVC": [_mixer_month(0.0, [_day(200.0, 198.0, 190.0, 2.0, chems={"Resin": 120.0})])],
        "UPVC_F": [_mixer_month(0.0, [_day(50.0, 49.0, 45.0, 1.0, chems={"Resin": 30.0})])],
    }
    comp = compound.build_compilation(by, ["2026-06"])
    # Item matrix aggregates across in-total compounds, sorted by total desc.
    mats = {m["name"]: m for m in comp["materials"]}
    assert mats["Resin"]["total"] == 60.0 + 120.0 + 30.0
    assert mats["Resin"]["by"]["CPVC"] == 60.0
    assert comp["materials"][0]["name"] == "Resin"  # largest first
    # Yield: pipe-extrusion compounds (CPVC, UPVC) vs fitting (UPVC_F).
    assert comp["pipe_given"] == 95.0 + 190.0
    assert comp["fitting_given"] == 45.0
    print("ok test_item_matrix_and_yield")


def test_validate_pass_fail_na():
    by = {
        "CPVC": [_mixer_month(0.0, [_day(1000.0, 990.0, 950.0, 10.0)])],
        "UPVC": [_mixer_month(0.0, [_day(2000.0, 1980.0, 1900.0, 20.0)])],
    }
    comp = compound.build_compilation(by, ["2026-06"])
    rollup = {"2026-06": {
        "CPVC": {"batch": 1000.0, "material": 990.0, "given": 950.0},   # exact -> PASS
        "UPVC": {"batch": 1500.0, "material": 1980.0, "given": 1900.0},  # batch off -> FAIL
        # AGRI absent -> all NA for AGRI (it has no recomputed either, but is in_total)
    }}
    res = compound.validate(comp, rollup, tol=0.005)
    assert res["available"] is True
    assert res["status"] == compound.FAIL
    bykey = {(r["compound"], r["field"]): r for r in res["rows"]}
    assert bykey[("CPVC", "Batch Weight")]["status"] == compound.PASS
    assert bykey[("UPVC", "Batch Weight")]["status"] == compound.FAIL
    assert bykey[("UPVC", "Batch Weight")]["diff_pct"] is not None
    # A compound with no rollup AND no recomputed figure is NA, never a fake PASS.
    assert bykey[("AGRI", "Batch Weight")]["status"] == compound.NA
    print("ok test_validate_pass_fail_na")


def test_validate_sums_multi_month_rollup():
    by = {"CPVC": [
        _mixer_month(0.0, [_day(1000.0, 990.0, 950.0, 10.0)]),
        _mixer_month(0.0, [_day(1000.0, 990.0, 950.0, 10.0)]),
    ]}
    comp = compound.build_compilation(by, ["2026-04", "2026-05"])
    rollup = {
        "2026-04": {"CPVC": {"batch": 1000.0, "material": 990.0, "given": 950.0}},
        "2026-05": {"CPVC": {"batch": 1000.0, "material": 990.0, "given": 950.0}},
    }
    res = compound.validate(comp, rollup, tol=0.005)
    # Recomputed batch (2000) reconciles against summed rollup (1000+1000).
    assert res["status"] == compound.PASS
    assert res["n_fail"] == 0
    print("ok test_validate_sums_multi_month_rollup")


def test_flow_window_suppresses_balance_and_isolates_days():
    # A sub-monthly window aggregates only its own dated day rows and blanks the
    # month-level opening/closing stock (a partial window has no stock balance).
    by = {"CPVC": [_mixer_month(1000.0, [
        {**_day(8000.0, 7777.0, 7000.0, 10.0), "date": "2026-06-10"},
        {**_day(4000.0, 3333.0, 3000.0, 5.0),  "date": "2026-06-20"},
    ])]}
    comp = compound.build_compilation(by, ["2026-06"], window=("2026-06-10", "2026-06-10"))
    assert comp["flow"] is True
    col = next(c for c in comp["cols"] if c["key"] == "CPVC")
    assert col["flow"] is True
    assert col["material"] == 7777.0          # ONLY the in-window day
    assert col["opening"] is None             # stock balance suppressed
    assert col["closing"] is None
    assert comp["total"]["opening"] is None    # ...and at the total level
    assert comp["total"]["closing"] is None
    # No window = full balance as before.
    full = compound.build_compilation(by, ["2026-06"])
    fcol = next(c for c in full["cols"] if c["key"] == "CPVC")
    assert full["flow"] is False
    assert fcol["material"] == 7777.0 + 3333.0
    assert fcol["opening"] == 1000.0 and fcol["closing"] is not None
    print("ok test_flow_window_suppresses_balance_and_isolates_days")


def test_validate_no_rollup_is_na():
    by = {"CPVC": [_mixer_month(0.0, [_day(100.0, 99.0, 95.0, 1.0)])]}
    comp = compound.build_compilation(by, ["2026-06"])
    res = compound.validate(comp, {})
    assert res["available"] is False
    assert res["status"] == compound.NA
    print("ok test_validate_no_rollup_is_na")


if __name__ == "__main__":
    test_mixer_balance_identity()
    test_cg_purchase_issue_identity()
    test_multi_month_opening_and_closing()
    test_fc_excluded_from_total()
    test_item_matrix_and_yield()
    test_validate_pass_fail_na()
    test_validate_sums_multi_month_rollup()
    test_flow_window_suppresses_balance_and_isolates_days()
    test_validate_no_rollup_is_na()
    print("all compound tests passed")
