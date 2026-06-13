"""
Data layer: Google Sheets reader (via Replit Google Sheets connection) + demo
data fallback. No data passes through any AI model.

Reads use the Replit "Google Sheets" connector (blueprint id: google-sheet):
we fetch a short-lived OAuth access token from the Replit connectors API on
every read (tokens auto-refresh, so we never cache them), then call the public
Google Sheets REST API directly.
"""
from __future__ import annotations
import os
import json
import random
import datetime
import urllib.request
import urllib.parse
import urllib.error
from typing import List, Optional
from metrics import ShiftRow

DEMO_MODE = False  # set at runtime


class SheetReadError(RuntimeError):
    """Raised when a real Google Sheet is configured but cannot be read."""


def _connector_available() -> bool:
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    has_token = bool(os.environ.get("REPL_IDENTITY") or os.environ.get("WEB_REPL_RENEWAL"))
    return bool(host and has_token)


def _get_access_token() -> Optional[str]:
    """Fetch a fresh Google OAuth access token from the Replit connectors API."""
    host = os.environ.get("REPLIT_CONNECTORS_HOSTNAME", "").strip()
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_renewal = os.environ.get("WEB_REPL_RENEWAL")
    if repl_identity:
        xtoken = "repl " + repl_identity
    elif web_renewal:
        xtoken = "depl " + web_renewal
    else:
        return None
    if not host:
        return None

    url = (
        f"https://{host}/api/v2/connection"
        "?include_secrets=true&connector_names=google-sheet"
    )
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "X_REPLIT_TOKEN": xtoken}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    items = data.get("items", [])
    if not items:
        return None
    settings = items[0].get("settings", {}) or {}
    token = settings.get("access_token")
    if not token:
        oauth = settings.get("oauth", {}) or {}
        creds = oauth.get("credentials", {}) if isinstance(oauth, dict) else {}
        token = creds.get("access_token")
    return token


def _coerce_float(val) -> float:
    if val is None or val == "":
        return 0.0
    s = str(val).strip().replace(",", "").replace("₹", "").split()[0]
    try:
        return float(s)
    except (ValueError, IndexError):
        return 0.0


def _normalise_date(val: str) -> str:
    """Accept dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd → always return yyyy-mm-dd."""
    val = str(val).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return val


def is_demo_mode() -> bool:
    """Live mode requires both a configured Sheet ID and an authorized
    Google Sheets connection. Otherwise we serve deterministic demo data."""
    sid = os.environ.get("SHEET_ID", "").strip()
    return not (sid and _connector_available())


def _fetch_sheet_values(sheet_id: str, tab: str, token: str) -> List[list]:
    """Call the Google Sheets REST API and return the raw value matrix."""
    rng = urllib.parse.quote(tab, safe="")
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise SheetReadError(
                f"Couldn't find a tab named \u201c{tab}\u201d in that spreadsheet. "
                "Check the tab name and the SHEET_ID."
            ) from e
        if e.code in (401, 403):
            raise SheetReadError(
                "The Google account doesn't have access to that spreadsheet, "
                "or the connection needs to be re-authorized."
            ) from e
        raise SheetReadError(f"Google Sheets API error ({e.code}).") from e
    except urllib.error.URLError as e:
        raise SheetReadError("Couldn't reach Google Sheets. Please try again.") from e
    return data.get("values", []) or []


def read_sheet(tab: str, from_date: str, to_date: str) -> List[dict]:
    """
    Read rows from a Google Sheet tab for the given ISO date range.
    Falls back to demo data when no live sheet is configured.
    """
    if is_demo_mode():
        return _demo_shift_log(from_date, to_date) if tab == "Shift Log" else []

    sheet_id = os.environ["SHEET_ID"].strip()
    token = _get_access_token()
    if not token:
        raise SheetReadError(
            "The Google Sheets connection isn't authorized. "
            "Reconnect it from the integrations panel and try again."
        )

    values = _fetch_sheet_values(sheet_id, tab, token)
    if not values:
        return []

    headers = [str(h).strip() for h in values[0]]
    result = []
    for raw in values[1:]:
        row = {headers[i]: (raw[i] if i < len(raw) else "") for i in range(len(headers))}
        raw_date = row.get("date", row.get("Date", ""))
        if not raw_date:
            continue
        iso = _normalise_date(str(raw_date))
        if from_date <= iso <= to_date:
            result.append(row)
    return result


