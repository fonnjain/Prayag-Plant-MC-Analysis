"""JSON API (v1) — lets external apps consume dashboard data and request
non-persistent Plumbing schedule previews.

Design rules (mirror the app's invariants exactly):
  * Every figure is produced by the SAME pipeline the dashboard uses
    (``get_data`` → daily-first read → compute_metrics → confirmation), so the
    API can never disagree with the UI.
  * No fake 0%: a ratio without a real baseline/denominator serializes as
    ``null`` (never 0). ``*_available`` flags say why.
  * Data-confirmation gating is FIRST-CLASS in the payload: consumers get
    ``confirmation.status`` and ``figures_gated`` so an error-gated period is
    never mistaken for clean data.
   * No route persists or mutates production/planning data.  The schedule preview
     computes from the current MP master data and returns a transient result.

Auth: every data endpoint requires the ``PRAYAG_API_KEY`` secret, supplied as
an ``X-API-Key`` header (preferred), ``Authorization: Bearer <key>``, or an
``api_key`` query param. If the secret is not configured the API answers 503 —
it is never silently open.
"""
from __future__ import annotations

import dataclasses
import datetime
import hmac
import math
import os
import re
from functools import wraps
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from metrics import (
    MetricsResult,
    rollup_by_date,
    rollup_by_machine,
    rollup_by_plant,
    rollup_by_segment,
)
from sheets import SheetReadError, months_with_data
from sources import DAILY_SOURCES, PLANT_LOCATIONS, PLANT_NAMES
import sheets as _sheets
import mp_corrective_replan as _mp_corrective_replan
import mp_engine as _mp_engine
import mp_model as _mp_model
import mp_rejection_plan as _mp_rejection_plan
import mp_scheduler as _mp_scheduler
import mp_wastage as _mp_wastage


API_KEY_ENV = "PRAYAG_API_KEY"

# Query params forwarded verbatim to the dashboard pipeline. Everything else is
# ignored so a consumer cannot reach internal knobs by accident.
_ALLOWED_ARGS = ("period", "plant", "segment", "machine", "from_date", "to_date")

_PERIOD_TOKENS = [
    "last_updated", "yesterday", "last_week", "last_month",
    "current_fy", "prior_fy", "q1", "q2", "q3", "q4", "custom",
    "YYYY-MM (exact calendar month)", "YYYY-MM-DD (single day)",
    "1..12 (fiscal-year month number, Apr=4 anchored to the current FY)",
]
_SCHEDULE_MATERIALS = frozenset({"CPVC", "UPVC", "SWR", "AGRI"})
_SCHEDULE_KINDS = frozenset({"pipe", "fitting"})
_CORRECTIVE_KINDS = frozenset({"pipe", "fitting", "solvent"})
_CORRECTIVE_CATEGORIES = frozenset(_mp_corrective_replan.CATEGORY_ORDER)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class SchedulePreviewError(ValueError):
    """A request cannot be converted into a safe schedule preview."""


class PlanningDataUnavailable(RuntimeError):
    """The MP master data required for a schedule preview is unavailable."""


class ScheduleMachinePoolConflict(RuntimeError):
    """A machine is configured in both independent Plumbing schedule pools."""


class CorrectivePreviewError(ValueError):
    """A request cannot be converted into a safe corrective preview."""


class CorrectiveActualsUnavailable(RuntimeError):
    """Report-11/12 could not both be read for a corrective preview."""


class CorrectiveComputationError(RuntimeError):
    """The corrective engine failed after request validation."""


@dataclasses.dataclass(frozen=True)
class _SchedulePreviewRequest:
    """Validated, engine-ready input for one non-persistent schedule preview."""

    month: str
    kind: str
    week_days: list[int]
    demand: list[dict[str, Any]]


@dataclasses.dataclass(frozen=True)
class _CorrectivePreviewRequest:
    """Validated input for one non-persistent corrective re-plan preview."""

    month: str
    as_of_date: str
    week_days: list[int]
    plan_recs: list[Any]
    cap_feasible_by_cat: dict[str, float] | None


@dataclasses.dataclass(frozen=True)
class _CorrectivePlanRecord:
    """Minimal PlanRecord-compatible row consumed by the corrective engine."""

    item_code: str
    family: str
    category: str
    produce_required: float
    produced: float
    ideal_qty: float = 0.0
    closing_stock: float = 0.0


