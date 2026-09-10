"""R-46 daily-production freeze: guards, operator flow, and read semantics."""
from contextlib import nullcontext

import pytest

import app as appmod
import auth
from metrics import Record
import sheets
import store


def _record(plant, machine, output):
    return Record(
        grain="daily",
        period="2026-06-01",
        date="2026-06-01",
        plant=plant,
        segment="Pipe" if plant == "PIPE" else "Moulding",
        machine=machine,
        unit="kg",
        total_count=output,
        reject_count=1,
        actual_hours=8,
        source_family=plant,
        source_file="file-june",
        source_tab="Report-5" if plant == "PIPE" else "Report-12",
    )


def _physical_results(pipe_output=100):
    return [
        ([_record("PIPE", "PIPE Pipe M/C-1", pipe_output)],
         {"emit": "PIPE", "ym": "2026-06", "record_count": 1}),
        ([_record("MOULDING", "MOULDING M/C-1", 50)],
         {"emit": "MOULDING", "ym": "2026-06", "record_count": 1}),
    ]


def _client(monkeypatch, *, role="admin"):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setattr(store, "AVAILABLE", True)
    monkeypatch.setattr(
        store,
        "get_user_by_id",
        lambda _id: {
            "id": 7,
            "email": "manager@prayagindia.com",
            "role": role,
            "is_active": True,
        },
    )
    monkeypatch.setattr(store, "daily_freeze_history", lambda: [])
    monkeypatch.setattr(store, "daily_freeze_audit_history", lambda: [])
    appmod.app.config["TESTING"] = True
    client = appmod.app.test_client()
    with client.session_transaction() as session:
        session["auth_user_id"] = 7
        session["auth_user"] = "manager@prayagindia.com"
        session["auth_role"] = role
        session["auth_csrf"] = "freeze-csrf"
    return client


def test_freeze_settings_requires_live_database_admin(monkeypatch):
    client = _client(monkeypatch, role="normal")
    assert client.get("/settings/daily-freeze").status_code == 403
    assert client.post("/settings/daily-freeze/preview").status_code == 403


