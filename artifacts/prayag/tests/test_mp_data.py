"""
Tests for Machine Planning Data Page endpoints (MP-1).
Covers: save/version, reset, estimated-rate flag, section isolation.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from unittest.mock import patch, MagicMock
import app as flask_app
import mp_model
import mp_seed as _mp_seed
from app import _mp_build_compound_cards, _mp_build_pipe_items, _MP_SEGMENT

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    """Prevent any real DB calls; override per-test as needed."""
    monkeypatch.setattr(mp_model, "AVAILABLE", False)


# ---------------------------------------------------------------------------
# GET /machine-planning/data — renders without touching "/"
# ---------------------------------------------------------------------------

class TestMpDataView:
    def test_renders_200(self, client):
        r = client.get("/machine-planning/data")
        assert r.status_code == 200

    def test_page_title_present(self, client):
        r = client.get("/machine-planning/data")
        assert b"Machine Planning" in r.data

    def test_plumbing_tab_default(self, client):
        r = client.get("/machine-planning/data")
        assert b"Plumbing" in r.data

    def test_ptmt_tab_stub(self, client):
        r = client.get("/machine-planning/data?tab=ptmt")
        assert r.status_code == 200
        assert b"PTMT" in r.data

    def test_root_route_not_mp(self, client):
        """The '/' URL rule must map to 'overview', not to 'mp_data_view'."""
        url_map = {rule.rule: rule.endpoint for rule in flask_app.app.url_map.iter_rules()}
        assert url_map.get("/") == "overview"
        assert url_map.get("/") != "mp_data_view"

    def test_data_route_not_mp(self, client):
        """/data URL rule must map to data_health_view, not to mp_data_view."""
        url_map = {rule.rule: rule.endpoint for rule in flask_app.app.url_map.iter_rules()}
        assert url_map.get("/data") == "data_health_view"
        assert url_map.get("/data") != "mp_data_view"

    def test_mp_data_route_registered(self, client):
        """/machine-planning/data must be registered and map to mp_data_view."""
        url_map = {rule.rule: rule.endpoint for rule in flask_app.app.url_map.iter_rules()}
        assert url_map.get("/machine-planning/data") == "mp_data_view"

    def test_month_selector_in_page(self, client):
        r = client.get("/machine-planning/data?month=2026-07")
        assert b"2026-07" in r.data

    def test_db_unavailable_banner(self, client):
        r = client.get("/machine-planning/data")
        assert b"Database unavailable" in r.data


# ---------------------------------------------------------------------------
# _mp_build_compound_cards
# ---------------------------------------------------------------------------

class TestBuildCompoundCards:
    def _make_recipes(self):
        """Return a minimal set of compound recipe dicts (2 cards)."""
        return [
            {"material": "CPVC", "type": "pipe", "component": "Resin",
             "ratio_kg": 100.0, "price_per_kg": 145.0,
             "wastage_factor": 1.05, "needs_recipe": False},
            {"material": "CPVC", "type": "pipe", "component": "Stabiliser",
             "ratio_kg": 5.0, "price_per_kg": 80.0,
             "wastage_factor": 1.05, "needs_recipe": False},
            {"material": "SWR", "type": "fitting", "component": "",
             "ratio_kg": 0.0, "price_per_kg": 0.0,
             "wastage_factor": 1.0, "needs_recipe": True},
        ]

    def test_returns_eight_cards(self):
        cards = _mp_build_compound_cards(self._make_recipes())
        assert len(cards) == 8

    def test_cpvc_pipe_totals(self):
        cards = _mp_build_compound_cards(self._make_recipes())
        cpvc = next(c for c in cards if c["material"] == "CPVC" and c["type"] == "pipe")
        assert cpvc["total_kg"] == pytest.approx(105.0)
        total_cost = 100 * 145 + 5 * 80  # 14500 + 400 = 14900
        assert cpvc["total_cost"] == pytest.approx(14900.0)
        # cost_per_kg = (14900 / 105) * 1.05
        assert cpvc["cost_per_kg"] == pytest.approx((14900 / 105) * 1.05, rel=1e-4)

    def test_ordering_consistent(self):
        cards = _mp_build_compound_cards(self._make_recipes())
        materials = [c["material"] for c in cards]
        assert materials == ["CPVC", "CPVC", "UPVC", "UPVC", "SWR", "SWR", "AGRI", "AGRI"]

    def test_needs_recipe_flag(self):
        cards = _mp_build_compound_cards(self._make_recipes())
        swr_fit = next(c for c in cards if c["material"] == "SWR" and c["type"] == "fitting")
        assert swr_fit["needs_recipe"] is True

    def test_empty_card_for_missing_material(self):
        cards = _mp_build_compound_cards([])
        # All 8 cards still created; all have empty components
        assert len(cards) == 8
        for c in cards:
            assert c["components"] == []
            assert c["total_kg"] == 0.0


# ---------------------------------------------------------------------------
# _mp_build_pipe_items
# ---------------------------------------------------------------------------

class TestBuildPipeItems:
    def _make_routing(self):
        return [
            {"machine": "M/C-1", "item_code": "PS2", "material": "CPVC", "capable": True},
            {"machine": "M/C-2", "item_code": "PS2", "material": "CPVC", "capable": True},
            {"machine": "M/C-1", "item_code": "PU3", "material": "UPVC", "capable": True},
            {"machine": "C01(I-40)", "item_code": "PW11", "material": "", "capable": True},  # moulding
        ]

    def test_only_pipe_machines_included(self):
        items = _mp_build_pipe_items(self._make_routing())
        codes = {i["item_code"] for i in items}
        assert "PW11" not in codes   # moulding machine, not M/C-*
        assert "PS2" in codes

    def test_capable_machines_per_item(self):
        items = _mp_build_pipe_items(self._make_routing())
        ps2 = next(i for i in items if i["item_code"] == "PS2")
        assert set(ps2["capable_machines"]) == {"M/C-1", "M/C-2"}

    def test_single_machine_item(self):
        items = _mp_build_pipe_items(self._make_routing())
        pu3 = next(i for i in items if i["item_code"] == "PU3")
        assert pu3["capable_machines"] == ["M/C-1"]

    def test_material_propagated(self):
        items = _mp_build_pipe_items(self._make_routing())
        ps2 = next(i for i in items if i["item_code"] == "PS2")
        assert ps2["material"] == "CPVC"


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/params
# ---------------------------------------------------------------------------

class TestSaveParams:
    def test_rejects_without_db(self, client):
        r = client.post(
            "/machine-planning/data/save/params",
            data=json.dumps({"waste_pct": 4.0, "pulverizer_pct": 25.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        # DB unavailable → 500 with error
        assert r.status_code == 500
        body = json.loads(r.data)
        assert body["ok"] is False

    def test_bad_payload_400(self, client):
        r = client.post(
            "/machine-planning/data/save/params",
            data=json.dumps({"waste_pct": "bad"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_saves_with_mock_db(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        def fake_upsert(row):
            saved["row"] = row
            return 1
        monkeypatch.setattr(mp_model, "upsert_params", fake_upsert)
        r = client.post(
            "/machine-planning/data/save/params",
            data=json.dumps({"waste_pct": 5.0, "pulverizer_pct": 30.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 200
        body = json.loads(r.data)
        assert body["ok"] is True
        assert saved["row"].waste_pct == pytest.approx(5.0)
        assert saved["row"].pulverizer_pct == pytest.approx(30.0)
        assert saved["row"].effective_month == "2026-07"

    def test_versioned_to_month(self, client, monkeypatch):
        """Save must bind to the requested effective_month, not today."""
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_params", lambda r: saved.update({"em": r.effective_month}) or 1)
        client.post(
            "/machine-planning/data/save/params",
            data=json.dumps({"waste_pct": 4.0, "pulverizer_pct": 25.0, "effective_month": "2025-12"}),
            content_type="application/json",
        )
        assert saved["em"] == "2025-12"


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/bom
# ---------------------------------------------------------------------------

class TestSaveBom:
    def test_saves_with_mock_db(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_single_bom", lambda r: saved.update({"r": r}) or 1)
        r = client.post(
            "/machine-planning/data/save/bom",
            data=json.dumps({"item_code": "PS2", "weight_per_pc_kg": 0.055, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert saved["r"].item_code == "PS2"
        assert saved["r"].weight_per_pc_kg == pytest.approx(0.055)

    def test_negative_weight_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/bom",
            data=json.dumps({"item_code": "PS2", "weight_per_pc_kg": -1.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/per-hour
# ---------------------------------------------------------------------------

class TestSavePerHour:
    def test_saves_kg_per_hr(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_single_per_hour", lambda r: saved.update({"r": r}) or 1)
        r = client.post(
            "/machine-planning/data/save/per-hour",
            data=json.dumps({"item_code": "SWR20", "basis": "kg_per_hr", "value": 180.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert saved["r"].basis == "kg_per_hr"
        assert saved["r"].value == pytest.approx(180.0)

    def test_invalid_basis_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/per-hour",
            data=json.dumps({"item_code": "X", "basis": "hourly", "value": 10.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_estimated_rate_saves_as_versioned_row(self, client, monkeypatch):
        """An estimated-rate item (no seed source) can be manually saved."""
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_single_per_hour", lambda r: saved.update({"r": r}) or 1)
        r = client.post(
            "/machine-planning/data/save/per-hour",
            data=json.dumps({"item_code": "SWR99", "basis": "kg_per_hr",
                             "value": 150.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert json.loads(r.data)["ok"] is True
        assert saved["r"].item_code == "SWR99"


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/machine
# ---------------------------------------------------------------------------

class TestSaveMachine:
    def test_saves_capacity(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        monkeypatch.setattr(mp_model, "get_machines", lambda *a, **kw: [
            {"machine": "M/C-3", "kind": "extrusion", "support_w": 4, "operators_ot": 1,
             "capacity_hrs_month": 500}
        ])
        upserted = {}
        monkeypatch.setattr(mp_model, "upsert_machines", lambda rows: upserted.update({"rows": rows}) or 1)
        r = client.post(
            "/machine-planning/data/save/machine",
            data=json.dumps({"machine": "M/C-3", "kind": "extrusion",
                             "capacity_hrs_month": 480.0, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert upserted["rows"][0].capacity_hrs_month == pytest.approx(480.0)

    def test_invalid_kind_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/machine",
            data=json.dumps({"machine": "M/C-1", "kind": "laser",
                             "capacity_hrs_month": 500, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/routing/pipe
# ---------------------------------------------------------------------------

class TestSavePipeRouting:
    def test_saves_capable_machines(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_routing_for_item",
                            lambda seg, ic, em, mcs: saved.update({"mcs": mcs}) or len(mcs))
        r = client.post(
            "/machine-planning/data/save/routing/pipe",
            data=json.dumps({"item_code": "PS2", "machines": ["M/C-1", "M/C-3"],
                             "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert set(saved["mcs"]) == {"M/C-1", "M/C-3"}

    def test_empty_machines_removes_all(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_routing_for_item",
                            lambda seg, ic, em, mcs: saved.update({"mcs": mcs}))
        client.post(
            "/machine-planning/data/save/routing/pipe",
            data=json.dumps({"item_code": "PS2", "machines": [], "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert saved["mcs"] == []

    def test_invalid_machine_name_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/routing/pipe",
            data=json.dumps({"item_code": "PS2", "machines": ["IM-01"],
                             "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/routing/fitting
# ---------------------------------------------------------------------------

class TestSaveFittingRouting:
    def test_saves_cavity_and_cycle(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        monkeypatch.setattr(mp_model, "get_fitting_std", lambda *a: [])
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_single_fitting_std",
                            lambda r: saved.update({"r": r}) or 1)
        r = client.post(
            "/machine-planning/data/save/routing/fitting",
            data=json.dumps({"item_code": "PW11", "machine": "C04(U-250)",
                             "cavity": 4.0, "cycle_time_sec": 22.0,
                             "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert saved["r"].cavity == pytest.approx(4.0)
        assert saved["r"].cycle_time_sec == pytest.approx(22.0)


# ---------------------------------------------------------------------------
# POST /machine-planning/data/save/compound
# ---------------------------------------------------------------------------

class TestSaveCompound:
    def test_saves_wastage_factor(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved = {}
        monkeypatch.setattr(mp_model, "upsert_compound_wastage",
                            lambda seg, mat, typ, wf, em: saved.update({"wf": wf}) or 3)
        r = client.post(
            "/machine-planning/data/save/compound",
            data=json.dumps({"material": "CPVC", "type": "pipe",
                             "wastage_factor": 1.08, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert saved["wf"] == pytest.approx(1.08)

    def test_wastage_below_one_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/compound",
            data=json.dumps({"material": "CPVC", "type": "pipe",
                             "wastage_factor": 0.95, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_invalid_material_rejected(self, client):
        r = client.post(
            "/machine-planning/data/save/compound",
            data=json.dumps({"material": "PVC", "type": "pipe",
                             "wastage_factor": 1.05, "effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /machine-planning/data/reset/<section>
# ---------------------------------------------------------------------------

class TestResetSection:
    def _patch_token(self, monkeypatch):
        monkeypatch.setattr("sheets._get_access_token", lambda: "fake-token",
                            raising=False)

    def test_unknown_section_404(self, client):
        r = client.post(
            "/machine-planning/data/reset/bogus",
            data=json.dumps({"effective_month": "2026-07"}),
            content_type="application/json",
        )
        assert r.status_code == 400
        body = json.loads(r.data)
        assert body["ok"] is False

    def test_params_reset_calls_seed(self, client, monkeypatch):
        called = {}
        monkeypatch.setattr(_mp_seed, "seed_params", lambda em: called.update({"em": em}) or {"rows_loaded": 1})
        monkeypatch.setattr("sheets._get_access_token", lambda: "tok", raising=False)
        r = client.post(
            "/machine-planning/data/reset/params",
            data=json.dumps({"effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert called["em"] == "2026-07"

    def test_bom_reset_calls_seed(self, client, monkeypatch):
        called = {}
        monkeypatch.setattr(_mp_seed, "seed_bom_weights",
                            lambda tok, em: called.update({"em": em}) or {"rows_loaded": 1})
        monkeypatch.setattr("sheets._get_access_token", lambda: "tok", raising=False)
        r = client.post(
            "/machine-planning/data/reset/bom",
            data=json.dumps({"effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert called["em"] == "2026-07"

    def test_compound_reset_calls_seed(self, client, monkeypatch):
        called = {}
        monkeypatch.setattr(_mp_seed, "seed_compound_recipes",
                            lambda tok, em: called.update({"em": em}) or {"rows_loaded": 1})
        monkeypatch.setattr("sheets._get_access_token", lambda: "tok", raising=False)
        r = client.post(
            "/machine-planning/data/reset/compound",
            data=json.dumps({"effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True

    def test_per_hour_reset_calls_seed(self, client, monkeypatch):
        called = {}
        monkeypatch.setattr(_mp_seed, "seed_per_hour",
                            lambda tok, em: called.update({"em": em}) or {"rows_loaded": 1})
        monkeypatch.setattr("sheets._get_access_token", lambda: "tok", raising=False)
        r = client.post(
            "/machine-planning/data/reset/per_hour",
            data=json.dumps({"effective_month": "2026-07"}),
            content_type="application/json",
        )
        body = json.loads(r.data)
        assert body["ok"] is True
        assert called["em"] == "2026-07"


# ---------------------------------------------------------------------------
# Estimated-rate flag logic
# ---------------------------------------------------------------------------

class TestEstimatedRateFlag:
    """Verify SWR/AGRI pipe items with no per-hour source get the flag."""

    def _make_pipe_items(self):
        return [
            {"item_code": "PS2",   "material": "CPVC",  "capable_machines": ["M/C-1"]},
            {"item_code": "SWR20", "material": "SWR",   "capable_machines": ["M/C-3"]},
            {"item_code": "AG10",  "material": "AGRI",  "capable_machines": ["M/C-4"]},
        ]

    def _make_ph_rows(self):
        return [
            {"item_code": "PS2",  "basis": "kg_per_hr", "value": 45.0},
        ]

    def test_swr_item_not_in_ph_flagged(self):
        pipe_items = self._make_pipe_items()
        ph_codes_kg = {r["item_code"] for r in self._make_ph_rows() if r["basis"] == "kg_per_hr"}
        estimated_materials = {"SWR", "AGRI"}
        estimated = {
            i["item_code"] for i in pipe_items
            if i["material"].upper() in estimated_materials
            and i["item_code"] not in ph_codes_kg
        }
        assert "SWR20" in estimated
        assert "AG10" in estimated
        assert "PS2" not in estimated

    def test_cpvc_item_never_flagged(self):
        pipe_items = self._make_pipe_items()
        ph_codes_kg = {r["item_code"] for r in self._make_ph_rows() if r["basis"] == "kg_per_hr"}
        estimated_materials = {"SWR", "AGRI"}
        estimated = {
            i["item_code"] for i in pipe_items
            if i["material"].upper() in estimated_materials
            and i["item_code"] not in ph_codes_kg
        }
        assert "PS2" not in estimated

    def test_swr_item_with_manual_rate_not_flagged(self):
        """If SWR item was manually saved to per_hour, it must NOT be flagged."""
        pipe_items = self._make_pipe_items()
        ph_rows_with_manual = self._make_ph_rows() + [
            {"item_code": "SWR20", "basis": "kg_per_hr", "value": 180.0}
        ]
        ph_codes_kg = {r["item_code"] for r in ph_rows_with_manual if r["basis"] == "kg_per_hr"}
        estimated_materials = {"SWR", "AGRI"}
        estimated = {
            i["item_code"] for i in pipe_items
            if i["material"].upper() in estimated_materials
            and i["item_code"] not in ph_codes_kg
        }
        assert "SWR20" not in estimated


# ---------------------------------------------------------------------------
# Version isolation — saving for one month must not affect another
# ---------------------------------------------------------------------------

class TestVersionIsolation:
    def test_save_bom_different_months(self, client, monkeypatch):
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        months_saved = []
        monkeypatch.setattr(mp_model, "upsert_single_bom",
                            lambda r: months_saved.append(r.effective_month) or 1)
        for em in ("2026-06", "2026-07"):
            client.post(
                "/machine-planning/data/save/bom",
                data=json.dumps({"item_code": "PS2", "weight_per_pc_kg": 0.05,
                                 "effective_month": em}),
                content_type="application/json",
            )
        assert months_saved == ["2026-06", "2026-07"]

    def test_save_params_prior_month_preserved(self, client, monkeypatch):
        """Saving params for 2026-07 must not touch 2026-06."""
        monkeypatch.setattr(mp_model, "AVAILABLE", True)
        saved_ems = []
        monkeypatch.setattr(mp_model, "upsert_params",
                            lambda r: saved_ems.append(r.effective_month) or 1)
        client.post("/machine-planning/data/save/params",
                    data=json.dumps({"waste_pct": 5.0, "pulverizer_pct": 28.0,
                                     "effective_month": "2026-07"}),
                    content_type="application/json")
        assert saved_ems == ["2026-07"]
        assert "2026-06" not in saved_ems
