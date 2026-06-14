"""Route-level regression tests for the read-only Data Verification endpoints.

These exercise the Flask wiring offline by monkeypatching the sheet readers and
the durable store so nothing touches the network or a real database. They guard:
  * GET /verify renders 200 and writes NO audit row (read-only invariant),
  * GET /verify.csv streams the provenance CSV,
  * POST /verify/log appends exactly one audit row (and validates run_by /
    store availability),
  * month resolution accepts ?period=YYYY-MM and falls back for junk input.

Run: cd artifacts/prayag && python3 -m tests.test_verify_routes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from metrics import Record


def _monthly(plant, machine, total, *, unit="kg", period="2026-05"):
    return Record(
        grain="monthly", plant=plant, machine=machine, period=period,
        total_count=total, reject_count=0.0, unit=unit,
        actual_hours=100.0, ideal_hours=120.0, ideal_source="sheet",
        source_file="FILE_M", source_tab="Report-1",
    )


class _FakeStore:
    """In-memory stand-in for store.py — records appends so we can assert the
    read-only invariant (GET writes nothing; only POST /verify/log appends)."""

    StoreError = appmod.store.StoreError

    def __init__(self, available=True):
        self.AVAILABLE = available
        self.rows = []

    def verify_record(self, *, period, run_by, checks_passed,
                      checks_failed, n_rows, note=""):
        if not (run_by or "").strip():
            raise self.StoreError("A name is required.")
        self.rows.append({
            "period": period, "run_by": run_by,
            "checks_passed": checks_passed, "checks_failed": checks_failed,
            "n_rows": n_rows, "note": note,
        })

    def verify_last(self, period=None):
        rel = [r for r in self.rows if not period or r["period"] == period]
        if not rel:
            return None
        d = dict(rel[-1]); d["when_disp"] = "01-05-2026 09:00"; return d

    def verify_history(self, period=None, limit=20):
        rel = [r for r in self.rows if not period or r["period"] == period]
        return [dict(r, when_disp="01-05-2026 09:00") for r in rel][-limit:]


def _install(monkeypatch_store=None, daily=None, reports=None):
    """Patch the readers + store onto the app module; return (client, fake)."""
    monthly = [_monthly("GARDEN", "M/C-1", 1000.0)]
    appmod.get_records = lambda months: (list(monthly), list(reports or []), [])
    appmod.get_daily_records = lambda months: (list(daily or []), [], [])
    appmod._apply_baselines = lambda rows: None
    appmod.months_with_data = lambda: ["2026-04", "2026-05"]
    appmod.is_demo_mode = lambda: False
    fake = monkeypatch_store if monkeypatch_store is not None else _FakeStore()
    appmod.store = fake
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client(), fake


def test_get_verify_renders_and_writes_nothing():
    client, fake = _install()
    resp = client.get("/verify")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_data(as_text=True)
    assert "Data Verification" in body
    assert "Reconciliation checks" in body
    assert fake.rows == [], "GET /verify must never write an audit row"
    print("ok: GET /verify -> 200, no audit write (read-only)")


def test_get_verify_csv_streams_provenance():
    client, _ = _install()
    resp = client.get("/verify.csv")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    text = resp.get_data(as_text=True)
    assert text.splitlines()[0].startswith("plant,machine,year_month")
    assert "FILE_M" in text
    print("ok: GET /verify.csv -> CSV with provenance + download header")


def test_post_log_appends_one_row():
    client, fake = _install()
    resp = client.post("/verify/log",
                       data={"month": "2026-05", "run_by": "Asha"})
    assert resp.status_code in (302, 303)
    assert len(fake.rows) == 1
    assert fake.rows[0]["run_by"] == "Asha"
    assert fake.rows[0]["period"] == "2026-05"
    print("ok: POST /verify/log -> appends exactly one audit row")


def test_post_log_requires_name():
    client, fake = _install()
    resp = client.post("/verify/log", data={"month": "2026-05", "run_by": ""})
    assert resp.status_code in (302, 303)
    assert fake.rows == [], "blank name must not write a row"
    print("ok: POST /verify/log without a name -> no row written")


def test_post_log_store_unavailable_is_safe():
    client, fake = _install(monkeypatch_store=_FakeStore(available=False))
    resp = client.post("/verify/log",
                       data={"month": "2026-05", "run_by": "Asha"})
    assert resp.status_code in (302, 303)
    assert fake.rows == [], "no store -> no write, no crash"
    print("ok: POST /verify/log with no store -> safe no-op")


def test_month_resolution_period_and_fallback():
    client, _ = _install()
    # ?period=YYYY-MM honoured
    assert appmod._verify_month({"period": "2026-04"}) == "2026-04"
    # ?month wins / valid
    assert appmod._verify_month({"month": "2026-05"}) == "2026-05"
    # junk falls back to latest data month
    assert appmod._verify_month({"month": "not-a-month"}) == "2026-05"
    assert appmod._verify_month({}) == "2026-05"
    print("ok: month resolution honours period/month, falls back on junk")


if __name__ == "__main__":
    test_get_verify_renders_and_writes_nothing()
    test_get_verify_csv_streams_provenance()
    test_post_log_appends_one_row()
    test_post_log_requires_name()
    test_post_log_store_unavailable_is_safe()
    test_month_resolution_period_and_fallback()
    print("\nAll verify route tests passed.")
