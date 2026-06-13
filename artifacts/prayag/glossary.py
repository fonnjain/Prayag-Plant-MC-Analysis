"""
Single source of truth for all metric definitions, formulas and rating bands.

Both the tappable (i) tooltips (rendered as JSON into the page) and the full
/glossary screen read from the structures defined here. Edit terms in ONE place.

This module is presentation metadata only — it performs no calculation.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Section A — Glossary terms
# key      : short code used in `data-term` attributes / tooltip lookup
# term     : display title (e.g. "OEE — Overall Equipment Effectiveness")
# meaning  : 1-line plain-English definition
# formula  : optional formula string ("" when not applicable)
# unit     : unit of measure
# where    : where it is used in the app
# ---------------------------------------------------------------------------
GLOSSARY = [
    {"key": "OEE", "term": "OEE — Overall Equipment Effectiveness",
     "meaning": "How effectively a machine runs vs ideal.",
     "formula": "A × P × Q", "unit": "%", "where": "All OEE views"},
    {"key": "A", "term": "A — Availability",
     "meaning": "Share of planned time the machine was actually running.",
     "formula": "Run Time ÷ Planned Production Time", "unit": "%", "where": "Machine card"},
    {"key": "P", "term": "P — Performance",
     "meaning": "Actual speed vs ideal speed.",
     "formula": "Total ÷ ((Run Time ÷ 60) × Ideal rate)", "unit": "%", "where": "Machine card"},
    {"key": "Q", "term": "Q — Quality",
     "meaning": "Share of output that was good.",
     "formula": "Good ÷ Total", "unit": "%", "where": "Machine card"},
    {"key": "ATT", "term": "Att. — Attainment",
     "meaning": "Actual output vs planned / target.",
     "formula": "Actual ÷ Planned", "unit": "%", "where": "Machine / Plant"},
    {"key": "DT", "term": "DT — Downtime",
     "meaning": "Minutes stopped during planned time.",
     "formula": "", "unit": "minutes", "where": "Losses"},
    {"key": "UNITS", "term": "units — Output",
     "meaning": "Total quantity produced.",
     "formula": "", "unit": "pcs / kg / ltr", "where": "Everywhere"},
    {"key": "PPT", "term": "PPT — Planned Production Time",
     "meaning": "Shift length minus planned stops.",
     "formula": "Shift length − Planned stops", "unit": "minutes", "where": "Availability"},
    {"key": "RUNTIME", "term": "Run Time",
     "meaning": "Time the machine was actually running.",
     "formula": "PPT − Downtime", "unit": "minutes", "where": "Availability"},
    {"key": "GOOD", "term": "Good",
     "meaning": "Good output, excluding rejects.",
     "formula": "Total − Reject", "unit": "pcs / kg", "where": "Quality"},
    {"key": "IDEAL_RATE", "term": "Ideal rate",
     "meaning": "Standard output per hour.",
     "formula": "", "unit": "units/hr", "where": "Performance"},
    {"key": "REJECT_PCT", "term": "Reject %",
     "meaning": "Share of output rejected.",
     "formula": "Reject ÷ Total", "unit": "%", "where": "All views"},
    {"key": "RUNNER_PCT", "term": "Runner %",
     "meaning": "Runner produce vs total produce.",
     "formula": "Runner ÷ Total", "unit": "kg / %", "where": "Moulding & PTMT"},
    {"key": "LUMPS", "term": "Lumps",
     "meaning": "Start-up / purge waste.",
     "formula": "", "unit": "kg", "where": "PTMT"},
    {"key": "UTIL", "term": "Utilisation",
     "meaning": "Actual hours vs ideal hours.",
     "formula": "Actual hours ÷ Ideal hours", "unit": "%", "where": "Machine / Mould"},
    {"key": "OUTPUT_EFF", "term": "Output efficiency",
     "meaning": "Actual vs ideal output (or run-hours).",
     "formula": "Actual output ÷ Ideal output", "unit": "%", "where": "Reports"},
    {"key": "MOULD_AGE_EFF", "term": "Mould age-in-efficiency",
     "meaning": "Efficiency % tracked against mould age.",
     "formula": "", "unit": "%", "where": "Mould reports"},
    {"key": "WEIGHT_LOSS", "term": "Weight-loss %",
     "meaning": "Material lost in processing.",
     "formula": "(Material in − Material out) ÷ Material in", "unit": "%", "where": "Compound"},
    {"key": "LABOUR_COST", "term": "Labour cost",
     "meaning": "Wages per kg of output, or per paid hour.",
     "formula": "Wages ÷ Output (kg)  ·  Wages ÷ Paid hours", "unit": "₹/kg · ₹/hr", "where": "Cost reports"},
    {"key": "POWER_COST", "term": "Power cost",
     "meaning": "Electricity cost for the period (total or per unit).",
     "formula": "", "unit": "₹ · ₹/unit", "where": "Cost reports"},
    {"key": "SOLAR_COST", "term": "Solar cost",
     "meaning": "Solar power cost / credit for the period.",
     "formula": "", "unit": "₹", "where": "Cost reports"},
    {"key": "HOURS", "term": "Ideal / Actual hours",
     "meaning": "Ideal hrs = planned run hours. Actual hrs = hours actually run.",
     "formula": "", "unit": "hours", "where": "Utilisation reports"},
    {"key": "FY", "term": "FY — Financial Year",
     "meaning": "Apr–Mar. YoY = year-on-year vs prior FY.",
     "formula": "", "unit": "—", "where": "Period selector"},
    {"key": "PLANT_UNIT", "term": "Plant / Unit · Segment",
     "meaning": "Plant = KH, VN, WB (or Unit-1/2/3). Segment = CP, PTMT, Sink, Hinges, Pipe & Fitting, Garden, HDPE, Tank.",
     "formula": "", "unit": "—", "where": "Filters"},
    {"key": "MC_MOULD", "term": "M/C · Mould",
     "meaning": "M/C = machine. Mould = tool with cavity / cycle / pc-weight.",
     "formula": "", "unit": "—", "where": "Reports"},
    {"key": "MACHINE_CODE", "term": "Machine code",
     "meaning": "Type-plant+number, e.g. TK-VN1 = Tank line, VN plant, machine 1.",
     "formula": "", "unit": "—", "where": "Machine"},
]

GLOSSARY_BY_KEY = {g["key"]: g for g in GLOSSARY}

# ---------------------------------------------------------------------------
# Section B — Formulas (rendered in monospace)
# ---------------------------------------------------------------------------
FORMULAS = [
    {"name": "PPT", "formula": "Shift length − Planned stops"},
    {"name": "Run Time", "formula": "PPT − Downtime"},
    {"name": "Good", "formula": "Total − Reject"},
    {"name": "A", "formula": "Run Time ÷ PPT"},
    {"name": "P", "formula": "Total ÷ ((Run Time ÷ 60) × Ideal rate)"},
    {"name": "Q", "formula": "Good ÷ Total"},
    {"name": "OEE", "formula": "A × P × Q"},
    {"name": "Att.", "formula": "Actual output ÷ Planned output"},
    {"name": "Reject %", "formula": "Reject ÷ Total"},
    {"name": "Runner %", "formula": "Runner ÷ Total"},
    {"name": "Utilisation", "formula": "Actual hours ÷ Ideal hours"},
    {"name": "Output efficiency", "formula": "Actual output ÷ Ideal output"},
    {"name": "Avg output/hour", "formula": "Output ÷ Run hours"},
    {"name": "Weight-loss %", "formula": "(Material in − Material out) ÷ Material in"},
    {"name": "Labour cost/kg", "formula": "Wages ÷ Output (kg)"},
    {"name": "Labour cost/hour", "formula": "Wages ÷ Paid hours"},
]

WORKED_EXAMPLE = "TK-VN1:  A 87.6%  ×  P 88.2%  ×  Q 97.0%  =  OEE 74.8%"
COMPUTE_NOTE = "All numbers are computed by deterministic code, never by the AI."

# ---------------------------------------------------------------------------
# Section C — OEE rating bands
# ---------------------------------------------------------------------------
RATING_BANDS = [
    {"label": "World-class", "range": "OEE ≥ 85%", "hex": "#548235"},
    {"label": "Good", "range": "60% – 85%", "hex": "#BF8F00"},
    {"label": "Needs action", "range": "OEE < 60%", "hex": "#C00000"},
]
RATING_NOTE = "The same colours apply to Attainment and other rated metrics."

# ---------------------------------------------------------------------------
# Mapping of report column header text -> glossary key (for header tooltips)
# ---------------------------------------------------------------------------
HEADER_TERM_MAP = {
    "Run Hrs": "RUNTIME",
    "Ideal Hrs": "HOURS",
    "Actual Hrs": "HOURS",
    "Output (kg)": "UNITS",
    "Output (pcs)": "UNITS",
    "Output": "UNITS",
    "Total Output": "UNITS",
    "Production (pcs)": "UNITS",
    "Reject %": "REJECT_PCT",
    "Reject (pcs)": "REJECT_PCT",
    "Total Reject": "REJECT_PCT",
    "Utilisation %": "UTIL",
    "Labour Cost/kg": "LABOUR_COST",
    "Labour Cost": "LABOUR_COST",
    "Labour/unit": "LABOUR_COST",
    "Power Cost": "POWER_COST",
    "Power/unit": "POWER_COST",
    "Solar Cost": "SOLAR_COST",
    "Runner %": "RUNNER_PCT",
    "Efficiency %": "OUTPUT_EFF",
    "Weight Loss %": "WEIGHT_LOSS",
    "OEE %": "OEE",
    "Segment": "PLANT_UNIT",
    "Mould": "MC_MOULD",
    "Machine": "MACHINE_CODE",
}
