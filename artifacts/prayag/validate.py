"""
Reconcile & validate step.
Checks computed totals against source and flags impossible values.
Numbers NEVER go through an AI model.
"""
from __future__ import annotations
from typing import List, Tuple
from metrics import Record, MetricsResult


ValidationWarning = str

TOLERANCE = 0.01            # tiny float drift for self-sum reconciliation
TOTAL_TOLERANCE_PCT = 0.02  # 2% tolerance vs the sheet's own TOTAL row


def validate_rows(rows: List[Record]) -> List[ValidationWarning]:
    """Row-level validation: impossible values in individual records."""
    warnings = []
    for r in rows:
        period = r.period or r.date
        label = f"{period} {r.plant}/{r.machine or r.mould}".strip()

        if r.downtime_min < 0:
            warnings.append(f"[{label}] Negative downtime ({r.downtime_min} min)")

        ppt = r.shift_len_min - r.planned_stops_min
        if r.grain == "daily" and r.downtime_min > ppt > 0:
            warnings.append(
                f"[{label}] Downtime ({r.downtime_min} min) exceeds PPT ({ppt:.0f} min)"
            )

        if r.reject_count > r.total_count and r.total_count > 0:
            warnings.append(
                f"[{label}] Rejects ({r.reject_count:.0f}) exceed output ({r.total_count:.0f})"
            )

        if r.total_count < 0:
            warnings.append(f"[{label}] Negative output ({r.total_count})")
        if r.reject_count < 0:
            warnings.append(f"[{label}] Negative reject count ({r.reject_count})")

        # Monthly hours sanity: actual hours should not exceed ideal (>100% util)
        if r.grain == "monthly" and r.ideal_hours > 0 and r.actual_hours > r.ideal_hours * 1.001:
            warnings.append(
                f"[{label}] Actual hours ({r.actual_hours:.0f}) exceed ideal "
                f"({r.ideal_hours:.0f}) — utilisation over 100%, check source"
            )

    return warnings


def validate_metrics(m: MetricsResult) -> List[ValidationWarning]:
    """Aggregate-level checks on a MetricsResult."""
    warnings = []

    if m.oee_available and m.performance > 1.0:
        warnings.append(
            f"Performance ({m.performance_pct:.1f}%) exceeds 100% — check ideal rates"
        )
    if m.oee_available and not (0 <= m.availability <= 1.0):
        warnings.append(f"Availability out of range: {m.availability_pct:.1f}%")
    if m.oee_available and not (0 <= m.quality <= 1.0):
        warnings.append(f"Quality out of range: {m.quality_pct:.1f}%")

    if m.utilisation > 1.0:
        warnings.append(
            f"Utilisation ({m.utilisation_pct:.1f}%) over 100% — actual hours exceed "
            "ideal; treat as a data-quality flag, not a real KPI"
        )
    if m.output_efficiency > 1.0:
        warnings.append(
            f"Output efficiency ({m.output_efficiency_pct:.1f}%) over 100% — "
            "actual output exceeds ideal; check source figures"
        )

    if m.rejection_pct > 0.5:
        warnings.append(f"Very high rejection rate: {m.rejection_pct_display:.1f}%")

    return warnings


def reconcile(rows: List[Record], computed: MetricsResult) -> Tuple[bool, List[ValidationWarning]]:
    """Assert engine totals match source-row sums (internal self-check)."""
    warnings: List[ValidationWarning] = []

    src_total = sum(r.total_count for r in rows)
    src_reject = sum(r.reject_count for r in rows)

    ok = True
    if abs(computed.total_count - src_total) > TOLERANCE:
        warnings.append(
            f"RECONCILE FAIL — computed output {computed.total_count:.2f} ≠ row sum {src_total:.2f}"
        )
        ok = False
    if abs(computed.reject_count - src_reject) > TOLERANCE:
        warnings.append(
            f"RECONCILE FAIL — computed rejects {computed.reject_count:.2f} ≠ row sum {src_reject:.2f}"
        )
        ok = False

    return ok, warnings


def full_validate(
    rows: List[Record],
    computed: MetricsResult,
    extra_warnings: List[ValidationWarning] | None = None,
) -> dict:
    """Run all validation steps; return a status dict.

    ``extra_warnings`` carries reconciliation notes raised at read time when the
    summed month rows disagree with the sheet's own TOTAL row.
    """
    row_warnings = validate_rows(rows)
    metric_warnings = validate_metrics(computed)
    reconciled, reconcile_warnings = reconcile(rows, computed)
    source_warnings = list(extra_warnings or [])

    if source_warnings:
        reconciled = False

    all_warnings = row_warnings + metric_warnings + reconcile_warnings + source_warnings

    return {
        "reconciled": reconciled,
        "flag_count": len(all_warnings),
        "warnings": all_warnings,
        "row_warnings": row_warnings,
        "metric_warnings": metric_warnings,
        "reconcile_warnings": reconcile_warnings,
        "source_warnings": source_warnings,
    }