def rows_to_shift_rows(raw_rows: List[dict]) -> List[ShiftRow]:
    """Parse raw dicts into typed ShiftRow objects with deterministic cleaning."""
    result = []
    for row in raw_rows:
        def g(key, alt=""):
            for k in [key, key.lower(), key.replace(" ", "_").lower()]:
                if k in row:
                    return row[k]
            return alt

        date_raw = g("date") or g("Date")
        iso_date = _normalise_date(str(date_raw)) if date_raw else ""
        if not iso_date:
            continue

        sr = ShiftRow(
            date=iso_date,
            plant=str(g("plant") or g("Plant") or "").strip(),
            machine=str(g("machine") or g("Machine") or "").strip(),
            mould=str(g("mould") or g("Mould") or "").strip(),
            segment=str(g("segment") or g("Segment") or "").strip(),
            product=str(g("product") or g("Product") or "").strip(),
            shift=str(g("shift") or g("Shift") or "").strip(),
            ideal_rate=_coerce_float(g("ideal_rate") or g("Ideal Rate")),
            shift_len_min=_coerce_float(g("shift_len_min") or g("Shift Length (Min)")),
            planned_stops_min=_coerce_float(g("planned_stops_min") or g("Planned Stops (Min)")),
            downtime_min=_coerce_float(g("downtime_min") or g("Downtime (Min)")),
            downtime_reason=str(g("downtime_reason") or g("Downtime Reason") or "").strip(),
            total_count=_coerce_float(g("total_count") or g("Total Count")),
            reject_count=_coerce_float(g("reject_count") or g("Reject Count")),
            planned_output=_coerce_float(g("planned_output") or g("Planned Output")),
            unit=str(g("unit") or g("Unit") or "pcs").strip(),
            labour_cost=_coerce_float(g("labour_cost") or g("Labour Cost")),
            power_cost=_coerce_float(g("power_cost") or g("Power Cost")),
            solar_cost=_coerce_float(g("solar_cost") or g("Solar Cost")),
            runner_lumps=_coerce_float(g("runner_lumps") or g("Runner/Lumps")),
            compound_type=str(g("compound_type") or g("Compound Type") or "").strip(),
        )
        # Skip rows that are entirely zeroed-out or look like totals
        if sr.shift_len_min == 0 and sr.total_count == 0:
            continue
        result.append(sr)
    return result


# ---------------------------------------------------------------------------
# Demo data generator — deterministic seed for reproducibility
# ---------------------------------------------------------------------------
_PLANTS = ["KH", "VN", "WB"]
_PLANT_NAMES = {"KH": "Khed", "VN": "Verna", "WB": "West Bengal"}

_MACHINES = {
    "KH": [
        {"id": "EX-KH1", "seg": "Pipe", "ideal": 280, "unit": "kg"},
        {"id": "EX-KH2", "seg": "Garden Pipe", "ideal": 180, "unit": "kg"},
        {"id": "IM-KH1", "seg": "PTMT", "ideal": 450, "unit": "pcs"},
        {"id": "IM-KH2", "seg": "CP", "ideal": 380, "unit": "pcs"},
        {"id": "TK-KH1", "seg": "Tanks", "ideal": 12, "unit": "pcs"},
    ],
    "VN": [
        {"id": "EX-VN1", "seg": "HDPE", "ideal": 260, "unit": "kg"},
        {"id": "IM-VN1", "seg": "PTMT", "ideal": 420, "unit": "pcs"},
        {"id": "IM-VN2", "seg": "CP", "ideal": 360, "unit": "pcs"},
        {"id": "TK-VN1", "seg": "Tanks", "ideal": 10, "unit": "pcs"},
    ],
    "WB": [
        {"id": "EX-WB1", "seg": "Pipe", "ideal": 240, "unit": "kg"},
        {"id": "IM-WB1", "seg": "PTMT", "ideal": 400, "unit": "pcs"},
        {"id": "IM-WB2", "seg": "CP", "ideal": 340, "unit": "pcs"},
    ],
}

