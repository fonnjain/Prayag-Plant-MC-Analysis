"""Offline enforcement of the /build-state static assertions.

These tests replicate the code-inspection checks from /build-state so that CI
catches configuration regressions without a Google Sheets connection.  They
mirror checks #4, #8, #9, #10, #11, #12, #13, #14, #15, #16 from the live
route, plus an PLANTS_WITHOUT_RUNHOURS guard added in A2.

If the live /build-state route fails a STATIC check it also fails here —
giving an offline signal for pre-deploy gates.  Live-data checks (#1–#3, #6,
#7, #17) require Google Sheets and are NOT replicated here; those must pass in
the deployed environment via /build-state itself.

Run: cd artifacts/prayag && python3 -m pytest tests/test_build_state_gate.py
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import confirm
import ideal_hours
import sheets as _sht
import sources as _src


# ---------------------------------------------------------------------------
# #4  PIPE reconciliation (Report-5 ↔ Report-11) is wired
# ---------------------------------------------------------------------------
def test_pipe_daily_uses_report5_with_r11_reconcile():
    pipe_specs = _sht._DAILY_LAYOUTS.get("PIPE", [])
    pipe_emit = next((s for s in pipe_specs if s.get("emit") == "PIPE"), {})
    assert pipe_emit.get("tab") == "Report-5", (
        f"PIPE tab={pipe_emit.get('tab')!r}, expected 'Report-5'")
    assert pipe_emit.get("pipe_reconcile") is True, (
        "PIPE pipe_reconcile not True — Report-11 reconciliation is not wired")
    assert pipe_emit.get("report11_tab") == "Report-11", (
        f"report11_tab={pipe_emit.get('report11_tab')!r}, expected 'Report-11'")


# ---------------------------------------------------------------------------
# #8  PTMT roster = 55 machines
# ---------------------------------------------------------------------------
def test_ptmt_roster_55_machines():
    n = sum(len(v) for v in _src.PTMT_GROUPS.values())
    assert n == 55, f"PTMT roster = {n}, expected 55"


# ---------------------------------------------------------------------------
# #9  PTMT uses in-sheet IDEAL HOUR column
# ---------------------------------------------------------------------------
def test_ptmt_has_ideal_col_wired():
    ideal_col = _sht._DAILY_LAYOUTS.get("PTMT", [{}])[0].get("ideal_col")
    assert ideal_col is not None, "PTMT ideal_col is None — wrongly relying on baseline"


# ---------------------------------------------------------------------------
# #10  PTMT outliers compared within process group
# ---------------------------------------------------------------------------
def test_ptmt_outliers_scoped_to_process_group():
    src = inspect.getsource(confirm)
    assert "by_group_machine" in src, "confirm.py: by_group_machine grouping not present"
    assert "(plant, segment)" in src, "confirm.py: (plant, segment) outlier scope not present"


# ---------------------------------------------------------------------------
# #11  Tank layout = 'tank' (plant-level; no per-machine roster)
# ---------------------------------------------------------------------------
def test_tank_layout_is_plant_level():
    layout = _sht._DAILY_LAYOUTS.get("TANK", [{}])[0].get("layout")
    assert layout == "tank", f"TANK layout={layout!r}, expected 'tank'"


# ---------------------------------------------------------------------------
# #12  baselines.json exists and is readable
# ---------------------------------------------------------------------------
def test_baselines_json_exists():
    import json
    bl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "baselines.json")
    assert os.path.isfile(bl_path), f"baselines.json not found at {bl_path}"
    with open(bl_path) as f:
        bl = json.load(f)
    # 'machines' dict is intentionally empty until real planned-hours data is
    # supplied — the important check is that the key exists and is a dict.
    assert isinstance(bl.get("machines"), dict), (
        "baselines.json 'machines' key is missing or not a dict")


# ---------------------------------------------------------------------------
# #13  Quarantine gate: actual > calendar → quarantine; >100% → WARNING
# ---------------------------------------------------------------------------
def test_quarantine_rule_uses_calendar_hours():
    from confirm import tier3_row_classify
    src = inspect.getsource(tier3_row_classify)
    assert "_calendar_hours" in src, (
        "tier3_row_classify: _calendar_hours not found — old actual>ideal rule still active")
    assert "calendar maximum" in src, (
        "tier3_row_classify: 'calendar maximum' label not found")


# ---------------------------------------------------------------------------
# #14  Per-row quarantine; clean rows still publish
# ---------------------------------------------------------------------------
def test_per_row_quarantine_not_period_blocking():
    from confirm import tier3_row_classify
    src = inspect.getsource(tier3_row_classify)
    assert "quarantined.append" in src, (
        "quarantined.append not present — period-level blocking still active")
    assert "return clean, quarantined" in src, (
        "return clean, quarantined not present")


# ---------------------------------------------------------------------------
# #15  Current month labelled 'in progress' (not 'overdue')
# ---------------------------------------------------------------------------
def test_current_month_label_is_in_progress_not_overdue():
    src = inspect.getsource(confirm)
    no_comments = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#")
    )
    assert "in progress" in no_comments, (
        "confirm.py: 'in progress' not found in code (only in comments?)")
    assert "overdue" not in no_comments, (
        "confirm.py: 'overdue' wording found — should be 'in progress'")


# ---------------------------------------------------------------------------
# #16  No Sheets write calls (read-only)
# ---------------------------------------------------------------------------
def test_no_sheets_write_calls():
    src = inspect.getsource(_sht)
    assert "batchUpdate" not in src, (
        "sheets.py: batchUpdate found — safety violation, must remain read-only")
    assert "values:append" not in src, (
        "sheets.py: values:append found — safety violation, must remain read-only")


# ---------------------------------------------------------------------------
# A2 guard — all three TANK locations are in PLANTS_WITHOUT_RUNHOURS
# ---------------------------------------------------------------------------
def test_all_tank_locations_in_plants_without_runhours():
    for plant in ("TANK", "TANK_VN", "TANK_WB"):
        assert plant in ideal_hours.PLANTS_WITHOUT_RUNHOURS, (
            f"{plant} missing from PLANTS_WITHOUT_RUNHOURS — "
            "TANK_VN/WB will fabricate 0% utilisation instead of suppressing it")


# ---------------------------------------------------------------------------
# A1 guard — gen_pipe_moulds prefers 17-20 over Report-12
# ---------------------------------------------------------------------------
def test_gen_pipe_moulds_calls_load_pipe_moulds():
    from reports import generators
    src = inspect.getsource(generators.gen_pipe_moulds)
    assert "load_pipe_moulds" in src, (
        "gen_pipe_moulds does not call sheets.load_pipe_moulds — "
        "it will always use the Report-12 fallback path")
    assert "stale_mould_tabs" in src, (
        "gen_pipe_moulds missing stale_mould_tabs divergence check")


# ---------------------------------------------------------------------------
# A3 guard — _tank_model reads rej_kg from r.reject_count (not secondary_counts)
# ---------------------------------------------------------------------------
def test_tank_model_reads_reject_count_not_secondary_rej_kg():
    from reports import generators
    src = inspect.getsource(generators._tank_model)
    assert 'r.reject_count' in src, (
        "_tank_model reads rej_kg from sc.get('rej_kg') (always 0) "
        "instead of r.reject_count — VN/WB rejection will compute as 0%")
    assert "sc.get(\"rej_kg\"" not in src and "sc.get('rej_kg'" not in src, (
        "_tank_model still reads rej_kg from secondary_counts — this is always 0")


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
    print(f"\nAll {len(tests)} build-state gate tests passed.")