def _positive_number(value: Any, field: str, line_number: int) -> float:
    """Return a finite positive request number or raise a client-safe error."""
    if isinstance(value, bool):
        raise SchedulePreviewError(
            f"demand[{line_number}].{field} must be a positive number."
        )
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SchedulePreviewError(
            f"demand[{line_number}].{field} must be a positive number."
        ) from None
    if number <= 0 or number == float("inf") or number != number:
        raise SchedulePreviewError(
            f"demand[{line_number}].{field} must be a positive finite number."
        )
    return number


def _nonnegative_number(value: Any, field: str) -> float:
    """Return a finite non-negative request number or raise a client-safe error."""
    if isinstance(value, bool):
        raise CorrectivePreviewError(f"{field} must be a non-negative number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CorrectivePreviewError(f"{field} must be a non-negative number.") from None
    if number < 0 or not math.isfinite(number):
        raise CorrectivePreviewError(
            f"{field} must be a non-negative finite number."
        )
    return number


def _parse_schedule_preview_request(payload: Any) -> _SchedulePreviewRequest:
    """Validate a Plumbing preview request and extract engine-owned fields.

    The planning app may retain presentation/provenance fields such as colour,
    urgency, category, or a supplied weight.  Only the fields the engine owns
    are consumed here; BOM weight is always resolved from the MP master data.
    """
    if not isinstance(payload, dict):
        raise SchedulePreviewError("Request body must be a JSON object.")

    if payload.get("segment") != "PLUMBING":
        raise SchedulePreviewError("segment must be exactly 'PLUMBING'.")

    month = payload.get("month")
    if not isinstance(month, str) or not _MONTH_RE.fullmatch(month):
        raise SchedulePreviewError("month must be in YYYY-MM format.")
    try:
        _mp_model.calendar_days_in_month(month)
    except Exception:
        raise SchedulePreviewError("month must be a valid calendar month in YYYY-MM format.") from None

    kind = payload.get("kind")
    if not isinstance(kind, str) or kind.lower() not in _SCHEDULE_KINDS:
        raise SchedulePreviewError("kind must be either 'pipe' or 'fitting'.")
    kind = kind.lower()

    if "week_days" not in payload:
        raise SchedulePreviewError("week_days is required for a schedule preview.")
    try:
        week_days = _mp_model.validate_week_days(payload["week_days"], month)
    except Exception as exc:
        raise SchedulePreviewError(str(exc)) from None

    lines = payload.get("demand")
    if not isinstance(lines, list) or not lines:
        raise SchedulePreviewError("demand must be a non-empty array.")

    demand: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines):
        if not isinstance(line, dict):
            raise SchedulePreviewError(f"demand[{line_number}] must be an object.")
        supplied_code = line.get("item_code")
        if not isinstance(supplied_code, str) or not supplied_code.strip():
            raise SchedulePreviewError(f"demand[{line_number}].item_code is required.")
        item_code = _mp_engine._norm_code(supplied_code)
        if not item_code:
            raise SchedulePreviewError(f"demand[{line_number}].item_code is invalid.")

        raw_code = line.get("raw_code", supplied_code)
        if not isinstance(raw_code, str) or not raw_code.strip():
            raise SchedulePreviewError(f"demand[{line_number}].raw_code must be text when supplied.")

        material = line.get("material")
        if not isinstance(material, str) or material.upper() not in _SCHEDULE_MATERIALS:
            allowed = ", ".join(sorted(_SCHEDULE_MATERIALS))
            raise SchedulePreviewError(
                f"demand[{line_number}].material must be one of {allowed}."
            )
        demand.append({
            "item_code": item_code,
            "raw_code": raw_code.strip(),
            "material": material.upper(),
            "qty_pcs": _positive_number(line.get("qty_pcs"), "qty_pcs", line_number),
        })

    return _SchedulePreviewRequest(
        month=month, kind=kind, week_days=week_days, demand=demand,
    )


