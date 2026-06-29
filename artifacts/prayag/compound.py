"""Compound Compilation — deterministic material/compound balance for Pipe.

Recomputes the monthly / FY compound mass-balance for the Pipe plant from the
Pipe & Fitting daily "Mixer Logbook" tabs (one per compound type), entirely
from raw daily cells (daily-first, like the rest of the app). The in-sheet
"Compound 6-10" rollup is a reconciliation / validation reference ONLY and is
never used to produce a headline figure.

Balance identity, mixed compounds: Closing = Opening + Material_out − Given.
For CPVC Fittings (purchased, CG 122): Closing = Opening + Purchase − Issue.
Weight-loss % is always recomputed (loss / batch), never read from a stored %.

Pure and network-free.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# Compound type -> source tab + layout. Order matches the factory compilation.
# ``in_total`` marks the 7 official compounds (the "Compound 6-10" columns);
# FC is a separate fitting compound shown alongside but excluded from the
# grand total and the reconciliation so the headline still ties out.
COMPOUNDS: List[dict] = [
    {"key": "CPVC",   "label": "CPVC",         "tab": "Report-6",    "layout": "mixer", "to": "Pipe",    "in_total": True},
    {"key": "UPVC",   "label": "UPVC",         "tab": "Report-7",    "layout": "mixer", "to": "Pipe",    "in_total": True},
    {"key": "AGRI",   "label": "AGRI",         "tab": "Report-8(A)", "layout": "mixer", "to": "Pipe",    "in_total": True},
    {"key": "SWR",    "label": "SWR",          "tab": "Report-8(B)", "layout": "mixer", "to": "Pipe",    "in_total": True},
    {"key": "UPVC_F", "label": "UPVC F",       "tab": "Report-9",    "layout": "mixer", "to": "Fitting", "in_total": True},
    {"key": "SWR_F",  "label": "SWR / AGRI F", "tab": "Report-10",   "layout": "mixer", "to": "Fitting", "in_total": True},
    {"key": "CPVC_F", "label": "CPVC F",       "tab": "CG 122",      "layout": "cg",    "to": "Fitting", "in_total": True},
    {"key": "FC",     "label": "FC",           "tab": "FC",          "layout": "mixer", "to": "Fitting", "in_total": False},
]

# (field key, display label) for the balance grid, in row order.
BALANCE_ROWS: List[tuple] = [
    ("opening",  "Opening Stock"),
    ("pulvizer", "Pulvizer"),
    ("batch",    "Total Batch Weight"),
    ("material", "Total Material out of Mixer"),
    ("given",    "Total Compound given"),
    ("loss",     "Total Weight Loss at Mixer"),
    ("loss_pct", "Av. Weight Loss %"),
    ("closing",  "Closing Stock"),
]

# Pipe-extrusion compounds (consumed by the Pipe plant) — used for the yield
# panel. The Fittings compounds go to the moulding/fitting plant instead.
PIPE_KEYS = {"CPVC", "UPVC", "AGRI", "SWR"}

PASS, FAIL, NA = "PASS", "FAIL", "NA"


def _in_window(d: dict, window: Optional[tuple]) -> bool:
    """True if day ``d`` falls inside an inclusive [start_iso, end_iso] window.
    No window → every day passes. A day missing its date is excluded in a window
    (a partial slice cannot honestly include an undated row)."""
    if not window:
        return True
    ds = d.get("date")
    return bool(ds) and window[0] <= ds <= window[1]


def _agg_mixer(parses: List[dict], window: Optional[tuple] = None) -> dict:
    """Aggregate a compound's mixer-logbook day rows.

    Default (no window) = a month/FY BALANCE: opening from the first month,
    closing = opening + material − given. With a sub-monthly ``window`` it is a
    FLOW view: only the window's day rows are summed and opening/closing stock
    (a point-in-time month figure) are left blank — a partial slice has no
    meaningful opening/closing balance.
    """
    flow = window is not None
    opening = None if flow else (parses[0].get("opening", 0.0) if parses else 0.0)
    batch = material = given = loss = pulvizer = 0.0
    chems: Dict[str, float] = {}
    days: List[dict] = []
    given_label = ""
    for p in parses:
        given_label = p.get("given_label") or given_label
        for d in p.get("days", []):
            if not _in_window(d, window):
                continue
            batch += d["batch"]
            material += d["material"]
            given += d["given"]
            loss += d["loss"]
            pulvizer += d["pulvizer"]
            for nm, v in d["chems"].items():
                chems[nm] = chems.get(nm, 0.0) + v
            days.append(d)
    closing = None if flow else opening + material - given
    loss_pct = (loss / batch) if batch else None
    return {
        "kind": "mixer", "flow": flow, "opening": opening, "pulvizer": pulvizer,
        "batch": batch, "material": material, "given": given, "loss": loss,
        "loss_pct": loss_pct, "closing": closing, "chems": chems, "days": days,
        "given_label": given_label or "Total Compound given",
        "has_data": bool(days),
    }


def _agg_cg(parses: List[dict], window: Optional[tuple] = None) -> dict:
    flow = window is not None
    opening = None if flow else (parses[0].get("opening", 0.0) if parses else 0.0)
    purchase = issue = 0.0
    days: List[dict] = []
    for p in parses:
        for d in p.get("days", []):
            if not _in_window(d, window):
                continue
            purchase += d["purchase"]
            issue += d["issue"]
            days.append(d)
    closing = None if flow else opening + purchase - issue
    return {
        "kind": "cg", "flow": flow, "opening": opening, "pulvizer": 0.0,
        "batch": 0.0, "material": purchase, "given": issue, "loss": 0.0,
        "loss_pct": None, "closing": closing, "purchase": purchase, "issue": issue,
        "chems": {}, "days": days, "given_label": "Issued to Fitting",
        "has_data": bool(days),
    }


def build_compilation(by_compound: Dict[str, List[dict]], months: List[str],
                      window: Optional[tuple] = None) -> dict:
    """Assemble the full compound-balance compilation.

    ``by_compound``: {compound_key: [per-month parse dict, chronological]}.
    Returns a template-ready dict: per-compound balance columns, the 7-compound
    grand TOTAL, the raw-material item breakdown matrix, and the yield split.
    """
    cols: List[dict] = []
    total = {k: 0.0 for k in ("opening", "pulvizer", "batch", "material", "given", "loss", "closing")}
    item_matrix: Dict[str, Dict[str, float]] = {}

    flow = window is not None
    for spec in COMPOUNDS:
        parses = by_compound.get(spec["key"], [])
        bal = _agg_cg(parses, window) if spec["layout"] == "cg" else _agg_mixer(parses, window)
        col = dict(spec)
        col.update(bal)
        cols.append(col)
        if spec["in_total"]:
            for k in total:
                total[k] += bal.get(k, 0.0) or 0.0
            for nm, v in bal.get("chems", {}).items():
                item_matrix.setdefault(nm, {})[spec["key"]] = item_matrix.get(nm, {}).get(spec["key"], 0.0) + v

    total["loss_pct"] = (total["loss"] / total["batch"]) if total["batch"] else None
    if flow:
        # A partial window has no opening/closing stock balance — only flow.
        total["opening"] = None
        total["closing"] = None

    items: List[dict] = []
    for nm, bycol in item_matrix.items():
        rowtot = sum(bycol.values())
        if rowtot <= 0:
            continue
        items.append({"name": nm, "by": bycol, "total": rowtot})
    items.sort(key=lambda r: -r["total"])

    # Yield: compound consumed by the Pipe plant vs Fitting plant.
    pipe_given = sum(c["given"] for c in cols if c["key"] in PIPE_KEYS)
    fitting_given = sum(c["given"] for c in cols if c["in_total"] and c["key"] not in PIPE_KEYS)

    return {
        "cols": cols,
        "total": total,
        "materials": items,
        "balance_rows": BALANCE_ROWS,
        "months": list(months),
        "has_data": any(c["has_data"] for c in cols),
        "pipe_given": pipe_given,
        "fitting_given": fitting_given,
        "flow": flow,
    }


def month_trend(by_compound: Dict[str, List[dict]], months: List[str]) -> List[dict]:
    """Per-month series for the FY trend chart.

    For each month in ``months`` (chronological), recompute the whole-month
    balance and return the total compound given (kg) and the average weight-loss
    %. Pure: re-uses ``build_compilation`` over each month's parses (tagged with
    ``ym`` at load time), so the trend ties out to the same daily-first numbers
    the grid shows.
    """
    out: List[dict] = []
    for ym in months:
        sub = {k: [p for p in plist if p.get("ym") == ym]
               for k, plist in by_compound.items()}
        comp = build_compilation(sub, [ym])
        if not comp["has_data"]:
            continue
        total = comp["total"]
        out.append({
            "ym": ym,
            "given": round(total["given"] or 0.0, 0),
            "loss_pct": round((total["loss_pct"] or 0.0) * 100, 2),
        })
    return out


def validate(comp: dict, rollup: Dict[str, dict], tol: float = 0.005) -> dict:
    """Reconcile the recomputed daily balance against the in-sheet "Compound
    6-10" monthly rollup (summed across the period).

    Each compound is checked on batch / material / given. A check is PASS within
    ``tol`` (0.5%), FAIL beyond it, NA when the rollup has no figure. Honest —
    a mismatch is surfaced and located, never auto-passed.
    """
    if not rollup:
        return {"available": False, "status": NA, "rows": [], "n_pass": 0, "n_fail": 0, "n_na": 0, "diagnoses": []}

    # Sum the additive rollup fields across all months in the period, and track
    # the FIRST month's opening stock and the LAST month's closing stock per
    # compound (opening/closing are point-in-time balances, NOT additive). The
    # rollup dict is keyed chronologically, so insertion order gives first/last.
    summed: Dict[str, Dict[str, float]] = {}
    first_open: Dict[str, float] = {}
    last_close: Dict[str, float] = {}
    for _ym, rd in rollup.items():
        for key, fields in rd.items():
            dst = summed.setdefault(key, {})
            for f, v in fields.items():
                dst[f] = dst.get(f, 0.0) + v
            if "opening" in fields and key not in first_open:
                first_open[key] = fields["opening"]
            if "closing" in fields:
                last_close[key] = fields["closing"]

    rows: List[dict] = []
    fail_keys: set = set()
    n_pass = n_fail = n_na = 0
    for col in comp["cols"]:
        if not col["in_total"]:
            continue
        ref = summed.get(col["key"])
        for field, flabel in (("batch", "Batch Weight"), ("material", "Material out"), ("given", "Compound given")):
            recomputed = col.get(field, 0.0) or 0.0
            sheet = (ref or {}).get(field)
            if sheet is None or (sheet == 0 and recomputed == 0):
                status = NA
                n_na += 1
                diff_pct = None
            else:
                denom = abs(sheet) if sheet else max(abs(recomputed), 1.0)
                diff_pct = abs(recomputed - sheet) / denom
                if diff_pct <= tol:
                    status = PASS
                    n_pass += 1
                else:
                    status = FAIL
                    n_fail += 1
                    fail_keys.add(col["key"])
            rows.append({
                "compound": col["label"], "field": flabel, "recomputed": recomputed,
                "sheet": sheet, "status": status,
                "diff_pct": round(diff_pct * 100, 2) if diff_pct is not None else None,
            })

    # Closing-stock arbiter: for each MISMATCHED compound, decide whether the
    # daily detail or the in-sheet rollup is the trustworthy figure. The sheet
    # publishes its own period-closing stock; that closing can only have been
    # carried from one set of flows. We test which:
    #   daily-flow closing  = first-opening + Σ(daily material) − Σ(daily given)   [= col.closing]
    #   rollup-self closing = first-opening + Σ(rollup material) − Σ(rollup given)
    # If the sheet's published closing reconciles with the DAILY flows but NOT
    # with its own (lower) summary cells, the daily Mixer-Logbook detail is
    # authoritative and the rollup's monthly Batch/Material/Given cells are
    # stale/understated — a source-sheet correction, not a parser bug.
    col_by_key = {c["key"]: c for c in comp["cols"]}
    diagnoses: List[dict] = []
    for key in sorted(fail_keys):
        col = col_by_key.get(key)
        ref = summed.get(key) or {}
        o = first_open.get(key)
        cl = last_close.get(key)
        if not col or o is None or cl is None:
            continue
        daily_close = col.get("closing")
        rollup_self_close = o + ref.get("material", 0.0) - ref.get("given", 0.0)
        matches_daily = daily_close is not None and abs(cl - daily_close) <= 1.0
        matches_self = abs(cl - rollup_self_close) <= 1.0
        if matches_daily and not matches_self:
            diagnoses.append({
                "compound": col["label"], "verdict": "daily",
                "text": (
                    "Daily detail is authoritative. The sheet's own published "
                    "closing stock ({cl:,.0f} kg) reconciles with the daily "
                    "Mixer-Logbook flows, not with the rollup's own "
                    "Batch/Material/Given cells (which would leave {self:,.0f} kg) "
                    "— so the rollup's monthly aggregate is understated and should "
                    "be corrected in the source sheet."
                ).format(cl=cl, self=rollup_self_close),
            })

    status = FAIL if n_fail else (PASS if n_pass else NA)
    return {"available": True, "status": status, "rows": rows,
            "n_pass": n_pass, "n_fail": n_fail, "n_na": n_na, "tol_pct": tol * 100,
            "diagnoses": diagnoses}
