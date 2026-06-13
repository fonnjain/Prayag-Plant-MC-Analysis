"""
Data layer: live Google Sheets reader (via the Replit Google Sheets connection)
plus a deterministic demo fallback. No data passes through any AI model.

Reads use the Replit "Google Sheets" connector (blueprint id: google-sheet):
we fetch a short-lived OAuth access token from the Replit connectors API and
reuse it until shortly before it expires, then call the public Google Sheets
REST API directly, reading each workbook by its pinned file ID (see sources.py).
"""
from __future__ import annotations
import os
import json
import time
import random
import datetime
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional, Tuple

from metrics import Record
import parsers
import sources

# ---------------------------------------------------------------------------
# Connection + token (cached within the process until near expiry)
# ---------------------------------------------------------------------------
_token_cache: dict = {"token": None, "exp": 0.0}
_data_cache: dict = {}          # months_key -> (ts, payload)
_DATA_TTL = 120.0               # seconds


class SheetReadError(RuntimeError):
    """Raised when a real Google Sheet is configured but cannot be read."""


def _connector_available() -> bool:
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    has_token = bool(os.environ.get("REPL_IDENTITY") or os.environ.get("WEB_REPL_RENEWAL"))
    return bool(host and has_token)


def is_demo_mode() -> bool:
    """Live mode needs an authorized Google Sheets connection; else demo data."""
    return not _connector_available()


def _fetch_token() -> Tuple[Optional[str], float]:
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_renewal = os.environ.get("WEB_REPL_RENEWAL")
    if repl_identity:
        xtoken = "repl " + repl_identity
    elif web_renewal:
        xtoken = "depl " + web_renewal
    else:
        return None, 0.0
    if not host:
        return None, 0.0

    url = (
        f"https://{host}/api/v2/connection"
        "?include_secrets=true&connector_names=google-sheet"
    )
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "X_REPLIT_TOKEN": xtoken}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
    except (urllib.error.URLError, ValueError) as e:
        raise SheetReadError(
            "Couldn't verify the Google Sheets connection. "
            "Please reconnect it and try again."
        ) from e

    items = data.get("items", [])
    if not items:
        return None, 0.0
    settings = items[0].get("settings", {}) or {}
    token = settings.get("access_token")
    expires_at = settings.get("expires_at")
    if not token:
        oauth = settings.get("oauth", {}) or {}
        creds = oauth.get("credentials", {}) if isinstance(oauth, dict) else {}
        token = creds.get("access_token")
        expires_at = expires_at or creds.get("expires_at") or creds.get("expiry_date")

    # Resolve expiry to an epoch seconds value; default to a short TTL.
    exp_epoch = time.time() + 240.0
    if isinstance(expires_at, (int, float)):
        exp_epoch = expires_at / 1000.0 if expires_at > 1e12 else float(expires_at)
    elif isinstance(expires_at, str):
        try:
            exp_epoch = datetime.datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            ).timestamp()
        except ValueError:
            pass
    return token, exp_epoch


def _get_access_token() -> Optional[str]:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["exp"] - 60:
        return _token_cache["token"]
    token, exp = _fetch_token()
    if token:
        _token_cache["token"] = token
        _token_cache["exp"] = exp
    return token


# ---------------------------------------------------------------------------
# Generic Sheets REST helpers
# ---------------------------------------------------------------------------
def _api_get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SheetReadError("Spreadsheet or tab not found (404).") from e
        if e.code in (401, 403):
            raise SheetReadError(
                "The Google account doesn't have access to a configured "
                "spreadsheet, or the connection needs to be re-authorized."
            ) from e
        raise SheetReadError(f"Google Sheets API error ({e.code}).") from e
    except urllib.error.URLError as e:
        raise SheetReadError("Couldn't reach Google Sheets. Please try again.") from e


def list_tabs(file_id: str, token: str) -> List[str]:
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}"
        "?fields=sheets.properties.title"
    )
    data = _api_get(url, token)
    return [s.get("properties", {}).get("title", "") for s in data.get("sheets", [])]


def read_values(file_id: str, tab: str, token: str) -> List[list]:
    rng = urllib.parse.quote(tab, safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values/{rng}"
    return _api_get(url, token).get("values", []) or []


def batch_get(file_id: str, tabs: List[str], token: str) -> dict:
    """Return {tab_title: value_matrix} for many tabs in one HTTP call."""
    if not tabs:
        return {}
    q = "&".join("ranges=" + urllib.parse.quote(t, safe="") for t in tabs)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}/values:batchGet?{q}"
    data = _api_get(url, token)
    out = {}
    for tab, vr in zip(tabs, data.get("valueRanges", [])):
        out[tab] = vr.get("values", []) or []
    return out


