"""Focused regression tests for partial daily-read handling.

``get_daily_records`` recovers around an isolated workbook failure and appends a
sentinel dict ``{"_failed_pairs": [(plant, ym), ...]}`` to its ``reports`` list.
Callers that discarded reports used to silently aggregate the remaining, under-
counted records. These tests lock in the fix per consumer:

  * NUMERICAL results (ideal-hours baseline, compound yield, tank kg/rejection,
    per-unit production for power cost) WITHHOLD the affected figure rather than
    publish an under-count.
  * DIAGNOSTIC/STATUS views (freshness panel, verification) EXPOSE the partial
    state explicitly (the affected plant-months are surfaced).

Run: cd artifacts/prayag && python3 -m tests.test_partial_daily_reads
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from metrics import Record


def _daily(plant, machine, ym, day, out, **kw):
    return Record(
        grain="daily", plant=plant, segment=plant.title(), machine=machine,
        period=ym, date=f"{ym}-{day:02d}", total_count=out, reject_count=0.0,
        actual_hours=kw.pop("hours", 8.0), ideal_hours=kw.pop("ideal", 8.0),
        **kw,
    )


def _failed_report(pairs):
    return {"_failed_pairs": list(pairs),
            "_failed_pair_reasons": {f"{p}:{m}": "" for p, m in pairs}}


class _Patch:
    """Temporarily swap attributes on ``app`` (and restore them)."""

    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for name, val in self._kw.items():
            self._saved[name] = getattr(app, name)
            setattr(app, name, val)
        return self

    def __exit__(self, *exc):
        for name, val in self._saved.items():
            setattr(app, name, val)
        return False


# ---------------------------------------------------------------------------
# _daily_failed_pairs — the extraction helper
# ---------------------------------------------------------------------------

def test_failed_pairs_extracted_from_sentinel():
    reports = [{"notes": ["benign"]}, _failed_report([("PIPE", "2026-06")])]
    assert app._daily_failed_pairs(reports) == [("PIPE", "2026-06")]


def test_failed_pairs_empty_when_none():
    assert app._daily_failed_pairs([{"notes": []}]) == []
    assert app._daily_failed_pairs([]) == []
    assert app._daily_failed_pairs(None) == []
    print("PASS: _daily_failed_pairs extracts sentinel, empty otherwise")


# ---------------------------------------------------------------------------
# _unit_prod — NUMERICAL: withhold the tainted (month, unit) bucket
# ---------------------------------------------------------------------------

def test_unit_prod_withholds_tainted_month_unit():
    # UNIT-2 = PIPE / MOULDING / TANK. PIPE's Jun read failed → Jun UNIT-2 is
    # tainted and must be withheld (not shown under-counted). May is clean and
    # kept. UNIT-3 (GARDEN) is untouched.
    rows = [
        _daily("PIPE", "M1", "2026-05", 1, 100.0),
        _daily("PIPE", "M1", "2026-06", 1, 100.0),
        _daily("MOULDING", "M2", "2026-06", 1, 50.0),
        _daily("GARDEN", "G1", "2026-06", 1, 30.0),
    ]

    def _gd(months):
        return rows, [_failed_report([("PIPE", "2026-06")])], []

    with _Patch(get_daily_records=_gd):
        out = app._unit_prod(["2026-05", "2026-06"])

    assert ("2026-05", "UNIT-2") in out, out          # clean month kept
    assert ("2026-06", "UNIT-2") not in out, out       # tainted → withheld
    assert ("2026-06", "UNIT-3") in out, out           # unaffected unit kept
    print("PASS: _unit_prod withholds the tainted (month, unit) bucket")


def test_unit_prod_full_read_keeps_everything():
    rows = [_daily("PIPE", "M1", "2026-06", 1, 100.0)]

    def _gd(months):
        return rows, [], []

    with _Patch(get_daily_records=_gd):
        out = app._unit_prod(["2026-06"])
    assert ("2026-06", "UNIT-2") in out
    assert out[("2026-06", "UNIT-2")].get("kg") == 100.0, out
    print("PASS: _unit_prod keeps everything on a complete read")


# ---------------------------------------------------------------------------
# _build_ideal_input — NUMERICAL: drop affected plant's baseline rows
# ---------------------------------------------------------------------------

def test_ideal_input_drops_partial_plant_baseline():
    rows = [
        _daily("PIPE", "PIPE M/C - 1", "2026-06", 1, 100.0, ideal=8.0),
        _daily("PIPE", "PIPE M/C - 1", "2026-06", 2, 100.0, ideal=8.0),
        _daily("HDPE", "HDPE M/C - 1", "2026-06", 1, 100.0, ideal=8.0),
    ]

    def _gd(months):
        return rows, [_failed_report([("PIPE", "2026-06")])], []

    with _Patch(get_daily_records=_gd):
        # ideal_overrides_for must return {} so PIPE isn't resurfaced via override.
        saved = app.store.ideal_overrides_for
        app.store.ideal_overrides_for = lambda m: {}
        try:
            view = app._build_ideal_input("2026-06")
        finally:
            app.store.ideal_overrides_for = saved

    plants = {r["plant"] for r in view["rows"]}
    assert "PIPE" not in plants, plants          # withheld: half-summed baseline
    assert "HDPE" in plants, plants              # clean plant kept
    assert view["partial_daily_disp"], view      # partial state exposed
    assert "PIPE" in view["partial_daily_plants"], view
    print("PASS: _build_ideal_input drops the partial plant and flags partial")


def test_ideal_input_override_survives_partial_read():
    rows = [_daily("PIPE", "PIPE M/C - 1", "2026-06", 1, 100.0, ideal=8.0)]

    def _gd(months):
        return rows, [_failed_report([("PIPE", "2026-06")])], []

    ov = {("PIPE", "PIPE M/C - 1"): {"ideal_hours": 200.0, "set_by": "mgr",
                                     "when_disp": "", "note": ""}}
    with _Patch(get_daily_records=_gd):
        saved = app.store.ideal_overrides_for
        app.store.ideal_overrides_for = lambda m: ov
        try:
            view = app._build_ideal_input("2026-06")
        finally:
            app.store.ideal_overrides_for = saved

    pipe_rows = [r for r in view["rows"] if r["plant"] == "PIPE"]
    assert len(pipe_rows) == 1, view["rows"]
    # Override (DB) is independent of the daily read → still effective; the SHEET
    # baseline is withheld (None) since the daily rows were dropped.
    assert pipe_rows[0]["override"] == 200.0, pipe_rows[0]
    assert pipe_rows[0]["sheet_value"] is None, pipe_rows[0]
    print("PASS: ideal-hours override survives a partial daily read")


# ---------------------------------------------------------------------------
# _build_verify — DIAGNOSTIC: expose partial state
# ---------------------------------------------------------------------------

def test_verify_exposes_partial_state():
    def _gr(months):
        return [], [], []

    def _gd(months):
        return ([], [_failed_report([("TANK", "2026-06")])], [])

    with _Patch(get_records=_gr, get_daily_records=_gd,
                _apply_baselines=lambda rows: None):
        result = app._build_verify("2026-06")

    assert result["partial_daily_pairs"], result
    assert result["partial_daily_pairs"][0]["plant"] == "TANK", result
    assert result["partial_daily_disp"], result
    print("PASS: _build_verify exposes the partial daily state")


def test_verify_no_partial_on_clean_read():
    with _Patch(get_records=lambda m: ([], [], []),
                get_daily_records=lambda m: ([], [], []),
                _apply_baselines=lambda rows: None):
        result = app._build_verify("2026-06")
    assert result["partial_daily_pairs"] == [], result
    assert result["partial_daily_disp"] == "", result
    print("PASS: _build_verify reports no partial state on a clean read")


# ---------------------------------------------------------------------------
# _build_freshness — DIAGNOSTIC: expose partial state + set partial flag
# ---------------------------------------------------------------------------

def test_freshness_flags_isolated_daily_failure():
    def _gd(months):
        return ([], [_failed_report([("MOULDING", "2026-06")])], [])

    with _Patch(is_demo_mode=lambda: False,
                months_with_data=lambda: ["2026-06"],
                get_records=lambda m: ([], [], []),
                get_daily_records=_gd,
                _build_stale_rollup_alerts=lambda: []):
        out = app._build_freshness()

    assert out["partial"] is True, out
    assert out["daily_partial_pairs"], out
    assert out["daily_partial_pairs"][0]["plant"] == "MOULDING", out
    assert out["daily_partial_disp"], out
    print("PASS: _build_freshness flags an isolated daily failure as partial")


# ---------------------------------------------------------------------------
# _tank_location_report — NUMERICAL: withhold kg/rejection when tank read partial
# ---------------------------------------------------------------------------

def test_tank_location_withholds_kg_on_partial(monkeypatch=None):
    # Route-level: assert the render context withholds daily-derived kg totals
    # and flags the view partial when the tank plant's daily read failed.
    daily = [
        _daily("TANK_VN", "Mould-A", "2026-06", 1, 500.0,
               secondary_counts={"kg": 40.0, "rej_mouth_kg": 2.0}),
    ]

    def _gd(months):
        return (daily, [_failed_report([("TANK_VN", "2026-06")])], [])

    captured = {}

    def _fake_render(template, **ctx):
        captured["template"] = template
        captured["ctx"] = ctx
        return ""

    with _Patch(get_daily_records=_gd, render_template=_fake_render,
                load_report_records=lambda fam: [],
                _sync_ctx=lambda: {}):
        with app.app.test_request_context("/reports/tank_vn?period=2026-06"):
            app._tank_location_report("tank_vn", "TANK_VN", "VN", "Tanks (VN)")

    ctx = captured["ctx"]
    assert ctx["tank_daily_partial"] is True, ctx
    assert ctx["total_kg"] == 0.0, ctx            # withheld, not the 40.0 above
    assert "TANK_VN" in ctx["tank_daily_partial_disp"] \
        or ctx["tank_daily_partial_disp"], ctx
    print("PASS: _tank_location_report withholds daily kg and flags partial")


# ---------------------------------------------------------------------------
# report_compound_compilation — NUMERICAL: withhold pipe_output/yield on partial
# ---------------------------------------------------------------------------

def test_compound_withholds_pipe_output_on_partial():
    daily = [_daily("PIPE", "M1", "2026-06", 1, 1000.0)]

    def _gd(months):
        return (daily, [_failed_report([("PIPE", "2026-06")])], [])

    captured = {}

    def _fake_render(template, **ctx):
        captured["template"] = template
        captured["ctx"] = ctx
        return ""

    import compound as compound_mod
    saved_build = compound_mod.build_compilation
    saved_validate = compound_mod.validate
    saved_trend = compound_mod.month_trend
    saved_mover = compound_mod.biggest_mover
    compound_mod.build_compilation = lambda *a, **k: {"pipe_given": 900.0}
    compound_mod.validate = lambda *a, **k: {"available": False, "status": "NA"}
    compound_mod.month_trend = lambda *a, **k: {"months": [], "total": [], "compounds": []}
    compound_mod.biggest_mover = lambda *a, **k: None

    with _Patch(get_daily_records=_gd, render_template=_fake_render,
                load_compound_data=lambda months: {
                    "by_compound": {}, "months": ["2026-06"], "rollup": {}},
                _sync_ctx=lambda: {}):
        with app.app.test_request_context("/reports/compound_compilation?period=2026-06"):
            try:
                app.report_compound_compilation()
            finally:
                compound_mod.build_compilation = saved_build
                compound_mod.validate = saved_validate
                compound_mod.month_trend = saved_trend
                compound_mod.biggest_mover = saved_mover

    ctx = captured["ctx"]
    assert ctx["pipe_partial"] is True, ctx
    assert ctx["pipe_output"] is None, ctx      # withheld, not 1000.0
    assert ctx["yield_pct"] is None, ctx        # derived figure withheld too
    print("PASS: compound report withholds pipe_output and yield on partial read")


if __name__ == "__main__":
    test_failed_pairs_extracted_from_sentinel()
    test_failed_pairs_empty_when_none()
    test_unit_prod_withholds_tainted_month_unit()
    test_unit_prod_full_read_keeps_everything()
    test_ideal_input_drops_partial_plant_baseline()
    test_ideal_input_override_survives_partial_read()
    test_verify_exposes_partial_state()
    test_verify_no_partial_on_clean_read()
    test_freshness_flags_isolated_daily_failure()
    test_tank_location_withholds_kg_on_partial()
    test_compound_withholds_pipe_output_on_partial()
    print("\nAll partial daily-read regression tests passed.")
