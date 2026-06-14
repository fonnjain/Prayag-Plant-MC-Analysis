import sheets, parsers
tok=sheets._get_access_token()
GA="1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA"
HD="1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8"
TK="1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo"
PT="1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g"

print("### PTMT 52 machines + ideal_source via _emit_daily")
res=sheets._emit_daily("PTMT","2026-06",PT,{"tab":"Report-5","layout":"matrix","ideal_col":("contains","IDEAL HOUR")},tok)
recs,rep=res
srcs={}
for r in recs: srcs[r.ideal_source]=srcs.get(r.ideal_source,0)+1
print("ideal_source counts:", srcs)
print("machines:", sorted({r.machine.replace('PTMT ','') for r in recs}))

print("\n### HDPE MACHINE tabs nonzero + DANA, layout of MACHINE with data")
for tab in ["MACHINE 1","MACHINE 2","MACHINE 3","MACHINE 4","MACHINE 5","MACHINE 6","DANA M/C"]:
    v=sheets.read_values(HD,tab,tok)
    nz=sum(1 for row in v if len(row)>7 and parsers.num(row[7])>0)
    print(f"  HDPE {tab}: rows={len(v)} nonzero-col7={nz}")

print("\n### HDPE MACHINE 1 header sample")
v=sheets.read_values(HD,"MACHINE 1",tok)
for i,row in enumerate(v[:6]):
    print(f"  r{i}:", [f"{c}:{str(x)[:12]!r}" for c,x in enumerate(row[:11]) if str(x).strip()])

print("\n### TANK tabs of interest")
for tab in ["PROD. REPORT","Daily Report","Grinding"]:
    v=sheets.read_values(TK,tab,tok)
    print(f"  --- TANK {tab}: rows={len(v)}")
    for i,row in enumerate(v[:8]):
        cells=[f"{c}:{str(x)[:12]!r}" for c,x in enumerate(row[:14]) if str(x).strip()]
        if cells: print(f"    r{i}:"," | ".join(cells))
