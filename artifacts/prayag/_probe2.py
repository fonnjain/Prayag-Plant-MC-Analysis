import sheets, parsers
tok = sheets._get_access_token()
def dump(name, fid, tab, nrows=14, ncols=18):
    print("="*80); print(f"{name} :: {tab}")
    try:
        v = sheets.read_values(fid, tab, tok)
    except Exception as e:
        print("read ERROR", repr(e)); return None
    print("rows:", len(v))
    for i,row in enumerate(v[:nrows]):
        cells = [f"{c}:{str(x)[:14]!r}" for c,x in enumerate(row[:ncols]) if str(x).strip()!=""]
        print(f"r{i}:", " | ".join(cells))
    return v

PT="1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g"
GA="1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA"
HD="1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8"
TK="1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo"

vpt=dump("PTMT", PT, "Report-5")
vga=dump("GARDEN", GA, "Daily Report")
vgm=dump("GARDEN", GA, "MACHINE 1", nrows=18)
