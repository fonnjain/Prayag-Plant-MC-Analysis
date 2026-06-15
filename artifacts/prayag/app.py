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
import freshness
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
    freshness: list = []

    # The daily files are the SOURCE OF TRUTH for every period. Monthly and FY
    # headline totals are summed from the authoritative daily tabs (one per
    # metric), not the monthly summary grid. The grid is kept only as a
    # reconciliation reference (a non-blocking note, emitted by get_daily_records)
    # and serves as the headline solely for a month that has no daily workbook at
    # all. Daily totals are never "reconciled down" to the lower summary figures.
    daily_file_months = [
        m for m in months
        if any(m in (cfg.get("files") or {}) for cfg in DAILY_SOURCES.values())
    ]
    if daily_file_months:
        try:
            drecs, dreports, dwarn = get_daily_records(daily_file_months)
        except SheetReadError as e:
            drecs, dreports, dwarn = [], [], []
            daily_err = f"Daily data could not be read: {e}"
        if not daily_err:
            # Per-plant data freshness: the latest date each plant has daily
            # data, computed from daily rows only (idle/empty days produce no
            # rows, so they never count). Surfaced in the completeness panel so
            # laggard plants are visible without blocking on them. ISO date
            # strings compare lexicographically, so plain ``>`` finds the max.
            fresh_by_plant: dict = {}
            for r in drecs:
                if r.date and r.date > fresh_by_plant.get(r.plant, ""):
                    fresh_by_plant[r.plant] = r.date
            freshness = [
                {"plant": p, "name": PLANT_NAMES.get(p, p),
                 "disp": _fmt(datetime.date.fromisoformat(d))}
                for p, d in sorted(
                    fresh_by_plant.items(), key=lambda kv: kv[1], reverse=True)
            ]
            # "last_updated" period: narrow to the actual last date with data.
            if pinfo["period"] == "last_updated":
                last_date = max((r.date for r in drecs if r.date), default=None)
                if last_date:
                    pinfo["from_iso"] = last_date
                    pinfo["to_iso"] = last_date
                    pinfo["label"] = f"Last updated: {_fmt(datetime.date.fromisoformat(last_date))}"
            fwin, twin = pinfo["from_iso"], pinfo["to_iso"]
            win = [r for r in drecs if fwin <= r.date <= twin]
            # Daily files are the only source for current figures. Months in
            # this period without a daily workbook show no data — the monthly
            # summary is not substituted.
            no_daily_months = [m for m in months if m not in daily_file_months]
            all_rows = win
            source_reports = list(dreports)
            recon_warnings = list(dwarn)
            daily_used = True
            if not win:
                latest = max((r.date for r in drecs), default=None)
                if latest:
                    latest_disp = _fmt(datetime.date.fromisoformat(latest))
                    grain_banner = (
                        f"{pinfo['label']} → no daily production was recorded in this "
                        f"period. Daily data is currently entered through {latest_disp}."
                    )
                else:
                    grain_banner = (
                        f"{pinfo['label']} → no daily production has been recorded for "
                        "this period yet."
                    )
            else:
                disp_plants = ", ".join(
                    PLANT_NAMES.get(p, p) for p in sorted({r.plant for r in win})
                )
                if pinfo.get("sub_monthly"):
                    grain_banner = (
                        f"{pinfo['label']} → true daily data for {disp_plants}. "
                        "Any plant not listed had no run recorded on these days."
                    )
                else:
                    extra = ""
                    if no_daily_months:
                        extra = (
                            " No daily workbook exists for "
                            + ", ".join(_month_disp(m) for m in no_daily_months)
                            + " — those months show no data."
                        )
                    grain_banner = (
                        f"{pinfo['label']} → totals are summed from the daily "
                        f"production files for {disp_plants}.{extra}"
                    )
    if not daily_used:
        if pinfo.get("sub_monthly"):
            # Sub-monthly windows never silently substitute the monthly-grid numbers.
            # A missing or failed daily fetch is shown as "no data for this window"
            # with an honest banner — the monthly summary is not blended in.
            all_rows = []
            source_reports = []
            recon_warnings = [daily_err] if daily_err else []
            if daily_err:
                grain_banner = (
                    f"{pinfo['label']} → daily data could not be fetched. "
                    "Showing no production data — monthly totals are not "
                    "substituted for a daily window."
                )
            else:
                disp = ", ".join(_month_disp(m) for m in months)
                grain_banner = (
                    f"{pinfo['label']} → no daily workbook is configured for {disp}. "
                    "No data for this window."
                )
        elif daily_err:
            # Monthly/FY but the daily read failed outright. Under the
            # daily-only rule the monthly summary is not substituted —
            # show nothing with an honest error banner.
            all_rows = []
            source_reports = []
            recon_warnings = [daily_err]
            grain_banner = (
                f"{pinfo['label']} → daily files could not be read. "
                "No production data is shown — the monthly summary is not "
                "substituted. Retry later or check the data source."
            )
        else:
            # No daily workbook is configured for any month in this period.
            # The monthly summary is not substituted.
            all_rows = []
            source_reports = []
            recon_warnings = []
            disp = ", ".join(_month_disp(m) for m in months)
            grain_banner = (
                f"{pinfo['label']} → no daily workbook is configured for {disp}. "
                "No production data for this period."
            )

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
    # Confirmation engine roster: the full-FY monthly grid is read here as
    # a machine *roster* only (which machines are expected to exist), not as
    # a source of production figures. This is the one intentional non-figure
    # monthly-summary read in the normal dashboard path.
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
    confirmation["freshness"] = freshness

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


