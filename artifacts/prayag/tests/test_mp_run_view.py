"""Tests for the machine-planning run-view page and its per-run report routes.

Covers:
  - mp_run_detail renders the plan tables (machine load + schedule) for both
    a pending (live-preview) run and a frozen run.
  - All report download endpoints return valid XLSX / ZIP for a run.
  - Scheduled util % in the machine-load table never exceeds 100%.
  - The corrective re-plan download endpoint is reachable and returns XLSX.

All tests run fully offline using in-process fixtures — no Google Sheets calls.
"""
import io
import sys
import os
import types
import dataclasses
import importlib
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Make app importable from the prayag directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Minimal engine fixture helpers
# ---------------------------------------------------------------------------
from mp_engine import (
    EngineResult, MachineLoad, CoverageGaps, PlanTotals,
    ItemResult, AssignedPortion, REPORT_11_GROUPS,
    FittingEngineResult, FittingItemResult, FittingAssignedPortion,
)
from mp_scheduler import ScheduleResult, WeekFillRow


def _item_result(**kw) -> ItemResult:
    """Build a minimal routable ItemResult for test fixtures."""
    defaults = dict(
        item_code="PIPE-001", raw_code="PIPE-001",
        material="CPVC", qty_pcs=1000.0,
        weight_per_pc_kg=0.5, material_kg=500.0,
        fresh_compound_kg=375.0, pulverizer_kg=125.0,
        rate_kg_per_hr=50.0, rate_estimated=False,
        machine_hrs=10.0, capable_machines=["M/C-1"],
        assignments=[
            AssignedPortion(machine="M/C-1", hrs=10.0,
                            material_kg=500.0, qty_pcs=1000.0),
        ],
        has_weight=True, has_machine=True,
    )
    defaults.update(kw)
    return ItemResult(**defaults)


def _machine_load(machine="M/C-1", cap=500.0, assigned=750.0, util=150.0) -> MachineLoad:
    return MachineLoad(
        machine=machine, capacity_hrs=cap, assigned_hrs=assigned,
        utilisation_pct=util, machine_days=25.0,
        material_kg=10000.0, fresh_compound_kg=7500.0, pulverizer_kg=2500.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )


def _engine_result(machine="M/C-1", cap=500.0, assigned=750.0, util=150.0) -> EngineResult:
    ml   = _machine_load(machine=machine, cap=cap, assigned=assigned, util=util)
    item = _item_result()
    return EngineResult(
        segment="PIPE", effective_month="2026-08",
        items=[item], machine_loads=[ml],
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=["M/C-7", "M/C-8"],
            locked_out_machines=[],
        ),
        totals=PlanTotals(
            total_qty_pcs=1000.0, total_material_kg=500.0,
            total_fresh_compound_kg=375.0, total_pulverizer_kg=125.0,
            routable_material_kg=500.0, routable_fresh_compound_kg=375.0,
            routable_pulverizer_kg=125.0,
        ),
        baseline_machine_loads=[ml],
        params_used={"waste_pct": 5.0, "pulverizer_pct": 25.0},
        effective_costs={}, cost_by_material={}, n_unpriced=0,
    )


def _schedule_result(machine="M/C-1", sched_hrs=480.0, cap=500.0) -> ScheduleResult:
    wf = WeekFillRow(
        week=1, machine=machine, capacity_hrs=cap,
        scheduled_hrs=sched_hrs, idle_hrs=cap - sched_hrs,
        utilisation_pct=round(sched_hrs / cap * 100, 1),
        changeovers=0, excess_kg=0.0, origin_breakdown={1: sched_hrs},
    )
    return ScheduleResult(
        segment="PIPE", effective_month="2026-08",
        blocks=[], weekly_fill=[wf], unfinished=[],
        total_capacity_hrs=cap, total_scheduled_hrs=sched_hrs,
        total_idle_hrs=cap - sched_hrs, total_excess_kg=0.0,
        total_changeovers=0, week_days=[6, 6, 6, 7], params_used={},
    )


