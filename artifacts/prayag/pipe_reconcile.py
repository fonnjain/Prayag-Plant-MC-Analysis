"""PIPE Report-5 ↔ Report-11 daily reconciliation (pure, network-free).

PIPE daily output and rejection are recorded TWICE, independently, in the
Pipe & Fitting workbook:

  * **Report-5**  — the per-machine daily matrix (Run Hours / Output / Rejection
    triplets per date).  Carries run hours, but NO pipe type.
  * **Report-11** — the "M/C & Item Wise Actual Production" item-level journal.
    Carries the pipe TYPE (CPVC / UPVC / SWR / AGRI) per row, but NO run hours.

Neither source is complete on its own — each one misses machine-days the other
records (e.g. for April 2026 Report-5 sums to 135,634 KG while Report-11 sums to
157,278 KG, and six machine-days appear only in Report-11).  So for every
(machine, date) cell the corrected figure is the **date-wise maximum** of the
two sources, taken over the UNION of every cell either source reports — for
output and, separately, for rejection.  This reproduced the original audited
April 2026 total of 157,883 KG output / 13,030 KG rejection (an as-of snapshot —
the live figure grows as machine-days are backfilled after month close; the
reconciliation logic is unchanged, so the live /build-state baseline is the
source of truth for the current expected totals).

Type split: Report-11's per-type proportions for a cell are scaled pro-rata to
that cell's corrected output (so when Report-5 is the higher figure, the extra
output is distributed across the same proportions).  A cell with **no Report-11
type signal at all** (present only in Report-5) is reported as **"untyped
pickup"** — its type is never guessed.

Header-based: columns are located by header TEXT, not fixed index, so the one
reader parses BOTH the FY2025-26 layout (header on row 5) and the FY2026-27
layout.  The standalone "Weight" column (immediately after "Pcs") is the output
— NOT "Ideal Weight (KG)"; "Actual Wt (Kg)" is rejection (the "FC" column is
kept separate); the FIRST "TYPES" column is the type (a duplicate sits further
right).  Pure: no network, no I/O.
"""
from typing import Callable, Dict, Optional, Tuple

import parsers

# Known pipe types (others encountered in the sheet are passed through as-is).
PIPE_TYPES = ("CPVC", "UPVC", "SWR", "AGRI")

# Header specs (text, never fixed index) — matched against the Report-11 band.
_H_DATE = ("eq", "DATE")
_H_MC = ("eq", "MACHINE NO.")
_H_TYPE = ("eq", "TYPES")          # first match wins (a dup sits further right)
_H_OUT = ("eq", "WEIGHT")          # standalone Weight after Pcs (not Ideal Weight)
_H_REJ = ("eq", "ACTUAL WT (KG)")  # rejection (the FC column is separate)


def parse_report11(
    values: list,
    year_month: str,
    mc_key: Callable[[str], Optional[int]],
) -> Dict[Tuple[int, str], dict]:
    """Parse Report-11 into ``{(machine_n, "YYYY-MM-DD"): {out, rej, by_type}}``.

    ``mc_key`` maps a machine label ("PIPE M/C - 1") to its integer number; a
    label that does not resolve (None) is skipped.  Multiple item rows for the
    same (machine, date) are summed.  Subtotal rows (label containing "TOTAL")
    are dropped.  Returns ``{}`` if the header cannot be located.
    """
    if not values:
        return {}

    # Header row = the first row (within the top band) carrying the DATE label.
    hdr = -1
    for i, row in enumerate(values[:15]):
        if any(parsers._match_header(c, _H_DATE) for c in row):
            hdr = i
            break
    if hdr < 0:
        return {}

    band = values[hdr]

    def find(spec) -> int:
        for c, val in enumerate(band):
            if parsers._match_header(val, spec):
                return c
        return -1

    date_c = find(_H_DATE)
    mc_c = find(_H_MC)
    type_c = find(_H_TYPE)
    out_c = find(_H_OUT)
    rej_c = find(_H_REJ)
    if date_c < 0 or mc_c < 0 or out_c < 0:
        return {}

    agg: Dict[Tuple[int, str], dict] = {}
    for row in values[hdr + 1:]:
        day = parsers._long_date_day(row[date_c] if date_c < len(row) else "")
        if day is None:
            continue
        label = str(row[mc_c]).strip() if mc_c < len(row) else ""
        if not label or "TOTAL" in label.upper():
            continue
        n = mc_key(label)
        if n is None:
            continue
        date = f"{year_month}-{day:02d}"
        out = parsers.num(row[out_c]) if 0 <= out_c < len(row) else 0.0
        rej = parsers.num(row[rej_c]) if 0 <= rej_c < len(row) else 0.0
        typ = str(row[type_c]).strip().upper() if 0 <= type_c < len(row) else ""
        a = agg.get((n, date))
        if a is None:
            a = {"out": 0.0, "rej": 0.0, "by_type": {}}
            agg[(n, date)] = a
        a["out"] += out
        a["rej"] += rej
        if typ and out > 0:
            a["by_type"][typ] = a["by_type"].get(typ, 0.0) + out
    return agg


