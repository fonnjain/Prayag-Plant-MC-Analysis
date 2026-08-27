"""Focused coverage for privacy-safe activity audit and password controls."""
from __future__ import annotations

import datetime

import auth
import app as appmod


def _seed_session(client, *, user_id=7, role="admin", force_password_change=False):
    with client.session_transaction() as sess:
        sess["auth_user_id"] = user_id
        sess["auth_user"] = "manager@prayagindia.com"
        sess["auth_role"] = role
        sess["auth_must_change_password"] = force_password_change
        sess["auth_csrf"] = "audit-test-csrf"
        sess["audit_session_id"] = "browser-session-7"


def _report():
    return {
        "from_day": "2026-08-01",
        "to_day": "2026-08-07",
        "summary": {
            "users": 1, "sessions": 2, "active_seconds": 7200,
            "idle_seconds": 900,
        },
        "rows": [{
            "day": "2026-08-04", "user_id": 7,
            "user_email": "manager@prayagindia.com",
            "active_seconds": 7200, "idle_seconds": 900, "session_count": 2,
            "pages": ["Dashboard home"], "actions": ["Save Params"],
        }],
        "events": [{
            "day": "2026-08-04", "when": "04-08-2026 10:20",
            "user_email": "manager@prayagindia.com",
            "type": "action", "label": "Save Params",
        }],
    }


def _make_client(monkeypatch):
    monkeypatch.setattr(auth, "app_password", lambda: None)
    monkeypatch.setattr(appmod.store, "AVAILABLE", True)
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def test_forced_password_change_blocks_dashboard_until_saved(monkeypatch):
    client = _make_client(monkeypatch)
    _seed_session(client, force_password_change=True)
    monkeypatch.setattr(auth, "app_password", lambda: "configured-password")
    monkeypatch.setattr(
        appmod.store,
        "get_user_by_id",
        lambda _id: {
            "id": 7, "email": "manager@prayagindia.com", "role": "admin",
            "is_active": True, "must_change_password": True,
        },
    )
    monkeypatch.setattr(appmod.store, "set_user_password", lambda *a, **kw: True)
    recorded = []
    monkeypatch.setattr(
        appmod.store, "record_user_activity_event",
        lambda **kwargs: recorded.append(kwargs),
    )

    assert client.get("/", follow_redirects=False).headers["Location"].endswith(
        "/change-password"
    )
    assert client.get("/change-password").status_code == 200
    response = client.post(
        "/change-password",
        data={
            "csrf_token": "audit-test-csrf",
            "password": "new-password",
            "confirm_password": "new-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess["auth_must_change_password"] is False
    assert recorded[-1]["label"] == "Changed first-login password"


def test_seeded_admin_login_is_sent_to_password_change(monkeypatch):
    """The initial configured password is a bootstrap credential, not a final one."""
    password_hash = auth.hash_password("bootstrap-password")
    monkeypatch.setattr(auth, "app_password", lambda: "bootstrap-password")
    monkeypatch.setattr(appmod.store, "AVAILABLE", True)
    monkeypatch.setattr(auth, "_seed_initial_admins", lambda: None)
    monkeypatch.setattr(
        appmod.store,
        "get_user_auth",
        lambda _email: {
            "id": 41, "email": "seeded@prayagindia.com",
            "password_hash": password_hash, "role": "admin", "is_active": True,
            "must_change_password": True,
        },
    )
    monkeypatch.setattr(
        appmod.store,
        "get_user_by_id",
        lambda _id: {
            "id": 41, "email": "seeded@prayagindia.com", "role": "admin",
            "is_active": True, "must_change_password": True,
        },
    )
    monkeypatch.setattr(appmod.store, "start_user_activity_session", lambda *a, **kw: None)
    appmod.app.config["TESTING"] = True

    with appmod.app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "seeded@prayagindia.com", "password": "bootstrap-password"},
            follow_redirects=False,
        )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/change-password")


