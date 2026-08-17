"""
Thin serialisers: convert mgmt_*.py builder dicts → (List[ReportSheet], List[Flag]).

Cardinal Rule: each serial_* calls the SAME mgmt_*.py builder the web page uses.
Figures are never recomputed here independently — only re-shaped for Excel.

Each serial_* returns (sheets, flags).  The generator wraps the result with a
Cover tab (first) and a Notes tab populated from flags (last), then returns a
ReportModel.

Rules strictly obeyed:
  • AWAITING SOURCE DATA / n/a / IDLE strings flow through as text; never 0.
  • None (genuinely missing) flows through as None → blank cell in xlsx.
  • No mgmt_*.py builder is modified here.
  • If a builder's return shape makes a tab awkward, we note it in that tab's
    ``note`` field rather than duplicating or guessing logic.
"""
from __future__ import annotations

from typing import List, Tuple

from .model import Column, Flag, ReportSheet, Section

_SheetFlagPair = Tuple[List[ReportSheet], List[Flag]]

# ---------------------------------------------------------------------------
# Shared small helpers
# ---------------------------------------------------------------------------

def _fy_from_ym(ym: str) -> str:
    """'2026-04' → '2627';  '2025-12' → '2526'."""
    try:
        yr, mo = int(ym[:4]), int(ym[5:7])
        fs = yr if mo >= 4 else yr - 1
        return f"{fs % 100:02d}{(fs + 1) % 100:02d}"
    except Exception:
        return "2627"


def _mo_abbr(ym: str) -> str:
    _A = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
          7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}
    try:
        return _A[int(ym[5:7])]
    except Exception:
        return ym


def _v(val):
    """Identity pass-through respecting None-as-blank and str-as-text."""
    return val


def _awaiting(val):
    """True / 'AWAITING' sentinel → canonical text string."""
    if val is True or val == "AWAITING":
        return "AWAITING SOURCE DATA"
    return val


def _pct(n, d):
    return None if not d else n / d * 100.0


def _avg(n, d):
    return None if not d else n / d


def _row(row_dict, keys):
    """Extract a subset of keys from a row dict, passing None for missing keys."""
    return {k: _v(row_dict.get(k)) for k in keys}


def _build_failed(err) -> _SheetFlagPair:
    return [], [Flag(rule="BUILD FAILURE", section="All", note=str(err))]


