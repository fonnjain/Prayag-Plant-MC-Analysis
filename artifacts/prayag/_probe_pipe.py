import sheets, sources, parsers
from collections import defaultdict

tok = sheets._get_access_token()
ym = "2026-04"

def num(x):
    try:
        return float(str(x).replace(",", "").replace("%", "").strip() or 0)
    except Exception:
        return 0.0

# ---- PIPE: Report-5 monthly summary per machine ----
fid = sources.DAILY_SOURCES["PIPE"]["files"][ym]
r5 = sheets.read_values(fid, "Report-5", tok)
print("=== PIPE Report-5 per-machine (col D,E,F,G,H,I,K) ===")
r5map = {}
for row in r5[4:]:
    mc = str(row[2]).strip() if len(row) > 2 else ""
    if not mc or mc.upper() == "TOTAL" or not mc.upper().startswith("PIPE"):
        continue
    D = num(row[3]); E = num(row[4]); F = num(row[5]); G = num(row[6]); H = num(row[7]); I = num(row[8]); K = row[10] if len(row) > 10 else ""
    r5map[mc] = (D, E, F, G, I)
    eff = (G / F / I) if (F and I) else 0.0
    util = (F / (D * E)) if (D and E) else 0.0
    print(f"  {mc:12} D={D:5.0f} E={E:4.0f} F={F:7.1f} G={G:9.0f} I={I:5.0f}  effK={K:>8}  myEff={eff:6.1%} myUtil={util:6.1%}")

# ---- PIPE: Report-11 daily long, summed per machine ----
print("\n=== PIPE Report-11 daily-summed per machine ===")
v11 = sheets.read_values(fid, "Report-11", tok)
raw = parsers.parse_daily_long(
    v11, plant="PIPE", segment="Pipe", unit="kg", year_month=ym,
    source_file=fid, source_tab="Report-11",
    machine_col=("eq", "MACHINE NO."), out_col=("eq", "WEIGHT"),
    run_col=("startswith", "RUNNING HOUR"), rej_col=("eq", "ACTUAL WT (KG)"),
)
agg = defaultdict(lambda: [0.0, 0.0, 0.0])
for r in raw:
    a = agg[r.machine]; a[0] += r.actual_hours; a[1] += r.total_count; a[2] += r.reject_count
for mc in sorted(agg):
    h, o, rej = agg[mc]
    print(f"  {mc:14} hrs={h:7.1f} out={o:9.0f} rej={rej:7.0f}")
print("  R11 machines:", sorted(agg.keys()))

# ---- GARDEN summary tabs ----
print("\n=== GARDEN tabs ===")
gfid = sources.DAILY_SOURCES["GARDEN"]["files"][ym]
gtabs = sheets.list_tabs(gfid, tok)
print(" ", gtabs)
for t in gtabs:
    if any(k in t.lower() for k in ["report-5", "summary", "m/c", "report 5"]):
        gv = sheets.read_values(gfid, t, tok)
        print(f"  --- '{t}' ({len(gv)} rows) ---")
        for i, row in enumerate(gv[:8]):
            print(f"   r{i}:", [str(c)[:14] for c in row[:14]])

# ---- MOULDING (Report-12 in PIPE workbook): does it have ideal cols? ----
print("\n=== MOULDING Report-12 header (PIPE workbook) ===")
v12 = sheets.read_values(fid, "Report-12", tok)
for i, row in enumerate(v12[:5]):
    print(f"   r{i}:", [str(c)[:14] for c in row[:16]])
