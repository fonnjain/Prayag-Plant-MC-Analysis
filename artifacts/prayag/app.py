"""
Prayag Production Analytics — Flask application entry point.
All arithmetic is deterministic Python. Claude is used only for narrative prose.
"""
from __future__ import annotations
import os
import re
import datetime
import json
from functools import lru_cache
from typing import Optional
from urllib.parse import urlsplit
from flask import Flask, render_template, request, jsonify, Response, abort, redirect

from sheets import (
    get_records, get_daily_records, detected_sources, months_with_data,
    is_demo_mode, SheetReadError, last_fetch_status, clear_caches,
)
from sources import PLANT_NAMES, ANNUAL_SOURCES, DAILY_SOURCES, FY_MONTHS
from metrics import (
    compute_metrics, rollup_by_plant, rollup_by_machine, rollup_by_mould,
    rollup_by_segment, rollup_by_period, rollup_by_date, downtime_pareto,
)
from validate import full_validate
from confirm import (
    full_confirm,
    confirmation_fingerprint,
    tier3_row_classify,
    TIER_LABELS,
    _month_due,
)
from narrative import (
    get_narrative, match_codes, summarize_confirmation, claude_sanity_check,
    select_model, model_label,
)
import store
import baselines
import verify
from pdf_export import generate_report_pdf
from glossary import (
    GLOSSARY, GLOSSARY_BY_KEY, FORMULAS, RATING_BANDS, RATING_NOTE,
    WORKED_EXAMPLE, COMPUTE_NOTE, HEADER_TERM_MAP,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "prayag-analytics-dev")

# In-process store: (fingerprint, resolved model) → Claude review text.
# Keyed by data fingerprint so a changed sheet invalidates the prior review, AND
# by the resolved model so a fast-tier review is never served when a deep-tier
# (or forced-deep) review is later requested for the same data state.
# Survives across requests; resets on server restart (cheap — user just re-runs).
_claude_reviews: dict[str, str] = {}


def _review_cache_key(data: dict) -> str:
    """Cache key for a Claude sanity review: data fingerprint + resolved model.

    The model is resolved from the period tier and any deep override so that
    switching tiers (or forcing deep) always re-runs rather than returning a
    cached review produced by a different model.
    """
    fingerprint = data["confirmation"].get("fingerprint", "")
    model, _, _ = select_model(data["period_type"], override=data.get("deep_override"))
    return f"{fingerprint}:{model}"


@app.errorhandler(SheetReadError)
def _handle_sheet_error(err):
    """Show a clear message instead of a 500 when the live sheet can't be read."""
    return render_template("sheet_error.html", message=str(err)), 200