# ============================================================================
# R1 — Segment Labour / Power / Ideal Cost
#       builder: mgmt_labour_power.build_mgmt_report_data(fy)
# ============================================================================
def serial_segment_labour(ym: str) -> _SheetFlagPair:
    from mgmt_labour_power import build_mgmt_report_data
    fy = _fy_from_ym(ym)
    d = build_mgmt_report_data(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    # ---- Combined Wages tab ----
    cw = d.get("combined_wages") or {}
    cw_units = cw.get("units") or []
    cw_cols = [
        Column("label",            "Unit",              "text", width=16),
        Column("n_payroll",        "Payroll Staff",     "int"),
        Column("n_contractor",     "Contractor",        "int"),
        Column("n_total",          "Total Headcount",   "int",  total=True),
        Column("paid_wages",       "Payroll Wages (₹)", "num",  total=True),
        Column("contractor_wages", "Contractor (₹)",    "num",  total=True),
        Column("total_wages",      "Total Wages (₹)",   "num",  total=True),
    ]
    cw_rows = []
    for u in cw_units:
        if isinstance(u, dict):
            cw_rows.append({**_row(u, ["n_payroll","n_contractor","n_total",
                                       "paid_wages","contractor_wages","total_wages"]),
                            "label": u.get("label","")})
    gt = cw.get("grand_total") or {}
    cw_total = {**_row(gt, ["n_total","paid_wages","contractor_wages","total_wages"]),
                "label": "GRAND TOTAL"}
    sheets.append(ReportSheet(
        name="Combined Wages",
        title=f"Combined Wages — {fy_lbl}",
        subtitle="All units combined headcount and wages.",
        sections=[Section(cw_cols, cw_rows, cw_total if gt else None)],
    ))

    # ---- UNIT-1, UNIT-2, UNIT-3 tabs ----
    unit_row_keys = [
        "month_label","n_labour","n_contractor","n_total_lab",
        "paid_hours","paid_wages","contractor_wages","total_wages",
        "jvvl","total_power","solar","per_kg_power","per_kg_labour","total_cost",
    ]
    unit_cols = [
        Column("month_label",      "Month",           "text", width=10),
        Column("n_labour",         "Labour",          "int"),
        Column("n_contractor",     "Contractor",      "int"),
        Column("n_total_lab",      "Total",           "int"),
        Column("paid_hours",       "Paid Hours",      "num"),
        Column("paid_wages",       "Payroll (₹)",     "num",  total=True),
        Column("contractor_wages", "Contractor (₹)",  "num",  total=True),
        Column("total_wages",      "Total Wages (₹)", "num",  total=True),
        Column("jvvl",             "Units (kWh)",     "num"),
        Column("total_power",      "Power Cost (₹)",  "num",  total=True),
        Column("solar",            "Solar (₹)",       "num"),
        Column("per_kg_power",     "Power / KG (₹)",  "num"),
        Column("per_kg_labour",    "Labour / KG (₹)", "num"),
        Column("total_cost",       "Total Cost (₹)",  "num",  total=True),
    ]
    for u in (d.get("units") or []):
        if not isinstance(u, dict):
            continue
        lbl = u.get("label", "UNIT")
        tab_rows, total_row = [], {}
        for seg in (u.get("segments") or []):
            if not isinstance(seg, dict):
                continue
            for mr in (seg.get("month_rows") or []):
                if not isinstance(mr, dict):
                    continue
                row = _row(mr, unit_row_keys)
                if mr.get("awaiting"):
                    row["total_wages"] = "AWAITING SOURCE DATA"
                    flags.append(Flag(
                        rule="R-42",
                        section=lbl,
                        month=str(mr.get("month_label") or mr.get("month_disp") or ""),
                        our_figure="AWAITING",
                        source_figure="—",
                        difference="—",
                        note=f"Wages not yet received from HR for "
                             f"{mr.get('month_label') or mr.get('month_disp','')}.",
                        cell_comment="Wages awaiting HR source sheet for this month.",
                    ))
                tab_rows.append(row)
            tr = seg.get("total_row") or {}
            for k, val in tr.items():
                if k not in ("month_label","month_disp","segment","unit","ym"):
                    total_row[k] = _v(val)
        total_row["month_label"] = "TOTAL"
        sheets.append(ReportSheet(
            name=lbl,
            title=f"{lbl} — Labour & Power Cost — {fy_lbl}",
            subtitle="Month-wise headcount, wages, power units and cost per KG.",
            sections=[Section(unit_cols, tab_rows,
                              total_row if tab_rows else None)],
        ))

    # ---- Ideal Power Cost tab ----
    ip = d.get("ideal_power_sec") or {}
    ip_rates = ip.get("rates") or []
    if ip_rates and isinstance(ip_rates[0], dict):
        sample = ip_rates[0]
        ip_month_keys = [k for k in sample if k not in ("seg","segment","total_ideal")]
        ip_cols = ([Column("seg", "Segment", "text", width=18)] +
                   [Column(k, k, "num") for k in ip_month_keys] +
                   [Column("total_ideal", "Total Ideal (₹)", "num", total=True)])
        ip_rows = []
        for rr in ip_rates:
            if isinstance(rr, dict):
                row = {k: _v(rr.get(k)) for k in ip_month_keys}
                row["seg"] = rr.get("seg") or rr.get("segment") or ""
                row["total_ideal"] = _v(rr.get("total_ideal"))
                ip_rows.append(row)
        itr = ip.get("total_row") or {}
        ip_total = {k: _v(itr.get(k)) for k in ["total_ideal"] + ip_month_keys}
        ip_total["seg"] = "TOTAL"
        sheets.append(ReportSheet(
            name="Ideal Power Cost",
            title=f"Ideal Power Cost — {fy_lbl}",
            sections=[Section(ip_cols, ip_rows, ip_total if itr else None)],
        ))
    else:
        sheets.append(ReportSheet(
            name="Ideal Power Cost",
            title=f"Ideal Power Cost — {fy_lbl}",
            note="Ideal power cost structure not available from current builder output.",
        ))

    # ---- Ideal Labour Cost tab ----
    il = d.get("ideal_labour_sec") or {}
    il_rates = il.get("rates") or []
    if il_rates and isinstance(il_rates[0], dict):
        sample = il_rates[0]
        il_month_keys = [k for k in sample if k not in ("seg","segment","total_ideal")]
        il_cols = ([Column("seg", "Segment", "text", width=18)] +
                   [Column(k, k, "num") for k in il_month_keys] +
                   [Column("total_ideal", "Total Ideal (₹)", "num", total=True)])
        il_rows = []
        for rr in il_rates:
            if isinstance(rr, dict):
                row = {k: _v(rr.get(k)) for k in il_month_keys}
                row["seg"] = rr.get("seg") or rr.get("segment") or ""
                row["total_ideal"] = _v(rr.get("total_ideal"))
                il_rows.append(row)
        iltr = il.get("total_row") or {}
        il_total = {k: _v(iltr.get(k)) for k in ["total_ideal"] + il_month_keys}
        il_total["seg"] = "TOTAL"
        sheets.append(ReportSheet(
            name="Ideal Labour Cost",
            title=f"Ideal Labour Cost — {fy_lbl}",
            sections=[Section(il_cols, il_rows, il_total if iltr else None)],
        ))
    else:
        sheets.append(ReportSheet(
            name="Ideal Labour Cost",
            title=f"Ideal Labour Cost — {fy_lbl}",
            note="Ideal labour cost structure not available from current builder output.",
        ))

    # ---- REJECTION & PRODUCTION tab ----
    rp = d.get("reject_prod_sec") or {}
    rp_segs = [s for s in (rp.get("segs") or []) if isinstance(s, str)]
    rp_months = rp.get("months") or []
    if rp_segs and rp_months and isinstance(rp_months[0], dict):
        rp_cols = ([Column("month_label", "Month", "text", width=10)] +
                   [Column(s, s, "num") for s in rp_segs])
        rp_rows = []
        for mo in rp_months:
            if isinstance(mo, dict):
                row = {"month_label": mo.get("month_label") or mo.get("month_disp") or ""}
                for s in rp_segs:
                    row[s] = _v(mo.get(s))
                rp_rows.append(row)
        rptr = rp.get("total_row") or {}
        rp_total = {s: _v(rptr.get(s)) for s in rp_segs}
        rp_total["month_label"] = "TOTAL"
        sheets.append(ReportSheet(
            name="REJECTION & PRODUCTION",
            title=f"Rejection & Production — {fy_lbl}",
            sections=[Section(rp_cols, rp_rows, rp_total if rptr else None)],
        ))
    else:
        sheets.append(ReportSheet(
            name="REJECTION & PRODUCTION",
            title=f"Rejection & Production — {fy_lbl}",
            note="Rejection & production data not available from current builder output.",
        ))

    return sheets, flags


# ============================================================================
# R2 — Pipe M/C Summary
#       builder: mgmt_pipe_summary.build_pipe_summary(fy)
# ============================================================================
def serial_pipe(ym: str) -> _SheetFlagPair:
    from mgmt_pipe_summary import build_pipe_summary
    fy = _fy_from_ym(ym)
    d = build_pipe_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    # ---- SUMMARY tab (section1 — monthly production) ----
    s1 = d.get("section1") or {}
    s1_rows = s1.get("month_rows") or []
    s1_total = s1.get("total_row") or {}
    s1_cols = [
        Column("month_lbl",         "Month",            "text", width=10),
        Column("run_hrs",           "Run Hours",        "num",  total=True),
        Column("gross_output_kg",   "Output (KG)",      "kg",   total=True),
        Column("labour",            "Labour",           "int",  total=True),
        Column("paid_hrs",          "Paid Hours",       "num"),
        Column("wages",             "Wages (₹)",        "num",  total=True),
        Column("devoted_per_person","Devot./Person",    "num"),
        Column("per_hour_cost",     "Cost / Hr (₹)",   "num"),
        Column("per_kg_cost",       "Cost / KG (₹)",   "num"),
    ]
    s1_data = [_row(r, [c.key for c in s1_cols]) for r in s1_rows]
    s1_totrow = _row(s1_total, [c.key for c in s1_cols])
    s1_totrow["month_lbl"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"Pipe M/C Summary — {fy_lbl}",
        subtitle="Monthly run hours, output, wages and cost ratios (PIPE). "
                 "Source: Report-5 (hours) & Report-11 (output).",
        sections=[Section(s1_cols, s1_data, s1_totrow if s1_total else None)],
        provenance=[w for w in (s1.get("warnings") or []) if isinstance(w, str)],
    ))

    # ---- SUMMARY-1 tab (section2 — per-machine YoY) ----
    s2 = d.get("section2") or {}
    fy27_rows = s2.get("fy2627") or []
    fy26_rows = s2.get("fy2526") or []
    s2_cols = [
        Column("machine",      "Machine",           "text", width=14),
        Column("pipe_type",    "Type",              "text", width=10),
        Column("actual_hrs",   "Actual Hrs",        "num",  total=True),
        Column("ideal_hrs",    "Ideal Hrs",         "num"),
        Column("actual_out_kg","Output (KG)",       "kg",   total=True),
        Column("ideal_rate",   "Ideal Rate",        "num"),
        Column("avg_hr",       "Avg / Hr",          "num"),
        Column("util_pct",     "Utilisation %",     "pct"),
        Column("out_eff_pct",  "Output Eff. %",     "pct"),
    ]
    s2_sections = []
    for label, rows in [(s2.get("fy2627_label","FY 26-27"), fy27_rows),
                        (s2.get("fy2526_label","FY 25-26"), fy26_rows)]:
        if rows:
            s2_sections.append(Section(
                s2_cols,
                [_row(r, [c.key for c in s2_cols]) for r in rows],
                heading=label,
            ))
    sheets.append(ReportSheet(
        name="SUMMARY-1",
        title=f"Pipe M/C — Year-on-Year — {fy_lbl}",
        subtitle="Per-machine actual vs ideal hours, output efficiency, utilisation.",
        sections=s2_sections or [],
        note="" if s2_sections else "No year-on-year data available.",
    ))

    # ---- MATERIAL tab (section3 — by material group) ----
    s3 = d.get("section3") or {}
    s3_groups = s3.get("groups") or []
    s3_total  = s3.get("total_row") or {}
    s3_cols = [
        Column("type",      "Material Type", "text", width=16),
        Column("machines",  "Machines",      "text", width=10),
        Column("qty",       "Qty",           "int"),
        Column("hrs",       "Hours",         "num",  total=True),
        Column("output_kg", "Output (KG)",   "kg",   total=True),
        Column("ideal_out", "Ideal Out",     "num"),
        Column("ideal_hrs", "Ideal Hrs",     "num"),
        Column("avg_hr",    "Avg / Hr",      "num"),
        Column("util_pct",  "Util %",        "pct"),
        Column("out_eff",   "Out Eff %",     "pct"),
    ]
    s3_data = [_row(r, [c.key for c in s3_cols]) for r in s3_groups]
    s3_totrow = _row(s3_total, [c.key for c in s3_cols])
    s3_totrow["type"] = "TOTAL"
    sheets.append(ReportSheet(
        name="MATERIAL",
        title=f"Pipe — Material Group Summary — {fy_lbl}",
        sections=[Section(s3_cols, s3_data, s3_totrow if s3_total else None)],
    ))

    # ---- MC WISE tab (section4 — month × machine matrix) ----
    s4 = d.get("section4") or {}
    s4_month_rows = s4.get("month_rows") or []
    s4_total_cols  = s4.get("total_cols") or {}
    mc_labels = list(s4_total_cols.keys()) if s4_total_cols else []
    if s4_month_rows and mc_labels:
        mcw_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                    [Column(mc, mc, "num") for mc in mc_labels])
        mcw_rows = []
        for mo in s4_month_rows:
            if not isinstance(mo, dict):
                continue
            cols_data = mo.get("cols") or {}
            row = {"month_lbl": mo.get("month_lbl") or mo.get("month_disp") or ""}
            for mc in mc_labels:
                mc_data = cols_data.get(mc)
                if isinstance(mc_data, dict):
                    row[mc] = _v(mc_data.get("output_kg") or mc_data.get("hrs")
                                 or mc_data.get("kg") or mc_data.get("val"))
                else:
                    row[mc] = _v(mc_data)
            mcw_rows.append(row)
        total_row_mcw = {"month_lbl": "TOTAL"}
        for mc, mc_data in s4_total_cols.items():
            if isinstance(mc_data, dict):
                total_row_mcw[mc] = _v(mc_data.get("output_kg") or mc_data.get("hrs")
                                       or mc_data.get("kg") or mc_data.get("val"))
            else:
                total_row_mcw[mc] = _v(mc_data)
        sheets.append(ReportSheet(
            name="MC WISE",
            title=f"Pipe — Machine-wise Monthly — {fy_lbl}",
            subtitle="Month × machine matrix. Values are output (KG) per machine per month.",
            sections=[Section(mcw_cols, mcw_rows, total_row_mcw)],
        ))

    # ---- HOURS + OUTPUT tabs (section5, section6) ----
    for sec_key, tab_name in [("section5","HOURS"), ("section6","OUTPUT")]:
        sx = d.get(sec_key) or {}
        machines = sx.get("machines") or []
        if machines:
            # machines may be a list of strings (machine labels) or dicts
            if isinstance(machines[0], str):
                mh_cols = [Column("machine", "Machine", "text", width=20)]
                sheets.append(ReportSheet(
                    name=tab_name,
                    title=f"Pipe — {tab_name} — {fy_lbl}",
                    sections=[Section(mh_cols,
                                      [{"machine": m} for m in machines])],
                ))
            elif isinstance(machines[0], dict):
                sample = machines[0]
                ex_keys = [k for k in sample if k not in ("machine","mc_label")]
                mh_cols = ([Column("machine", "Machine", "text", width=16)] +
                            [Column(k, k, "num") for k in ex_keys])
                sheets.append(ReportSheet(
                    name=tab_name,
                    title=f"Pipe — {tab_name} — {fy_lbl}",
                    sections=[Section(mh_cols,
                                      [_row(m, ["machine"] + ex_keys) for m in machines])],
                ))

    return sheets, flags


