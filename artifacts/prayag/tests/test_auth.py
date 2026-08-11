"""
Tests for the Phase-3 authentication gate.

Run: cd artifacts/prayag && python3 -m pytest tests/test_auth.py -v

All tests are offline (no Google Sheets, no Postgres).  The before_request
gate and login/logout routes are exercised through Flask's test client.

Test matrix (per spec):
  1. Gate inactive when PRAYAG_APP_PASSWORD is unset
  2. Gate active when PRAYAG_APP_PASSWORD is set
  3. Exempt paths (/login, /logout, /health, /static/) reachable unauthenticated
  4. POST to a mutating route is blocked (redirected) pre-auth
  5. Correct password sets session["auth_user"]
  6. Wrong password does NOT set session["auth_user"]
  7. /logout clears the session and re-gates
  8. _verify_credentials is the only place the password is compared
"""
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(password: "str | None"):
    """Return a Flask test client with PRAYAG_APP_PASSWORD set/unset."""
    import auth as auth_mod
    import app as appmod

    # Patch app_password for this test run
    orig = auth_mod.app_password
    auth_mod.app_password = lambda: password
    appmod.app.config["TESTING"] = True
    client = appmod.app.test_client()
    # Restore after yield — caller is responsible for cleanup (see fixtures below)
    return client, auth_mod, orig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_no_pw():
    """Test client with gate INACTIVE (no password set)."""
    import auth as auth_mod
    import app as appmod

    orig = auth_mod.app_password
    auth_mod.app_password = lambda: None
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c
    auth_mod.app_password = orig


@pytest.fixture()
def client_with_pw():
    """Test client with gate ACTIVE (password = 'secret123')."""
    import auth as auth_mod
    import app as appmod

    orig = auth_mod.app_password
    auth_mod.app_password = lambda: "secret123"
    appmod.app.config["TESTING"] = True
    with appmod.app.test_client() as c:
        yield c
    auth_mod.app_password = orig


# ---------------------------------------------------------------------------
# 1. Gate inactive when secret unset
# ---------------------------------------------------------------------------

def test_gate_inactive_when_no_password(client_no_pw):
    """With no password set, GET '/' must return 200 (not a redirect)."""
    resp = client_no_pw.get("/")
    assert resp.status_code == 200, (
        f"Gate must be inactive when PRAYAG_APP_PASSWORD is unset; got {resp.status_code}"
    )
    print("PASS: gate inactive — GET '/' returns 200 without password configured")


# ---------------------------------------------------------------------------
# 2. Gate active when secret is set
# ---------------------------------------------------------------------------

def test_gate_active_redirects_unauthenticated(client_with_pw):
    """With password set, unauthenticated GET '/' must redirect to /login."""
    resp = client_with_pw.get("/")
    assert resp.status_code in (301, 302), (
        f"Gate must redirect unauthenticated requests; got {resp.status_code}"
    )
    assert "/login" in resp.headers.get("Location", ""), (
        f"Redirect must point to /login; Location={resp.headers.get('Location')}"
    )
    print("PASS: gate active — unauthenticated GET '/' redirects to /login")


# ---------------------------------------------------------------------------
# 3. Exempt paths reachable while unauthenticated
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/login", "/health"])
def test_exempt_paths_reachable_unauthenticated(client_with_pw, path):
    """Exempt paths must not redirect to /login even when gate is active."""
    resp = client_with_pw.get(path)
    assert resp.status_code != 302 or "/login" not in resp.headers.get("Location", ""), (
        f"Exempt path {path!r} must not be redirected to /login"
    )
    assert resp.status_code < 500, f"Exempt path {path!r} must not 500"
    print(f"PASS: exempt path {path!r} reachable unauthenticated (status={resp.status_code})")


def test_logout_exempt_unauthenticated(client_with_pw):
    """/logout must be reachable (clears nothing, redirects to /login)."""
    resp = client_with_pw.get("/logout")
    # /logout redirects to /login — that's fine, it must not 403/500
    assert resp.status_code in (301, 302)
    print("PASS: /logout reachable unauthenticated")


# ---------------------------------------------------------------------------
# 4. POST to a mutating route is blocked pre-auth
# ---------------------------------------------------------------------------

def test_mutating_post_blocked_pre_auth(client_with_pw):
    """An unauthenticated POST to a mutating route must redirect, not execute."""
    # /confirmation/approve is a POST-only route that writes to Postgres.
    # We verify it redirects rather than executing.
    resp = client_with_pw.post("/confirmation/approve", data={})
    assert resp.status_code in (301, 302), (
        f"Unauthenticated POST must be blocked (redirect); got {resp.status_code}"
    )
    assert "/login" in resp.headers.get("Location", ""), (
        f"Blocked POST must redirect to /login; Location={resp.headers.get('Location')}"
    )
    print("PASS: unauthenticated POST to mutating route is redirected, not executed")


