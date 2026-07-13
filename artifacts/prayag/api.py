"""Read-only JSON API (v1) — lets external apps consume the full dashboard data.

Design rules (mirror the app's invariants exactly):
  * Every figure is produced by the SAME pipeline the dashboard uses
    (``get_data`` → daily-first read → compute_metrics → confirmation), so the
    API can never disagree with the UI.
  * No fake 0%: a ratio without a real baseline/denominator serializes as
    ``null`` (never 0). ``*_available`` flags say why.
  * Data-confirmation gating is FIRST-CLASS in the payload: consumers get
    ``confirmation.status`` and ``figures_gated`` so an error-gated period is
    never mistaken for clean data.
  * Read-only: no route mutates anything.

Auth: every data endpoint requires the ``PRAYAG_API_KEY`` secret, supplied as
an ``X-API-Key`` header (preferred), ``Authorization: Bearer <key>``, or an
``api_key`` query param. If the secret is not configured the API answers 503 —
it is never silently open.
"""
from __future__ import annotations

import dataclasses
import hmac
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from metrics import (
    MetricsResult,
    rollup_by_date,
    rollup_by_machine,
    rollup_by_plant,
    rollup_by_segment,
)
from sheets import SheetReadError, months_with_data
from sources import DAILY_SOURCES, PLANT_LOCATIONS, PLANT_NAMES


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


def _configured_key() -> str:
    # DB-stored key (managed from /settings/api-key) takes priority over the
    # environment variable so the key can be rotated from the published app UI.
    try:
        import store as _store  # local import to avoid circular import at module load
        db_key = _store.get_api_key()
        if db_key:
            return db_key.strip()
    except Exception:
        pass
    return (os.environ.get(API_KEY_ENV) or "").strip()


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
        key = _configured_key()
        if not key:
            return jsonify({
                "error": "api_disabled",
                "message": (
                    f"The API is not enabled: the {API_KEY_ENV} secret is not "
                    "configured on this deployment."
                ),
            }), 503
        supplied = _supplied_key()
        if not supplied or not hmac.compare_digest(supplied, key):
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
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
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
        return jsonify({"ok": True, "api_enabled": bool(_configured_key())})

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

    return bp
