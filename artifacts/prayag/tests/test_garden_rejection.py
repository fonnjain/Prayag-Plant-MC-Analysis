"""Garden rejection — join from the Daily Report matrix.

Validates the fix described in the spec:

  The block tabs (MACHINE 1-4) have no rejection column.  Rejection data
  lives in the 'Daily Report' wide matrix, which the Garden join already
  opens for run hours.  The join previously discarded rejection.

Key invariants tested here:

  Trap 1  — the rejection join is NOT gated on actual_hours > 0, so July
             (0 run hours but 1,853.50 kg rejection) populates correctly.
  Trap 2  — the rejection denominator is the DR's OWN output (not the
             block-tab output), so 1853.50 ÷ 32191 ≈ 5.76% not 2.71%.
  R-08    — May (empty DR) shows n/a, not 0.00%.
  R-35    — basis-divergence note fires when block-tab output differs from
             DR output by > 2%.
  R-23    — output from block tabs is never changed by the rejection join.

Fully offline: list_tabs and read_values are patched; the real
sheets._emit_blocks is called so any production drift in that function is
caught immediately — there is no mirrored reimplementation in this file.

Run: cd artifacts/prayag && python3 -m pytest tests/test_garden_rejection.py -v
"""
import os
import re
import sys
import unittest.mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
from metrics import Record, compute_metrics


# ---------------------------------------------------------------------------
# Fixture builders — produce raw Sheets cell-array values
# ---------------------------------------------------------------------------

_MONTH_ABBR: dict[str, tuple[str, str]] = {
    "2026-04": ("Apr", "26"),
    "2026-05": ("May", "26"),
    "2026-06": ("Jun", "26"),
    "2026-07": ("Jul", "26"),
}


def _block_values(ym: str, day_kg: list) -> list:
    """Raw cell values for one GARDEN block tab (one machine, no rejection column).

    day_kg: [(day_of_month, output_kg), ...]  — pass [] for no production.
    Block tabs never carry a rejection column; all rejection comes from the
    Daily Report matrix so rejection_tracked stays False until the join.
    """
    abbr, yr = _MONTH_ABBR[ym]
    rows: list = [["DATE", "KG"]]
    for day, kg in day_kg:
        rows.append([f"{abbr} {day}, 20{yr}", str(kg)])
    return rows


def _dr_values(ym: str, dates: list, machines: dict) -> list:
    """Raw cell values for the GARDEN 'Daily Report' wide matrix.

    dates   : ordered list of day-of-month integers; must have >= 2 entries
              for parse_daily_matrix to recognise the date-row header.
    machines: {label_str: [(day, run_hours, output_kg, reject_kg), ...]}
              Omitted (day, machine) pairs default to (0, 0, 0).
    """
    abbr, yr = _MONTH_ABBR[ym]
    # Row 0: machine-label column + three sub-columns per date group
    date_row: list = ["MACHINE"]
    for d in dates:
        date_row += [f"{d:02d}-{abbr}-{yr}", "", ""]
    # Row 1: sub-headers (Run Hours / Output / Rejection per group)
    sub_row: list = [""]
    for _ in dates:
        sub_row += ["Run Hours", "Output", "Rejection"]
    rows: list = [date_row, sub_row]
    for label, entries in machines.items():
        d_map = {d: (rh, out, rej) for d, rh, out, rej in entries}
        row: list = [label]
        for d in dates:
            rh, out, rej = d_map.get(d, (0, 0, 0))
            row += [str(rh), str(out), str(rej)]
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Shared constants and test-runner helper
# ---------------------------------------------------------------------------

_TABS = ["MACHINE 1", "MACHINE 2", "MACHINE 3", "MACHINE 4", "Daily Report"]
_FAKE_FID = "fixture-file-id"
_FAKE_TOK  = "fixture-token"
_SPEC = {
    "tab_re": r"MACHINE\s+\d+",
    "runhours_tab": "Daily Report",
    "machine_prefix": "GARDEN M/C - ",
}