def _parse_corrective_preview_request(payload: Any) -> _CorrectivePreviewRequest:
    """Validate and adapt caller-owned demand for the corrective engine."""
    if not isinstance(payload, dict):
        raise CorrectivePreviewError("Request body must be a JSON object.")
    if payload.get("segment") != "PLUMBING":
        raise CorrectivePreviewError("segment must be exactly 'PLUMBING'.")

    month = payload.get("month")
    if not isinstance(month, str) or not _MONTH_RE.fullmatch(month):
        raise CorrectivePreviewError("month must be in YYYY-MM format.")
    try:
        _mp_model.calendar_days_in_month(month)
    except Exception:
        raise CorrectivePreviewError(
            "month must be a valid calendar month in YYYY-MM format."
        ) from None

    as_of_date = payload.get("as_of_date")
    if not isinstance(as_of_date, str):
        raise CorrectivePreviewError("as_of_date must be in YYYY-MM-DD format.")
    try:
        as_of = datetime.date.fromisoformat(as_of_date)
    except ValueError:
        raise CorrectivePreviewError(
            "as_of_date must be a valid date in YYYY-MM-DD format."
        ) from None
    if as_of.strftime("%Y-%m") != month:
        raise CorrectivePreviewError(
            "as_of_date must fall inside the selected month."
        )

    if "week_days" not in payload:
        raise CorrectivePreviewError(
            "week_days is required for a corrective re-plan preview."
        )
    try:
        week_days = _mp_model.validate_week_days(payload["week_days"], month)
    except Exception as exc:
        raise CorrectivePreviewError(str(exc)) from None

    lines = payload.get("demand")
    if not isinstance(lines, list) or not lines:
        raise CorrectivePreviewError("demand must be a non-empty array.")

    plan_recs: list[_CorrectivePlanRecord] = []
    for line_number, line in enumerate(lines):
        if not isinstance(line, dict):
            raise CorrectivePreviewError(f"demand[{line_number}] must be an object.")

        supplied_code = line.get("item_code")
        if not isinstance(supplied_code, str) or not supplied_code.strip():
            raise CorrectivePreviewError(
                f"demand[{line_number}].item_code is required."
            )
        item_code = _mp_engine._norm_code(supplied_code)
        if not item_code:
            raise CorrectivePreviewError(
                f"demand[{line_number}].item_code is invalid."
            )

        material = line.get("material")
        if not isinstance(material, str) or material.upper() not in _SCHEDULE_MATERIALS:
            allowed = ", ".join(sorted(_SCHEDULE_MATERIALS))
            raise CorrectivePreviewError(
                f"demand[{line_number}].material must be one of {allowed}."
            )
        material = material.upper()

        category = line.get("category")
        if not isinstance(category, str) or category.strip().lower() not in _CORRECTIVE_KINDS:
            allowed = ", ".join(sorted(_CORRECTIVE_KINDS))
            raise CorrectivePreviewError(
                f"demand[{line_number}].category must be one of {allowed}."
            )
        category = category.strip().lower()

        try:
            qty_pcs = _positive_number(
                line.get("qty_pcs"), "qty_pcs", line_number,
            )
        except SchedulePreviewError as exc:
            raise CorrectivePreviewError(str(exc)) from None
        produced_to_date = _nonnegative_number(
            line.get("produced_to_date_pcs"),
            f"demand[{line_number}].produced_to_date_pcs",
        )
        plan_recs.append(_CorrectivePlanRecord(
            item_code=item_code,
            family=material,
            category=category.title(),
            produce_required=qty_pcs,
            produced=produced_to_date,
        ))

    cap_feasible_raw = payload.get("cap_feasible_by_cat")
    cap_feasible_by_cat: dict[str, float] | None = None
    if cap_feasible_raw is not None:
        if not isinstance(cap_feasible_raw, dict):
            raise CorrectivePreviewError(
                "cap_feasible_by_cat must be an object keyed by canonical category."
            )
        cap_feasible_by_cat = {}
        for category, value in cap_feasible_raw.items():
            if category not in _CORRECTIVE_CATEGORIES:
                raise CorrectivePreviewError(
                    "cap_feasible_by_cat contains an unknown category: "
                    f"{category!r}."
                )
            cap_feasible_by_cat[category] = _nonnegative_number(
                value, f"cap_feasible_by_cat[{category!r}]",
            )

    return _CorrectivePreviewRequest(
        month=month,
        as_of_date=as_of_date,
        week_days=week_days,
        plan_recs=plan_recs,
        cap_feasible_by_cat=cap_feasible_by_cat,
    )


def _schedule_plan_lookups(month: str) -> tuple[dict, dict]:
    """Build the same rejection and wastage lookup inputs as the planning UI."""
    params_row = _mp_model.get_params("PLUMBING", month)
    override_pct = float(getattr(params_row, "waste_pct", 0.0) or 0.0)
    return (
        _mp_rejection_plan.build_rejection_lookup("PLUMBING"),
        _mp_wastage.build_wastage_lookup("PLUMBING", override_pct=override_pct),
    )


