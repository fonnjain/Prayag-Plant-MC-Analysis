import sheets
tok = sheets._get_access_token()
print("token:", bool(tok))
files = {
  "PTMT": "1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g",
  "GARDEN": "1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA",
  "HDPE": "1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8",
  "TANK": "1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo",
}
for name, fid in files.items():
    print("="*70); print(name, fid)
    try:
        tabs = sheets.list_tabs(fid, tok)
        print("TABS:", tabs)
    except Exception as e:
        print("list_tabs ERROR:", repr(e))
