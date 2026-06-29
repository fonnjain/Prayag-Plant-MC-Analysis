"""Offline unit tests for the compound-tab parsers (parsers.py).

Covers the three new layout parsers that feed the Compound Compilation:
- parse_mixer_logbook: per-day batch/material/given/loss/pulvizer + chem matrix,
  month opening stock, and the sheet's own "given" label; TOTAL/WEEK rows and
  blank/garbage date labels are skipped; chemical vs pulvizer column split.
- parse_cg_logbook: CPVC-Fittings purchase/issue/balance day rows + opening.
- parse_compound_rollup: the in-sheet "Compound 6-10" reconciliation matrix
  (column-header -> compound key, row-label -> balance field), CPVC F vs CPVC
  longest-match precedence.

Pure / no network — fixture rows only.

Run: cd artifacts/prayag && python3 -m tests.test_compound_parsers
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_mixer_logbook, parse_cg_logbook, parse_compound_rollup


def test_mixer_logbook_basic():
    rows = [
        ["", "OP. STOCK", ""],
        ["", "1000", ""],
        # header row: Date | 1st | <chem> | <pulvizer> | Total Batch Weight | ...
        ["", "Date", "1st", "Resin K-67", "Pulvizer", "Total Batch Weight",
         "Total Material out of Mixer", "Total Compound given to Pipe Plant",
         "Total Weight Loss at Mixer", "Closing Stock"],
        # sub-row carries chem names where the header is generic
        ["", "", "", "", "", "", "", "", "", ""],
        ["", "01-04-2026", "", "300", "10", "500", "495", "480", "5", "1015"],
        ["", "02-04-2026", "", "300", "20", "500", "497", "490", "3", "1022"],
        ["", "TOTAL", "", "600", "30", "1000", "992", "970", "8", ""],
    ]
    out = parse_mixer_logbook(rows)
    assert out is not None
    assert out["opening"] == 1000.0
    assert len(out["days"]) == 2  # TOTAL row skipped
    d0 = out["days"][0]
    assert d0["batch"] == 500.0
    assert d0["material"] == 495.0
    assert d0["given"] == 480.0
    assert d0["loss"] == 5.0
    assert d0["pulvizer"] == 10.0
    assert d0["chems"].get("Resin K-67") == 300.0
    assert out["total_chems"].get("Resin K-67") == 600.0
    assert "Resin K-67" in out["chem_names"]
    assert "Pulvizer" not in out["chem_names"]
    assert "Pipe Plant" in out["given_label"]
    print("ok test_mixer_logbook_basic")


def test_mixer_logbook_no_header():
    assert parse_mixer_logbook([["foo", "bar"], ["baz"]]) is None
    assert parse_mixer_logbook([]) is None
    print("ok test_mixer_logbook_no_header")


def test_cg_logbook_basic():
    rows = [
        ["", "Date", "Op. Stock", "Purchase", "Issue", "Balance"],
        ["", "", "9000", "", "", ""],
        ["", "01-04-2026", "", "5000", "4000", "10000"],
        ["", "02-04-2026", "", "1000", "800", "10200"],
        ["", "TOTAL", "", "6000", "4800", ""],
    ]
    out = parse_cg_logbook(rows)
    assert out is not None
    assert out["opening"] == 9000.0
    assert len(out["days"]) == 2
    assert out["days"][0]["purchase"] == 5000.0
    assert out["days"][0]["issue"] == 4000.0
    assert out["days"][1]["balance"] == 10200.0
    print("ok test_cg_logbook_basic")


def test_compound_rollup_keys_and_precedence():
    rows = [
        # header: blank | S.No-ish | CPVC | UPVC | AGRI | SWR | CPVC F
        ["", "Types", "CPVC", "UPVC", "AGRI", "SWR", "CPVC F"],
        ["", "Opening Stock", "1999", "1259", "1000", "2009", "9950"],
        ["", "Total Batch Weight", "78762", "128754", "55666", "139910", "0"],
        ["", "Total Compound given", "79400", "127500", "55375", "140000", "15750"],
        ["", "noise row", "1", "2", "3", "4", "5"],
    ]
    out = parse_compound_rollup(rows)
    assert out["CPVC"]["opening"] == 1999.0
    assert out["CPVC"]["batch"] == 78762.0
    assert out["CPVC"]["given"] == 79400.0
    # "CPVC F" must map to CPVC_F, not be swallowed by the "CPVC" token.
    assert out["CPVC_F"]["opening"] == 9950.0
    assert out["CPVC_F"]["given"] == 15750.0
    assert "CPVC_F" in out and out["CPVC_F"] != out["CPVC"]
    print("ok test_compound_rollup_keys_and_precedence")


def test_compound_rollup_no_header():
    assert parse_compound_rollup([["a", "b"], ["c", "d"]]) == {}
    assert parse_compound_rollup([]) == {}
    print("ok test_compound_rollup_no_header")


if __name__ == "__main__":
    test_mixer_logbook_basic()
    test_mixer_logbook_no_header()
    test_cg_logbook_basic()
    test_compound_rollup_keys_and_precedence()
    test_compound_rollup_no_header()
    print("all compound parser tests passed")