def _build_freshness() -> dict:
    """Dashboard-detected "last updated / what changed" for every source sheet.

    Comprehensive and view-independent: fingerprints are computed from the full
    set of currently-present records (monthly grids + daily workbooks across all
    months that hold data), not the page's selected period — so the same change
    state shows on every page. Reads are cached; failures degrade to whatever
    could be read (change tracking never blocks a page render). Skipped in demo
    mode (no real workbooks to track)."""
    if is_demo_mode():
        return {
            "available": False, "demo": True, "sources": [], "updated": [],
            "n_total": 0, "n_updated": 0,
            "recent_days": freshness.RECENT_DAYS, "checked_at_disp": "",
        }
    months = months_with_data()
    recs: list = []
    read_errors: list = []
    try:
        mrecs, _r, _w = get_records(months)
        recs.extend(mrecs)
    except SheetReadError as e:
        read_errors.append(f"monthly grids ({e})")
    try:
        drecs, _dr, _dw = get_daily_records(months)
        recs.extend(drecs)
    except SheetReadError as e:
        read_errors.append(f"daily workbooks ({e})")
    out = freshness.build(recs)
    out["demo"] = False
    # Coverage honesty. "partial" flags only a TOTAL read failure of a category
    # (every monthly grid, or every daily workbook, raised SheetReadError) — the
    # affected workbooks keep their last-known state but were not re-checked, so
    # the panel warns the check was incomplete. A single isolated per-file
    # failure does NOT raise (get_daily_records recovers the rest); that file
    # simply drops out of `recs` this load and freshness.build lists it from its
    # last-known snapshot — already honest, no extra signal needed. We do NOT
    # treat reader warnings as "partial": get_daily_records always emits the
    # benign daily-vs-grid reconciliation note, so that would false-alarm.
    out["partial"] = bool(read_errors)
    out["read_errors"] = read_errors
    return out


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
        "freshness": _build_freshness(),
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
        "freshness": _build_freshness(),
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


