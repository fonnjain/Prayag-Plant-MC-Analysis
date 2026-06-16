"""
Ingestion Manifest & reconciliation for the Prayag data pipeline.

After the app fetches current data from Google Sheets, this module:
  1. Builds a MANIFEST of what was read (one entry per source file).
  2. Runs COVERAGE reconciliation deterministically (Reconciliation 1):
     expected-minus-fetched → present_but_empty, not_found_at_all, stale_suspects.
  3. Checks per-file SCHEMA invariants (required columns; output ≥ reject).
  4. Calls Claude for an optional ADVISORY pass (fuzzy: stale/drift/unaccounted).
  5. Persists a run log so a future miss can be traced to manifest vs review.

Division of labour (non-negotiable):
- Code is authoritative for coverage, schema, and aggregates.
- Claude is advisory only for the fuzzy layer (stale/partial, naming drift).
- Nothing Claude returns may auto-modify a figure or expand ingestion scope.

Drive.file scope constraint: the Google account cannot list arbitrary folder
contents, so drive_actual is not populated; Reconciliation 2 is skipped.
Coverage from Reconciliation 1 is fully deterministic.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
from typing import List, Optional

import sources

logger = logging.getLogger("prayag.manifest")

# ── Expected schema columns per plant (the ones the pipeline relies on) ────────
# Checked against columns_seen in each source_report. If a required column is
# absent and columns_seen is non-empty, it's a schema_flag.
_REQUIRED_COLS: dict[str, list[str]] = {
    "PIPE":     ["Date", "Shift"],
    "MOULDING": ["Date", "Wt in Kgs"],
    "PTMT":     ["TOTAL", "Actual Rejection Weight"],
    "HDPE":     ["M/C Run Hour"],
    "GARDEN":   [],   # block tabs — no single flat header to check
    "TANK":     [],   # item-level report — no machine header
    "CP":       [],
}

_STALE_DAYS = 10     # a file with data ≥10 days behind as_of is a stale suspect
_FY_MONTHS = sources.FY_MONTHS


def _fy() -> str:
    today = datetime.date.today()
    y = today.year if today.month >= 4 else today.year - 1
    return f"{y}-{str(y + 1)[2:]}"


# ---------------------------------------------------------------------------
# Expected universe
# ---------------------------------------------------------------------------

def build_expected_universe(months: List[str]) -> dict:
    """Plants × months the pipeline knows to look for in scope."""
    daily_configured: dict[str, list[str]] = {}
    for plant, cfg in sources.DAILY_SOURCES.items():
        ms = [m for m in months if m in cfg.get("files", {})]
        if ms:
            daily_configured[plant] = ms

    annual_plants = [s["plant"] for s in sources.ANNUAL_SOURCES]

    return {
        "fy": _fy(),
        "plants": list(sources.PLANT_NAMES.keys()),
        "plant_names": sources.PLANT_NAMES,
        "months": months,
        "daily_configured": daily_configured,
        "annual_summary_plants": annual_plants,
        "naming_pattern": "{PLANT} Date Sheet & Monthly Report - {Mon} '{YYYY}",
        "drive_actual_note": (
            "drive.file scope: folder enumeration is not possible with the "
            "current Google auth scope. drive_actual is not populated; "
            "Reconciliation 2 (Drive minus Expected) is therefore skipped."
        ),
    }


# ---------------------------------------------------------------------------
# Enrichment — add date_range, aggregates, columns_seen to source_reports
# ---------------------------------------------------------------------------

def _date_range(records) -> Optional[list]:
    dates = [r.date for r in records if getattr(r, "date", None)]
    return [min(dates), max(dates)] if dates else None


def _make_aggregates(records, unit: str = "kg") -> Optional[dict]:
    if not records:
        return None
    total = sum(getattr(r, "total_count", 0) or 0 for r in records)
    reject = sum(getattr(r, "reject_count", 0) or 0 for r in records)
    return {
        "total_count": round(total, 1),
        "reject_count": round(reject, 1),
        "unit": unit,
        "output_ge_reject": total >= reject,
    }


def enrich_reports(reports: list, all_records) -> list:
    """Return shallow-copied source_reports enriched with date_range and aggregates."""
    by_file: dict[str, list] = {}
    for r in all_records:
        fid = getattr(r, "source_file", None) or ""
        by_file.setdefault(fid, []).append(r)

    enriched = []
    for rep in reports:
        rep = dict(rep)
        fid = rep.get("file_id", "")
        recs = by_file.get(fid, [])
        rep.setdefault("date_range_in_data", _date_range(recs))
        rep.setdefault("rows_found", rep.get("record_count", len(recs)))
        unit = rep.get("unit") or (
            sources.ANNUAL_SOURCES[0].get("unit", "kg")
            if [s for s in sources.ANNUAL_SOURCES if s["plant"] == rep.get("plant")]
            else "kg"
        )
        rep.setdefault("aggregates", _make_aggregates(recs, unit=unit))
        enriched.append(rep)
    return enriched


# ---------------------------------------------------------------------------
# Reconciliation 1 — Expected − Fetched (deterministic)
# ---------------------------------------------------------------------------

def build_coverage(
    expected_universe: dict,
    enriched_reports: list,
    as_of: datetime.date,
) -> dict:
    """Deterministic: Expected daily files minus what was actually fetched."""
    months = expected_universe["months"]
    daily_configured = expected_universe["daily_configured"]  # plant code → [month]

    # Index fetched entries by (plant_code, month)
    fetched_index: dict[tuple, dict] = {}
    for rep in enriched_reports:
        if rep.get("grain") != "daily":
            continue
        p = rep.get("plant", "")
        for ym in (rep.get("months_available") or []):
            if ym in months:
                fetched_index[(p, ym)] = rep

    expected_pairs: list[tuple[str, str]] = [
        (p, ym)
        for p, ms in daily_configured.items()
        for ym in ms
    ]

    present_but_empty: list[dict] = []
    not_found_at_all: list[dict] = []
    stale_suspects: list[dict] = []
    fetched_with_data = 0

    for p, ym in expected_pairs:
        plant_name = sources.PLANT_NAMES.get(p, p)
        file_id = sources.DAILY_SOURCES.get(p, {}).get("files", {}).get(ym, "")
        entry = fetched_index.get((p, ym))

        if entry is None:
            not_found_at_all.append({"plant": plant_name, "month": ym, "file_id": file_id})
            continue

        rows = entry.get("rows_found", entry.get("record_count", 0))
        if not rows:
            present_but_empty.append({"plant": plant_name, "month": ym, "file_id": file_id})
            continue

        fetched_with_data += 1

        # Stale check: data's last date vs as_of and vs the month end
        dr = entry.get("date_range_in_data")
        if dr and dr[1]:
            try:
                last = datetime.date.fromisoformat(dr[1])
                gap = (as_of - last).days
                # Only flag as stale if the month isn't fully past
                yr, mo = int(ym[:4]), int(ym[5:7])
                if mo == 12:
                    month_end = datetime.date(yr + 1, 1, 1) - datetime.timedelta(days=1)
                else:
                    month_end = datetime.date(yr, mo + 1, 1) - datetime.timedelta(days=1)
                if last < month_end and gap > _STALE_DAYS:
                    stale_suspects.append({
                        "plant": plant_name,
                        "month": ym,
                        "file_id": file_id,
                        "data_through": dr[1],
                        "as_of": as_of.isoformat(),
                        "days_behind": gap,
                    })
            except (ValueError, TypeError):
                pass

    return {
        "expected_count": len(expected_pairs),
        "fetched_with_data": fetched_with_data,
        "present_but_empty": present_but_empty,
        "not_found_at_all": not_found_at_all,
        "stale_suspects": stale_suspects,
    }


# ---------------------------------------------------------------------------
# Schema flags (per-file invariants)
# ---------------------------------------------------------------------------

def build_schema_flags(enriched_reports: list) -> list:
    """Return schema_flag dicts for any per-file invariant violations."""
    flags: list[dict] = []
    for rep in enriched_reports:
        plant = rep.get("plant", "")
        plant_name = sources.PLANT_NAMES.get(plant, plant)
        month = (rep.get("months_available") or ["?"])[0]
        fid = rep.get("file_id", "")

        # output ≥ reject invariant
        agg = rep.get("aggregates")
        if agg and not agg.get("output_ge_reject", True):
            flags.append({
                "plant": plant_name,
                "month": month,
                "file_id": fid,
                "type": "output_lt_reject",
                "issue": (
                    f"output {agg['total_count']:,.0f} {agg.get('unit','?')} "
                    f"< reject {agg['reject_count']:,.0f} {agg.get('unit','?')} — "
                    "investigate column mapping first; likely a read-column error, "
                    "not real data"
                ),
                "severity": "error",
            })

        # Required columns present.
        # Guard: only check when cols_seen has ≥4 entries — a single-entry list
        # is a title row (e.g. "PTMT DAILY PRODUCTION REPORT - APRIL 2026"), not
        # the actual column headers; those produce false-positive flags.
        cols_seen = rep.get("columns_seen") or []
        if len(cols_seen) < 4:
            continue
        for col in _REQUIRED_COLS.get(plant, []):
            if not any(col.lower() in str(c).lower() for c in cols_seen):
                flags.append({
                    "plant": plant_name,
                    "month": month,
                    "file_id": fid,
                    "type": "missing_column",
                    "issue": f"required column '{col}' not found in columns_seen",
                    "severity": "warning",
                })
    return flags


# ---------------------------------------------------------------------------
# Top-level manifest builder
# ---------------------------------------------------------------------------

def build_manifest(
    months: List[str],
    all_records,
    source_reports: list,
    as_of: Optional[datetime.date] = None,
) -> dict:
    """Build the full ingestion manifest + both reconciliations.

    Args:
        months:          The FY months in scope (e.g. ["2026-04", "2026-05", ...]).
        all_records:     The full list of Record objects from get_data / get_daily_records.
        source_reports:  The list of source_report dicts from the data fetch.
        as_of:           The date to evaluate staleness against (defaults to today).

    Returns a dict matching the manifest spec shape:
        {fy, as_of, generated_at, expected_universe, fetched, coverage, schema_flags,
         drive_actual, unaccounted_raw}
    """
    if as_of is None:
        as_of = datetime.date.today()

    expected = build_expected_universe(months)
    enriched = enrich_reports(source_reports, all_records)
    coverage = build_coverage(expected, enriched, as_of)
    schema_flags = build_schema_flags(enriched)

    return {
        "fy": expected["fy"],
        "as_of": as_of.isoformat(),
        "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expected_universe": expected,
        "fetched": enriched,
        "coverage": coverage,
        "schema_flags": schema_flags,
        "drive_actual": [],
        "unaccounted_raw": [],
    }


def manifest_fingerprint(manifest: dict) -> str:
    payload = json.dumps(
        {k: manifest[k] for k in ("as_of", "coverage", "schema_flags")},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Compact summary for the advisory prompt (avoids huge JSON payloads)
# ---------------------------------------------------------------------------

def manifest_summary(manifest: dict) -> dict:
    """A compact, token-efficient summary of the manifest for the Claude prompt."""
    fetched_summary = []
    for rep in manifest.get("fetched", []):
        agg = rep.get("aggregates")
        fetched_summary.append({
            "plant": sources.PLANT_NAMES.get(rep.get("plant", ""), rep.get("plant", "")),
            "month": (rep.get("months_available") or ["?"])[0],
            "file_id": rep.get("file_id", ""),
            "file_title": rep.get("title", ""),
            "grain": rep.get("grain", ""),
            "rows_found": rep.get("rows_found", 0),
            "date_range_in_data": rep.get("date_range_in_data"),
            "modified_time": rep.get("modified_time"),
            "columns_seen": (rep.get("columns_seen") or [])[:12],
            "aggregates": agg,
            "warning": rep.get("warning"),
        })
    return {
        "fy": manifest["fy"],
        "as_of": manifest["as_of"],
        "expected_universe": {
            "plants": list(manifest["expected_universe"]["daily_configured"].keys()),
            "months": manifest["expected_universe"]["months"],
            "naming_pattern": manifest["expected_universe"]["naming_pattern"],
        },
        "fetched": fetched_summary,
        "drive_actual": [],
    }
