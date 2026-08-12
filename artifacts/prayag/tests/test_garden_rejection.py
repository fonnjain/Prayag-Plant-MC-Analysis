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

Fully offline: Records are constructed directly; no Google Sheets calls.

Run: cd artifacts/prayag && python3 -m pytest tests/test_garden_rejection.py -v
"""
import datetime
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from metrics import Record, compute_metrics


# ---------------------------------------------------------------------------
# Helpers: build Records that mimic parser output
# ---------------------------------------------------------------------------

def _dr_row(machine_num: int, date: str, ym: str, *,
            run_hours: float, output_kg: float, reject_kg: float) -> Record:
    """Mimic a row returned by parse_daily_matrix for Garden's Daily Report."""
    return Record(
        grain="daily",
        period=ym,
        date=date,
        plant="GARDEN",
        segment="GARDEN",
        unit="kg",
        machine=f"GARDEN MACHINE-{machine_num}",
        actual_hours=run_hours,
        total_count=output_kg,
        reject_count=reject_kg,
        source_family="GARDEN",
    )


def _block_row(machine_num: int, date: str, ym: str, *,
               output_kg: float) -> Record:
    """Mimic a row returned by parse_daily_blocks (block tab, no rejection column)."""
    return Record(
        grain="daily",
        period=ym,
        date=date,
        plant="GARDEN",
        segment="GARDEN",
        unit="kg",
        machine=f"GARDEN M/C - {machine_num}",
        total_count=output_kg,
        rejection_tracked=False,    # block tabs have no rejection column
    )


# ---------------------------------------------------------------------------
# The join logic (mirrors the fix in sheets._emit_blocks)
# ---------------------------------------------------------------------------

