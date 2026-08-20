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
from report_cell_accessors import pivot_cell

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


def _pivot_pair_columns(machines: list[str]) -> list[Column]:
    """Excel columns matching the page's Hours | Output pair per machine."""
    columns: list[Column] = []
    for machine in machines:
        columns.extend([
            Column(f"{machine}__hrs", f"{machine} Hrs", "int"),
            Column(f"{machine}__out", f"{machine} Output (KG)", "kg"),
        ])
    return columns


def _pivot_pair_row(label: str, cells: dict, machines: list[str]) -> dict:
    """Flatten builder pivot cells without changing their Hours/Output meaning."""
    row = {"month_lbl": label}
    for machine in machines:
        cell = cells.get(machine)
        row[f"{machine}__hrs"] = pivot_cell(cell, "hrs")
        row[f"{machine}__out"] = pivot_cell(cell, "out")
    return row


def _month_metric_columns(months: list[str]) -> list[Column]:
    """Create the page-equivalent Hrs | Gross KG | Avg/Hr group for each month."""
    columns: list[Column] = []
    for month in months:
        columns.extend([
            Column(f"{month}__hrs", f"{month} Hrs", "num"),
            Column(f"{month}__gross_kg", f"{month} Gross KG", "kg"),
            Column(f"{month}__avg_hr", f"{month} Avg/Hr", "rate"),
        ])
    return columns


def _set_month_metrics(row: dict, month: str, values: object) -> None:
    """Flatten a GOM nested monthly cell without losing any page-visible metric."""
    cell = values if isinstance(values, dict) else {}
    row[f"{month}__hrs"] = cell.get("hrs")
    row[f"{month}__gross_kg"] = cell.get("gross_kg")
    row[f"{month}__avg_hr"] = cell.get("avg_hr")


def _build_failed(err) -> _SheetFlagPair:
    return [], [Flag(rule="BUILD FAILURE", section="All", note=str(err))]


