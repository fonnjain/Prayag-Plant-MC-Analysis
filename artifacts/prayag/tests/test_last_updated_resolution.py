"""Regression tests for the "Last Updated" default-period resolution.

The default period resolves to the single most recent day with REAL production,
deliberately SKIPPING empty in-progress days. A daily tab is often created dated
ahead of actual data entry (a placeholder for tomorrow, or a date that looks like
the future because the sheets are kept in IST while the server clock is UTC); its
rows exist but are all-zero. Picking the bare max date would land the headline on
that empty day ("No data recorded for this period yet" / Total Output 0).

Rejection must NOT count as production: a wide-matrix parser (PTMT) books the
whole month's rejection onto the LAST calendar day's row, so that day carries
reject>0 with zero output — counting it would re-introduce the empty-day bug.

Run: cd artifacts/prayag && python3 -m tests.test_last_updated_resolution
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app
from metrics import Record


def _daily(date, plant="PTMT", total_count=0.0, actual_hours=0.0, reject_count=0.0):
    return Record(grain="daily", date=date, period=date, plant=plant,
                  total_count=total_count, actual_hours=actual_hours,
                  reject_count=reject_count)


def test_has_production_output_or_hours():
    assert app._has_production(_daily("2026-06-27", total_count=8814.8))
    assert app._has_production(_daily("2026-06-27", actual_hours=933.0))
    assert app._has_production(_daily("2026-06-27", total_count=1.0, actual_hours=0.0))


def test_has_production_excludes_empty_and_reject_only():
    # All-zero placeholder day -> not production.
    assert not app._has_production(_daily("2026-06-30"))
    # Reject-only (matrix last-day lump) -> NOT production, by design.
    assert not app._has_production(_daily("2026-06-30", reject_count=1200.0))


def test_latest_skips_future_zero_day():
    """Future-dated zero-output rows, a reject-only lumped last-day row, and a
    prior day with real output -> the real production day is selected."""
    drecs = [
        _daily("2026-06-27", total_count=8814.8, actual_hours=933.0),  # real
        _daily("2026-06-29"),                                          # empty (today)
        _daily("2026-06-30", reject_count=5000.0),                     # reject-only lump
    ]
    assert app._latest_production_date(drecs) == "2026-06-27"


def test_latest_ignores_month_grain_aux_rows():
    """Month-grain Report-5 aux rows (grinders) carry output but are not a daily
    date — they must never be picked as the latest daily date."""
    aux = Record(grain="monthly", date="2026-06-01", plant="PIPE", total_count=999.0)
    drecs = [_daily("2026-06-27", total_count=10.0), aux]
    assert app._latest_production_date(drecs) == "2026-06-27"


def test_latest_none_when_no_production():
    drecs = [_daily("2026-06-30"), _daily("2026-06-30", reject_count=100.0)]
    assert app._latest_production_date(drecs) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("ALL PASSED")
