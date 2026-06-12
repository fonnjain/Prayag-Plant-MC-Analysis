"""
Reconcile & validate step.
Checks computed totals against source and flags impossible values.
Numbers NEVER go through an AI model.
"""
from __future__ import annotations
from typing import List, Tuple
from metrics import ShiftRow, MetricsResult


ValidationWarning = str


def validate_rows(rows: List[ShiftRow]) -> List[ValidationWarning]:
    """Row-level validation: impossible values in individual shift records."""
    warnings = []
    for i, r in enumerate(rows):
        label = f"{r.date} {r.plant}/{r.machine} shift-{r.shift}"

        if r.downtime_min < 0:
            warnings.append(f"[{label}] Negative downtime ({r.downtime_min} min)")

        ppt = r.shift_len_min - r.planned_stops_min
        if r.downtime_min > ppt > 0:
            warnings.append(
                f"[{label}] Downtime ({r.downtime_min} min) exceeds PPT ({ppt:.0f} min)"
            )

        if r.reject_count > r.total_count:
            warnings.append(
                f"[{label}] Rejects ({r.reject_count}) exceed total count ({r.total_count})"
            )

        if r.total_count < 0:
            warnings.append(f"[{label}] Negative total count ({r.total_count})")

        if r.reject_count < 0:
            warnings.append(f"[{label}] Negative reject count ({r.reject_count})")

        if r.ideal_rate <= 0:
            warnings.append(f"[{label}] Zero or negative ideal rate")

    return warnings


def validate_metrics(m: MetricsResult) -> List[ValidationWarning]:
    """Aggregate-level checks on a MetricsResult."""
    warnings = []

    if m.performance > 1.0:
        warnings.append(
            f"Performance ({m.performance_pct:.1f}%) exceeds 100% — check ideal rates"
        )

    if m.availability < 0 or m.availability > 1.0:
        warnings.append(f"Availability out of range: {m.availability_pct:.1f}%")

    if m.quality < 0 or m.quality > 1.0:
        warnings.append(f"Quality out of range: {m.quality_pct:.1f}%")

    if m.rejection_pct > 0.5:
        warnings.append(f"Very high rejection rate: {m.rejection_pct_display:.1f}%")

    return warnings


def reconcile(rows: List[ShiftRow], computed: MetricsResult) -> Tuple[bool, List[ValidationWarning]]:
    """
    Assert that engine totals match source-row sums.
    Returns (reconciled: bool, warnings: list).
    """
    warnings: List[ValidationWarning] = []

    src_total = sum(r.total_count for r in rows)
    src_reject = sum(r.reject_count for r in rows)
    src_downtime = sum(r.downtime_min for r in rows)

    TOLERANCE = 0.01  # allow tiny float drift

    ok = True
    if abs(computed.total_count - src_total) > TOLERANCE:
        warnings.append(
            f"RECONCILE FAIL — computed output {computed.total_count:.2f} ≠ source sum {src_total:.2f}"
        )
        ok = False

    if abs(computed.reject_count - src_reject) > TOLERANCE:
        warnings.append(
            f"RECONCILE FAIL — computed rejects {computed.reject_count:.2f} ≠ source sum {src_reject:.2f}"
        )
        ok = False

    if abs(computed.downtime_min - src_downtime) > TOLERANCE:
        warnings.append(
            f"RECONCILE FAIL — computed downtime {computed.downtime_min:.1f} ≠ source sum {src_downtime:.1f}"
        )
        ok = False

    return ok, warnings


def full_validate(rows: List[ShiftRow], computed: MetricsResult) -> dict:
    """Run all validation steps; return a status dict."""
    row_warnings = validate_rows(rows)
    metric_warnings = validate_metrics(computed)
    reconciled, reconcile_warnings = reconcile(rows, computed)

    all_warnings = row_warnings + metric_warnings + reconcile_warnings

    return {
        "reconciled": reconciled,
        "flag_count": len(all_warnings),
        "warnings": all_warnings,
        "row_warnings": row_warnings,
        "metric_warnings": metric_warnings,
        "reconcile_warnings": reconcile_warnings,
    }
