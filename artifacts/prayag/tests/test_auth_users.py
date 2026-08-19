"""Unit coverage for the database-backed account authentication path."""
from flask import Flask, session

import auth


def test_database_user_login_uses_email_password_and_role(monkeypatch):
    monkeypatch.setattr(auth, "app_password", lambda: "bootstrap-password")
    monkeypatch.setattr(auth.store, "AVAILABLE", True)
    monkeypatch.setattr(auth, "_seed_initial_admins", lambda: None)
    password_hash = auth.hash_password("correct-password")
    monkeypatch.setattr(
        auth.store,
        "get_user_auth",
        lambda email: {
            "id": 7,
            "email": email,
            "password_hash": password_hash,
            "role": "normal",
            "is_active": True,
        },
    )

    identity = auth._verify_credentials(" Person@PrayagIndia.com ", "correct-password")

    assert identity == {
        "id": 7,
        "email": "person@prayagindia.com",
        "role": "normal",
    }


def test_unknown_database_user_cannot_use_legacy_shared_password(monkeypatch):
    monkeypatch.setattr(auth, "app_password", lambda: "bootstrap-password")
    monkeypatch.setattr(auth.store, "AVAILABLE", True)
    monkeypatch.setattr(auth, "_seed_initial_admins", lambda: None)
    monkeypatch.setattr(auth.store, "get_user_auth", lambda email: None)

    assert auth._verify_credentials("unknown@prayagindia.com", "bootstrap-password") is None


def test_csrf_token_is_session_bound():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    with app.test_request_context("/"):
        token = auth.csrf_token()
        assert auth.valid_csrf(token)
        assert not auth.valid_csrf("not-the-token")
        assert session["auth_csrf"] == token