def _full_block_map(ym: str, explicit: dict) -> dict:
    """Block-map with all 4 MACHINE tabs; tabs not in *explicit* are empty."""
    return {t: explicit.get(t, _block_values(ym, []))
            for t in _TABS if t.startswith("MACHINE")}


def _run_emit_blocks(ym: str, block_map: dict, dr: list) -> tuple:
    """Patch list_tabs + read_values and invoke the real sheets._emit_blocks.

    Returns (raw_records, report_dict).  Notes accumulated by _emit_blocks
    are in report.get('notes', []).
    """
    def _list_tabs(file_id, token):
        return _TABS

    def _read_values(file_id, tab, token):
        if tab == "Daily Report":
            return dr
        return block_map.get(tab, [])

    with (
        unittest.mock.patch("sheets.list_tabs",   side_effect=_list_tabs),
        unittest.mock.patch("sheets.read_values", side_effect=_read_values),
    ):
        raw, report = sheets._emit_blocks(
            "GARDEN", ym, _FAKE_FID, _SPEC, _FAKE_TOK, "GARDEN", "kg", {}
        )
    return raw, report


def _by_machine_date(raw: list) -> dict:
    """Index raw records by (machine_number_str, date_str)."""
    idx: dict = {}
    for r in raw:
        m = re.search(r"(\d+)", r.machine)
        if m:
            idx[(m.group(1), r.date)] = r
    return idx


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------