def reconcile(
    r5: Dict[Tuple[int, str], dict],
    r11: Dict[Tuple[int, str], dict],
) -> Tuple[Dict[Tuple[int, str], dict], dict]:
    """Date-wise-max reconciliation of two per-(machine, date) sources.

    ``r5``  : ``{(n, date): {"out", "rej"}}``        (run hours kept by caller)
    ``r11`` : ``{(n, date): {"out", "rej", "by_type"}}``

    Returns ``(corrected, audit)`` where ``corrected[(n, date)]`` carries the
    chosen output/rejection, both source values, which source won, the pro-rata
    type allocation, and the untyped-pickup amount.  ``audit`` is a summary.
    """
    corrected: Dict[Tuple[int, str], dict] = {}
    keys = set(r5) | set(r11)
    n_both = n_r5_only = n_r11_only = 0
    untyped_total = 0.0

    for k in sorted(keys):
        a = r5.get(k)
        b = r11.get(k)
        r5_out = float(a["out"]) if a else 0.0
        r11_out = float(b["out"]) if b else 0.0
        r5_rej = float(a["rej"]) if a else 0.0
        r11_rej = float(b["rej"]) if b else 0.0

        out = max(r5_out, r11_out)
        rej = max(r5_rej, r11_rej)
        pick_out = "tie" if r5_out == r11_out else ("R5" if r5_out > r11_out else "R11")
        pick_rej = "tie" if r5_rej == r11_rej else ("R5" if r5_rej > r11_rej else "R11")

        by_type = (b or {}).get("by_type", {})
        tsum = sum(by_type.values())
        if tsum > 0 and out > 0:
            # Scale Report-11's type proportions to the corrected total.
            types = {t: (v / tsum) * out for t, v in by_type.items()}
            untyped = 0.0
        else:
            # No Report-11 type signal (R5-only cell, or blank type): untyped.
            types = {}
            untyped = out
        untyped_total += untyped

        corrected[k] = {
            "out": out, "rej": rej,
            "r5_out": r5_out, "r11_out": r11_out,
            "r5_rej": r5_rej, "r11_rej": r11_rej,
            "pick_out": pick_out, "pick_rej": pick_rej,
            "types": types, "untyped": untyped,
        }
        if a and b:
            n_both += 1
        elif a:
            n_r5_only += 1
        else:
            n_r11_only += 1

    out_total = sum(c["out"] for c in corrected.values())
    rej_total = sum(c["rej"] for c in corrected.values())
    type_totals: Dict[str, float] = {}
    for c in corrected.values():
        for t, v in c["types"].items():
            type_totals[t] = type_totals.get(t, 0.0) + v

    audit = {
        "n_cells": len(keys),
        "n_both": n_both,
        "n_r5_only": n_r5_only,
        "n_r11_only": n_r11_only,
        "out_total": out_total,
        "rej_total": rej_total,
        "untyped_kg": untyped_total,
        "type_totals": type_totals,
    }
    return corrected, audit
