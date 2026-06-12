"""
Data layer: Google Sheets reader (via gspread service-account) + demo data fallback.
No data passes through any AI model.
"""
from __future__ import annotations
import os
import json
import random
import datetime
from typing import List, Optional
from metrics import ShiftRow

DEMO_MODE = False  # set at runtime


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
    sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    sid = os.environ.get("SHEET_ID", "").strip()
    return not (sa and sid)


def read_sheet(tab: str, from_date: str, to_date: str) -> List[dict]:
    """
    Read rows from a Google Sheet tab for the given ISO date range.
    Falls back to demo data if credentials are absent.
    """
    if is_demo_mode():
        return _demo_shift_log(from_date, to_date) if tab == "Shift Log" else []

    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    sheet_id = os.environ["SHEET_ID"]

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://spreadsheets.google.com/feeds",
              "https://www.googleapis.com/auth/drive.readonly"]
    creds_dict = json.loads(sa_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    ws = sh.worksheet(tab)
    rows = ws.get_all_records(default_blank="")
    # Filter by date
    result = []
    for row in rows:
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