def _safe_json(obj) -> str:
    """JSON for safe embedding inside a <script> tag.

    Escapes characters that could break out of the script context, so values
    coming from an externally-editable Google Sheet cannot inject markup.
    """
    return (
        json.dumps(obj)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def _today() -> datetime.date:
    return datetime.date.today()


def _fmt(d: datetime.date) -> str:
    return d.strftime("%d-%m-%Y")


def _months_between(f: datetime.date, t: datetime.date) -> list[str]:
    out, y, m = [], f.year, f.month
    while (y, m) <= (t.year, t.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _month_disp(ym: str) -> str:
    try:
        return datetime.date(int(ym[:4]), int(ym[5:7]), 1).strftime("%b %Y")
    except (ValueError, IndexError):
        return ym


def _fmt_age(seconds: int) -> str:
    """Human-readable age string, e.g. '3m', '2h 15m', '1d 4h'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        h, m = seconds // 3600, (seconds % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    d, h = seconds // 86400, (seconds % 86400) // 3600
    return f"{d}d {h}h" if h else f"{d}d"


def parse_period(args) -> dict:
    """Resolve the requested period to calendar months (the source grain).

    Returns a dict with from/to ISO dates, a human label, the list of overlapped
    months, and a banner explaining any sub-monthly → monthly resolution.
    """
    period = args.get("period", "last_updated")
    today = _today()
    yesterday = today - datetime.timedelta(days=1)
    sub_monthly = False

    if period == "last_updated":
        # Use the last 30 days as the search window; get_data narrows to the
        # actual last date that has production entries once records are loaded.
        t = yesterday
        f = t - datetime.timedelta(days=29)
        label = "Last Updated"   # refined in get_data once records are known
        sub_monthly = True
    elif period == "yesterday":
        f = t = yesterday
        label = f"Yesterday ({_fmt(yesterday)})"
        sub_monthly = True
    elif period == "last_week":
        t = yesterday
        f = t - datetime.timedelta(days=6)
        label = f"Last 7 days: {_fmt(f)} to {_fmt(t)}"
        sub_monthly = True
    elif period == "last_month":
        t = yesterday
        f = t - datetime.timedelta(days=29)
        label = f"Last 30 days: {_fmt(f)} to {_fmt(t)}"
        sub_monthly = True
    elif period == "current_fy":
        year = today.year if today.month >= 4 else today.year - 1
        f = datetime.date(year, 4, 1)
        t = datetime.date(year + 1, 3, 31)
        label = f"FY {year}-{str(year+1)[2:]}"
    elif period == "prior_fy":
        year = (today.year if today.month >= 4 else today.year - 1) - 1
        f = datetime.date(year, 4, 1)
        t = datetime.date(year + 1, 3, 31)
        label = f"FY {year}-{str(year+1)[2:]}"
    elif period == "custom":
        try:
            f = datetime.date.fromisoformat(args.get("from_date", str(yesterday)))
            t = datetime.date.fromisoformat(args.get("to_date", str(yesterday)))
        except ValueError:
            f = t = yesterday
        label = f"Custom: {_fmt(f)} to {_fmt(t)}"
        sub_monthly = (t - f).days < 27
    elif period in [str(m) for m in range(1, 13)]:
        m = int(period)
        # FY runs Apr–Mar: Apr–Dec map to the FY start year, Jan–Mar to the next.
        fy_start = today.year if today.month >= 4 else today.year - 1
        year = fy_start if m >= 4 else fy_start + 1
        f = datetime.date(year, m, 1)
        t = (datetime.date(year, m % 12 + 1, 1) - datetime.timedelta(days=1)) if m < 12 else datetime.date(year, 12, 31)
        label = datetime.date(year, m, 1).strftime("%B %Y")
    else:
        year = today.year if today.month >= 4 else today.year - 1
        f = datetime.date(year, 4, 1)
        t = datetime.date(year + 1, 3, 31)
        label = f"FY {year}-{str(year+1)[2:]}"

    if f > t:
        f = t

    months = _months_between(f, t)

    # The grain banner for sub-monthly windows is built in get_data, where we
    # know whether true daily data was served. parse_period never claims daily is
    # unavailable — a sub-monthly window is served from the daily files directly.
    banner = ""

    return {
        "from_iso": f.isoformat(),
        "to_iso": t.isoformat(),
        "label": label,
        "months": months,
        "banner": banner,
        "period": period,
        "sub_monthly": sub_monthly,
    }


def _period_type(period: str) -> str:
    """Map a UI period to a model-tier kind.

    Daily/weekly windows are the frequent, low-stakes runs → fast tier.
    A whole fiscal month, the FY, or the prior FY are the infrequent,
    high-stakes reviews → deep tier. Used only to pick the language model;
    the numbers are computed identically regardless.
    """
    p = (period or "").strip()
    if p in ("yesterday", "last_week", "last_month", "last_updated", "custom"):
        return "weekly"
    if p in ("current_fy", "prior_fy"):
        return "fiscal_year"
    if p in [str(m) for m in range(1, 13)]:
        return "monthly"
    return "fiscal_year"


def _deep_override(args) -> Optional[bool]:
    """Read a manual deep-analysis override from the request, or None.

    ``deep_analysis`` (or ``deep``) = on/true/1/yes → force deep;
    off/false/0/no → force fast; anything else → no override (tier by period).
    """
    raw = (args.get("deep_analysis") or args.get("deep") or "").strip().lower()
    if raw in ("1", "true", "on", "yes", "deep"):
        return True
    if raw in ("0", "false", "off", "no", "fast"):
        return False
    return None


def _period_key(from_iso: str, to_iso: str, plant: str, segment: str, machine: str) -> str:
    return f"{from_iso}_{to_iso}_{plant}_{segment}_{machine}"


def _apply_baselines(rows):
    """Layer the per-machine planned-hours baseline onto monthly rows in place.

    The sheet only carries a flat placeholder for ideal/planned hours. Where a
    machine has a configured baseline (baselines.json), we use it as the
    utilisation/efficiency denominator and stamp ``ideal_source='config'`` while
    preserving the original sheet value in ``ideal_hours_sheet``. Where it does
    not, we keep the sheet value and leave ``ideal_source='sheet'`` so the
    confirmation layer can flag it. This never touches how output or actual hours
    were READ — only the target each machine is measured against.
    """
    for r in rows:
        if r.grain != "monthly":
            continue
        r.ideal_hours_sheet = r.ideal_hours
        base = baselines.resolve(r.plant, r.machine, r.period or "")
        if base:
            r.ideal_hours = base["planned_hours"]
            if base.get("ideal_output") is not None:
                r.ideal_output = base["ideal_output"]
            r.ideal_source = "config"
        else:
            r.ideal_source = "sheet"
    return rows


# ---------------------------------------------------------------------------
# Data pipeline (read → filter → compute → validate)
# ---------------------------------------------------------------------------

def get_data(args):
    pinfo = parse_period(args)
    months = pinfo["months"]

    plant_filter = args.get("plant", "")
    segment_filter = args.get("segment", "")
    machine_filter = args.get("machine", "")

    # Sub-monthly windows are served from true daily data whenever a daily
    # workbook exists for the needed month(s). A monthly summary cannot be sliced
    # into a partial month, so we never fall back to monthly totals just because a
    # particular window has no rows yet — we stay in daily grain and say so. We
    # only fall back to monthly when no daily file exists for the period at all,
    # or the daily read failed outright.
    daily_used = False
    daily_err = None
    grain_banner = pinfo["banner"]
    all_rows = source_reports = recon_warnings = None
    if pinfo.get("sub_monthly"):
        daily_file_months = [
            m for m in months
            if any(m in (cfg.get("files") or {})
                   for cfg in DAILY_SOURCES.values())
        ]
        try:
            drecs, dreports, dwarn = get_daily_records(months)
        except SheetReadError as e:
            drecs, dreports, dwarn = [], [], []
            daily_err = f"Daily data could not be read ({e}); fell back to monthly totals."
        if daily_file_months and not daily_err:
            # "last_updated" period: narrow to the actual last date with data.
            if pinfo["period"] == "last_updated":
                last_date = max((r.date for r in drecs if r.date), default=None)
                if last_date:
                    pinfo["from_iso"] = last_date
                    pinfo["to_iso"] = last_date
                    pinfo["label"] = f"Last updated: {_fmt(datetime.date.fromisoformat(last_date))}"
                # If no daily data found at all, keep the 30-day window so the
                # "no data" grain banner fires normally below.
            fwin, twin = pinfo["from_iso"], pinfo["to_iso"]
            win = [r for r in drecs if fwin <= r.date <= twin]
            all_rows, source_reports, recon_warnings = win, dreports, dwarn
            daily_used = True
            if win:
                disp_plants = ", ".join(
                    PLANT_NAMES.get(p, p) for p in sorted({r.plant for r in win})
                )
                grain_banner = (
                    f"{pinfo['label']} → true daily data for {disp_plants}. "
                    "Any plant not listed had no run recorded on these days."
                )
            else:
                latest = max((r.date for r in drecs), default=None)
                if latest:
                    latest_disp = _fmt(datetime.date.fromisoformat(latest))
                    grain_banner = (
                        f"{pinfo['label']} → no daily production was recorded on these "
                        f"dates. Daily data is currently entered through {latest_disp}."
                    )
                else:
                    grain_banner = (
                        f"{pinfo['label']} → no daily production has been recorded for "
                        "this window yet."
                    )
    if not daily_used:
        all_rows, source_reports, recon_warnings = get_records(months)
        _apply_baselines(all_rows)
        if pinfo.get("sub_monthly") and not daily_err:
            disp = ", ".join(_month_disp(m) for m in months)
            grain_banner = (
                f"{pinfo['label']} → no daily workbook is available for this window, "
                f"so monthly totals for {disp} are shown instead."
            )
        if daily_err:
            recon_warnings = list(recon_warnings or []) + [daily_err]

    # Quarantine physically-impossible rows (Tier 3 hard errors): they are held
    # aside with their raw value + provenance and EXCLUDED from every published
    # figure, while the rest of the period publishes normally. ``raw_all`` keeps
    # the full set for confirmation detection; ``clean_all`` is what we publish.
    raw_all = all_rows
    clean_all, quarantined_all, _q_issues = tier3_row_classify(raw_all)

    rows = clean_all
    if plant_filter:
        rows = [r for r in rows if r.plant == plant_filter]
    if segment_filter:
        rows = [r for r in rows if r.segment == segment_filter]
    if machine_filter:
        rows = [r for r in rows if r.machine == machine_filter]

    overall = compute_metrics(rows)
    validation = full_validate(rows, overall, extra_warnings=recon_warnings)

    # ---- Four-tier data confirmation (deterministic) ----
    # Completeness is measured against the full-FY monthly grid as the master
    # roster. Confirmation always runs on the UNFILTERED period rows so a plant
    # or machine filter never makes the dataset look incomplete. Detection runs on
    # the raw set; the published metrics it reconciles against exclude quarantine.
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
    try:
        master_rows = get_records(FY_MONTHS)[0]
        _apply_baselines(master_rows)
    except SheetReadError:
        master_rows = list(raw_all)
    confirm_overall = compute_metrics(clean_all)
    confirmation = full_confirm(
        period_months=months,
        period_rows=raw_all,
        source_reports=source_reports or [],
        master_rows=master_rows,
        fy_months_with_data=months_with_data(),
        computed=confirm_overall,
        daily_used=daily_used,
        as_of=_today(),
        extra_recon_warnings=recon_warnings,
        matcher=(match_codes if has_claude else None),
    )

    # ---- Manager sign-off (release of error-gated figures) ----
    # The sign-off is keyed to the UNFILTERED period and the exact data state
    # (fingerprint), so a filter never changes it and a data change re-gates it.
    sign_pk = _period_key(pinfo["from_iso"], pinfo["to_iso"], "", "", "")
    fingerprint = confirmation_fingerprint(confirmation)
    signoff = store.effective(sign_pk, fingerprint)
    confirmation["period_key"] = sign_pk
    confirmation["fingerprint"] = fingerprint
    confirmation["signoff"] = signoff

    # ---- Per-issue acknowledgements (accept individual known anomalies) ----
    # A manager can mark a single issue as reviewed/accepted. Acks are keyed to
    # the UNFILTERED period and a STABLE issue identity (not the fingerprint), so
    # a recurring known anomaly (e.g. PIPE's by-design reconcile offset) stays
    # acknowledged as its magnitude drifts. An acknowledged error no longer drives
    # the headline "needs review" gate; if every blocking error is acknowledged
    # the status is downgraded from error to warning so the figures publish.
    acks = store.acks_for(sign_pk)
    acked_n = 0
    open_blocking = 0
    for i in confirmation["issues"]:
        a = acks.get(i.get("key"))
        if a:
            i["acknowledged"] = True
            i["ack"] = a
            acked_n += 1
        else:
            i["acknowledged"] = False
            i["ack"] = None
            if i["severity"] == "error" and not i.get("quarantined"):
                open_blocking += 1
    confirmation["counts"]["acknowledged"] = acked_n
    confirmation["counts"]["error_open"] = open_blocking
    if open_blocking == 0 and confirmation["status"] == "error":
        confirmation["status"] = "warning"

    confirmation["released"] = bool(signoff) and confirmation["status"] == "error"

    # Flag requested months that hold no data yet (monthly path only).
    # Only months that have actually ENDED can be "missing" — the current
    # in-progress month and any future month are not yet due, so a blank there
    # is expected and must never be announced as a gap.
    banner = grain_banner
    if not daily_used:
        have = set(months_with_data())
        as_of = _today()
        empty_months = [m for m in months if m not in have and _month_due(m, as_of)]
        if empty_months:
            disp = ", ".join(_month_disp(m) for m in empty_months)
            if len(empty_months) == len(months):
                note = f"No data yet for this period ({disp})."
            else:
                note = f"No data yet for {disp}."
            banner = f"{banner} {note}".strip()

    # ---- Fetch status (stale cache / partial plant recovery) ----
    fs = last_fetch_status()
    if fs.get("stale"):
        fs["stale_age_disp"] = _fmt_age(fs.get("stale_age_seconds", 0))

    # Model tier for any language (prose) call on this view. Affects only which
    # model writes the words — never the figures.
    period_type = _period_type(pinfo["period"])
    deep_override = _deep_override(args)
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY", ""))
    analysis_label = model_label(period_type, override=deep_override) if has_claude else None

    return {
        "rows": rows,
        "all_rows": clean_all,
        "quarantined": quarantined_all,
        "overall": overall,
        "validation": validation,
        "confirmation": confirmation,
        "from_iso": pinfo["from_iso"],
        "to_iso": pinfo["to_iso"],
        "period_label": pinfo["label"],
        "period": pinfo["period"],
        "period_type": period_type,
        "deep_override": deep_override,
        "analysis_label": analysis_label,
        "months": months,
        "grain_banner": banner,
        "daily_used": daily_used,
        "source_reports": source_reports,
        "plant_filter": plant_filter,
        "segment_filter": segment_filter,
        "machine_filter": machine_filter,
        "demo_mode": is_demo_mode(),
        "has_claude": has_claude,
        "fetch_status": fs,
    }


@app.context_processor
def inject_glossary():
    """Make the single-source glossary available to every template."""
    return {
        "glossary": GLOSSARY,
        "glossary_data": GLOSSARY_BY_KEY,
        "formulas": FORMULAS,
        "rating_bands": RATING_BANDS,
        "rating_note": RATING_NOTE,
        "worked_example": WORKED_EXAMPLE,
        "compute_note": COMPUTE_NOTE,
        "header_term_map": HEADER_TERM_MAP,
    }


def _common_ctx(data: dict) -> dict:
    """Build template context that every page needs."""
    opt_rows = data.get("all_rows", data["rows"])
    plants = sorted(set(r.plant for r in opt_rows))
    segments = sorted(set(r.segment for r in opt_rows))
    machines = sorted(set(r.machine for r in opt_rows if r.machine))
    return {
        **data,
        "plants": plants,
        "plant_names": PLANT_NAMES,
        "segments": segments,
        "machines": machines,
        "overall_dict": data["overall"].to_dict(),
        "today_disp": _fmt(_today()),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _safe_next(nxt: str) -> str:
    """Return ``nxt`` only if it is an internal, same-origin relative path;
    otherwise fall back to ``/``. Guards against open redirects."""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or nxt.startswith("/\\"):
        return "/"
    parts = urlsplit(nxt)
    # A safe internal target has no scheme and no host (netloc).
    if parts.scheme or parts.netloc:
        return "/"
    return nxt


@app.route("/refresh")
def refresh():
    """Drop the sheet caches so the next page load fetches the latest live data,
    then return to the page the user came from (defaults to the overview)."""
    clear_caches()
    return redirect(_safe_next(request.args.get("next", "/")))


@app.route("/")
def overview():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    od = ctx["overall_dict"]
    oee_available = od["oee_available"]

    # Trend: per-day when showing true daily data, else per-month.
    if data.get("daily_used"):
        by_t = rollup_by_date(data["rows"])
        trend_keys = sorted(by_t.keys())
        trend_labels = [_fmt(datetime.date.fromisoformat(k)) for k in trend_keys]
    else:
        by_t = rollup_by_period(data["rows"])
        trend_keys = sorted(by_t.keys())
        trend_labels = [_month_disp(k) for k in trend_keys]
    trend_values = [round(by_t[k].headline * 100, 1) for k in trend_keys]
    trend_label = "OEE %" if oee_available else "Output Efficiency %"

    # Plant overview
    by_plant = rollup_by_plant(data["rows"])
    plant_labels = sorted(by_plant.keys())
    plant_headline = [round(by_plant[p].headline * 100, 1) for p in plant_labels]
    plant_output = [round(by_plant[p].total_count, 0) for p in plant_labels]
    plant_names_disp = [PLANT_NAMES.get(p, p) for p in plant_labels]

    # Narrative
    narrative = None
    if ctx["has_claude"] and data["rows"]:
        if oee_available:
            summary = {
                "OEE": f"{od['oee']}%",
                "Availability": f"{od['availability']}%",
                "Performance": f"{od['performance']}%",
                "Quality": f"{od['quality']}%",
                "Total Output": od["total_count"],
                "Rejection %": f"{od['rejection_pct']}%",
            }
        else:
            summary = {
                "Output Efficiency": f"{od['output_efficiency']}%",
                "Utilisation": f"{od['utilisation']}%",
                "Total Output": od["total_count"],
                "Rejection %": f"{od['rejection_pct']}%",
            }
        narrative = get_narrative(
            view="Overview",
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", "", ""),
            metrics_summary=summary,
            period_type=data["period_type"],
            deep=data["deep_override"],
        )

    ctx.update({
        "trend_labels": _safe_json(trend_labels),
        "trend_values": _safe_json(trend_values),
        "trend_label": trend_label,
        "plant_labels": _safe_json(plant_names_disp),
        "plant_oee": _safe_json(plant_headline),
        "plant_output": _safe_json(plant_output),
        "by_plant": {p: by_plant[p].to_dict() for p in plant_labels},
        "narrative": narrative,
    })
    return render_template("overview.html", **ctx)


@app.route("/plant")
def plant_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    by_plant = rollup_by_plant(data["rows"])
    plant_items = []
    for p in sorted(by_plant.keys()):
        m = by_plant[p]
        plant_items.append({
            "plant": p,
            "name": PLANT_NAMES.get(p, p),
            "metrics": m.to_dict(),
        })

    plant_labels = [item["plant"] for item in plant_items]
    plant_headline = [item["metrics"]["headline"] for item in plant_items]
    plant_output = [item["metrics"]["total_count"] for item in plant_items]
    plant_attainment = [item["metrics"]["attainment"] for item in plant_items]

    od = ctx["overall_dict"]
    ctx.update({
        "plant_items": plant_items,
        "plant_labels": _safe_json(plant_labels),
        "plant_oee": _safe_json(plant_headline),
        "plant_output": _safe_json(plant_output),
        "plant_attainment": _safe_json(plant_attainment),
        "oee_available": od["oee_available"],
        "headline_label": od["headline_label"],
    })
    return render_template("plant.html", **ctx)


@app.route("/machine")
def machine_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    by_machine = rollup_by_machine(data["rows"])
    machine_items = sorted(
        [{"machine": k, "metrics": v.to_dict()} for k, v in by_machine.items() if k],
        key=lambda x: x["metrics"]["headline"],
        reverse=True,
    )

    machine_labels = [x["machine"] for x in machine_items]
    machine_headline = [x["metrics"]["headline"] for x in machine_items]

    od = ctx["overall_dict"]
    ctx.update({
        "machine_items": machine_items,
        "machine_labels": _safe_json(machine_labels),
        "machine_oee": _safe_json(machine_headline),
        "oee_available": od["oee_available"],
        "headline_label": od["headline_label"],
    })
    return render_template("machine.html", **ctx)


@app.route("/losses")
def losses():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    pareto = downtime_pareto(data["rows"])
    labels = [p["reason"] or "Unknown" for p in pareto]
    minutes = [p["minutes"] for p in pareto]
    cum_pct = [p["cumulative_pct"] for p in pareto]

    ctx.update({
        "pareto": pareto,
        "pareto_labels": _safe_json(labels),
        "pareto_minutes": _safe_json(minutes),
        "pareto_cum_pct": _safe_json(cum_pct),
        "top3": pareto[:3],
        "total_downtime": sum(minutes),
        "oee_available": ctx["overall_dict"]["oee_available"],
    })
    return render_template("losses.html", **ctx)


@app.route("/glossary")
def glossary_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    return render_template("glossary.html", **ctx)


@app.route("/confirmation")
def confirmation_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    conf = data["confirmation"]

    # Plain-English summary from Claude (from already-computed issues only).
    summary = None
    if ctx["has_claude"]:
        issues_brief = [
            f"[{i['tier_label']}/{i['severity']}] {i['message']}"
            for i in conf["issues"]
        ]
        summary = summarize_confirmation(
            conf["status"], conf["score_label"], issues_brief
        )

    fingerprint = conf.get("fingerprint", "")
    claude_review_text = _claude_reviews.get(_review_cache_key(data))
    ctx.update({
        "conf": conf,
        "conf_summary": summary,
        "tier_labels": TIER_LABELS,
        "signoff_history": store.history(conf.get("period_key")),
        "signoff_store_ok": store.AVAILABLE,
        "signoff_msg": request.args.get("signoff_msg", ""),
        "claude_reviewed": claude_review_text is not None,
        "claude_review_text": claude_review_text or "",
    })
    return render_template("confirmation.html", **ctx)


def _redirect_to_confirmation(args, msg: str = ""):
    """Redirect back to the confirmation screen, preserving the period."""
    qs = f"period={args.get('period', 'current_fy')}"
    if args.get("period") == "custom":
        qs += (
            f"&from_date={args.get('from_date', '')}"
            f"&to_date={args.get('to_date', '')}"
        )
    if msg:
        from urllib.parse import quote
        qs += f"&signoff_msg={quote(msg)}"
    return redirect(f"/confirmation?{qs}")


@app.route("/confirmation/approve", methods=["POST"])
def confirmation_approve():
    return _do_signoff("approve", request.form)


@app.route("/confirmation/revoke", methods=["POST"])
def confirmation_revoke():
    return _do_signoff("revoke", request.form)


@app.route("/confirmation/ack_issue", methods=["POST"])
def confirmation_ack_issue():
    return _do_ack("ack", request.form)


@app.route("/confirmation/unack_issue", methods=["POST"])
def confirmation_unack_issue():
    return _do_ack("unack", request.form)


def _do_ack(action: str, form):
    """Acknowledge (or un-acknowledge) a single flagged issue for this period."""
    if not store.AVAILABLE:
        return _redirect_to_confirmation(form, "Review store is unavailable.")
    approver = (form.get("approver", "") or "").strip()
    if action == "ack" and not approver:
        return _redirect_to_confirmation(
            form, "Please enter your name to acknowledge an issue."
        )
    issue_k = (form.get("issue_key", "") or "").strip()
    if not issue_k:
        return _redirect_to_confirmation(form, "No issue specified.")

    # Recompute against live data so we acknowledge an issue that exists NOW and
    # capture its current location/wording for the trail.
    data = get_data(form)
    conf = data["confirmation"]
    match = next((i for i in conf["issues"] if i.get("key") == issue_k), None)
    if action == "ack" and match is None:
        return _redirect_to_confirmation(
            form, "That issue is no longer present — nothing to acknowledge."
        )

    try:
        store.ack_record(
            action,
            period_key=conf["period_key"],
            issue_key=issue_k,
            tier=(match or {}).get("tier", 0),
            severity=(match or {}).get("severity", ""),
            plant=(match or {}).get("plant", ""),
            machine=(match or {}).get("machine", ""),
            message=(match or {}).get("message", ""),
            approver=approver or "(removed)",
            role=form.get("role", ""),
            note=form.get("note", ""),
        )
    except store.StoreError as e:
        return _redirect_to_confirmation(form, f"Could not save: {e}")

    msg = (
        "Issue acknowledged — it no longer drives the review gate."
        if action == "ack"
        else "Acknowledgement removed — the issue is active again."
    )
    return _redirect_to_confirmation(form, msg)


@app.route("/confirmation/claude_review", methods=["POST"])
def confirmation_claude_review():
    """Run Claude sanity check on the current confirmation state. Returns JSON.

    Passes ONLY the already-computed tier issues and pre-computed metrics to
    Claude. No raw sheet data is ever sent. Result is keyed to the data
    fingerprint and cached in _claude_reviews for the lifetime of this process.
    """
    if not bool(os.environ.get("ANTHROPIC_API_KEY", "")):
        return jsonify({"ok": False, "error": "Claude is not configured."}), 400

    try:
        data = get_data(request.form)
    except SheetReadError as e:
        return jsonify({"ok": False, "error": f"Could not read data: {e}"}), 500

    conf = data["confirmation"]
    fingerprint = conf["fingerprint"]
    review_ck = _review_cache_key(data)

    if review_ck in _claude_reviews:
        return jsonify({"ok": True, "fingerprint": fingerprint,
                        "review": _claude_reviews[review_ck]})

    od = data["overall"].to_dict()
    if od.get("oee_available"):
        metrics_summary = {
            "OEE": f"{od['oee']}%",
            "Availability": f"{od['availability']}%",
            "Performance": f"{od['performance']}%",
            "Quality": f"{od['quality']}%",
            "Total Output (kg/pcs)": od["total_count"],
            "Rejection %": f"{od['rejection_pct']}%",
        }
    else:
        metrics_summary = {
            "Output Efficiency": f"{od['output_efficiency']}%",
            "Utilisation": f"{od['utilisation']}%",
            "Total Output (kg/pcs)": od["total_count"],
            "Rejection %": f"{od['rejection_pct']}%",
        }

    review = claude_sanity_check(
        conf, metrics_summary, data["period_label"],
        period_type=data["period_type"], deep=data["deep_override"],
    )
    if review is None:
        return jsonify({"ok": False,
                        "error": "Claude could not generate a review — check API key or try again."}), 500

    _claude_reviews[review_ck] = review
    return jsonify({"ok": True, "fingerprint": fingerprint, "review": review})


def _do_signoff(action: str, form):
    """Record a manager sign-off (or revoke) against the CURRENT data state."""
    if not store.AVAILABLE:
        return _redirect_to_confirmation(form, "Sign-off store is unavailable.")
    approver = (form.get("approver", "") or "").strip()
    if action == "approve" and not approver:
        return _redirect_to_confirmation(form, "Please enter your name to sign off.")

    # Claude sanity check is mandatory before approving (when Claude is available).
    if action == "approve" and bool(os.environ.get("ANTHROPIC_API_KEY", "")):
        # We need the resolved review key to check — peek at the current state.
        _chk_data = get_data(form)
        if _review_cache_key(_chk_data) not in _claude_reviews:
            return _redirect_to_confirmation(
                form,
                "Please complete the Claude sanity check before signing off."
            )

    # Recompute against live data so we sign off exactly what is shown now.
    data = get_data(form)
    conf = data["confirmation"]
    posted_fp = form.get("fingerprint", "")
    if posted_fp and posted_fp != conf["fingerprint"]:
        return _redirect_to_confirmation(
            form, "The data changed since you opened this page — review it again."
        )
    if action == "approve" and conf["status"] == "pass":
        return _redirect_to_confirmation(form, "Nothing to sign off — data is clean.")

    try:
        store.record(
            action,
            period_key=conf["period_key"],
            fingerprint=conf["fingerprint"],
            from_iso=data["from_iso"],
            to_iso=data["to_iso"],
            period_label=data["period_label"],
            status_at=conf["status"],
            score_label=conf["score_label"],
            error_count=conf["counts"]["error"],
            warning_count=conf["counts"]["warning"],
            issue_count=conf["counts"]["total"],
            approver=approver or "(revoked)",
            role=form.get("role", ""),
            note=form.get("note", ""),
        )
    except store.StoreError as e:
        return _redirect_to_confirmation(form, f"Could not save: {e}")

    msg = (
        "Figures published under your sign-off."
        if action == "approve"
        else "Sign-off revoked — figures are withheld again."
    )
    return _redirect_to_confirmation(form, msg)


@app.route("/sources")
def sources_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    # Always show the full configured inventory (annual + daily workbooks),
    # independent of the selected period. Overlay any reconciliation results
    # from the current period's loaded reports, keyed by file+tab.
    reports = detected_sources()
    overlays = {
        (r.get("file_id"), r.get("tab")): r
        for r in (data.get("source_reports") or [])
        if r.get("reconcile")
    }
    for r in reports:
        ov = overlays.get((r.get("file_id"), r.get("tab")))
        if ov:
            r["reconcile"] = ov["reconcile"]
            if ov.get("record_count"):
                r["record_count"] = ov["record_count"]
    ctx.update({
        "reports": reports,
        "annual_sources": ANNUAL_SOURCES,
        "daily_sources": DAILY_SOURCES,
    })
    return render_template("detected_sources.html", **ctx)


# ---------------------------------------------------------------------------
# Data verification (read-only — expose computed figures + provenance so the
# numbers can be reconciled against the source sheets; never writes a figure)
# ---------------------------------------------------------------------------
_YM_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _verify_month(args) -> str:
    """Resolve the month to verify (``YYYY-MM``).

    Accepts ``?month=YYYY-MM`` or the spec's ``?period=YYYY-MM``; otherwise
    defaults to the most recent month that actually has data.
    """
    raw = (args.get("month") or args.get("period") or "").strip()
    if _YM_RE.match(raw):
        return raw
    have = months_with_data()
    if have:
        return have[-1]
    today = _today()
    return f"{today.year:04d}-{today.month:02d}"


def _build_verify(month: str) -> dict:
    """Load the deterministic monthly + daily records for ``month`` and assemble
    the verification result. Daily read failures degrade to monthly-only (the
    daily-vs-summary check then reports NA) rather than erroring the page."""
    try:
        monthly_rows, monthly_reports, _w = get_records([month])
        _apply_baselines(monthly_rows)
    except SheetReadError:
        monthly_rows, monthly_reports = [], []
    try:
        daily_rows, daily_reports, _dw = get_daily_records([month])
    except SheetReadError:
        daily_rows, daily_reports = [], []
    return verify.build_verification(
        month, monthly_rows, monthly_reports, daily_rows, daily_reports
    )


@app.route("/verify")
def verify_view():
    month = _verify_month(request.args)
    result = _build_verify(month)
    return render_template(
        "verify.html",
        result=result,
        verify_month=month,
        available_months=months_with_data(),
        last_run=store.verify_last(month),
        verify_history=store.verify_history(month),
        verify_store_ok=store.AVAILABLE,
        verify_msg=request.args.get("verify_msg", ""),
        # Minimal chrome context for base.html.
        period=month,
        period_label=f"Verification — {result['month_label']}",
        plant_filter="", segment_filter="", machine_filter="",
        plant_names=PLANT_NAMES,
        demo_mode=is_demo_mode(),
    )


@app.route("/verify.csv")
def verify_csv():
    month = _verify_month(request.args)
    result = _build_verify(month)
    csv_text = verify.rows_to_csv(result)
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="verification_{month}.csv"'
        },
    )


@app.route("/verify/log", methods=["POST"])
def verify_log():
    """Record one append-only verification-run entry. Writes NO fact data."""
    form = request.form
    month = _verify_month(form)
    run_by = (form.get("run_by", "") or "").strip()

    def _back(msg: str = ""):
        qs = f"month={month}"
        if msg:
            from urllib.parse import quote
            qs += f"&verify_msg={quote(msg)}"
        return redirect(f"/verify?{qs}")

    if not store.AVAILABLE:
        return _back("Audit log store is unavailable (no database configured).")
    if not run_by:
        return _back("Please enter your name to log a verification run.")
    result = _build_verify(month)
    try:
        store.verify_record(
            period=month,
            run_by=run_by,
            checks_passed=result["checks_passed"],
            checks_failed=result["checks_failed"],
            n_rows=result["grand"]["n_rows"],
            note=(form.get("note", "") or "").strip(),
        )
    except store.StoreError as e:
        return _back(f"Could not record verification: {e}")
    return _back(f"Verification logged by {run_by}.")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

REPORT_TYPES = [
    {"id": "extrusion_summary", "title": "Extrusion M/C Summary", "desc": "Pipe / Garden / HDPE: run hours, output kg, rejection %, utilisation %, labour cost/kg", "segments": ["Pipe", "Garden Pipe", "HDPE"]},
    {"id": "injection_summary", "title": "Injection Moulding M/C Summary", "desc": "Ideal vs actual hours, output, rejection, runner, utilisation %", "segments": ["PTMT", "CP"]},
    {"id": "mould_summary", "title": "Mould-wise Summary", "desc": "Per-mould output, run hours, runner %, rejection %, utilisation %", "segments": []},
    {"id": "mould_efficiency", "title": "Mould Age-in-Efficiency", "desc": "Per mould production pcs, ideal vs actual hours, efficiency %", "segments": []},
    {"id": "tank_summary", "title": "Tank Litre Summary", "desc": "Production & rejection by capacity (200–5000L) × layer, litres & pieces", "segments": ["Tanks"]},
    {"id": "compound_summary", "title": "Compound / Material Compilation", "desc": "Batch weight, mixer output, weight-loss %, by compound type", "segments": []},
    {"id": "segment_cost", "title": "Segment-wise Cost", "desc": "Labour/Power/Solar: headcount, paid hours, wages, per-kg & per-hour cost", "segments": []},
    {"id": "utilisation", "title": "Utilisation (Machine & Mould)", "desc": "Actual vs ideal hours, utilisation %, 3-month utilisation trend", "segments": []},
]


@app.route("/reports")
def reports():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    ctx["report_types"] = REPORT_TYPES
    return render_template("reports.html", **ctx)


@app.route("/reports/<report_id>")
def report_detail(report_id: str):
    rpt = next((r for r in REPORT_TYPES if r["id"] == report_id), None)
    if not rpt:
        abort(404)

    data = get_data(request.args)
    ctx = _common_ctx(data)

    rows = data["rows"]
    if rpt["segments"]:
        rows = [r for r in rows if r.segment in rpt["segments"]]

    # Build table depending on report type
    headers, table_rows, chart_labels, chart_values, chart_label = _build_report_table(report_id, rows, data)

    # Aggregate for this sub-set
    sub_overall = compute_metrics(rows)
    sub_validation = full_validate(rows, sub_overall)
    sub_dict = sub_overall.to_dict()

    narrative = None
    if ctx["has_claude"] and rows:
        narrative = get_narrative(
            view=rpt["title"],
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", rpt["id"], ""),
            metrics_summary={k: v for k, v in sub_dict.items() if isinstance(v, (int, float))},
            extra_context=f"Report type: {rpt['title']}",
            period_type=data["period_type"],
            deep=data["deep_override"],
        )

    ctx.update({
        "report": rpt,
        "headers": headers,
        "table_rows": table_rows,
        "chart_labels": _safe_json(chart_labels),
        "chart_values": _safe_json(chart_values),
        "chart_label": chart_label,
        "sub_overall": sub_dict,
        "sub_validation": sub_validation,
        "narrative": narrative,
    })
    return render_template("report_detail.html", **ctx)


def _build_report_table(report_id: str, rows, data: dict):
    from metrics import rollup_by_machine, rollup_by_mould, rollup_by_segment

    if report_id == "extrusion_summary":
        by_machine = rollup_by_machine(rows)
        headers = ["Machine", "Run Hrs", "Output (kg)", "Reject %", "Utilisation %", "Labour Cost/kg"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            if not mc:
                continue
            lc_per_kg = round(m.labour_cost / m.total_count, 2) if m.total_count > 0 else 0
            table_rows.append([mc, f"{m.run_time/60:.1f}", f"{m.total_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%", f"{m.utilisation_pct:.1f}%",
                                f"₹{lc_per_kg:.2f}"])
            chart_labels.append(mc)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output (kg)"

    elif report_id == "injection_summary":
        by_machine = rollup_by_machine(rows)
        oee_av = any(r.has_oee for r in rows)
        headers = ["Machine", "Ideal Hrs", "Actual Hrs", "Output (pcs)", "Reject %", "Runner %", "Utilisation %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            if not mc:
                continue
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            table_rows.append([mc, f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}", f"{m.total_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%", f"{round(m.runner_pct*100,2):.2f}%",
                                f"{m.utilisation_pct:.1f}%"])
            chart_labels.append(mc)
            chart_values.append(m.oee_pct if m.oee_available else m.output_efficiency_pct)
        return headers, table_rows, chart_labels, chart_values, ("OEE %" if oee_av else "Output Efficiency %")

    elif report_id == "mould_summary":
        by_mould = rollup_by_mould(rows)
        headers = ["Mould", "Output", "Run Hrs", "Runner %", "Reject %", "Utilisation %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{m.run_time/60:.1f}",
                                f"{round(m.runner_pct*100,2):.2f}%", f"{m.rejection_pct_display:.2f}%",
                                f"{m.utilisation_pct:.1f}%"])
            chart_labels.append(mould)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output"

    elif report_id == "mould_efficiency":
        by_mould = rollup_by_mould(rows)
        headers = ["Mould", "Output (pcs)", "Ideal Hrs", "Actual Hrs", "Efficiency %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            eff = round(m.utilisation_pct, 1)
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}", f"{eff}%"])
            chart_labels.append(mould)
            chart_values.append(eff)
        return headers, table_rows, chart_labels, chart_values, "Efficiency %"

    elif report_id == "tank_summary":
        by_mould = rollup_by_mould(rows)
        headers = ["Tank Size", "Production (pcs)", "Reject (pcs)", "Reject %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{m.reject_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%"])
            chart_labels.append(mould)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Production (pcs)"

    elif report_id == "compound_summary":
        by_compound = {}
        for r in rows:
            ct = r.compound_type or "Other"
            by_compound.setdefault(ct, []).append(r)
        headers = ["Compound", "Total Output", "Total Reject", "Weight Loss %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for ct, ct_rows in sorted(by_compound.items()):
            m = compute_metrics(ct_rows)
            wl = round((1 - m.quality) * 100, 2)
            table_rows.append([ct, f"{m.total_count:,.0f}", f"{m.reject_count:,.0f}", f"{wl:.2f}%"])
            chart_labels.append(ct)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output"

    elif report_id == "segment_cost":
        by_seg = rollup_by_segment(rows)
        headers = ["Segment", "Output", "Labour Cost", "Power Cost", "Solar Cost", "Labour/unit", "Power/unit"]
        table_rows = []
        chart_labels, chart_values = [], []
        for seg, m in sorted(by_seg.items()):
            lpu = round(m.labour_cost / m.total_count, 2) if m.total_count > 0 else 0
            ppu = round(m.power_cost / m.total_count, 2) if m.total_count > 0 else 0
            table_rows.append([seg, f"{m.total_count:,.0f}", f"₹{m.labour_cost:,.0f}",
                                f"₹{m.power_cost:,.0f}", f"₹{m.solar_cost:,.0f}",
                                f"₹{lpu:.2f}", f"₹{ppu:.2f}"])
            chart_labels.append(seg)
            chart_values.append(round(m.labour_cost, 0))
        return headers, table_rows, chart_labels, chart_values, "Labour Cost (₹)"

    elif report_id == "utilisation":
        by_machine = rollup_by_machine(rows)
        headers = ["Machine", "Ideal Hrs", "Actual Hrs", "Utilisation %", "Output Eff %", "OEE %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            if not mc:
                continue
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            oee_cell = f"{m.oee_pct:.1f}%" if m.oee_available else "n/a"
            table_rows.append([mc, f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}",
                                f"{m.utilisation_pct:.1f}%", f"{m.output_efficiency_pct:.1f}%", oee_cell])
            chart_labels.append(mc)
            chart_values.append(m.utilisation_pct)
        return headers, table_rows, chart_labels, chart_values, "Utilisation %"

    return [], [], [], [], ""


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

@app.route("/export-pdf/<report_id>")
def export_pdf(report_id: str):
    rpt = next((r for r in REPORT_TYPES if r["id"] == report_id), None)
    if not rpt:
        abort(404)

    data = get_data(request.args)
    rows = data["rows"]
    if rpt["segments"]:
        rows = [r for r in rows if r.segment in rpt["segments"]]

    headers, table_rows, _, _, _ = _build_report_table(report_id, rows, data)
    sub_overall = compute_metrics(rows)
    sub_validation = full_validate(rows, sub_overall)

    narrative = None
    conf_summary = None
    prov: dict = {}
    if data.get("has_claude") and rows:
        sub_dict = sub_overall.to_dict()
        narrative = get_narrative(
            view=rpt["title"],
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", report_id, ""),
            metrics_summary={k: v for k, v in sub_dict.items() if isinstance(v, (int, float))},
            period_type=data["period_type"],
            deep=data["deep_override"],
            provenance=prov,
        )
    conf = data.get("confirmation")
    if data.get("has_claude") and conf:
        issues_brief = [
            f"[{i['tier_label']}/{i['severity']}] {i['message']}"
            for i in conf.get("issues", [])
        ]
        conf_summary = summarize_confirmation(
            conf["status"], conf["score_label"], issues_brief
        )

    pdf_bytes = generate_report_pdf(
        title=rpt["title"],
        period_label=data["period_label"],
        overall=sub_overall.to_dict(),
        table_rows=table_rows,
        table_headers=headers,
        narrative=narrative,
        validation_status=sub_validation,
        confirmation=data.get("confirmation"),
        confirmation_summary=conf_summary,
        analysis_model=(prov.get("label") or data.get("analysis_label")) if narrative else None,
    )

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=prayag_{report_id}_{data['from_iso']}.pdf"},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "demo": is_demo_mode()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
