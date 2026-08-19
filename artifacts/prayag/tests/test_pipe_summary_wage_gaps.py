"""Regression coverage for visible Pipe payroll-source gaps."""
from types import SimpleNamespace

import mgmt_pipe_summary as summary


def _pipe_record(month: str, *, kg: float = 0.0):
    return SimpleNamespace(
        plant="PIPE",
        period=month,
        actual_hours=10.0 if kg else 0.0,
        total_count=kg,
        reject_count=0.0,
    )


def test_unregistered_active_month_warns_but_empty_future_month_does_not():
    result = summary._build_section1(
        "2627",
        [_pipe_record("2026-08", kg=100.0)],
        {
            "AUG": {"paid_hrs": 80.0, "labour": 2.0},
            "SEP": {"paid_hrs": None, "labour": None},
        },
        token="unused",
    )

    august = next(row for row in result["month_rows"] if row["ym"] == "2026-08")
    september = next(row for row in result["month_rows"] if row["ym"] == "2026-09")

    assert august["awaiting"] is True
    assert september["awaiting"] is True
    assert any(message.startswith("AUG: Pipe production") for message in result["warnings"])
    assert not any(message.startswith("SEP:") for message in result["warnings"])