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
from types import SimpleNamespace

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
                  actual_hours=20.0, ideal_hours=24.0, ideal_source="sheet",
                  source_file="file-1", source_tab="Report-5", source_row=42)
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
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
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
    preview = c.post("/data-api/v1/schedule", json={
        "segment": "PLUMBING", "month": "2026-07", "kind": "pipe",
        "week_days": [7, 7, 7, 10],
        "demand": [{"item_code": "PIPEA", "material": "CPVC", "qty_pcs": 1}],
    })
    assert preview.status_code == 503
    assert preview.get_json()["error"] == "api_disabled"
    corrective = c.post("/data-api/v1/corrective-replan", json={
        "segment": "PLUMBING", "month": "2026-07",
        "as_of_date": "2026-07-15", "week_days": [7, 7, 7, 10],
        "demand": [{
            "item_code": "PIPEA", "material": "CPVC", "category": "pipe",
            "qty_pcs": 100, "produced_to_date_pcs": 10,
        }],
    })
    assert corrective.status_code == 503
    assert corrective.get_json()["error"] == "api_disabled"
    print("PASS: API is closed (503) until PRAYAG_API_KEY is configured")


def test_auth_required_and_accepted(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
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
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
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
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
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
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    c = _client()
    body = c.get("/data-api/v1/records",
                 headers={"X-API-Key": "sekret-123"}).get_json()
    assert body["row_count"] == 2
    by_plant = {r["plant"]: r for r in body["rows"]}
    assert by_plant["TANK"]["unit"] == "Ltr"
    assert by_plant["TANK"]["runhours_tracked"] is False
    assert by_plant["PIPE"]["machine"] == "M/C-1"
    assert by_plant["PIPE"]["ideal_source"] == "sheet"
    assert by_plant["PIPE"]["source_row"] == 42
    assert by_plant["TANK"]["source_row"] is None
    print("PASS: /records returns raw rows with provenance and unit fields")


def test_ptmt_summary_labels_source_gross_total_without_changing_it(monkeypatch):
    """PTMT API consumers can distinguish source-gross from good/net output."""
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    ptmt = Record(
        grain="daily", period="2026-07", date="2026-07-01",
        plant="PTMT", machine="PTMT M/C-1",
        total_count=105.0, reject_count=5.0,
    )
    data = _fake_data()
    data["rows"] = [ptmt]
    data["all_rows"] = [ptmt]
    data["overall"] = compute_metrics([ptmt])
    data["plant_filter"] = "PTMT"

    body = _client(lambda _args: data).get(
        "/data-api/v1/summary", headers={"X-API-Key": "sekret-123"},
    ).get_json()
    summary = body["by_plant"]["PTMT"]
    assert summary["total_count"] == 105.0
    assert summary["total_count_basis"] == "gross"
    assert summary["good_count"] == 100.0


def _schedule_request(**overrides):
    request = {
        "segment": "PLUMBING",
        "month": "2026-07",
        "kind": "pipe",
        "week_days": [7, 7, 7, 10],
        "demand": [
            {"item_code": "PIPEA", "raw_code": "Pipe A", "material": "CPVC",
             "qty_pcs": 100000, "weight": 999, "colour": "Ivory",
             "category": "Pipe", "urgency_rank": 1},
            {"item_code": "PIPEB", "material": "UPVC", "qty_pcs": 100000},
        ],
    }
    request.update(overrides)
    return request


def _seed_schedule_preview(monkeypatch):
    """Install a deterministic Plumbing master for an end-to-end API preview."""
    params = SimpleNamespace(
        waste_pct=0.0,
        pulverizer_pct=25.0,
        min_run_block_hours=2.0,
        week_days="[6,6,6,7]",
        week_days_configured=False,
    )
    machines = [
        {"machine": machine, "capacity_hrs_month": 500, "hours_per_shift": 10,
         "kind": "extrusion"}
        for machine in ("M/C-3", "M/C-4", "M/C-5")
    ]
    monkeypatch.setattr(apimod._mp_model, "get_params", lambda *_: params)
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: machines if kind == "extrusion" else [],
    )
    monkeypatch.setattr(
        apimod._mp_model, "get_bom_weight_rows",
        lambda *_: [
            {"item_code": "PIPEA", "weight_per_pc_kg": 1.0},
            {"item_code": "PIPEB", "weight_per_pc_kg": 1.0},
        ],
    )
    monkeypatch.setattr(
        apimod._mp_model, "get_per_hour",
        lambda *_: [
            {"item_code": "PIPEA", "basis": "kg_per_hr", "value": 100.0},
            {"item_code": "PIPEB", "basis": "kg_per_hr", "value": 100.0},
        ],
    )
    monkeypatch.setattr(
        apimod._mp_model, "get_routing",
        lambda *_: [
            {"item_code": item, "machine": machine, "material": material,
             "capable": True}
            for item, material in (("PIPEA", "CPVC"), ("PIPEB", "UPVC"))
            for machine in ("M/C-3", "M/C-4", "M/C-5")
        ],
    )
    monkeypatch.setattr(apimod._mp_model, "get_compound_recipes", lambda *_: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_downtime_affecting_month",
        lambda *_: [
            {"machine": machine, "start_date": "2026-07-01",
             "end_date": "2026-07-31"}
            for machine in ("M/C-3", "M/C-4")
        ],
    )
    monkeypatch.setattr(
        apimod._mp_rejection_plan, "build_rejection_lookup",
        lambda *_: {"items": {}, "material": {}, "overall": {}, "has_data": False},
    )
    monkeypatch.setattr(
        apimod._mp_wastage, "build_wastage_lookup",
        lambda *_args, **_kwargs: {
            "rates": {}, "all": 0.0, "basis": "default", "has_data": False,
        },
    )


