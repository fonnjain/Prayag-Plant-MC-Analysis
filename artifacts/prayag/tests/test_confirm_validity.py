"""Regression tests for the deterministic Data Confirmation validity tier.

Tier 3 is split into:
  * ``tier3_row_classify(rows) -> (clean, quarantined, issues)`` — physically
    impossible rows are QUARANTINED (held aside, excluded from published metrics);
    above-baseline-but-possible values are warnings that never quarantine.
  * ``tier3_aggregate(computed) -> issues`` — ratio-over-100% on the published
    metrics, downgraded to WARNING (possible, not impossible).

Run: cd artifacts/prayag && python3 -m tests.test_confirm_validity
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import Record, compute_metrics
from confirm import tier3_row_classify, tier3_aggregate, ERROR, WARNING


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


def _monthly_hours_row(actual_hours: float, ideal_hours: float,
                       period: str = "2026-05") -> Record:
    """A monthly grid row carrying logged vs planned run-hours directly."""
    return Record(
        grain="monthly",
        plant="PIPE",
        machine="M/C-4",
        period=period,
        total_count=1000.0,
        reject_count=0.0,
        actual_hours=actual_hours,
        ideal_hours=ideal_hours,
    )


def test_aggregate_performance_over_100_is_a_warning():
    # Output (1500) far exceeds the theoretical ideal (1000/hr * 1hr) → raw
    # performance = 150%. Displayed performance is clamped to 100%, but the
    # aggregate check flags the impossible raw value — now a WARNING, not an error.
    rows = [_oee_row(total_count=1500.0, ideal_rate=1000.0)]
    computed = compute_metrics(rows)

    assert computed.oee_available, "fixture should be OEE-capable"
    assert abs(computed.performance - 1.0) < 1e-9, "displayed performance is clamped"
    assert computed.performance_raw > 1.0, "raw performance must be unclamped"

    issues = tier3_aggregate(computed)
    perf = [
        i for i in issues
        if "Performance" in i["message"] and "100%" in i["message"]
    ]
    assert perf, f"expected a Tier-3 performance>100% issue, got: {issues}"
    assert all(i["severity"] == WARNING for i in perf), (
        f"ratio>100% must be a warning, not an error: {perf}")
    print("PASS: aggregate performance>100% produces a Tier-3 warning")


def test_clean_performance_has_no_aggregate_issue():
    rows = [_oee_row(total_count=800.0, ideal_rate=1000.0)]
    computed = compute_metrics(rows)
    issues = tier3_aggregate(computed)
    perf = [i for i in issues if "Performance" in i["message"]]
    assert not perf, f"clean data should not flag performance: {perf}"
    print("PASS: clean performance produces no aggregate issue")


def test_rejects_exceed_zero_output_quarantines_the_row():
    # Output is 0 but rejects are 5 — impossible regardless of zero output.
    row = Record(grain="monthly", plant="PIPE", machine="M/C-1",
                 period="2026-05", total_count=0.0, reject_count=5.0)
    clean, quarantined, issues = tier3_row_classify([row])

    rej_errors = [
        i for i in issues
        if i["severity"] == ERROR and "Rejects" in i["message"]
    ]
    assert rej_errors, f"expected a rejects>output error, got: {issues}"
    assert all(i.get("quarantined") for i in rej_errors), "hard errors must quarantine"
    assert quarantined == [row], "the impossible row must be held aside"
    assert clean == [], "the impossible row must not stay in the clean set"
    print("PASS: rejects exceeding zero output quarantines the row")


def _daily_out_rej(machine, day, out, rej, period="2026-05"):
    """A daily-matrix row (hours/output carried directly, no shift model)."""
    return Record(grain="daily", plant="PTMT", machine=machine, period=period,
                  date=f"{period}-{day:02d}", total_count=out, reject_count=rej,
                  source_tab="Report-5")


def test_daily_reject_on_one_day_not_flagged_when_month_output_dwarfs_it():
    # PTMT books a machine's whole-month rejection against the last day (the
    # matrix has no per-date rejection column). So a single day can show
    # reject > that day's output even though month output far exceeds rejects.
    # This must NOT quarantine — every row stays published.
    rows = [_daily_out_rej("PTMT 150-4", d, 70.0, 0.0) for d in range(1, 28)]
    rows.append(_daily_out_rej("PTMT 150-4", 31, 12.0, 238.0))  # month reject lump
    out_sum = sum(r.total_count for r in rows)   # 1902 > 238
    clean, quarantined, issues = tier3_row_classify(rows)

    rej_err = [i for i in issues
               if i["severity"] == ERROR and "exceed output" in i["message"]]
    assert not rej_err, f"month output dwarfs rejects — must not flag: {rej_err}"
    assert quarantined == [], "valid rows must not be held aside"
    assert len(clean) == len(rows), "every row must publish"
    assert out_sum > 238.0
    print("PASS: single-day reject>output not flagged when month output dwarfs it")


def test_machine_month_reject_truly_exceeds_output_quarantines_all_rows():
    # A genuine impossibility: summed over the month, rejects (300) exceed output
    # (100). Every row for that machine-month is held aside, with one error.
    rows = [_daily_out_rej("PTMT 80-9", 1, 40.0, 100.0),
            _daily_out_rej("PTMT 80-9", 2, 60.0, 200.0)]
    clean, quarantined, issues = tier3_row_classify(rows)

    rej_err = [i for i in issues
               if i["severity"] == ERROR and "exceed output" in i["message"]]
    assert len(rej_err) == 1, f"one aggregate error per machine-month: {issues}"
    assert all(i.get("quarantined") for i in rej_err), "must quarantine"
    assert len(quarantined) == 2 and clean == [], "all month rows held aside"
    print("PASS: genuine machine-month reject>output quarantines all its rows")


def test_hours_over_calendar_quarantines_the_row():
    # 1527 logged hours in May (744 calendar hours) is physically impossible.
    row = _monthly_hours_row(actual_hours=1527.0, ideal_hours=500.0, period="2026-05")
    clean, quarantined, issues = tier3_row_classify([row])

    hard = [
        i for i in issues
        if i["severity"] == ERROR and "calendar maximum" in i["message"]
    ]
    assert hard, f"expected a calendar-ceiling error, got: {issues}"
    assert all(i.get("quarantined") for i in hard), "calendar overflow must quarantine"
    assert quarantined == [row] and clean == [], "impossible row held aside, not published"
    print("PASS: hours above the calendar ceiling quarantines the row")


def test_hours_over_ideal_within_calendar_is_a_warning_not_quarantined():
    # 507 logged hours vs a 500h planned ideal — above plan but well within the
    # 744h calendar ceiling. Possible (overtime / under-set ideal) → WARNING only,
    # and the row must STILL be published (not quarantined).
    row = _monthly_hours_row(actual_hours=507.0, ideal_hours=500.0, period="2026-05")
    clean, quarantined, issues = tier3_row_classify([row])

    warns = [
        i for i in issues
        if i["severity"] == WARNING and "over 100%" in i["message"]
    ]
    assert warns, f"expected a utilisation-over-100% warning, got: {issues}"
    assert not any(i["severity"] == ERROR for i in issues), "must not be a hard error"
    assert clean == [row], "above-plan-but-possible row must remain published"
    assert quarantined == [], "above-plan-but-possible row must not be quarantined"
    print("PASS: hours above ideal but within calendar is a warning, not quarantined")


def test_clean_hours_row_has_no_issue():
    row = _monthly_hours_row(actual_hours=480.0, ideal_hours=500.0, period="2026-05")
    clean, quarantined, issues = tier3_row_classify([row])
    assert issues == [], f"clean hours should produce no Tier-3 issue: {issues}"
    assert clean == [row] and quarantined == []
    print("PASS: clean hours row produces no issue")


if __name__ == "__main__":
    test_aggregate_performance_over_100_is_a_warning()
    test_clean_performance_has_no_aggregate_issue()
    test_rejects_exceed_zero_output_quarantines_the_row()
    test_daily_reject_on_one_day_not_flagged_when_month_output_dwarfs_it()
    test_machine_month_reject_truly_exceeds_output_quarantines_all_rows()
    test_hours_over_calendar_quarantines_the_row()
    test_hours_over_ideal_within_calendar_is_a_warning_not_quarantined()
    test_clean_hours_row_has_no_issue()
    print("\nAll validity regression tests passed.")