_MOULDS = {
    "PTMT": ["MT-25mm", "MT-32mm", "MT-40mm"],
    "CP": ["CP-15mm", "CP-20mm", "CP-25mm"],
    "Pipe": [""],
    "HDPE": [""],
    "Garden Pipe": [""],
    "Tanks": ["TK-200L", "TK-500L", "TK-1000L", "TK-2000L", "TK-5000L"],
}

_PRODUCTS = {
    "PTMT": ["Ball Valve 25mm", "Elbow 32mm", "Tee 40mm"],
    "CP": ["CP Tap 15mm", "Shower 20mm", "Bib Cock 25mm"],
    "Pipe": ["Pipe 110mm SWR", "Pipe 75mm SWR", "Pipe 50mm CPVC"],
    "HDPE": ["HDPE 63mm", "HDPE 90mm", "HDPE 110mm"],
    "Garden Pipe": ["Garden Hose 1/2\"", "Garden Hose 3/4\""],
    "Tanks": ["Loft Tank 200L", "Storage 500L", "Tank 1000L", "Tank 2000L", "Tank 5000L"],
}

_DOWNTIME_REASONS = [
    "Mould Change", "Material Change", "Breakdown - Hydraulic",
    "Breakdown - Electrical", "Die Head Change", "Colour Change",
    "Trial Run", "Power Failure", "Operator Absence", ""
]

_SHIFTS = ["A", "B", "C"]


def _demo_shift_log(from_date: str, to_date: str) -> List[dict]:
    rng = random.Random(42)  # fixed seed for reproducibility
    rows = []

    start = datetime.date.fromisoformat(from_date)
    end = datetime.date.fromisoformat(to_date)
    day = start
    while day <= end:
        for plant, machines in _MACHINES.items():
            for shift in _SHIFTS:
                for mc in machines:
                    seg = mc["seg"]
                    mould_list = _MOULDS.get(seg, [""])
                    mould = rng.choice(mould_list)
                    product = rng.choice(_PRODUCTS.get(seg, [seg]))

                    shift_len = 480  # 8h
                    planned_stops = rng.choice([0, 15, 20, 30])
                    ppt = shift_len - planned_stops
                    max_dt = int(ppt * 0.35)
                    downtime = rng.randint(0, max_dt)
                    run_time = ppt - downtime
                    dt_reason = rng.choice(_DOWNTIME_REASONS) if downtime > 0 else ""

                    ideal = mc["ideal"]
                    run_hrs = run_time / 60.0
                    ideal_output = run_hrs * ideal
                    # Performance between 0.70–0.98
                    perf = rng.uniform(0.70, 0.98)
                    total = round(ideal_output * perf, 2)
                    # Quality between 0.93–0.995
                    qual = rng.uniform(0.93, 0.995)
                    reject = round(total * (1 - qual), 2)
                    runner = round(total * rng.uniform(0.005, 0.02), 2) if seg in ["PTMT", "CP"] else 0.0

                    planned = round(ideal * (ppt / 60.0) * 0.90, 2)

                    # Costs
                    labour = round(rng.uniform(800, 1800), 2)
                    power = round(total * rng.uniform(2.5, 5.0), 2)
                    solar = round(power * rng.uniform(0.1, 0.3), 2)

                    rows.append({
                        "date": day.isoformat(),
                        "plant": plant,
                        "machine": mc["id"],
                        "mould": mould,
                        "segment": seg,
                        "product": product,
                        "shift": shift,
                        "ideal_rate": ideal,
                        "shift_len_min": shift_len,
                        "planned_stops_min": planned_stops,
                        "downtime_min": downtime,
                        "downtime_reason": dt_reason,
                        "total_count": total,
                        "reject_count": reject,
                        "planned_output": planned,
                        "unit": mc["unit"],
                        "labour_cost": labour,
                        "power_cost": power,
                        "solar_cost": solar,
                        "runner_lumps": runner,
                        "compound_type": seg if seg in ["PTMT", "CP", "HDPE"] else "",
                    })
        day += datetime.timedelta(days=1)
    return rows
