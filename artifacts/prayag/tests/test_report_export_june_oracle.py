"""Offline oracle test: the management-report exports still recompute the
attached **June 2026** reference totals — a second, structurally-different month.

This is the June counterpart of ``test_report_export_oracle.py`` (May). June is
worth pinning on its own because its workbook set has a *different shape*: the
PTMT reports ship as a **separate** workbook and there is a **standalone (D)
Pipe Moulds Summary** workbook, so a June-specific parser/layout/generator drift
could slip through offline even when May still passes.

It is fully offline: the June daily records and the raw PIPE Report-12 values
are replayed from committed JSON fixtures (``tests/fixtures/``), captured once
from the live sheets, so a generator regression drifts a total and fails here —
no Google Sheets access required.

Fixtures:
- ``daily_2026_06.json``        — every daily Record for 2026-06 (all plants),
  feeds ``sheets.get_daily_records`` (pipe/moulding/gom/mould_eff/garden/ptmt_*).
- ``pipe_report12_2026_06.json``— raw PIPE Report-12 values, feeds the
  ``gen_pipe_moulds`` item-grain read path (``sheets.read_values``).

Oracle workbooks (``attached_assets/``):
- ``Prayag_June_2026_Management_Reports_1782892467438.xlsx``
- ``Prayag_June_2026_PTMT_Reports_1782892467438.xlsx``
- ``Prayag_June_2026_D_Pipe_Moulds_Summary_1782873729074.xlsx``

Oracle-verified vs snapshot-pinned
----------------------------------
Unlike the May oracle, the June reference workbooks were frozen **mid-month**
(their Overview sheet says June was still being entered — Report-11 covered only
5 of 11 production dates), and the live Kaharani source has since been backfilled
with more production days. So the June oracle OUTPUT cells for the Kaharani
daily-driven reports are STALE relative to today's recomputed figures — pinning
them would test the oracle's staleness, not a generator regression (the exact
principle in ``.agents/memory/prayag-report-export-oracle.md``, here reaching the
output column because June, unlike May, was never re-baselined). Each pinned
value below is therefore tagged:

- ORACLE-VERIFIED — the committed June fixture reconciles with the June oracle
  within ±0.5% (the live ``/build-state`` acceptance tolerance): ``garden``,
  ``ptmt_moulds``, ``ptmt_eff``, and — for ``pipe`` — the oracle's own documented
  "complete Report-5 daily" grand total (168,738 kg on the Type-wise sheet; the
  A-summary cell 170,216 is the stale R5/R11 date-wise max).
- SNAPSHOT-PINNED — the June oracle output has drifted past ±0.5% due to the
  mid-month freeze, so the value is pinned to the recomputed figure from the
  committed June fixture. This still guards the generator against parser / layout
  / generator regressions on a structurally-different second month: ``moulding``,
  ``gom``, ``mould_eff``, ``pipe_moulds``.

``hdpe`` has NO June production entered (the oracle reports it as "awaiting
source / no production"), so there is no output to pin — it is skipped.

Run: cd artifacts/prayag && python3 -m pytest tests/test_report_export_june_oracle.py
 or: cd artifacts/prayag && python3 -m tests.test_report_export_june_oracle
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
from metrics import Record
from reports import registry

_FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_YM = "2026-06"
_TOL = 0.005   # ±0.5%, matching the live /build-state acceptance gate.


# ---------------------------------------------------------------------------
# Reference totals. See the module docstring for ORACLE-VERIFIED vs
# SNAPSHOT-PINNED. Every headline OUTPUT is pinned; secondary columns are pinned
# only where they still reconcile.
# ---------------------------------------------------------------------------
_REF = {
    # (A) Pipe M/C Summary — ORACLE-VERIFIED: matches the oracle's own documented
    # complete Report-5 daily grand total (168,738 kg). The A-cell 170,216 is the
    # stale R5/R11 date-wise max and is intentionally NOT pinned.
    "pipe":        {"out": 168_738},
    # (B) Moulding M/C Summary — SNAPSHOT-PINNED (oracle 89,100.2 is a mid-month
    # freeze; live source backfilled to 93,122.9).
    "moulding":    {"out": 93_122.89},
    # (C) Group of Moulding — SNAPSHOT-PINNED (ties to (B) output).
    "gom":         {"out": 93_122.89},
    # (D) Pipe Moulds Summary — SNAPSHOT-PINNED (D-oracle 89,151.74 kg / 1,340,117
    # pcs is a mid-month freeze; Report-12 now recomputes to 93,122.9 / 1,325,251).
    "pipe_moulds": {"kg": 93_122.89, "pcs": 1_325_251},
    # Moulding %age Efficiency — SNAPSHOT-PINNED (output ties to (B)).
    "mould_eff":   {"out": 93_122.89},
    # Garden Pipe Summary — ORACLE-VERIFIED headline output (60,928 kg).
    "garden":      {"out": 60_928},
    # PTMT Moulds Summary — ORACLE-VERIFIED grand total (excl. grinding) &
    # injection subtotal (separate PTMT workbook).
    "ptmt_moulds": {"grand_out": 156_977.4, "injection_out": 141_097.5},
    # PTMT %age Efficiency — ORACLE-VERIFIED INJECTION TOTAL (kg & machine count).
    "ptmt_eff":    {"out": 141_097.4, "n": 48},
}


# ---------------------------------------------------------------------------
# Offline fixture wiring: replace the live sheet readers with committed data.
# ---------------------------------------------------------------------------
def _install_fixtures():
    with open(os.path.join(_FIX, "daily_2026_06.json")) as f:
        recs = [Record(**d) for d in json.load(f)]
    with open(os.path.join(_FIX, "pipe_report12_2026_06.json")) as f:
        report12 = json.load(f)

    saved = {
        "gdr": sheets.get_daily_records,
        "dfi": sheets._daily_file_id,
        "tok": sheets._get_access_token,
        "rv":  sheets.read_values,
        "lpm": sheets.load_pipe_moulds,
    }
    sheets.get_daily_records = lambda months, *a, **k: (
        [r for r in recs if r.period in months], [], [])
    sheets._daily_file_id = lambda plant, ym: "FIXTURE" if plant == "PIPE" else None
    sheets._get_access_token = lambda *a, **k: "FIXTURE"
    sheets.read_values = lambda fid, tab, tok, *a, **k: (
        report12 if tab == "Report-12" else [])

    # Simulate the mid-month-frozen Reports 17-20 state (89,152 kg) that diverges
    # from the backfilled Report-12 figure (93,123 kg) by 4.3% > 1%.  This causes
    # gen_pipe_moulds to flag stale_mould_tabs=True and fall back to Report-12, so
    # the oracle pin ("pipe_moulds": 93,122.89 kg) remains valid and the fallback
    # path is exercised in CI.
    def _stale_moulds_data(ym):
        return {
            "available": True, "incomplete": False, "missing": [],
            "month": ym, "file_id": "FIXTURE",
            "grand_kg": 89_151.74, "grand_pcs": 1_340_117,
            "groups": [
                {"group": "CPVC", "total_kg": 22_000.0, "total_pcs": 330_000, "n_run": 5, "n_total": 6},
                {"group": "UPVC", "total_kg": 42_000.0, "total_pcs": 620_000, "n_run": 8, "n_total": 9},
                {"group": "SWR",  "total_kg": 18_000.0, "total_pcs": 270_000, "n_run": 4, "n_total": 5},
                {"group": "AGRI", "total_kg":  7_151.74, "total_pcs": 120_117, "n_run": 2, "n_total": 2},
            ],
        }

    sheets.load_pipe_moulds = _stale_moulds_data
    return saved


def _restore(saved):
    sheets.get_daily_records  = saved["gdr"]
    sheets._daily_file_id     = saved["dfi"]
    sheets._get_access_token  = saved["tok"]
    sheets.read_values        = saved["rv"]
    sheets.load_pipe_moulds   = saved["lpm"]


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
# One test per report — each pins the generator's key TOTAL(s) to the reference.
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
        print(f"\nAll {len(tests)} June report-export oracle tests passed.")
    finally:
        _restore(_saved)
