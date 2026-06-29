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


def _agg_mixer(parses: List[dict]) -> dict:
    opening = parses[0].get("opening", 0.0) if parses else 0.0
    batch = material = given = loss = pulvizer = 0.0
    chems: Dict[str, float] = {}
    days: List[dict] = []
    given_label = ""
    for p in parses:
        given_label = p.get("given_label") or given_label
        for d in p.get("days", []):
            batch += d["batch"]
            material += d["material"]
            given += d["given"]
            loss += d["loss"]
            pulvizer += d["pulvizer"]
            for nm, v in d["chems"].items():
                chems[nm] = chems.get(nm, 0.0) + v
            days.append(d)
    closing = opening + material - given
    loss_pct = (loss / batch) if batch else None
    return {
        "kind": "mixer", "opening": opening, "pulvizer": pulvizer, "batch": batch,
        "material": material, "given": given, "loss": loss, "loss_pct": loss_pct,
        "closing": closing, "chems": chems, "days": days,
        "given_label": given_label or "Total Compound given",
        "has_data": bool(days),
    }


def _agg_cg(parses: List[dict]) -> dict:
    opening = parses[0].get("opening", 0.0) if parses else 0.0
    purchase = issue = 0.0
    days: List[dict] = []
    for p in parses:
        for d in p.get("days", []):
            purchase += d["purchase"]
            issue += d["issue"]
            days.append(d)
    closing = opening + purchase - issue
    return {
        "kind": "cg", "opening": opening, "pulvizer": 0.0, "batch": 0.0,
        "material": purchase, "given": issue, "loss": 0.0, "loss_pct": None,
        "closing": closing, "purchase": purchase, "issue": issue,
        "chems": {}, "days": days, "given_label": "Issued to Fitting",
        "has_data": bool(days),
    }


def build_compilation(by_compound: Dict[str, List[dict]], months: List[str]) -> dict:
    """Assemble the full compound-balance compilation.

    ``by_compound``: {compound_key: [per-month parse dict, chronological]}.
    Returns a template-ready dict: per-compound balance columns, the 7-compound
    grand TOTAL, the raw-material item breakdown matrix, and the yield split.
    """
    cols: List[dict] = []
    total = {k: 0.0 for k in ("opening", "pulvizer", "batch", "material", "given", "loss", "closing")}
    item_matrix: Dict[str, Dict[str, float]] = {}

    for spec in COMPOUNDS:
        parses = by_compound.get(spec["key"], [])
        bal = _agg_cg(parses) if spec["layout"] == "cg" else _agg_mixer(parses)
        col = dict(spec)
        col.update(bal)
        cols.append(col)
        if spec["in_total"]:
            for k in total:
                total[k] += bal.get(k, 0.0) or 0.0
            for nm, v in bal.get("chems", {}).items():
                item_matrix.setdefault(nm, {})[spec["key"]] = item_matrix.get(nm, {}).get(spec["key"], 0.0) + v

    total["loss_pct"] = (total["loss"] / total["batch"]) if total["batch"] else None

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
    }


def validate(comp: dict, rollup: Dict[str, dict], tol: float = 0.005) -> dict:
    """Reconcile the recomputed daily balance against the in-sheet "Compound
    6-10" monthly rollup (summed across the period).

    Each compound is checked on batch / material / given. A check is PASS within
    ``tol`` (0.5%), FAIL beyond it, NA when the rollup has no figure. Honest —
    a mismatch is surfaced and located, never auto-passed.
    """
    if not rollup:
        return {"available": False, "status": NA, "rows": [], "n_pass": 0, "n_fail": 0, "n_na": 0}

    # Sum the rollup across all months in the period.
    summed: Dict[str, Dict[str, float]] = {}
    for _ym, rd in rollup.items():
        for key, fields in rd.items():
            dst = summed.setdefault(key, {})
            for f, v in fields.items():
                dst[f] = dst.get(f, 0.0) + v

    rows: List[dict] = []
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
            rows.append({
                "compound": col["label"], "field": flabel, "recomputed": recomputed,
                "sheet": sheet, "status": status,
                "diff_pct": round(diff_pct * 100, 2) if diff_pct is not None else None,
            })

    status = FAIL if n_fail else (PASS if n_pass else NA)
    return {"available": True, "status": status, "rows": rows,
            "n_pass": n_pass, "n_fail": n_fail, "n_na": n_na, "tol_pct": tol * 100}