# ============================================================================
# R3 — Moulding M/C Summary
#       builder: mgmt_moulding_summary.build_moulding_summary(fy)
# ============================================================================
def serial_moulding(ym: str) -> _SheetFlagPair:
    from mgmt_moulding_summary import build_moulding_summary
    fy = _fy_from_ym(ym)
    d = build_moulding_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    for w in (d.get("roster_warnings") or []):
        flags.append(Flag(rule="R-06", section="SUMMARY",
                          note=str(w) if not isinstance(w, str) else w))

    # ---- SUMMARY tab (section1 — monthly totals) ----
    s1 = d.get("section1") or {}
    s1_rows = s1.get("rows") or []
    s1_cols_keys = ([k for k in (s1_rows[0].keys() if s1_rows else [])])
    if s1_rows:
        s1_cols = (
            [Column("month_lbl",  "Month",       "text", width=10)] +
            [Column(k, k.replace("_"," ").title(), "num", total=True)
             for k in s1_cols_keys
             if k not in ("month_lbl","month_disp","ym","is_total")]
        )
        s1_data = [_row(r, [c.key for c in s1_cols]) for r in s1_rows]
        sheets.append(ReportSheet(
            name="SUMMARY",
            title=f"Moulding Summary — {fy_lbl}",
            subtitle="Monthly net output, rejection, hours and wages. "
                     "Auxiliary machines (grinders, mixers) excluded.",
            sections=[Section(s1_cols, s1_data)],
        ))
    else:
        sheets.append(ReportSheet(name="SUMMARY",
                                  title=f"Moulding Summary — {fy_lbl}",
                                  note="No data available."))

    # ---- SUMMARY-1 tab (section2 — YoY by band) ----
    s2 = d.get("section2") or {}
    fy27 = s2.get("fy2627") or []
    fy26 = s2.get("fy2526") or []
    s2_cols = [
        Column("band",      "Band (Ton)",  "text", width=12),
        Column("mc_count",  "Machines",   "int"),
        Column("actual_hrs","Actual Hrs",  "num",  total=True),
        Column("ideal_hrs", "Ideal Hrs",  "num",  total=True),
        Column("output_kg", "Output (KG)","kg",   total=True),
        Column("reject_kg", "Reject (KG)","kg",   total=True),
        Column("runner_kg", "Runner (KG)","kg"),
        Column("avg_hr",    "Avg / Hr",   "num"),
        Column("util_pct",  "Util %",     "pct"),
    ]
    s2_secs = []
    for lbl, rows in [(s2.get("fy2627_label","FY 26-27"), fy27),
                      (s2.get("fy2526_label","FY 25-26"), fy26)]:
        if rows:
            s2_secs.append(Section(s2_cols,
                                   [_row(r, [c.key for c in s2_cols]) for r in rows],
                                   heading=lbl))
    sheets.append(ReportSheet(
        name="SUMMARY-1",
        title=f"Moulding — Year-on-Year by Tonnage Band — {fy_lbl}",
        sections=s2_secs or [],
        note="" if s2_secs else "No year-on-year data.",
    ))

    # ---- HOURS tab (section4 — month rows + machine breakdown) ----
    s4 = d.get("section4") or {}
    s4_month_rows = s4.get("month_rows") or []
    mc_bands = s4.get("mc_bands") or []
    if s4_month_rows:
        h_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                  [Column(str(b), f"{b} Ton", "num") for b in mc_bands])
        h_rows = []
        for mo in s4_month_rows:
            if not isinstance(mo, dict):
                continue
            cols_data = mo.get("cols") or {}
            row = {"month_lbl": mo.get("month_lbl") or mo.get("month_disp") or ""}
            for b in mc_bands:
                bc = cols_data.get(str(b)) or cols_data.get(b)
                row[str(b)] = (bc.get("hrs") if isinstance(bc, dict) else _v(bc))
            h_rows.append(row)
        sheets.append(ReportSheet(
            name="HOURS",
            title=f"Moulding — Run Hours by Band & Month — {fy_lbl}",
            sections=[Section(h_cols, h_rows)],
        ))

    # ---- Per-machine tabs (section3 — MC-1 to MC-27) ----
    s3 = d.get("section3") or {}
    mc_list = s3.get("machines") or []
    mc_detail_cols = [
        Column("month_lbl",  "Month",       "text", width=10),
        Column("ideal_hrs",  "Ideal Hrs",   "num"),
        Column("actual_hrs", "Actual Hrs",  "num",  total=True),
        Column("output_kg",  "Output (KG)", "kg",   total=True),
        Column("reject_kg",  "Reject (KG)", "kg",   total=True),
        Column("runner_kg",  "Runner (KG)", "kg"),
        Column("avg_hr",     "Avg / Hr",    "num"),
        Column("util_pct",   "Util %",      "pct"),
    ]
    import re as _re2
    for mc in mc_list:
        if not isinstance(mc, dict):
            continue
        mc_key  = mc.get("mc_key") or mc.get("mould_id") or "MC"
        band    = mc.get("band", "")
        # Extract number from mc_key to get clean "MC-1" style tab names
        _num_m = _re2.search(r'(\d+)', str(mc_key))
        _num   = _num_m.group(1) if _num_m else str(mc_key).strip()
        tab_name = f"MC-{_num}"
        mr_rows = mc.get("month_rows") or []
        tr      = mc.get("total_row") or {}
        mc_rows = [_row(r, [c.key for c in mc_detail_cols]) for r in mr_rows
                   if isinstance(r, dict)]
        mc_totrow = _row(tr, [c.key for c in mc_detail_cols])
        mc_totrow["month_lbl"] = "TOTAL"
        sheets.append(ReportSheet(
            name=tab_name[:31],
            title=f"{tab_name} — {band} Ton — {fy_lbl}",
            subtitle=f"Mould ID: {mc.get('mould_id','')}",
            sections=[Section(mc_detail_cols, mc_rows,
                              mc_totrow if tr else None)],
        ))

    return sheets, flags