class TestJulyRejection:
    """July 2026: 0 run hours, 1,853.50 kg rejection across M2 and M3."""

    YM = "2026-07"

    def _block_map(self):
        return _full_block_map(self.YM, {
            "MACHINE 1": _block_values(self.YM, [(15, 15000)]),
            "MACHINE 2": _block_values(self.YM, [(15, 18000)]),
            "MACHINE 3": _block_values(self.YM, [(15, 35000)]),
        })

    def _dr(self):
        # Two date groups required for parse_daily_matrix to detect the header.
        return _dr_values(self.YM, [15, 16], {
            "MACHINE-1": [(15, 0, 2191,  0.0)],
            "MACHINE-2": [(15, 0, 8000, 70.00)],
            "MACHINE-3": [(15, 0, 22000, 1783.50)],
        })

    def test_reject_count_populated_despite_zero_hours(self):
        """Trap 1: rejection join must NOT be gated on actual_hours > 0."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        idx = _by_machine_date(raw)

        # rej_map must have entries for M2 and M3 even though hours=0
        assert idx[("2", "2026-07-15")].reject_count == pytest.approx(70.0)
        assert idx[("3", "2026-07-15")].reject_count == pytest.approx(1783.50)
        assert idx[("2", "2026-07-15")].rejection_tracked is True
        assert idx[("3", "2026-07-15")].rejection_tracked is True

    def test_rejection_pct_uses_dr_output_not_block_output(self):
        """Trap 2: denominator is DR output (32,191 kg), not block-tab output."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        metrics = compute_metrics(raw)

        # DR output total = 2191 + 8000 + 22000 = 32,191
        # Reject = 70 + 1783.50 = 1,853.50
        # Pct = 1853.50 / 32191 ≈ 5.76%
        assert metrics.reject_count == pytest.approx(1853.50)
        assert metrics.rejection_pct == pytest.approx(1853.50 / 32191.0, rel=1e-4)

    def test_block_output_unchanged(self):
        """R-23: block-tab output must not change after the rejection join."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        idx = _by_machine_date(raw)
        assert idx[("1", "2026-07-15")].total_count == pytest.approx(15000.0)
        assert idx[("2", "2026-07-15")].total_count == pytest.approx(18000.0)
        assert idx[("3", "2026-07-15")].total_count == pytest.approx(35000.0)


class TestMayRejection:
    """May 2026: DR is genuinely empty (no rows returned) → rejection n/a."""

    YM = "2026-05"

    def _block_map(self):
        return _full_block_map(self.YM, {
            "MACHINE 1": _block_values(self.YM, [(10, 13000)]),
            "MACHINE 2": _block_values(self.YM, [(10, 12000)]),
        })

    def test_may_rejection_suppressed_when_dr_empty(self):
        """Empty DR → no rh_rows → rejection_tracked stays False → n/a."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), [])

        for r in raw:
            assert r.rejection_tracked is False, (
                f"{r.machine}: rejection_tracked should be False when DR is empty"
            )
            assert r.reject_count == 0.0

        metrics = compute_metrics(raw)
        assert metrics.rejection_available is False
        assert metrics.rejection_pct == 0.0  # 0/0 via _safe_div, but not displayed

    def test_may_no_fake_zero_percent(self):
        """R-08: May must not display 0.00% (rejection_available must be False)."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), [])
        metrics = compute_metrics(raw)
        assert not metrics.rejection_available, "May: rejection_available must be False"


class TestAprilRejection:
    """April 2026: 1,191 kg rejection, DR output 38,950 kg → 3.06%."""

    YM = "2026-04"

    def _block_map(self):
        return _full_block_map(self.YM, {
            "MACHINE 1": _block_values(self.YM, [(10, 10500)]),
            "MACHINE 2": _block_values(self.YM, [(10, 9500)]),
            "MACHINE 3": _block_values(self.YM, [(15, 13200)]),
            "MACHINE 4": _block_values(self.YM, [(15, 9536)]),
        })

    def _dr(self):
        return _dr_values(self.YM, [10, 15], {
            "MACHINE-1": [(10, 8, 9000,  300.0)],
            "MACHINE-2": [(10, 8, 8000,  250.0)],
            "MACHINE-3": [(15, 8, 12000, 441.0)],
            "MACHINE-4": [(15, 8, 9950,  200.0)],
        })

    def test_april_rejection_kg_and_pct(self):
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        metrics = compute_metrics(raw)

        assert metrics.reject_count == pytest.approx(1191.0)
        assert metrics.rejection_pct == pytest.approx(1191.0 / 38950.0, rel=1e-4)

    def test_april_rejection_tracked(self):
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        assert all(r.rejection_tracked for r in raw)
        metrics = compute_metrics(raw)
        assert metrics.rejection_available is True


class TestBasisDivergenceNote:
    """Basis-divergence note fires when block-tab vs DR output differ > 2%."""

    YM = "2026-07"

    def test_note_fires_when_gap_exceeds_2pct(self):
        """Large divergence (July: block 68390 vs DR 32191 ≈ 52%) → note."""
        bm = _full_block_map(self.YM, {
            "MACHINE 2": _block_values(self.YM, [(15, 30000)]),
            "MACHINE 3": _block_values(self.YM, [(15, 38390)]),
        })
        dr = _dr_values(self.YM, [15, 16], {
            "MACHINE-2": [(15, 0, 10000,  70.0)],
            "MACHINE-3": [(15, 0, 22191, 1783.50)],
        })
        _, report = _run_emit_blocks(self.YM, bm, dr)
        notes = report.get("notes", [])
        assert notes, "Basis-divergence note must fire when gap > 2%"
        assert any("Daily Report output basis" in n for n in notes)
        assert any("block-tab output" in n for n in notes)

    def test_note_suppressed_when_gap_within_2pct(self):
        """Small divergence (< 2%) → no note."""
        bm = _full_block_map("2026-06", {
            "MACHINE 1": _block_values("2026-06", [(10, 10000)]),
        })
        dr = _dr_values("2026-06", [10, 11], {
            "MACHINE-1": [(10, 8, 9900, 50.0)],
        })
        _, report = _run_emit_blocks("2026-06", bm, dr)
        notes = report.get("notes", [])
        div_notes = [n for n in notes if "Daily Report output basis" in n]
        assert not div_notes, f"Unexpected divergence note for <2% gap: {div_notes}"

    def test_note_suppressed_when_no_rejection_data(self):
        """No rejection in DR (empty) → no note."""
        bm = _full_block_map("2026-05", {
            "MACHINE 1": _block_values("2026-05", [(10, 13000)]),
        })
        _, report = _run_emit_blocks("2026-05", bm, [])
        notes = report.get("notes", [])
        assert not notes

    def test_note_names_both_output_figures(self):
        """Note text must include both the DR basis and block-tab figures."""
        bm = _full_block_map(self.YM, {
            "MACHINE 2": _block_values(self.YM, [(15, 68390)]),
        })
        dr = _dr_values(self.YM, [15, 16], {
            "MACHINE-2": [(15, 0, 32191, 1853.50)],
        })
        _, report = _run_emit_blocks(self.YM, bm, dr)
        notes = report.get("notes", [])
        div_notes = [n for n in notes if "Daily Report output basis" in n]
        assert div_notes, "Divergence note must fire"
        note = div_notes[0]
        assert "32,191" in note, f"DR output figure missing from note: {note}"
        assert "68,390" in note, f"Block-tab output figure missing from note: {note}"


class TestRejectionTrackedGating:
    """rejection_tracked is set only for machine-dates that have a DR row."""

    YM = "2026-07"

    def test_machine_without_dr_row_keeps_rejection_tracked_false(self):
        """A block record with no matching DR entry stays rejection_tracked=False."""
        bm = _full_block_map(self.YM, {
            "MACHINE 1": _block_values(self.YM, [(20, 5000)]),
            "MACHINE 2": _block_values(self.YM, [(20, 8000)]),
        })
        dr = _dr_values(self.YM, [20, 21], {
            # DR only has machine 2; machine 1 is absent
            "MACHINE-2": [(20, 0, 7500, 100.0)],
        })
        raw, _ = _run_emit_blocks(self.YM, bm, dr)
        idx = _by_machine_date(raw)
        assert idx[("1", "2026-07-20")].rejection_tracked is False, (
            "Machine-1 has no DR entry: rejection_tracked must stay False"
        )
        assert idx[("2", "2026-07-20")].rejection_tracked is True

    def test_machine_date_with_zero_rejection_but_output_gets_tracked(self):
        """If DR has output for a machine-date but reject=0, rejection is tracked as 0."""
        bm = _full_block_map("2026-04", {
            "MACHINE 1": _block_values("2026-04", [(1, 9000)]),
        })
        dr = _dr_values("2026-04", [1, 2], {
            "MACHINE-1": [(1, 8, 8500, 0.0)],
        })
        raw, _ = _run_emit_blocks("2026-04", bm, dr)
        idx = _by_machine_date(raw)
        r = idx[("1", "2026-04-01")]
        assert r.rejection_tracked is True
        assert r.reject_count == 0.0
        assert r.reject_denominator == pytest.approx(8500.0)


class TestJulyPerMachineKg:
    """Per-machine July: MACHINE-2 = 70.00 kg, MACHINE-3 = 1,783.50 kg."""

    YM = "2026-07"

    def test_per_machine_rejection_kg(self):
        bm = _full_block_map(self.YM, {
            "MACHINE 2": _block_values(self.YM, [(15, 18000)]),
            "MACHINE 3": _block_values(self.YM, [(15, 35000)]),
        })
        dr = _dr_values(self.YM, [15, 16], {
            "MACHINE-2": [(15, 0, 8000,  70.00)],
            "MACHINE-3": [(15, 0, 22000, 1783.50)],
        })
        raw, _ = _run_emit_blocks(self.YM, bm, dr)
        idx = _by_machine_date(raw)
        assert idx[("2", "2026-07-15")].reject_count == pytest.approx(70.00)
        assert idx[("3", "2026-07-15")].reject_count == pytest.approx(1783.50)

        # Per-machine metrics
        m2 = compute_metrics([idx[("2", "2026-07-15")]])
        m3 = compute_metrics([idx[("3", "2026-07-15")]])
        assert m2.reject_count == pytest.approx(70.00)
        assert m3.reject_count == pytest.approx(1783.50)


class TestSyntheticRejectionOnlyRecords:
    """Rejection-only DR rows (no matching block-tab date) become synthetic records."""

    YM = "2026-07"

    # Spec fixture: M/C-2 31-Jul 70.00; M/C-3 10-Jul 1,513.50, 27-Jul 95.00,
    # 30-Jul 90.00, 31-Jul 85.00.  M/C-3 block tab: Jul-10, Jul-27, Jul-30.
    # M/C-2 has no block entries at all → entire M/C-2 rejection is synthetic.

    def _block_map(self):
        return _full_block_map(self.YM, {
            "MACHINE 3": _block_values(self.YM, [
                (10, 1513.40),
                (27, 1474.80),
                (30, 1425.40),
            ]),
        })

    def _dr(self):
        # Four date groups (10, 27, 30, 31) → ≥2, parser happy.
        return _dr_values(self.YM, [10, 27, 30, 31], {
            "MACHINE-2": [(31, 0, 0,      70.00)],
            "MACHINE-3": [
                (10, 0, 0,       1513.50),
                (27, 0, 1378.30,   95.00),
                (30, 0, 1352.90,   90.00),
                (31, 0, 0,         85.00),
            ],
        })

    def test_total_rejection_kg_reaches_spec_target(self):
        """Full July: 1,853.50 kg total = 1,698.50 transferred + 155.00 synthetic."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        metrics = compute_metrics(raw)
        assert metrics.reject_count == pytest.approx(1853.50, abs=0.01)

    def test_two_synthetic_records_appended(self):
        """M/C-2 Jul-31 and M/C-3 Jul-31 each get a synthetic record."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        # Synthetic records: total_count == 0 and rejection_tracked == True
        synth = [r for r in raw if r.total_count == 0.0 and r.rejection_tracked]
        assert len(synth) == 2, (
            f"Expected 2 synthetic records, got {len(synth)}; "
            f"synth machines: {[r.machine for r in synth]}"
        )
        synth_mcs = {re.search(r"(\d+)", r.machine).group(1) for r in synth}
        assert "2" in synth_mcs, "Synthetic record for M/C-2 missing"
        assert "3" in synth_mcs, "Synthetic record for M/C-3 missing"
        for r in synth:
            mc = re.search(r"(\d+)", r.machine).group(1)
            if mc == "2":
                assert r.reject_count == pytest.approx(70.00)
            else:
                assert r.reject_count == pytest.approx(85.00)

    def test_synthetic_records_have_zero_output_and_hours(self):
        """Synthetic records must not add output or hours (R-23 / util invariant)."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        synth = [r for r in raw if r.total_count == 0.0 and r.rejection_tracked]
        for r in synth:
            assert r.total_count == 0.0, (
                f"{r.machine}: synthetic record has output {r.total_count}"
            )
            assert (r.actual_hours or 0.0) == 0.0, (
                f"{r.machine}: synthetic record has hours {r.actual_hours}"
            )

    def test_synthetic_records_rejection_tracked_true(self):
        """rejection_tracked must be True so the kg is not suppressed."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        synth = [r for r in raw if r.total_count == 0.0 and r.rejection_tracked]
        for r in synth:
            assert r.rejection_tracked is True

    def test_per_machine_rejection_after_fix(self):
        """M/C-2 total = 70.00, M/C-3 total = 1,783.50 (1513.50 + 95 + 90 + 85)."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        from collections import defaultdict
        by_mc: dict = defaultdict(float)
        for r in raw:
            mn = re.search(r"(\d+)", r.machine)
            if mn:
                by_mc[mn.group(1)] += r.reject_count
        assert by_mc["2"] == pytest.approx(70.00)
        assert by_mc["3"] == pytest.approx(1783.50, abs=0.01)

    def test_rejection_pct_reaches_576(self):
        """After fix: 1853.50 / 32191 ≈ 5.76%."""
        # Simpler fixture: known total DR output = 32191 on Jul-15.
        bm = _full_block_map(self.YM, {
            "MACHINE 2": _block_values(self.YM, [(15, 18000)]),
            "MACHINE 3": _block_values(self.YM, [(15, 35000)]),
        })
        dr = _dr_values(self.YM, [15, 31], {
            "MACHINE-2": [
                (15, 0, 32191, 1698.50),
                (31, 0, 0,       70.00),
            ],
            "MACHINE-3": [(31, 0, 0, 85.00)],
        })
        raw, _ = _run_emit_blocks(self.YM, bm, dr)
        metrics = compute_metrics(raw)
        assert metrics.reject_count == pytest.approx(1853.50)
        assert metrics.rejection_pct == pytest.approx(1853.50 / 32191.0, rel=1e-3)

    def test_warning_note_emitted(self):
        """R-35: a note must be appended listing the dropped dates and total kg."""
        raw, report = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        notes = report.get("notes", [])
        synth_notes = [n for n in notes if "no production row" in n]
        assert synth_notes, f"No synthetic-record note emitted; all notes: {notes}"
        note = synth_notes[0]
        assert "155.00" in note, f"Total kg missing from note: {note}"
        assert "M/C-2" in note
        assert "M/C-3" in note
        assert "31-Jul" in note

    def test_warning_note_absent_when_all_matched(self):
        """When every DR rejection row has a block record, no synthetic note fires."""
        bm = _full_block_map(self.YM, {
            "MACHINE 2": _block_values(self.YM, [(15, 18000)]),
            "MACHINE 3": _block_values(self.YM, [(15, 35000)]),
        })
        dr = _dr_values(self.YM, [15, 16], {
            "MACHINE-2": [(15, 0, 8000,  70.0)],
            "MACHINE-3": [(15, 0, 22000, 85.0)],
        })
        _, report = _run_emit_blocks(self.YM, bm, dr)
        notes = report.get("notes", [])
        synth_notes = [n for n in notes if "no production row" in n]
        assert not synth_notes, f"Unexpected synthetic note: {synth_notes}"

    def test_block_output_unchanged_after_synthetic_emit(self):
        """R-23: synthetic records must not alter any pre-existing block record."""
        raw, _ = _run_emit_blocks(self.YM, self._block_map(), self._dr())
        # Expected total_count for the three real block-tab entries
        expected = {
            ("GARDEN M/C - 3", "2026-07-10"): 1513.40,
            ("GARDEN M/C - 3", "2026-07-27"): 1474.80,
            ("GARDEN M/C - 3", "2026-07-30"): 1425.40,
        }
        for r in raw:
            key = (r.machine, r.date)
            if key in expected:
                assert r.total_count == pytest.approx(expected[key]), (
                    f"{r.machine} {r.date}: block output changed to {r.total_count}"
                )