def test_schedule_rejects_invalid_contract_without_running_master_reads(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    c = _client()
    hdr = {"X-API-Key": "sekret-123"}

    for patch, expected in (
        ({"segment": "PTMT"}, "segment"),
        ({"month": "2026-13"}, "month"),
        ({"kind": "both"}, "kind"),
        ({"week_days": [7, 7, 7]}, "exactly four"),
        ({"demand": [{"item_code": "PIPEA", "material": "PPR", "qty_pcs": 1}]},
         "material"),
        ({"demand": [{"item_code": "PIPEA", "material": "CPVC", "qty_pcs": 0}]},
         "qty_pcs"),
    ):
        response = c.post(
            "/data-api/v1/schedule", headers=hdr, json=_schedule_request(**patch)
        )
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["error"] == "invalid_schedule_request"
        assert expected.lower() in response.get_json()["message"].lower()


def test_schedule_reports_missing_machine_master_as_a_stable_error(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(apimod._mp_model, "get_machines", lambda *_args, **_kwargs: [])
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(),
    )
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"] == "planning_data_unavailable"
    assert "machine master data" in body["message"]


def test_schedule_dispatches_fitting_demand_to_the_fitting_engine(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: [{"machine": "MOD-1"}]
        if kind == "moulding" else [],
    )
    monkeypatch.setattr(apimod, "_schedule_plan_lookups", lambda _month: ({}, {}))
    monkeypatch.setattr(
        apimod._mp_model, "get_downtime_affecting_month", lambda *_: []
    )
    seen = {}
    fitting_item = SimpleNamespace(
        item_code="FIT-A", raw_code="FIT-A", has_weight=True,
        has_machine=True, machine_hrs=1.0, capable_machines=["MOD-1"],
    )

    def _fitting_engine(demand, month, segment, **_kwargs):
        seen["engine"] = (demand, month, segment)
        return SimpleNamespace(items=[fitting_item])

    def _fitting_schedule(**kwargs):
        seen["schedule"] = kwargs
        return SimpleNamespace(to_dict=lambda: {
            "segment": "PLUMBING",
            "kind": "fitting",
            "effective_month": "2026-07",
            "blocks": [],
            "weekly_fill": [],
            "unfinished": [],
        })

    monkeypatch.setattr(apimod._mp_engine, "run_fitting_engine", _fitting_engine)
    monkeypatch.setattr(apimod._mp_scheduler, "run_fitting_schedule", _fitting_schedule)
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(kind="fitting", demand=[{
            "item_code": "FIT-A", "material": "CPVC", "qty_pcs": 100,
        }]),
    )
    assert response.status_code == 200, response.get_json()
    demand, month, segment = seen["engine"]
    assert month == "2026-07" and segment == "PLUMBING"
    assert isinstance(demand[0], apimod._mp_engine.FittingDemandItem)
    assert seen["schedule"]["fitting_items"] == [fitting_item]
    assert seen["schedule"]["week_days_override"] == [7, 7, 7, 10]
    assert response.get_json()["kind"] == "fitting"


