"""Offline regression tests for the read-only Data Verification assembly.

verify.build_verification is pure and network-free: given already-loaded
deterministic Records (monthly summary grid + true daily rows) and the source
report dicts, it surfaces the figures with provenance and runs three
reconciliation checks (daily-vs-summary, row-vs-total, plant-vs-machines), each
PASS / FAIL / NA at a 0.5% tolerance. A mismatch must be surfaced AND located
(which plant/machine, the two numbers) — never silently reconciled.

Run: cd artifacts/prayag && python3 -m tests.test_verify
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import verify
from metrics import Record


def _monthly(plant, machine, total, *, reject=0.0, hours=100.0,
             ideal_hours=120.0, unit="kg", period="2026-05",
             file_id="FILE_M", tab="Report-1", ideal_source="sheet") -> Record:
    return Record(
        grain="monthly", plant=plant, machine=machine, period=period,
        total_count=total, reject_count=reject, unit=unit,
        actual_hours=hours, ideal_hours=ideal_hours, ideal_source=ideal_source,
        source_file=file_id, source_tab=tab,
    )


def _daily(plant, machine, total, *, unit="kg", date="2026-05-01") -> Record:
    return Record(
        grain="daily", plant=plant, machine=machine, period="2026-05",
        date=date, total_count=total, reject_count=0.0, unit=unit,
        shift_len_min=0.0,
    )


def _report(plant, detail_total, grid_total, *, title=None,
            file_id="FILE_M", tab="Report-1") -> dict:
    return {
        "plant": plant,
        "title": title or plant,
        "file_id": file_id,
        "tab": tab,
        "reconcile": {
            "detail_total": detail_total,
            "grid_total": grid_total,
            "diff_pct": 0.0,
            "ok": abs(detail_total - grid_total) < 1e-6,
        },
    }


# ---------------------------------------------------------------------------

def test_clean_all_checks_pass():
    monthly = [
        _monthly("GARDEN", "M/C-1", 1000.0),
        _monthly("GARDEN", "M/C-2", 500.0),
    ]
    daily = [
        _daily("GARDEN", "M/C-1", 600.0),
        _daily("GARDEN", "M/C-1", 400.0),
        _daily("GARDEN", "M/C-2", 500.0),
    ]
    reports = [_report("GARDEN", 1500.0, 1500.0)]
    res = verify.build_verification("2026-05", monthly, reports, daily, [])

    assert res["checks_passed"] == 3, res["checks"]
    assert res["checks_failed"] == 0, res["checks"]
    assert res["checks"]["daily_vs_summary"]["status"] == verify.PASS
    assert res["checks"]["row_vs_total"]["status"] == verify.PASS
    assert res["checks"]["plant_vs_machines"]["status"] == verify.PASS
    # §1 rows + roll-up
    assert res["grand"]["n_rows"] == 2
    assert abs(res["grand"]["output"] - 1500.0) < 1e-6
    print("ok: clean fixtures -> 3/3 PASS")


def test_daily_mismatch_fails_and_is_located():
    monthly = [_monthly("GARDEN", "M/C-1", 1000.0)]
    # Daily sum is 950 vs summary 1000 -> 5% off, well past 0.5% tolerance.
    daily = [_daily("GARDEN", "M/C-1", 950.0)]
    res = verify.build_verification("2026-05", monthly, [], daily, [])

    chk = res["checks"]["daily_vs_summary"]
    assert chk["status"] == verify.FAIL, chk
    failed = [e for e in chk["entries"] if e["status"] == verify.FAIL]
    assert failed, "a FAIL must be located, not just flagged at the rollup"
    # located: plant + the two numbers carried for reconciliation
    plant_fail = [e for e in failed if e.get("scope") == "plant"]
    assert plant_fail and plant_fail[0]["plant"] == "GARDEN"
    assert abs(plant_fail[0]["daily_sum"] - 950.0) < 1e-6
    assert abs(plant_fail[0]["summary_value"] - 1000.0) < 1e-6
    print("ok: daily mismatch -> FAIL located at GARDEN (950 vs 1000)")


def test_row_vs_total_mismatch_fails():
    reports = [_report("PIPE", detail_total=900.0, grid_total=1000.0)]
    res = verify.build_verification("2026-05", [], reports, [], [])
    chk = res["checks"]["row_vs_total"]
    assert chk["status"] == verify.FAIL, chk
    assert chk["entries"][0]["plant"] == "PIPE"
    print("ok: detail rows vs sheet TOTAL mismatch -> FAIL")


def test_plant_vs_machines_detects_unassigned_output():
    # Plant total 1000 but only 800 is recorded against a machine; 200 sits
    # against a blank machine -> machines-sum != plant-total.
    monthly = [
        _monthly("HDPE", "M/C-1", 800.0),
        _monthly("HDPE", "", 200.0),
    ]
    res = verify.build_verification("2026-05", monthly, [], [], [])
    chk = res["checks"]["plant_vs_machines"]
    assert chk["status"] == verify.FAIL, chk
    e = chk["entries"][0]
    assert abs(e["daily_sum"] - 800.0) < 1e-6      # machines sum
    assert abs(e["summary_value"] - 1000.0) < 1e-6  # plant total
    print("ok: output with no machine assigned -> plant_vs_machines FAIL")


def test_no_daily_is_na_not_fail():
    monthly = [_monthly("MOULDING", "M/C-1", 1000.0, unit="pcs")]
    res = verify.build_verification("2026-05", monthly, [], [], [])
    assert res["checks"]["daily_vs_summary"]["status"] == verify.NA
    print("ok: no daily rows -> daily_vs_summary NA (not FAIL)")


def test_csv_has_header_and_provenance():
    monthly = [_monthly("GARDEN", "M/C-1", 1000.0,
                        file_id="FILE_XYZ", tab="Report-7")]
    res = verify.build_verification("2026-05", monthly, [], [], [])
    csv_text = verify.rows_to_csv(res)
    lines = csv_text.strip().splitlines()
    assert lines[0] == ",".join(verify.CSV_HEADER)
    assert "FILE_XYZ" in lines[1] and "Report-7" in lines[1]
    print("ok: CSV carries header + source provenance")


def test_pipe_net_of_rejection_is_annotated_not_autopassed():
    # PIPE daily is net-of-rejection; the summary is gross. The check still
    # FAILs honestly (no auto-pass) but carries the explanatory note.
    monthly = [_monthly("PIPE", "M/C-1", 1000.0)]
    daily = [_daily("PIPE", "M/C-1", 930.0)]
    res = verify.build_verification("2026-05", monthly, [], daily, [])
    chk = res["checks"]["daily_vs_summary"]
    plant_entry = [e for e in chk["entries"] if e.get("scope") == "plant"][0]
    assert plant_entry["status"] == verify.FAIL
    assert "net-of-rejection" in plant_entry["note"]
    print("ok: PIPE net-vs-gross -> honest FAIL with annotation")


if __name__ == "__main__":
    test_clean_all_checks_pass()
    test_daily_mismatch_fails_and_is_located()
    test_row_vs_total_mismatch_fails()
    test_plant_vs_machines_detects_unassigned_output()
    test_no_daily_is_na_not_fail()
    test_csv_has_header_and_provenance()
    test_pipe_net_of_rejection_is_annotated_not_autopassed()
    print("\nAll verify tests passed.")
