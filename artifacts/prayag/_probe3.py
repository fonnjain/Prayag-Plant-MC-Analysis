import sheets, parsers
tok = sheets._get_access_token()
YM="2026-06"
PLANTS={"PTMT":"1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g",
        "GARDEN":"1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA",
        "HDPE":"1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8",
        "TANK":"1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo"}
print("### Current _load_daily output per plant:")
for p,fid in PLANTS.items():
    try:
        res = sheets._load_daily(p, YM, tok)
        for recs, rep in res:
            outs=sum(r.total_count for r in recs); hrs=sum(r.actual_hours for r in recs)
            machs=sorted({r.machine for r in recs})
            print(f"{p:7} emit={rep['plant']:9} recs={len(recs):4} machines={len(machs):3} out={outs:.1f} hrs={hrs:.1f} warn={rep.get('warning')}")
            if machs[:3]: print("        sample:", machs[:4])
    except Exception as e:
        print(p, "ERR", repr(e))

print("\n### Garden 'Daily Report' full TOTAL row + any nonzero machine cell:")
v=sheets.read_values(PLANTS["GARDEN"],"Daily Report",tok)
for i,row in enumerate(v):
    nz=[(c,x) for c,x in enumerate(row) if parsers.num(x)!=0]
    if nz: print(f"  r{i} nonzero:", nz[:12])

print("\n### Garden MACHINE tabs: count nonzero TOTAL(KG) rows")
for tab in ["MACHINE 1","MACHINE 2","MACHINE 3","MACHINE 4"]:
    v=sheets.read_values(PLANTS["GARDEN"],tab,tok)
    nz=0; samp=[]
    for row in v:
        # col6=KG col7=TOTAL(KG) per header; date col0
        kg = parsers.num(row[7]) if len(row)>7 else 0
        if kg>0:
            nz+=1
            if len(samp)<3: samp.append((str(row[0])[:12], kg))
    print(f"  {tab}: nonzero-KG rows={nz} sample={samp}")