# ============================================================================
# R1 — Segment Labour / Power / Ideal Cost
#       builder: mgmt_labour_power.build_mgmt_report_data(fy)
# ============================================================================
def serial_segment_labour(ym: str) -> _SheetFlagPair:
    from mgmt_labour_power import (
        build_mgmt_report_data, _IDEAL_COST_SEGS,
    )
    fy = _fy_from_ym(ym)
    d = build_mgmt_report_data(fy, through_ym=ym)
    if d.get("error"):
        return _build_failed(d["error"])
    flags: List[Flag] = []
    sheets: List[ReportSheet] = []
    fy_lbl = d.get("fy_label", f"FY {fy}")

    # =========================================================================
    # Tab 1 — Combined Wages
    # Shows per-segment rows (indented) grouped under their unit, then a unit
    # subtotal row, then the grand-total row.
    # =========================================================================
    cw = d.get("combined_wages") or {}
    cw_cols = [
        Column("label",            "Segment / Unit",    "text", width=20),
        Column("n_payroll",        "Payroll Staff",     "int"),
        Column("n_contractor",     "Contractor",        "int"),
        Column("n_total",          "Total Headcount",   "int",  total=True),
        Column("paid_wages",       "Payroll Wages (₹)", "cur",  total=True),
        Column("contractor_wages", "Contractor (₹)",    "cur",  total=True),
        Column("total_wages",      "Total Wages (₹)",   "cur",  total=True),
    ]
    cw_keys = ["n_payroll","n_contractor","n_total","paid_wages",
               "contractor_wages","total_wages"]
    cw_rows = []
    for u in (cw.get("units") or []):
        if not isinstance(u, dict):
            continue
        # Per-segment rows indented under this unit
        for seg in (u.get("segments") or []):
            if not isinstance(seg, dict):
                continue
            cw_rows.append({**_row(seg, cw_keys),
                            "label": f"  {seg.get('name','')}"})
        # Unit subtotal
        cw_rows.append({**_row(u, cw_keys),
                        "label": u.get("label","")})
    gt = cw.get("grand_total") or {}
    cw_total = {**_row(gt, cw_keys), "label": "GRAND TOTAL"}
    sheets.append(ReportSheet(
        name="Combined Wages",
        title=f"Combined Wages — {fy_lbl}",
        subtitle=(
            "Per-segment headcount and wages from the annual segment-cost workbook. "
            "Indented rows = segments; bold rows = unit subtotals."
        ),
        sections=[Section(cw_cols, cw_rows, cw_total if gt else None)],
    ))

    # =========================================================================
    # Tabs 2–4 — UNIT-1 / UNIT-2 / UNIT-3
    # 17-column layout matching the source workbook.
    # Row order: TOTAL row first (per segment), then APR → MAR.
    # =========================================================================
    # Note: kwh / unit_per_kg / rate_708 / solar are UNIT-level columns in the
    # source workbook; the per-segment tabs do not carry them, so those cells
    # will be blank for per-segment rows.  jvvl here = per-segment JVVL Power
    # Amount (₹), which equals total_power for per-seg rows.
    unit_cols = [
        Column("segment",          "Segment",               "text", width=14),
        Column("month_label",      "Month",                 "text", width=8),
        Column("n_labour",         "No. Of Labour",         "int"),
        Column("n_contractor",     "Contractor Labour",     "int"),
        Column("n_total_lab",      "Total Labour",          "int"),
        Column("paid_wages",       "Paid Wages (₹)",        "cur",  total=True),
        Column("contractor_wages", "Contr. Wages (₹)",      "cur",  total=True),
        Column("total_wages",      "Total Paid Wages (₹)", "cur",  total=True),
        Column("our_prod_kg",      "Total Prod. (Kgs)",    "kg",   total=True),
        Column("jvvl",             "JVVL Power Amt (₹)",   "cur",  total=True),
        Column("kwh",              "Elec. Gen. (kWh)",      "num"),
        Column("unit_per_kg",      "Unit / KG",             "rate"),
        Column("rate_708",         "7.08 Basic Rate",       "rate"),
        Column("total_power",      "Total Power (₹)",       "cur",  total=True),
        Column("per_kg_power",     "Per KG Power (₹)",      "rate"),
        Column("per_kg_labour",    "Per KG Labour (₹)",     "rate"),
        Column("total_cost",       "Total Cost (₹)",        "cur",  total=True),
    ]
    unit_keys = [c.key for c in unit_cols]

    for u in (d.get("units") or []):
        if not isinstance(u, dict):
            continue
        u_lbl = u.get("label", "UNIT")
        tab_rows: List[dict] = []
        tab_comments: dict = {}

        for seg in (u.get("segments") or []):
            if not isinstance(seg, dict):
                continue
            seg_name = seg.get("name", "")

            # TOTAL row for this segment comes first
            tr = seg.get("total_row") or {}
            tot_row = _row(tr, unit_keys)
            tot_row["segment"]     = seg_name
            tot_row["month_label"] = "TOTAL"
            if tr.get("awaiting"):
                tot_row["total_wages"] = "AWAITING SOURCE DATA"
            tab_rows.append(tot_row)

            # Monthly rows APR → MAR
            for mr in (seg.get("month_rows") or []):
                if not isinstance(mr, dict):
                    continue
                row = _row(mr, unit_keys)
                row["segment"]     = seg_name
                row["month_label"] = str(
                    mr.get("month_label") or mr.get("month_disp") or "")
                if mr.get("awaiting"):
                    row["total_wages"] = "AWAITING SOURCE DATA"
                    mo_lbl = row["month_label"]
                    flags.append(Flag(
                        rule="R-07",
                        section=u_lbl,
                        month=f"{seg_name} {mo_lbl}",
                        our_figure="AWAITING SOURCE DATA",
                        source_figure="—",
                        difference="—",
                        note=(
                            f"Wages for {seg_name} {mo_lbl} not yet received "
                            f"from HR (R-07 / R-08). Cell shows 'AWAITING SOURCE "
                            f"DATA', never 0 or blank."
                        ),
                        cell_comment="R-07: Wages awaiting HR source sheet.",
                    ))
                    if seg_name and mo_lbl:
                        tab_comments[(f"{seg_name}|{mo_lbl}", "total_wages")] = (
                            "R-07: Wages awaiting HR source sheet.")
                tab_rows.append(row)

        # Standing #REF! flag for UNIT-1 (source workbook roll-up tab is broken)
        if u_lbl == "UNIT-1":
            flags.append(Flag(
                rule="UNIT-1 #REF!",
                section=u_lbl,
                month="All months",
                our_figure="Computed (per-segment tabs: CP, PTMT, Hardware, Sink)",
                source_figure="#REF! / 0 (UNIT-1 roll-up tab in source workbook)",
                difference="—",
                note=(
                    "The source workbook's UNIT-1 roll-up tab has #REF! errors in "
                    "the headcount columns and reads 0 in the production column. "
                    "Our figures are computed from the per-segment tabs (CP, PTMT, "
                    "Hardware, Sink), which do not have this defect. "
                    "Cardinal Rule: we report the per-segment computed figure; "
                    "the broken roll-up tab is not used."
                ),
            ))

        # R-22 basis note (production = gross in costing; net on production pages)
        sheets.append(ReportSheet(
            name=u_lbl,
            title=f"{u_lbl} — Labour & Power Cost — {fy_lbl}",
            subtitle=(
                "R-22: 'Total Prod. (Kgs)' is GROSS (net + rejection), matching "
                "the source workbook's own note. Production management pages use net. "
                "TOTAL row appears first per segment, then APR–MAR. "
                "kwh / Unit/KG / 7.08 Rate columns are UNIT-level in the source "
                "workbook; they are blank here (per-segment tabs do not carry them)."
            ),
            sections=[Section(unit_cols, tab_rows)],
            cell_comments=tab_comments,
        ))

    # Standing R-22 flag (one entry, covers all three UNIT tabs)
    flags.append(Flag(
        rule="R-22",
        section="UNIT-1 / UNIT-2 / UNIT-3",
        month="All",
        our_figure="Gross (net + rejection)",
        source_figure="As per source workbook note",
        difference="—",
        note=(
            "Production figures in the UNIT tabs use GROSS basis (total output "
            "including rejection), matching the source workbook note. "
            "The plant production pages (Garden, HDPE, Pipe, PTMT) use NET. "
            "This is intentional — the costing model uses gross."
        ),
    ))

    # =========================================================================
    # Tab 5 — Ideal Power Cost
    # Columns: Month | Pipe | Fittings | Garden | HDPE | Tank | PTMT | Total
    # Builder shape: ideal_power_sec.months = [{month_lbl, segs: {seg: {net, ideal_cost}}, total_ideal}, ...]
    # =========================================================================
    _R43_POWER_NOTE = (
        "R-43: The source workbook's Fittings production column in the Ideal "
        "Power Cost tab contains PIECE COUNTS (APR: 13,40,117 pcs), not kg. "
        "Multiplying by a per-kg rate inflates the Ideal Power Cost for Fittings "
        "~15×. Our figure uses Moulding gross output kg, which is the correct basis."
    )
    _R43_LABOUR_NOTE = (
        "R-43: The source workbook's Fittings production column in the Ideal "
        "Labour Cost tab contains PIECE COUNTS (APR: 13,40,117 pcs), not kg. "
        "Multiplying by a per-kg rate inflates the Ideal Labour Cost for Fittings "
        "~15×. Our figure uses Moulding gross output kg, which is the correct basis."
    )
    _IC_SEGS = list(_IDEAL_COST_SEGS)  # ["Pipe","Fittings","Garden","HDPE","Tank","PTMT"]

    def _build_ideal_tab(sec: dict, kind: str, note: str) -> ReportSheet:
        """Pivot {month_lbl, segs:{seg:{ideal_cost}}, total_ideal} into tabular form."""
        months_data = sec.get("months") or []
        tot_raw     = sec.get("total_row") or {}
        tab_name    = "Ideal Power Cost" if kind == "power" else "Ideal Labour Cost"
        rate_note   = "₹/kg rates: Pipe 4.0, Fittings 8.0, Garden 3.0, HDPE 4.0, Tank 5.0, PTMT 5.0" \
                      if kind == "power" else \
                      "₹/kg rates: Pipe 2.5, Fittings 6.5, Garden 3.0, HDPE 1.25, Tank 6.0, PTMT 6.0"

        cols = [Column("month_lbl", "Month", "text", width=8)]
        for seg in _IC_SEGS:
            cols.append(Column(f"ic_{seg}", seg, "cur", total=True))
        cols.append(Column("total_ideal", "Total Ideal (₹)", "cur", total=True))

        rows: List[dict] = []
        cell_comments: dict = {}
        for mo in months_data:
            if not isinstance(mo, dict):
                continue
            mo_lbl = mo.get("month_lbl") or mo.get("month_disp") or ""
            row: dict = {"month_lbl": mo_lbl}
            segs_data = mo.get("segs") or {}
            for seg in _IC_SEGS:
                row[f"ic_{seg}"] = _v((segs_data.get(seg) or {}).get("ideal_cost"))
            row["total_ideal"] = _v(mo.get("total_ideal"))
            rows.append(row)
            # R-43 cell comment on Fittings column for every month
            if mo_lbl:
                cell_comments[(mo_lbl, "ic_Fittings")] = (
                    "R-43: Fittings production is kg (Moulding gross output). "
                    "Source workbook column is piece count — inflates ideal cost ~15×."
                )

        tot_segs = (tot_raw.get("segs") or {})
        total_row: dict = {"month_lbl": "TOTAL"}
        for seg in _IC_SEGS:
            total_row[f"ic_{seg}"] = _v((tot_segs.get(seg) or {}).get("ideal_cost"))
        total_row["total_ideal"] = _v(tot_raw.get("total_ideal"))

        return ReportSheet(
            name=tab_name,
            title=f"{tab_name} — {fy_lbl}",
            subtitle=(
                f"Monthly ideal cost by segment (₹ = gross production kg × rate). "
                f"{rate_note}. "
                f"R-43: Fittings column uses Moulding gross kg (not source piece count)."
            ),
            sections=[Section(cols, rows, total_row if rows else None)],
            cell_comments=cell_comments,
        )

    ip = d.get("ideal_power_sec") or {}
    if ip.get("months"):
        sheets.append(_build_ideal_tab(ip, "power", _R43_POWER_NOTE))
        flags.append(Flag(
            rule="R-43",
            section="Ideal Power Cost",
            month="All months",
            our_figure="Moulding gross kg × ₹8.0/kg",
            source_figure="Piece count × ₹8.0/kg (APR: 13,40,117 pcs)",
            difference="~15× inflated in source",
            note=_R43_POWER_NOTE,
            cell_comment="R-43: Fittings uses kg, not the source's piece count.",
        ))
    else:
        sheets.append(ReportSheet(
            name="Ideal Power Cost",
            title=f"Ideal Power Cost — {fy_lbl}",
            note="Ideal power cost not available from builder output.",
        ))

    il = d.get("ideal_labour_sec") or {}
    if il.get("months"):
        sheets.append(_build_ideal_tab(il, "labour", _R43_LABOUR_NOTE))
        flags.append(Flag(
            rule="R-43",
            section="Ideal Labour Cost",
            month="All months",
            our_figure="Moulding gross kg × ₹6.5/kg",
            source_figure="Piece count × ₹6.5/kg (APR: 13,40,117 pcs)",
            difference="~15× inflated in source",
            note=_R43_LABOUR_NOTE,
            cell_comment="R-43: Fittings uses kg, not the source's piece count.",
        ))
    else:
        sheets.append(ReportSheet(
            name="Ideal Labour Cost",
            title=f"Ideal Labour Cost — {fy_lbl}",
            note="Ideal labour cost not available from builder output.",
        ))

    # =========================================================================
    # Tab 7 — REJECTION & PRODUCTION
    # Builder shape: reject_prod_sec.months = [{month_lbl, segs:{seg:{net,reject,rej_pct,r24_note?}}}]
    # 3 columns per segment: Net KG | Reject KG | Rej %
    # =========================================================================
    rp = d.get("reject_prod_sec") or {}
    rp_segs_list = [s for s in (rp.get("segs") or []) if isinstance(s, str)]
    rp_months    = rp.get("months") or []

    if rp_segs_list and rp_months:
        rp_cols = [Column("month_lbl", "Month", "text", width=8)]
        for seg in rp_segs_list:
            rp_cols.append(Column(f"rp_{seg}_net",
                                  f"{seg} Net (KG)", "kg", total=True))
            rp_cols.append(Column(f"rp_{seg}_rej",
                                  f"{seg} Rej. (KG)", "kg", total=True))
            rp_cols.append(Column(f"rp_{seg}_pct",
                                  f"{seg} Rej. %", "pct"))

        rp_rows: List[dict] = []
        rp_comments: dict = {}
        for mo in rp_months:
            if not isinstance(mo, dict):
                continue
            mo_lbl = mo.get("month_lbl") or mo.get("month_disp") or ""
            row: dict = {"month_lbl": mo_lbl}
            segs_data = mo.get("segs") or {}
            for seg in rp_segs_list:
                sd = segs_data.get(seg) or {}
                row[f"rp_{seg}_net"] = _v(sd.get("net"))
                row[f"rp_{seg}_rej"] = _v(sd.get("reject"))
                row[f"rp_{seg}_pct"] = _v(sd.get("rej_pct"))
                # R-24: PTMT June (and any future months)
                r24_note = sd.get("r24_note")
                if r24_note:
                    net_val    = sd.get("net") or 0.0
                    annual_val = sd.get("r24_annual") or 0
                    flags.append(Flag(
                        rule="R-24",
                        section="REJECTION & PRODUCTION",
                        month=f"{seg} {mo_lbl}",
                        our_figure=f"{net_val:,.0f} kg (daily records)",
                        source_figure=f"{annual_val:,} kg (annual / mould chain)",
                        difference="Open — notified to Alok Roy",
                        note=r24_note,
                        cell_comment=f"R-24: {r24_note}",
                    ))
                    if mo_lbl:
                        rp_comments[(mo_lbl, f"rp_{seg}_net")] = (
                            f"R-24: {r24_note}"
                        )
            rp_rows.append(row)

        # Total row
        rp_tot_segs = ((rp.get("total_row") or {}).get("segs") or {})
        rp_total: dict = {"month_lbl": "TOTAL"}
        for seg in rp_segs_list:
            sd = rp_tot_segs.get(seg) or {}
            rp_total[f"rp_{seg}_net"] = _v(sd.get("net"))
            rp_total[f"rp_{seg}_rej"] = _v(sd.get("reject"))
            rp_total[f"rp_{seg}_pct"] = _v(sd.get("rej_pct"))

        sheets.append(ReportSheet(
            name="REJECTION & PRODUCTION",
            title=f"Rejection & Production — {fy_lbl}",
            subtitle=(
                "Monthly net production, rejection and rejection % by segment. "
                "R-24: PTMT June divergence — daily 147,835 kg vs annual "
                "(mould chain) 160,478 kg; open with Alok Roy."
            ),
            sections=[Section(rp_cols, rp_rows, rp_total)],
            cell_comments=rp_comments,
        ))
    else:
        sheets.append(ReportSheet(
            name="REJECTION & PRODUCTION",
            title=f"Rejection & Production — {fy_lbl}",
            note="Rejection & production data not available from builder output.",
        ))

    # R-42 Garden Pipe wages standing note (R5 serial has the monthly flag;
    # document the cross-source divergence here for the R1 export)
    flags.append(Flag(
        rule="R-42",
        section="UNIT-3 / Garden Pipe",
        month="FY total",
        our_figure="₹2,20,797 payroll + ₹50,547 contractor (Segment Cost source)",
        source_figure="₹4,26,164 (Garden annual workbook)",
        difference="Same paid hours in both; reconciliation open",
        note=(
            "Garden Pipe wages from the Segment Cost workbook "
            "(₹2,20,797 payroll + ₹50,547 contractor = ₹2,71,344 total) "
            "differ from the Garden annual workbook total (₹4,26,164). "
            "Paid hours are identical in both sources. "
            "R-42: figures shown are from the Segment Cost workbook (authoritative "
            "for this report). The divergence is open with the data owner."
        ),
    ))

    return sheets, flags


