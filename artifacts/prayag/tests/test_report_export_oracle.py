"""Offline oracle test: every management-report export still recomputes the
attached May 2026 reference totals.

The generators were hand-validated against the attached May/June 2026 reference
oracle workbooks, and the live ``/build-state`` route re-checks a handful of
totals against the real Google Sheets. But that live check needs network, so a
parser/layout/generator change could silently drift a report's numbers without
failing the offline test suite.

This test pins each report generator's key TOTAL(s) to the reference values from
``attached_assets/Prayag_May_2026_Management_Reports_1782892421291.xlsx`` (the
acceptance oracle). It is fully offline: the May daily records and the raw PIPE
Report-12 values are replayed from committed JSON fixtures
(``tests/fixtures/``), captured once from the live sheets, so a generator
regression drifts a total and fails here — no Google Sheets access required.

Fixtures:
- ``daily_2026_05.json``        — every daily Record for 2026-05 (all plants),
  feeds ``sheets.get_daily_records`` (pipe/moulding/gom/mould_eff/garden/hdpe/
  ptmt_*).
- ``pipe_report12_2026_05.json``— raw PIPE Report-12 values, feeds the
  ``gen_pipe_moulds`` item-grain read path (``sheets.read_values``).

Run: cd artifacts/prayag && python3 -m pytest tests/test_report_export_oracle.py
 or: cd artifacts/prayag && python3 -m tests.test_report_export_oracle
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
from metrics import Record
from reports import registry

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_YM = "2026-05"
_TOL = 0.005   # ±0.5%, matching the live /build-state acceptance gate.


# ---------------------------------------------------------------------------
# Reference totals — read straight off the acceptance oracle workbook
# ``Prayag_May_2026_Management_Reports_1782892421291.xlsx`` (the TOTAL / subtotal
# rows of each report sheet). A generator that drifts from these has regressed.
# ---------------------------------------------------------------------------
#
# Each report's headline OUTPUT (kg) is pinned — that is the key total the report
# exists to publish, and it recomputes exactly (this mirrors the live
# ``/build-state`` #19 gate, which also checks only the output totals). Secondary
# columns (run-hours, rejection, pcs) are pinned ONLY where they still reconcile
# with the recomputed daily figures: the oracle workbooks were frozen before
# later source backfill, so some of their run-hours / rejection cells are stale
# against today's recomputed values (garden run-hours now recompute to blank,
# hdpe rejection differs, moulding/gom/mould-eff run-hours drift ~0.6%). Pinning
# those would test the oracle's staleness, not a generator regression, so they
# are intentionally omitted.
_REF = {
    # (A) Pipe M/C Summary — TOTAL row (out / rej / run-hours all reconcile)
    "pipe":        {"out": 313_637, "rej": 30_484, "hrs": 1_832},
    # (B) Moulding M/C Summary — TOTAL row (out & rej reconcile)
    "moulding":    {"out": 75_771.1, "rej": 752.24},
    # (C) Group of Moulding — TOTAL row (ties to the (B) output)
    "gom":         {"out": 75_771.1},
    # (D) Pipe Moulds Summary — TOTAL MOULDING row (kg ties to (B); pcs)
    "pipe_moulds": {"kg": 75_771.1, "pcs": 1_163_032},
    # Moulding %age Efficiency — TOTAL / AVG row (output ties to (B))
    "mould_eff":   {"out": 75_771.1},
    # Garden Pipe Summary — TOTAL row (headline output)
    "garden":      {"out": 53_234.5},
    # HDPE Summary — TOTAL row (headline output)
    "hdpe":        {"out": 1_369.2},
    # PTMT Moulds Summary — GRAND TOTAL (excl. grinding) & injection subtotal
    "ptmt_moulds": {"grand_out": 111_992, "injection_out": 101_580.4},
    # PTMT %age Efficiency — INJECTION TOTAL row (kg & machine count)
    "ptmt_eff":    {"out": 101_580.3, "n": 48},
}


# ---------------------------------------------------------------------------
# Offline fixture wiring: replace the live sheet readers with committed data.
# ---------------------------------------------------------------------------
def _install_fixtures():
    with open(os.path.join(_FIX, "daily_2026_05.json")) as f:
        recs = [Record(**d) for d in json.load(f)]
    with open(os.path.join(_FIX, "pipe_report12_2026_05.json")) as f:
        report12 = json.load(f)

    saved = {
        "gdr": sheets.get_daily_records,
        "dfi": sheets._daily_file_id,
        "tok": sheets._get_access_token,
        "rv": sheets.read_values,
    }
    sheets.get_daily_records = lambda months, *a, **k: (
        [r for r in recs if r.period in months], [], [])
    sheets._daily_file_id = lambda plant, ym: "FIXTURE" if plant == "PIPE" else None
    sheets._get_access_token = lambda *a, **k: "FIXTURE"
    sheets.read_values = lambda fid, tab, tok, *a, **k: (
        report12 if tab == "Report-12" else [])
    return saved


def _restore(saved):
    sheets.get_daily_records = saved["gdr"]
    sheets._daily_file_id = saved["dfi"]
    sheets._get_access_token = saved["tok"]
    sheets.read_values = saved["rv"]


def _totals(rid):
    """All total/subtotal rows of a built report, keyed by their label cell."""
    model = registry.build_report(rid, _YM)
    out = []
    for sh in model.sheets:
        for sec in sh.sections:
            if sec.total_row:
                out.append(sec.total_row)
    return out


def _first_with(rows, key):
    for row in rows:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _close(actual, expected):
    return actual is not None and abs(actual - expected) / expected <= _TOL


# ---------------------------------------------------------------------------
# One test per report — each pins the generator's key TOTAL(s) to the oracle.
# ---------------------------------------------------------------------------
def _check_single_total(rid, expected):
    # Some reports render more than one sheet (e.g. Pipe adds a Type-wise sheet
    # whose total lacks the run-hours/rejection keys), so each expected key is
    # matched against the first total row that carries it.
    rows = _totals(rid)
    assert rows, f"{rid}: report built no total row"
    for key, exp in expected.items():
        got = _first_with(rows, key)
        assert _close(got, exp), (
            f"{rid} TOTAL[{key}]: expected ≈ {exp:,} (±0.5%), got {got}")


def test_pipe_export_matches_oracle():
    _check_single_total("pipe", _REF["pipe"])


def test_moulding_export_matches_oracle():
    _check_single_total("moulding", _REF["moulding"])


def test_gom_export_matches_oracle():
    _check_single_total("gom", _REF["gom"])


def test_pipe_moulds_export_matches_oracle():
    _check_single_total("pipe_moulds", _REF["pipe_moulds"])


def test_mould_eff_export_matches_oracle():
    _check_single_total("mould_eff", _REF["mould_eff"])


def test_garden_export_matches_oracle():
    _check_single_total("garden", _REF["garden"])


def test_hdpe_export_matches_oracle():
    _check_single_total("hdpe", _REF["hdpe"])


def test_ptmt_moulds_export_matches_oracle():
    # Multi-section: injection subtotal + corrugator/blow subtotal + grand total.
    rows = _totals("ptmt_moulds")
    exp = _REF["ptmt_moulds"]
    inj = next((r["out"] for r in rows
                if "Injection" in str(r.get("mc", ""))), None)
    grand = next((r["out"] for r in rows
                  if "GRAND TOTAL" in str(r.get("mc", ""))), None)
    assert _close(inj, exp["injection_out"]), (
        f"ptmt_moulds injection subtotal: expected ≈ {exp['injection_out']:,}, "
        f"got {inj}")
    assert _close(grand, exp["grand_out"]), (
        f"ptmt_moulds grand total: expected ≈ {exp['grand_out']:,}, got {grand}")


def test_ptmt_eff_export_matches_oracle():
    rows = _totals("ptmt_eff")
    exp = _REF["ptmt_eff"]
    total = rows[-1]
    assert _close(total.get("out"), exp["out"]), (
        f"ptmt_eff INJECTION TOTAL out: expected ≈ {exp['out']:,}, "
        f"got {total.get('out')}")
    assert total.get("n") == exp["n"], (
        f"ptmt_eff machine count: expected {exp['n']}, got {total.get('n')}")


# ---------------------------------------------------------------------------
# pytest fixture wiring (module-scoped install/restore) + standalone runner.
# ---------------------------------------------------------------------------
try:
    import pytest

    @pytest.fixture(autouse=True, scope="module")
    def _fixtures():
        saved = _install_fixtures()
        try:
            yield
        finally:
            _restore(saved)
except ImportError:  # pragma: no cover - pytest always present in this repo
    pass


if __name__ == "__main__":
    _saved = _install_fixtures()
    try:
        tests = [v for k, v in sorted(globals().items())
                 if k.startswith("test_") and callable(v)]
        for t in tests:
            t()
            print(f"PASS: {t.__name__}")
        print(f"\nAll {len(tests)} report-export oracle tests passed.")
    finally:
        _restore(_saved)