# ---------------------------------------------------------------------------
# Live monthly load — annual M/C summary workbooks
# ---------------------------------------------------------------------------
def _load_annual_family(src: dict, token: str) -> Tuple[List[Record], dict]:
    """Read one annual family workbook → (records, source_report)."""
    file_id = src["file_id"]
    tabs = list_tabs(file_id, token)
    detail_tabs = [t for t in tabs if parsers.DETAIL_TAB_RE.match(t.strip())]

    want = list(detail_tabs)
    if src["tab"] in tabs:
        want.append(src["tab"])
    matrices = batch_get(file_id, want, token)

    records: List[Record] = []
    for t in detail_tabs:
        records.extend(parsers.parse_mc_detail(
            matrices.get(t, []),
            plant=src["plant"],
            segment=src["segment"],
            unit=src["unit"],
            source_file=file_id,
            source_tab=t,
        ))

    # Reconcile summed detail output vs the grid TOTAL row.
    recon = None
    grid_total = parsers.grid_total_output(matrices.get(src["tab"], []))
    detail_total = sum(r.total_count for r in records)
    if grid_total is not None and grid_total > 0:
        diff = abs(detail_total - grid_total) / grid_total
        recon = {
            "grid_total": round(grid_total, 1),
            "detail_total": round(detail_total, 1),
            "diff_pct": round(diff * 100, 2),
            "ok": diff <= 0.02,
        }

    months = sorted({r.period for r in records})
    report = {
        "family": src["family"],
        "title": src["title"],
        "file_id": file_id,
        "tab": src["tab"],
        "detail_tabs": detail_tabs,
        "grain": "monthly",
        "months_available": months,
        "record_count": len(records),
        "segment": src["segment"],
        "plant": src["plant"],
        "reconcile": recon,
        "field_map": parsers.MC_DETAIL_FIELD_MAP,
    }
    return records, report


def _load_live_monthly(token: str) -> dict:
    all_records: List[Record] = []
    reports: List[dict] = []
    warnings: List[str] = []

    for src in sources.ANNUAL_SOURCES:
        try:
            recs, report = _load_annual_family(src, token)
        except SheetReadError as e:
            raise SheetReadError(
                f"Couldn't read {src['title']} "
                f"(family '{src['family']}', tab '{src['tab']}', "
                f"file {src['file_id']}): {e}"
            ) from e
        all_records.extend(recs)
        reports.append(report)

        # Surface silent layout drift: a family that parses nothing despite
        # having detail tabs almost certainly means the parser/header detection
        # broke, not that the factory produced zero output.
        if report["record_count"] == 0:
            if report["detail_tabs"]:
                warnings.append(
                    f"{report['title']}: found {len(report['detail_tabs'])} machine "
                    "tab(s) but parsed 0 rows — sheet layout may have changed."
                )
            else:
                warnings.append(
                    f"{report['title']}: no machine (M/C-n) tabs found in the workbook."
                )

        recon = report.get("reconcile")
        if recon is None and report["record_count"] > 0:
            # Reconciliation could not run — the grid TOTAL row/tab was missing
            # or unreadable, so we cannot cross-check the parsed totals.
            warnings.append(
                f"{report['title']}: could not read the grid TOTAL row, so parsed "
                "totals were not reconciled against the source."
            )
        elif recon and not recon["ok"]:
            warnings.append(
                f"{report['title']}: month rows sum to {recon['detail_total']:.0f} "
                f"but grid TOTAL is {recon['grid_total']:.0f} "
                f"({recon['diff_pct']:.1f}% off)"
            )

    return {
        "records": all_records,
        "reports": reports,
        "recon_warnings": warnings,
        "grain": "monthly",
    }


def _live_payload() -> dict:
    """Cached full live monthly payload (all families, all FY months)."""
    now = time.time()
    cached = _data_cache.get("live")
    if cached and now - cached[0] < _DATA_TTL:
        return cached[1]
    token = _get_access_token()
    if not token:
        raise SheetReadError(
            "The Google Sheets connection isn't authorized. "
            "Reconnect it from the integrations panel and try again."
        )
    payload = _load_live_monthly(token)
    _data_cache["live"] = (now, payload)
    return payload


def get_records(months: List[str]) -> Tuple[List[Record], List[dict], List[str]]:
    """Return (records filtered to ``months``, source reports, recon warnings).

    Falls back to deterministic demo data when no connection is available.
    """
    if is_demo_mode():
        recs = _demo_records_for_months(months)
        return recs, _demo_reports(), []

    payload = _live_payload()
    wanted = set(months)
    recs = [r for r in payload["records"] if r.period in wanted]
    return recs, payload["reports"], payload["recon_warnings"]


