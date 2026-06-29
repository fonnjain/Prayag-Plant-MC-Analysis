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
- validate() multi-month rollup summing,
- the closing-stock arbiter: verdict "daily" when the published closing ties out
  to the daily flows but NOT to the understated rollup cells (April/May pattern),
  and NO diagnosis when the rollup is self-consistent (June control).

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


def test_month_trend_per_compound_breakdown():
    # The trend returns BOTH the grand total AND a per-compound breakdown, each
    # aligned position-for-position to the months that have data.
    by = {
        "CPVC": [
            {**_mixer_month(0.0, [_day(1000.0, 990.0, 950.0, 10.0)]), "ym": "2026-04"},
            {**_mixer_month(0.0, [_day(2000.0, 1980.0, 1900.0, 40.0)]), "ym": "2026-05"},
        ],
        "UPVC": [
            {**_mixer_month(0.0, [_day(500.0, 495.0, 480.0, 5.0)]), "ym": "2026-04"},
            # UPVC has NO May data -> given 0, loss_pct None for that month.
        ],
    }
    trend = compound.month_trend(by, ["2026-04", "2026-05"])
    assert trend["months"] == ["2026-04", "2026-05"]
    # Grand-total series still produced.
    assert [t["ym"] for t in trend["total"]] == ["2026-04", "2026-05"]
    assert trend["total"][0]["given"] == 950.0 + 480.0
    # Per-compound series for all 7 in-total compounds (FC excluded).
    bykey = {c["key"]: c for c in trend["compounds"]}
    assert set(bykey) == {"CPVC", "UPVC", "AGRI", "SWR", "UPVC_F", "SWR_F", "CPVC_F"}
    assert "FC" not in bykey
    # CPVC drifts up Apr->May; UPVC absent in May reads 0 given, None loss.
    assert bykey["CPVC"]["given"] == [950.0, 1900.0]
    assert abs(bykey["CPVC"]["loss_pct"][1] - 2.0) < 1e-9   # 40/2000 = 2%
    assert bykey["UPVC"]["given"] == [480.0, 0.0]
    assert bykey["UPVC"]["loss_pct"][1] is None
    # A compound that never logged anything is a flat row of zeros / None.
    assert bykey["AGRI"]["given"] == [0.0, 0.0]
    assert bykey["AGRI"]["loss_pct"] == [None, None]
    print("ok test_month_trend_per_compound_breakdown")


def test_month_trend_skips_empty_months():
    # Months with no data anywhere are dropped, and per-compound arrays stay
    # aligned to the surviving months only.
    by = {"CPVC": [
        {**_mixer_month(0.0, [_day(1000.0, 990.0, 950.0, 10.0)]), "ym": "2026-06"},
    ]}
    trend = compound.month_trend(by, ["2026-04", "2026-05", "2026-06"])
    assert trend["months"] == ["2026-06"]
    bykey = {c["key"]: c for c in trend["compounds"]}
    assert bykey["CPVC"]["given"] == [950.0]
    assert len(bykey["CPVC"]["loss_pct"]) == 1
    print("ok test_month_trend_skips_empty_months")


def test_validate_no_rollup_is_na():
    by = {"CPVC": [_mixer_month(0.0, [_day(100.0, 99.0, 95.0, 1.0)])]}
    comp = compound.build_compilation(by, ["2026-06"])
    res = compound.validate(comp, {})
    assert res["available"] is False
    assert res["status"] == compound.NA
    print("ok test_validate_no_rollup_is_na")


def _tagged(ym, opening, days, **extra):
    """A per-month mixer parse dict tagged with its ``ym`` (as load_compound_data
    stamps it), optionally carrying junk fields (e.g. a stored loss_pct) that the
    engine must ignore."""
    p = {"ym": ym, "opening": opening, "given_label": "Total Compound given", "days": days}
    p.update(extra)
    return p


