"""Per-report generators.

Each ``gen_*`` recomputes one report's figures from the daily source workbooks
and returns a ``ReportModel`` whose column layout matches the plant's own
standardized management report. Generators import ONLY the compute layer
(``sheets`` / ``metrics`` / ``parsers`` / ``sources``) — never ``app`` — so the
reports package has no circular dependency on the Flask app.

Invariants preserved from the dashboard:
- Daily-first: figures are summed from the authoritative daily workbooks.
- Never fabricate a 0%: a ratio that cannot be computed is left ``None``
  (blank / "needs review"); a value that is genuinely absent from the source is
  omitted rather than invented.
- Per-plant units: output units are per plant; figures are never summed across
  units.
- Auxiliaries (grinders / pulverizers / mixers / sockets) are excluded from a
  plant headline; PTMT grinding is regrind and never counted as plant output.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import List, Optional

import sheets
import sources
from metrics import Record, compute_metrics, rollup_by_machine

from .model import Column, ReportModel, ReportSheet, Section
from .period import month_disp

PTMT_IDEAL_HOURS = 572.0        # in-sheet monthly ideal hours per PTMT machine
MOULD_IDEAL_HOURS = 500.0       # app-default planned hours for a moulding machine
_PIPE_MATERIALS = ("CPVC", "UPVC", "SWR", "AGRI")
_NON_PIPE = "Non-pipe (PP/PPR/ABS/TEFLON)"
_MC_RE = re.compile(r"M\s*/?\s*C\s*-?\s*(\d+)", re.I)
# Finishing/auxiliary lines never count toward a moulding plant headline.
_AUX_RE = re.compile(r"GRIND|PULVER|MIXER|SOCKET", re.I)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _daily(ym: str) -> List[Record]:
    recs, _reports, _warn = sheets.get_daily_records([ym])
    return recs


def _plant_recs(ym: str, plant: str) -> List[Record]:
    return [r for r in _daily(ym) if r.plant == plant]


def _mould_recs(ym: str) -> List[Record]:
    """Moulding records with finishing/auxiliary lines excluded."""
    return [r for r in _plant_recs(ym, "MOULDING") if not _AUX_RE.search(r.machine or "")]


def _pct(num: float, den: float) -> Optional[float]:
    if not den:
        return None
    return num / den * 100.0


def _avg(num: float, den: float) -> Optional[float]:
    if not den:
        return None
    return num / den


def _mc_num(label: str) -> Optional[int]:
    m = _MC_RE.search(label or "")
    return int(m.group(1)) if m else None


def _tonnage_mould(code: str) -> Optional[str]:
    """Tonnage for a moulding code, e.g. A02(U-150) -> 150, B01(C-275) -> 275.

    Prefers a number inside parentheses; falls back to the largest number in
    the string (guards against the machine ordinal like the "01" in "A01").
    """
    if not code:
        return None
    par = re.search(r"\(([^)]*)\)", str(code))
    if par:
        m = re.search(r"(\d{2,4})", par.group(1))
        if m:
            return m.group(1)
    nums = [int(n) for n in re.findall(r"\d{2,4}", str(code))]
    return str(max(nums)) if nums else None


def _tonnage_ptmt(code: str) -> Optional[str]:
    """Tonnage for a PTMT code: leading number after an optional N- prefix."""
    m = re.search(r"(\d{2,4})", str(code) or "")
    return m.group(1) if m else None


def _awaiting(rid, label, plant, ym, note) -> ReportModel:
    return ReportModel(
        rid=rid, label=label, plant=plant, ym=ym, month_disp=month_disp(ym),
        available=False, flags=["awaiting source"],
        sheets=[ReportSheet(name=label[:31], title=label,
                            subtitle=f"{plant} · {month_disp(ym)}", note=note)],
        headline=note)


# ---------------------------------------------------------------------------
# (A) Pipe M/C Summary
# ---------------------------------------------------------------------------
def gen_pipe(rid, label, plant, ym) -> ReportModel:
    recs = _plant_recs(ym, "PIPE")
    if not recs:
        return _awaiting(rid, label, plant, ym, "No PIPE daily workbook for this month.")
    # Headline = real extruder machines only; auxiliaries are excluded.
    extruders = [r for r in recs if _mc_num(r.machine) is not None]
    by_mc = rollup_by_machine(extruders)
    cols = [
        Column("mc",       "Machine",          "text", width=16),
        Column("hrs",      "Run Hours",         "num",  total=True),
        Column("out",      "Output (KG)",       "kg",   total=True),
        Column("rej",      "Rejection (KG)",    "kg",   total=True),
        Column("avg",      "Avg Output / Hr",   "num"),
        Column("rej_pct",  "Rejection %",       "pct"),
        Column("mc_eff",   "M/C Efficiency %",  "pct"),
        Column("paid_hrs", "Paid Hours",        "num"),
        Column("wages",    "Wages (₹)",         "num"),
        Column("per_hr",   "Per Hr (₹)",        "num"),
        Column("per_kg",   "Per KG (₹)",        "num"),
    ]
    rows = []
    t_h = t_o = t_r = t_mc_eff_den = 0.0
    for mc in sorted(by_mc, key=lambda k: (_mc_num(k) or 0, k)):
        res = by_mc[mc]
        d = res.to_dict()
        t_h += d["actual_hours"]; t_o += d["total_count"]; t_r += d["reject_count"]
        t_mc_eff_den += res.mc_eff_hours_ideal
        rows.append({"mc": mc.replace("Pipe M/C", "M/C"),
                     "hrs": d["actual_hours"] or None, "out": d["total_count"],
                     "rej": d["reject_count"],
                     "avg": _avg(d["total_count"], d["actual_hours"]),
                     "rej_pct": _pct(d["reject_count"], d["total_count"]),
                     "mc_eff": d["mc_efficiency"] if d["mc_eff_available"] else None,
                     "paid_hrs": None, "wages": None, "per_hr": None, "per_kg": None})
    # TOTAL M/C Efficiency denominator comes from Report-5 directly so that idle
    # machines (0 run hours, no production records) are still counted.  The stored
    # TOTAL cell in col M is also wrong (misses M/C-9); reading per-machine values
    # and summing them is always correct (e.g. 9 × 500 = 4 500 for PIPE).
    # Fall back to the records-based accumulator when R5 is unavailable.
    r5_ideal = sum(
        info.get("ideal_month_hours", 0.0)
        for lbl, info in sheets.pipe_run5_parsed(ym).items()
        if _MC_RE.search(lbl)
    )
    total_mc_eff_den = r5_ideal if r5_ideal > 0 else t_mc_eff_den
    total = {"mc": "TOTAL", "hrs": t_h or None, "out": t_o, "rej": t_r,
             "avg": _avg(t_o, t_h), "rej_pct": _pct(t_r, t_o),
             "mc_eff": _pct(t_h, total_mc_eff_den) if total_mc_eff_den > 0 else None,
             "paid_hrs": None, "wages": None, "per_hr": None, "per_kg": None}
    main = ReportSheet(name="(A) Pipe M-C Summary",
        title=f"(A) Pipe M/C Summary — {month_disp(ym)}",
        subtitle="Kaharani · Pipe extrusion, per real extruder machine "
                 "(auxiliaries excluded). Output & rejection are the date-wise "
                 "reconciliation of Report-5 & Report-11; every ratio recomputed.",
        sections=[Section(cols, rows, total)],
        provenance=[f"Recomputed total output {t_o:,.0f} kg · rejection {t_r:,.0f} kg "
                    f"· run hours {t_h:,.0f}.",
                    "Paid hours / wages: awaiting HR department source sheet."])

    # Pipe Type-wise (audit; the untyped pickup keeps the headline whole).
    by_mat = defaultdict(float)
    for r in extruders:
        mat = (r.material or "").upper()
        if mat in _PIPE_MATERIALS:
            by_mat[mat] += r.total_count
    typed_sub = sum(by_mat.values())
    tcols = [Column("t", "Type", "text", width=12),
             Column("out", "Output (KG)", "kg", total=True),
             Column("share", "Share of Typed %", "pct"),
             Column("note", "Note", "text", width=22)]
    trows = []
    for mat in _PIPE_MATERIALS:
        if mat in by_mat:
            trows.append({"t": mat, "out": by_mat[mat],
                          "share": _pct(by_mat[mat], typed_sub),
                          "note": "Report-11 typed"})
    trows.append({"t": "Typed subtotal", "out": typed_sub,
                  "share": 100.0 if typed_sub else None, "note": ""})
    trows.append({"t": "Untyped pickup", "out": max(t_o - typed_sub, 0.0),
                  "share": None, "note": "pending Report-11 type"})
    ttotal = {"t": "TOTAL PIPE", "out": t_o, "share": None, "note": "type completeness"}
    type_sheet = ReportSheet(name="Pipe Type-wise",
        title=f"Pipe Production — Type-wise (audit) — {month_disp(ym)}",
        subtitle="Type split available from Report-11 (audit-only; it never reduces "
                 "the (A) headline — untyped machine-days are picked up as a "
                 "residual so the typed rows plus pickup reconcile to the total).",
        sections=[Section(tcols, trows, ttotal)])

    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym), sheets=[main, type_sheet],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# (B) Moulding M/C Summary
# ---------------------------------------------------------------------------
def gen_moulding(rid, label, plant, ym) -> ReportModel:
    recs = _mould_recs(ym)
    if not recs:
        return _awaiting(rid, label, plant, ym, "No Moulding (Report-12) data for this month.")
    by_mc = rollup_by_machine(recs)
    cols = [
        Column("mc",       "Machine",          "text", width=16),
        Column("out",      "Output (KG)",       "kg",   total=True),
        Column("rej",      "Rejection (KG)",    "kg",   total=True),
        Column("hrs",      "Run Hours",         "num",  total=True),
        Column("avg",      "Avg Output / Hr",   "num"),
        Column("rej_pct",  "Rejection %",       "pct"),
        Column("mc_eff",   "M/C Efficiency %",  "pct"),
        Column("paid_hrs", "Paid Hours",        "num"),
        Column("wages",    "Wages (₹)",         "num"),
        Column("per_hr",   "Per Hr (₹)",        "num"),
        Column("per_kg",   "Per KG (₹)",        "num"),
    ]
    rows = []
    t_o = t_r = t_h = t_mc_eff_den = 0.0
    for mc in sorted(by_mc):
        res = by_mc[mc]
        d = res.to_dict()
        t_o += d["total_count"]; t_r += d["reject_count"]; t_h += d["actual_hours"]
        t_mc_eff_den += res.mc_eff_hours_ideal
        rows.append({"mc": mc, "out": d["total_count"], "rej": d["reject_count"],
                     "hrs": d["actual_hours"] or None,
                     "avg": _avg(d["total_count"], d["actual_hours"]),
                     "rej_pct": _pct(d["reject_count"], d["total_count"]),
                     "mc_eff": d["mc_efficiency"] if d["mc_eff_available"] else None,
                     "paid_hrs": None, "wages": None, "per_hr": None, "per_kg": None})
    # TOTAL denominator from R5 directly so idle moulding machines (0 output,
    # no Report-12 rows) are still counted.  Moulding machines in Report-5 are
    # those that are neither extruders (_MC_RE) nor finishing auxiliaries
    # (_AUX_RE).  Falls back to the records-based accumulator when unavailable.
    r5_ideal_m = sum(
        info.get("ideal_month_hours", 0.0)
        for lbl, info in sheets.pipe_run5_parsed(ym).items()
        if not _MC_RE.search(lbl) and not _AUX_RE.search(lbl)
    )
    total_mc_eff_den_m = r5_ideal_m if r5_ideal_m > 0 else t_mc_eff_den
    total = {"mc": "TOTAL", "out": t_o, "rej": t_r, "hrs": t_h or None,
             "avg": _avg(t_o, t_h), "rej_pct": _pct(t_r, t_o),
             "mc_eff": _pct(t_h, total_mc_eff_den_m) if total_mc_eff_den_m > 0 else None,
             "paid_hrs": None, "wages": None, "per_hr": None, "per_kg": None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="(B) Moulding M-C Summary",
            title=f"(B) Moulding M/C Summary — {month_disp(ym)}",
            subtitle="Kaharani · Injection moulding, per machine. Output in kg "
                     "(Report-12 'Wt in Kgs'); run hours joined from Report-5.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed total output {t_o:,.0f} kg · rejection "
                        f"{t_r:,.1f} kg · run hours {t_h:,.0f}.",
                        "Paid hours / wages: awaiting HR department source sheet."])],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# (C) Group of Moulding — Report-12 grouped by tonnage
# ---------------------------------------------------------------------------
def gen_gom(rid, label, plant, ym) -> ReportModel:
    recs = _mould_recs(ym)
    if not recs:
        return _awaiting(rid, label, plant, ym, "No Moulding (Report-12) data for this month.")
    bands = defaultdict(lambda: {"out": 0.0, "hrs": 0.0})
    for mc, m in rollup_by_machine(recs).items():
        ton = _tonnage_mould(mc) or "—"
        d = m.to_dict()
        bands[ton]["out"] += d["total_count"]; bands[ton]["hrs"] += d["actual_hours"]
    grand = sum(b["out"] for b in bands.values())
    cols = [Column("ton", "Tonnage Group", "text", width=14),
            Column("out", "Output (KG)", "kg", total=True),
            Column("hrs", "Run Hours", "num", total=True),
            Column("share", "Share of Output %", "pct")]
    rows = []
    t_o = t_h = 0.0
    for ton in sorted(bands, key=lambda x: int(x) if str(x).isdigit() else 9999):
        b = bands[ton]
        t_o += b["out"]; t_h += b["hrs"]
        rows.append({"ton": f"{ton} Ton", "out": b["out"], "hrs": b["hrs"] or None,
                     "share": _pct(b["out"], grand)})
    total = {"ton": "TOTAL", "out": t_o, "hrs": t_h or None,
             "share": 100.0 if t_o else None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="(C) Group of Moulding",
            title=f"(C) Group of Moulding — {month_disp(ym)}",
            subtitle="Kaharani · Report-12 output grouped by machine tonnage. "
                     "Ties to the (B) total; recomputed from the daily rows.",
            sections=[Section(cols, rows, total)])],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# (D) Pipe Moulds Summary — REBUILT from Report-12 by MATERIAL
# ---------------------------------------------------------------------------
def _read_report12(ym: str):
    """Item-grain rows of the Moulding Report-12 tab for one PIPE month.

    Returns a list of ``{material, item, machine, pcs, kg}``. Columns are found
    from the header band; the pcs column sits immediately left of "Wt in Kgs".
    """
    fid = sheets._daily_file_id("PIPE", ym)
    if not fid:
        return []
    token = sheets._get_access_token()
    if not token:
        raise sheets.SheetReadError(
            "The Google Sheets connection isn't authorized. "
            "Reconnect it from the integrations panel and try again.")
    vals = sheets.read_values(fid, "Report-12", token)
    if not vals:
        return []

    def norm(x):
        return str(x).strip().upper() if x is not None else ""

    from parsers import _long_date_day, num
    head_i = date_c = -1
    for i, row in enumerate(vals[:12]):
        for c, v in enumerate(row):
            if norm(v) == "DATE":
                head_i, date_c = i, c
                break
        if head_i >= 0:
            break
    if head_i < 0:
        return []
    first = -1
    for j in range(head_i + 1, len(vals)):
        if _long_date_day(vals[j][date_c] if date_c < len(vals[j]) else "") is not None:
            first = j
            break
    if first < 0:
        return []
    band = vals[head_i:first]

    def find(pred):
        for row in band:
            for c, v in enumerate(row):
                if pred(norm(v)):
                    return c
        return -1

    mat_c = find(lambda s: s == "MATERIAL")
    item_c = find(lambda s: "ITEM CODE" in s)
    mc_c = find(lambda s: "MOULDING MACHI" in s)
    kg_c = find(lambda s: "WT IN KGS" in s)
    pcs_c = kg_c - 1 if kg_c > 0 else -1
    if mat_c < 0 or kg_c < 0:
        return []

    items = []
    for row in vals[first:]:
        if _long_date_day(row[date_c] if date_c < len(row) else "") is None:
            continue
        mat = norm(row[mat_c]) if mat_c < len(row) else ""
        if not mat or "TOTAL" in mat:
            continue
        kg = num(row[kg_c]) if kg_c < len(row) else 0.0
        pcs = num(row[pcs_c]) if 0 <= pcs_c < len(row) else 0.0
        if kg <= 0 and pcs <= 0:
            continue
        items.append({
            "material": mat,
            "item": str(row[item_c]).strip() if 0 <= item_c < len(row) else "",
            "machine": str(row[mc_c]).strip() if 0 <= mc_c < len(row) else "",
            "pcs": pcs, "kg": kg})
    return items


def gen_pipe_moulds(rid, label, plant, ym) -> ReportModel:
    """(D) Pipe Moulds Summary — always sourced from Reports 17-20 (mould-working
    tabs) when available, regardless of agreement with Report-12.  Report-12 is
    used as a cross-check / reconciliation note ONLY; it never overrides 17-20
    as the authoritative source.  Only falls back to 'awaiting source' when 17-20
    tabs are genuinely unavailable (incomplete parse or load error).
    """
    # --- Report-12 for cross-check ONLY (never authoritative) ---
    items = []
    r12_err = None
    try:
        items = _read_report12(ym)
    except sheets.SheetReadError as e:
        r12_err = str(e)
    r12_kg = sum(it["kg"] for it in items) if items else 0.0

    # --- Reports 17-20 via load_pipe_moulds (always the authoritative source) ---
    moulds_data = None
    try:
        moulds_data = sheets.load_pipe_moulds(ym)
    except Exception:
        pass

    # 17-20 is usable when: data loaded, available, and parse not incomplete.
    # Divergence from R12 is shown as an alert but NEVER changes the source.
    stale_mould_tabs = False
    use_1720 = False
    if moulds_data and moulds_data.get("available") and not moulds_data.get("incomplete"):
        r1720_kg = moulds_data.get("grand_kg", 0.0)
        if r1720_kg > 0:
            use_1720 = True
            if r12_kg > 0:
                divergence = abs(r1720_kg - r12_kg) / r12_kg
                stale_mould_tabs = divergence > 0.01  # alert only; 17-20 still used

    # ---- Path A: use Reports 17-20 ----------------------------------------
    if use_1720:
        groups_list = moulds_data["groups"]
        grand_kg  = moulds_data["grand_kg"]
        grand_pcs = moulds_data["grand_pcs"]
        pipe_kg = sum(g["total_kg"] for g in groups_list
                      if g["group"] in _PIPE_MATERIALS)
        cols = [Column("mat",   "Material",          "text", width=22),
                Column("n",     "Active Moulds",      "text"),
                Column("pcs",   "Production (Pcs)",   "int",  total=True),
                Column("kg",    "Production (KG)",    "kg",   total=True),
                Column("share", "Share of KG %",      "pct")]
        rows = []
        for g in groups_list:
            rows.append({"mat": g["group"], "n": str(g.get("n_run", 0)),
                         "pcs": g["total_pcs"], "kg": g["total_kg"],
                         "share": _pct(g["total_kg"], grand_kg)})
        total = {"mat": "TOTAL MOULDING", "n": "", "pcs": grand_pcs, "kg": grand_kg,
                 "share": 100.0 if grand_kg else None}
        # Build the cross-check note from actual divergence — never hard-code the %
        if items and r12_kg > 0:
            _div_pct = abs(r1720_kg - r12_kg) / r12_kg * 100
            if stale_mould_tabs:   # divergence > 1%
                xref = (
                    f"Cross-checked against Report-12: {r12_kg:,.0f} kg "
                    f"({_div_pct:.1f}% divergence — Reports 17–20 remain authoritative; "
                    f"Report-12 may reflect a later data backfill that has not yet "
                    f"been updated in Reports 17–20 tabs)."
                )
            else:
                xref = (
                    f"Cross-checked against Report-12: {r12_kg:,.0f} kg "
                    f"({_div_pct:.1f}% divergence ≤1% — Reports 17–20 are authoritative)."
                )
        elif items:
            xref = "Report-12 loaded but has zero total — cross-check skipped."
        else:
            xref = "Report-12 not available for cross-check."

        # Headline: only claim "ties to Moulding" when divergence is within spec
        if stale_mould_tabs:
            _headline = f"{grand_kg:,.0f} kg (17–20 authoritative; R12 divergence noted)"
        else:
            _headline = f"{grand_kg:,.0f} kg (ties to Moulding)"

        return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
            month_disp=month_disp(ym),
            sheets=[ReportSheet(name="(D) Pipe Moulds Summary",
                title=f"(D) Pipe Moulds Summary — {month_disp(ym)}",
                subtitle="Kaharani · Mould output grouped by material (CPVC/UPVC/SWR/AGRI), "
                         "recomputed from Reports 17–20 (mould-working tabs), "
                         "cross-checked against Report-12.",
                sections=[Section(cols, rows, total)],
                provenance=[
                    f"Grand total {grand_kg:,.0f} kg / {grand_pcs:,.0f} pcs "
                    f"recomputed from Reports 17–20. {xref}",
                    "Source: Pipe & Fitting daily workbook, Reports 17–20 tabs, "
                    "one tab per material group (CPVC/UPVC/SWR/AGRI).",
                ])],
            headline=_headline)

    # ---- Path B: 17-20 unavailable → awaiting source (R12 is NOT authoritative) ---
    # Report-12 stays as a cross-check reference only; it is never served as the
    # management-report headline.  A missing / incomplete 17-20 parse is shown as
    # "awaiting source" so analysts know to fix the source, not see fabricated data.
    note = ("Reports 17–20 not yet available for this month." if not moulds_data
            else "Reports 17–20 parse is incomplete for this month — awaiting full data.")
    if r12_err:
        note += f" (Report-12 also failed: {r12_err})"
    elif r12_kg:
        note += (f" Report-12 shows {r12_kg:,.0f} kg as a reference; "
                 "it is not used here as the authoritative source.")
    return _awaiting(rid, label, plant, ym, note)


# ---------------------------------------------------------------------------
# Moulding %age Efficiency
# ---------------------------------------------------------------------------
def gen_mould_eff(rid, label, plant, ym) -> ReportModel:
    recs = _mould_recs(ym)
    if not recs:
        return _awaiting(rid, label, plant, ym, "No Moulding (Report-12) data for this month.")
    by_mc = rollup_by_machine(recs)
    cols = [Column("mc", "Machine", "text", width=16),
            Column("out", "Output (KG)", "kg", total=True),
            Column("hrs", "Run Hours", "num", total=True),
            Column("avg", "Avg Output / Hr", "num"),
            Column("util", "M/C Utilisation %", "pct"),
            Column("rej_pct", "Rejection %", "pct")]
    rows = []
    t_o = t_h = 0.0
    n = 0
    for mc in sorted(by_mc):
        d = by_mc[mc].to_dict()
        t_o += d["total_count"]; t_h += d["actual_hours"]; n += 1
        rows.append({"mc": mc, "out": d["total_count"], "hrs": d["actual_hours"] or None,
                     "avg": _avg(d["total_count"], d["actual_hours"]),
                     "util": _pct(d["actual_hours"], MOULD_IDEAL_HOURS)
                             if d["actual_hours"] else None,
                     "rej_pct": _pct(d["reject_count"], d["total_count"])})
    total = {"mc": "TOTAL / AVG", "out": t_o, "hrs": t_h or None,
             "avg": _avg(t_o, t_h),
             "util": _pct(t_h, n * MOULD_IDEAL_HOURS) if n else None,
             "rej_pct": None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="Moulding %age Efficiency",
            title=f"Moulding %age Efficiency — {month_disp(ym)}",
            subtitle="Kaharani · Per-machine output & run hours. Utilisation is run "
                     "hours vs the app-default 500 h/machine (moulding publishes no "
                     "in-sheet ideal-output rate, so output-efficiency is not shown).",
            sections=[Section(cols, rows, total)])],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# Garden
# ---------------------------------------------------------------------------
def gen_garden(rid, label, plant, ym) -> ReportModel:
    recs = _plant_recs(ym, "GARDEN")
    if not recs:
        return _awaiting(rid, label, plant, ym, "No Garden daily data for this month.")
    by_mc = rollup_by_machine(recs)
    grand = sum(m.to_dict()["total_count"] for m in by_mc.values())
    cols = [Column("mc", "Machine", "text", width=16),
            Column("out", "Output (KG)", "kg", total=True),
            Column("hrs", "Run Hours", "num", total=True),
            Column("rej", "Rejection (KG)", "kg", total=True),
            Column("share", "Share of KG %", "pct")]
    rows = []
    t_o = t_h = t_r = 0.0
    for mc in sorted(by_mc):
        d = by_mc[mc].to_dict()
        t_o += d["total_count"]; t_h += d["actual_hours"]; t_r += d["reject_count"]
        rows.append({"mc": mc, "out": d["total_count"], "hrs": d["actual_hours"] or None,
                     "rej": d["reject_count"], "share": _pct(d["total_count"], grand)})
    total = {"mc": "TOTAL", "out": t_o, "hrs": t_h or None, "rej": t_r,
             "share": 100.0 if t_o else None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="Garden Pipe Summary",
            title=f"Garden Pipe M/C Summary — {month_disp(ym)}",
            subtitle="Kaharani · Garden pipe output (kg) per machine from the "
                     "per-machine block tabs; run hours joined from the Daily "
                     "Report matrix.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed total output {t_o:,.0f} kg."])],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# HDPE
# ---------------------------------------------------------------------------
def gen_hdpe(rid, label, plant, ym) -> ReportModel:
    recs = _plant_recs(ym, "HDPE")
    if not recs:
        return _awaiting(rid, label, plant, ym, "No HDPE daily data for this month.")
    by_mc = rollup_by_machine(recs)
    grand = sum(m.to_dict()["total_count"] for m in by_mc.values())
    cols = [Column("mc", "Machine", "text", width=14),
            Column("out", "Output (KG)", "kg", total=True),
            Column("rej", "Rejection (KG)", "kg", total=True),
            Column("hrs", "Run Hours", "num", total=True),
            Column("share", "Share of KG %", "pct")]
    rows = []
    t_o = t_r = t_h = 0.0
    for mc in sorted(by_mc):
        d = by_mc[mc].to_dict()
        t_o += d["total_count"]; t_r += d["reject_count"]; t_h += d["actual_hours"]
        rows.append({"mc": mc, "out": d["total_count"], "rej": d["reject_count"],
                     "hrs": d["actual_hours"] or None, "share": _pct(d["total_count"], grand)})
    total = {"mc": "TOTAL", "out": t_o, "rej": t_r, "hrs": t_h or None,
             "share": 100.0 if t_o else None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="HDPE Summary",
            title=f"HDPE M/C Summary — {month_disp(ym)}",
            subtitle="Kaharani · HDPE output (kg) & rejection per machine from the "
                     "HDPE daily matrix.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed total output {t_o:,.0f} kg."])],
        headline=f"{t_o:,.0f} kg output")


# ---------------------------------------------------------------------------
# TANK (KH) — per item
# ---------------------------------------------------------------------------
def gen_tank_kh(rid, label, plant, ym) -> ReportModel:
    recs = _plant_recs(ym, "TANK")
    if not recs:
        return _awaiting(rid, label, plant, ym, "No Tank (KH) daily data for this month.")
    return _tank_model(rid, label, plant, ym, recs, "Kaharani",
                       "Per-item tank production & rejection (output-only; no "
                       "machine identity, so no per-machine OEE).")


def _tank_model(rid, label, plant, ym, recs, loc, note) -> ReportModel:
    """Multi-unit Tank report: headline Litres, secondary Pcs + KG (material).

    Rejection headline is in Litres (daily records: pcs × tank size;
    annual records: read directly from the SUMMARY (LTR) tab).
    Rejection % = rej_ltr / prod_ltr — no cross-unit division.

    Routing by reject_unit:
      "Ltr"  — daily records after the Phase-1 fix.
      ""     — annual records (parse_tank_annual_*); unit="Ltr" so infer Ltr.
    A pcs secondary count is shown where no Ltr rejection exists (zero-rej months).
    """
    by_item: dict = defaultdict(lambda: {
        "ltr": 0.0, "pcs": 0.0, "kg": 0.0,
        "rej_ltr": 0.0, "rej_pcs": 0.0,
    })
    for r in recs:
        k = r.mould or getattr(r, "product", None) or "—"
        sc = r.secondary_counts or {}
        if r.unit == "Ltr":
            by_item[k]["ltr"] += r.total_count
            by_item[k]["pcs"] += sc.get("pcs", 0.0)
            by_item[k]["kg"]  += sc.get("kg",  0.0)
        elif r.unit == "pcs":
            by_item[k]["pcs"] += r.total_count
            by_item[k]["ltr"] += sc.get("Ltr", 0.0)
            by_item[k]["kg"]  += sc.get("kg",  0.0)
        else:  # kg primary (fallback)
            by_item[k]["kg"]  += r.total_count
            by_item[k]["ltr"] += sc.get("Ltr", 0.0)
            by_item[k]["pcs"] += sc.get("pcs", 0.0)
        # Rejection routing: prefer explicit reject_unit; when blank, infer from
        # production unit (annual records carry unit="Ltr" with Ltr rejection but
        # no reject_unit field).  r.reject_count is always in the resolved unit.
        _rej_unit = getattr(r, "reject_unit", "") or r.unit
        if _rej_unit == "Ltr":
            by_item[k]["rej_ltr"] += r.reject_count
        # else: unusual path — ignore rather than mis-label.
        by_item[k]["rej_pcs"] += sc.get("rej_pcs", 0.0)  # raw pcs (secondary only)

    cols = [
        Column("item",    "Item",          "text", width=24),
        Column("pcs",     "Pcs",           "num",  total=True),
        Column("ltr",     "Litres",        "num",  total=True),
        Column("kg",      "KG (material)", "num",  total=True),
        Column("rej",     "Rejection (L)", "num",  total=True),
        Column("rej_pct", "Rejection %",   "pct"),
    ]
    rows = []
    t_ltr = t_pcs = t_kg = t_rej_ltr = t_rej_pcs = 0.0
    for k in sorted(by_item):
        v = by_item[k]
        t_ltr     += v["ltr"];      t_pcs     += v["pcs"];      t_kg     += v["kg"]
        t_rej_ltr += v["rej_ltr"];  t_rej_pcs += v["rej_pcs"]
        # Rejection display: prefer Ltr; show raw pcs count as fallback (zero-rej).
        rej_val   = v["rej_ltr"] if v["rej_ltr"] > 0 else v["rej_pcs"]
        rej_denom = v["ltr"]     if v["rej_ltr"] > 0 else v["pcs"]
        rows.append({
            "item":    k,
            "pcs":     v["pcs"]  or None,
            "ltr":     v["ltr"]  or None,
            "kg":      v["kg"]   or None,
            "rej":     rej_val   or None,
            "rej_pct": _pct(rej_val, rej_denom) if rej_denom > 0 else None,
        })
    t_rej_val   = t_rej_ltr if t_rej_ltr > 0 else t_rej_pcs
    t_rej_denom = t_ltr     if t_rej_ltr > 0 else t_pcs
    total = {
        "item":    "TOTAL",
        "pcs":     t_pcs      or None,
        "ltr":     t_ltr      or None,
        "kg":      t_kg       or None,
        "rej":     t_rej_val  or None,
        "rej_pct": _pct(t_rej_val, t_rej_denom) if t_rej_denom > 0 else None,
    }
    headline = f"{t_ltr:,.0f} L" if t_ltr > 0 else f"{t_pcs:,.0f} tanks"
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name=f"Tanks {plant}",
            title=f"Tanks ({loc}) — {month_disp(ym)}",
            subtitle=f"{loc} · Litres headline · Rejection % on Ltr basis · {note}",
            sections=[Section(cols, rows, total)])],
        headline=headline)


def _tank_location(rid, label, plant, ym, family, rec_plant, loc) -> ReportModel:
    """Annual-summary fallback for months with no daily workbook."""
    try:
        recs = [r for r in sheets.load_report_records(family)
                if r.plant == rec_plant and r.period == ym]
    except sheets.SheetReadError as e:
        return _awaiting(rid, label, plant, ym, str(e))
    if not recs:
        return _awaiting(rid, label, plant, ym,
                         f"No {loc} annual-summary rows for this month.")
    return _tank_model(rid, label, plant, ym, recs, loc,
                       "Production & rejection by item, read from the annual "
                       "summary sheet (no daily workbook wired for this month).")


def gen_tank_vn(rid, label, plant, ym) -> ReportModel:
    """Tank VN (Vasna/PRV) — prefers daily workbook, falls back to annual summary."""
    recs = _plant_recs(ym, "TANK_VN")
    if recs:
        return _tank_model(rid, label, plant, ym, recs, "Varanasi",
                           "Per-item production & rejection (daily workbook).")
    return _tank_location(rid, label, plant, ym, "tank_vn", "TANK_VN", "Varanasi")


def gen_tank_wb(rid, label, plant, ym) -> ReportModel:
    """Tank WB (Wambori/PDWB) — prefers daily workbook, falls back to annual summary."""
    recs = _plant_recs(ym, "TANK_WB")
    if recs:
        return _tank_model(rid, label, plant, ym, recs, "Wambori",
                           "Per-item production & rejection (daily workbook).")
    return _tank_location(rid, label, plant, ym, "tank_wb", "TANK_WB", "Wambori")


# ---------------------------------------------------------------------------
# Segment Labour / Power / Solar cost
# ---------------------------------------------------------------------------
def gen_segment_labour(rid, label, plant, ym) -> ReportModel:
    try:
        sheets.load_report_records("seg_labour")
    except sheets.SheetReadError as e:
        return _awaiting(rid, label, plant, ym, str(e))
    rows_raw = []
    for _fid, cached in getattr(sheets, "_seg_labour_cache", {}).items():
        for r in cached.get("rows", []):
            if r.get("month") == ym:
                rows_raw.append(r)
    if not rows_raw:
        return _awaiting(rid, label, plant, ym, "No segment labour rows for this month.")
    cols = [Column("unit", "Unit", "text", width=12),
            Column("segment", "Segment", "text", width=18),
            Column("labour", "Labour Cost", "num", total=True),
            Column("power", "Power Cost", "num", total=True),
            Column("solar", "Solar Cost", "num", total=True)]
    rows = []
    t_l = t_p = t_s = 0.0
    for r in sorted(rows_raw, key=lambda x: (x.get("unit", ""), x.get("segment", ""))):
        lab = r.get("labour", 0.0) or 0.0
        pw = r.get("power", 0.0) or 0.0
        so = r.get("solar", 0.0) or 0.0
        t_l += lab; t_p += pw; t_s += so
        rows.append({"unit": r.get("unit", ""), "segment": r.get("segment", ""),
                     "labour": lab, "power": pw, "solar": so})
    total = {"unit": "TOTAL", "segment": "", "labour": t_l, "power": t_p, "solar": t_s}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="Segment Labour",
            title=f"Segment Labour / Power / Solar — {month_disp(ym)}",
            subtitle="All locations · Cost by unit & segment, read from the segment "
                     "labour source sheet.",
            sections=[Section(cols, rows, total)])],
        headline="cost by segment")


# ---------------------------------------------------------------------------
# PTMT shared: per-machine stats from records
# ---------------------------------------------------------------------------
def _ptmt_stats(ym: str):
    """{code: {kg, hours, group}} for PTMT machines, plus a "had records" flag."""
    recs = _plant_recs(ym, "PTMT")
    stats: dict = {}
    for r in recs:
        code = r.machine.replace("PTMT ", "").strip() if r.machine else ""
        if not code:
            continue
        grp, _fin = sheets._ptmt_group(code)
        s = stats.setdefault(code, {"kg": 0.0, "hours": 0.0, "group": grp})
        s["kg"] += r.total_count
        s["hours"] += r.actual_hours
    return stats, bool(recs)


_PTMT_INJECTION = ("PTMT – Injection (standard)", "PTMT – Injection (N-line)")
_PTMT_MOULD_COLS = [
    Column("mc", "Machine", "text", width=16),
    Column("out", "Output (KG)", "kg", total=True),
    Column("hrs", "Run Hours", "num", total=True),
    Column("ideal", "Ideal Hours", "num", total=True),
    Column("util", "Utilisation %", "pct"),
    Column("avg", "Avg Output / Hr", "num"),
]


def _ptmt_mould_row(code, s):
    return {"mc": code, "out": s["kg"], "hrs": s["hours"] or None,
            "ideal": PTMT_IDEAL_HOURS,
            "util": _pct(s["hours"], PTMT_IDEAL_HOURS) if s["hours"] else None,
            "avg": _avg(s["kg"], s["hours"])}


def gen_ptmt_moulds(rid, label, plant, ym) -> ReportModel:
    stats, ok = _ptmt_stats(ym)
    if not ok:
        return _awaiting(rid, label, plant, ym, "No PTMT daily data for this month.")

    def subtotal(name, n, kg, hrs):
        ideal = n * PTMT_IDEAL_HOURS
        return {"mc": name, "out": kg, "hrs": hrs or None, "ideal": ideal,
                "util": _pct(hrs, ideal) if hrs else None, "avg": _avg(kg, hrs)}

    # Injection: full roster order (shows zero machines), one subtotal.
    inj_codes = (sources.PTMT_GROUPS["PTMT – Injection (standard)"]
                 + sources.PTMT_GROUPS["PTMT – Injection (N-line)"])
    inj_rows = []
    i_kg = i_h = 0.0
    for code in inj_codes:
        s = stats.get(code, {"kg": 0.0, "hours": 0.0})
        inj_rows.append(_ptmt_mould_row(code, s))
        i_kg += s["kg"]; i_h += s["hours"]
    inj_sec = Section(_PTMT_MOULD_COLS, inj_rows,
                      subtotal("Injection moulding — subtotal", len(inj_codes), i_kg, i_h),
                      heading="Injection moulding")

    # Corrugator + Blow: the machines actually present in those groups.
    cb = [(c, s) for c, s in stats.items()
          if s["group"] in ("PTMT – Corrugator", "PTMT – Blow Moulding")]
    cb.sort(key=lambda kv: (kv[1]["group"], kv[0]))
    cb_rows = [_ptmt_mould_row(c, s) for c, s in cb]
    c_kg = sum(s["kg"] for _c, s in cb)
    c_h = sum(s["hours"] for _c, s in cb)
    cb_sec = Section(_PTMT_MOULD_COLS, cb_rows,
                     subtotal("Corrugator + Blow — subtotal", len(cb), c_kg, c_h),
                     heading="Corrugator + Blow moulding")

    g_kg = i_kg + c_kg; g_h = i_h + c_h
    g_n = len(inj_codes) + len(cb)
    grand_sec = Section(_PTMT_MOULD_COLS, [],
                        subtotal("GRAND TOTAL (excl. grinding)", g_n, g_kg, g_h),
                        heading="Grand total")

    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="PTMT Moulds Summary",
            title=f"PTMT Moulds Summary — {month_disp(ym)}",
            subtitle="Bhiwadi · Per-machine PTMT output (kg), run hours and "
                     "utilisation vs the in-sheet ideal (572 h/machine). Injection, "
                     "corrugator and blow are shown separately; grinding is regrind "
                     "and excluded from plant output.",
            sections=[inj_sec, cb_sec, grand_sec],
            provenance=[f"Recomputed PTMT output (excl. grinding) {g_kg:,.0f} kg.",
                        "Source: PTMT daily workbook, Report-5 (per-machine matrix)."])],
        headline=f"{g_kg:,.0f} kg output")


# ---------------------------------------------------------------------------
# Compound / Material Balance — 14th management report
# ---------------------------------------------------------------------------
def gen_compound(rid, label, plant, ym) -> ReportModel:
    """Compound mass-balance summary — recomputed from Pipe & Fitting daily
    mixer-logbook tabs (Reports 6–10, CG 122).  One row per compound; columns
    are Opening, Batch, Given, Closing, Loss (kg) and Loss %.  A second sheet
    shows the raw-material chemical breakdown across all compounds.

    This is the same data that drives the /compound web page; the generator
    formats it as an exportable management-report .xlsx.
    """
    import compound as _cmpd
    try:
        data = sheets.load_compound_data([ym])
    except sheets.SheetReadError as e:
        return _awaiting(rid, label, plant, ym, str(e))

    comp = _cmpd.build_compilation(data["by_compound"], data["months"])
    if not comp.get("has_data"):
        return _awaiting(rid, label, plant, ym,
                         "No compound data found for this month.")

    # --- Sheet 1: per-compound balance summary ---
    bal_cols = [
        Column("cmp",      "Compound",      "text", width=16),
        Column("opening",  "Opening (kg)",  "num"),
        Column("batch",    "Batch (kg)",     "num",  total=True),
        Column("given",    "Given (kg)",     "num",  total=True),
        Column("closing",  "Closing (kg)",  "num"),
        Column("loss_kg",  "Loss (kg)",      "num",  total=True),
        Column("loss_pct", "Loss %",         "pct"),
    ]
    bal_rows = []
    for c in comp["cols"]:
        if not c.get("has_data"):
            continue
        loss_kg = c.get("batch", 0.0) - c.get("given", 0.0)
        bal_rows.append({
            "cmp":      c["label"],
            "opening":  c.get("opening") or None,
            "batch":    c.get("batch") or None,
            "given":    c.get("given") or None,
            "closing":  c.get("closing") or None,
            "loss_kg":  loss_kg if (c.get("batch") and c.get("given")) else None,
            "loss_pct": c.get("loss_pct") or None,
        })
    tot = comp["total"]
    t_batch = tot.get("batch", 0.0) or 0.0
    t_given = tot.get("given", 0.0) or 0.0
    bal_total = {
        "cmp":      "GRAND TOTAL",
        "opening":  None,
        "batch":    t_batch or None,
        "given":    t_given or None,
        "closing":  None,
        "loss_kg":  (t_batch - t_given) if (t_batch and t_given) else None,
        "loss_pct": tot.get("loss_pct") or None,
    }
    bal_sheet = ReportSheet(
        name="Compound Balance",
        title=f"Compound / Material Balance — {month_disp(ym)}",
        subtitle="Kaharani · Pipe & Fitting mixer-logbook mass-balance, recomputed "
                 "from daily tabs (Reports 6–10, CG 122). Closing = Opening + Batch "
                 "− Given; Loss % = Loss / Batch.",
        sections=[Section(bal_cols, bal_rows, bal_total)],
        provenance=[
            f"Grand batch {t_batch:,.0f} kg · given {t_given:,.0f} kg "
            f"· loss {(t_batch - t_given):,.0f} kg ({(tot.get('loss_pct') or 0)*100:.2f}%).",
            "Source: Pipe & Fitting daily workbook, mixer-logbook tabs.",
        ],
    )

    # --- Sheet 2: raw-material chemical breakdown ---
    mat_items = comp.get("materials", [])
    cmp_keys  = [c["key"] for c in comp["cols"] if c.get("has_data")]
    cmp_lbls  = {c["key"]: c["label"] for c in comp["cols"]}
    mat_cols  = [Column("mat", "Material / Chemical", "text", width=24)]
    for k in cmp_keys:
        mat_cols.append(Column(k, cmp_lbls.get(k, k), "num", total=True))
    mat_cols.append(Column("_tot", "Total (kg)", "num", total=True))

    mat_rows = []
    m_grand: dict = {k: 0.0 for k in cmp_keys}
    m_grand["_tot"] = 0.0
    for item in mat_items:
        row: dict = {"mat": item["name"]}
        row_tot = 0.0
        for k in cmp_keys:
            v = item["by"].get(k, 0.0)
            row[k] = v or None
            row_tot += v
            m_grand[k] += v
        row["_tot"] = row_tot or None
        m_grand["_tot"] += row_tot
        mat_rows.append(row)
    mat_total = {k: (m_grand[k] or None) for k in cmp_keys}
    mat_total["mat"] = "TOTAL"
    mat_total["_tot"] = m_grand["_tot"] or None

    mat_sheet = ReportSheet(
        name="Raw Materials",
        title=f"Raw Material Breakdown — {month_disp(ym)}",
        subtitle="Chemical / raw-material usage across compounds. "
                 "Sourced from the same mixer-logbook tabs as the balance sheet.",
        sections=[Section(mat_cols, mat_rows, mat_total)],
    )

    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[bal_sheet, mat_sheet],
        headline=f"{t_batch:,.0f} kg batch")


def gen_ptmt_eff(rid, label, plant, ym) -> ReportModel:
    stats, ok = _ptmt_stats(ym)
    if not ok:
        return _awaiting(rid, label, plant, ym, "No PTMT daily data for this month.")
    inj_codes = (sources.PTMT_GROUPS["PTMT – Injection (standard)"]
                 + sources.PTMT_GROUPS["PTMT – Injection (N-line)"])
    bands = defaultdict(lambda: {"n": 0, "kg": 0.0, "hours": 0.0})
    for code in inj_codes:
        ton = _tonnage_ptmt(code) or "Other"
        s = stats.get(code, {"kg": 0.0, "hours": 0.0})
        b = bands[ton]
        b["n"] += 1; b["kg"] += s["kg"]; b["hours"] += s["hours"]
    cols = [Column("ton", "Tonnage Group", "text", width=14),
            Column("n", "Machines", "int", total=True),
            Column("out", "Output (KG)", "kg", total=True),
            Column("hrs", "Run Hours", "num", total=True),
            Column("util", "Utilisation %", "pct"),
            Column("avg", "Avg Output / Hr", "num")]
    rows = []
    t_n = t_kg = t_h = 0.0
    for ton in sorted(bands, key=lambda x: int(x) if str(x).isdigit() else 9999):
        b = bands[ton]
        ideal = b["n"] * PTMT_IDEAL_HOURS
        t_n += b["n"]; t_kg += b["kg"]; t_h += b["hours"]
        rows.append({"ton": f"{ton} Ton", "n": b["n"], "out": b["kg"],
                     "hrs": b["hours"] or None,
                     "util": _pct(b["hours"], ideal) if b["hours"] else None,
                     "avg": _avg(b["kg"], b["hours"])})
    t_ideal = t_n * PTMT_IDEAL_HOURS
    total = {"ton": "INJECTION TOTAL", "n": t_n, "out": t_kg, "hrs": t_h or None,
             "util": _pct(t_h, t_ideal) if t_h else None, "avg": _avg(t_kg, t_h)}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="PTMT %age Efficiency",
            title=f"PTMT %age Efficiency — {month_disp(ym)}",
            subtitle="Bhiwadi · Injection machines grouped by tonnage; output (kg), "
                     "run hours and utilisation vs the in-sheet ideal (572 h/machine "
                     "× machines in the group). Corrugator, blow & grinding excluded.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed injection output {t_kg:,.0f} kg across "
                        f"{int(t_n)} machines."])],
        headline=f"{t_kg:,.0f} kg injection")