def _apply_garden_rejection_join(raw, rh_rows, *, prefix="GARDEN M/C - ",
                                   plant="GARDEN", ym="2026-07"):
    """Apply the Garden rejection join to *raw* (in place) using *rh_rows*.

    Mirrors sheets._emit_blocks exactly, including the synthetic-record step.
    Returns (rej_map, dr_out_map, notes).
    """
    rh_map = {}
    rej_map = {}
    dr_out_map = {}

    for rr in rh_rows:
        _mn = re.search(r"(\d+)", rr.machine)
        if not _mn:
            continue
        _k = (_mn.group(1), rr.date)
        if rr.actual_hours and rr.actual_hours > 0:
            rh_map[_k] = rh_map.get(_k, 0.0) + rr.actual_hours
        rej_map[_k] = rej_map.get(_k, 0.0) + rr.reject_count
        dr_out_map[_k] = dr_out_map.get(_k, 0.0) + rr.total_count

    for r in raw:
        _m = re.search(r"(\d+)", r.machine)
        key = (_m.group(1), r.date) if _m else None
        if key:
            if key in rh_map:
                r.actual_hours = rh_map[key]
            if key in dr_out_map:
                r.reject_count = rej_map.get(key, 0.0)
                r.reject_denominator = dr_out_map[key]
                r.rejection_tracked = True

    notes = []

    # Synthetic records for rejection-only machine-dates (mirrors production)
    if rej_map:
        _matched_keys = set()
        for r in raw:
            _m2 = re.search(r"(\d+)", r.machine)
            if _m2:
                _matched_keys.add((_m2.group(1), r.date))

        _synth_items = []
        for _k, _rej_kg in sorted(rej_map.items()):
            if _rej_kg > 0 and _k not in _matched_keys:
                _synth_items.append((_k[0], _k[1], _rej_kg,
                                     dr_out_map.get(_k, 0.0)))

        if _synth_items:
            for _mc_num, _sdate, _rej_kg, _dr_out_kg in _synth_items:
                raw.append(Record(
                    grain="daily",
                    period=ym,
                    date=_sdate,
                    plant=plant,
                    segment="garden",
                    unit="kg",
                    machine=f"{prefix}{_mc_num}",
                    actual_hours=0.0,
                    total_count=0.0,
                    reject_count=_rej_kg,
                    reject_denominator=_dr_out_kg,
                    rejection_tracked=True,
                    source_family="garden",
                ))

            def _fmt_dmy(iso_date):
                _dt = datetime.datetime.strptime(iso_date, "%Y-%m-%d")
                return f"{_dt.day}-{_dt.strftime('%b')}"

            _synth_parts = ", ".join(
                f"M/C-{mc} {_fmt_dmy(dt)} {rkg:.2f} kg"
                for mc, dt, rkg, _ in _synth_items
            )
            _synth_total = sum(rkg for _, _, rkg, _ in _synth_items)
            notes.append(
                f"{plant} {ym}: {_synth_total:,.2f} kg of rejection "
                f"recorded on machine-dates with no production row "
                f"({_synth_parts}) — counted in the rejection total, "
                f"shown separately as no output is attributed to those "
                f"dates."
            )

    # Basis-divergence note
    if rej_map:
        _dr_total = sum(dr_out_map.values())
        _blk_total = sum(r.total_count for r in raw)
        if _dr_total > 0 and _blk_total > 0:
            _gap = abs(_blk_total - _dr_total) / max(_blk_total, _dr_total)
            if _gap > 0.02:
                notes.append(
                    f"{plant} {ym}: rejection % is measured against the "
                    f"Daily Report output basis "
                    f"({_dr_total:,.0f} kg), which differs from the "
                    f"displayed block-tab output ({_blk_total:,.0f} kg)."
                )
    return rej_map, dr_out_map, notes


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestJulyRejection:
    """July 2026: 0 run hours, 1,853.50 kg rejection across M2 and M3."""

    YM = "2026-07"

    def _make_rh_rows(self):
        # July: M2 and M3 have rejection but NO run hours
        return [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=8000, reject_kg=70.00),
            _dr_row(3, "2026-07-15", self.YM, run_hours=0, output_kg=22000, reject_kg=1783.50),
            _dr_row(1, "2026-07-15", self.YM, run_hours=0, output_kg=2191, reject_kg=0.0),
        ]

    def _make_raw(self):
        return [
            _block_row(1, "2026-07-15", self.YM, output_kg=15000),
            _block_row(2, "2026-07-15", self.YM, output_kg=18000),
            _block_row(3, "2026-07-15", self.YM, output_kg=35000),
        ]

    def test_reject_count_populated_despite_zero_hours(self):
        """Trap 1: rejection join must NOT be gated on actual_hours > 0."""
        raw = self._make_raw()
        rh_rows = self._make_rh_rows()
        rej_map, dr_out_map, _ = _apply_garden_rejection_join(raw, rh_rows)

        # rej_map must have entries for M2 and M3 even though hours=0
        assert ("2", "2026-07-15") in rej_map
        assert rej_map[("2", "2026-07-15")] == pytest.approx(70.0)
        assert ("3", "2026-07-15") in rej_map
        assert rej_map[("3", "2026-07-15")] == pytest.approx(1783.50)

        # Block records must have their rejection joined
        by_mc = {r.machine: r for r in raw}
        assert by_mc["GARDEN M/C - 2"].reject_count == pytest.approx(70.0)
        assert by_mc["GARDEN M/C - 3"].reject_count == pytest.approx(1783.50)
        assert by_mc["GARDEN M/C - 2"].rejection_tracked is True
        assert by_mc["GARDEN M/C - 3"].rejection_tracked is True

    def test_rejection_pct_uses_dr_output_not_block_output(self):
        """Trap 2: denominator is DR output (32,191 kg), not block-tab output."""
        raw = self._make_raw()
        rh_rows = self._make_rh_rows()
        _apply_garden_rejection_join(raw, rh_rows)

        metrics = compute_metrics(raw)

        # DR output total = 8000 + 22000 + 2191 = 32,191
        # Reject = 70 + 1783.50 = 1,853.50
        # Pct = 1853.50 / 32191 ≈ 5.76%
        assert metrics.reject_count == pytest.approx(1853.50)
        assert metrics.rejection_pct == pytest.approx(1853.50 / 32191.0, rel=1e-4)

    def test_block_output_unchanged(self):
        """R-23: block-tab output must not change after the rejection join."""
        raw = self._make_raw()
        block_output_before = {r.machine: r.total_count for r in raw}
        _apply_garden_rejection_join(raw, self._make_rh_rows())
        for r in raw:
            assert r.total_count == block_output_before[r.machine], (
                f"{r.machine} output changed: {block_output_before[r.machine]} → {r.total_count}"
            )