# ============================================================================
# R2 — Pipe M/C Summary
#       builder: mgmt_pipe_summary.build_pipe_summary(fy)
# ============================================================================
def serial_pipe(ym: str) -> _SheetFlagPair:
    from mgmt_pipe_summary import build_pipe_summary
    fy = _fy_from_ym(ym)
    d = build_pipe_summary(fy, through_ym=ym)
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
        Column("run_hrs",           "Run Hours",        "int",  total=True),
        Column("gross_output_kg",   "Output (KG)",      "kg",   total=True),
        Column("labour",            "Labour",           "int",  total=True),
        Column("paid_hrs",          "Paid Hours",       "num"),
        Column("wages",             "Wages (₹)",        "cur",  total=True),
        Column("devoted_per_person","Devot./Person",    "rate"),
        Column("per_hour_cost",     "Cost / Hr (₹)",   "rate"),
        Column("per_kg_cost",       "Cost / KG (₹)",   "rate"),
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
        Column("actual_hrs",   "Actual Hrs",        "int",  total=True),
        Column("ideal_hrs",    "Ideal Hrs",         "int"),
        Column("actual_out_kg","Output (KG)",       "kg",   total=True),
        Column("ideal_rate",   "Ideal Rate",        "rate"),
        Column("avg_hr",       "Avg / Hr",          "rate"),
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
        Column("hrs",       "Hours",         "int",  total=True),
        Column("output_kg", "Output (KG)",   "kg",   total=True),
        Column("ideal_out", "Ideal Out",     "kg"),
        Column("ideal_hrs", "Ideal Hrs",     "int"),
        Column("avg_hr",    "Avg / Hr",      "rate"),
        Column("util_pct",  "Util %",        "pct"),
        Column("out_eff",   "Out Eff %",     "pct"),
    ]
    def _s3_row(r):
        row = _row(r, [c.key for c in s3_cols])
        # "machines" is stored as a list in the builder — join for the cell
        if isinstance(row.get("machines"), list):
            row["machines"] = ", ".join(str(m) for m in row["machines"])
        return row
    s3_data = [_s3_row(r) for r in s3_groups]
    s3_totrow = _row(s3_total, [c.key for c in s3_cols])
    s3_totrow["type"] = "TOTAL"
    sheets.append(ReportSheet(
        name="MATERIAL",
        title=f"Pipe — Material Group Summary — {fy_lbl}",
        sections=[Section(s3_cols, s3_data, s3_totrow if s3_total else None)],
    ))

    # ---- MC WISE tab + HOURS + OUTPUT (all from section4 data) ----
    s4 = d.get("section4") or {}
    s4_month_rows = s4.get("month_rows") or []
    s4_total_cols  = s4.get("total_cols") or {}
    mc_labels = list(s4_total_cols.keys()) if s4_total_cols else []

    if s4_month_rows and mc_labels:
        mcw_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                    _pivot_pair_columns(mc_labels))
        hrs_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                    [Column(mc, mc, "int") for mc in mc_labels])
        out_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                    [Column(mc, mc, "kg") for mc in mc_labels])
        mcw_rows, hrs_rows, out_rows = [], [], []
        total_row_mcw = _pivot_pair_row("TOTAL", s4_total_cols, mc_labels)
        hrs_totrow     = {"month_lbl": "TOTAL"}
        out_totrow     = {"month_lbl": "TOTAL"}
        for mo in s4_month_rows:
            if not isinstance(mo, dict):
                continue
            cols_data = mo.get("cols") or {}
            lbl = mo.get("month_lbl") or mo.get("month_disp") or ""
            mrow = _pivot_pair_row(lbl, cols_data, mc_labels)
            hrow = {"month_lbl": lbl}
            orow = {"month_lbl": lbl}
            for mc in mc_labels:
                mc_data = cols_data.get(mc)
                hrow[mc] = pivot_cell(mc_data, "hrs")
                orow[mc] = pivot_cell(mc_data, "out")
            mcw_rows.append(mrow)
            hrs_rows.append(hrow)
            out_rows.append(orow)
        for mc, mc_data in s4_total_cols.items():
            hrs_totrow[mc] = pivot_cell(mc_data, "hrs")
            out_totrow[mc] = pivot_cell(mc_data, "out")
        sheets.append(ReportSheet(
            name="MC WISE",
            title=f"Pipe — Machine-wise Monthly — {fy_lbl}",
            subtitle="Month × machine matrix. Each machine has Hours and Output (KG).",
            sections=[Section(mcw_cols, mcw_rows, total_row_mcw)],
        ))
        sheets.append(ReportSheet(
            name="HOURS",
            title=f"Pipe — Run Hours by Machine & Month — {fy_lbl}",
            subtitle="Machine actual run hours per month (integer hours).",
            sections=[Section(hrs_cols, hrs_rows, hrs_totrow)],
        ))
        sheets.append(ReportSheet(
            name="OUTPUT",
            title=f"Pipe — Output (KG) by Machine & Month — {fy_lbl}",
            subtitle="Machine gross output KG per month.",
            sections=[Section(out_cols, out_rows, out_totrow)],
        ))

    # ---- MONTHWISE tab (section5: flat listing, one row per machine-month) ----
    s5 = d.get("section5") or {}
    s5_machines = s5.get("machines") or []
    if s5_machines:
        mw_cols = [
            Column("machine",     "Machine",     "text", width=10),
            Column("pipe_type",   "Type",        "text", width=10),
            Column("month_lbl",   "Month",       "text", width=8),
            Column("ideal_hrs",   "Ideal Hrs",   "int"),
            Column("actual_hrs",  "Actual Hrs",  "int",  total=True),
            Column("output_kg",   "Output (KG)", "kg",   total=True),
            Column("ideal_output","Ideal Out",   "kg"),
            Column("avg_hr",      "Avg / Hr",    "rate"),
        ]
        mw_rows = []
        for mc_dict in s5_machines:
            if not isinstance(mc_dict, dict):
                continue
            mc_lbl  = mc_dict.get("machine", "")
            mc_type = mc_dict.get("pipe_type", "")
            for mr in (mc_dict.get("rows") or []):
                if not isinstance(mr, dict):
                    continue
                row = _row(mr, ["month_lbl","ideal_hrs","actual_hrs",
                                "output_kg","ideal_output","avg_hr"])
                row["machine"]   = mc_lbl
                row["pipe_type"] = mc_type
                mw_rows.append(row)
        sheets.append(ReportSheet(
            name="MONTHWISE",
            title=f"Pipe — Monthwise Detail — {fy_lbl}",
            subtitle="All machines × all months; one row per machine-month combination.",
            sections=[Section(mw_cols, mw_rows)],
        ))

    # ---- Per-machine tabs MC-1…MC-9 (section6: expanded month_rows per machine) ----
    import re as _re_pipe
    s6 = d.get("section6") or {}
    s6_machines = s6.get("machines") or []
    mc_month_cols = [
        Column("month_lbl",   "Month",       "text", width=10),
        Column("ideal_hrs",   "Ideal Hrs",   "int"),
        Column("actual_hrs",  "Actual Hrs",  "int",  total=True),
        Column("output_kg",   "Output (KG)", "kg",   total=True),
        Column("ideal_output","Ideal Out",   "kg"),
        Column("avg_hr",      "Avg / Hr",    "rate"),
        Column("util_pct",    "Util %",      "pct"),
        Column("eff_pct",     "Eff %",       "pct"),
    ]
    for mc_dict in s6_machines:
        if not isinstance(mc_dict, dict):
            continue
        mc_lbl  = mc_dict.get("machine", "")
        mc_type = mc_dict.get("pipe_type", "")
        _nm = _re_pipe.search(r"(\d+)", str(mc_lbl))
        _n  = _nm.group(1) if _nm else str(mc_lbl).strip()
        tab_name_mc = f"MC-{_n}"
        mr_rows = [_row(r, [c.key for c in mc_month_cols])
                   for r in (mc_dict.get("month_rows") or [])
                   if isinstance(r, dict)]
        tr = mc_dict.get("total_row") or {}
        mc_totrow = _row(tr, [c.key for c in mc_month_cols])
        mc_totrow["month_lbl"] = "TOTAL"
        sheets.append(ReportSheet(
            name=tab_name_mc[:31],
            title=f"{tab_name_mc} — {mc_type} — {fy_lbl}",
            subtitle=f"Ideal rate: {mc_dict.get('ideal_rate','')} kg/hr.",
            sections=[Section(mc_month_cols, mr_rows,
                              mc_totrow if tr else None)],
        ))

    return sheets, flags


