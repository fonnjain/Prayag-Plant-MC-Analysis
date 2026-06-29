"""Regression for stale-rollup alert acknowledgement (dismiss a known stale rollup).

Three guarantees are locked in here, all offline (no Google Sheets, no Postgres):

1. ``compound.stale_rollup_alerts`` stamps each alert with a STABLE compound·month
   ``key`` (so an ack persists across pulls) plus a data ``fingerprint`` that
   changes when the alert's figures change (so the ack re-surfaces if the rollup
   drifts again to a new state after a fix).

2. ``_build_stale_rollup_alerts`` marks an alert acknowledged ONLY when a stored
   ack matches both the key and the current fingerprint — a stale-fingerprint ack
   lets the alert re-surface.

3. The ``/confirmation/ack_rollup`` route persists an ack only for an alert that
   is still current (matching fingerprint), and rejects an ack with no name.

Run: cd artifacts/prayag && python3 -m tests.test_stale_rollup_ack
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
import compound as compound_mod


# --- A tiny synthetic compound dataset that produces exactly one stale alert ---
# One compound (SWR_F) in one month. The daily Mixer-Logbook detail gives a
# closing of opening + material − given = 100 + 1000 − 900 = 200. The in-sheet
# rollup publishes that SAME closing (200) but understated material/given cells
# (800/700 → self-closing 200 as well? No — we want self != daily). Choose
# rollup material/given so the rollup's own cells DON'T reconcile to its closing
# but the daily flows DO, which is the "daily is authoritative / rollup stale"
# verdict the arbiter flags.
def _dataset(material=1000.0, given=900.0):
    day = {
        "date": "2026-05-10", "batch": material, "material": material,
        "given": given, "loss": 0.0, "pulvizer": 0.0, "chems": {},
        "given_label": "Total Compound given",
    }
    parse = {"ym": "2026-05", "opening": 100.0, "days": [day],
             "given_label": "Total Compound given"}
    by_compound = {"SWR_F": [parse]}
    # Rollup publishes the true daily closing (opening + daily material − daily
    # given) but understated summary cells (material 800 / given 750 → its own
    # self-closing would be 150, not the published value) — so the daily detail
    # reconciles with the published closing and the rollup's own cells do not,
    # which is exactly the "daily authoritative / rollup stale" verdict.
    rollup = {"2026-05": {"SWR_F": {
        "batch": 800.0, "material": 800.0, "given": 750.0,
        "opening": 100.0, "closing": 100.0 + material - given,
    }}}
    return by_compound, rollup, ["2026-05"]


def test_alert_has_stable_key_and_drifting_fingerprint():
    by_c, roll, months = _dataset()
    alerts = compound_mod.stale_rollup_alerts(by_c, roll, months)
    assert len(alerts) == 1, f"expected one stale alert, got {alerts}"
    a = alerts[0]
    assert a["key"] == "SWR / AGRI F|2026-05"
    assert a["fingerprint"], "alert must carry a data fingerprint"

    # A different data state (more material/given) keeps the SAME key but a
    # DIFFERENT fingerprint, so a prior ack will no longer apply.
    by_c2, roll2, months2 = _dataset(material=1500.0, given=1300.0)
    a2 = compound_mod.stale_rollup_alerts(by_c2, roll2, months2)[0]
    assert a2["key"] == a["key"], "key must be stable (compound·month)"
    assert a2["fingerprint"] != a["fingerprint"], "fingerprint must track the figures"
    print("PASS: stale-rollup alert key is stable, fingerprint tracks the data state")


def _patch_alerts(by_c, roll, months, acks):
    """Patch app to use the synthetic dataset + a fixed ack map, return builder."""
    orig = (appmod.is_demo_mode, appmod.load_compound_data,
            appmod.months_with_data, appmod.store.stale_rollup_acks)
    appmod.is_demo_mode = lambda: False
    appmod.load_compound_data = lambda m: {"by_compound": by_c, "rollup": roll, "months": months}
    appmod.months_with_data = lambda: months
    appmod.store.stale_rollup_acks = lambda: acks
    return orig


def _restore(orig):
    (appmod.is_demo_mode, appmod.load_compound_data,
     appmod.months_with_data, appmod.store.stale_rollup_acks) = orig


def test_ack_mutes_only_on_matching_fingerprint():
    by_c, roll, months = _dataset()
    base = compound_mod.stale_rollup_alerts(by_c, roll, months)[0]

    # Matching fingerprint → acknowledged (muted).
    orig = _patch_alerts(by_c, roll, months,
                         {base["key"]: {"fingerprint": base["fingerprint"], "approver": "Mgr"}})
    try:
        built = appmod._build_stale_rollup_alerts()
        assert built[0]["acknowledged"] is True, "matching ack must mute the alert"
        assert built[0]["ack"]["approver"] == "Mgr"
    finally:
        _restore(orig)

    # Stale fingerprint → re-surfaces (active).
    orig = _patch_alerts(by_c, roll, months,
                         {base["key"]: {"fingerprint": "stale", "approver": "Mgr"}})
    try:
        built = appmod._build_stale_rollup_alerts()
        assert built[0]["acknowledged"] is False, "stale-fingerprint ack must re-surface"
    finally:
        _restore(orig)
    print("PASS: ack mutes only on a matching fingerprint, re-surfaces otherwise")


def _run_route(alert_key, fingerprint, approver="A. Manager", route="ack_rollup"):
    by_c, roll, months = _dataset()
    recorded = []
    orig = _patch_alerts(by_c, roll, months, {})
    orig_avail, orig_rec = appmod.store.AVAILABLE, appmod.store.stale_rollup_ack_record
    appmod.store.AVAILABLE = True
    appmod.store.stale_rollup_ack_record = lambda action, **kw: recorded.append((action, kw))
    try:
        client = appmod.app.test_client()
        resp = client.post(f"/confirmation/{route}", data={
            "alert_key": alert_key, "fingerprint": fingerprint,
            "approver": approver, "next": "/confirmation",
        }, follow_redirects=False)
        return recorded, resp.headers.get("Location", "")
    finally:
        appmod.store.AVAILABLE = orig_avail
        appmod.store.stale_rollup_ack_record = orig_rec
        _restore(orig)


def test_route_records_for_current_alert():
    by_c, roll, months = _dataset()
    a = compound_mod.stale_rollup_alerts(by_c, roll, months)[0]
    recorded, _ = _run_route(a["key"], a["fingerprint"])
    assert len(recorded) == 1 and recorded[0][0] == "ack", "ack must persist"
    assert recorded[0][1]["alert_key"] == a["key"]
    print("PASS: acknowledging a current stale-rollup alert persists it")


def test_route_rejects_stale_or_unknown_alert():
    by_c, roll, months = _dataset()
    a = compound_mod.stale_rollup_alerts(by_c, roll, months)[0]
    recorded, _ = _run_route(a["key"], "wrong-fingerprint")
    assert not recorded, "ack for a superseded data state must NOT be recorded"
    recorded2, _ = _run_route("NOPE|2026-05", a["fingerprint"])
    assert not recorded2, "ack for an unknown alert must NOT be recorded"
    print("PASS: acknowledging a stale/unknown alert is rejected")


def test_route_requires_a_name():
    by_c, roll, months = _dataset()
    a = compound_mod.stale_rollup_alerts(by_c, roll, months)[0]
    recorded, _ = _run_route(a["key"], a["fingerprint"], approver="")
    assert not recorded, "ack without a name must NOT be recorded"
    print("PASS: acknowledgement without a name is rejected")


if __name__ == "__main__":
    test_alert_has_stable_key_and_drifting_fingerprint()
    test_ack_mutes_only_on_matching_fingerprint()
    test_route_records_for_current_alert()
    test_route_rejects_stale_or_unknown_alert()
    test_route_requires_a_name()
    print("\nAll stale-rollup acknowledgement tests passed.")
