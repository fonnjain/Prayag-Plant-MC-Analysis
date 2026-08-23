"""Offline Flask smoke coverage for the main user-facing route families.

These tests intentionally hit the registered Flask handlers instead of calling
builders directly.  Shared page plumbing belongs here: a builder-only suite
cannot catch an unbound module reference in an otherwise successful route.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from metrics import Record, compute_metrics


def _route_data() -> dict:
    """Small, coherent data shape for routes that use the shared get_data path."""
    rows = [
        Record(
            grain="monthly",
            period="2026-07",
            date="2026-07",
            plant="PIPE",
            segment="Plumbing",
            machine="M/C-1",
            total_count=100.0,
            reject_count=2.0,
            actual_hours=8.0,
            ideal_hours=10.0,
            ideal_output=120.0,
            planned_output=120.0,
        )
    ]
    return {
        "rows": rows,
        "all_rows": rows,
        "overall": compute_metrics(rows),
        "period": "2026-07",
        "period_label": "July 2026",
        "period_type": "month",
        "from_iso": "2026-07-01",
        "to_iso": "2026-07-31",
        "plant_filter": "",
        "segment_filter": "",
        "machine_filter": "",
        "daily_used": False,
        "has_claude": False,
        "deep_override": None,
        "source_reports": [],
        "grain_banner": "",
        "demo_mode": True,
        "confirmation": {
            "status": "pass",
            "counts": {"error": 0, "warning": 0, "total": 0},
            "issues": [],
            "period_key": "2026-07",
            "score_label": "1/1 source checked",
            "tiers": {},
            "score": {"files": (1, 1), "machines": (1, 1), "months": (1, 1)},
            "fingerprint": "route-smoke",
        },
    }


@pytest.fixture()
def client(monkeypatch):
    """Test core GET routes without Sheets, Drive, Postgres, or Claude."""
    appmod.app.config.update(TESTING=True, SECRET_KEY="route-smoke")
    monkeypatch.setattr(appmod.auth, "app_password", lambda: None)
    monkeypatch.setattr(appmod, "get_data", lambda _args: _route_data())
    monkeypatch.setattr(appmod, "_sync_ctx", lambda: {})
    monkeypatch.setattr(appmod, "is_demo_mode", lambda: True)
    monkeypatch.setattr(appmod, "last_fetch_status", lambda: {})
    monkeypatch.setattr(appmod, "_build_freshness", lambda: {
        "available": False, "sources": [], "n_total": 0, "n_updated": 0,
        "recent_days": 7,
    })
    monkeypatch.setattr(appmod.store, "history", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(appmod, "detected_sources", lambda: [])
    monkeypatch.setattr(appmod, "_build_index_catalogue", lambda: [])
    monkeypatch.setattr(appmod, "_build_data_health", lambda: {
        "today_disp": "01-07-2026",
        "last_synced": {},
        "n_tracked": 0,
        "recent_days": 7,
        "changed_n": 0,
        "fresh_available": False,
        "fresh_demo": True,
        "cards": {
            "latest_date": "—", "latest_plant": "", "latest_days_behind": None,
            "plants_reporting": 0, "plants_total": 0, "files_no_data": 0,
            "machines_idle": 0, "machines_total": 0,
        },
        "plants": [], "empty_workbooks": [], "idle_machines": [],
        "roster_gaps": [], "workbooks": [],
    })
    with appmod.app.test_client() as test_client:
        yield test_client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/performance",
        "/plant",
        "/machine",
        "/losses",
        "/reports",
        "/reports/pipe_summary",
        "/confirmation",
        "/sources",
        "/data",
    ],
)
def test_main_routes_render_200(client, path):
    """Every main route returns a rendered response through its Flask handler."""
    response = client.get(path)
    assert response.status_code == 200, f"{path} returned {response.status_code}"