# ============================================================================
# R4 — Group of Moulding (GOM)
#       builder: mgmt_gom_summary.build_gom_summary(fy)
# ============================================================================
def serial_gom(ym: str) -> _SheetFlagPair:
    from mgmt_gom_summary import build_gom_summary
    fy = _fy_from_ym(ym)
    d = build_gom_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    for w in (d.get("roster_warnings") or []):
        flags.append(Flag(rule="R-06", section="SUMMARY",
                          note=str(w) if not isinstance(w, str) else w))

    # ---- SUMMARY tab (section1 — re-uses moulding YoY, net basis) ----
    s1 = d.get("section1") or {}
    s1_rows = s1.get("rows") or []
    if s1_rows:
        s1_col_keys = [k for k in s1_rows[0] if k not in ("ym","is_total")]
        s1_cols = [Column(k, k.replace("_"," ").title(),
                          "text" if k in ("band","month_lbl") else "num",
                          width=12 if k == "band" else None)
                   for k in s1_col_keys]
        sheets.append(ReportSheet(
            name="SUMMARY",
            title=f"GOM — Band Summary (net) — {fy_lbl}",
            subtitle="Production by tonnage band, net basis, year-on-year.",
            sections=[Section(s1_cols,
                              [_row(r, s1_col_keys) for r in s1_rows])],
        ))
    else:
        sheets.append(ReportSheet(name="SUMMARY", title=f"GOM Summary — {fy_lbl}",
                                  note="No data."))

    # ---- SUMMARY-1 tab (section2 — band × month, gross) ----
    s2 = d.get("section2") or {}
    s2_band_rows = s2.get("band_rows") or []
    months_order = []
    if s2_band_rows:
        mo_dict = s2_band_rows[0].get("months") or {}
        months_order = list(mo_dict.keys())
    s2_cols = ([Column("band",    "Band (Ton)", "text", width=12),
                Column("mc_count","Machines",   "int")] +
               [Column(m, m, "num") for m in months_order] +
               [Column("_total_hrs", "Total Hrs",     "num", total=True),
                Column("_total_kg",  "Total Output (KG)", "kg",  total=True),
                Column("_avg_hr",    "Avg / Hr",      "num")])
    s2_rows = []
    for br in s2_band_rows:
        if not isinstance(br, dict):
            continue
        row = {"band": br.get("band",""), "mc_count": br.get("mc_count")}
        tot = br.get("total") or {}
        for m in months_order:
            mc = (br.get("months") or {}).get(m) or {}
            row[m] = _v(mc.get("hrs") if mc else None)
        row["_total_hrs"] = _v(tot.get("hrs"))
        row["_total_kg"]  = _v(tot.get("gross_kg"))
        row["_avg_hr"]    = _v(tot.get("avg_hr"))
        s2_rows.append(row)
    s2_total_r = s2.get("total_row") or {}
    s2_totrow = {"band":"TOTAL", "mc_count": None}
    for m in months_order:
        s2_totrow[m] = None
    s2_tot_total = s2_total_r.get("total") or {}
    s2_totrow["_total_hrs"] = _v(s2_tot_total.get("hrs"))
    s2_totrow["_total_kg"]  = _v(s2_tot_total.get("gross_kg"))
    s2_totrow["_avg_hr"]    = _v(s2_tot_total.get("avg_hr"))
    sheets.append(ReportSheet(
        name="SUMMARY-1",
        title=f"GOM — Band × Month (gross) — {fy_lbl}",
        subtitle="Hours, gross output and avg per hour by tonnage band per month.",
        sections=[Section(s2_cols, s2_rows, s2_totrow if s2_band_rows else None)],
    ))

    # ---- Per-band tabs (section3 — machine rows per band) ----
    s3 = d.get("section3") or {}
    by_band = s3.get("by_band") or {}
    band_mc_cols = [
        Column("band_mc_num", "Band MC#",   "text", width=12),
        Column("global_mc",   "Global MC",  "text", width=14),
        Column("mould_id",    "Mould ID",   "text", width=16),
    ]
    months_keys: List[str] = []
    for band_data in by_band.values():
        mr_list = band_data.get("machine_rows") or []
        if mr_list:
            mo_d = mr_list[0].get("months") or {}
            months_keys = list(mo_d.keys())
            break
    band_mc_cols += [Column(m, m, "num") for m in months_keys]
    band_mc_cols += [Column("_total_hrs","Total Hrs","num",total=True),
                     Column("_total_kg","Total KG","kg",total=True),
                     Column("_avg_hr","Avg/Hr","num")]
    for band_label, band_data in sorted(by_band.items(),
                                        key=lambda x: int(x[0]) if str(x[0]).isdigit() else 9999):
        mr_list = band_data.get("machine_rows") or []
        mc_band_rows = []
        for mr in mr_list:
            if not isinstance(mr, dict):
                continue
            row = {
                "band_mc_num": mr.get("band_mc_num",""),
                "global_mc":   mr.get("global_mc",""),
                "mould_id":    mr.get("mould_id",""),
            }
            tot = mr.get("total") or {}
            for m in months_keys:
                mc_val = (mr.get("months") or {}).get(m) or {}
                row[m] = _v(mc_val.get("hrs") if isinstance(mc_val, dict) else mc_val)
            row["_total_hrs"] = _v(tot.get("hrs"))
            row["_total_kg"]  = _v(tot.get("gross_kg"))
            row["_avg_hr"]    = _v(tot.get("avg_hr"))
            mc_band_rows.append(row)
        tr = band_data.get("total_row") or {}
        tot_r = tr.get("total") if isinstance(tr, dict) else {}
        band_totrow = {"band_mc_num":"TOTAL","global_mc":"","mould_id":""}
        for m in months_keys:
            band_totrow[m] = None
        band_totrow["_total_hrs"] = _v((tot_r or {}).get("hrs"))
        band_totrow["_total_kg"]  = _v((tot_r or {}).get("gross_kg"))
        band_totrow["_avg_hr"]    = _v((tot_r or {}).get("avg_hr"))
        sheets.append(ReportSheet(
            name=str(band_label)[:31],
            title=f"GOM — {band_label} Ton Machines — {fy_lbl}",
            subtitle="Per-machine hours and output for this tonnage band.",
            sections=[Section(band_mc_cols, mc_band_rows,
                              band_totrow if mr_list else None)],
        ))

    return sheets, flags