# ============================================================================
# R3 — Moulding M/C Summary
#       builder: mgmt_moulding_summary.build_moulding_summary(fy)
# ============================================================================
def serial_moulding(ym: str) -> _SheetFlagPair:
    from mgmt_moulding_summary import build_moulding_summary
    fy = _fy_from_ym(ym)
    d = build_moulding_summary(fy, through_ym=ym)
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
        Column("actual_hrs","Actual Hrs",  "int",  total=True),
        Column("ideal_hrs", "Ideal Hrs",  "int",  total=True),
        Column("output_kg", "Output (KG)","kg",   total=True),
        Column("reject_kg", "Reject (KG)","kg",   total=True),
        Column("runner_kg", "Runner (KG)","kg"),
        Column("avg_hr",    "Avg / Hr",   "rate"),
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

    # ---- PIVOT + HOURS tabs (section4 — month rows + machine breakdown) ----
    s4 = d.get("section4") or {}
    s4_month_rows = s4.get("month_rows") or []
    s4_machines = s4.get("machines") or []
    if s4_month_rows:
        pivot_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                      _pivot_pair_columns(s4_machines))
        h_cols = ([Column("month_lbl", "Month", "text", width=10)] +
                  [Column(machine, f"{machine} Hrs", "int")
                   for machine in s4_machines])
        pivot_rows, h_rows = [], []
        pivot_total = _pivot_pair_row(
            "TOTAL", s4.get("total_cols") or {}, s4_machines,
        )
        h_total = {"month_lbl": "TOTAL"}
        for machine in s4_machines:
            h_total[machine] = pivot_cell(
                (s4.get("total_cols") or {}).get(machine), "hrs",
            )
        for mo in s4_month_rows:
            if not isinstance(mo, dict):
                continue
            cols_data = mo.get("cols") or {}
            label = mo.get("month_lbl") or mo.get("month_disp") or ""
            pivot_rows.append(_pivot_pair_row(label, cols_data, s4_machines))
            row = {"month_lbl": label}
            for machine in s4_machines:
                row[machine] = pivot_cell(cols_data.get(machine), "hrs")
            h_rows.append(row)
        sheets.append(ReportSheet(
            name="PIVOT",
            title=f"Moulding — Machine Monthly Pivot — {fy_lbl}",
            subtitle="Month × machine matrix. Each machine has Hours and Output (KG).",
            sections=[Section(pivot_cols, pivot_rows, pivot_total)],
        ))
        sheets.append(ReportSheet(
            name="HOURS",
            title=f"Moulding — Run Hours by Band & Month — {fy_lbl}",
            sections=[Section(h_cols, h_rows, h_total)],
        ))

    # ---- Per-machine tabs (section3 — MC-1 to MC-27) ----
    s3 = d.get("section3") or {}
    mc_list = s3.get("machines") or []
    mc_detail_cols = [
        Column("month_lbl",  "Month",       "text", width=10),
        Column("ideal_hrs",  "Ideal Hrs",   "int"),
        Column("actual_hrs", "Actual Hrs",  "int",  total=True),
        Column("output_kg",  "Output (KG)", "kg",   total=True),
        Column("reject_kg",  "Reject (KG)", "kg",   total=True),
        Column("runner_kg",  "Runner (KG)", "kg"),
        Column("avg_hr",     "Avg / Hr",    "rate"),
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
    d = build_gom_summary(fy, through_ym=ym)
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
    s1_cols = [
        Column("band",             "Band (Ton)",          "text", width=12),
        Column("mc_count",         "Machines",            "int"),
        Column("ideal_hrs",        "Ideal Hrs",           "int"),
        Column("actual_hrs",       "Actual Hrs",          "int", total=True),
        Column("output_kg",        "Output (KG)",         "kg",  total=True),
        Column("reject_kg",        "Reject (KG)",         "kg",  total=True),
        Column("runner_kg",        "Runner (KG)",         "kg"),
        Column("avg_hr",           "Avg / Hr",            "rate"),
        Column("avg_hr_sheet",     "Avg / Hr (Sheet)",    "rate"),
        Column("util_pct",         "Util %",              "pct"),
    ]

    def _s1_row(source: dict) -> dict:
        row = _row(source, [column.key for column in s1_cols])
        # The FY26-27 GOM page displays the weighted rate as its main TOTAL
        # value and retains the sheet's sum-of-rates as an explicit annotation.
        if source.get("avg_hr_weighted") is not None:
            row["avg_hr"] = source["avg_hr_weighted"]
            row["avg_hr_sheet"] = source.get("avg_hr_sheet")
        return row

    s1_sections = []
    for label, source_rows in [
        (s1.get("fy2627_label", "FY 26-27"), s1.get("fy2627") or []),
        (s1.get("fy2526_label", "FY 25-26"), s1.get("fy2526") or []),
    ]:
        if not source_rows:
            continue
        regular_rows = [_s1_row(row) for row in source_rows
                        if isinstance(row, dict) and not row.get("is_total")]
        total = next((row for row in source_rows
                      if isinstance(row, dict) and row.get("is_total")), None)
        s1_sections.append(Section(
            s1_cols,
            regular_rows,
            _s1_row(total) if total else None,
            heading=label,
        ))
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"GOM — Band Summary (net) — {fy_lbl}",
        subtitle="Production by tonnage band, net basis, year-on-year.",
        sections=s1_sections,
        note="" if s1_sections else "No data.",
    ))

    # ---- SUMMARY-1 tab (section2 — band × month, gross) ----
    s2 = d.get("section2") or {}
    s2_band_rows = s2.get("band_rows") or []
    months_order = []
    if s2_band_rows:
        mo_dict = s2_band_rows[0].get("months") or {}
        months_order = list(mo_dict.keys())
    s2_cols = ([Column("band",    "Band (Ton)", "text", width=12),
                Column("mc_count","Machines",   "int")] +
               _month_metric_columns(months_order) +
               [Column("_total_hrs", "Total Hrs",     "num", total=True),
                Column("_total_kg",  "Total Output (KG)", "kg",  total=True),
                Column("_avg_hr",    "Avg / Hr",      "rate")])
    s2_rows = []
    for br in s2_band_rows:
        if not isinstance(br, dict):
            continue
        row = {"band": br.get("band",""), "mc_count": br.get("mc_count")}
        tot = br.get("total") or {}
        for m in months_order:
            mc = (br.get("months") or {}).get(m) or {}
            _set_month_metrics(row, m, mc)
        row["_total_hrs"] = _v(tot.get("hrs"))
        row["_total_kg"]  = _v(tot.get("gross_kg"))
        row["_avg_hr"]    = _v(tot.get("avg_hr"))
        s2_rows.append(row)
    s2_total_r = s2.get("total_row") or {}
    s2_totrow = {"band":"TOTAL", "mc_count": None}
    for m in months_order:
        _set_month_metrics(s2_totrow, m, (s2_total_r.get("months") or {}).get(m))
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
    band_mc_cols += _month_metric_columns(months_keys)
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
                _set_month_metrics(row, m, mc_val)
            row["_total_hrs"] = _v(tot.get("hrs"))
            row["_total_kg"]  = _v(tot.get("gross_kg"))
            row["_avg_hr"]    = _v(tot.get("avg_hr"))
            mc_band_rows.append(row)
        tr = band_data.get("total_row") or {}
        tot_r = tr.get("total") if isinstance(tr, dict) else {}
        band_totrow = {"band_mc_num":"TOTAL","global_mc":"","mould_id":""}
        for m in months_keys:
            _set_month_metrics(band_totrow, m, None)
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
        Column("run_hrs",         "Run Hours",      "int",  total=True),
        Column("net_kg",          "Net KG",         "kg",   total=True),
        Column("reject_kg",       "Reject KG",      "kg",   total=True),
        Column("gross_kg",        "Gross KG",       "kg",   total=True),
        Column("rej_pct_gross",   "Rej % (gross)",  "pct"),
        Column("labour",          "Labour",         "int"),
        Column("paid_hrs",        "Paid Hrs",       "num"),
        Column("wages",           "Wages (₹)",      "cur",  total=True),
        Column("contractor_wages","Contractor (₹)", "cur",  total=True),
        Column("devoted_per_person","Devot./Person","rate"),
        Column("per_hour_cost",   "Cost / Hr (₹)", "rate"),
        Column("per_kg_cost",     "Cost / KG (₹)", "rate"),
    ]
    sum_rows = []
    sum_cell_comments: dict = {}
    for r in all_rows:
        if not isinstance(r, dict):
            continue
        row = _row(r, [c.key for c in sum_cols])
        if r.get("awaiting_wages"):
            row["wages"] = "AWAITING SOURCE DATA"
            lbl = str(r.get("month_lbl") or r.get("month_disp") or "")
            flags.append(Flag(
                rule="R-42",
                section="SUMMARY",
                month=lbl,
                our_figure="AWAITING",
                source_figure="—",
                difference="—",
                note=f"Garden wages not yet received for {lbl}.",
                cell_comment="Wages awaiting HR source sheet.",
            ))
            if lbl:
                sum_cell_comments[(lbl, "wages")] = "R-42: Wages awaiting HR source sheet."
        sum_rows.append(row)
    totrow = _row(total_row, [c.key for c in sum_cols])
    totrow["month_lbl"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"Garden M/C Summary — {fy_lbl}",
        subtitle="GARDEN plant — monthly hours, output (DR basis), wages and cost. "
                 "Net kg from the daily daily-report workbook.",
        sections=[Section(sum_cols, sum_rows, totrow if total_row else None)],
        cell_comments=sum_cell_comments,
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

    # ---- Per-machine tabs (HOURS / OUTPUT / MC-n) ----
    by_machine = sec.get("by_machine") or {}

    def _mc_short_g(name: str) -> str:
        """'GARDEN M/C - 1' → 'MC-1'"""
        tail = name.rsplit("-", 1)[-1].strip()
        return f"MC-{tail}" if tail.isdigit() else name

    sorted_mcs_g = sorted(
        by_machine.keys(),
        key=lambda n: int(n.rsplit("-", 1)[-1].strip())
            if n.rsplit("-", 1)[-1].strip().isdigit() else 0,
    )

    if sorted_mcs_g:
        _mc_month_lbls_g = [r["month_lbl"]
                            for r in (by_machine[sorted_mcs_g[0]].get("month_rows") or [])]

        # HOURS pivot — machines as rows, months as columns
        hrs_cols_g = (
            [Column("machine", "Machine", "text", width=14)] +
            [Column(lbl, lbl, "int") for lbl in _mc_month_lbls_g] +
            [Column("_total", "TOTAL", "int", total=True)]
        )
        hrs_rows_g = []
        for mc in sorted_mcs_g:
            row: dict = {"machine": _mc_short_g(mc)}
            tot = 0.0
            for r in by_machine[mc].get("month_rows") or []:
                row[r["month_lbl"]] = r.get("run_hrs")
                tot += r.get("run_hrs") or 0.0
            row["_total"] = tot or None
            hrs_rows_g.append(row)
        sheets.append(ReportSheet(
            name="HOURS",
            title=f"Garden — Run Hours by Machine — {fy_lbl}",
            subtitle="Monthly run hours per machine (from Daily Report matrix).",
            sections=[Section(hrs_cols_g, hrs_rows_g)],
        ))

        # OUTPUT pivot — machines as rows, months as columns (net KG)
        out_cols_g = (
            [Column("machine", "Machine", "text", width=14)] +
            [Column(lbl, lbl, "kg") for lbl in _mc_month_lbls_g] +
            [Column("_total", "TOTAL", "kg", total=True)]
        )
        out_rows_g = []
        for mc in sorted_mcs_g:
            row = {"machine": _mc_short_g(mc)}
            tot = 0.0
            for r in by_machine[mc].get("month_rows") or []:
                row[r["month_lbl"]] = r.get("net_kg")
                tot += r.get("net_kg") or 0.0
            row["_total"] = tot or None
            out_rows_g.append(row)
        sheets.append(ReportSheet(
            name="OUTPUT",
            title=f"Garden — Net KG Output by Machine — {fy_lbl}",
            subtitle="Monthly net output (KG) per machine (block tab basis).",
            sections=[Section(out_cols_g, out_rows_g)],
        ))

        # MC-n detail tabs — months as rows, per-machine summary columns
        mc_cols_g = [
            Column("month_lbl",    "Month",          "text", width=10),
            Column("run_hrs",      "Run Hours",      "int",  total=True),
            Column("net_kg",       "Net KG",         "kg",   total=True),
            Column("reject_kg",    "Reject KG",      "kg",   total=True),
            Column("gross_kg",     "Gross KG",       "kg",   total=True),
            Column("rej_pct_gross","Rej % (gross)",  "pct"),
        ]
        for mc in sorted_mcs_g:
            mc_data   = by_machine[mc]
            mc_short  = _mc_short_g(mc)
            tab_name  = mc_short.replace("/", "-").replace(" ", "")  # "MC-1"
            mc_drows  = [_row(r, [c.key for c in mc_cols_g])
                         for r in (mc_data.get("month_rows") or [])]
            mc_tot    = _row(mc_data.get("total_row") or {}, [c.key for c in mc_cols_g])
            mc_tot["month_lbl"] = "TOTAL"
            sheets.append(ReportSheet(
                name=tab_name,
                title=f"Garden — {mc_short} — Monthly Detail — {fy_lbl}",
                subtitle=f"Monthly output, rejection and run hours for {mc_short}.",
                sections=[Section(mc_cols_g, mc_drows,
                                  mc_tot if mc_data.get("total_row") else None)],
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
        Column("run_hrs",         "Run Hours",      "int",  total=True),
        Column("net_kg",          "Net KG",         "kg",   total=True),
        Column("reject_kg",       "Reject KG",      "kg",   total=True),
        Column("gross_kg",        "Gross KG",       "kg",   total=True),
        Column("rej_pct_gross",   "Rej % (gross)",  "pct"),
        Column("labour",          "Labour",         "int"),
        Column("paid_hrs",        "Paid Hrs",       "num"),
        Column("wages",           "Wages (₹)",      "cur",  total=True),
        Column("contractor_wages","Contractor (₹)", "cur",  total=True),
        Column("devoted_per_person","Devot./Person","rate"),
        Column("per_hour_cost",   "Cost / Hr (₹)", "rate"),
        Column("per_kg_cost",     "Cost / KG (₹)", "rate"),
    ]
    sum_rows = []
    sum_cell_comments: dict = {}
    for r in all_rows:
        if not isinstance(r, dict):
            continue
        row = _row(r, [c.key for c in sum_cols])
        if r.get("is_idle"):
            row["net_kg"] = "IDLE"
            row["gross_kg"] = "IDLE"
        if r.get("awaiting_wages"):
            row["wages"] = "AWAITING SOURCE DATA"
            lbl = str(r.get("month_lbl") or "")
            flags.append(Flag(
                rule="R-42",
                section="SUMMARY",
                month=lbl,
                our_figure="AWAITING",
                source_figure="—",
                difference="—",
                note=f"HDPE wages not yet received for {lbl}.",
                cell_comment="Wages awaiting HR source sheet.",
            ))
            if lbl:
                sum_cell_comments[(lbl, "wages")] = "R-42: Wages awaiting HR source sheet."
        if r.get("r23_differs"):
            lbl = str(r.get("month_lbl") or "")
            flags.append(Flag(
                rule="R-23",
                section="SUMMARY",
                month=lbl,
                our_figure=f"{r.get('net_kg',0):,.0f} kg",
                source_figure=f"{r.get('dr_net_kg',0):,.0f} kg (DR)",
                difference=f"{(r.get('net_kg',0) or 0) - (r.get('dr_net_kg',0) or 0):,.0f} kg",
                note="HDPE daily-report net KG differs from our computed figure.",
                cell_comment="R-23: DR net KG differs from computed.",
            ))
            if lbl:
                sum_cell_comments[(lbl, "net_kg")] = "R-23: DR net KG differs from computed — see Notes."
        sum_rows.append(row)
    totrow = _row(total_row, [c.key for c in sum_cols])
    totrow["month_lbl"] = "TOTAL"
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"HDPE M/C Summary — {fy_lbl}",
        subtitle="HDPE plant — monthly hours, output (DR basis), wages and cost.",
        sections=[Section(sum_cols, sum_rows, totrow if total_row else None)],
        cell_comments=sum_cell_comments,
    ))

    # ---- SUMMARY-1 tab — DR vs block-tab reconciliation ----
    r23_rows_h = [r for r in all_rows if r.get("r23_has_data")]
    if r23_rows_h:
        r23_cols_h = [
            Column("month_lbl",  "Month",        "text", width=10),
            Column("net_kg",     "Our Net KG",   "kg"),
            Column("dr_net_kg",  "DR Net KG",    "kg"),
            Column("gross_kg",   "Our Gross KG", "kg"),
            Column("dr_gross_kg","DR Gross KG",  "kg"),
        ]
        r23_data_h = [_row(r, [c.key for c in r23_cols_h]) for r in r23_rows_h]
        for r in r23_rows_h:
            if r.get("r23_differs"):
                flags.append(Flag(
                    rule="R-23",
                    section="SUMMARY-1",
                    month=str(r.get("month_lbl") or ""),
                    our_figure=f"{r.get('net_kg',0):,.0f} kg",
                    source_figure=f"{r.get('dr_net_kg',0):,.0f} kg (DR)",
                    difference=(
                        f"{(r.get('net_kg',0) or 0) - (r.get('dr_net_kg',0) or 0):,.0f} kg"
                    ),
                    note=(
                        "HDPE block-tab net KG differs from DR-basis net. "
                        "JUL: DR is unmaintained (0) vs our 22,448.04 kg from block tabs."
                    ),
                    cell_comment="R-23: DR basis ≠ block-tab output — see Notes.",
                ))
        sheets.append(ReportSheet(
            name="SUMMARY-1",
            title=f"HDPE — DR vs Block-Tab Reconciliation — {fy_lbl}",
            subtitle=(
                "R-23 check: Daily Report basis (what the sheet SUMMARY reads) "
                "vs our block-tab computed figures. JUL 22,448.04 kg is correct; "
                "the DR is unmaintained for HDPE."
            ),
            sections=[Section(r23_cols_h, r23_data_h)],
        ))

    # ---- Per-machine tabs (HOURS / OUTPUT / MC-n) ----
    by_machine_h = sec.get("by_machine") or {}

    def _mc_short_h(name: str) -> str:
        """'HDPE M/C - 2' → 'MC-2'"""
        tail = name.rsplit("-", 1)[-1].strip()
        return f"MC-{tail}" if tail.isdigit() else name

    sorted_mcs_h = sorted(
        by_machine_h.keys(),
        key=lambda n: int(n.rsplit("-", 1)[-1].strip())
            if n.rsplit("-", 1)[-1].strip().isdigit() else 0,
    )

    if sorted_mcs_h:
        _mc_month_lbls_h = [r["month_lbl"]
                            for r in (by_machine_h[sorted_mcs_h[0]].get("month_rows") or [])]

        # HOURS pivot
        hrs_cols_h = (
            [Column("machine", "Machine", "text", width=14)] +
            [Column(lbl, lbl, "int") for lbl in _mc_month_lbls_h] +
            [Column("_total", "TOTAL", "int", total=True)]
        )
        hrs_rows_h = []
        for mc in sorted_mcs_h:
            row: dict = {"machine": _mc_short_h(mc)}
            tot = 0.0
            for r in by_machine_h[mc].get("month_rows") or []:
                lbl = r["month_lbl"]
                rh  = None if r.get("is_idle") else r.get("run_hrs")
                row[lbl] = "IDLE" if r.get("is_idle") else rh
                tot += rh or 0.0
            row["_total"] = tot or None
            hrs_rows_h.append(row)
        sheets.append(ReportSheet(
            name="HOURS",
            title=f"HDPE — Run Hours by Machine — {fy_lbl}",
            subtitle=(
                "Monthly run hours per machine (Daily Report matrix join). "
                "APR and JUN: IDLE (machines genuinely not running)."
            ),
            sections=[Section(hrs_cols_h, hrs_rows_h)],
        ))

        # OUTPUT pivot
        out_cols_h = (
            [Column("machine", "Machine", "text", width=14)] +
            [Column(lbl, lbl, "kg") for lbl in _mc_month_lbls_h] +
            [Column("_total", "TOTAL", "kg", total=True)]
        )
        out_rows_h = []
        for mc in sorted_mcs_h:
            row = {"machine": _mc_short_h(mc)}
            tot = 0.0
            for r in by_machine_h[mc].get("month_rows") or []:
                lbl = r["month_lbl"]
                nk  = r.get("net_kg")
                row[lbl] = "IDLE" if r.get("is_idle") else nk
                tot += nk or 0.0
            row["_total"] = tot or None
            out_rows_h.append(row)
        sheets.append(ReportSheet(
            name="OUTPUT",
            title=f"HDPE — Net KG Output by Machine — {fy_lbl}",
            subtitle=(
                "Monthly net output (KG) per machine (block tab basis). "
                "APR and JUN: IDLE. JUL M/C-2: rejection tracked as n/a."
            ),
            sections=[Section(out_cols_h, out_rows_h)],
        ))

        # MC-n detail tabs
        mc_cols_h = [
            Column("month_lbl",    "Month",          "text", width=10),
            Column("run_hrs",      "Run Hours",      "int",  total=True),
            Column("net_kg",       "Net KG",         "kg",   total=True),
            Column("reject_kg",    "Reject KG",      "kg",   total=True),
            Column("gross_kg",     "Gross KG",       "kg",   total=True),
            Column("rej_pct_gross","Rej % (gross)",  "pct"),
        ]
        for mc in sorted_mcs_h:
            mc_data   = by_machine_h[mc]
            mc_short  = _mc_short_h(mc)
            tab_name  = mc_short.replace("/", "-").replace(" ", "")
            mc_drows: list = []
            for r in (mc_data.get("month_rows") or []):
                drow = _row(r, [c.key for c in mc_cols_h])
                if r.get("is_idle"):
                    drow["net_kg"] = "IDLE"
                    drow["reject_kg"] = "IDLE"
                    drow["gross_kg"] = "IDLE"
                    drow["rej_pct_gross"] = "IDLE"
                elif r.get("has_unknown_rej"):
                    drow["rej_pct_gross"] = "n/a"
                mc_drows.append(drow)
            mc_tot = _row(mc_data.get("total_row") or {}, [c.key for c in mc_cols_h])
            mc_tot["month_lbl"] = "TOTAL"
            if mc_data.get("total_row", {}).get("has_unknown_rej"):
                mc_tot["rej_pct_gross"] = "n/a"
            sheets.append(ReportSheet(
                name=tab_name,
                title=f"HDPE — {mc_short} — Monthly Detail — {fy_lbl}",
                subtitle=(
                    f"Monthly output, rejection and run hours for {mc_short}. "
                    "APR/JUN: IDLE. JUL M/C-2: rejection 'n/a' "
                    "(column present but blank — R-08)."
                ),
                sections=[Section(mc_cols_h, mc_drows,
                                  mc_tot if mc_data.get("total_row") else None)],
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

    # ---- Per-size tabs (from section_size — one tab per litre capacity) ----
    # section_size.rows has one dict per size ("500", "750", etc.) with monthly data.
    for sz_row in (ss.get("rows") or []):
        if not isinstance(sz_row, dict):
            continue
        sz_lbl = str(sz_row.get("label") or sz_row.get("item") or "")
        if not sz_lbl:
            continue
        # Builder returns bare numbers ("500", "750"); append " LTR" for tab name.
        tab_name = (f"{sz_lbl} LTR"
                    if not sz_lbl.upper().endswith("LTR") else sz_lbl)[:31]
        t_cols = ([Column("label", "Size", "text", width=18)] +
                  [Column(ym_k, disp, "num") for ym_k, disp in zip(months, months_disp)] +
                  [Column("_total", "Total / FY", "num", total=True)])
        t_row = {"label": tab_name}
        row_total = 0.0
        any_val = False
        mos = sz_row.get("months") or {}
        for ym_k in months:
            cell = mos.get(ym_k)
            if isinstance(cell, dict):
                val = (cell.get("pcs") or cell.get("prod")
                       or cell.get("ltr") or cell.get("kg"))
            elif cell is None:
                val = None
            else:
                val = cell
            t_row[ym_k] = _v(val)
            if val is not None and isinstance(val, (int, float)):
                row_total += val
                any_val = True
        t_row["_total"] = row_total if any_val else None
        sheets.append(ReportSheet(
            name=tab_name,
            title=f"{plant_lbl} — {tab_name} — {fy_lbl}",
            sections=[Section(t_cols, [t_row])],
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
        Column("hours",        "Run Hours",      "int",  total=True),
        Column("output_kg",    "Output (KG)",    "kg",   total=True),
        Column("reject_kg",    "Reject (KG)",    "kg",   total=True),
        Column("runner_kg",    "Runner (KG)",    "kg",   total=True),
        Column("reject_pct",   "Reject %",       "pct"),
        Column("runner_pct",   "Runner %",       "pct"),
        Column("moulds",       "Active Moulds",  "int"),
        Column("av_hr_per_mould","Hrs / Mould",  "rate"),
        Column("lumps_kg",     "Lumps (KG)",     "kg"),
        Column("wastage_pct",  "Wastage %",      "pct"),
        Column("grinder_kg",   "Grinder (KG)",   "kg"),
        Column("labour",       "Labour",         "int"),
        Column("paid_hours",   "Paid Hours",     "num"),
        Column("wages",        "Wages (₹)",      "cur",  total=True),
        Column("cost_per_hr",  "Cost / Hr (₹)",  "rate"),
        Column("cost_per_kg",  "Cost / KG (₹)",  "rate"),
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
    # Builder dict shape: {"col": str, "sheet": numeric (wrong TOTAL),
    #   "correct": numeric (our recomputed value), "formula": str,
    #   "sourced": bool, "note": str}
    for bug in (d.get("sheet_total_bugs") or []):
        if not isinstance(bug, dict):
            continue
        correct_val = bug.get("correct")
        sheet_val   = bug.get("sheet")
        formula     = bug.get("formula") or ""
        col_name    = bug.get("col") or ""
        sourced     = bug.get("sourced", True)
        bug_note    = bug.get("note") or ""
        try:
            _diff    = float(sheet_val) - float(correct_val)
            diff_str = (f"+{_diff:,.2f}" if _diff > 0 else f"{_diff:,.2f}")
        except Exception:
            diff_str = ""
        flags.append(Flag(
            rule="Sheet TOTAL defect",
            section="SUMMARY",
            month="FY",
            our_figure=(f"{float(correct_val):,.2f}"
                        if correct_val is not None else "—"),
            source_figure=(f"{float(sheet_val):,.2f}"
                           if sheet_val is not None else "—"),
            difference=diff_str,
            note=(
                f"Source sheet TOTAL for '{col_name}' sums the four monthly "
                f"values instead of recomputing the ratio ({formula}). "
                + (f"{bug_note} " if bug_note else "")
                + ("" if sourced else
                   "Column is blank in our output (not in Records pipeline).")
            ),
            cell_comment=f"Sheet TOTAL defect: {col_name}",
        ))

    # Add note about omitted tabs
    sheets.append(ReportSheet(
        name="Note on Omitted Tabs",
        title="PTMT — Tabs Not Produced (Pipeline Gap)",
        note=(
            "The source workbook contains four tabs not produced here: "
            "MC Utilization, Month Wise MC, GROUP WISE, and MATERIAL WISE. "
            "Unlike Garden and HDPE — where machine-grain daily records exist "
            "and per-machine tabs have now been added — PTMT has no per-machine "
            "daily production records in the current pipeline. Individual "
            "mould-to-machine assignment is not tracked at the daily record "
            "level, so these tabs cannot be derived from the same sources as "
            "the rest of the report. Only the SUMMARY tab is produced here; "
            "it matches the web-page figures."
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
        Column("total_hrs", "Total Hrs",       "int",  total=True),
        Column("ideal_hrs", "Ideal Hrs",       "int"),
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
            mo_data = (r.get("monthly") or {}).get(ym_k)
            row[f"m_{ym_k}"] = _v((mo_data or {}).get("pcs") if mo_data else None)
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

    # ---- SUMMARY tab — primary cumulative scope, optional audit baseline, recon ----
    recon = d.get("recon") or []
    baseline_block = d.get("baseline_block")
    primary_block = next(
        (
            block for block in blocks
            if isinstance(block, dict) and block.get("scope") == "primary"
        ),
        None,
    )
    scope_cols = [
        Column("material",  "Material",    "text", width=14),
        Column("n_run",     "Run Moulds",  "int",  total=True),
        Column("hrs",       "Hours",       "int",  total=True),
        Column("pcs",       "Pieces",      "int",  total=True),
        Column("kg",        "KG",          "kg",   total=True),
        Column("avg_month", "Avg / Month", "rate"),
    ]
    summary_sections = []
    if primary_block:
        summary_sections.append(Section(
            scope_cols,
            [_row(row, [c.key for c in scope_cols])
             for row in (primary_block.get("rows") or [])
             if isinstance(row, dict)],
            _row(primary_block["total_row"], [c.key for c in scope_cols])
            if primary_block.get("total_row") else None,
            heading=(
                f"PRIMARY cumulative subtotal — "
                f"{primary_block.get('period_label') or fy_lbl} "
                "(the report and download scope)"
            ),
        ))
    if isinstance(baseline_block, dict):
        summary_sections.append(Section(
            scope_cols,
            [_row(row, [c.key for c in scope_cols])
             for row in (baseline_block.get("rows") or [])
             if isinstance(row, dict)],
            _row(baseline_block["total_row"], [c.key for c in scope_cols])
            if baseline_block.get("total_row") else None,
            heading=(
                f"APR–JUL audit baseline subtotal — "
                f"{baseline_block.get('period_label')} "
                "(same latest workbook; not the download scope)"
            ),
        ))
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
        summary_sections.append(Section(
            rec_cols,
            rec_rows,
            heading=(
                f"APR–JUL anchor reconciliation — "
                f"{d.get('recon_scope_label') or 'Apr–Jul,26'} baseline only"
            ),
        ))
    sheets.append(ReportSheet(
        name="SUMMARY",
        title=f"Pipe Moulds — Reconciliation Summary — {fy_lbl}",
        subtitle=(
            "Primary cumulative figures remain the report and download scope. "
            "Apr–Jul figures, when shown separately, are an audit-only source baseline."
        ),
        sections=summary_sections,
    ))

    # ---- Per-material tabs (source workbook layout: one tab per material) ----
    pm_cols = [
        Column("month",     "Month",        "text", width=12),
        Column("material",  "Material",     "text", width=14),
        Column("n_total",   "Total Moulds", "int",  total=True),
        Column("n_run",     "Run Moulds",   "int",  total=True),
        Column("hrs",       "Hours",        "int",  total=True),
        Column("av_hr",     "Avg Hrs",      "rate"),
        Column("pcs",       "Pieces",       "int",  total=True),
        Column("kg",        "KG",           "kg",   total=True),
        Column("avg_month", "Avg / Month",  "rate"),
    ]
    fy_abbrev = f"{fy[:2]}-{fy[2:]}"   # "2627" → "26-27"

    # Collect flags and shared provenance from all blocks first
    shared_prov: list = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        period_lbl = block.get("period_label") or block.get("fy_key") or "FY"
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
        for mv in (block.get("missing") or []):
            flags.append(Flag(rule="Missing", section=period_lbl, note=str(mv)))
        for issue in (block.get("month_issues") or []):
            if not isinstance(issue, dict):
                continue
            flags.append(Flag(
                rule="Incomplete-Month",
                section=period_lbl,
                month=str(issue.get("month") or ""),
                note=str(issue.get("note") or "Incomplete cumulative month block"),
            ))
        if block.get("unavailable"):
            flags.append(Flag(
                rule="Unavailable",
                section=period_lbl,
                note="No complete source period is available for this block.",
            ))
        if not shared_prov:
            if d.get("sourcing_note"):
                shared_prov.append(d["sourcing_note"])
            if d.get("defect_note"):
                shared_prov.append(d["defect_note"])
                flags.append(Flag(rule="Sheet-Defect", section=period_lbl,
                                  note=d["defect_note"]))
            if d.get("hours_note"):
                shared_prov.append(d["hours_note"])

    # Collect materials: spec order is CPVC, UPVC, AGRI, SWR, PPR.
    # Any extra materials from the builder are appended after in source order.
    _SPEC_ORDER = ["CPVC", "UPVC", "AGRI", "SWR", "PPR"]
    seen_mats: set = set()
    all_mats_from_blocks: list = []
    for block in blocks:
        for row in (block.get("rows") or []):
            mat = row.get("material")
            if mat and mat != "TOTAL" and mat not in seen_mats:
                seen_mats.add(mat)
                all_mats_from_blocks.append(mat)
    materials_order: list = [m for m in _SPEC_ORDER if m in seen_mats] + [
        m for m in all_mats_from_blocks if m not in _SPEC_ORDER
    ]

    # One tab per material; both FY periods as sections inside
    for mat in materials_order:
        tab_name = f"{mat} Mould Summary ({fy_abbrev})"[:31]
        sections_pm: list = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            period_lbl = block.get("period_label") or block.get("fy_key") or "FY"
            fy_key = block.get("fy_key", "")
            blk_rows = block.get("rows") or []
            mat_row = next((r for r in blk_rows
                            if isinstance(r, dict) and r.get("material") == mat), None)
            if mat_row is not None:
                data_rows_pm = [
                    _row(row, [c.key for c in pm_cols])
                    for row in (block.get("month_rows") or [])
                    if isinstance(row, dict) and row.get("material") == mat
                ]
                # Older or incomplete sources may have an aggregate without
                # recognisable monthly blocks. Preserve the total transparently
                # instead of fabricating a month-level history.
                if not data_rows_pm:
                    data_rows_pm = [_row({
                        **mat_row,
                        "month": "Monthly grain unavailable",
                    }, [c.key for c in pm_cols])]
                period_total = _row({
                    **mat_row,
                    "month": f"{period_lbl} total",
                }, [c.key for c in pm_cols])
            else:
                absent = {c.key: None for c in pm_cols}
                absent["month"] = period_lbl
                absent["material"] = "⚠ unavailable"
                data_rows_pm = [absent]
                period_total = None
            heading = (
                f"{period_lbl} — monthly source figures + Apr–Jul total "
                f"(R-03: closed FY25-26 annual workbook)"
                if fy_key == "2526"
                else f"PRIMARY cumulative scope — {period_lbl}"
            )
            sections_pm.append(Section(
                pm_cols, data_rows_pm, total_row=period_total, heading=heading
            ))
            if (
                fy_key == "2627"
                and isinstance(baseline_block, dict)
                and (base_row := next(
                    (row for row in (baseline_block.get("rows") or [])
                     if isinstance(row, dict) and row.get("material") == mat),
                    None,
                )) is not None
            ):
                baseline_total = _row({
                    **base_row,
                    "month": (
                        f"{baseline_block.get('period_label')} "
                        "audit baseline subtotal"
                    ),
                }, [c.key for c in pm_cols])
                sections_pm.append(Section(
                    pm_cols,
                    [],
                    total_row=baseline_total,
                    heading=(
                        "APR–JUL audit baseline subtotal — same latest workbook; "
                        "not the primary download scope"
                    ),
                ))
        sheets.append(ReportSheet(
            name=tab_name,
            title=f"Pipe Moulds — {mat} — {fy_lbl}",
            subtitle=f"Both FY periods for {mat}. FY25-26 sourced from closed annual (R-03).",
            sections=sections_pm,
            provenance=shared_prov,
        ))

    return sheets, flags
