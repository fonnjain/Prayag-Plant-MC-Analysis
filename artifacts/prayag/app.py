"""
Prayag Production Analytics — Flask application entry point.
All arithmetic is deterministic Python. Claude is used only for narrative prose.
"""
from __future__ import annotations
import os
import datetime
import json
from functools import lru_cache
from flask import Flask, render_template, request, jsonify, Response, abort

from sheets import read_sheet, rows_to_shift_rows, is_demo_mode, _PLANTS, _PLANT_NAMES, _MACHINES
from metrics import (
    compute_metrics, rollup_by_plant, rollup_by_machine, rollup_by_mould,
    rollup_by_segment, rollup_by_date, downtime_pareto,
)
from validate import full_validate
from narrative import get_narrative
from pdf_export import generate_report_pdf
from glossary import (
    GLOSSARY, GLOSSARY_BY_KEY, FORMULAS, RATING_BANDS, RATING_NOTE,
    WORKED_EXAMPLE, COMPUTE_NOTE, HEADER_TERM_MAP,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "prayag-analytics-dev")

# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def _today() -> datetime.date:
    return datetime.date.today()


def parse_period(args) -> tuple[str, str, str]:
    """
    Returns (from_iso, to_iso, label_ddmmyyyy).
    """
    period = args.get("period", "yesterday")
    today = _today()
    yesterday = today - datetime.timedelta(days=1)

    if period == "yesterday":
        f = t = yesterday
        label = f"Yesterday ({_fmt(yesterday)})"
    elif period == "last_week":
        t = yesterday
        f = t - datetime.timedelta(days=6)
        label = f"Last 7 days: {_fmt(f)} to {_fmt(t)}"
    elif period == "last_month":
        t = yesterday
        f = t - datetime.timedelta(days=29)
        label = f"Last 30 days: {_fmt(f)} to {_fmt(t)}"
    elif period == "current_fy":
        year = today.year if today.month >= 4 else today.year - 1
        f = datetime.date(year, 4, 1)
        t = datetime.date(year + 1, 3, 31)
        label = f"FY {year}-{str(year+1)[2:]} ({_fmt(f)} to {_fmt(t)})"
    elif period == "prior_fy":
        year = (today.year if today.month >= 4 else today.year - 1) - 1
        f = datetime.date(year, 4, 1)
        t = datetime.date(year + 1, 3, 31)
        label = f"FY {year}-{str(year+1)[2:]} ({_fmt(f)} to {_fmt(t)})"
    elif period == "custom":
        try:
            f = datetime.date.fromisoformat(args.get("from_date", str(yesterday)))
            t = datetime.date.fromisoformat(args.get("to_date", str(yesterday)))
        except ValueError:
            f = t = yesterday
        label = f"Custom: {_fmt(f)} to {_fmt(t)}"
    elif period in [str(m) for m in range(1, 13)]:
        m = int(period)
        year = today.year
        f = datetime.date(year, m, 1)
        last_day = (datetime.date(year, m % 12 + 1, 1) - datetime.timedelta(days=1)) if m < 12 else datetime.date(year, 12, 31)
        t = last_day
        label = f"{datetime.date(year, m, 1).strftime('%B %Y')}: {_fmt(f)} to {_fmt(t)}"
    else:
        f = t = yesterday
        label = f"Yesterday ({_fmt(yesterday)})"

    # Never go into the future
    if t > today:
        t = today
    if f > t:
        f = t

    return f.isoformat(), t.isoformat(), label


def _fmt(d: datetime.date) -> str:
    return d.strftime("%d-%m-%Y")


def _period_key(from_iso: str, to_iso: str, plant: str, segment: str, machine: str) -> str:
    return f"{from_iso}_{to_iso}_{plant}_{segment}_{machine}"


# ---------------------------------------------------------------------------
# Data pipeline (read → clean → filter → compute → validate)
# ---------------------------------------------------------------------------