def test_freeze_requires_secure_session_secret(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.delenv("SESSION_SECRET")
    assert client.get("/settings/daily-freeze").status_code == 503


def test_preview_is_uncached_read_only_and_shows_verification(monkeypatch):
    client = _client(monkeypatch)
    results = _physical_results()
    calls = []
    monkeypatch.setattr(sheets, "parse_daily_physical_uncached",
                        lambda physical, ym: calls.append((physical, ym)) or results)
    monkeypatch.setattr(store, "daily_read_count", lambda *args: 1)
    confirmations = []
    monkeypatch.setattr(
        store,
        "daily_freeze_confirmation_create",
        lambda **kwargs: confirmations.append(kwargs) or "freeze-once",
    )
    writes = []
    monkeypatch.setattr(store, "daily_freeze_write",
                        lambda *args, **kwargs: writes.append((args, kwargs)))

    response = client.post(
        "/settings/daily-freeze/preview",
        data={
            "csrf_token": "freeze-csrf",
            "emitter": "PIPE",
            "month": "2026-06",
        },
    )

    assert response.status_code == 200
    assert calls == [("PIPE", "2026-06")]
    assert writes == []
    assert confirmations[0]["action"] == "freeze"
    for text in (
        b"Logical records", b"Physical records", b"High-water",
        b"Date coverage", b"Machines", b"Run hours", b"100.0",
    ):
        assert text in response.data


def test_preview_refuses_current_month_before_read(monkeypatch):
    client = _client(monkeypatch)
    called = []
    monkeypatch.setattr(
        sheets, "parse_daily_physical_uncached",
        lambda *args: called.append(args),
    )
    response = client.post(
        "/settings/daily-freeze/preview",
        data={
            "csrf_token": "freeze-csrf",
            "emitter": "PIPE",
            "month": appmod._today().strftime("%Y-%m"),
        },
    )
    assert response.status_code == 400
    assert called == []


def test_confirm_second_parse_must_match_preview(monkeypatch):
    client = _client(monkeypatch)
    first = _physical_results()
    first_capture = appmod._daily_freeze_capture(first, "PIPE")
    source_id = appmod.DAILY_SOURCES["PIPE"]["files"]["2026-06"]
    payload = {
        key: value for key, value in first_capture.items()
        if key not in ("records", "reports")
    }
    payload.update({
        "physical": "PIPE",
        "source_id": source_id,
        "high_water": 1,
    })
    monkeypatch.setattr(
        store,
        "daily_freeze_confirmation_consume",
        lambda *args, **kwargs: {
            "emitter": "PIPE",
            "ym": "2026-06",
            "fingerprint": first_capture["physical_fingerprint"],
            "payload": payload,
            "action": "freeze",
        },
    )
    monkeypatch.setattr(
        sheets,
        "parse_daily_physical_uncached",
        lambda *args: _physical_results(pipe_output=101),
    )
    monkeypatch.setattr(store, "daily_read_count", lambda *args: 1)
    monkeypatch.setattr(
        store, "daily_freeze_lock",
        lambda *args: nullcontext((object(), object())),
    )
    writes = []
    monkeypatch.setattr(store, "daily_freeze_write",
                        lambda *args, **kwargs: writes.append((args, kwargs)))
    monkeypatch.setattr(store, "daily_freeze_audit", lambda **kwargs: 1)

    response = client.post(
        "/settings/daily-freeze/confirm",
        data={
            "csrf_token": "freeze-csrf",
            "confirm": "yes",
            "nonce": "freeze-once",
            "emitter": "PIPE",
            "month": "2026-06",
        },
    )
    assert response.status_code == 409
    assert b"Source changed between uncached parses" in response.data
    assert writes == []


def test_confirm_writes_only_selected_logical_emitter(monkeypatch):
    client = _client(monkeypatch)
    results = _physical_results()
    capture = appmod._daily_freeze_capture(results, "PIPE")
    source_id = appmod.DAILY_SOURCES["PIPE"]["files"]["2026-06"]
    payload = {
        key: value for key, value in capture.items()
        if key not in ("records", "reports")
    }
    payload.update({
        "physical": "PIPE",
        "source_id": source_id,
        "high_water": 1,
    })
    monkeypatch.setattr(
        store,
        "daily_freeze_confirmation_consume",
        lambda *args, **kwargs: {
            "emitter": "PIPE",
            "ym": "2026-06",
            "fingerprint": capture["physical_fingerprint"],
            "payload": payload,
            "action": "freeze",
        },
    )
    monkeypatch.setattr(sheets, "parse_daily_physical_uncached",
                        lambda *args: results)
    monkeypatch.setattr(store, "daily_read_count", lambda *args: 1)
    monkeypatch.setattr(
        store, "daily_freeze_lock",
        lambda *args: nullcontext((object(), object())),
    )
    writes = []
    monkeypatch.setattr(
        store,
        "daily_freeze_write",
        lambda *args, **kwargs: writes.append((args, kwargs))
        or {"version": 1},
    )

    response = client.post(
        "/settings/daily-freeze/confirm",
        data={
            "csrf_token": "freeze-csrf",
            "confirm": "yes",
            "nonce": "freeze-once",
            "emitter": "PIPE",
            "month": "2026-06",
        },
    )
    assert response.status_code == 302
    assert len(writes) == 1
    assert writes[0][0][0:2] == ("PIPE", "2026-06")
    assert [row.plant for row in writes[0][0][2]] == ["PIPE"]
    assert writes[0][1]["preview_count"] == 1
    assert writes[0][1]["live_count"] == 1


def test_uncached_capture_refuses_awaiting_and_highwater_failures(monkeypatch):
    monkeypatch.setattr(sheets, "_get_access_token", lambda: "token")
    monkeypatch.setattr(sheets, "_daily_key_lock", lambda key: nullcontext())
    monkeypatch.setattr(
        sheets,
        "_load_daily",
        lambda *args: [([], {
            "emit": "PIPE", "ym": "2026-06",
            "record_count": 0, "empty_source": True,
        })],
    )
    monkeypatch.setattr(sheets, "_daily_pair_incomplete_reason",
                        lambda *args: None)
    with pytest.raises(sheets.DailyReadIncompleteError, match="awaiting data"):
        sheets.parse_daily_physical_uncached("PIPE", "2026-06")

    monkeypatch.setattr(sheets, "_load_daily",
                        lambda *args: _physical_results())
    monkeypatch.setattr(
        sheets,
        "_daily_pair_incomplete_reason",
        lambda *args: "PIPE returned 1 record, below its prior complete population of 2",
    )
    with pytest.raises(sheets.DailyReadIncompleteError, match="below its prior"):
        sheets.parse_daily_physical_uncached("PIPE", "2026-06")

    monkeypatch.setattr(sheets, "_daily_pair_incomplete_reason",
                        lambda *args: None)
    monkeypatch.setattr(store, "daily_read_count", lambda *args: None)
    with pytest.raises(sheets.DailyReadIncompleteError, match="no established durable high-water"):
        sheets.parse_daily_physical_uncached("PIPE", "2026-06")


def test_fully_frozen_pair_never_calls_live_or_oauth(monkeypatch):
    monkeypatch.setattr(store, "daily_freeze_active", lambda emit, ym: True)
    monkeypatch.setattr(store, "daily_freeze_state_token",
                        lambda emit, ym: f"{emit}-frozen")
    payloads = {
        "PIPE": ([_record("PIPE", "FROZEN PIPE", 100)],
                 [{"emit": "PIPE", "frozen": True}]),
        "MOULDING": ([_record("MOULDING", "FROZEN MOULD", 50)],
                     [{"emit": "MOULDING", "frozen": True}]),
    }
    monkeypatch.setattr(store, "daily_freeze_read",
                        lambda emit, ym: payloads[emit])
    monkeypatch.setattr(sheets, "_get_access_token",
                        lambda: pytest.fail("OAuth must not be requested"))
    monkeypatch.setattr(sheets, "_load_daily",
                        lambda *args: pytest.fail("Sheets must not be read"))
    monkeypatch.setattr(sheets, "_daily_plants", lambda: ["PIPE"])
    rows, reports, _ = sheets.get_daily_records(
        ["2026-06"], source_plants=["PIPE"]
    )
    assert {row.machine for row in rows} == {"FROZEN PIPE", "FROZEN MOULD"}
    assert all(report["frozen"] for report in reports)


def test_mixed_pipe_freeze_keeps_moulding_live_without_caching_overlay(monkeypatch):
    sheets._daily_cache.clear()
    sheets._daily_cache_state_tokens.clear()
    monkeypatch.setattr(
        store, "daily_freeze_active",
        lambda emit, ym: emit == "PIPE",
    )
    monkeypatch.setattr(store, "daily_freeze_state_token",
                        lambda emit, ym: f"{emit}-state")
    monkeypatch.setattr(
        store,
        "daily_freeze_read",
        lambda emit, ym: (
            [_record("PIPE", "FROZEN PIPE", 100)],
            [{"emit": "PIPE", "frozen": True}],
        ),
    )
    monkeypatch.setattr(store, "pg_cache_read", lambda *args: None)
    monkeypatch.setattr(store, "pg_cache_write", lambda *args: None)
    live_calls = []
    def live_only(*args, **kwargs):
        live_calls.append(kwargs.get("emits"))
        assert kwargs.get("emits") == {"MOULDING"}
        return [_physical_results(pipe_output=999)[1]]
    monkeypatch.setattr(sheets, "_load_daily", live_only)
    monkeypatch.setattr(sheets, "_daily_pair_incomplete_reason",
                        lambda *args: None)
    monkeypatch.setattr(sheets, "_remember_complete_daily_pair",
                        lambda *args: None)
    results = sheets._load_daily_cached("PIPE", "2026-06", "token")
    assert results[0][0][0].machine == "FROZEN PIPE"
    assert results[1][0][0].machine == "MOULDING M/C-1"
    assert live_calls == [{"MOULDING"}]
    assert sheets._daily_cache[("PIPE", "2026-06")][1][0][0][0].machine == "MOULDING M/C-1"


def test_corrupt_frozen_snapshot_fails_closed(monkeypatch):
    monkeypatch.setattr(store, "daily_freeze_active", lambda emit, ym: True)
    monkeypatch.setattr(
        store,
        "daily_freeze_read",
        lambda emit, ym: (_ for _ in ()).throw(
            store.FrozenSnapshotError("DAILY_FREEZE_CORRUPT")
        ),
    )
    monkeypatch.setattr(sheets, "_load_daily",
                        lambda *args: pytest.fail("must not fall back"))
    with pytest.raises(store.FrozenSnapshotError, match="CORRUPT"):
        sheets._load_daily_cached("PIPE", "2026-06", None)


def test_missing_durable_store_cannot_silently_fall_back_live(monkeypatch):
    monkeypatch.setattr(store, "AVAILABLE", False)
    with pytest.raises(store.FrozenSnapshotError, match="STATE_UNAVAILABLE"):
        store.daily_freeze_active("PIPE", "2026-06")
    with pytest.raises(store.FrozenSnapshotError, match="STATE_UNAVAILABLE"):
        store.daily_freeze_read("PIPE", "2026-06")


def test_unfreeze_requires_single_use_matching_confirmation(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        store,
        "daily_freeze_active_snapshot",
        lambda *args: {
            "id": 55,
            "version": 3,
            "fingerprint": "snapshot-fingerprint",
            "physical_key": "PIPE",
            "source_file_id": "file-june",
            "record_count": 70,
        },
    )
    monkeypatch.setattr(
        store,
        "daily_freeze_confirmation_create",
        lambda **kwargs: "unfreeze-once",
    )
    preview = client.post(
        "/settings/daily-freeze/unfreeze/preview",
        data={
            "csrf_token": "freeze-csrf",
            "emitter": "PIPE",
            "month": "2026-06",
        },
    )
    assert preview.status_code == 200
    assert b"Confirm unfreeze" in preview.data

    consumed = {"used": False}
    def consume(*args, **kwargs):
        if consumed["used"]:
            return None
        consumed["used"] = True
        return {
            "emitter": "PIPE", "ym": "2026-06",
            "fingerprint": "snapshot-fingerprint",
            "payload": {
                "action": "unfreeze",
                "snapshot_id": 55,
                "version": 3,
                "physical_key": "PIPE",
            },
            "action": "unfreeze",
        }
    monkeypatch.setattr(store, "daily_freeze_confirmation_consume", consume)
    unfreezes = []
    monkeypatch.setattr(store, "daily_freeze_unfreeze",
                        lambda *args, **kwargs: unfreezes.append((args, kwargs)))
    monkeypatch.setattr(appmod, "clear_caches", lambda: None)
    form = {
        "csrf_token": "freeze-csrf",
        "confirm": "yes",
        "nonce": "unfreeze-once",
        "emitter": "PIPE",
        "month": "2026-06",
    }
    assert client.post(
        "/settings/daily-freeze/unfreeze/confirm", data=form
    ).status_code == 302
    assert client.post(
        "/settings/daily-freeze/unfreeze/confirm", data=form
    ).status_code == 409
    assert unfreezes[0][0] == ("PIPE", "2026-06")
    assert unfreezes[0][1]["expected_snapshot_id"] == 55
    assert unfreezes[0][1]["expected_version"] == 3
    assert unfreezes[0][1]["expected_fingerprint"] == "snapshot-fingerprint"


def test_direct_unfreeze_bypass_does_not_exist(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/settings/daily-freeze/unfreeze",
        data={
            "csrf_token": "freeze-csrf",
            "confirm": "yes",
            "emitter": "PIPE",
            "month": "2026-06",
        },
    )
    assert response.status_code == 404


@pytest.mark.skipif(not store.AVAILABLE, reason="development Postgres unavailable")
def test_real_store_round_trip_integrity_nonce_and_audit():
    """Exercise the actual R-46 tables and transaction path, then clean up."""
    emitter = "TEST_R46_FREEZE"
    ym = "2000-01"
    store._init_daily_freezes()

    def cleanup():
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {store._FREEZE_CONFIRM} WHERE emitter=%s AND ym=%s",
                (emitter, ym),
            )
            cur.execute(
                f"DELETE FROM {store._FREEZE_AUDIT} WHERE emitter=%s AND ym=%s",
                (emitter, ym),
            )
            cur.execute(
                f"DELETE FROM {store._FREEZE_RECORDS} WHERE snapshot_id IN "
                f"(SELECT id FROM {store._FREEZE_HEADER} WHERE emitter=%s AND ym=%s)",
                (emitter, ym),
            )
            cur.execute(
                f"DELETE FROM {store._FREEZE_HEADER} WHERE emitter=%s AND ym=%s",
                (emitter, ym),
            )

    cleanup()
    try:
        nonce = store.daily_freeze_confirmation_create(
            user_id=9000001,
            emitter=emitter,
            ym=ym,
            fingerprint="preview-fingerprint",
            payload={"physical": "TEST_PHYSICAL"},
            action="freeze",
            ttl=60,
        )
        pending = store.daily_freeze_confirmation_consume(
            nonce, user_id=9000001
        )
        assert pending["action"] == "freeze"
        assert store.daily_freeze_confirmation_consume(
            nonce, user_id=9000001
        ) is None

        result = store.daily_freeze_write(
            emitter,
            ym,
            [_record(emitter, "TEST MACHINE", 12)],
            [{"emit": emitter, "ym": ym, "record_count": 1}],
            user_id=9000001,
            user_email="r46-test@example.invalid",
            physical_key="TEST_PHYSICAL",
            source_file_id="TEST_FILE",
            preview_count=1,
            live_count=1,
            high_water=1,
            verification={"logical_count": 1},
        )
        assert result["version"] == 1
        assert store.daily_freeze_active(emitter, ym) is True
        records, reports = store.daily_freeze_read(emitter, ym)
        assert len(records) == 1
        assert reports[0]["frozen"] is True
        assert reports[0]["freeze_version"] == 1
        assert reports[0]["freeze_fingerprint"] == result["fingerprint"]

        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {store._FREEZE_HEADER} SET integrity_checksum='corrupt' "
                "WHERE id=%s",
                (result["snapshot_id"],),
            )
        with pytest.raises(store.FrozenSnapshotError, match="checksum mismatch"):
            store.daily_freeze_read(emitter, ym)
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"UPDATE {store._FREEZE_HEADER} SET integrity_checksum=fingerprint "
                "WHERE id=%s",
                (result["snapshot_id"],),
            )

        store.daily_freeze_unfreeze(
            emitter,
            ym,
            user_id=9000001,
            user_email="r46-test@example.invalid",
        )
        assert store.daily_freeze_active(emitter, ym) is False
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT action FROM {store._FREEZE_AUDIT} "
                "WHERE emitter=%s AND ym=%s ORDER BY id",
                (emitter, ym),
            )
            assert [row[0] for row in cur.fetchall()] == ["freeze", "unfreeze"]
    finally:
        cleanup()