# ============================================================================
# R5 — Garden M/C Summary
#       builder: mgmt_garden_summary.build_garden_summary(fy)
# ============================================================================
def serial_garden(ym: str) -> _SheetFlagPair:
    from mgmt_garden_summary import build_garden_summary
    fy = _fy_from_ym(ym)
    d = build_garden_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    sec = d.get("section") or {}
    all_rows = (sec.get("month_rows_display") or sec.get("month_rows") or [])
    total_row = sec.get("total_row") or {}

    # ---- SUMMARY tab ----
    sum_cols = [
        Column("month_lbl",       "Month",          "text", width=10),
        Column("run_hrs",         "Run Hours",      "num",  total=True),
        Column("net_kg",          "Net KG",         "kg",   total=True),
        Column("reject_kg",       "Reject KG",      "kg",   total=True),
        Column("gross_kg",        "Gross KG",       "kg",   total=True),
        Column("rej_pct_gross",   "Rej % (gross)",  "pct"),
        Column("labour",          "Labour",         "int"),
        Column("paid_hrs",        "Paid Hrs",       "num"),
        Column("wages",           "Wages (₹)",      "num",  total=True),
        Column("contractor_wages","Contractor (₹)", "num",  total=True),
        Column("devoted_per_person","Devot./Person","num"),
        Column("per_hour_cost",   "Cost / Hr (₹)", "num"),
        Column("per_kg_cost",     "Cost / KG (₹)", "num"),
    ]
    sum_rows = []
    for r in all_rows:
        if not isinstance(r, dict):
            continue
        row = _row(r, [c.key for c in sum_cols])
        if r.get("awaiting_wages"):
            row["wages"] = "AWAITING SOURCE DATA"
            flags.append(Flag(
                rule="R-42",
                section="SUMMARY",
                month=str(r.get("month_lbl") or r.get("month_disp") or ""),
                our_figure="AWAITING",
                source_figure="—",
                difference="—",
                note=f"Garden wages not yet received for {r.get('month_lbl','')}.",
                cell_comment="Wages awaiting HR source sheet.",
            ))
        sum_rows.append(row)
    totrow = _row(total_row, [c.key for c in sum_cols])
    totrow["month_lbl"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"Garden M/C Summary — {fy_lbl}",
        subtitle="GARDEN plant — monthly hours, output (DR basis), wages and cost. "
                 "Net kg from the daily daily-report workbook.",
        sections=[Section(sum_cols, sum_rows, totrow if total_row else None)],
    ))

    # ---- SUMMARY-1 tab — DR vs WB reconciliation ----
    r23_rows = [r for r in all_rows if r.get("r23_has_data")]
    if r23_rows:
        r23_cols = [
            Column("month_lbl",  "Month",        "text", width=10),
            Column("net_kg",     "Our Net KG",   "kg"),
            Column("dr_net_kg",  "DR Net KG",    "kg"),
            Column("wb_net_kg",  "WB Net KG",    "kg"),
            Column("gross_kg",   "Our Gross KG", "kg"),
            Column("dr_gross_kg","DR Gross KG",  "kg"),
        ]
        r23_data = [_row(r, [c.key for c in r23_cols]) for r in r23_rows]
        for r in r23_rows:
            if r.get("r23_differs"):
                flags.append(Flag(
                    rule="R-23",
                    section="SUMMARY-1",
                    month=str(r.get("month_lbl") or ""),
                    our_figure=f"{r.get('net_kg',0):,.0f} kg",
                    source_figure=f"{r.get('dr_net_kg',0):,.0f} kg (DR)",
                    difference=f"{(r.get('net_kg',0) or 0) - (r.get('dr_net_kg',0) or 0):,.0f} kg",
                    note="Garden daily-report net KG differs from our computed figure.",
                    cell_comment="R-23: DR net KG differs from computed — see Notes.",
                ))
        sheets.append(ReportSheet(
            name="SUMMARY-1",
            title=f"Garden — DR vs WB Reconciliation — {fy_lbl}",
            subtitle="R-23 check: daily-report net KG vs our computed total.",
            sections=[Section(r23_cols, r23_data)],
        ))

    return sheets, flags


