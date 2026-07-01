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
        Column("mc", "Machine", "text", width=16),
        Column("hrs", "Run Hours", "num", total=True),
        Column("out", "Output (KG)", "kg", total=True),
        Column("rej", "Rejection (KG)", "kg", total=True),
        Column("avg", "Avg Output / Hr", "num"),
        Column("rej_pct", "Rejection %", "pct"),
    ]
    rows = []
    t_h = t_o = t_r = 0.0
    for mc in sorted(by_mc, key=lambda k: (_mc_num(k) or 0, k)):
        d = by_mc[mc].to_dict()
        t_h += d["actual_hours"]; t_o += d["total_count"]; t_r += d["reject_count"]
        rows.append({"mc": mc.replace("Pipe M/C", "M/C"),
                     "hrs": d["actual_hours"] or None, "out": d["total_count"],
                     "rej": d["reject_count"],
                     "avg": _avg(d["total_count"], d["actual_hours"]),
                     "rej_pct": _pct(d["reject_count"], d["total_count"])})
    total = {"mc": "TOTAL", "hrs": t_h or None, "out": t_o, "rej": t_r,
             "avg": _avg(t_o, t_h), "rej_pct": _pct(t_r, t_o)}
    main = ReportSheet(name="(A) Pipe M-C Summary",
        title=f"(A) Pipe M/C Summary — {month_disp(ym)}",
        subtitle="Kaharani · Pipe extrusion, per real extruder machine "
                 "(auxiliaries excluded). Output & rejection are the date-wise "
                 "reconciliation of Report-5 & Report-11; every ratio recomputed.",
        sections=[Section(cols, rows, total)],
        provenance=[f"Recomputed total output {t_o:,.0f} kg · rejection {t_r:,.0f} kg "
                    f"· run hours {t_h:,.0f}."])

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
        Column("mc", "Machine", "text", width=16),
        Column("out", "Output (KG)", "kg", total=True),
        Column("rej", "Rejection (KG)", "kg", total=True),
        Column("hrs", "Run Hours", "num", total=True),
        Column("avg", "Avg Output / Hr", "num"),
        Column("rej_pct", "Rejection %", "pct"),
    ]
    rows = []
    t_o = t_r = t_h = 0.0
    for mc in sorted(by_mc):
        d = by_mc[mc].to_dict()
        t_o += d["total_count"]; t_r += d["reject_count"]; t_h += d["actual_hours"]
        rows.append({"mc": mc, "out": d["total_count"], "rej": d["reject_count"],
                     "hrs": d["actual_hours"] or None,
                     "avg": _avg(d["total_count"], d["actual_hours"]),
                     "rej_pct": _pct(d["reject_count"], d["total_count"])})
    total = {"mc": "TOTAL", "out": t_o, "rej": t_r, "hrs": t_h or None,
             "avg": _avg(t_o, t_h), "rej_pct": _pct(t_r, t_o)}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="(B) Moulding M-C Summary",
            title=f"(B) Moulding M/C Summary — {month_disp(ym)}",
            subtitle="Kaharani · Injection moulding, per machine. Output in kg "
                     "(Report-12 'Wt in Kgs'); run hours joined from Report-5.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Recomputed total output {t_o:,.0f} kg · rejection "
                        f"{t_r:,.1f} kg · run hours {t_h:,.0f}."])],
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
    try:
        items = _read_report12(ym)
    except sheets.SheetReadError as e:
        return _awaiting(rid, label, plant, ym, str(e))
    if not items:
        return _awaiting(rid, label, plant, ym, "No Moulding (Report-12) data for this month.")

    groups = defaultdict(lambda: {"moulds": set(), "pcs": 0.0, "kg": 0.0})
    for it in items:
        g = it["material"] if it["material"] in _PIPE_MATERIALS else _NON_PIPE
        key = it["item"] or it["machine"] or "—"
        groups[g]["moulds"].add(key)
        groups[g]["pcs"] += it["pcs"]; groups[g]["kg"] += it["kg"]

    grand_kg = sum(g["kg"] for g in groups.values())
    grand_pcs = sum(g["pcs"] for g in groups.values())
    pipe_kg = sum(groups[m]["kg"] for m in _PIPE_MATERIALS if m in groups)

    cols = [Column("mat", "Material", "text", width=22),
            Column("n", "Moulds / Items", "text"),
            Column("pcs", "Production (Pcs)", "int", total=True),
            Column("kg", "Production (KG)", "kg", total=True),
            Column("share", "Share of KG %", "pct")]
    rows = []
    for m in list(_PIPE_MATERIALS) + [_NON_PIPE]:
        if m not in groups:
            continue
        g = groups[m]
        # Non-pipe moulds count is not meaningful for the pipe view -> "-".
        n = "-" if m == _NON_PIPE else str(len(g["moulds"]))
        rows.append({"mat": m, "n": n, "pcs": g["pcs"], "kg": g["kg"],
                     "share": _pct(g["kg"], grand_kg)})
    total = {"mat": "TOTAL MOULDING", "n": "", "pcs": grand_pcs, "kg": grand_kg,
             "share": 100.0 if grand_kg else None}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name="(D) Pipe Moulds Summary",
            title=f"(D) Pipe Moulds Summary — {month_disp(ym)}",
            subtitle="Kaharani · Mould/item output grouped by material, REBUILT "
                     "from Report-12 (the Report-17..20 tabs are a stale snapshot "
                     "and are not used). Pipe = CPVC/UPVC/SWR/AGRI; the rest is "
                     "Non-pipe. Grain = item code within material.",
            sections=[Section(cols, rows, total)],
            provenance=[f"Pipe-mould output (CPVC+UPVC+SWR+AGRI) {pipe_kg:,.0f} kg + "
                        f"non-pipe residual = Report-12 month total {grand_kg:,.0f} "
                        "kg — full reconciliation.",
                        "Source: Pipe & Fitting daily workbook, Report-12, grouped "
                        "by its own MATERIAL column."])],
        headline=f"{grand_kg:,.0f} kg (ties to Moulding)")


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
    by_item = defaultdict(lambda: {"count": 0.0, "rej": 0.0, "unit": ""})
    for r in recs:
        k = r.mould or r.product or "—"
        by_item[k]["count"] += r.total_count
        by_item[k]["rej"] += r.reject_count
        by_item[k]["unit"] = r.unit
    cols = [Column("item", "Item", "text", width=24),
            Column("unit", "Unit", "text"),
            Column("count", "Production", "num", total=True),
            Column("rej", "Rejection", "num", total=True),
            Column("rej_pct", "Rejection %", "pct")]
    rows = []
    t_c = t_r = 0.0
    for k in sorted(by_item):
        v = by_item[k]
        t_c += v["count"]; t_r += v["rej"]
        rows.append({"item": k, "unit": v["unit"], "count": v["count"],
                     "rej": v["rej"], "rej_pct": _pct(v["rej"], v["count"])})
    total = {"item": "TOTAL", "unit": "", "count": t_c, "rej": t_r,
             "rej_pct": _pct(t_r, t_c)}
    return ReportModel(rid=rid, label=label, plant=plant, ym=ym,
        month_disp=month_disp(ym),
        sheets=[ReportSheet(name=f"Tanks {plant}",
            title=f"Tanks ({loc}) — {month_disp(ym)}",
            subtitle=f"{loc} · {note}",
            sections=[Section(cols, rows, total)])],
        headline=f"{t_c:,.0f} produced")


def _tank_location(rid, label, plant, ym, family, rec_plant, loc) -> ReportModel:
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
                       "summary sheet (no daily workbook to cross-check).")


def gen_tank_vn(rid, label, plant, ym) -> ReportModel:
    return _tank_location(rid, label, plant, ym, "tank_vn", "TANK_VN", "Varanasi")


def gen_tank_wb(rid, label, plant, ym) -> ReportModel:
    return _tank_location(rid, label, plant, ym, "tank_wb", "TANK_WB", "West Bengal")


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
