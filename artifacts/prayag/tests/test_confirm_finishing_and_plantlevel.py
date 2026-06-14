"""Regression tests for the daily-data honesty fixes in Data Confirmation.

Covers three defects introduced when every daily plant began serving true daily
data (GARDEN/HDPE blocks, TANK plant-level, PTMT process groups + grinder KG):

  1. Tier-2 engine self-reconcile must mirror ``compute_metrics`` row selection —
     when a set mixes production with finishing/regrind rows the published plant
     total counts production only, so the row sum used to self-reconcile must
     exclude finishing rows too (else a false ERROR for every PTMT page).
  2. Tier-2 segment-vs-lines hierarchy must skip a segment whose output is
     reported entirely without a machine identity (TANK, logged per item) — there
     are no "lines" to reconcile against, so it is not a mismatch.
  3. Tier-4 outliers must be measured WITHIN a (plant, segment) process group, not
     plant-wide, so PTMT's incomparable processes (Injection vs Blow vs Grinding)
     don't flag whole processes as outliers.

Run: cd artifacts/prayag && python3 -m tests.test_confirm_finishing_and_plantlevel
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import Record, compute_metrics
from confirm import tier2_reconciliation, tier4_plausibility, ERROR, WARNING


def _row(plant, machine, total, *, segment="", mould="", unit="kg",
         is_finishing=False, period="2026-06"):
    return Record(
        grain="daily",
        plant=plant,
        machine=machine,
        mould=mould,
        segment=segment,
        unit=unit,
        period=period,
        date="2026-06-01",
        total_count=float(total),
        reject_count=0.0,
        is_finishing=is_finishing,
    )


def test_self_reconcile_excludes_finishing_rows():
    # A mixed set: real production + a regrind (finishing) row. The published
    # plant total excludes the regrind, so the self-reconcile row sum must too.
    rows = [
        _row("PTMT", "N-1", 900, segment="PTMT \u2013 Injection (N-line)"),
        _row("PTMT", "B-1", 600, segment="PTMT \u2013 Blow Moulding"),
        _row("PTMT", "GRINDER-1", 500, segment="PTMT \u2013 Grinding",
             is_finishing=True),
    ]
    computed = compute_metrics(rows)
    assert abs(computed.total_count - 1500.0) < 1e-9, (
        f"plant total must exclude the 500 regrind: {computed.total_count}")

    issues = tier2_reconciliation([], rows, computed)
    self_err = [i for i in issues
                if i["severity"] == ERROR and "Internal mismatch" in i["message"]]
    assert not self_err, (
        f"self-reconcile must not false-flag a mixed finishing set: {self_err}")
    print("PASS: tier-2 self-reconcile excludes finishing rows (no false error)")


def test_pure_finishing_set_reconciles_to_itself():
    # A grinding-only view: no production rows, so the finishing rows ARE the set.
    rows = [
        _row("PTMT", "GRINDER-1", 500, segment="PTMT \u2013 Grinding",
             is_finishing=True),
        _row("PTMT", "GRINDER-2", 300, segment="PTMT \u2013 Grinding",
             is_finishing=True),
    ]
    computed = compute_metrics(rows)
    assert abs(computed.total_count - 800.0) < 1e-9, (
        f"pure-finishing view shows its own total: {computed.total_count}")
    issues = tier2_reconciliation([], rows, computed)
    self_err = [i for i in issues
                if i["severity"] == ERROR and "Internal mismatch" in i["message"]]
    assert not self_err, f"pure-finishing set must reconcile to itself: {self_err}"
    print("PASS: tier-2 self-reconcile holds for a pure-finishing set")


def test_machine_less_segment_is_not_a_hierarchy_mismatch():
    # TANK output is logged per item with no machine identity. Its segment total
    # has no machine "lines" to reconcile against, so it must NOT be flagged.
    rows = [
        _row("TANK", "", 200, segment="Tank", mould="WT-3LL-05", unit="pcs"),
        _row("TANK", "", 165, segment="Tank", mould="WT-4LL-10", unit="pcs"),
    ]
    computed = compute_metrics(rows)
    issues = tier2_reconciliation([], rows, computed)
    seg_mismatch = [i for i in issues if "sum of its lines" in i["message"]]
    assert not seg_mismatch, (
        f"machine-less segment must not be a hierarchy mismatch: {seg_mismatch}")
    print("PASS: tier-2 skips segment-vs-lines for a machine-less (TANK) segment")


def test_machine_bearing_segment_still_reconciles():
    # A real machine-bearing segment whose total disagrees with its lines MUST
    # still be flagged — the machine-less skip must not silence genuine breaks.
    # Two rows for the SAME machine on different dates so seg total == lines sum,
    # then add an orphan-to-segment total via a row with the segment but the
    # machine total deliberately short is impossible here; instead inject a
    # mismatch by giving the segment a row with no machine PLUS machine rows.
    rows = [
        _row("PIPE", "M/C-1", 700, segment="Pipe"),
        _row("PIPE", "M/C-2", 500, segment="Pipe"),
        _row("PIPE", "", 300, segment="Pipe"),  # segment output w/o a line
    ]
    computed = compute_metrics(rows)
    issues = tier2_reconciliation([], rows, computed)
    seg_mismatch = [i for i in issues if "sum of its lines" in i["message"]]
    assert seg_mismatch, (
        "a segment with machine lines whose total exceeds the lines sum must "
        f"still flag a mismatch: {issues}")
    print("PASS: tier-2 still flags a genuine machine-bearing segment mismatch")


def test_outliers_are_scoped_within_a_process_segment():
    # Two processes in one plant with very different output scales. Plant-wide
    # this would flag the whole small-output process as outliers; scoped within
    # each segment, the consistent members are fine and only a true within-group
    # outlier is flagged.
    rows = [
        # Big-output process — all comparable.
        _row("PTMT", "B-1", 1000, segment="PTMT \u2013 Blow Moulding"),
        _row("PTMT", "B-2", 1100, segment="PTMT \u2013 Blow Moulding"),
        _row("PTMT", "B-3", 900, segment="PTMT \u2013 Blow Moulding"),
        # Small-output process — all comparable to each other, tiny vs Blow.
        _row("PTMT", "N-1", 100, segment="PTMT \u2013 Injection (N-line)"),
        _row("PTMT", "N-2", 110, segment="PTMT \u2013 Injection (N-line)"),
        _row("PTMT", "N-3", 90, segment="PTMT \u2013 Injection (N-line)"),
    ]
    issues = tier4_plausibility(rows, [], ["2026-06"], {}, daily_used=True)
    outliers = [i for i in issues if "looks like an outlier" in i["message"]]
    assert not outliers, (
        f"no machine should be a within-segment outlier here: {outliers}")

    # Now make ONE N-line machine a genuine within-group outlier (10× its peers).
    rows.append(_row("PTMT", "N-4", 1000, segment="PTMT \u2013 Injection (N-line)"))
    issues2 = tier4_plausibility(rows, [], ["2026-06"], {}, daily_used=True)
    n4 = [i for i in issues2
          if "looks like an outlier" in i["message"] and "N-4" in i["message"]]
    assert n4, f"a true within-segment outlier must still be flagged: {issues2}"
    assert all("Injection (N-line) median" in i["message"] for i in n4), (
        f"outlier must be compared to its OWN process median: {n4}")
    print("PASS: tier-4 outliers are scoped within (plant, segment) process groups")


if __name__ == "__main__":
    test_self_reconcile_excludes_finishing_rows()
    test_pure_finishing_set_reconciles_to_itself()
    test_machine_less_segment_is_not_a_hierarchy_mismatch()
    test_machine_bearing_segment_still_reconciles()
    test_outliers_are_scoped_within_a_process_segment()
    print("\nAll finishing/plant-level confirmation regression tests passed.")