def test_month_trend_ties_to_aggregate_total():
    # Per-month trend points must sum to exactly what build_compilation reports
    # over the whole period — otherwise the chart silently desyncs from the grid.
    # One month carries a bogus stored "loss_pct" the engine must NOT read.
    by = {
        "CPVC": [
            _tagged("2026-04", 1000.0, [_day(1000.0, 990.0, 950.0, 10.0)], loss_pct=99.0),
            _tagged("2026-05", 9999.0, [_day(2000.0, 1980.0, 1900.0, 40.0)]),
        ],
        "UPVC": [
            _tagged("2026-04", 0.0, [_day(500.0, 495.0, 480.0, 5.0)]),
            _tagged("2026-05", 0.0, [_day(700.0, 690.0, 670.0, 7.0)]),
        ],
    }
    months = ["2026-04", "2026-05"]
    series = compound.month_trend(by, months)["total"]
    assert [s["ym"] for s in series] == months

    # Sum of per-month "given" == the aggregated grand TOTAL given.
    agg = compound.build_compilation(by, months)
    assert sum(s["given"] for s in series) == round(agg["total"]["given"], 0)
    assert sum(s["given"] for s in series) == round(950.0 + 1900.0 + 480.0 + 670.0, 0)

    # Each month's loss% is recomputed loss/batch (NOT the stored 99.0).
    for ym in months:
        sub = {k: [p for p in plist if p["ym"] == ym] for k, plist in by.items()}
        mcomp = compound.build_compilation(sub, [ym])
        pt = next(s for s in series if s["ym"] == ym)
        expected = round((mcomp["total"]["loss"] / mcomp["total"]["batch"]) * 100, 2)
        assert pt["loss_pct"] == expected
    # 2026-04: loss 15 / batch 1500 = 1.0% ; the stored 99.0 was ignored.
    assert next(s for s in series if s["ym"] == "2026-04")["loss_pct"] == 1.0
    print("ok test_month_trend_ties_to_aggregate_total")


def test_month_trend_single_month_and_window():
    by = {"CPVC": [_tagged("2026-06", 0.0, [
        {**_day(1000.0, 990.0, 950.0, 10.0), "date": "2026-06-10"},
        {**_day(2000.0, 1980.0, 1900.0, 20.0), "date": "2026-06-20"},
    ])]}

    # Single-month period: exactly one trend point, tying to the month's total.
    series = compound.month_trend(by, ["2026-06"])["total"]
    assert len(series) == 1
    agg = compound.build_compilation(by, ["2026-06"])
    assert series[0]["given"] == round(agg["total"]["given"], 0)
    assert series[0]["loss_pct"] == round((agg["total"]["loss_pct"] or 0.0) * 100, 2)

    # A month with no matching ym data is skipped → empty series. This is the
    # contract the app relies on to blank the trend for a sub-monthly window:
    # build_compilation(window=...) is a flow view with no monthly balance to
    # chart, so the trend is suppressed entirely.
    assert compound.month_trend(by, ["2026-07"])["months"] == []
    assert compound.month_trend(by, ["2026-07"])["total"] == []
    assert compound.month_trend(by, [])["total"] == []
    win = compound.build_compilation(by, ["2026-06"], window=("2026-06-10", "2026-06-10"))
    assert win["flow"] is True
    assert win["total"]["opening"] is None and win["total"]["closing"] is None
    print("ok test_month_trend_single_month_and_window")


