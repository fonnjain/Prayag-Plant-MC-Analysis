"""Offline unit tests for sheets.resolve_report_tab.

Guards the core safety guarantee: a production figure must NEVER depend on an
unverified workbook Index. The resolver may only switch the daily-production tab
away from the configured fallback when it can positively verify the Index-named
tab exists, and it must honour the Index's own frequency rule (daily ingestion
resolves to a Daily/sliceable report only).

Pure / no network — the sheets internals it calls are monkeypatched.

Run: cd artifacts/prayag && python3 -m tests.test_resolve_report_tab
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets


def _patch(monkey: dict):
    """Apply attribute overrides on the sheets module, returning a restorer."""
    saved = {k: getattr(sheets, k) for k in monkey}
    for k, v in monkey.items():
        setattr(sheets, k, v)
    return lambda: [setattr(sheets, k, v) for k, v in saved.items()]


def test_verified_match_switches_to_index_tab():
    # A sliceable report matches the keywords AND its tab is present in the
    # workbook → switch to it (via_index True).
    restore = _patch({
        "workbook_index": lambda *a, **k: [
            {"report": "Report-11", "sliceable": True,
             "description": "m/c & item wise actual production in kg & pcs"},
        ],
        "_get_access_token": lambda *a, **k: "tok",
        "_daily_file_id": lambda *a, **k: "fid",
        "list_tabs": lambda *a, **k: ["Report-11", "Report-5"],
    })
    try:
        tab, via = sheets.resolve_report_tab(
            "PIPE", ["production"], "Report-X")
        assert tab == "Report-11" and via is True, (tab, via)
    finally:
        restore()
    print("PASS: verified description match switches to the Index-named tab")


def test_no_tab_list_with_mismatched_id_keeps_fallback():
    # The safety-critical case: list_tabs is unavailable (no token → titles=[]).
    # The Index names a DIFFERENT tab than the fallback. We must NOT trust the
    # unverified id — keep the fallback, via_index False, so the figure is
    # unchanged.
    restore = _patch({
        "workbook_index": lambda *a, **k: [
            {"report": "Report-99", "sliceable": True,
             "description": "m/c wise production"},
        ],
        "_get_access_token": lambda *a, **k: "",   # → titles stays []
        "_daily_file_id": lambda *a, **k: "fid",
        "list_tabs": lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("list_tabs must not be reached without a token")),
    })
    try:
        tab, via = sheets.resolve_report_tab(
            "PIPE", ["production"], "Report-5")
        assert tab == "Report-5" and via is False, (tab, via)
    finally:
        restore()
    print("PASS: unverifiable Index id that differs from fallback is rejected "
          "(fallback retained)")


def test_no_tab_list_with_spacing_only_diff_is_a_safe_noop_switch():
    # If the only difference is spacing ("Report-8 (A)" vs fallback "Report-8(A)")
    # the switch is a no-op pointing at the same tab → allowed, but we return the
    # known-good fallback string.
    restore = _patch({
        "workbook_index": lambda *a, **k: [
            {"report": "Report-8 (A)", "sliceable": True,
             "description": "m/c wise production"},
        ],
        "_get_access_token": lambda *a, **k: "",   # titles=[]
        "_daily_file_id": lambda *a, **k: "fid",
        "list_tabs": lambda *a, **k: [],
    })
    try:
        tab, via = sheets.resolve_report_tab(
            "PIPE", ["production"], "Report-8(A)")
        assert tab == "Report-8(A)" and via is True, (tab, via)
    finally:
        restore()
    print("PASS: spacing-only difference is a safe no-op switch returning the "
          "configured fallback string")


def test_sliceable_gating_skips_weekly_snapshot():
    # Two reports match the keywords: a weekly (non-sliceable) snapshot and a
    # daily (sliceable) tab. Daily ingestion must skip the weekly and pick the
    # daily one — a snapshot must never be sliced as if it were per-day.
    restore = _patch({
        "workbook_index": lambda *a, **k: [
            {"report": "Report-20", "sliceable": False,
             "description": "weekly production summary"},
            {"report": "Report-11", "sliceable": True,
             "description": "daily production detail"},
        ],
        "_get_access_token": lambda *a, **k: "tok",
        "_daily_file_id": lambda *a, **k: "fid",
        "list_tabs": lambda *a, **k: ["Report-11", "Report-20"],
    })
    try:
        tab, via = sheets.resolve_report_tab(
            "PIPE", ["production"], "Report-X", require_sliceable=True)
        assert tab == "Report-11" and via is True, (tab, via)
        # With require_sliceable=False the first match (weekly) is now eligible.
        tab2, via2 = sheets.resolve_report_tab(
            "PIPE", ["production"], "Report-X", require_sliceable=False)
        assert tab2 == "Report-20" and via2 is True, (tab2, via2)
    finally:
        restore()
    print("PASS: require_sliceable skips the weekly snapshot and resolves the "
          "daily tab; disabling it allows any frequency")


def test_no_index_degrades_to_fallback():
    restore = _patch({
        "workbook_index": lambda *a, **k: [],   # no Index available
        "_get_access_token": lambda *a, **k: "tok",
        "_daily_file_id": lambda *a, **k: "fid",
        "list_tabs": lambda *a, **k: ["Report-5"],
    })
    try:
        tab, via = sheets.resolve_report_tab("PIPE", ["production"], "Report-5")
        assert tab == "Report-5" and via is False, (tab, via)
    finally:
        restore()
    print("PASS: missing Index degrades to the configured fallback")


if __name__ == "__main__":
    test_verified_match_switches_to_index_tab()
    test_no_tab_list_with_mismatched_id_keeps_fallback()
    test_no_tab_list_with_spacing_only_diff_is_a_safe_noop_switch()
    test_sliceable_gating_skips_weekly_snapshot()
    test_no_index_degrades_to_fallback()
    print("\nAll resolve_report_tab unit tests passed.")