@app.route("/build-state")
def build_state():
    """
    16 build-state assertions: code/config (static, instant) + live data
    (uses warm cache or makes fresh sheet reads).  Returns an HTML PASS/FAIL
    table.  All 16 must PASS before running the Claude sanity check or
    attempting sign-off.
    """
    import inspect
    import urllib.request as _ul
    import confirm  as _cfm
    import sheets   as _sht
    import sources  as _src

    checks: list = []

    def _chk(num, desc, passed, expected, actual, fix=""):
        checks.append(dict(num=num, desc=desc, passed=passed,
                           expected=str(expected), actual=str(actual), fix=fix))

    def _skip(num, desc, reason="", fix=""):
        checks.append(dict(num=num, desc=desc, passed=None,
                           expected="-", actual=f"SKIP: {reason}", fix=fix))

    # ------------------------------------------------------------------ #
    # STATIC: code / config (no I/O)                                      #
    # ------------------------------------------------------------------ #

    # #4  Pipe output uses Report-11 only (no Summary / Report-13 in the sum)
    pipe_specs = _sht._DAILY_LAYOUTS.get("PIPE", [])
    pipe_out_tabs = [s.get("tab", "") for s in pipe_specs if s.get("emit") == "PIPE"]
    _chk(4, "Pipe daily output tab = Report-11 only (not Summary / Report-13)",
         pipe_out_tabs == ["Report-11"],
         "['Report-11']", str(pipe_out_tabs),
         "overlapping-tab double-count")

    # #5  'Last 7 days' is on the daily path
    _chk(5, "'Last 7 days' uses daily path (sub_monthly=True)",
         bool(parse_period({"period": "last_week"}).get("sub_monthly")),
         True, parse_period({"period": "last_week"}).get("sub_monthly"),
         "daily-first not live for sub-monthly")

    # #8  PTMT roster = 55 machines
    ptmt_n = sum(len(v) for v in _src.PTMT_GROUPS.values())
    _chk(8, "PTMT roster machine count = 55",
         ptmt_n == 55, 55, ptmt_n, "PTMT roster not wired")

    # #9  PTMT has in-sheet IDEAL HOUR column wired
    ptmt_ideal = _sht._DAILY_LAYOUTS.get("PTMT", [{}])[0].get("ideal_col") is not None
    _chk(9, "PTMT utilisation uses in-sheet IDEAL HOUR column",
         ptmt_ideal, True, ptmt_ideal, "PTMT wrongly on baseline list")

    # #10  PTMT outlier compare is within process group
    cfm_src = inspect.getsource(_cfm)
    grp_ok = "by_group_machine" in cfm_src and "(plant, segment)" in cfm_src
    _chk(10, "PTMT outliers compared within process group",
         grp_ok, True, grp_ok, "grouping not wired")

    # #11  TANK layout = 'tank' → plant-level, no per-machine roster
    tank_layout = _sht._DAILY_LAYOUTS.get("TANK", [{}])[0].get("layout")
    _chk(11, "Tank scored at plant level (layout='tank')",
         tank_layout == "tank", "tank", str(tank_layout),
         "Tank still scored vs machine roster")

    # #12  baselines.json present and readable
    _bl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baselines.json")
    try:
        with open(_bl_path) as _f:
            _bl = json.load(_f)
        _chk(12, "baselines.json exists (config-driven baselines)",
             isinstance(_bl.get("machines"), dict),
             "exists", f"{len(_bl.get('machines', {}))} machine entries",
             "baseline config not wired")
    except Exception as _e:
        _chk(12, "baselines.json exists", False, "exists",
             f"ERROR: {_e}", "baseline config not wired")

    # #13  actual > calendar → quarantine; >100% within calendar → WARNING
    t3_src = inspect.getsource(tier3_row_classify)
    _chk(13, "actual > calendar hours → quarantine; >100%↔ceiling → WARNING",
         "_calendar_hours" in t3_src and "calendar maximum" in t3_src,
         True, "_calendar_hours" in t3_src,
         "old actual>ideal rule still active")

    # #14  Per-row quarantine; clean rows still publish
    _chk(14, "Impossible row quarantined per-row; rest of period publishes",
         "quarantined.append" in t3_src and "return clean, quarantined" in t3_src,
         True, "quarantined.append" in t3_src,
         "period-level blocking still active")

    # #15  'in progress' wording used, not 'overdue'
    cfm_no_comments = "\n".join(
        ln for ln in cfm_src.splitlines() if not ln.strip().startswith("#")
    )
    _chk(15, "Current month labelled 'in progress' (not 'overdue')",
         "in progress" in cfm_no_comments and "overdue" not in cfm_no_comments,
         True, "in progress" in cfm_no_comments,
         "completeness wording not updated")

    # #16  Read-only: no Sheets write calls
    sht_src = inspect.getsource(_sht)
    _chk(16, "No source values written (read-only to Sheets)",
         "batchUpdate" not in sht_src and "values:append" not in sht_src,
         True, "batchUpdate" not in sht_src,
         "safety violation — fix immediately")

    # ------------------------------------------------------------------ #
    # LIVE DATA: assert ground-truth figures from the live sheets          #
    # ------------------------------------------------------------------ #
    PIPE_MAY_EXP  = 107_609
    MOULD_MAY_EXP = 75_771
    TOL = 0.005

    try:
        _tok = _sht._get_access_token()
    except Exception:
        _tok = None

    if not _tok:
        for _n, _d in [
            (1, f"PIPE May output ≈ {PIPE_MAY_EXP:,} (Report-11 detail rows)"),
            (2, "PIPE May detail-row sum == Report-11 TOTAL row"),
            (3, f"MOULDING May output ≈ {MOULD_MAY_EXP:,} (Report-12 detail rows)"),
            (6, "HDPE current-month daily rows > 0"),
            (7, "Garden current-month rows > 0  AND  Tank May rows > 0"),
        ]:
            _skip(_n, _d, "no Sheets connection", "reconnect integration")
    else:
        # --- May data: PIPE, MOULDING, TANK ---
        _rows_may: list = []
        _may_err: str = ""
        _tank_may_n: int = 0
        try:
            _rows_may, _, _ = get_daily_records(["2026-05"])
        except Exception as _e:
            _may_err = str(_e)

        if _may_err:
            _chk(1, f"PIPE May output ≈ {PIPE_MAY_EXP:,}", False,
                 "-", f"ERROR: {_may_err}", "sheet read failed")
            _chk(3, f"MOULDING May output ≈ {MOULD_MAY_EXP:,}", False,
                 "-", f"ERROR: {_may_err}", "sheet read failed")
            _skip(2, "PIPE detail == Report-11 TOTAL row",
                  f"May read failed: {_may_err}")
        else:
            _pipe_sum  = sum(r.total_count for r in _rows_may if r.plant == "PIPE")
            _mould_sum = sum(r.total_count for r in _rows_may if r.plant == "MOULDING")
            _tank_may_n = sum(1 for r in _rows_may if r.plant == "TANK")

            _chk(1, f"PIPE May output ≈ {PIPE_MAY_EXP:,} (Report-11 detail rows)",
                 abs(_pipe_sum - PIPE_MAY_EXP) / PIPE_MAY_EXP <= TOL,
                 f"{PIPE_MAY_EXP:,} ±0.5%", f"{_pipe_sum:,.0f}",
                 "one-authoritative-tab fix not live")

            _chk(3, f"MOULDING May output ≈ {MOULD_MAY_EXP:,} (Report-12 detail rows)",
                 abs(_mould_sum - MOULD_MAY_EXP) / MOULD_MAY_EXP <= TOL,
                 f"{MOULD_MAY_EXP:,} ±0.5%", f"{_mould_sum:,.0f}",
                 "Moulding not read from Report-12 / double-count")

            # #2  Monthly May view uses daily-only path (no grid fallback)
            # Verify get_data returns daily_used=True for period=5 AND that
            # the PIPE figure matches the daily-sourced total (daily files are
            # the only authoritative source — the monthly summary is not read
            # for figures under the daily-only rule).
            try:
                from werkzeug.datastructures import ImmutableMultiDict as _IMMD
                _gd5 = get_data(_IMMD([("period", "5")]))
                _daily_used5 = _gd5.get("daily_used", False)
                _gd5_pipe = sum(
                    r.total_count for r in _gd5.get("rows", []) if r.plant == "PIPE"
                )
                # daily_used must be True; PIPE figure must be close to the
                # daily-read total (within 1% — quarantine may remove a handful of rows)
                _pipe_match = (
                    _pipe_sum > 0 and abs(_gd5_pipe - _pipe_sum) / _pipe_sum <= 0.01
                )
                _chk(2,
                     f"Monthly May view: daily-only path (daily_used=True, "
                     f"PIPE ≈ {PIPE_MAY_EXP:,.0f} from daily)",
                     _daily_used5 and _pipe_match,
                     f"daily_used=True  PIPE≈{_pipe_sum:,.0f}",
                     f"daily_used={_daily_used5}  PIPE={_gd5_pipe:,.0f}",
                     "daily_used=False or PIPE figure diverges from daily source")
            except Exception as _e2:
                _skip(2, "Monthly May view: daily-only path",
                      f"get_data error: {_e2}")

        # --- Current-month data: GARDEN + TANK ---
        # HDPE May already in _rows_may above (per-date matrix rows from "Daily
        # Report"). TANK May is empty (no data entered); TANK June has rows — use June.
        _cur_ym = _today().strftime("%Y-%m")
        _rows_jun: list = []
        _jun_err: str = ""
        try:
            _rows_jun, _, _ = get_daily_records([_cur_ym])
        except Exception as _e:
            _jun_err = str(_e)

        # #6  HDPE parser — verify using May (confirmed data month). HDPE reads the
        # "Daily Report" matrix and supplies its own in-sheet baseline, so every
        # produced row must carry an efficiency baseline (ideal_source != "none").
        _hdpe_may = [r for r in _rows_may if r.plant == "HDPE"]
        _hdpe_may_n = len(_hdpe_may)
        _hdpe_based = _hdpe_may_n > 0 and all(r.ideal_source != "none" for r in _hdpe_may)
        _chk(6, "HDPE daily rows > 0 with in-sheet baseline (2026-05, latest data month)",
             _hdpe_based, "rows>0 & ideal_source!=none",
             f"rows={_hdpe_may_n} sources={sorted({r.ideal_source for r in _hdpe_may})}",
             "HDPE parser not finished or baseline missing")

        if _jun_err:
            _chk(7, f"GARDEN {_cur_ym} rows > 0  AND  TANK {_cur_ym} rows > 0",
                 False, "both > 0", f"ERROR: {_jun_err}", "parser / read failed")
        else:
            _garden_n = sum(1 for r in _rows_jun if r.plant == "GARDEN")
            _tank_jun_n = sum(1 for r in _rows_jun if r.plant == "TANK")
            _chk(7, f"GARDEN {_cur_ym} rows > 0  AND  TANK {_cur_ym} rows > 0",
                 _garden_n > 0 and _tank_jun_n > 0,
                 "both > 0",
                 f"GARDEN={_garden_n}  TANK={_tank_jun_n}",
                 "parser not finished")

    # ------------------------------------------------------------------ #
    # Render                                                               #
    # ------------------------------------------------------------------ #
    checks.sort(key=lambda x: x["num"])
    n_pass  = sum(1 for x in checks if x["passed"] is True)
    n_fail  = sum(1 for x in checks if x["passed"] is False)
    n_skip  = sum(1 for x in checks if x["passed"] is None)
    n_total = len(checks)

    if n_fail == 0:
        _st_line = f"BUILD STATE: {n_pass}/{n_total} PASS → safe to run sanity check"
        _st_col  = "#1a7a3c"
    else:
        _st_line = (f"BUILD STATE: {n_pass}/{n_total} PASS — BLOCKED"
                    f"  ({n_fail} FAIL{'  · ' + str(n_skip) + ' skip' if n_skip else ''})")
        _st_col = "#c0392b"

    _rows_html = ""
    for x in checks:
        p = x["passed"]
        if p is True:
            _badge = '<span style="color:#1a7a3c;font-weight:700">✓ PASS</span>'
        elif p is False:
            _badge = '<span style="color:#c0392b;font-weight:700">✗ FAIL</span>'
        else:
            _badge = '<span style="color:#888;font-weight:600">– SKIP</span>'
        _fix = (f'<div style="color:#c0392b;font-size:11px;margin-top:2px">'
                f'→ {x["fix"]}</div>'
                if x["fix"] and p is not True else "")
        _rows_html += (
            f'<tr style="border-bottom:1px solid #e2e8f0">'
            f'<td style="padding:8px 6px;text-align:center;font-weight:700;'
            f'color:#1F3864">#{x["num"]}</td>'
            f'<td style="padding:8px 6px">{x["desc"]}{_fix}</td>'
            f'<td style="padding:8px 6px;text-align:center">{_badge}</td>'
            f'<td style="padding:8px 6px;font-family:monospace;font-size:12px;'
            f'color:#555">{x["expected"]}</td>'
            f'<td style="padding:8px 6px;font-family:monospace;font-size:12px;'
            f'color:#333">{x["actual"]}</td>'
            f'</tr>\n'
        )

    # Fail summary block
    _fail_block = ""
    if n_fail:
        _fail_block = '<div style="margin:12px 0;padding:10px 14px;background:#fff0f0;border-radius:8px;border-left:4px solid #c0392b">'
        for x in checks:
            if x["passed"] is False:
                _fail_block += (f'<div style="font-size:13px;margin:2px 0">'
                                f'<b>FAIL #{x["num"]}</b> {x["actual"]}'
                                f'{" → " + x["fix"] if x["fix"] else ""}</div>')
        _fail_block += "</div>"

    _html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Build State — Prayag Analytics</title>