# ============================================================================
# R6 — HDPE M/C Summary
#       builder: mgmt_hdpe_summary.build_hdpe_summary(fy)
# ============================================================================
def serial_hdpe(ym: str) -> _SheetFlagPair:
    from mgmt_hdpe_summary import build_hdpe_summary
    fy = _fy_from_ym(ym)
    d = build_hdpe_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    sec = d.get("section") or {}
    all_rows = (sec.get("month_rows_display") or sec.get("month_rows") or [])
    total_row = sec.get("total_row") or {}

    sum_cols = [
        Column("month_lbl",       "Month",          "text", width=10),
        Column("run_hrs",         "Run Hours",      "num",  total=True),
        Column("net_kg",          "Net KG",         "kg",   total=True),
        Column("reject_kg",       "Reject KG",      "kg",   total=True),
        Column("gross_kg",        "Gross KG",       "kg",   total=True),
        Column("rej_pct_gross",   "Rej % (gross)",  "pct"),
        Column("labour",          "Labour",         "int"),
        Column("paid_hrs",        "Paid Hrs",       "num"),
        Column("wages",           "Wages (₹)",      "num",  total=True),
        Column("contractor_wages","Contractor (₹)", "num",  total=True),
        Column("devoted_per_person","Devot./Person","num"),
        Column("per_hour_cost",   "Cost / Hr (₹)", "num"),
        Column("per_kg_cost",     "Cost / KG (₹)", "num"),
    ]
    sum_rows = []
    for r in all_rows:
        if not isinstance(r, dict):
            continue
        row = _row(r, [c.key for c in sum_cols])
        if r.get("is_idle"):
            row["net_kg"] = "IDLE"
            row["gross_kg"] = "IDLE"
        if r.get("awaiting_wages"):
            row["wages"] = "AWAITING SOURCE DATA"
            flags.append(Flag(
                rule="R-42",
                section="SUMMARY",
                month=str(r.get("month_lbl") or ""),
                our_figure="AWAITING",
                source_figure="—",
                difference="—",
                note=f"HDPE wages not yet received for {r.get('month_lbl','')}.",
                cell_comment="Wages awaiting HR source sheet.",
            ))
        if r.get("r23_differs"):
            flags.append(Flag(
                rule="R-23",
                section="SUMMARY",
                month=str(r.get("month_lbl") or ""),
                our_figure=f"{r.get('net_kg',0):,.0f} kg",
                source_figure=f"{r.get('dr_net_kg',0):,.0f} kg (DR)",
                difference=f"{(r.get('net_kg',0) or 0) - (r.get('dr_net_kg',0) or 0):,.0f} kg",
                note="HDPE daily-report net KG differs from our computed figure.",
                cell_comment="R-23: DR net KG differs from computed.",
            ))
        sum_rows.append(row)
    totrow = _row(total_row, [c.key for c in sum_cols])
    totrow["month_lbl"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"HDPE M/C Summary — {fy_lbl}",
        subtitle="HDPE plant — monthly hours, output (DR basis), wages and cost.",
        sections=[Section(sum_cols, sum_rows, totrow if total_row else None)],
    ))

    return sheets, flags