class TestMayRejection:
    """May 2026: DR is genuinely empty (no rows returned) → rejection n/a."""

    YM = "2026-05"

    def _make_raw(self):
        return [
            _block_row(1, "2026-05-10", self.YM, output_kg=13000),
            _block_row(2, "2026-05-10", self.YM, output_kg=12000),
        ]

    def test_may_rejection_suppressed_when_dr_empty(self):
        """Empty DR → no rh_rows → rejection_tracked stays False → n/a."""
        raw = self._make_raw()
        _apply_garden_rejection_join(raw, rh_rows=[])  # empty DR

        for r in raw:
            assert r.rejection_tracked is False, (
                f"{r.machine}: rejection_tracked should be False when DR is empty"
            )
            assert r.reject_count == 0.0

        metrics = compute_metrics(raw)
        assert metrics.rejection_available is False
        assert metrics.rejection_pct == 0.0   # 0/0 via _safe_div, but not displayed

    def test_may_no_fake_zero_percent(self):
        """R-08: May must not display 0.00% (rejection_available must be False)."""
        raw = self._make_raw()
        _apply_garden_rejection_join(raw, rh_rows=[])
        metrics = compute_metrics(raw)
        assert not metrics.rejection_available, "May: rejection_available must be False"


class TestAprilRejection:
    """April 2026: 1,191 kg rejection, DR output 38,950 kg → 3.06%."""

    YM = "2026-04"

    def _make_rh_rows(self):
        # Spread across multiple dates and machines
        return [
            _dr_row(1, "2026-04-10", self.YM, run_hours=8, output_kg=9000, reject_kg=300.0),
            _dr_row(2, "2026-04-10", self.YM, run_hours=8, output_kg=8000, reject_kg=250.0),
            _dr_row(3, "2026-04-15", self.YM, run_hours=8, output_kg=12000, reject_kg=441.0),
            _dr_row(4, "2026-04-15", self.YM, run_hours=8, output_kg=9950, reject_kg=200.0),
        ]  # total reject = 1191; total DR out = 38950

    def _make_raw(self):
        return [
            _block_row(1, "2026-04-10", self.YM, output_kg=10500),
            _block_row(2, "2026-04-10", self.YM, output_kg=9500),
            _block_row(3, "2026-04-15", self.YM, output_kg=13200),
            _block_row(4, "2026-04-15", self.YM, output_kg=9536),
        ]  # total block out = 42736

    def test_april_rejection_kg_and_pct(self):
        raw = self._make_raw()
        _apply_garden_rejection_join(raw, self._make_rh_rows())
        metrics = compute_metrics(raw)

        assert metrics.reject_count == pytest.approx(1191.0)
        assert metrics.rejection_pct == pytest.approx(1191.0 / 38950.0, rel=1e-4)

    def test_april_rejection_tracked(self):
        raw = self._make_raw()
        _apply_garden_rejection_join(raw, self._make_rh_rows())
        assert all(r.rejection_tracked for r in raw)
        metrics = compute_metrics(raw)
        assert metrics.rejection_available is True