def _require_schedulable_items(
    items: list[Any], machine_names: set[str], kind: str,
) -> None:
    """Reject previews that would otherwise silently drop requested demand."""
    problems: list[str] = []
    for item in items:
        label = str(getattr(item, "raw_code", "") or getattr(item, "item_code", "item"))
        if not getattr(item, "has_weight", False):
            problems.append(f"{label}: no BOM weight")
        elif not getattr(item, "has_machine", False):
            problems.append(f"{label}: no capable {kind} route")
        elif not math.isfinite(float(getattr(item, "machine_hrs", 0.0) or 0.0)):
            problems.append(f"{label}: quantity exceeds the safe scheduling range")
        elif float(getattr(item, "machine_hrs", 0.0) or 0.0) <= 0:
            problems.append(f"{label}: no usable production rate")
        elif not (set(getattr(item, "capable_machines", []) or []) & machine_names):
            problems.append(f"{label}: route has no active {kind} machine")
    if problems:
        raise SchedulePreviewError(
            "Requested demand cannot be scheduled from the current Plumbing "
            "master: " + "; ".join(problems) + "."
        )


def run_schedule_preview(payload: Any) -> _mp_scheduler.ScheduleResult:
    """Run one transient, capacity-feasible Plumbing schedule preview.

    This deliberately reads master data and downtime live but does not create a
    plan run, cache a result, write plan lines, or mutate the saved calendar.
    """
    preview = _parse_schedule_preview_request(payload)
    try:
        extrusion_machines = _mp_model.get_machines(
            "PLUMBING", preview.month, kind="extrusion"
        )
        moulding_machines = _mp_model.get_machines(
            "PLUMBING", preview.month, kind="moulding"
        )
        extrusion_names = {
            str(row.get("machine") or "")
            for row in extrusion_machines if row.get("machine")
        }
        moulding_names = {
            str(row.get("machine") or "")
            for row in moulding_machines if row.get("machine")
        }
        overlap = sorted(extrusion_names & moulding_names)
        if overlap:
            raise ScheduleMachinePoolConflict(
                "Plumbing machine pool overlap detected: " + ", ".join(overlap) +
                ". Pipe and fitting previews cannot be merged safely."
            )

        machines = (
            extrusion_machines if preview.kind == "pipe" else moulding_machines
        )
        if not machines:
            machine_kind_label = (
                "extrusion" if preview.kind == "pipe" else "moulding"
            )
            raise PlanningDataUnavailable(
                f"No {machine_kind_label} machine master data is configured for {preview.month}."
            )
        machine_names = {
            str(row.get("machine") or "") for row in machines if row.get("machine")
        }
        rejection_lookup, wastage_lookup = _schedule_plan_lookups(preview.month)
        downtime_records = _mp_model.get_downtime_affecting_month(
            "PLUMBING", preview.month
        )
        if preview.kind == "pipe":
            demand = [
                _mp_engine.DemandItem(
                    **line, week_qty={}, first_requested_week=1
                )
                for line in preview.demand
            ]
            engine_result = _mp_engine.run_engine(
                demand, preview.month, "PLUMBING",
                rej_lookup=rejection_lookup, wastage_lookup=wastage_lookup,
            )
            _require_schedulable_items(
                engine_result.items, machine_names, "extrusion",
            )
            return _mp_scheduler.run_shift_schedule(
                engine_items=engine_result.items,
                demand_items=demand,
                segment="PLUMBING",
                effective_month=preview.month,
                downtime_records=downtime_records,
                week_days_override=preview.week_days,
            )

        fitting_demand = [
            _mp_engine.FittingDemandItem(**line) for line in preview.demand
        ]
        fitting_result = _mp_engine.run_fitting_engine(
            fitting_demand, preview.month, "PLUMBING",
            rej_lookup=rejection_lookup, wastage_lookup=wastage_lookup,
        )
        _require_schedulable_items(
            fitting_result.items, machine_names, "moulding",
        )
        return _mp_scheduler.run_fitting_schedule(
            fitting_items=fitting_result.items,
            fitting_demand=fitting_demand,
            segment="PLUMBING",
            effective_month=preview.month,
            downtime_records=downtime_records,
            week_days_override=preview.week_days,
        )
    except PlanningDataUnavailable:
        raise
    except ScheduleMachinePoolConflict:
        raise
    except SchedulePreviewError:
        raise
    except Exception as exc:
        raise PlanningDataUnavailable(
            "The Plumbing planning master data could not be read."
        ) from exc