def detected_sources() -> List[dict]:
    """Reviewable mapping of every source the engine reads."""
    if is_demo_mode():
        return _demo_reports()
    return _live_payload()["reports"]


def months_with_data() -> List[str]:
    """All FY months that currently hold any real data (for the period engine)."""
    if is_demo_mode():
        return sorted({d for d in _demo_month_index()})
    return sorted({r.period for r in _live_payload()["records"]})


# ---------------------------------------------------------------------------
# Demo data (deterministic) — daily grain so OEE path is exercised offline
# ---------------------------------------------------------------------------
_DEMO_PLANTS = {
    "PIPE": [("PIPE EX-1", "Pipe", 280, "kg"), ("PIPE EX-2", "Pipe", 240, "kg")],
    "GARDEN": [("GARDEN M/C-1", "Garden Pipe", 180, "kg")],
    "HDPE": [("HDPE M/C-1", "HDPE", 260, "kg")],
    "MOULDING": [("MOULDING M/C-1", "Moulding", 450, "pcs"),
                 ("MOULDING M/C-2", "Moulding", 380, "pcs")],
}
_DEMO_REASONS = [
    "Mould Change", "Material Change", "Breakdown - Hydraulic",
    "Breakdown - Electrical", "Colour Change", "Power Failure", "",
]
_DEMO_SHIFTS = ["A", "B", "C"]


def _demo_records_range(from_iso: str, to_iso: str) -> List[Record]:
    rng = random.Random(42)
    rows: List[Record] = []
    start = datetime.date.fromisoformat(from_iso)
    end = datetime.date.fromisoformat(to_iso)
    day = start
    while day <= end:
        for plant, machines in _DEMO_PLANTS.items():
            for shift in _DEMO_SHIFTS:
                for mc_id, seg, ideal, unit in machines:
                    shift_len = 480
                    planned_stops = rng.choice([0, 15, 20, 30])
                    ppt = shift_len - planned_stops
                    downtime = rng.randint(0, int(ppt * 0.35))
                    run = ppt - downtime
                    reason = rng.choice(_DEMO_REASONS) if downtime > 0 else ""
                    run_hrs = run / 60.0
                    ideal_out = run_hrs * ideal
                    total = round(ideal_out * rng.uniform(0.70, 0.98), 2)
                    reject = round(total * (1 - rng.uniform(0.93, 0.995)), 2)
                    runner = round(total * rng.uniform(0.005, 0.02), 2) if unit == "pcs" else 0.0
                    rows.append(Record(
                        grain="daily", has_oee=True,
                        period=day.strftime("%Y-%m"), date=day.isoformat(),
                        plant=plant, segment=seg, machine=mc_id, unit=unit,
                        shift=shift, ideal_rate=ideal, shift_len_min=shift_len,
                        planned_stops_min=planned_stops, downtime_min=downtime,
                        downtime_reason=reason, total_count=total,
                        reject_count=reject, runner_lumps=runner,
                        planned_output=round(ideal * (ppt / 60.0) * 0.9, 2),
                        labour_cost=round(rng.uniform(800, 1800), 2),
                        power_cost=round(total * rng.uniform(2.5, 5.0), 2),
                        solar_cost=round(total * rng.uniform(0.4, 1.2), 2),
                        source_family=seg, source_file="demo", source_tab="demo",
                    ))
        day += datetime.timedelta(days=1)
    return rows


def _demo_month_index() -> List[str]:
    return ["2026-04", "2026-05", "2026-06"]


def _demo_records_for_months(months: List[str]) -> List[Record]:
    out: List[Record] = []
    for ym in months:
        if ym not in _demo_month_index():
            continue
        y, m = int(ym[:4]), int(ym[5:7])
        first = datetime.date(y, m, 1)
        if m == 12:
            nxt = datetime.date(y + 1, 1, 1)
        else:
            nxt = datetime.date(y, m + 1, 1)
        last = nxt - datetime.timedelta(days=1)
        out.extend(_demo_records_range(first.isoformat(), last.isoformat()))
    return out


def _demo_reports() -> List[dict]:
    reports = []
    for plant, machines in _DEMO_PLANTS.items():
        reports.append({
            "family": plant.lower(),
            "title": f"DEMO — {sources.PLANT_NAMES.get(plant, plant)}",
            "file_id": "demo",
            "tab": "demo daily log",
            "detail_tabs": [m[0] for m in machines],
            "grain": "daily",
            "months_available": _demo_month_index(),
            "record_count": 0,
            "segment": machines[0][1],
            "plant": plant,
            "reconcile": None,
        })
    return reports