# ============================================================================
# R7–9 — Tank KH / VN / WB Summary
#         builder: mgmt_tank_summary.build_tank_summary(plant, fy)
# ============================================================================
def serial_tank(ym: str, plant: str = "TANK") -> _SheetFlagPair:
    from mgmt_tank_summary import build_tank_summary
    fy = _fy_from_ym(ym)
    d = build_tank_summary(plant, fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")
    plant_lbl = d.get("plant_label", plant)
    months = d.get("months") or []
    months_disp = d.get("months_disp") or months

    def _tank_section_to_sheet(sec_dict, tab_name, subtitle):
        rows = sec_dict.get("rows") or []
        total = sec_dict.get("total") or {}
        if not rows:
            return ReportSheet(name=tab_name, title=f"{tab_name} — {fy_lbl}",
                               note="No data for this period.")
        # Columns: label + one col per month
        t_cols = ([Column("label", "Item", "text", width=24)] +
                  [Column(ym_k, disp, "num") for ym_k, disp in zip(months, months_disp)] +
                  [Column("_total", "Total / FY", "num", total=True)])
        t_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            t_row = {"label": row.get("label","") or row.get("item","")}
            mos = row.get("months") or {}
            row_total = 0.0
            any_val = False
            for ym_k in months:
                cell = mos.get(ym_k)
                if isinstance(cell, dict):
                    val = cell.get("prod") or cell.get("ltr") or cell.get("pcs") or cell.get("kg")
                elif cell is None:
                    val = None
                else:
                    val = cell
                t_row[ym_k] = _v(val)
                if val is not None and isinstance(val, (int,float)):
                    row_total += val
                    any_val = True
            t_row["_total"] = row_total if any_val else None
            t_rows.append(t_row)
        # total row
        totrow = {"label": total.get("label","TOTAL")}
        tot_mos = total.get("months") or {}
        grand_total = 0.0
        for ym_k in months:
            cell = tot_mos.get(ym_k)
            if isinstance(cell, dict):
                val = cell.get("prod") or cell.get("ltr") or cell.get("pcs") or cell.get("kg")
            elif cell is None:
                val = None
            else:
                val = cell
            totrow[ym_k] = _v(val)
            if val is not None and isinstance(val, (int,float)):
                grand_total += val
        totrow["_total"] = grand_total or None
        return ReportSheet(
            name=tab_name,
            title=f"{plant_lbl} — {tab_name} — {fy_lbl}",
            subtitle=subtitle,
            sections=[Section(t_cols, t_rows, totrow)],
        )

    # ---- SUMMARY (LTR) — section_type ----
    st = d.get("section_type") or {}
    sheets.append(_tank_section_to_sheet(
        st, "SUMMARY (LTR)",
        f"{plant_lbl} — production in litres, by item and month.",
    ))

    # ---- SUMMARY (PCS) — section_size ----
    ss = d.get("section_size") or {}
    sheets.append(_tank_section_to_sheet(
        ss, "SUMMARY (PCS)",
        f"{plant_lbl} — production in pieces, by item and month.",
    ))

    # ---- Per-size tabs (from section_type rows grouped by size label) ----
    size_groups: dict = {}
    for row in (st.get("rows") or []):
        if not isinstance(row, dict):
            continue
        item_lbl = str(row.get("label") or row.get("item") or "")
        # Extract size from label e.g. "500 LTR", "HKTANK 750 LTR"
        import re as _re
        m = _re.search(r"(\d{3,4})\s*(?:LTR|L|PCS)?", item_lbl.upper())
        size_key = m.group(1) + " LTR" if m else item_lbl[:20]
        size_groups.setdefault(size_key, []).append(row)
    for size_key, size_rows in size_groups.items():
        tab_name = size_key[:31]
        t_cols = ([Column("label", "Item", "text", width=24)] +
                  [Column(ym_k, disp, "num") for ym_k, disp in zip(months, months_disp)] +
                  [Column("_total", "Total", "num", total=True)])
        t_rows = []
        for row in size_rows:
            t_row = {"label": row.get("label","")}
            mos = row.get("months") or {}
            row_total = 0.0
            for ym_k in months:
                cell = mos.get(ym_k)
                val = (cell.get("prod") or cell.get("ltr") if isinstance(cell,dict) else cell)
                t_row[ym_k] = _v(val)
                if isinstance(val, (int,float)):
                    row_total += val
            t_row["_total"] = row_total or None
            t_rows.append(t_row)
        sheets.append(ReportSheet(
            name=tab_name,
            title=f"{plant_lbl} — {size_key} — {fy_lbl}",
            sections=[Section(t_cols, t_rows)],
        ))

    # ---- Flags: data_errors (R-26), divergences ----
    for err in (d.get("data_errors") or []):
        if isinstance(err, dict):
            flags.append(Flag(
                rule="R-26",
                section="SUMMARY (LTR)",
                month=str(err.get("month") or err.get("ym") or ""),
                our_figure=str(err.get("our") or ""),
                source_figure=str(err.get("sheet") or err.get("source") or ""),
                difference=str(err.get("diff") or ""),
                note=str(err.get("note") or err.get("msg") or err),
                cell_comment="R-26 data error — see Notes tab.",
            ))
    for div in (d.get("divergences") or []):
        if isinstance(div, dict):
            flags.append(Flag(
                rule="Divergence",
                section="SUMMARY",
                month=str(div.get("month") or ""),
                our_figure=str(div.get("ours") or ""),
                source_figure=str(div.get("sheet") or ""),
                difference=str(div.get("diff") or ""),
                note=str(div.get("note") or div),
            ))

    return sheets, flags


# ============================================================================
# R11 — PTMT Moulds Summary
#        builder: mgmt_ptmt_summary.build_ptmt_summary(fy)
# ============================================================================
def serial_ptmt_moulds(ym: str) -> _SheetFlagPair:
    from mgmt_ptmt_summary import build_ptmt_summary
    fy = _fy_from_ym(ym)
    d = build_ptmt_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    rows = d.get("rows") or []
    total = d.get("total") or {}
    sum_cols = [
        Column("disp",         "Month",          "text", width=12),
        Column("hours",        "Run Hours",      "num",  total=True),
        Column("output_kg",    "Output (KG)",    "kg",   total=True),
        Column("reject_kg",    "Reject (KG)",    "kg",   total=True),
        Column("runner_kg",    "Runner (KG)",    "kg",   total=True),
        Column("reject_pct",   "Reject %",       "pct"),
        Column("runner_pct",   "Runner %",       "pct"),
        Column("moulds",       "Active Moulds",  "int"),
        Column("av_hr_per_mould","Hrs / Mould",  "num"),
        Column("lumps_kg",     "Lumps (KG)",     "kg"),
        Column("wastage_pct",  "Wastage %",      "pct"),
        Column("grinder_kg",   "Grinder (KG)",   "kg"),
        Column("labour",       "Labour",         "int"),
        Column("paid_hours",   "Paid Hours",     "num"),
        Column("wages",        "Wages (₹)",      "num",  total=True),
        Column("cost_per_hr",  "Cost / Hr (₹)",  "num"),
        Column("cost_per_kg",  "Cost / KG (₹)",  "num"),
    ]
    data_rows = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("has_data"):
            continue
        row = _row(r, [c.key for c in sum_cols])
        # R-24 divergences
        r24 = r.get("r24") or {}
        if isinstance(r24, dict) and r24.get("note"):
            flags.append(Flag(
                rule="R-24",
                section="SUMMARY",
                month=str(r.get("disp") or r.get("ym") or ""),
                our_figure=str(r24.get("daily") or ""),
                source_figure=str(r24.get("annual") or ""),
                difference="",
                note=str(r24.get("note") or ""),
                cell_comment="R-24: daily vs annual figures differ — see Notes.",
            ))
        data_rows.append(row)
    totrow = _row(total, [c.key for c in sum_cols])
    totrow["disp"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"PTMT Moulds Summary — {fy_lbl}",
        subtitle="Bhiwadi — PTMT monthly production, run hours, rejection, wages. "
                 "Run hours from Report-5 (machine hours). "
                 "Note: column total in source sheet may include CPVC (R-24).",
        sections=[Section(sum_cols, data_rows, totrow if total else None)],
    ))

    # Flags from sheet_total_bugs
    for bug in (d.get("sheet_total_bugs") or []):
        if isinstance(bug, dict):
            flags.append(Flag(
                rule="R-24",
                section="SUMMARY",
                month="",
                our_figure=str(bug.get("sourced") or bug.get("correct") or ""),
                source_figure=str(bug.get("formula") or ""),
                difference="",
                note=str(bug.get("note") or
                         f"Sheet TOTAL defect in column '{bug.get('col','')}' "
                         f"of sheet '{bug.get('sheet','')}'."),
                cell_comment=f"R-24 sheet total defect: {bug.get('col','')}",
            ))

    # Add note about omitted tabs
    sheets.append(ReportSheet(
        name="Note on Omitted Tabs",
        title="PTMT — Tabs Requiring Per-Machine Data",
        note=(
            "The source workbook contains MC Utilization, Month Wise MC, "
            "Group Wise, and Material Wise tabs. These require per-machine "
            "monthly data that is not currently available from the "
            "build_ptmt_summary builder output. Only the SUMMARY tab is "
            "produced here. The web-based management report page shows the "
            "same SUMMARY-level figures."
        ),
    ))

    return sheets, flags