def test_schedule_rejects_machine_registered_in_both_plumbing_pools(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: [{"machine": "M/C-DUAL"}]
        if kind in ("extrusion", "moulding") else [],
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(),
    )
    assert response.status_code == 409
    body = response.get_json()
    assert body["error"] == "schedule_machine_pool_overlap"
    assert "M/C-DUAL" in body["message"]


def test_schedule_rejects_pipe_demand_missing_from_the_bom_master(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    _seed_schedule_preview(monkeypatch)
    monkeypatch.setattr(
        apimod._mp_model, "get_bom_weight_rows",
        lambda *_: [{"item_code": "PIPEA", "weight_per_pc_kg": 1.0}],
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(),
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_schedule_request"
    assert "PIPEB: no BOM weight" in body["message"]


def test_schedule_rejects_unroutable_fitting_demand(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: [{"machine": "MOD-1"}]
        if kind == "moulding" else [],
    )
    monkeypatch.setattr(apimod, "_schedule_plan_lookups", lambda _month: ({}, {}))
    monkeypatch.setattr(
        apimod._mp_model, "get_downtime_affecting_month", lambda *_: []
    )
    monkeypatch.setattr(
        apimod._mp_engine, "run_fitting_engine",
        lambda *_args, **_kwargs: SimpleNamespace(items=[SimpleNamespace(
            item_code="FIT-A", raw_code="FIT-A", has_weight=True,
            has_machine=False, machine_hrs=0.0, capable_machines=[],
        )]),
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(kind="fitting", demand=[{
            "item_code": "FIT-A", "material": "CPVC", "qty_pcs": 100,
        }]),
    )
    assert response.status_code == 400
    body = response.get_json()
    assert body["error"] == "invalid_schedule_request"
    assert "FIT-A: no capable moulding route" in body["message"]


def test_schedule_rejects_pipe_demand_with_no_usable_production_rate(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: [{"machine": "M/C-1"}]
        if kind == "extrusion" else [],
    )
    monkeypatch.setattr(apimod, "_schedule_plan_lookups", lambda _month: ({}, {}))
    monkeypatch.setattr(
        apimod._mp_model, "get_downtime_affecting_month", lambda *_: []
    )
    monkeypatch.setattr(
        apimod._mp_engine, "run_engine",
        lambda *_args, **_kwargs: SimpleNamespace(items=[SimpleNamespace(
            item_code="PIPE-A", raw_code="PIPE-A", has_weight=True,
            has_machine=True, machine_hrs=0.0, capable_machines=["M/C-1"],
        )]),
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(demand=[{
            "item_code": "PIPE-A", "material": "CPVC", "qty_pcs": 100,
        }]),
    )
    assert response.status_code == 400
    assert "PIPE-A: no usable production rate" in response.get_json()["message"]


def test_schedule_rejects_extreme_finite_demand_before_nonfinite_output(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    _seed_schedule_preview(monkeypatch)
    monkeypatch.setattr(
        apimod._mp_model, "get_bom_weight_rows",
        lambda *_: [{"item_code": "PIPEA", "weight_per_pc_kg": 1e308}],
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(demand=[{
            "item_code": "PIPEA", "material": "CPVC", "qty_pcs": 1e308,
        }]),
    )
    assert response.status_code == 400, response.get_json()
    body = response.get_json()
    assert body["error"] == "invalid_schedule_request"
    assert "PIPEA: quantity exceeds the safe scheduling range" in body["message"]


def test_schedule_rejects_fitting_route_without_an_active_machine(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    monkeypatch.setattr(
        apimod._mp_model, "get_machines",
        lambda _segment, _month, kind=None: [{"machine": "MOD-1"}]
        if kind == "moulding" else [],
    )
    monkeypatch.setattr(apimod, "_schedule_plan_lookups", lambda _month: ({}, {}))
    monkeypatch.setattr(
        apimod._mp_model, "get_downtime_affecting_month", lambda *_: []
    )
    monkeypatch.setattr(
        apimod._mp_engine, "run_fitting_engine",
        lambda *_args, **_kwargs: SimpleNamespace(items=[SimpleNamespace(
            item_code="FIT-A", raw_code="FIT-A", has_weight=True,
            has_machine=True, machine_hrs=1.0, capable_machines=["MOD-99"],
        )]),
    )
    response = _client().post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(kind="fitting", demand=[{
            "item_code": "FIT-A", "material": "CPVC", "qty_pcs": 100,
        }]),
    )
    assert response.status_code == 400
    assert "FIT-A: route has no active moulding machine" in response.get_json()["message"]


def test_schedule_preview_uses_engine_calendar_capacity_and_downtime(monkeypatch):
    """The API calls the real pipe engine and shift scheduler, entirely offline."""
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])
    _seed_schedule_preview(monkeypatch)
    c = _client()
    response = c.post(
        "/data-api/v1/schedule",
        headers={"X-API-Key": "sekret-123"},
        json=_schedule_request(),
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()

    # Caller-provided full calendar is used, rather than the saved legacy split.
    assert body["segment"] == "PLUMBING"
    assert body["kind"] == "pipe"
    assert body["effective_month"] == "2026-07"
    assert body["week_days"] == [7, 7, 7, 10]
    assert body["params_used"]["week_days"] == [7, 7, 7, 10]

    # M/C-3 and M/C-4 are explicitly shown as unavailable, not silently omitted.
    assert body["downtime_machine_days"] == 62
    for machine in ("M/C-3", "M/C-4"):
        blocks = [b for b in body["blocks"] if b["machine"] == machine]
        assert len(blocks) == 62
        assert all(b["item_code"] == "DOWN" and b["is_idle"] for b in blocks)

    # The only available member of the shared M/C-3/4/5 pool is capacity-limited.
    mc5_rows = [r for r in body["weekly_fill"] if r["machine"] == "M/C-5"]
    assert sum(r["scheduled_hrs"] for r in mc5_rows) <= sum(
        r["capacity_hrs"] for r in mc5_rows
    )
    assert all(r["scheduled_hrs"] <= r["capacity_hrs"] for r in mc5_rows)
    productive = [b for b in body["blocks"] if not b["is_idle"]]
    assert {b["machine"] for b in productive} == {"M/C-5"}

    # Demand exceeds M/C-5's month capacity. The result has both capacity units.
    assert body["unfinished"]
    assert all(item["remaining_kg"] > 0 for item in body["unfinished"])
    assert all(item["remaining_pcs"] > 0 for item in body["unfinished"])
    assert all(
        item["downtime_reason"] == ""
        for item in body["unfinished"]
    )
    assert "POST" in response.headers["Access-Control-Allow-Methods"]


def _corrective_request(**overrides):
    request = {
        "segment": "PLUMBING",
        "month": "2026-07",
        "as_of_date": "2026-07-15",
        "week_days": [7, 7, 7, 10],
        "demand": [
            {
                "item_code": "PIPE-A",
                "material": "CPVC",
                "category": "pipe",
                "qty_pcs": 1000,
                "produced_to_date_pcs": 50,
            },
            {
                "item_code": "FIT-A",
                "material": "UPVC",
                "category": "fitting",
                "qty_pcs": 2000,
                "produced_to_date_pcs": 125,
            },
        ],
        "cap_feasible_by_cat": {
            "CPVC Pipe": 900,
            "UPVC Fitting": 1500,
        },
    }
    request.update(overrides)
    return request


def _enable_api(monkeypatch):
    monkeypatch.setenv(apimod.API_KEY_ENV, "sekret-123")
    monkeypatch.setattr(storemod, "get_api_key", lambda: None)
    monkeypatch.setattr(storemod, "get_all_api_keys", lambda: [])


def test_corrective_replan_rejects_invalid_contract_before_loading_actuals(monkeypatch):
    _enable_api(monkeypatch)
    monkeypatch.setattr(
        apimod._sheets, "load_corrective_replan_actuals",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not load actuals")),
    )
    c = _client()
    hdr = {"X-API-Key": "sekret-123"}

    for patch, expected in (
        ({"segment": "PTMT"}, "segment"),
        ({"month": "2026-13"}, "month"),
        ({"as_of_date": "2026-08-01"}, "selected month"),
        ({"week_days": [7, 7, 7]}, "exactly four"),
        ({"demand": [{
            "item_code": "PIPE-A", "material": "PPR", "category": "pipe",
            "qty_pcs": 100, "produced_to_date_pcs": 0,
        }]}, "material"),
        ({"demand": [{
            "item_code": "PIPE-A", "material": "CPVC", "category": "other",
            "qty_pcs": 100, "produced_to_date_pcs": 0,
        }]}, "category"),
        ({"demand": [{
            "item_code": "PIPE-A", "material": "CPVC", "category": "pipe",
            "qty_pcs": 100, "produced_to_date_pcs": -1,
        }]}, "produced_to_date_pcs"),
        ({"cap_feasible_by_cat": {"Made Up Category": 1}}, "unknown category"),
    ):
        response = c.post(
            "/data-api/v1/corrective-replan",
            headers=hdr,
            json=_corrective_request(**patch),
        )
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["error"] == "invalid_corrective_replan_request"
        assert expected.lower() in response.get_json()["message"].lower()

    for bad_qty in (0, True, "not-a-number", float("nan"), float("inf")):
        response = c.post(
            "/data-api/v1/corrective-replan",
            headers=hdr,
            json=_corrective_request(demand=[{
                "item_code": "PIPE-A",
                "material": "CPVC",
                "category": "pipe",
                "qty_pcs": bad_qty,
                "produced_to_date_pcs": 0,
            }]),
        )
        assert response.status_code == 400, response.get_json()
        assert response.get_json()["error"] == "invalid_corrective_replan_request"
        assert "qty_pcs" in response.get_json()["message"]


def test_corrective_replan_maps_demand_calendar_and_confidence_to_native_result(
    monkeypatch,
):
    _enable_api(monkeypatch)
    monkeypatch.setattr(
        apimod._sheets, "load_corrective_replan_actuals",
        lambda month: {
            "file_id": "pipe-file",
            "r11": [["r11"]],
            "r12": [["r12"]],
            "error": None,
        },
    )
    seen = {}

    def _compute(**kwargs):
        seen.update(kwargs)
        return apimod._mp_corrective_replan.CorrectiveReplanResult(
            month=kwargs["month"],
            as_of_date=kwargs["as_of_date"],
            working_days_total=sum(kwargs["configured_week_days"]),
            working_days_elapsed=14,
            working_days_remaining=17,
            categories=[
                apimod._mp_corrective_replan.CategoryResult(
                    category="CPVC Pipe",
                    daily_values=[100, 120, 140],
                    n_days=3,
                    produced_to_date=360,
                    cap_per_day=120,
                    method="mean(low-confidence,3d)",
                    remaining=950,
                    working_days_remaining=17,
                    feasible=2040,
                    shortfall=0,
                    cap_feasible=900,
                ),
                apimod._mp_corrective_replan.CategoryResult(
                    category="UPVC Fitting",
                    daily_values=[],
                    n_days=0,
                    produced_to_date=0,
                    cap_per_day=0,
                    method="none",
                    remaining=1875,
                    working_days_remaining=17,
                    feasible=0,
                    shortfall=1875,
                    cap_feasible=1500,
                ),
            ],
            source_file_id=kwargs["file_id"],
            source_date_min="2026-07-01",
            source_date_max="2026-07-03",
            plan_produced_total=175,
            actual_produced_total=360,
            other_produced=0,
            warnings=[],
        )

    monkeypatch.setattr(
        apimod._mp_corrective_replan, "compute_corrective_replan", _compute,
    )
    response = _client().post(
        "/data-api/v1/corrective-replan",
        headers={"X-API-Key": "sekret-123"},
        json=_corrective_request(),
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()

    assert seen["month"] == "2026-07"
    assert seen["as_of_date"] == "2026-07-15"
    assert seen["r11_values"] == [["r11"]]
    assert seen["r12_values"] == [["r12"]]
    assert seen["configured_week_days"] == [7, 7, 7, 10]
    assert seen["cap_feasible_by_cat"] == {
        "CPVC Pipe": 900,
        "UPVC Fitting": 1500,
    }
    pipe, fitting = seen["plan_recs"]
    assert (pipe.item_code, pipe.family, pipe.category) == (
        "PIPEA", "CPVC", "Pipe",
    )
    assert (pipe.produce_required, pipe.produced) == (1000, 50)
    assert (fitting.item_code, fitting.family, fitting.category) == (
        "FITA", "UPVC", "Fitting",
    )

    assert body["week_days"] == [7, 7, 7, 10]
    assert body["working_days_total"] == 31
    assert body["min_days_for_p90"] == 5
    assert body["units"]["shortfall"] == "pcs"
    by_category = {row["category"]: row for row in body["categories"]}
    assert by_category["CPVC Pipe"]["low_confidence"] is True
    assert by_category["CPVC Pipe"]["not_started"] is False
    assert by_category["CPVC Pipe"]["method"] == "mean(low-confidence,3d)"
    assert by_category["CPVC Pipe"]["cap_feasible"] == 900
    assert by_category["UPVC Fitting"]["not_started"] is True
    assert by_category["UPVC Fitting"]["shortfall"] == 1875
    assert response.headers["Cache-Control"] == "no-store"


def test_corrective_replan_returns_stable_error_for_unavailable_actuals(monkeypatch):
    _enable_api(monkeypatch)
    monkeypatch.setattr(
        apimod._sheets, "load_corrective_replan_actuals",
        lambda _month: {
            "file_id": "pipe-file",
            "r11": [["partial"]],
            "r12": [],
            "error": "sensitive connector failure detail",
        },
    )
    response = _client().post(
        "/data-api/v1/corrective-replan",
        headers={"X-API-Key": "sekret-123"},
        json=_corrective_request(),
    )
    assert response.status_code == 503
    body = response.get_json()
    assert body["error"] == "corrective_actuals_unavailable"
    assert "Report-11 and Report-12 actuals are unavailable" in body["message"]
    assert "sensitive connector" not in body["message"]


def test_corrective_replan_endpoint_runs_real_engine_with_sunday_aware_calendar(
    monkeypatch,
):
    _enable_api(monkeypatch)
    r11 = [
        ["", "DATE", "", "", "TYPES", "ITEM CODE", "", "", "PCS"],
        ["", "Jul 1, 2026", "", "", "CPVC", "PIPE-A", "", "", 100],
        ["", "Jul 2, 2026", "", "", "CPVC", "PIPE-A", "", "", 120],
        ["", "Jul 3, 2026", "", "", "CPVC", "PIPE-A", "", "", 140],
    ]
    r12 = [
        ["DATE", "MATERIAL", "ITEM CODE", "", "", "", "", "", "OUTPUT PRODUCTION"],
        ["Jul 1, 2026", "UPVC", "FIT-A", "", "", "", "", "", 200],
        ["Jul 2, 2026", "UPVC", "FIT-A", "", "", "", "", "", 220],
        ["Jul 3, 2026", "UPVC", "FIT-A", "", "", "", "", "", 240],
        ["Jul 4, 2026", "UPVC", "FIT-A", "", "", "", "", "", 260],
        ["Jul 5, 2026", "UPVC", "FIT-A", "", "", "", "", "", 280],
    ]
    monkeypatch.setattr(
        apimod._sheets, "load_corrective_replan_actuals",
        lambda _month: {
            "file_id": "pipe-file",
            "r11": r11,
            "r12": r12,
            "error": None,
        },
    )

    response = _client().post(
        "/data-api/v1/corrective-replan",
        headers={"X-API-Key": "sekret-123"},
        json=_corrective_request(),
    )
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    by_category = {row["category"]: row for row in body["categories"]}

    assert body["working_days_total"] == 31
    assert body["working_days_elapsed"] == 14
    assert body["working_days_remaining"] == 17
    assert by_category["CPVC Pipe"]["n_days"] == 3
    assert by_category["CPVC Pipe"]["method"] == "mean(low-confidence,3d)"
    assert by_category["CPVC Pipe"]["low_confidence"] is True
    assert by_category["CPVC Pipe"]["remaining"] == 950
    assert by_category["UPVC Fitting"]["n_days"] == 5
    assert by_category["UPVC Fitting"]["method"] == "p90"
    assert by_category["UPVC Fitting"]["low_confidence"] is False
    assert by_category["UPVC Fitting"]["remaining"] == 1875
    assert by_category["SWR Solvent"]["not_started"] is True


def test_corrective_replan_hides_computation_failure_details(monkeypatch):
    _enable_api(monkeypatch)
    monkeypatch.setattr(
        apimod._sheets, "load_corrective_replan_actuals",
        lambda _month: {
            "file_id": "pipe-file",
            "r11": [],
            "r12": [],
            "error": None,
        },
    )
    monkeypatch.setattr(
        apimod._mp_corrective_replan,
        "compute_corrective_replan",
        lambda **_kwargs: (_ for _ in ()).throw(
            RuntimeError("sensitive internal parser state")
        ),
    )
    response = _client().post(
        "/data-api/v1/corrective-replan",
        headers={"X-API-Key": "sekret-123"},
        json=_corrective_request(),
    )
    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "corrective_replan_failed"
    assert body["message"] == "The Plumbing corrective re-plan could not be computed."
    assert "sensitive internal" not in body["message"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