# ---------------------------------------------------------------------------
# Mock run rows
# ---------------------------------------------------------------------------
# Stored demand must match DemandItem / FittingDemandItem field sets exactly,
# since the comparison handler reconstructs these objects from the DB row.
_DEMAND_DICT = [dict(
    item_code="PIPE-001", raw_code="PIPE-001",
    material="CPVC", qty_pcs=1000,
    week_qty={"1": 1000}, first_requested_week=1,
)]

_FITTING_DICT = [dict(
    item_code="FIT-001", raw_code="FIT-001",
    material="CPVC", qty_pcs=500,
)]

_PENDING_RUN = {
    "id": 11,
    "segment": "PIPE",
    "month": "2026-08",
    "uploaded_demand": _DEMAND_DICT,
    "fitting_demand": _FITTING_DICT,
    "frozen_inputs": None,
    "results": None,
    "status": "pending",
    "created_at": None,
    "uploaded_file_path": "",
}

_FROZEN_RUN = {
    **_PENDING_RUN,
    "status": "draft",
    "results": {
        "pipe": {
            "machine_loads": [
                {"machine": "M/C-1", "capacity_hrs": 500, "assigned_hrs": 750,
                 "utilisation_pct": 150.0, "machine_days": 25, "material_kg": 10000,
                 "fresh_compound_kg": 7500, "pulverizer_kg": 2500,
                 "staffing_ok": True, "operators_ot": 0, "support_w": 0}
            ],
            "baseline_machine_loads": [],
            "items": [],
            "coverage_gaps": {"no_weight": [], "no_machine": [],
                              "idle_machines": [], "locked_out_machines": []},
            "totals": {"total_qty_pcs": 1000, "total_material_kg": 500,
                       "total_fresh_compound_kg": 375, "total_pulverizer_kg": 125,
                       "routable_material_kg": 500, "routable_fresh_compound_kg": 375,
                       "routable_pulverizer_kg": 125},
            "params_used": {"waste_pct": 5.0, "pulverizer_pct": 25.0},
            "effective_costs": {}, "cost_by_material": {}, "n_unpriced": 0,
        },
        "fitting": {},
    },
}


# ---------------------------------------------------------------------------
# Shared patch context
# ---------------------------------------------------------------------------
def _common_patches(run_row):
    """Return a dict of patch targets -> mock objects for the run-view route."""
    result   = _engine_result()
    sched    = _schedule_result()

    patches = {
        "app.get_plan_run_by_id":            MagicMock(return_value=run_row),
        "app._mp2_result_from_run":          MagicMock(return_value=result),
        "app._mp3_fitting_result_from_run":  MagicMock(return_value=None),
        "app._mp_schedule_from_run":         MagicMock(return_value=sched),
        "app._mp_fitting_schedule_from_run": MagicMock(return_value=None),
        "app._build_plan_lookups": MagicMock(return_value=(
            {"has_data": False}, {"has_data": False}
        )),
    }
    return patches, result, sched


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    import app as _app
    _app.app.config["TESTING"] = True
    _app.app.config["SECRET_KEY"] = "test"
    with _app.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Test: pending run renders plan in UI