def run_corrective_replan_preview(
    payload: Any,
) -> tuple[_mp_corrective_replan.CorrectiveReplanResult, list[int]]:
    """Run one transient Plumbing pace projection using internally-read actuals."""
    preview = _parse_corrective_preview_request(payload)
    try:
        actuals = _sheets.load_corrective_replan_actuals(preview.month)
    except Exception as exc:
        raise CorrectiveActualsUnavailable(
            f"Report-11 and Report-12 actuals are unavailable for {preview.month}."
        ) from exc

    if not isinstance(actuals, dict) or actuals.get("error"):
        raise CorrectiveActualsUnavailable(
            f"Report-11 and Report-12 actuals are unavailable for {preview.month}."
        )

    try:
        result = _mp_corrective_replan.compute_corrective_replan(
            month=preview.month,
            plan_recs=preview.plan_recs,
            r11_values=actuals.get("r11") or [],
            r12_values=actuals.get("r12") or [],
            as_of_date=preview.as_of_date,
            file_id=str(actuals.get("file_id") or ""),
            cap_feasible_by_cat=preview.cap_feasible_by_cat,
            configured_week_days=preview.week_days,
        )
    except Exception as exc:
        raise CorrectiveComputationError(
            "The Plumbing corrective re-plan could not be computed."
        ) from exc
    return result, preview.week_days


def _corrective_result_json(
    result: _mp_corrective_replan.CorrectiveReplanResult,
    week_days: list[int],
) -> dict:
    """Serialize native result fields plus explicit confidence and unit metadata."""
    payload = dataclasses.asdict(result)
    for category, category_payload in zip(result.categories, payload["categories"]):
        category_payload["low_confidence"] = category.low_confidence
        category_payload["not_started"] = category.not_started
        category_payload["shortfall_pct"] = category.shortfall_pct
    payload["week_days"] = list(week_days)
    payload["min_days_for_p90"] = _mp_corrective_replan.MIN_DAYS_FOR_P90
    payload["units"] = {
        "produced_to_date": "pcs",
        "remaining": "pcs",
        "cap_per_day": "pcs/day",
        "feasible": "pcs",
        "shortfall": "pcs",
        "cap_feasible": "pcs",
    }
    return payload


def _configured_keys() -> list:
    """Return all active API keys (DB rows first, then env-var fallback).

    Multiple DB keys are all valid — any one of them authorises a request.
    The env-var key acts as a fallback for deployments without a database.
    """
    try:
        import store as _store  # local import to avoid circular import at module load
        db_keys = _store.get_all_api_keys()
        if db_keys:
            return [k.strip() for k in db_keys if k and k.strip()]
    except Exception:
        pass
    env_key = (os.environ.get(API_KEY_ENV) or "").strip()
    return [env_key] if env_key else []


def _supplied_key() -> str:
    hdr = (request.headers.get("X-API-Key") or "").strip()
    if hdr:
        return hdr
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (request.args.get("api_key") or "").strip()


def _require_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        keys = _configured_keys()
        if not keys:
            return jsonify({
                "error": "api_disabled",
                "message": (
                    f"The API is not enabled: no API key has been configured. "
                    "Generate one from the dashboard Settings → API Key page."
                ),
            }), 503
        supplied = _supplied_key()
        if not supplied or not any(hmac.compare_digest(supplied, k) for k in keys):
            return jsonify({
                "error": "unauthorized",
                "message": "Missing or invalid API key. Send it as an "
                           "'X-API-Key' header.",
            }), 401
        return fn(*a, **kw)
    return wrapper