def get_data(args):
    from_iso, to_iso, period_label = parse_period(args)

    plant_filter = args.get("plant", "")
    segment_filter = args.get("segment", "")
    machine_filter = args.get("machine", "")

    raw = read_sheet("Shift Log", from_iso, to_iso)
    all_rows = rows_to_shift_rows(raw)

    # Apply filters
    rows = all_rows
    if plant_filter:
        rows = [r for r in rows if r.plant == plant_filter]
    if segment_filter:
        rows = [r for r in rows if r.segment == segment_filter]
    if machine_filter:
        rows = [r for r in rows if r.machine == machine_filter]

    overall = compute_metrics(rows)
    validation = full_validate(rows, overall)

    return {
        "rows": rows,
        "all_rows": all_rows,
        "overall": overall,
        "validation": validation,
        "from_iso": from_iso,
        "to_iso": to_iso,
        "period_label": period_label,
        "period": args.get("period", "yesterday"),
        "plant_filter": plant_filter,
        "segment_filter": segment_filter,
        "machine_filter": machine_filter,
        "demo_mode": is_demo_mode(),
        "has_claude": bool(os.environ.get("ANTHROPIC_API_KEY", "")),
    }


@app.context_processor
def inject_glossary():
    """Make the single-source glossary available to every template."""
    return {
        "glossary": GLOSSARY,
        "glossary_data": GLOSSARY_BY_KEY,
        "formulas": FORMULAS,
        "rating_bands": RATING_BANDS,
        "rating_note": RATING_NOTE,
        "worked_example": WORKED_EXAMPLE,
        "compute_note": COMPUTE_NOTE,
        "header_term_map": HEADER_TERM_MAP,
    }


