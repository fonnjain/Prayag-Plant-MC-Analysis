"""Route-level tests for surfacing reports skipped from the "Download all (ZIP)".

A partial ZIP (one or more reports failed to build) is still served in full, but
the download route stashes the skipped report ids in a short-lived cookie so the
next /management-reports page load can name exactly what was left out. Guards:
  * a partial bundle sets the ``mr_zip_skipped`` cookie (ym + skipped ids),
  * a clean bundle clears any stale cookie,
  * the index page turns that cookie into a human-readable warning (report
    labels, correct month) and then clears the cookie (one-shot notice),
  * a cookie for a DIFFERENT month is ignored (no false warning).

Run: cd artifacts/prayag && python3 -m tests.test_management_zip_skipped
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from reports import registry as rreg


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


class _FakeBundle:
    def __init__(self, built, total, skipped):
        self.data = b"PK\x03\x04fake-zip"
        self.built = built
        self.total = total
        self.skipped = skipped


def test_partial_zip_sets_skipped_cookie(monkeypatch):
    monkeypatch.setattr(rreg, "zip_bundle",
                        lambda ym, plant=None: _FakeBundle(11, 13, ["pipe", "hdpe"]))
    monkeypatch.setattr("reports.period.resolve_month", lambda m: "2026-06")
    client = _client()
    resp = client.get("/management-reports/all.zip?month=2026-06")
    assert resp.status_code == 200
    # Werkzeug quotes the value on the wire because it contains commas (\054);
    # the browser/request layer unquotes it (see the index round-trip test). Just
    # assert the month and both skipped ids made it into the cookie.
    val = client.get_cookie("mr_zip_skipped").value
    assert "2026-06" in val and "pipe" in val and "hdpe" in val, val


def test_clean_zip_clears_cookie(monkeypatch):
    monkeypatch.setattr(rreg, "zip_bundle",
                        lambda ym, plant=None: _FakeBundle(13, 13, []))
    monkeypatch.setattr("reports.period.resolve_month", lambda m: "2026-06")
    client = _client()
    resp = client.get("/management-reports/all.zip?month=2026-06")
    assert resp.status_code == 200
    cookie = resp.headers.get("Set-Cookie", "")
    # cleared: emitted with an expiry in the past / Max-Age=0
    assert "mr_zip_skipped=;" in cookie or "Max-Age=0" in cookie, cookie


def test_index_surfaces_skipped_labels_and_clears(monkeypatch):
    monkeypatch.setattr("reports.period.resolve_month", lambda m: "2026-06")
    monkeypatch.setattr("reports.period.available_months", lambda: ["2026-06"])
    monkeypatch.setattr("reports.period.month_disp", lambda m: "June 2026")
    monkeypatch.setattr(rreg, "index_view", lambda ym: [])
    client = _client()
    client.set_cookie("mr_zip_skipped", "2026-06|pipe,hdpe")
    resp = client.get("/management-reports?month=2026-06")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "missing 2 reports" in body
    assert rreg.get("pipe").label in body
    assert rreg.get("hdpe").label in body
    # one-shot: the cookie is cleared after being read
    assert "mr_zip_skipped=;" in resp.headers.get("Set-Cookie", "") \
        or "Max-Age=0" in resp.headers.get("Set-Cookie", "")


def test_index_ignores_cookie_for_other_month(monkeypatch):
    monkeypatch.setattr("reports.period.resolve_month", lambda m: "2026-06")
    monkeypatch.setattr("reports.period.available_months", lambda: ["2026-06"])
    monkeypatch.setattr("reports.period.month_disp", lambda m: "June 2026")
    monkeypatch.setattr(rreg, "index_view", lambda ym: [])
    client = _client()
    client.set_cookie("mr_zip_skipped", "2026-05|pipe")
    resp = client.get("/management-reports?month=2026-06")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "could not be built" not in body


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
