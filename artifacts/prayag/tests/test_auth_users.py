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


def test_initial_user_seed_requires_a_password_change(monkeypatch):
    """Initial configured admins are newly created accounts, not an exception."""
    import store

    statements = []

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=None):
            statements.append((statement, params))

        def fetchone(self):
            return None

    class FakeConnection:
        def cursor(self, **_kwargs):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(store, "AVAILABLE", True)
    monkeypatch.setattr(store, "_conn", lambda: FakeConnection())
    store.seed_initial_users(
        [{"email": "seeded@prayagindia.com", "password_hash": "hash", "role": "admin"}],
        seed_key="test-password-change-seed",
    )

    user_insert = next(sql for sql, _params in statements if "INSERT INTO app_users" in sql)
    assert "must_change_password" in user_insert
    assert "TRUE, TRUE" in user_insert
    assert "must_change_password = TRUE" in user_insert


def test_password_change_policy_migrates_legacy_active_accounts(monkeypatch):
    """An existing bootstrap marker cannot cause the one-time policy upgrade to skip."""
    import store

    statements = []

    class FakeCursor:
        def __init__(self):
            self.fetch_results = iter([{"seed_key": "password-change-policy-v1"}])

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement, params=None):
            statements.append((statement, params))

        def fetchone(self):
            return next(self.fetch_results, None)

    class FakeConnection:
        def cursor(self, **_kwargs):
            return FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(store, "AVAILABLE", True)
    monkeypatch.setattr(store, "_conn", lambda: FakeConnection())
    store._init_user_tables()

    migration_update = next(
        sql for sql, _params in statements
        if "SET must_change_password=TRUE" in sql
    )
    assert "WHERE is_active=TRUE AND must_change_password=FALSE" in migration_update
    assert "password_hash" not in migration_update