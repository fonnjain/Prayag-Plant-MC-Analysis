"""Route-level regression for the manager sign-off, with NO network/DB.

Locks in the re-gating semantics: a sign-off must bind to the data state shown.
If the posted fingerprint no longer matches the freshly recomputed confirmation
(the sheets changed since the page loaded), the approval must be REJECTED and
nothing written to the trail. A matching fingerprint must persist an approval.

`get_data` (which hits Google Sheets) and `store.record` (Postgres) are stubbed,
so the test is deterministic and offline.

Run: cd artifacts/prayag && python3 -m tests.test_signoff_routes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod


def _fake_data(fingerprint, status="error"):
    return {
        "from_iso": "2026-04-01", "to_iso": "2027-03-31",
        "period_label": "Current FY",
        "confirmation": {
            "status": status,
            "period_key": "2026-04-01_2027-03-31___",
            "fingerprint": fingerprint,
            "score_label": "4/4 files",
            "counts": {"error": 3, "warning": 4, "total": 7},
        },
    }


def _run(posted_fp, current_fp, approver="A. Manager", status="error"):
    """POST an approval; return (recorded_actions, redirect_location)."""
    recorded = []
    orig_get_data, orig_record, orig_avail = (
        appmod.get_data, appmod.store.record, appmod.store.AVAILABLE,
    )
    appmod.get_data = lambda form: _fake_data(current_fp, status)
    appmod.store.AVAILABLE = True
    appmod.store.record = lambda action, **kw: recorded.append((action, kw))
    try:
        client = appmod.app.test_client()
        resp = client.post("/confirmation/approve", data={
            "period": "current_fy", "approver": approver, "fingerprint": posted_fp,
        }, follow_redirects=False)
        return recorded, resp.headers.get("Location", "")
    finally:
        appmod.get_data = orig_get_data
        appmod.store.record = orig_record
        appmod.store.AVAILABLE = orig_avail


def test_stale_fingerprint_is_rejected():
    recorded, loc = _run(posted_fp="STALEFP", current_fp="LIVEFP")
    assert not recorded, "a stale (changed-data) sign-off must NOT be recorded"
    assert "changed" in loc, "user must be told the data changed"
    print("PASS: stale fingerprint is rejected and nothing is recorded")


def test_matching_fingerprint_is_recorded():
    recorded, loc = _run(posted_fp="LIVEFP", current_fp="LIVEFP")
    assert len(recorded) == 1 and recorded[0][0] == "approve", "approval must persist"
    assert recorded[0][1]["fingerprint"] == "LIVEFP"
    print("PASS: matching fingerprint persists the approval")


def test_missing_approver_is_rejected():
    recorded, loc = _run(posted_fp="LIVEFP", current_fp="LIVEFP", approver="")
    assert not recorded, "an approval without a name must NOT be recorded"
    print("PASS: approval without a name is rejected")


if __name__ == "__main__":
    test_stale_fingerprint_is_rejected()
    test_matching_fingerprint_is_recorded()
    test_missing_approver_is_rejected()
    print("\nAll sign-off route regression tests passed.")