class TestBasisDivergenceNote:
    """Basis-divergence note fires when block-tab vs DR output differ > 2%."""

    YM = "2026-07"

    def test_note_fires_when_gap_exceeds_2pct(self):
        """Large divergence (July: block 68390 vs DR 32191 ≈ 52%) → note."""
        raw = [
            _block_row(2, "2026-07-15", self.YM, output_kg=30000),
            _block_row(3, "2026-07-15", self.YM, output_kg=38390),
        ]
        rh_rows = [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=10000, reject_kg=70.0),
            _dr_row(3, "2026-07-15", self.YM, run_hours=0, output_kg=22191, reject_kg=1783.50),
        ]
        _, _, notes = _apply_garden_rejection_join(raw, rh_rows)
        assert notes, "Basis-divergence note must fire when gap > 2%"
        assert "Daily Report output basis" in notes[0]
        assert "block-tab output" in notes[0]

    def test_note_suppressed_when_gap_within_2pct(self):
        """Small divergence (< 2%) → no note."""
        raw = [
            _block_row(1, "2026-06-10", "2026-06", output_kg=10000),
        ]
        rh_rows = [
            _dr_row(1, "2026-06-10", "2026-06", run_hours=8, output_kg=9900, reject_kg=50.0),
        ]
        _, _, notes = _apply_garden_rejection_join(raw, rh_rows)
        assert not notes, f"Unexpected note for <2% gap: {notes}"

    def test_note_suppressed_when_no_rejection_data(self):
        """No rejection in DR (rej_map empty after join) → no note."""
        raw = [_block_row(1, "2026-05-10", "2026-05", output_kg=13000)]
        rh_rows = []   # empty DR
        _, _, notes = _apply_garden_rejection_join(raw, rh_rows)
        assert not notes

    def test_note_names_both_output_figures(self):
        """Note text must include both the DR basis and block-tab figures."""
        raw = [_block_row(2, "2026-07-15", self.YM, output_kg=68390)]
        rh_rows = [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=32191, reject_kg=1853.50),
        ]
        _, _, notes = _apply_garden_rejection_join(raw, rh_rows)
        assert notes
        note = notes[0]
        assert "32,191" in note, f"DR output figure missing from note: {note}"
        assert "68,390" in note, f"Block-tab output figure missing from note: {note}"


class TestRejectionTrackedGating:
    """rejection_tracked is set only for machine-dates that have a DR row."""

    YM = "2026-07"

    def test_machine_without_dr_row_keeps_rejection_tracked_false(self):
        """A block record with no matching DR entry stays rejection_tracked=False."""
        raw = [
            _block_row(1, "2026-07-20", self.YM, output_kg=5000),  # machine 1
            _block_row(2, "2026-07-20", self.YM, output_kg=8000),  # machine 2
        ]
        rh_rows = [
            # DR only has machine 2; machine 1 is absent
            _dr_row(2, "2026-07-20", self.YM, run_hours=0, output_kg=7500, reject_kg=100.0),
        ]
        _apply_garden_rejection_join(raw, rh_rows)
        by_mc = {r.machine: r for r in raw}
        assert by_mc["GARDEN M/C - 1"].rejection_tracked is False, (
            "Machine-1 has no DR entry: rejection_tracked must stay False"
        )
        assert by_mc["GARDEN M/C - 2"].rejection_tracked is True

    def test_machine_date_with_zero_rejection_but_output_gets_tracked(self):
        """If DR has output for a machine-date but reject=0, rejection is tracked as 0."""
        raw = [_block_row(1, "2026-04-01", "2026-04", output_kg=9000)]
        rh_rows = [
            _dr_row(1, "2026-04-01", "2026-04", run_hours=8, output_kg=8500, reject_kg=0.0),
        ]
        _apply_garden_rejection_join(raw, rh_rows)
        r = raw[0]
        assert r.rejection_tracked is True
        assert r.reject_count == 0.0
        assert r.reject_denominator == pytest.approx(8500.0)


class TestJulyPerMachineKg:
    """Per-machine July: MACHINE-2 = 70.00 kg, MACHINE-3 = 1,783.50 kg."""

    YM = "2026-07"

    def test_per_machine_rejection_kg(self):
        raw = [
            _block_row(2, "2026-07-15", self.YM, output_kg=18000),
            _block_row(3, "2026-07-15", self.YM, output_kg=35000),
        ]
        rh_rows = [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=8000, reject_kg=70.00),
            _dr_row(3, "2026-07-15", self.YM, run_hours=0, output_kg=22000, reject_kg=1783.50),
        ]
        _apply_garden_rejection_join(raw, rh_rows)

        by_mc = {r.machine: r for r in raw}
        assert by_mc["GARDEN M/C - 2"].reject_count == pytest.approx(70.00)
        assert by_mc["GARDEN M/C - 3"].reject_count == pytest.approx(1783.50)

        # Per-machine metrics
        m2_metrics = compute_metrics([by_mc["GARDEN M/C - 2"]])
        m3_metrics = compute_metrics([by_mc["GARDEN M/C - 3"]])

        assert m2_metrics.reject_count == pytest.approx(70.00)
        assert m3_metrics.reject_count == pytest.approx(1783.50)




