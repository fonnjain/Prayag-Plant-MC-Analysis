"""Partial daily reads must never generate a clean-looking report export."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sheets
from reports import generators


def test_inline_report_generator_withholds_partial_daily_source(monkeypatch):
    monkeypatch.setattr(
        sheets,
        "get_daily_records",
        lambda _months: (
            [],
            [{"_failed_pairs": [("PTMT", "2026-06")]}],
            ["PTMT source is incomplete"],
        ),
    )

    with pytest.raises(sheets.SheetReadError, match="report export is withheld"):
        generators._daily("2026-06")