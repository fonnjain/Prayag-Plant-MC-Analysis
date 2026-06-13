"""Regression for per-issue acknowledgement (accept individual flagged issues).

Two guarantees are locked in here, both offline (no Google Sheets, no Postgres):

1. ``confirm.issue_key`` is STABLE across figure drift. The same logical anomaly
   (same plant/machine/month/tier, same wording bar the numbers) must keep the
   same key so an acknowledgement is not silently lost on the next data pull when
   the exact magnitude changes. A genuinely different issue must get a different
   key.

2. The ``/confirmation/ack_issue`` route persists an acknowledgement only for an
   issue that actually exists in the live data, and rejects an ack with no name.

Run: cd artifacts/prayag && python3 -m tests.test_issue_ack
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from confirm import issue_key


def test_issue_key_is_stable_across_magnitude_drift():
    a = {"tier": 2, "severity": "warning", "plant": "PIPE", "machine": "",
         "month": "", "sheet": "MC", "file": "F1",
         "message": "PIPE: detail rows sum to 1000 but the sheet TOTAL is 1066 (6.6% off)."}
    b = dict(a, message="PIPE: detail rows sum to 1200 but the sheet TOTAL is 1290 (7.5% off).")
    assert issue_key(a) == issue_key(b), "same anomaly with drifted figures must keep its key"

    c = dict(a, machine="M/C-7")  # a different location → different issue
    assert issue_key(a) != issue_key(c), "a structurally different issue must differ"
    print("PASS: issue_key is stable across magnitude drift, distinct across location")


def _fake_data(issues, status="error"):
    return {
        "from_iso": "2026-04-01", "to_iso": "2027-03-31",
        "period_label": "Current FY",
        "confirmation": {
            "status": status,
            "period_key": "2026-04-01_2027-03-31___",
            "fingerprint": "FP", "score_label": "4/4 files",
            "counts": {"error": 1, "warning": 0, "total": 1},
            "issues": issues,
        },
    }


def _run_ack(issue_key_posted, live_issues, approver="A. Manager", route="ack_issue"):
    recorded = []
    orig_get_data, orig_record, orig_avail = (
        appmod.get_data, appmod.store.ack_record, appmod.store.AVAILABLE,
    )
    appmod.get_data = lambda form: _fake_data(live_issues)
    appmod.store.AVAILABLE = True
    appmod.store.ack_record = lambda action, **kw: recorded.append((action, kw))
    try:
        client = appmod.app.test_client()
        resp = client.post(f"/confirmation/{route}", data={
            "period": "current_fy", "approver": approver, "issue_key": issue_key_posted,
        }, follow_redirects=False)
        return recorded, resp.headers.get("Location", "")
    finally:
        appmod.get_data = orig_get_data
        appmod.store.ack_record = orig_record
        appmod.store.AVAILABLE = orig_avail


def test_ack_records_for_present_issue():
    live = [{"key": "ABC123", "tier": 2, "severity": "warning", "plant": "PIPE",
             "machine": "", "message": "PIPE reconcile off."}]
    recorded, loc = _run_ack("ABC123", live)
    assert len(recorded) == 1 and recorded[0][0] == "ack", "ack must persist"
    assert recorded[0][1]["issue_key"] == "ABC123"
    assert recorded[0][1]["plant"] == "PIPE", "ack must capture the issue's metadata"
    print("PASS: acknowledging a present issue persists it with its metadata")


def test_ack_rejected_when_issue_absent():
    live = [{"key": "OTHER", "tier": 1, "severity": "error", "message": "x"}]
    recorded, loc = _run_ack("GONE", live)
    assert not recorded, "ack for a missing issue must NOT be recorded"
    print("PASS: acknowledging a vanished issue is rejected")


def test_ack_requires_a_name():
    live = [{"key": "ABC123", "tier": 2, "severity": "warning", "message": "x"}]
    recorded, loc = _run_ack("ABC123", live, approver="")
    assert not recorded, "ack without a name must NOT be recorded"
    print("PASS: acknowledgement without a name is rejected")


if __name__ == "__main__":
    test_issue_key_is_stable_across_magnitude_drift()
    test_ack_records_for_present_issue()
    test_ack_rejected_when_issue_absent()
    test_ack_requires_a_name()
    print("\nAll per-issue acknowledgement tests passed.")