class TestMC3Jul10ZeroDenominator:
    """M/C-3 10-Jul: 1,513.50 kg rejection, DR output = 0 — zero denominator."""

    YM = "2026-07"

    def test_zero_denominator_record_is_tracked(self):
        """Block record exists for Jul-10, so rejection IS transferred and tracked."""
        bm = _full_block_map(self.YM, {
            "MACHINE 3": _block_values(self.YM, [(10, 1513.40)]),
        })
        # Two date groups needed for parse_daily_matrix to find the header.
        dr = _dr_values(self.YM, [10, 11], {
            "MACHINE-3": [(10, 0, 0, 1513.50)],
        })
        raw, _ = _run_emit_blocks(self.YM, bm, dr)
        idx = _by_machine_date(raw)
        r = idx[("3", "2026-07-10")]
        assert r.rejection_tracked is True
        assert r.reject_count == pytest.approx(1513.50)
        assert r.reject_denominator == pytest.approx(0.0)

    def test_zero_denominator_does_not_crash_compute_metrics(self):
        """compute_metrics must not raise on a zero reject_denominator."""
        bm = _full_block_map(self.YM, {
            "MACHINE 3": _block_values(self.YM, [(10, 1513.40)]),
        })
        dr = _dr_values(self.YM, [10, 11], {
            "MACHINE-3": [(10, 0, 0, 1513.50)],
        })
        raw, _ = _run_emit_blocks(self.YM, bm, dr)
        metrics = compute_metrics(raw)   # must not raise
        # reject_count accumulates; rejection_pct with denom=0 is suppressed
        assert metrics.reject_count == pytest.approx(1513.50)


if __name__ == "__main__":
    import subprocess, sys as _sys
    result = subprocess.run(
        [_sys.executable, "-m", "pytest", __file__, "-v"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    _sys.exit(result.returncode)
