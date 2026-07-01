"""Period resolution for the management-report exports.

Reports are single-month snapshots (one workbook per report per month), so a
report is addressed by a ``YYYY-MM`` month key. This module resolves the set of
selectable months (the union of every daily source's configured files) and the
default month (the latest month for which the PIPE daily workbook exists — the
most complete plant).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import sources


def available_months() -> List[str]:
    months = set()
    for plant, cfg in sources.DAILY_SOURCES.items():
        for ym in (cfg.get("files") or {}):
            months.add(ym)
    return sorted(months, reverse=True)


def default_month() -> Optional[str]:
    pipe = sorted((sources.DAILY_SOURCES.get("PIPE", {}).get("files") or {}).keys())
    if pipe:
        return pipe[-1]
    months = available_months()
    return months[0] if months else None


def resolve_month(arg: Optional[str]) -> Optional[str]:
    """Validate a ``YYYY-MM`` request; fall back to the default month."""
    months = set(available_months())
    if arg and arg in months:
        return arg
    return default_month()


def month_disp(ym: Optional[str]) -> str:
    if not ym:
        return "—"
    try:
        return datetime.strptime(ym + "-01", "%Y-%m-%d").strftime("%b %Y")
    except ValueError:
        return ym


def month_slug(ym: Optional[str]) -> str:
    if not ym:
        return "unknown"
    try:
        return datetime.strptime(ym + "-01", "%Y-%m-%d").strftime("%b-%Y")
    except ValueError:
        return ym