# ---------------------------------------------------------------------------
# 5. Correct password sets session["auth_user"]
# ---------------------------------------------------------------------------

def test_correct_password_sets_session(client_with_pw):
    """POST /login with the correct password must set session['auth_user']."""
    resp = client_with_pw.post(
        "/login",
        data={"username": "anyone", "password": "secret123"},
        follow_redirects=False,
    )
    assert resp.status_code in (301, 302), (
        f"Successful login must redirect; got {resp.status_code}"
    )
    assert resp.headers.get("Location", "").endswith("/") or \
           "login" not in resp.headers.get("Location", ""), \
           f"Successful login must redirect to home, not login; Location={resp.headers.get('Location')}"
    # After login, GET '/' must succeed (session cookie set by test client)
    resp2 = client_with_pw.get("/")
    assert resp2.status_code == 200, (
        f"After login, GET '/' must return 200; got {resp2.status_code}"
    )
    print("PASS: correct password sets session and allows access")


# ---------------------------------------------------------------------------
# 6. Wrong password does NOT set session["auth_user"]
# ---------------------------------------------------------------------------

def test_wrong_password_does_not_authenticate(client_with_pw):
    """POST /login with wrong password must return 401 and NOT grant access."""
    resp = client_with_pw.post(
        "/login",
        data={"username": "anyone", "password": "wrongpassword"},
        follow_redirects=False,
    )
    assert resp.status_code == 401, (
        f"Wrong password must return 401; got {resp.status_code}"
    )
    # Without a session, GET '/' must still redirect
    resp2 = client_with_pw.get("/")
    assert resp2.status_code in (301, 302), (
        f"After failed login, GET '/' must still redirect; got {resp2.status_code}"
    )
    print("PASS: wrong password returns 401 and does not grant access")


# ---------------------------------------------------------------------------
# 7. /logout clears the session and re-gates
# ---------------------------------------------------------------------------

def test_logout_clears_session_and_regates(client_with_pw):
    """After logout, GET '/' must redirect to /login again."""
    # Log in first
    client_with_pw.post(
        "/login",
        data={"username": "anyone", "password": "secret123"},
        follow_redirects=False,
    )
    # Confirm access
    assert client_with_pw.get("/").status_code == 200

    # Log out
    resp = client_with_pw.get("/logout")
    assert resp.status_code in (301, 302)

    # Gate must be active again
    resp2 = client_with_pw.get("/")
    assert resp2.status_code in (301, 302) and "/login" in resp2.headers.get("Location", ""), (
        "After logout, GET '/' must redirect to /login"
    )
    print("PASS: logout clears session — gate active again")


# ---------------------------------------------------------------------------
# 8. _verify_credentials is the ONLY place the password is compared
# ---------------------------------------------------------------------------

def test_verify_credentials_is_only_comparison_point():
    """Confirm no other code calls hmac.compare_digest or reads PRAYAG_APP_PASSWORD."""
    import inspect
    import auth as auth_mod

    # The function must exist with the right signature
    sig = inspect.signature(auth_mod._verify_credentials)
    params = list(sig.parameters)
    assert "username" in params and "password" in params, (
        "_verify_credentials must accept username and password"
    )

    # The source of auth.py must contain exactly ONE call to hmac.compare_digest
    src = inspect.getsource(auth_mod)
    count = src.count("compare_digest")
    assert count == 1, (
        f"hmac.compare_digest must appear exactly once in auth.py; found {count}"
    )

    # No other module-level file in the app should read PRAYAG_APP_PASSWORD
    # except through auth.app_password()
    import app as appmod
    app_src = inspect.getsource(appmod)
    # Strip comments for the check
    non_comment_lines = [
        ln for ln in app_src.splitlines()
        if not ln.strip().startswith("#")
    ]
    direct_reads = [
        ln for ln in non_comment_lines
        if "PRAYAG_APP_PASSWORD" in ln
    ]
    assert len(direct_reads) == 0, (
        f"app.py must not read PRAYAG_APP_PASSWORD directly; found: {direct_reads}"
    )

    print("PASS: _verify_credentials is the only place the password is compared")


# ---------------------------------------------------------------------------
# 9. Bonus: gate inactive — banner context var is correct
# ---------------------------------------------------------------------------

def test_auth_configured_false_when_no_password(client_no_pw):
    """When gate inactive, the home page must be accessible (banner visible)."""
    resp = client_no_pw.get("/")
    assert resp.status_code == 200
    # The 'Access control not configured' banner should be rendered
    body = resp.data.decode("utf-8", errors="replace")
    assert "Access control not configured" in body, (
        "When PRAYAG_APP_PASSWORD is unset, base.html must show the warning banner"
    )
    print("PASS: banner 'Access control not configured' shown when gate inactive")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # pytest fixtures don't work when running directly — use pytest
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    sys.exit(result.returncode)
