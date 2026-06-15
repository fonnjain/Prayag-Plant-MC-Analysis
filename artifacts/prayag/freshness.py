"""Dashboard-detected "last updated / what changed" tracking for source sheets.

WHY a content fingerprint (not Google's edit time): the connected Google
account only holds the ``drive.file`` scope, so the Drive metadata API returns
404 for every workbook the app did not itself create — Google's true
``modifiedTime`` is simply not reachable with this connection (verified). So
instead of Google's edit time, the dashboard hashes the *parsed values* it
reads from each workbook and remembers (in Postgres, via ``store``) each
distinct version. A snapshot is recorded ONLY when a workbook's content
actually changes (current version ≠ the last-seen version), so the most recent
snapshot's timestamp is "when the dashboard last detected this version of the
data" — a reliable signal that the sheet had an input/update. A revert to a
previously-seen version re-stamps that version as the latest (so the next read
converges instead of re-detecting a change forever).

This module is pure-ish: it reads already-cached records (no Drive calls) and
the durable store. It degrades gracefully — with no ``DATABASE_URL`` it still
lists every workbook with its current row count but cannot persist or compare,
so nothing is flagged as changed.
"""
from __future__ import annotations

import datetime
import hashlib
from typing import Dict, Iterable, List, Optional

import sources
import store

# A workbook whose latest detected change is within this many days is shown as
# "recently updated". Persistent and identical on every page (no per-user
# "last looked" state exists — there is no login).
RECENT_DAYS = 7

# Record fields that represent data actually read FROM the sheet (an input or
# update). Derived/config-sourced fields (e.g. the *used* ideal_hours, which can
# come from baselines.json) are deliberately excluded so the fingerprint tracks
# sheet edits, not config changes.
_FP_FIELDS = (
    "grain", "period", "date", "plant", "segment", "machine", "mould",
    "product", "material", "unit",
    "total_count", "reject_count", "runner_lumps", "planned_output",
    "ideal_output", "actual_hours", "ideal_hours_sheet",
    "shift", "shift_len_min", "planned_stops_min", "downtime_min",
    "downtime_reason", "ideal_rate",
    "labour_cost", "power_cost", "solar_cost", "compound_type",
)


def _num(v) -> str:
    """Stable string for a cell value so re-reads of identical data match.

    Numbers are normalised to a fixed 4-dp float form, so an integer ``100`` and
    a float ``100.0`` for the same cell hash identically (parsers can emit either
    for the same value) and float jitter is killed. ``bool`` is handled before
    ``int`` (it is an ``int`` subclass) so flags keep their own representation.
    """
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.4f}"
    return str(v)


def _file_meta() -> Dict[str, dict]:
    """file_id -> {label, plant, grain, month} for every configured workbook."""
    meta: Dict[str, dict] = {}
    for s in sources.ANNUAL_SOURCES:
        meta[s["file_id"]] = {
            "label": s["title"],
            "plant": s["plant"],
            "grain": "monthly",
            "month": "",
        }
    for plant, cfg in sources.DAILY_SOURCES.items():
        for ym, fid in (cfg.get("files") or {}).items():
            if not fid:
                continue
            meta[fid] = {
                "label": f"{sources.PLANT_NAMES.get(plant, plant)} daily — {ym}",
                "plant": plant,
                "grain": "daily",
                "month": ym,
            }
    return meta