# ============================================================================
# R12 — PTMT Mould Efficiency
#        builder: mgmt_ptmt_mould_eff.build_ptmt_mould_eff(fy)
# ============================================================================
def serial_ptmt_mould_eff(ym: str) -> _SheetFlagPair:
    from mgmt_ptmt_mould_eff import build_ptmt_mould_eff
    fy = _fy_from_ym(ym)
    d = build_ptmt_mould_eff(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    rows = d.get("rows") or []
    total_row = d.get("total_row") or {}
    months = d.get("months") or []
    month_labels = d.get("month_labels") or months

    # Build columns: mould_id + per-month + total
    eff_cols = [
        Column("mould_id",  "Mould ID",        "text", width=18),
        Column("group",     "Group",           "text", width=12),
    ]
    for ym_k, lbl in zip(months, month_labels):
        eff_cols.append(Column(f"m_{ym_k}", str(lbl)[:10], "num"))
    eff_cols += [
        Column("total_pcs", "Total Pcs",       "int",  total=True),
        Column("total_hrs", "Total Hrs",       "num",  total=True),
        Column("ideal_hrs", "Ideal Hrs",       "num"),
        Column("util_pct",  "Utilisation %",   "pct"),
    ]

    eff_rows = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        row = {
            "mould_id": r.get("mould_id",""),
            "group":    r.get("group",""),
        }
        for ym_k in months:
            mo_data = None
            for blk in (r.get("monthly") or []):
                if isinstance(blk, dict) and blk.get("ym") == ym_k:
                    mo_data = blk
                    break
            row[f"m_{ym_k}"] = _v((mo_data or {}).get("pcs") or (mo_data or {}).get("actual"))
        total_blk = r.get("total") or {}
        row["total_pcs"] = _v(total_blk.get("pcs") or total_blk.get("actual"))
        row["total_hrs"] = _v(total_blk.get("hrs") or total_blk.get("hours"))
        row["ideal_hrs"] = _v(total_blk.get("ideal_hrs") or total_blk.get("ideal"))
        row["util_pct"]  = _v(total_blk.get("util_pct") or total_blk.get("util"))
        eff_rows.append(row)

    tr_total = total_row.get("total") or {}
    totrow = {"mould_id":"TOTAL","group":""}
    for ym_k in months:
        totrow[f"m_{ym_k}"] = None
    totrow["total_pcs"] = _v(tr_total.get("pcs") or tr_total.get("actual"))
    totrow["total_hrs"] = _v(tr_total.get("hrs") or tr_total.get("hours"))
    totrow["ideal_hrs"] = _v(tr_total.get("ideal_hrs") or tr_total.get("ideal"))
    totrow["util_pct"]  = _v(tr_total.get("util_pct") or tr_total.get("util"))

    prov = []
    if d.get("sourcing_note"):
        prov.append(d["sourcing_note"])
    sheets.append(ReportSheet(
        name="PTMT",
        title=f"PTMT Mould Efficiency — {fy_lbl}",
        subtitle="Bhiwadi — per-mould production and utilisation. "
                 "Ideal hours: from annual workbook (— if unavailable).",
        sections=[Section(eff_cols, eff_rows, totrow if total_row else None)],
        provenance=prov,
    ))

    # Anchor flags
    for anc in (d.get("anchors") or []):
        if isinstance(anc, dict):
            status = anc.get("status","")
            if status not in ("ok","PASS","green"):
                flags.append(Flag(
                    rule="Anchor",
                    section="PTMT",
                    month="",
                    our_figure=str(anc.get("ours") or ""),
                    source_figure=str(anc.get("spec") or ""),
                    difference=str(anc.get("diff") or ""),
                    note=str(anc.get("note") or anc),
                ))

    return sheets, flags


# ============================================================================
# R13 — Pipe Moulds Summary
#        builder: mgmt_pipe_moulds_summary.build_pipe_moulds_summary(fy)
# ============================================================================
def serial_pipe_moulds(ym: str) -> _SheetFlagPair:
    from mgmt_pipe_moulds_summary import build_pipe_moulds_summary
    fy = _fy_from_ym(ym)
    d = build_pipe_moulds_summary(fy)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    blocks = d.get("blocks") or []

    # ---- SUMMARY tab — recon table ----
    recon = d.get("recon") or []
    if recon:
        rec_cols = [
            Column("label",  "Check",          "text", width=28),
            Column("ours",   "Our Figure",     "text", width=16),
            Column("spec",   "Spec / Source",  "text", width=16),
            Column("status", "Status",         "text", width=10),
            Column("note",   "Note",           "text", width=40),
        ]
        rec_rows = [_row(r, [c.key for c in rec_cols]) for r in recon
                    if isinstance(r, dict)]
        sheets.append(ReportSheet(
            name="SUMMARY",
            title=f"Pipe Moulds — Reconciliation Summary — {fy_lbl}",
            subtitle="Self-check table comparing computed figures to source anchors.",
            sections=[Section(rec_cols, rec_rows)],
        ))
    else:
        sheets.append(ReportSheet(name="SUMMARY",
                                  title=f"Pipe Moulds Summary — {fy_lbl}"))

    # ---- Per-FY block tabs ----
    pm_cols = [
        Column("material",  "Material",     "text", width=14),
        Column("n_total",   "Total Moulds", "int",  total=True),
        Column("n_run",     "Run Moulds",   "int",  total=True),
        Column("hrs",       "Hours",        "num",  total=True),
        Column("av_hr",     "Avg Hrs",      "num"),
        Column("pcs",       "Pieces",       "int",  total=True),
        Column("kg",        "KG",           "kg",   total=True),
        Column("avg_month", "Avg / Month",  "num"),
    ]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        period_lbl = block.get("period_label") or block.get("fy_key") or "FY"
        blk_rows = block.get("rows") or []
        blk_total = block.get("total_row") or {}
        missing = block.get("missing") or []
        unavail = block.get("unavailable") or []

        b_data = [_row(r, [c.key for c in pm_cols]) for r in blk_rows
                  if isinstance(r, dict)]
        b_totrow = _row(blk_total, [c.key for c in pm_cols])
        b_totrow["material"] = "TOTAL"

        # PPR missing flag
        if not block.get("has_ppr"):
            flags.append(Flag(
                rule="PPR-Missing",
                section=period_lbl,
                month="",
                our_figure="—",
                source_figure="PPR",
                difference="—",
                note=f"PPR mould data not available for {period_lbl}. "
                     "It is excluded from the TOTAL.",
                cell_comment="PPR excluded from TOTAL — data unavailable.",
            ))
        for mv in missing:
            flags.append(Flag(rule="Missing", section=period_lbl,
                              note=str(mv)))
        for uv in unavail:
            flags.append(Flag(rule="Unavailable", section=period_lbl,
                              note=str(uv)))

        prov = []
        if d.get("sourcing_note"):
            prov = [d["sourcing_note"]]
        if d.get("defect_note"):
            prov.append(d["defect_note"])
            flags.append(Flag(
                rule="Sheet-Defect",
                section=period_lbl,
                note=d["defect_note"],
            ))
        if d.get("hours_note"):
            prov.append(d["hours_note"])

        sheets.append(ReportSheet(
            name=period_lbl[:31],
            title=f"Pipe Moulds — {period_lbl} — {fy_lbl}",
            subtitle="Mould run count, hours, pieces and KG by material.",
            sections=[Section(pm_cols, b_data, b_totrow if blk_total else None)],
            provenance=prov,
        ))

    return sheets, flags
