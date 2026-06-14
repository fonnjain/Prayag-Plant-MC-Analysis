"""Regression tests for the per-machine planned-hours baseline mechanism.

The baseline (baselines.json, resolved by ``baselines.resolve``) is the
denominator for utilisation/output-efficiency. It must:
  * match a machine code case- and whitespace-insensitively,
  * return None for machines with no entry (caller suppresses the ratio),
  * be applied in place by ``app._apply_baselines`` so the USED ideal_hours
    becomes the config value while the original sheet value is preserved and
    the provenance is stamped — and the read output/actual hours are untouched,
  * surface as a per-plant WARNING (no baseline) + an INFO (config applied)
    via ``confirm.baseline_confirm``.

No real planned-hours baselines are available for PIPE/MOULDING (no shift-pattern
data), so baselines.json ships with NO machine entries — estimates and the sheet's
flat 500-h placeholder must never be entered. These tests therefore inject a
temporary in-memory baseline to exercise the mechanism without shipping an estimate.

Run: cd artifacts/prayag && python3 -m tests.test_baselines
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import baselines
from metrics import Record, compute_metrics
from confirm import baseline_confirm, WARNING, INFO
import app


def _inject(planned_hours: float = 624.0, machine: str = "MOULDING M/C - 4") -> None:
    """Force a single in-memory config baseline (no file edit, no estimate shipped)."""
    baselines._cache = {
        "machines": {
            baselines._norm(machine): {
                "planned_hours": planned_hours,
                "ideal_output_rate": None,
                "overrides": {},
                "basis": "test fixture",
            }
        },
        "formula_defaults": {},
    }


def _monthly(machine: str, actual_hours: float, ideal_hours: float = 500.0,
             output: float = 1000.0, plant: str = "MOULDING") -> Record:
    return Record(
        grain="monthly",
        plant=plant,
        machine=machine,
        period="2026-05",
        actual_hours=actual_hours,
        ideal_hours=ideal_hours,
        ideal_output=output,
        total_count=output,
    )


def test_shipped_baselines_have_no_estimates():
    # The team has no shift-pattern data, so we must NOT ship estimated baselines.
    baselines.reload()
    cfg = baselines._load()
    assert cfg["machines"] == {}, cfg["machines"]
    assert baselines.resolve("MOULDING", "MOULDING M/C - 4", "2026-05") is None
    assert baselines.resolve("PIPE", "PIPE M/C - 4", "2026-05") is None
    print("PASS: baselines.json ships no estimated entries (PIPE/MOULDING = baseline not set)")


def test_resolve_matches_and_misses():
    _inject(624.0)
    try:
        # Whitespace/case-insensitive match against the injected entry.
        for code in ("MOULDING M/C - 4", "moulding m/c-4", "MOULDING M/C-4"):
            b = baselines.resolve("MOULDING", code, "2026-05")
            assert b and b["planned_hours"] == 624.0 and b["source"] == "config", code
        # Unseeded machine and other plant → no baseline.
        assert baselines.resolve("MOULDING", "MOULDING M/C - 1", "2026-05") is None
        assert baselines.resolve("PIPE", "PIPE M/C - 4", "2026-05") is None
    finally:
        baselines.reload()
    print("PASS: resolve matches an injected baseline (ws/case-insensitive), misses others")


def test_apply_baselines_overrides_denominator_only():
    _inject(624.0)
    try:
        rows = [
            _monthly("MOULDING M/C - 4", actual_hours=507.0, output=9000.0),
            _monthly("MOULDING M/C - 1", actual_hours=327.0, output=5000.0),
        ]
        app._apply_baselines(rows)
        seeded, unseeded = rows[0], rows[1]
        # Seeded: USED ideal becomes the config baseline; sheet value preserved.
        assert seeded.ideal_source == "config"
        assert seeded.ideal_hours == 624.0
        assert seeded.ideal_hours_sheet == 500.0
        # The READ facts (actual hours, output) are never touched.
        assert seeded.actual_hours == 507.0 and seeded.total_count == 9000.0
        # Unseeded: stays on the sheet placeholder, flagged as such.
        assert unseeded.ideal_source == "sheet"
        assert unseeded.ideal_hours == 500.0 and unseeded.ideal_hours_sheet == 500.0
    finally:
        baselines.reload()
    print("PASS: baseline overrides only the ideal-hours denominator + provenance")


def test_baseline_clears_over_100_utilisation():
    _inject(624.0)
    try:
        # 507 actual vs the sheet's 500 placeholder is >100% utilisation (a flag);
        # against the real 624h baseline it is a healthy 81%.
        row = _monthly("MOULDING M/C - 4", actual_hours=507.0)
        before = compute_metrics([row])
        assert before.utilisation_pct > 100.0
        app._apply_baselines([row])
        after = compute_metrics([row])
        assert after.utilisation_pct < 100.0
    finally:
        baselines.reload()
    print("PASS: a real baseline turns a spurious >100% utilisation into a real figure")


def test_baseline_confirm_warns_and_notes():
    _inject(624.0)
    try:
        rows = [
            _monthly("MOULDING M/C - 4", actual_hours=507.0),
            _monthly("MOULDING M/C - 1", actual_hours=327.0),
            _monthly("MOULDING M/C - 2", actual_hours=280.0),
        ]
        app._apply_baselines(rows)
        issues = baseline_confirm(rows)
        warns = [i for i in issues if i["severity"] == WARNING]
        infos = [i for i in issues if i["severity"] == INFO]
        assert len(warns) == 1 and "2 machine(s)" in warns[0]["message"], warns
        assert warns[0]["plant"] == "MOULDING"
        assert len(infos) == 1 and "MOULDING M/C - 4" in infos[0]["message"], infos
    finally:
        baselines.reload()
    print("PASS: baseline_confirm emits a per-plant WARNING + a config-applied INFO")


if __name__ == "__main__":
    test_shipped_baselines_have_no_estimates()
    test_resolve_matches_and_misses()
    test_apply_baselines_overrides_denominator_only()
    test_baseline_clears_over_100_utilisation()
    test_baseline_confirm_warns_and_notes()
    print("\nAll baseline regression tests passed.")