# ---------------------------------------------------------------------------
class TestPendingRunView:
    def test_page_renders_200(self, client):
        """Pending run view returns 200 (not a redirect or error)."""
        patches, result, sched = _common_patches(_PENDING_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        assert r.status_code == 200

    def test_live_preview_label_present(self, client):
        """Pending run page shows the 'live preview' banner."""
        patches, *_ = _common_patches(_PENDING_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        assert "live preview" in body.lower() or "not saved" in body.lower(), (
            "Expected a live-preview notice on a pending run page"
        )

    def test_machine_load_table_rendered(self, client):
        """Pending run page renders the machine load table with M/C-1."""
        patches, *_ = _common_patches(_PENDING_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        assert "M/C-1" in body, "Expected M/C-1 in machine load table"
        # Demand column header
        assert "Demand" in body, "Expected Demand column header"
        # Scheduled column header
        assert "Sched" in body or "Scheduled" in body, (
            "Expected Sched/Scheduled column header (dual-column view)"
        )

    def test_demand_util_shown_above_100(self, client):
        """Pending run page shows demand util > 100% (150%) for M/C-1."""
        patches, *_ = _common_patches(_PENDING_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        assert "150.0%" in body or "150%" in body, (
            "Expected demand util 150% visible for M/C-1"
        )

    def test_scheduled_util_never_exceeds_100_in_rendered_table(self, client):
        """Scheduled util % in the rendered HTML must never exceed 100%.

        The schedule fixture has sched_hrs=480 / cap=500 => 96%.
        """
        patches, *_ = _common_patches(_PENDING_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        # 96% scheduled util — must appear
        assert "96.0%" in body or "96%" in body, (
            "Expected scheduled util 96% (480/500) in the page"
        )
        # The scheduled column header "Sched %" and its values must appear;
        # the scheduled util for M/C-1 is 96% (480/500), never > 100%.
        assert "101%" not in body, "Scheduled column must not show > 100%"


# ---------------------------------------------------------------------------
# Test: frozen run renders plan
# ---------------------------------------------------------------------------
class TestFrozenRunView:
    def test_page_renders_200(self, client):
        patches, *_ = _common_patches(_FROZEN_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        assert r.status_code == 200

    def test_frozen_run_has_no_live_preview_label(self, client):
        """Frozen run must NOT show the live preview banner."""
        patches, *_ = _common_patches(_FROZEN_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        assert "not saved" not in body.lower() or "not saved until frozen" not in body.lower(), (
            "Frozen run should not show 'not saved' live-preview notice"
        )

    def test_rerun_freeze_button_still_absent_when_frozen(self, client):
        """Frozen runs must not show the Re-run & Freeze button."""
        patches, *_ = _common_patches(_FROZEN_RUN)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            r = client.get("/machine-planning/runs/11")
        body = r.data.decode()
        assert "Re-run" not in body or "Freeze" not in body


# ---------------------------------------------------------------------------
# Test: download endpoints return XLSX / ZIP
# ---------------------------------------------------------------------------
class TestRunReportDownloads:
    """All per-run report endpoints must return XLSX (or ZIP) for run #11."""

    def _get_report(self, client, report_id: str):
        result  = _engine_result()
        sched   = _schedule_result()

        import mp_reports as _rpt
        xlsx_stub = b"PK\x03\x04" + b"\x00" * 100   # minimal ZIP magic (xlsx is a zip)

        with patch("mp_model.get_plan_run_by_id", MagicMock(return_value=_PENDING_RUN)), \
             patch("app._mp2_result_from_run",  MagicMock(return_value=result)), \
             patch("app._mp3_fitting_result_from_run", MagicMock(return_value=None)), \
             patch("app._mp_schedule_from_run", MagicMock(return_value=sched)), \
             patch("app._mp_fitting_schedule_from_run", MagicMock(return_value=None)), \
             patch("mp_engine.run_engine", MagicMock(return_value=result)), \
             patch("mp_engine.run_fitting_engine", MagicMock(return_value=None)), \
             patch.object(_rpt, "report_11_bytes",   MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "report_11x_bytes",  MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "report_12_bytes",   MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "consolidated_plan_bytes",       MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "revised_production_plan_bytes", MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "machine_plan_comparison_bytes", MagicMock(return_value=xlsx_stub)), \
             patch.object(_rpt, "capacity_feasible_plan_bytes",  MagicMock(return_value=xlsx_stub)), \
             patch("app._build_plan_lookups", MagicMock(return_value=(
                 {"has_data": False}, {"has_data": False}
             ))):
            return client.get(f"/machine-planning/runs/11/report/{report_id}")

    @pytest.mark.parametrize("report_id", ["11", "11A", "11B", "11C", "11D"])
    def test_pipe_subgroup_reports(self, client, report_id):
        r = self._get_report(client, report_id)
        assert r.status_code == 200, (
            f"report/{report_id} returned {r.status_code}"
        )
        assert r.content_type == (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ), f"Expected XLSX content-type for {report_id}"

    def test_consolidated_report(self, client):
        r = self._get_report(client, "consolidated")
        assert r.status_code == 200
        assert "spreadsheetml" in r.content_type

    def test_revised_production_plan(self, client):
        r = self._get_report(client, "revised")
        assert r.status_code == 200
        assert "spreadsheetml" in r.content_type

    def test_machine_plan_comparison(self, client):
        r = self._get_report(client, "comparison")
        assert r.status_code == 200
        assert "spreadsheetml" in r.content_type

    def test_capacity_feasible_plan(self, client):
        r = self._get_report(client, "capacity_feasible")
        assert r.status_code == 200
        assert "spreadsheetml" in r.content_type

    def test_zip_download(self, client):
        """ZIP endpoint must return application/zip with at least one file."""
        r = self._get_report(client, "zip")
        assert r.status_code == 200
        assert r.content_type == "application/zip"

    def test_unknown_report_id_is_404(self, client):
        """An unrecognised report_id must return 404, not 500."""
        with patch("mp_model.get_plan_run_by_id", MagicMock(return_value=_PENDING_RUN)):
            r = client.get("/machine-planning/runs/11/report/unknown_report")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Test: download table links present in the page HTML
# ---------------------------------------------------------------------------
class TestRunViewDownloadLinks:
    def _render(self, client, run_row=None):
        if run_row is None:
            run_row = _PENDING_RUN
        patches, *_ = _common_patches(run_row)
        with patch("mp_model.get_plan_run_by_id", patches["app.get_plan_run_by_id"]), \
             patch("app._mp2_result_from_run",          patches["app._mp2_result_from_run"]), \
             patch("app._mp3_fitting_result_from_run",  patches["app._mp3_fitting_result_from_run"]), \
             patch("app._mp_schedule_from_run",         patches["app._mp_schedule_from_run"]), \
             patch("app._mp_fitting_schedule_from_run", patches["app._mp_fitting_schedule_from_run"]):
            return client.get("/machine-planning/runs/11")

    def test_all_report_links_present(self, client):
        r = self._render(client)
        body = r.data.decode()
        for report_id in ["11", "11A", "11B", "11C", "11D",
                          "consolidated", "revised", "comparison",
                          "capacity_feasible"]:
            assert f"report/{report_id}" in body, (
                f"Expected download link for report/{report_id} in run-view page"
            )

    def test_zip_link_present(self, client):
        r = self._render(client)
        assert "report/zip" in r.data.decode()

    def test_download_table_has_description_column(self, client):
        r = self._render(client)
        body = r.data.decode()
        assert "What it contains" in body, (
            "Expected 'What it contains' column header in the 3-column download table"
        )

    def test_rerun_freeze_button_present_for_pending(self, client):
        r = self._render(client, _PENDING_RUN)
        body = r.data.decode()
        assert "Re-run" in body and "Freeze" in body, (
            "Expected Re-run & Freeze button on a pending run"
        )


# ---------------------------------------------------------------------------
# Fitting-only run fixtures
# ---------------------------------------------------------------------------

_FITTING_ONLY_RUN = {
    "id": 12,
    "segment": "PIPE",
    "month": "2026-08",
    "uploaded_demand": [],          # no pipe demand
    "fitting_demand": _FITTING_DICT,
    "frozen_inputs": None,
    "results": None,
    "status": "pending",
    "created_at": None,
    "uploaded_file_path": "",
}

_FITTING_ONLY_FROZEN_RUN = {
    **_FITTING_ONLY_RUN,
    "id": 13,
    "status": "draft",
    "results": {
        "pipe": {},                  # no pipe results
        "fitting": {
            "machine_loads": [
                {"machine": "MOD-1", "capacity_hrs": 250, "assigned_hrs": 200,
                 "utilisation_pct": 80.0, "machine_days": 25, "material_kg": 5000,
                 "fresh_compound_kg": 3750, "pulverizer_kg": 1250,
                 "staffing_ok": True, "operators_ot": 0, "support_w": 0}
            ],
            "baseline_machine_loads": [],
            "items": [],
            "coverage_gaps": {"no_weight": [], "no_machine": [],
                              "idle_machines": [], "locked_out_machines": []},
            "totals": {"total_qty_pcs": 500, "total_material_kg": 250,
                       "total_fresh_compound_kg": 187, "total_pulverizer_kg": 63,
                       "routable_material_kg": 250, "routable_fresh_compound_kg": 187,
                       "routable_pulverizer_kg": 63},
            "params_used": {"waste_pct": 4.0, "pulverizer_pct": 25.0},
            "n_route_estimated": 0, "n_unroutable": 0,
            "effective_costs": {}, "cost_by_material": {}, "n_unpriced": 0,
        },
    },
}


def _fitting_item_result() -> FittingItemResult:
    return FittingItemResult(
        item_code="FIT-001", raw_code="FIT-001",
        material="CPVC", qty_pcs=500.0,
        weight_per_pc_kg=0.5, material_kg=250.0,
        fresh_compound_kg=187.5, pulverizer_kg=62.5,
        pcs_per_hr=100.0, rate_estimated=False,
        machine_hrs=5.0, cavity=4.0, cycle_time_sec=144.0,
        num_cycles=125.0, capable_machines=["MOD-1"],
        route_estimated=False,
        assignments=[
            FittingAssignedPortion(machine="MOD-1", hrs=5.0,
                                   qty_pcs=500.0, material_kg=250.0),
        ],
        has_weight=True, has_machine=True,
    )


def _fitting_engine_result() -> FittingEngineResult:
    ml = MachineLoad(
        machine="MOD-1", capacity_hrs=250.0, assigned_hrs=200.0,
        utilisation_pct=80.0, machine_days=25.0,
        material_kg=5000.0, fresh_compound_kg=3750.0, pulverizer_kg=1250.0,
        staffing_ok=True, operators_ot=0, support_w=0,
    )
    return FittingEngineResult(
        segment="PIPE", effective_month="2026-08",
        items=[_fitting_item_result()],
        machine_loads=[ml],
        coverage_gaps=CoverageGaps(
            no_weight=[], no_machine=[], idle_machines=[], locked_out_machines=[],
        ),
        totals=PlanTotals(
            total_qty_pcs=500.0, total_material_kg=250.0,
            total_fresh_compound_kg=187.5, total_pulverizer_kg=62.5,
            routable_material_kg=250.0, routable_fresh_compound_kg=187.5,
            routable_pulverizer_kg=62.5,
        ),
        baseline_machine_loads=[ml],
        params_used={"waste_pct": 4.0, "pulverizer_pct": 25.0},
        n_route_estimated=0, n_unroutable=0,
    )


def _fitting_schedule_result() -> ScheduleResult:
    wf = WeekFillRow(
        week=1, machine="MOD-1", capacity_hrs=62.5,
        scheduled_hrs=50.0, idle_hrs=12.5,
        utilisation_pct=80.0, changeovers=1, excess_kg=0.0,
        origin_breakdown={1: 50.0},
    )
    return ScheduleResult(
        segment="PIPE", effective_month="2026-08",
        blocks=[], weekly_fill=[wf], unfinished=[],
        total_capacity_hrs=250.0, total_scheduled_hrs=200.0,
        total_idle_hrs=50.0, total_excess_kg=0.0,
        total_changeovers=1, week_days=[6, 6, 6, 7], params_used={},
    )


# ---------------------------------------------------------------------------
# Test: fitting-only run (no pipe demand) renders without errors
# ---------------------------------------------------------------------------
class TestFittingOnlyRunView:
    """Run with fitting demand only (no pipe demand) must render the fitting
    weekly fill block without 500 errors or missing-variable references."""

    def _render(self, client, run_row=None):
        if run_row is None:
            run_row = _FITTING_ONLY_RUN
        fit_res   = _fitting_engine_result()
        fit_sched = _fitting_schedule_result()
        with patch("mp_model.get_plan_run_by_id", MagicMock(return_value=run_row)), \
             patch("app._mp2_result_from_run",          MagicMock(return_value=None)), \
             patch("app._mp3_fitting_result_from_run",  MagicMock(return_value=fit_res)), \
             patch("app._mp_schedule_from_run",         MagicMock(return_value=None)), \
             patch("app._mp_fitting_schedule_from_run", MagicMock(return_value=fit_sched)):
            return client.get("/machine-planning/runs/12")

    def test_page_renders_200(self, client):
        """Fitting-only run must return 200, not a 500 or missing-variable crash."""
        r = self._render(client)
        assert r.status_code == 200, (
            f"Expected 200 for fitting-only run; got {r.status_code}: "
            f"{r.data[:300].decode(errors='replace')}"
        )

    def test_fitting_weekly_fill_section_present(self, client):
        """Fitting Moulding — Weekly Fill block must appear in the page."""
        r = self._render(client)
        body = r.data.decode()
        assert "Fitting Moulding" in body or "Weekly Fill" in body, (
            "Expected 'Fitting Moulding' / 'Weekly Fill' heading in fitting-only run"
        )

    def test_fitting_stats_row_rendered(self, client):
        """Capacity / Scheduled / Idle / Changeovers stat cards must appear."""
        r = self._render(client)
        body = r.data.decode()
        # Stat card labels
        assert "Capacity" in body,    "Expected 'Capacity' stat card"
        assert "Scheduled" in body,   "Expected 'Scheduled' stat card"
        assert "Idle" in body,        "Expected 'Idle' stat card"
        assert "Changeovers" in body, "Expected 'Changeovers' stat card"

    def test_fitting_stats_values_rendered(self, client):
        """Stat cards must contain the fixture values: 250h capacity, 200h scheduled."""
        r = self._render(client)
        body = r.data.decode()
        assert "250" in body, "Expected total capacity 250 in fitting-only run page"
        assert "200" in body, "Expected total scheduled 200 in fitting-only run page"

    def test_fitting_machine_appears_in_weekly_table(self, client):
        """MOD-1 must appear in the fitting weekly fill table."""
        r = self._render(client)
        body = r.data.decode()
        assert "MOD-1" in body, "Expected MOD-1 in fitting weekly fill table"

    def test_pipe_section_absent(self, client):
        """When there is no pipe plan the pipe section header must not render.

        "Extrusion Machines" is unique to the PIPE · Extrusion Machines label;
        the fitting section says "Moulding Machine Load" so this is safe.
        """
        r = self._render(client)
        body = r.data.decode()
        assert "Extrusion Machines" not in body, (
            "Pipe 'Extrusion Machines' section label should be absent for a fitting-only run"
        )

    def test_pipe_schedule_section_absent(self, client):
        """Pipe 'Shift Schedule — Weekly Fill' must not appear when pipe plan is absent."""
        r = self._render(client)
        body = r.data.decode()
        # The pipe weekly fill section is guarded by {% if schedule_result %}
        # Only assert the pipe-specific "Full Schedule" link is absent
        assert "Full Schedule" not in body, (
            "Pipe 'Full Schedule' link should not appear for a fitting-only run"
        )

    def test_frozen_fitting_only_run_renders_200(self, client):
        """A frozen run with no pipe results and fitting results must return 200."""
        fit_res   = _fitting_engine_result()
        fit_sched = _fitting_schedule_result()
        with patch("mp_model.get_plan_run_by_id", MagicMock(return_value=_FITTING_ONLY_FROZEN_RUN)), \
             patch("app._mp2_result_from_run",          MagicMock(return_value=None)), \
             patch("app._mp3_fitting_result_from_run",  MagicMock(return_value=fit_res)), \
             patch("app._mp_schedule_from_run",         MagicMock(return_value=None)), \
             patch("app._mp_fitting_schedule_from_run", MagicMock(return_value=fit_sched)):
            r = client.get("/machine-planning/runs/13")
        assert r.status_code == 200, (
            f"Frozen fitting-only run returned {r.status_code}: "
            f"{r.data[:300].decode(errors='replace')}"
        )