def test_validate_arbiter_understated_rollup_verdict_daily():
    # Reproduces the April/May understated-rollup pattern for SWR / AGRI F: the
    # daily Mixer-Logbook detail carries the true flows, the sheet's published
    # period-closing stock ties out to those daily flows, but the rollup's own
    # monthly Batch/Material/Given cells are understated and would imply a
    # different (lower) closing. The arbiter must declare the daily detail
    # authoritative.
    by = {"SWR_F": [
        _mixer_month(1000.0, [_day(3000.0, 2900.0, 2400.0, 100.0)]),  # April
        _mixer_month(9999.0, [_day(3000.0, 2900.0, 2400.0, 100.0)]),  # May (opening ignored)
    ]}
    comp = compound.build_compilation(by, ["2026-04", "2026-05"])
    col = next(c for c in comp["cols"] if c["key"] == "SWR_F")
    # Daily-flow closing = first-opening + Σmaterial − Σgiven.
    assert col["closing"] == 1000.0 + 5800.0 - 4800.0  # == 2000

    rollup = {
        # Understated monthly cells; published closing carried from daily flows.
        "2026-04": {"SWR_F": {"opening": 1000.0, "batch": 2000.0, "material": 2000.0,
                              "given": 2400.0, "closing": 600.0}},
        "2026-05": {"SWR_F": {"batch": 2000.0, "material": 2000.0,
                              "given": 2400.0, "closing": 2000.0}},
    }
    res = compound.validate(comp, rollup, tol=0.005)
    # The understated material/given cells must trip a FAIL...
    assert res["status"] == compound.FAIL
    # ...and the arbiter must emit exactly one "daily" verdict for SWR / AGRI F.
    assert len(res["diagnoses"]) == 1
    diag = res["diagnoses"][0]
    assert diag["compound"] == "SWR / AGRI F"
    assert diag["verdict"] == "daily"
    # The published closing (2000) is cited, not the rollup-self closing (200).
    assert "2,000 kg" in diag["text"]
    assert "200 kg" in diag["text"]
    print("ok test_validate_arbiter_understated_rollup_verdict_daily")


def test_validate_arbiter_self_consistent_rollup_no_diagnosis():
    # Control (June): the rollup's own Batch/Material/Given cells reconcile to its
    # OWN published closing stock, even though they differ from the daily detail.
    # Because the rollup is self-consistent, the arbiter must NOT declare the daily
    # detail authoritative — no diagnosis is emitted (even though a check FAILs).
    by = {"SWR_F": [_mixer_month(1000.0, [_day(3000.0, 5800.0, 4800.0, 100.0)])]}
    comp = compound.build_compilation(by, ["2026-06"])
    col = next(c for c in comp["cols"] if c["key"] == "SWR_F")
    assert col["closing"] == 1000.0 + 5800.0 - 4800.0  # == 2000

    rollup = {
        # material − given = 3000 − 2000 = 1000, so opening(1000)+1000 = 2000 ==
        # the published closing: the rollup reconciles with itself.
        "2026-06": {"SWR_F": {"opening": 1000.0, "batch": 3000.0, "material": 3000.0,
                              "given": 2000.0, "closing": 2000.0}},
    }
    res = compound.validate(comp, rollup, tol=0.005)
    # The differing material/given still FAIL the cell-level reconciliation...
    assert res["status"] == compound.FAIL
    assert res["n_fail"] > 0
    # ...but the arbiter stays silent because the rollup is self-consistent.
    assert res["diagnoses"] == []
    print("ok test_validate_arbiter_self_consistent_rollup_no_diagnosis")


if __name__ == "__main__":
    test_mixer_balance_identity()
    test_cg_purchase_issue_identity()
    test_multi_month_opening_and_closing()
    test_fc_excluded_from_total()
    test_item_matrix_and_yield()
    test_validate_pass_fail_na()
    test_validate_sums_multi_month_rollup()
    test_flow_window_suppresses_balance_and_isolates_days()
    test_month_trend_per_compound_breakdown()
    test_month_trend_skips_empty_months()
    test_validate_no_rollup_is_na()
    test_month_trend_ties_to_aggregate_total()
    test_month_trend_single_month_and_window()
    test_validate_arbiter_understated_rollup_verdict_daily()
    test_validate_arbiter_self_consistent_rollup_no_diagnosis()
    print("all compound tests passed")