def _metrics_json(m: MetricsResult) -> dict:
    """Serialize a MetricsResult honouring the no-fake-0% invariant.

    ``to_dict`` reports every ratio numerically (0 when uncomputable); the API
    replaces any ratio without a real baseline with ``null`` so a consumer can
    never mistake "not measurable" for "0%".
    """
    d = m.to_dict()
    if not d.get("oee_available"):
        d["oee"] = None
        d["availability"] = None
        d["performance"] = None
        d["quality"] = None
    if not d.get("util_available"):
        d["utilisation"] = None
    if not d.get("eff_available"):
        d["output_efficiency"] = None
    if not d.get("headline_available"):
        d["headline"] = None
        d["headline_rating"] = None
    if not d.get("planned_output"):
        d["attainment"] = None
    # Rejection column absent from source tab(s) — numerator was never captured.
    # Suppress rather than display a false 0% ("not captured" ≠ "no rejection").
    if not d.get("rejection_available", True):
        d["rejection_pct"] = None
    if not d.get("total_count"):
        d["rejection_pct"] = None
        d["runner_pct"] = None
    return d


def _record_json(r) -> dict:
    """Serialize a Record row with its provenance, suppressing meaningless ratios
    downstream (the row itself is raw values only — ratios come from metrics)."""
    d = dataclasses.asdict(r)
    # Old L2-cache pickles may lack newer fields; asdict on the dataclass always
    # has them, but guard the dict shape anyway for stability.
    d.setdefault("secondary_counts", {})
    return d


def _confirmation_json(conf: dict) -> dict:
    """Trimmed confirmation block: enough for a consumer to honour the gate."""
    issues = [
        {
            "key": i.get("key"),
            "tier": i.get("tier"),
            "severity": i.get("severity"),
            "message": i.get("message"),
            "plant": i.get("plant"),
            "acknowledged": bool(i.get("acknowledged")),
            "quarantined": bool(i.get("quarantined")),
        }
        for i in (conf.get("issues") or [])
    ]
    signoff = conf.get("signoff")
    return {
        "status": conf.get("status"),
        "counts": conf.get("counts"),
        "released": bool(conf.get("released")),
        "signed_off": bool(signoff),
        "signoff": ({"by": signoff.get("approver"),
                     "at": signoff.get("when_disp")}
                    if isinstance(signoff, dict) else None),
        "fingerprint": conf.get("fingerprint"),
        "issues": issues,
    }


def _figures_gated(conf: dict) -> bool:
    """True when the dashboard would show 'needs review' instead of headline
    figures: an unreleased error-status confirmation."""
    return conf.get("status") == "error" and not conf.get("released")


def _clean_args() -> dict:
    return {k: request.args.get(k) for k in _ALLOWED_ARGS
            if request.args.get(k)}


