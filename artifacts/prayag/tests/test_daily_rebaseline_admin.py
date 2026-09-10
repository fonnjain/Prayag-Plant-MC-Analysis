"""Admin-only, explicit-confirm coverage for production-safe re-baselining."""
import auth
import app as appmod


def _client(monkeypatch):
    monkeypatch.setattr(auth, "app_password", lambda: None)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(appmod.store, "AVAILABLE", True)
    monkeypatch.setattr(appmod.store, "daily_rebaseline_audit_history", lambda: [])
    monkeypatch.setattr(
        appmod.store,
        "get_user_by_id",
        lambda _id: {
            "id": 7, "email": "manager@prayagindia.com", "role": "admin",
            "is_active": True,
        },
    )
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _session(client, role="admin"):
    with client.session_transaction() as sess:
        sess["auth_user_id"] = 7
        sess["auth_user"] = "manager@prayagindia.com"
        sess["auth_role"] = role
        sess["auth_csrf"] = "rebaseline-csrf"


def test_rebaseline_page_is_admin_only(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        appmod.store,
        "get_user_by_id",
        lambda _id: {
            "id": 7, "email": "manager@prayagindia.com", "role": "normal",
            "is_active": True,
        },
    )
    _session(client, role="normal")
    assert client.get("/settings/daily-rebaseline").status_code == 403
    assert client.post("/settings/daily-rebaseline/preview").status_code == 403
    assert client.post("/settings/daily-rebaseline/confirm").status_code == 403


def test_preview_shows_all_values_and_does_not_write(monkeypatch):
    client = _client(monkeypatch)
    _session(client)
    writes = []
    monkeypatch.setattr(
        appmod,
        "inspect_daily_logical_population",
        lambda emitter, ym: {
            "emit": emitter, "month": ym, "physical_source": "PIPE",
            "stored_count": 71, "live_count": 70,
        },
    )
    monkeypatch.setattr(
        appmod.store,
        "create_daily_rebaseline_confirmation",
        lambda **kwargs: "one-time-confirmation",
    )
    monkeypatch.setattr(
        appmod, "rebaseline_daily_logical_population",
        lambda *args: writes.append(args),
    )
    response = client.post(
        "/settings/daily-rebaseline/preview",
        data={
            "csrf_token": "rebaseline-csrf", "emitter": "PIPE",
            "month": "2026-06", "new_count": "70",
        },
    )
    assert response.status_code == 200
    for value in (b"PIPE", b"2026-06", b"Stored value", b"71",
                  b"Live parsed count", b"Requested new value"):
        assert value in response.data
    assert writes == []


def test_confirm_requires_matching_preview_and_calls_guard_unchanged(monkeypatch):
    client = _client(monkeypatch)
    _session(client)
    pending = {
            "emitter": "PIPE", "month": "2026-06", "stored_count": 71,
            "live_count": 70, "new_count": 70,
    }
    monkeypatch.setattr(
        appmod.store, "consume_daily_rebaseline_confirmation",
        lambda nonce, user_id: pending if nonce == "one-time-confirmation" else None,
    )
    class _Lock:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    monkeypatch.setattr(appmod.store, "daily_rebaseline_lock", lambda *a: _Lock())
    monkeypatch.setattr(appmod.store, "daily_read_count", lambda *a: 71)
    audits = []
    monkeypatch.setattr(
        appmod.store, "start_daily_rebaseline_audit",
        lambda **kwargs: audits.append(("start", kwargs)) or 19,
    )
    monkeypatch.setattr(
        appmod.store, "finish_daily_rebaseline_audit",
        lambda audit_id, **kwargs: audits.append(("finish", audit_id, kwargs)),
    )
    calls = []
    monkeypatch.setattr(
        appmod, "rebaseline_daily_logical_population",
        lambda *args: calls.append(args) or {
            "emit": "PIPE", "ym": "2026-06",
            "previous_count": 71, "record_count": 70,
        },
    )
    response = client.post(
        "/settings/daily-rebaseline/confirm",
        data={
            "csrf_token": "rebaseline-csrf", "confirm": "yes",
            "nonce": "one-time-confirmation",
            "emitter": "PIPE", "month": "2026-06", "new_count": "70",
        },
    )
    assert response.status_code == 200
    assert calls == [("PIPE", "2026-06", 70)]
    assert audits[0][0] == "start"
    assert audits[0][1]["old_count"] == 71
    assert audits[-1] == ("finish", 19, {"status": "succeeded"})


def test_confirm_refusal_is_audited_and_reported(monkeypatch):
    client = _client(monkeypatch)
    _session(client)
    pending = {
            "emitter": "PIPE", "month": "2026-07", "stored_count": 177,
            "live_count": 176, "new_count": 176,
    }
    monkeypatch.setattr(
        appmod.store, "consume_daily_rebaseline_confirmation",
        lambda nonce, user_id: pending,
    )
    class _Lock:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    monkeypatch.setattr(appmod.store, "daily_rebaseline_lock", lambda *a: _Lock())
    monkeypatch.setattr(appmod.store, "daily_read_count", lambda *a: 177)
    monkeypatch.setattr(
        appmod.store, "start_daily_rebaseline_audit", lambda **kwargs: 20,
    )
    outcomes = []
    monkeypatch.setattr(
        appmod.store, "finish_daily_rebaseline_audit",
        lambda audit_id, **kwargs: outcomes.append((audit_id, kwargs)),
    )
    def refuse(*_args):
        raise appmod.SheetReadError(
            "Refusing to re-baseline PIPE 2026-07: live parse returned 175 "
            "records, expected 176."
        )
    monkeypatch.setattr(appmod, "rebaseline_daily_logical_population", refuse)
    response = client.post(
        "/settings/daily-rebaseline/confirm",
        data={
            "csrf_token": "rebaseline-csrf", "confirm": "yes",
            "nonce": "one-time-confirmation",
            "emitter": "PIPE", "month": "2026-07", "new_count": "176",
        },
    )
    assert response.status_code == 409
    assert b"live parse returned 175 records, expected 176" in response.data
    assert outcomes == [(20, {
        "status": "refused",
        "error": (
            "Refusing to re-baseline PIPE 2026-07: live parse returned 175 "
            "records, expected 176."
        ),
    })]