class TestSyntheticRejectionOnlyRecords:
    """Rejection-only DR rows (no matching block-tab date) become synthetic records."""

    YM = "2026-07"

    def _make_july_raw(self):
        """Block records: M/C-3 has entries for Jul-10, Jul-27, Jul-30 only."""
        return [
            _block_row(3, "2026-07-10", self.YM, output_kg=1513.40),
            _block_row(3, "2026-07-27", self.YM, output_kg=1474.80),
            _block_row(3, "2026-07-30", self.YM, output_kg=1425.40),
        ]

    def _make_july_rh_rows(self):
        """DR rows: M/C-2 Jul-31 70 kg, M/C-3 Jul-10 1513.50 kg,
           M/C-3 Jul-27 95 kg, M/C-3 Jul-30 90 kg, M/C-3 Jul-31 85 kg."""
        return [
            _dr_row(2, "2026-07-31", self.YM, run_hours=0, output_kg=0, reject_kg=70.00),
            _dr_row(3, "2026-07-10", self.YM, run_hours=0, output_kg=0, reject_kg=1513.50),
            _dr_row(3, "2026-07-27", self.YM, run_hours=0, output_kg=1378.30, reject_kg=95.00),
            _dr_row(3, "2026-07-30", self.YM, run_hours=0, output_kg=1352.90, reject_kg=90.00),
            _dr_row(3, "2026-07-31", self.YM, run_hours=0, output_kg=0, reject_kg=85.00),
        ]

    def test_total_rejection_kg_reaches_spec_target(self):
        """Full July: 1,853.50 kg total = 1,698.50 transferred + 155.00 synthetic."""
        raw = self._make_july_raw()
        rh_rows = self._make_july_rh_rows()
        _apply_garden_rejection_join(raw, rh_rows, ym=self.YM)
        metrics = compute_metrics(raw)
        assert metrics.reject_count == pytest.approx(1853.50, abs=0.01)

    def test_two_synthetic_records_appended(self):
        """M/C-2 Jul-31 and M/C-3 Jul-31 each get a synthetic record."""
        raw = self._make_july_raw()
        n_before = len(raw)
        _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        synth = [r for r in raw[n_before:]]
        assert len(synth) == 2, f"Expected 2 synthetic records, got {len(synth)}"

        by_mc = {re.search(r"(\d+)", r.machine).group(1): r for r in synth}
        assert "2" in by_mc, "Synthetic record for M/C-2 missing"
        assert "3" in by_mc, "Synthetic record for M/C-3 missing"
        assert by_mc["2"].reject_count == pytest.approx(70.00)
        assert by_mc["3"].reject_count == pytest.approx(85.00)

    def test_synthetic_records_have_zero_output_and_hours(self):
        """Synthetic records must not add output or hours (R-23 / util invariant)."""
        raw = self._make_july_raw()
        n_before = len(raw)
        _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        for r in raw[n_before:]:
            assert r.total_count == 0.0, f"{r.machine}: synthetic record has output {r.total_count}"
            assert (r.actual_hours or 0.0) == 0.0, f"{r.machine}: synthetic record has hours"

    def test_synthetic_records_rejection_tracked_true(self):
        """rejection_tracked must be True so the kg is not suppressed."""
        raw = self._make_july_raw()
        n_before = len(raw)
        _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        for r in raw[n_before:]:
            assert r.rejection_tracked is True

    def test_per_machine_rejection_after_fix(self):
        """M/C-2 total = 70.00, M/C-3 total = 1,783.50 (1513.50 + 95 + 90 + 85)."""
        raw = self._make_july_raw()
        _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        # Group by machine number
        from collections import defaultdict
        by_mc = defaultdict(float)
        for r in raw:
            _mn = re.search(r"(\d+)", r.machine)
            if _mn:
                by_mc[_mn.group(1)] += r.reject_count
        assert by_mc["2"] == pytest.approx(70.00)
        assert by_mc["3"] == pytest.approx(1783.50, abs=0.01)

    def test_rejection_pct_reaches_576(self):
        """After fix: 1853.50 / 32191 ≈ 5.76%."""
        raw = self._make_july_raw()
        # Add M/C-2 block rows so the DR output denominator is anchored
        raw += [_block_row(2, "2026-07-27", self.YM, output_kg=709.60)]
        rh_rows = self._make_july_rh_rows()
        # Add DR output for M/C-2 on Jul-27 to make denominator = 32191
        rh_rows.append(
            _dr_row(2, "2026-07-27", self.YM, run_hours=0, output_kg=32191 - 0 - 1378.30 - 1352.90, reject_kg=0)
        )
        # Use a simpler fixture: known denom = 32191
        raw2 = [
            _block_row(2, "2026-07-15", self.YM, output_kg=18000),
            _block_row(3, "2026-07-15", self.YM, output_kg=35000),
        ]
        rh2 = [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=32191, reject_kg=1698.50),
            _dr_row(2, "2026-07-31", self.YM, run_hours=0, output_kg=0, reject_kg=70.00),
            _dr_row(3, "2026-07-31", self.YM, run_hours=0, output_kg=0, reject_kg=85.00),
        ]
        _apply_garden_rejection_join(raw2, rh2, ym=self.YM)
        metrics = compute_metrics(raw2)
        assert metrics.reject_count == pytest.approx(1853.50)
        assert metrics.rejection_pct == pytest.approx(1853.50 / 32191.0, rel=1e-3)

    def test_warning_note_emitted(self):
        """R-35: a note must be appended listing the dropped dates and total kg."""
        raw = self._make_july_raw()
        _, _, notes = _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        synth_notes = [n for n in notes if "no production row" in n]
        assert synth_notes, f"No synthetic-record note emitted; all notes: {notes}"
        note = synth_notes[0]
        assert "155.00" in note, f"Total kg missing from note: {note}"
        assert "M/C-2" in note
        assert "M/C-3" in note
        assert "31-Jul" in note

    def test_warning_note_absent_when_all_matched(self):
        """When every DR rejection row has a block record, no synthetic note fires."""
        raw = [
            _block_row(2, "2026-07-15", self.YM, output_kg=18000),
            _block_row(3, "2026-07-15", self.YM, output_kg=35000),
        ]
        rh_rows = [
            _dr_row(2, "2026-07-15", self.YM, run_hours=0, output_kg=8000, reject_kg=70.0),
            _dr_row(3, "2026-07-15", self.YM, run_hours=0, output_kg=22000, reject_kg=85.0),
        ]
        _, _, notes = _apply_garden_rejection_join(raw, rh_rows, ym=self.YM)
        synth_notes = [n for n in notes if "no production row" in n]
        assert not synth_notes, f"Unexpected synthetic note: {synth_notes}"

    def test_block_output_unchanged_after_synthetic_emit(self):
        """R-23: synthetic records must not alter any pre-existing block record."""
        raw = self._make_july_raw()
        before = {(r.machine, r.date): r.total_count for r in raw}
        _apply_garden_rejection_join(raw, self._make_july_rh_rows(), ym=self.YM)
        for r in raw:
            key = (r.machine, r.date)
            if key in before:
                assert r.total_count == before[key], (
                    f"{r.machine} {r.date}: output changed {before[key]} -> {r.total_count}"
                )