def create_api(get_data) -> Blueprint:
    """Build the /data-api/v1 blueprint. ``get_data`` is injected from app.py to
    avoid a circular import — the API is a thin JSON view over that pipeline."""
    bp = Blueprint("api", __name__)

    @bp.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = \
            "X-API-Key, Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @bp.errorhandler(SheetReadError)
    def _sheet_err(e):
        return jsonify({
            "error": "source_unavailable",
            "message": f"The production sheets could not be read: {e}",
        }), 502

    # ---- open endpoints -------------------------------------------------
    @bp.get("/")
    def index():
        return jsonify({
            "name": "Prayag Production Analytics API",
            "version": "v1",
            "auth": f"Send the {API_KEY_ENV} value as an 'X-API-Key' header "
                    "(or 'Authorization: Bearer <key>').",
            "endpoints": {
                "GET /data-api/v1/health": "liveness check (no auth)",
                "GET /data-api/v1/plants": "plant roster, locations, daily months wired",
                "GET /data-api/v1/periods": "valid period tokens + months holding data",
                "GET /data-api/v1/summary": "computed figures for a period "
                    "(?period=&plant=&segment=&machine=) — overall metrics, "
                    "per-plant/machine/date rollups, confirmation status",
                "GET /data-api/v1/records": "raw row-level data for a period "
                    "(same filters) — every row with provenance",
                "POST /data-api/v1/schedule": "non-persistent, capacity-feasible "
                    "Plumbing pipe or fitting schedule preview",
                "POST /data-api/v1/corrective-replan": "non-persistent Plumbing "
                    "run-rate projection from internally-read Report-11/12 actuals",
            },
            "period_tokens": _PERIOD_TOKENS,
            "notes": [
                "All figures are recomputed deterministically from the raw "
                "production sheets — stored % cells are never trusted.",
                "A ratio without a real baseline is null, never 0.",
                "Never sum output across units — use output_by_unit.",
                "figures_gated=true means the period has unresolved data-"
                "confirmation errors; treat figures as 'needs review'.",
            ],
        })

    @bp.get("/health")
    def health():
        return jsonify({"ok": True, "api_enabled": bool(_configured_keys())})

    # ---- data endpoints (auth) ------------------------------------------
    @bp.get("/plants")
    @_require_key
    def plants():
        out = []
        for code, name in PLANT_NAMES.items():
            cfg = DAILY_SOURCES.get(code) or {}
            months = sorted((cfg.get("files") or {}).keys())
            out.append({
                "code": code,
                "name": name,
                "location": PLANT_LOCATIONS.get(code, ""),
                "daily_months": months,
                "daily_wired": bool(months),
            })
        return jsonify({"plants": out})

    @bp.get("/periods")
    @_require_key
    def periods():
        return jsonify({
            "period_tokens": _PERIOD_TOKENS,
            "months_with_data": months_with_data(),
        })

    @bp.get("/summary")
    @_require_key
    def summary():
        data = get_data(_clean_args())
        conf = data["confirmation"]
        rows = data["rows"]
        payload = {
            "period": {
                "requested": data["period"],
                "label": data["period_label"],
                "from": data["from_iso"],
                "to": data["to_iso"],
                "months": data["months"],
                "daily_first": data["daily_used"],
                "banner": data["grain_banner"],
            },
            "filters": {
                "plant": data["plant_filter"],
                "segment": data["segment_filter"],
                "machine": data["machine_filter"],
            },
            "figures_gated": _figures_gated(conf),
            "overall": _metrics_json(data["overall"]),
            "by_plant": {
                p: _metrics_json(m)
                for p, m in sorted(rollup_by_plant(rows).items())
            },
            "confirmation": _confirmation_json(conf),
            "quarantined_rows": len(data["quarantined"]),
            "row_count": len(rows),
        }
        if data["plant_filter"]:
            payload["by_machine"] = {
                k: _metrics_json(m)
                for k, m in sorted(rollup_by_machine(rows).items())
            }
            payload["by_segment"] = {
                k: _metrics_json(m)
                for k, m in sorted(rollup_by_segment(rows).items())
            }
        if data["daily_used"]:
            payload["by_date"] = {
                k: _metrics_json(m)
                for k, m in sorted(rollup_by_date(rows).items()) if k
            }
        return jsonify(payload)

    @bp.get("/records")
    @_require_key
    def records():
        data = get_data(_clean_args())
        conf = data["confirmation"]
        return jsonify({
            "period": {
                "requested": data["period"],
                "label": data["period_label"],
                "from": data["from_iso"],
                "to": data["to_iso"],
            },
            "figures_gated": _figures_gated(conf),
            "confirmation_status": conf.get("status"),
            "row_count": len(data["rows"]),
            "rows": [_record_json(r) for r in data["rows"]],
            "quarantined": [_record_json(r) for r in data["quarantined"]],
        })

    @bp.post("/schedule")
    @_require_key
    def schedule():
        """Return one authenticated, non-persistent Plumbing schedule preview."""
        payload = request.get_json(silent=True)
        try:
            result = run_schedule_preview(payload)
        except SchedulePreviewError as exc:
            return jsonify({
                "error": "invalid_schedule_request",
                "message": str(exc),
            }), 400
        except ScheduleMachinePoolConflict as exc:
            return jsonify({
                "error": "schedule_machine_pool_overlap",
                "message": str(exc),
            }), 409
        except PlanningDataUnavailable as exc:
            return jsonify({
                "error": "planning_data_unavailable",
                "message": str(exc),
            }), 503
        return jsonify(result.to_dict())

    @bp.post("/corrective-replan")
    @_require_key
    def corrective_replan():
        """Return one authenticated, non-persistent Plumbing pace projection."""
        payload = request.get_json(silent=True)
        try:
            result, week_days = run_corrective_replan_preview(payload)
        except CorrectivePreviewError as exc:
            return jsonify({
                "error": "invalid_corrective_replan_request",
                "message": str(exc),
            }), 400
        except CorrectiveActualsUnavailable as exc:
            return jsonify({
                "error": "corrective_actuals_unavailable",
                "message": str(exc),
            }), 503
        except CorrectiveComputationError as exc:
            current_app.logger.error(
                "corrective re-plan API computation failed", exc_info=True,
            )
            return jsonify({
                "error": "corrective_replan_failed",
                "message": str(exc),
            }), 500
        return jsonify(_corrective_result_json(result, week_days))

    return bp
