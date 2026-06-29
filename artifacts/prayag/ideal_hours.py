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
  3. app DEFAULT        — a per-plant fallback in ``APP_DEFAULT_IDEAL_HOURS``,
                          real planned-hours figures supplied by the business that
                          do NOT live in the source sheets (Garden Pipe / Tank =
                          500 h, HDPE = 550 h per machine per month). Used only
                          when neither an override nor a live sheet value exists.
                          Source badge "app default".
  4. NOT SET            — no baseline: utilisation is suppressed (shown as "no
                          baseline set"), never a misleading 0%.

NOTE on output-only plants: a default supplies the ideal-hours DENOMINATOR, but
utilisation (run hours / ideal) still needs real run hours. Plants in
``PLANTS_WITHOUT_RUNHOURS`` record output only (no run hours), so their daily
records are stamped ``runhours_tracked=False`` and ``compute_metrics`` keeps
their utilisation SUPPRESSED (never a fake 0%) until run hours are recorded — the
default alone does not flip them to a live figure.

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
# nor a live sheet value exists. These are real planned hours supplied by the
# business that are NOT published in the source sheets — never an estimate or the
# grid's flat 500-hour placeholder. Keys are plant codes (sources.ANNUAL_SOURCES
# / DAILY layout ``emit``).
APP_DEFAULT_IDEAL_HOURS: dict = {
    "GARDEN": 500.0,   # Garden Pipe — app-logic default (not in sheet)
    "TANK": 500.0,     # Tanks (KH) — app-logic default (not in sheet)
    "HDPE": 550.0,     # HDPE — app-logic default (not in sheet)
}

# Plants that record OUTPUT only and carry NO run hours. An app-default ideal
# still supplies their utilisation denominator, but utilisation must stay
# suppressed (not a fake 0%) until run hours are actually recorded — enforced via
# ``Record.runhours_tracked=False`` and the gate in ``metrics.compute_metrics``.
PLANTS_WITHOUT_RUNHOURS = frozenset({"GARDEN", "TANK"})

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
