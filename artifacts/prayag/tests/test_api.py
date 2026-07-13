"""Offline regression for the read-only JSON API (/data-api/v1), NO network.

Locks the API's contract to the app's core invariants:
  * closed by default — 503 when PRAYAG_API_KEY is unset, 401 on a bad key;
  * no fake 0% — a ratio without a real baseline serializes as null, never 0;
  * confirmation gating is explicit — figures_gated=true for an unreleased
    error-status period;
  * raw records serialize with provenance and per-unit fields intact.

``get_data`` is faked so the test is deterministic and offline.

Run: cd artifacts/prayag && python3 -m pytest tests/test_api.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask

import api as apimod
import store as storemod
from metrics import Record, compute_metrics


def _fake_data(status="ok", released=False):
    """Minimal but realistic get_data() payload: one TANK output-only row
    (utilisation must stay suppressed) and one PIPE row with hours."""
    tank = Record(grain="daily", period="2026-07-01", date="2026-07-01",
                  plant="TANK", unit="Ltr", total_count=1200.0,
                  runhours_tracked=False, ideal_hours=500.0,
                  ideal_source="app_default")
    pipe = Record(grain="daily", period="2026-07-01", date="2026-07-01",
                  plant="PIPE", machine="M/C-1", unit="kg",
                  total_count=800.0, reject_count=10.0,
                  actual_hours=20.0, ideal_hours=24.0, ideal_source="sheet")
    rows = [tank, pipe]
    return {
        "rows": rows,
        "all_rows": rows,
        "quarantined": [],
        "overall": compute_metrics(rows),
        "validation": {},
        "confirmation": {
            "status": status,
            "released": released,
            "counts": {"error": 1 if status == "error" else 0},
            "issues": [
                {"key": "k1", "tier": "validity", "severity": "error",
                 "message": "example issue", "plant": "PIPE",
                 "acknowledged": False, "quarantined": False},
            ] if status == "error" else [],
            "signoff": None,
            "fingerprint": "fp-test",
        },
        "from_iso": "2026-07-01",
        "to_iso": "2026-07-01",
        "period_label": "01-07-2026",
        "period": "2026-07-01",
        "months": ["2026-07"],
        "grain_banner": "test banner",
        "daily_used": True,
        "source_reports": [],
        "plant_filter": "",
        "segment_filter": "",
        "machine_filter": "",
    }


def _client(get_data=None):
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(
        apimod.create_api(get_data or (lambda args: _fake_data())),
        url_prefix="/data-api/v1")
    return app.test_client()


def test_closed_without_key(monkeypatch):
    monkeypatch.delenv(apimod.API_KEY_ENV, raising=False)
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    c = _client()
    # Open endpoints still answer.
    assert c.get("/data-api/v1/").status_code == 200
    h = c.get("/data-api/v1/health")
    assert h.status_code == 200 and h.get_json()["api_enabled"] is False
    # Every data endpoint is 503 until the key is configured.
    for ep in ("/data-api/v1/summary", "/data-api/v1/records", "/data-api/v1/plants",
               "/data-api/v1/periods"):
        r = c.get(ep)
        assert r.status_code == 503, (ep, r.status_code)
        assert r.get_json()["error"] == "api_disabled"
    print("PASS: API is closed (503) until PRAYAG_API_KEY is configured")


def test_auth_required_and_accepted(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    c = _client()
    assert c.get("/data-api/v1/summary").status_code == 401
    assert c.get("/data-api/v1/summary",
                 headers={"X-API-Key": "wrong"}).status_code == 401
    ok_hdr = c.get("/data-api/v1/summary", headers={"X-API-Key": "sekret-123"})
    assert ok_hdr.status_code == 200, ok_hdr.status_code
    ok_bearer = c.get("/data-api/v1/summary",
                      headers={"Authorization": "Bearer sekret-123"})
    assert ok_bearer.status_code == 200
    print("PASS: bad/missing key -> 401; X-API-Key and Bearer both accepted")


def test_no_fake_zero_ratios(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    c = _client()
    body = c.get("/data-api/v1/summary",
                 headers={"X-API-Key": "sekret-123"}).get_json()
    tank = body["by_plant"]["TANK"]
    # TANK is output-only: utilisation must be null, never 0.
    assert tank["utilisation"] is None, tank["utilisation"]
    assert tank["oee"] is None
    # PIPE has real hours + baseline: utilisation is a number.
    pipe = body["by_plant"]["PIPE"]
    assert isinstance(pipe["utilisation"], (int, float)) and pipe["utilisation"] > 0
    # Output is bucketed per unit, never a meaningless cross-unit sum.
    assert body["overall"]["output_by_unit"] == {"Ltr": 1200.0, "kg": 800.0}
    assert body["overall"]["is_mixed_unit"] is True
    print("PASS: unavailable ratios are null and output stays per-unit")


def test_figures_gated_flag(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    hdr = {"X-API-Key": "sekret-123"}

    gated = _client(lambda a: _fake_data(status="error", released=False))
    b1 = gated.get("/data-api/v1/summary", headers=hdr).get_json()
    assert b1["figures_gated"] is True
    assert b1["confirmation"]["status"] == "error"
    assert b1["confirmation"]["issues"][0]["message"] == "example issue"

    released = _client(lambda a: _fake_data(status="error", released=True))
    b2 = released.get("/data-api/v1/summary", headers=hdr).get_json()
    assert b2["figures_gated"] is False

    clean = _client(lambda a: _fake_data(status="ok"))
    b3 = clean.get("/data-api/v1/summary", headers=hdr).get_json()
    assert b3["figures_gated"] is False
    print("PASS: figures_gated mirrors the dashboard's confirmation gate")


def test_records_serialization(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    c = _client()
    body = c.get("/data-api/v1/records",
                 headers={"X-API-Key": "sekret-123"}).get_json()
    assert body["row_count"] == 2
    by_plant = {r["plant"]: r for r in body["rows"]}
    assert by_plant["TANK"]["unit"] == "Ltr"
    assert by_plant["TANK"]["runhours_tracked"] is False
    assert by_plant["PIPE"]["machine"] == "M/C-1"
    assert by_plant["PIPE"]["ideal_source"] == "sheet"
    print("PASS: /records returns raw rows with provenance and unit fields")


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