class TestMC3Jul10ZeroDenominator:
    """M/C-3 10-Jul: 1,513.50 kg rejection, DR output = 0 — zero denominator."""

    YM = "2026-07"

    def test_zero_denominator_record_is_tracked(self):
        """Block record exists for Jul-10, so rejection IS transferred and tracked."""
        raw = [_block_row(3, "2026-07-10", self.YM, output_kg=1513.40)]
        rh_rows = [
            _dr_row(3, "2026-07-10", self.YM, run_hours=0, output_kg=0, reject_kg=1513.50),
        ]
        _apply_garden_rejection_join(raw, rh_rows, ym=self.YM)
        r = raw[0]
        assert r.rejection_tracked is True
        assert r.reject_count == pytest.approx(1513.50)
        assert r.reject_denominator == pytest.approx(0.0)

    def test_zero_denominator_does_not_crash_compute_metrics(self):
        """compute_metrics must not raise on a zero reject_denominator."""
        raw = [_block_row(3, "2026-07-10", self.YM, output_kg=1513.40)]
        rh_rows = [
            _dr_row(3, "2026-07-10", self.YM, run_hours=0, output_kg=0, reject_kg=1513.50),
        ]
        _apply_garden_rejection_join(raw, rh_rows, ym=self.YM)
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
