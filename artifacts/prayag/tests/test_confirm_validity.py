"""Regression tests for the deterministic Data Confirmation validity tier.

Run: cd artifacts/prayag && python3 -m tests.test_confirm_validity
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import Record, compute_metrics
from confirm import tier3_validity, ERROR


def _oee_row(total_count: float, ideal_rate: float) -> Record:
    """A daily OEE shift-log row: 60 min shift, no planned stops, no downtime,
    so production time = 60 min = 1 hr and theoretical ideal output = ideal_rate."""
    return Record(
        grain="daily",
        plant="PIPE",
        machine="M/C-1",
        period="2025-04",
        date="2025-04-01",
        total_count=total_count,
        reject_count=0.0,
        has_oee=True,
        shift_len_min=60.0,
        planned_stops_min=0.0,
        downtime_min=0.0,
        ideal_rate=ideal_rate,
    )


def test_performance_over_100_is_a_tier3_error():
    # Output (1500) far exceeds the theoretical ideal (1000/hr * 1hr) → raw
    # performance = 150%. The displayed performance is clamped to 100%, but the
    # validity tier must still flag the impossible raw value as an ERROR.
    rows = [_oee_row(total_count=1500.0, ideal_rate=1000.0)]
    computed = compute_metrics(rows)

    assert computed.oee_available, "fixture should be OEE-capable"
    assert abs(computed.performance - 1.0) < 1e-9, "displayed performance is clamped"
    assert computed.performance_raw > 1.0, "raw performance must be unclamped"

    issues = tier3_validity(rows, computed)
    perf_errors = [
        i for i in issues
        if i["severity"] == ERROR and "Performance" in i["message"] and "100%" in i["message"]
    ]
    assert perf_errors, f"expected a Tier-3 performance>100% error, got: {issues}"
    print("PASS: performance>100% produces a Tier-3 error")


def test_clean_performance_has_no_error():
    rows = [_oee_row(total_count=800.0, ideal_rate=1000.0)]
    computed = compute_metrics(rows)
    issues = tier3_validity(rows, computed)
    perf_errors = [i for i in issues if "Performance" in i["message"]]
    assert not perf_errors, f"clean data should not flag performance: {perf_errors}"
    print("PASS: clean performance produces no error")


def test_rejects_exceed_zero_output_is_a_tier3_error():
    # Output is 0 but rejects are 5 — impossible regardless of zero output.
    rows = [Record(grain="monthly", plant="PIPE", machine="M/C-1",
                   period="2025-04", total_count=0.0, reject_count=5.0)]
    computed = compute_metrics(rows)
    issues = tier3_validity(rows, computed)
    rej_errors = [
        i for i in issues if i["severity"] == ERROR and "Rejects" in i["message"]
    ]
    assert rej_errors, f"expected a rejects>output error, got: {issues}"
    print("PASS: rejects exceeding zero output produces a Tier-3 error")


if __name__ == "__main__":
    test_performance_over_100_is_a_tier3_error()
    test_clean_performance_has_no_error()
    test_rejects_exceed_zero_output_is_a_tier3_error()
    print("\nAll validity regression tests passed.")