def _common_ctx(data: dict) -> dict:
    """Build template context that every page needs."""
    opt_rows = data.get("all_rows", data["rows"])
    plants = sorted(set(r.plant for r in opt_rows)) or _PLANTS
    segments = sorted(set(r.segment for r in opt_rows))
    machines = sorted(set(r.machine for r in opt_rows))
    return {
        **data,
        "plants": plants,
        "plant_names": _PLANT_NAMES,
        "segments": segments,
        "machines": machines,
        "overall_dict": data["overall"].to_dict(),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def overview():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    # Daily OEE trend
    by_date = rollup_by_date(data["rows"])
    daily_labels = sorted(by_date.keys())
    daily_oee = [round(by_date[d].oee * 100, 1) for d in daily_labels]
    daily_labels_fmt = [datetime.date.fromisoformat(d).strftime("%d-%m") for d in daily_labels]

    # Plant overview
    by_plant = rollup_by_plant(data["rows"])
    plant_labels = sorted(by_plant.keys())
    plant_oee = [round(by_plant[p].oee * 100, 1) for p in plant_labels]
    plant_output = [round(by_plant[p].total_count, 0) for p in plant_labels]

    # Narrative
    narrative = None
    if ctx["has_claude"] and data["rows"]:
        od = ctx["overall_dict"]
        narrative = get_narrative(
            view="Overview",
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", "", ""),
            metrics_summary={
                "OEE": f"{od['oee']}%",
                "Availability": f"{od['availability']}%",
                "Performance": f"{od['performance']}%",
                "Quality": f"{od['quality']}%",
                "Total Output": od['total_count'],
                "Rejection %": f"{od['rejection_pct']}%",
                "Plan Attainment": f"{od['attainment']}%",
            },
        )

    ctx.update({
        "daily_labels": json.dumps(daily_labels_fmt),
        "daily_oee": json.dumps(daily_oee),
        "plant_labels": json.dumps(plant_labels),
        "plant_oee": json.dumps(plant_oee),
        "plant_output": json.dumps(plant_output),
        "by_plant": {p: by_plant[p].to_dict() for p in plant_labels},
        "narrative": narrative,
    })
    return render_template("overview.html", **ctx)


@app.route("/plant")
def plant_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    by_plant = rollup_by_plant(data["rows"])
    plant_items = []
    for p in sorted(by_plant.keys()):
        m = by_plant[p]
        plant_items.append({
            "plant": p,
            "name": _PLANT_NAMES.get(p, p),
            "metrics": m.to_dict(),
        })

    plant_labels = [item["plant"] for item in plant_items]
    plant_oee = [item["metrics"]["oee"] for item in plant_items]
    plant_output = [item["metrics"]["total_count"] for item in plant_items]
    plant_attainment = [item["metrics"]["attainment"] for item in plant_items]

    ctx.update({
        "plant_items": plant_items,
        "plant_labels": json.dumps(plant_labels),
        "plant_oee": json.dumps(plant_oee),
        "plant_output": json.dumps(plant_output),
        "plant_attainment": json.dumps(plant_attainment),
    })
    return render_template("plant.html", **ctx)


@app.route("/machine")
def machine_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    by_machine = rollup_by_machine(data["rows"])
    machine_items = sorted(
        [{"machine": k, "metrics": v.to_dict()} for k, v in by_machine.items()],
        key=lambda x: x["metrics"]["oee"],
        reverse=True,
    )

    machine_labels = [x["machine"] for x in machine_items]
    machine_oee = [x["metrics"]["oee"] for x in machine_items]

    ctx.update({
        "machine_items": machine_items,
        "machine_labels": json.dumps(machine_labels),
        "machine_oee": json.dumps(machine_oee),
    })
    return render_template("machine.html", **ctx)


@app.route("/losses")
def losses():
    data = get_data(request.args)
    ctx = _common_ctx(data)

    pareto = downtime_pareto(data["rows"])
    labels = [p["reason"] or "Unknown" for p in pareto]
    minutes = [p["minutes"] for p in pareto]
    cum_pct = [p["cumulative_pct"] for p in pareto]

    ctx.update({
        "pareto": pareto,
        "pareto_labels": json.dumps(labels),
        "pareto_minutes": json.dumps(minutes),
        "pareto_cum_pct": json.dumps(cum_pct),
        "top3": pareto[:3],
        "total_downtime": sum(minutes),
    })
    return render_template("losses.html", **ctx)


@app.route("/glossary")
def glossary_view():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    return render_template("glossary.html", **ctx)


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

REPORT_TYPES = [
    {"id": "extrusion_summary", "title": "Extrusion M/C Summary", "desc": "Pipe / Garden / HDPE: run hours, output kg, rejection %, utilisation %, labour cost/kg", "segments": ["Pipe", "Garden Pipe", "HDPE"]},
    {"id": "injection_summary", "title": "Injection Moulding M/C Summary", "desc": "Ideal vs actual hours, output, rejection, runner, utilisation %", "segments": ["PTMT", "CP"]},
    {"id": "mould_summary", "title": "Mould-wise Summary", "desc": "Per-mould output, run hours, runner %, rejection %, utilisation %", "segments": []},
    {"id": "mould_efficiency", "title": "Mould Age-in-Efficiency", "desc": "Per mould production pcs, ideal vs actual hours, efficiency %", "segments": []},
    {"id": "tank_summary", "title": "Tank Litre Summary", "desc": "Production & rejection by capacity (200–5000L) × layer, litres & pieces", "segments": ["Tanks"]},
    {"id": "compound_summary", "title": "Compound / Material Compilation", "desc": "Batch weight, mixer output, weight-loss %, by compound type", "segments": []},
    {"id": "segment_cost", "title": "Segment-wise Cost", "desc": "Labour/Power/Solar: headcount, paid hours, wages, per-kg & per-hour cost", "segments": []},
    {"id": "utilisation", "title": "Utilisation (Machine & Mould)", "desc": "Actual vs ideal hours, utilisation %, 3-month utilisation trend", "segments": []},
]


@app.route("/reports")
def reports():
    data = get_data(request.args)
    ctx = _common_ctx(data)
    ctx["report_types"] = REPORT_TYPES
    return render_template("reports.html", **ctx)


@app.route("/reports/<report_id>")
def report_detail(report_id: str):
    rpt = next((r for r in REPORT_TYPES if r["id"] == report_id), None)
    if not rpt:
        abort(404)

    data = get_data(request.args)
    ctx = _common_ctx(data)

    rows = data["rows"]
    if rpt["segments"]:
        rows = [r for r in rows if r.segment in rpt["segments"]]

    # Build table depending on report type
    headers, table_rows, chart_labels, chart_values, chart_label = _build_report_table(report_id, rows, data)

    # Aggregate for this sub-set
    sub_overall = compute_metrics(rows)
    sub_validation = full_validate(rows, sub_overall)
    sub_dict = sub_overall.to_dict()

    narrative = None
    if ctx["has_claude"] and rows:
        narrative = get_narrative(
            view=rpt["title"],
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", rpt["id"], ""),
            metrics_summary={k: v for k, v in sub_dict.items() if isinstance(v, (int, float))},
            extra_context=f"Report type: {rpt['title']}",
        )

    ctx.update({
        "report": rpt,
        "headers": headers,
        "table_rows": table_rows,
        "chart_labels": json.dumps(chart_labels),
        "chart_values": json.dumps(chart_values),
        "chart_label": chart_label,
        "sub_overall": sub_dict,
        "sub_validation": sub_validation,
        "narrative": narrative,
    })
    return render_template("report_detail.html", **ctx)


def _build_report_table(report_id: str, rows, data: dict):
    from metrics import rollup_by_machine, rollup_by_mould, rollup_by_segment

    if report_id == "extrusion_summary":
        by_machine = rollup_by_machine(rows)
        headers = ["Machine", "Run Hrs", "Output (kg)", "Reject %", "Utilisation %", "Labour Cost/kg"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            lc_per_kg = round(m.labour_cost / m.total_count, 2) if m.total_count > 0 else 0
            table_rows.append([mc, f"{m.run_time/60:.1f}", f"{m.total_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%", f"{m.utilisation_pct:.1f}%",
                                f"₹{lc_per_kg:.2f}"])
            chart_labels.append(mc)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output (kg)"

    elif report_id == "injection_summary":
        by_machine = rollup_by_machine(rows)
        headers = ["Machine", "Ideal Hrs", "Actual Hrs", "Output (pcs)", "Reject %", "Runner %", "Utilisation %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            table_rows.append([mc, f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}", f"{m.total_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%", f"{round(m.runner_pct*100,2):.2f}%",
                                f"{m.utilisation_pct:.1f}%"])
            chart_labels.append(mc)
            chart_values.append(m.oee_pct)
        return headers, table_rows, chart_labels, chart_values, "OEE %"

    elif report_id == "mould_summary":
        by_mould = rollup_by_mould(rows)
        headers = ["Mould", "Output", "Run Hrs", "Runner %", "Reject %", "Utilisation %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{m.run_time/60:.1f}",
                                f"{round(m.runner_pct*100,2):.2f}%", f"{m.rejection_pct_display:.2f}%",
                                f"{m.utilisation_pct:.1f}%"])
            chart_labels.append(mould)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output"

    elif report_id == "mould_efficiency":
        by_mould = rollup_by_mould(rows)
        headers = ["Mould", "Output (pcs)", "Ideal Hrs", "Actual Hrs", "Efficiency %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            eff = round(m.utilisation_pct, 1)
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}", f"{eff}%"])
            chart_labels.append(mould)
            chart_values.append(eff)
        return headers, table_rows, chart_labels, chart_values, "Efficiency %"

    elif report_id == "tank_summary":
        by_mould = rollup_by_mould(rows)
        headers = ["Tank Size", "Production (pcs)", "Reject (pcs)", "Reject %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mould, m in sorted(by_mould.items()):
            if not mould:
                continue
            table_rows.append([mould, f"{m.total_count:,.0f}", f"{m.reject_count:,.0f}",
                                f"{m.rejection_pct_display:.2f}%"])
            chart_labels.append(mould)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Production (pcs)"

    elif report_id == "compound_summary":
        by_compound = {}
        for r in rows:
            ct = r.compound_type or "Other"
            by_compound.setdefault(ct, []).append(r)
        headers = ["Compound", "Total Output", "Total Reject", "Weight Loss %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for ct, ct_rows in sorted(by_compound.items()):
            m = compute_metrics(ct_rows)
            wl = round((1 - m.quality) * 100, 2)
            table_rows.append([ct, f"{m.total_count:,.0f}", f"{m.reject_count:,.0f}", f"{wl:.2f}%"])
            chart_labels.append(ct)
            chart_values.append(round(m.total_count, 0))
        return headers, table_rows, chart_labels, chart_values, "Output"

    elif report_id == "segment_cost":
        by_seg = rollup_by_segment(rows)
        headers = ["Segment", "Output", "Labour Cost", "Power Cost", "Solar Cost", "Labour/unit", "Power/unit"]
        table_rows = []
        chart_labels, chart_values = [], []
        for seg, m in sorted(by_seg.items()):
            lpu = round(m.labour_cost / m.total_count, 2) if m.total_count > 0 else 0
            ppu = round(m.power_cost / m.total_count, 2) if m.total_count > 0 else 0
            table_rows.append([seg, f"{m.total_count:,.0f}", f"₹{m.labour_cost:,.0f}",
                                f"₹{m.power_cost:,.0f}", f"₹{m.solar_cost:,.0f}",
                                f"₹{lpu:.2f}", f"₹{ppu:.2f}"])
            chart_labels.append(seg)
            chart_values.append(round(m.labour_cost, 0))
        return headers, table_rows, chart_labels, chart_values, "Labour Cost (₹)"

    elif report_id == "utilisation":
        by_machine = rollup_by_machine(rows)
        headers = ["Machine", "Ideal Hrs", "Actual Hrs", "Utilisation %", "OEE %"]
        table_rows = []
        chart_labels, chart_values = [], []
        for mc, m in sorted(by_machine.items()):
            ideal_hrs = m.shift_len_min / 60
            actual_hrs = m.run_time / 60
            table_rows.append([mc, f"{ideal_hrs:.1f}", f"{actual_hrs:.1f}",
                                f"{m.utilisation_pct:.1f}%", f"{m.oee_pct:.1f}%"])
            chart_labels.append(mc)
            chart_values.append(m.utilisation_pct)
        return headers, table_rows, chart_labels, chart_values, "Utilisation %"

    return [], [], [], [], ""


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

@app.route("/export-pdf/<report_id>")
def export_pdf(report_id: str):
    rpt = next((r for r in REPORT_TYPES if r["id"] == report_id), None)
    if not rpt:
        abort(404)

    data = get_data(request.args)
    rows = data["rows"]
    if rpt["segments"]:
        rows = [r for r in rows if r.segment in rpt["segments"]]

    headers, table_rows, _, _, _ = _build_report_table(report_id, rows, data)
    sub_overall = compute_metrics(rows)
    sub_validation = full_validate(rows, sub_overall)

    narrative = None
    if data.get("has_claude") and rows:
        sub_dict = sub_overall.to_dict()
        narrative = get_narrative(
            view=rpt["title"],
            period_label=data["period_label"],
            period_key=_period_key(data["from_iso"], data["to_iso"], "", report_id, ""),
            metrics_summary={k: v for k, v in sub_dict.items() if isinstance(v, (int, float))},
        )

    pdf_bytes = generate_report_pdf(
        title=rpt["title"],
        period_label=data["period_label"],
        overall=sub_overall.to_dict(),
        table_rows=table_rows,
        table_headers=headers,
        narrative=narrative,
        validation_status=sub_validation,
    )

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=prayag_{report_id}_{data['from_iso']}.pdf"},
    )


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({"status": "ok", "demo": is_demo_mode()})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
