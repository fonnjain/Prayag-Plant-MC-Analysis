"""
Capture material stock fixtures for offline pytest.

Run from the prayag directory (NOT while Flask holds port 21800):
    cd artifacts/prayag
    python3 tests/capture_material_fixtures.py

Writes to tests/fixtures/:
    pipe_material_rm_2026_06.json     — PIPE Report-2 (RM, 42 items)
    pipe_material_bop_2026_06.json    — PIPE Report-3 (BOP, 31 items)
    pipe_material_pack_2026_06.json   — PIPE Report-4 (PACK, 15 items)
    ptmt_material_bop_2026_06.json    — PTMT Report-2 (BOP, 53 items)
    ptmt_material_pack_2026_06.json   — PTMT Report-3 (PACK, 32 items)
    ptmt_material_rm_2026_06.json     — PTMT Report-4 (RM, 21 items)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import sheets
import sources

FIXTURES = Path(__file__).parent / "fixtures"
YM = "2026-06"

TAB_MAP = {
    "PIPE": [
        ("Report-2", "pipe_material_rm_2026_06.json"),
        ("Report-3", "pipe_material_bop_2026_06.json"),
        ("Report-4", "pipe_material_pack_2026_06.json"),
    ],
    "PTMT": [
        ("Report-2", "ptmt_material_bop_2026_06.json"),
        ("Report-3", "ptmt_material_pack_2026_06.json"),
        ("Report-4", "ptmt_material_rm_2026_06.json"),
    ],
}


def main():
    token = sheets._get_access_token()
    if not token:
        print("ERROR: no Google Sheets token — connect the integration first.")
        sys.exit(1)

    for plant, tabs in TAB_MAP.items():
        fid = sources.planning_file_id(plant, YM)
        if not fid:
            print(f"SKIP {plant} — no file ID for {YM}")
            continue
        for tab_name, fname in tabs:
            try:
                vals = sheets.read_values(fid, tab_name, token)
                out = FIXTURES / fname
                out.write_text(json.dumps(vals, ensure_ascii=False, indent=2))
                print(f"OK  {plant}/{tab_name} → {fname} ({len(vals)} rows)")
            except Exception as e:
                print(f"ERR {plant}/{tab_name}: {e}")


if __name__ == "__main__":
    main()
