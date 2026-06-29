"""Ideal run-hours resolution — the single place that decides the monthly ideal
run hours behind a machine's utilisation.

Precedence, highest first:

  1. user OVERRIDE      — a value typed on the /input page, stored in the app DB
                          (``store.ideal_hours_overrides``), keyed (plant, machine,
                          month). NEVER written back to the Google Sheets. Clearing
                          an override reverts to the sheet value below.
  2. live SHEET value   — a real per-machine figure read from the production sheet.
                          PTMT publishes a direct monthly ``IDEAL HOUR`` (kind
                          ``"sheet"``); PIPE publishes ``Ideal Run Hour Per Day``
                          which is multiplied by the month's calendar days to a
                          monthly figure (kind ``"derived"``). Both are stamped on
                          the Record in ``sheets._emit_daily``.
  3. app DEFAULT        — a per-plant fallback in ``APP_DEFAULT_IDEAL_HOURS``. Ships
                          EMPTY: only real, business-supplied planned hours belong
                          here, never an estimate or a placeholder.
  4. NOT SET            — no baseline: utilisation is suppressed (shown as "no
                          baseline set"), never a misleading 0%.

v1 covers ideal HOURS only (→ utilisation). Ideal output-per-hour (→ efficiency)
is explicitly out of scope here. Pure module: no network, no DB.
"""
from __future__ import annotations

import calendar
from typing import Optional, Tuple

# --- configuration ----------------------------------------------------------

# How PIPE's "Ideal Run Hour Per Day" is expanded to a monthly figure. The basis
# materially changes utilisation (calendar days >> the days a machine actually
# ran), so it is a single explicit constant, not a guess scattered in the code.
# "calendar" = every day in the month (the documented default); a plant that wants
# "days it ran" should set per-machine overrides on the /input page instead.
PIPE_IDEAL_DAYS_BASIS = "calendar"

# Per-plant monthly ideal-hours fallback, applied only when neither an override
# nor a live sheet value exists. Ships EMPTY on purpose — populate ONLY with real
# planned hours supplied by the business, never an estimate or the grid's flat
# 500-hour placeholder.
APP_DEFAULT_IDEAL_HOURS: dict = {}

# Source labels (also used as CSS/badge keys on the page).
SRC_OVERRIDE = "override"
SRC_SHEET = "sheet"
SRC_DERIVED = "derived"
SRC_APP_DEFAULT = "app_default"
SRC_NOT_SET = "not_set"

SRC_LABELS = {
    SRC_OVERRIDE: "Override",
    SRC_SHEET: "From sheet",
    SRC_DERIVED: "Derived",
    SRC_APP_DEFAULT: "App default",
    SRC_NOT_SET: "Not set",
}


# --- helpers ----------------------------------------------------------------

def days_in_month(month_iso: str) -> int:
    """Calendar days in ``YYYY-MM`` (e.g. ``2026-04`` → 30). 0 on a bad input."""
    try:
        y, m = int(month_iso[:4]), int(month_iso[5:7])
        return calendar.monthrange(y, m)[1]
    except Exception:
        return 0


def cap_hours(month_iso: str) -> float:
    """Sanity ceiling for a monthly override: 24h × calendar days. A value above
    this is physically impossible; the page WARNS (it does not silently clamp)."""
    return 24.0 * days_in_month(month_iso)


def resolve(
    *,
    override: Optional[float],
    sheet_value: Optional[float],
    sheet_kind: str = SRC_SHEET,
    plant: str = "",
) -> Tuple[Optional[float], str]:
    """Return ``(effective_monthly_ideal_hours, source)`` for one machine-month.

    ``override`` is the stored value or ``None`` (not set). An override of exactly
    ``0`` is meaningful — "this machine is not expected to run this month" — and is
    honoured (utilisation suppressed), NOT treated as missing.
    ``sheet_value`` is the live per-machine monthly ideal from the sheet (already
    expanded for PIPE), or ``None``/0 when the sheet carries none.
    """
    if override is not None:
        return float(override), SRC_OVERRIDE
    if sheet_value and sheet_value > 0:
        return float(sheet_value), (sheet_kind or SRC_SHEET)
    dflt = APP_DEFAULT_IDEAL_HOURS.get(plant)
    if dflt and dflt > 0:
        return float(dflt), SRC_APP_DEFAULT
    return None, SRC_NOT_SET
