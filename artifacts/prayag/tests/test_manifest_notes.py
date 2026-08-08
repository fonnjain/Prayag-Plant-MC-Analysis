"""Integration test: parser-emitted notes (e.g. "rejection column not found")
must survive through build_manifest — flagged in schema_flags, carried on the
fetched report (rendered by templates/manifest.html), and included in the
compact advisory summary.

Pure / no network — fixture report dicts only.

Run: cd artifacts/prayag && python3 -m tests.test_manifest_notes
"""
import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import manifest as manifest_mod

NOTE = ("MOULDING 2026-07: rejection column matching 'ACTUAL REJECTION' was "
        "not found in tab 'Report-12' — rejection is being read as 0 and may "
        "be understated (sheet layout may have changed).")


def _report(notes=None):
    return {
        "family": "pipe", "title": "Plumbing Moulding — daily (2026-07)",
        "file_id": "F1", "tab": "Report-12", "detail_tabs": [],
        "grain": "daily", "months_available": ["2026-07"],
        "record_count": 3, "segment": "MOULDING", "plant": "MOULDING",
        "reconcile": None, "warning": None,
        "columns_seen": ["DATE", "Moulding Machine", "Wt in Kgs", "Pc"],
        **({"notes": notes} if notes is not None else {}),
    }


def test_parser_note_surfaces_in_manifest():
    man = manifest_mod.build_manifest(
        ["2026-07"], [], [_report(notes=[NOTE])],
        as_of=datetime.date(2026, 7, 31),
    )
    # 1. Flagged in schema_flags (severity=warning, type=parser_note)
    pn = [f for f in man["schema_flags"] if f["type"] == "parser_note"]
    assert len(pn) == 1, f"expected 1 parser_note flag, got {man['schema_flags']}"
    assert pn[0]["issue"] == NOTE and pn[0]["severity"] == "warning", pn
    # 2. Carried on the fetched report (rendered as rep.notes in manifest.html)
    fetched = [r for r in man["fetched"] if r["file_id"] == "F1"]
    assert fetched and fetched[0].get("notes") == [NOTE], fetched
    # 3. Included in the compact advisory summary
    summary = manifest_mod.manifest_summary(man)
    srep = [r for r in summary["fetched"] if r["file_id"] == "F1"]
    assert srep and srep[0]["notes"] == [NOTE], srep
    print("PASS: parser note survives build_manifest → schema flag, fetched "
          "report and advisory summary")


def test_no_note_no_flag():
    man = manifest_mod.build_manifest(
        ["2026-07"], [], [_report()],
        as_of=datetime.date(2026, 7, 31),
    )
    assert not [f for f in man["schema_flags"] if f["type"] == "parser_note"], \
        man["schema_flags"]
    summary = manifest_mod.manifest_summary(man)
    srep = [r for r in summary["fetched"] if r["file_id"] == "F1"]
    assert srep and srep[0]["notes"] == [], srep
    print("PASS: reports without notes produce no parser_note flag")


if __name__ == "__main__":
    test_parser_note_surfaces_in_manifest()
    test_no_note_no_flag()
    print("\nAll manifest note tests passed.")