def compute_fingerprints(records: Iterable) -> Dict[str, dict]:
    """Group records by their source workbook and hash each one's content.

    Returns ``{file_id: {"fp", "row_count", "grain", "plant"}}``. A workbook
    with no records this run is simply absent (it cannot be fingerprinted
    without having been read).
    """
    grouped: Dict[str, List[str]] = {}
    info: Dict[str, dict] = {}
    for r in records:
        fid = getattr(r, "source_file", "") or ""
        if not fid:
            continue
        parts = [_num(getattr(r, f, "")) for f in _FP_FIELDS]
        grouped.setdefault(fid, []).append("\x1f".join(parts))
        if fid not in info:
            info[fid] = {
                "grain": getattr(r, "grain", "") or "",
                "plant": getattr(r, "plant", "") or "",
            }
    out: Dict[str, dict] = {}
    for fid, lines in grouped.items():
        canon = "\n".join(sorted(lines))
        out[fid] = {
            "fp": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
            "row_count": len(lines),
            "grain": info[fid]["grain"],
            "plant": info[fid]["plant"],
        }
    return out


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def build(records: Iterable, *, now: Optional[datetime.datetime] = None) -> dict:
    """Reconcile freshly-read ``records`` against the stored fingerprints.

    Records any genuine change (append-only) and returns a template-ready dict:
    every tracked workbook with its last-changed timestamp and an "updated"
    flag (changed within ``RECENT_DAYS``). Best-effort: store failures never
    raise — they just mean nothing is flagged.
    """
    now = now or _now()
    meta = _file_meta()
    current = compute_fingerprints(records)
    state = store.fingerprint_state()

    sources_out: List[dict] = []
    n_updated = 0
    for fid, cur in current.items():
        m = meta.get(fid, {})
        label = m.get("label") or cur.get("plant") or fid[:10]
        plant = m.get("plant") or cur.get("plant") or ""
        grain = m.get("grain") or cur.get("grain") or ""
        prev = state.get(fid)

        if prev is None:
            # First time we have ever fingerprinted this workbook — a baseline,
            # not a detected change (we cannot know its history before now).
            rec = store.fingerprint_record(
                file_id=fid, fingerprint=cur["fp"], label=label,
                plant=plant, grain=grain, row_count=cur["row_count"],
            )
            observed = rec["observed_at"] if rec else now
            snapshots = 1
            just_changed = False
        elif prev.get("fingerprint") != cur["fp"]:
            # Content differs from the last version we saw → a real update.
            rec = store.fingerprint_record(
                file_id=fid, fingerprint=cur["fp"], label=label,
                plant=plant, grain=grain, row_count=cur["row_count"],
            )
            observed = rec["observed_at"] if rec else now
            snapshots = int(prev.get("snapshots") or 1) + 1
            just_changed = True
        else:
            observed = prev.get("observed_at") or now
            snapshots = int(prev.get("snapshots") or 1)
            just_changed = False

        observed_dt = observed if isinstance(observed, datetime.datetime) else now
        age = now - observed_dt
        recent = age <= datetime.timedelta(days=RECENT_DAYS)
        ever_changed = snapshots > 1 or just_changed
        updated = bool(recent and ever_changed)
        if updated:
            n_updated += 1

        sources_out.append({
            "file_id": fid,
            "label": label,
            "plant": plant,
            "plant_name": sources.PLANT_NAMES.get(plant, plant),
            "grain": grain,
            "month": m.get("month", ""),
            "row_count": cur["row_count"],
            "last_changed_disp": observed_dt.strftime("%d-%m-%Y %H:%M"),
            "last_changed_ts": observed_dt.timestamp(),
            "updated": updated,
            "ever_changed": ever_changed,
            "tracked": True,
        })

    # Configured workbooks not read this run (e.g. daily files for months not in
    # play) so the inventory is honest about what has/has not been checked.
    for fid, m in meta.items():
        if fid in current:
            continue
        prev = state.get(fid)
        if prev:
            observed = prev.get("observed_at")
            observed_dt = observed if isinstance(observed, datetime.datetime) else now
            sources_out.append({
                "file_id": fid,
                "label": m["label"],
                "plant": m["plant"],
                "plant_name": sources.PLANT_NAMES.get(m["plant"], m["plant"]),
                "grain": m["grain"],
                "month": m.get("month", ""),
                "row_count": int(prev.get("row_count") or 0),
                "last_changed_disp": observed_dt.strftime("%d-%m-%Y %H:%M"),
                "last_changed_ts": observed_dt.timestamp(),
                "updated": False,
                "ever_changed": int(prev.get("snapshots") or 1) > 1,
                "tracked": True,
            })
        else:
            # Configured but never read AND never fingerprinted — listed for an
            # honest inventory, but explicitly "not yet observed" (no timestamp
            # to invent) and sorted to the bottom (ts 0).
            sources_out.append({
                "file_id": fid,
                "label": m["label"],
                "plant": m["plant"],
                "plant_name": sources.PLANT_NAMES.get(m["plant"], m["plant"]),
                "grain": m["grain"],
                "month": m.get("month", ""),
                "row_count": 0,
                "last_changed_disp": "Not yet observed",
                "last_changed_ts": 0.0,
                "updated": False,
                "ever_changed": False,
                "tracked": False,
            })

    # Newest change first; untracked-but-configured handled by the caller's view.
    sources_out.sort(key=lambda s: s["last_changed_ts"], reverse=True)

    return {
        "available": store.AVAILABLE,
        "recent_days": RECENT_DAYS,
        "checked_at_disp": now.strftime("%d-%m-%Y %H:%M"),
        "sources": sources_out,
        "n_total": len(sources_out),
        "n_updated": n_updated,
        "updated": [s for s in sources_out if s["updated"]],
    }