def test_forced_password_change_allows_only_safe_heartbeat(monkeypatch):
    client = _make_client(monkeypatch)
    _seed_session(client, force_password_change=True)
    monkeypatch.setattr(auth, "app_password", lambda: "configured-password")
    monkeypatch.setattr(
        appmod.store,
        "get_user_by_id",
        lambda _id: {
            "id": 7, "email": "manager@prayagindia.com", "role": "admin",
            "is_active": True, "must_change_password": True,
        },
    )
    calls = []
    monkeypatch.setattr(
        appmod.store,
        "record_user_activity_heartbeat",
        lambda session_id, state: calls.append((session_id, state)) or True,
    )

    response = client.post(
        "/activity/heartbeat", json={"state": "active"},
        headers={"X-CSRF-Token": "audit-test-csrf"},
    )
    assert response.status_code == 204
    assert calls == [("browser-session-7", "active")]


def test_admin_reset_requires_password_change_at_next_login(monkeypatch):
    client = _make_client(monkeypatch)
    _seed_session(client)
    captured = {}
    monkeypatch.setattr(
        appmod.store,
        "set_user_password",
        lambda user_id, password_hash, **kwargs: captured.update(
            user_id=user_id, require_change=kwargs["must_change_password"]
        ) or True,
    )

    response = client.post(
        "/settings/users/19/password",
        data={"csrf_token": "audit-test-csrf", "password": "reset-password"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert captured == {"user_id": 19, "require_change": True}
    assert "message=" in response.headers["Location"]


def test_audit_page_and_pdf_are_admin_only_and_filtered(monkeypatch):
    client = _make_client(monkeypatch)
    monkeypatch.setattr(appmod.store, "user_activity_report", lambda *a, **kw: _report())
    monkeypatch.setattr(
        appmod.store,
        "list_users",
        lambda: [{"id": 7, "email": "manager@prayagindia.com"}],
    )
    _seed_session(client, role="normal")
    assert client.get("/settings/users/audit").status_code == 403
    assert client.get("/settings/users/audit.pdf").status_code == 403

    _seed_session(client, role="admin")
    page = client.get(
        "/settings/users/audit?from_date=2026-08-01&to_date=2026-08-07&user_id=7"
    )
    assert page.status_code == 200
    assert b"User Activity Audit" in page.data
    assert b"Dashboard home" in page.data
    assert b"manager@prayagindia.com" in page.data
    pdf = client.get(
        "/settings/users/audit.pdf?from_date=2026-08-01&to_date=2026-08-07&user_id=7"
    )
    assert pdf.status_code == 200
    assert pdf.headers["Content-Type"].startswith("application/pdf")
    assert pdf.data.startswith(b"%PDF")


def test_audit_filter_rejects_untrusted_query_text(monkeypatch):
    client = _make_client(monkeypatch)
    _seed_session(client)
    monkeypatch.setattr(appmod.store, "user_activity_report", lambda *a, **kw: _report())
    monkeypatch.setattr(appmod.store, "list_users", lambda: [])

    assert client.get("/settings/users/audit?user_id=7%20OR%201=1").status_code == 400
    assert client.get("/settings/users/audit?from_date=not-a-date").status_code == 400


def test_heartbeat_only_accepts_state_and_never_includes_request_content(monkeypatch):
    client = _make_client(monkeypatch)
    _seed_session(client)
    captured = []
    monkeypatch.setattr(
        appmod.store, "record_user_activity_heartbeat",
        lambda session_id, state: captured.append((session_id, state)) or True,
    )
    response = client.post(
        "/activity/heartbeat",
        json={"state": "active", "anything_else": "private form text"},
        headers={"X-CSRF-Token": "audit-test-csrf"},
    )
    assert response.status_code == 204
    assert captured == [("browser-session-7", "active")]
    assert client.post(
        "/activity/heartbeat", json={"state": "watching-screen"},
        headers={"X-CSRF-Token": "audit-test-csrf"},
    ).status_code == 400


def test_activity_duration_is_human_readable():
    assert appmod._activity_duration(0) == "0m"
    assert appmod._activity_duration(3_660) == "1h 1m"
    assert appmod._activity_filter_dates  # route helper remains available
    assert datetime.date.fromisoformat("2026-08-01")