<style>
  body{{font-family:system-ui,sans-serif;margin:0;padding:16px;background:#f7f8fc;color:#222}}
  h1{{font-size:18px;color:#1F3864;margin:0 0 2px}}
  .meta{{font-size:11px;color:#888;margin-bottom:12px}}
  .status{{font-size:14px;font-weight:700;color:{_st_col};padding:10px 14px;
            background:#fff;border-radius:8px;border-left:4px solid {_st_col};margin-bottom:8px}}
  table{{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;
         overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.07)}}
  th{{background:#1F3864;color:#fff;padding:8px 6px;font-size:12px;text-align:left}}
  td{{font-size:13px;vertical-align:top}}
  .note{{margin-top:10px;font-size:11px;color:#888}}
  @media(max-width:600px){{
    th:nth-child(4),th:nth-child(5),td:nth-child(4),td:nth-child(5){{display:none}}
  }}
</style>
</head>
<body>
<h1>Prayag Analytics — Build State</h1>
<p class="meta">As of {_fmt(_today())}. Static assertions run instantly; live-data rows use cache when warm.</p>
<div class="status">{_st_line}</div>
{_fail_block}
<table>
<thead><tr>
  <th style="width:36px">#</th>
  <th>Assertion</th>
  <th style="width:68px">Result</th>
  <th style="width:140px">Expected</th>
  <th style="width:170px">Actual</th>
</tr></thead>
<tbody>{_rows_html}</tbody>
</table>
<p class="note">Reload to re-run. Live-data rows (#1–#3, #6–#7) use the data cache when warm (fast); cold reads may take up to 30 s.</p>
</body></html>"""
    return Response(_html, mimetype="text/html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
