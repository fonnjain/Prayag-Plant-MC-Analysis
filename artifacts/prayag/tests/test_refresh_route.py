"""Route-level regression for the manual "Refresh data" action, NO network.

/refresh must (1) drop the in-process sheet caches so the next page load
re-reads the live sheets, and (2) redirect back only to an INTERNAL relative
path — an attacker-supplied absolute/scheme-relative URL must never be honoured
(open-redirect guard).

`clear_caches` is stubbed so the test is deterministic and offline.

Run: cd artifacts/prayag && python3 -m tests.test_refresh_route
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod


def _client():
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_refresh_clears_caches_and_redirects_internal():
    calls = {"n": 0}
    appmod.clear_caches = lambda: calls.__setitem__("n", calls["n"] + 1)
    c = _client()

    resp = c.get("/refresh?next=%2F%3Fperiod%3Dlast_month")
    assert resp.status_code == 302, resp.status_code
    assert calls["n"] == 1, "clear_caches must be invoked exactly once"
    loc = resp.headers["Location"]
    assert loc.endswith("/?period=last_month"), loc
    print("PASS: /refresh clears caches and redirects to the internal next path")


def test_refresh_blocks_open_redirect():
    appmod.clear_caches = lambda: None
    c = _client()
    for bad in [
        "https://evil.com",
        "//evil.com",
        "/\\evil.com",
        "http://evil.com/x",
        "javascript:alert(1)",
    ]:
        resp = c.get("/refresh", query_string={"next": bad})
        assert resp.status_code == 302, (bad, resp.status_code)
        loc = resp.headers["Location"]
        # Must collapse to the safe internal root, never the attacker's origin.
        assert "evil.com" not in loc and "javascript" not in loc, (bad, loc)
        assert loc.endswith("/"), (bad, loc)
    print("PASS: /refresh refuses external/scheme-relative redirect targets")


def test_refresh_default_is_root():
    appmod.clear_caches = lambda: None
    c = _client()
    resp = c.get("/refresh")
    assert resp.status_code == 302, resp.status_code
    assert resp.headers["Location"].endswith("/"), resp.headers["Location"]
    print("PASS: /refresh with no next defaults to the overview")


if __name__ == "__main__":
    test_refresh_clears_caches_and_redirects_internal()
    test_refresh_blocks_open_redirect()
    test_refresh_default_is_root()
    print("\nAll refresh-route regression tests passed.")
