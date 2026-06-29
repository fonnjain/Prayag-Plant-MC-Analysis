"""Offline unit tests for the workbook Index parser (parsers.parse_index).

The Index tab is each PTMT / Pipe&Fitting workbook's own authoritative
description of every Report-N tab. Covers:
- header location with a leading blank column A,
- continuation (blank "Reports") rows folded into the report above as sub_blocks
  (Report-5's three machine families),
- merged Frequency cells inheriting the value from the row above,
- the live quirk where a frequency ("Every Monday") is typed into the Types
  column instead of Frequency,
- unit inference from the description ("in KG & Pcs" → kg+pcs; "Ltr" → Ltr),
- frequency_class / sliceable (only Daily reports are sliceable).

Pure / no network — fixture rows only.

Run: cd artifacts/prayag && python3 -m tests.test_index_parser
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parsers import parse_index


# Mirrors the real layout: blank column A, then
# S.No | Reports | Frequency | Types | Include | By Whom | Action Taken By
_HEADER = ["", "S.No", "Reports", "Frequency", "Types", "Include",
           "By Whom", "Action Taken By"]


def _by_key(reports):
    return {r["report_key"]: r for r in reports}


def test_pipe_report5_three_sub_blocks_and_merged_frequency():
    rows = [
        ["PIPE & FITTING — INDEX"],
        _HEADER,
        ["", "1", "Report-5", "Daily", "M/C Wise Run Hour & Production",
         "Pipe M/C", "Production", "Manager"],
        # Continuation rows: blank Reports → sub-blocks of Report-5. Frequency
        # cell is blank (merged) and must inherit "Daily" from the row above.
        ["", "",  "",         "",      "", "Mixer, Grinder, Pulverizer",
         "Maintenance", ""],
        ["", "",  "",         "",      "", "Moulding M/C", "Production", ""],
        ["", "2", "Report-11", "Daily",
         "M/C & Item Wise Actual Production in KG & Pcs", "All M/C",
         "Production", "Manager"],
    ]
    reports = parse_index(rows)
    by = _by_key(reports)
    assert "report-5" in by, by.keys()
    r5 = by["report-5"]
    # Report-5 has THREE machine families: the first ("Pipe M/C") sits on the
    # main report row's Include; the other two are continuation (blank-Reports)
    # rows folded in as sub_blocks.
    assert len(r5["sub_blocks"]) == 2, \
        f"Report-5's 2 continuation rows must fold into sub_blocks, got {r5['sub_blocks']}"
    families = [r5["include"]] + [sb["include"] for sb in r5["sub_blocks"]]
    assert "Pipe M/C" in families, families
    assert "Moulding M/C" in families and "Mixer, Grinder, Pulverizer" in families, families
    assert r5["frequency"] == "Daily" and r5["sliceable"] is True
    # Report-11 production description carries both units.
    r11 = by["report-11"]
    assert "kg" in r11["units"] and "pcs" in r11["units"], r11["units"]
    assert r11["unit"] == "kg", "kg must be the primary metric unit when both present"
    assert r11["frequency_class"] == "daily" and r11["sliceable"] is True
    print("PASS: Report-5 folds 3 sub-blocks + inherits merged Daily frequency; "
          "Report-11 desc yields kg+pcs")


def test_ptmt_production_and_wastage_with_frequency_in_types_quirk():
    rows = [
        ["PTMT — INDEX"],
        _HEADER,
        ["", "1", "Report-6", "Daily",
         "M/C & Item Wise Actual Production in KG & Pcs", "All M/C",
         "Production", "Manager"],
        # The live quirk: Report-12's frequency ("Every Monday") was typed into
        # the Types column, leaving the Frequency column blank. The parser must
        # recover it as a weekly (non-sliceable) frequency and NOT inherit the
        # "Daily" above, nor leave the frequency token polluting the description.
        ["", "2", "Report-12", "", "Every Monday", "Wastage Management",
         "Quality", "Manager"],
    ]
    reports = parse_index(rows)
    by = _by_key(reports)
    r6 = by["report-6"]
    assert "production" in r6["description"].lower(), r6["description"]
    assert r6["unit"] == "kg" and r6["sliceable"] is True

    r12 = by["report-12"]
    assert "wastage" in r12["description"].lower(), r12["description"]
    assert r12["frequency"].lower() == "every monday", r12["frequency"]
    assert r12["frequency_class"] == "weekly" and r12["sliceable"] is False, \
        "a weekly snapshot must NOT be sliceable (never summed as daily)"
    assert "every monday" not in r12["description"].lower(), \
        "the frequency token must not leak into the description"
    print("PASS: PTMT Report-6 production (kg, sliceable) + Report-12 wastage "
          "recovers weekly frequency mis-typed into Types")


def test_no_header_returns_empty_and_blank_rows_skipped():
    assert parse_index([]) == [], "empty input → []"
    assert parse_index([["just", "some", "data"], ["1", "2", "3"]]) == [], \
        "no recognisable header row → []"
    rows = [
        _HEADER,
        ["", "", "", "", "", "", "", ""],   # fully blank → skipped
        ["", "1", "Report-1", "Daily", "Attendance", "All", "HR", ""],
    ]
    reports = parse_index(rows)
    assert len(reports) == 1 and reports[0]["report"] == "Report-1", reports
    print("PASS: missing header → []; fully blank rows skipped")


if __name__ == "__main__":
    test_pipe_report5_three_sub_blocks_and_merged_frequency()
    test_ptmt_production_and_wastage_with_frequency_in_types_quirk()
    test_no_header_returns_empty_and_blank_rows_skipped()
    print("\nAll Index parser unit tests passed.")
