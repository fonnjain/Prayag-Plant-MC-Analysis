"""
Per-machine planned-hours baseline master (CONFIG, not data).

This is the denominator for utilisation and output-efficiency. The production
sheets carry only a flat placeholder for "Ideal Hours" (500 for every Moulding
machine), which is NOT a real baseline. The *real* planned-hours figure is
maintained here by the team — but only when genuine shift-pattern data exists.
Estimates and the 500-h placeholder must never be entered as a baseline. PIPE and
MOULDING have no shift-pattern data today, so the file ships with no entries for
them and they correctly show "baseline not set".

It NEVER changes how output or hours are READ from the sheet. It only sets the
target each machine is measured against, and every figure keeps its provenance
(no real baseline vs config baseline). When a machine has no baseline the engine
suppresses its ratio and FLAGS it as an advisory, non-blocking warning — it never
silently invents one and never gates sign-off.

Editing: change baselines.json. No code edit is needed. Each entry documents its
``basis`` (how the planned hours were derived) and who set it.
"""
from __future__ import annotations
import json
import os
import re
from typing import Optional

_PATH = os.path.join(os.path.dirname(__file__), "baselines.json")
_cache: Optional[dict] = None


def _norm(s: str) -> str:
    """Normalise a machine code for matching: drop all whitespace, upper-case.

    The sheets store codes like ``"MOULDING M/C - 4"`` (spaces around the dash);
    config can be written more naturally and still match.
    """
    return re.sub(r"\s+", "", str(s or "")).upper()


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        machines = {}
        for k, v in (raw.get("machines") or {}).items():
            machines[_norm(k)] = v
        _cache = {
            "machines": machines,
            "formula_defaults": raw.get("formula_defaults") or {},
        }
    return _cache


def reload() -> None:
    """Drop the in-memory cache (after editing the file, or in tests)."""
    global _cache
    _cache = None


def resolve(plant: str, machine: str, month: str) -> Optional[dict]:
    """Return the planned-hours baseline for a machine-month, or ``None``.

    Lookup order:
      1. an explicit per-machine entry (with optional per-month ``overrides``),
      2. else a per-plant ``formula_defaults`` entry,
      3. else ``None`` — the caller falls back to the sheet placeholder and flags it.

    Returns ``{planned_hours, ideal_output, basis, source}``. ``ideal_output`` is
    ``None`` when no ``ideal_output_rate`` is configured (output-efficiency then
    keeps the sheet's own ideal-output figure).
    """
    cfg = _load()
    entry = cfg["machines"].get(_norm(machine))
    if entry:
        planned = entry.get("planned_hours")
        override = (entry.get("overrides") or {}).get(month)
        if override is not None:
            planned = override
        if planned is None:
            return None
        rate = entry.get("ideal_output_rate")
        return {
            "planned_hours": float(planned),
            "ideal_output": (float(rate) * float(planned)) if rate else None,
            "basis": entry.get("basis", ""),
            "source": "config",
        }

    fd = cfg["formula_defaults"].get(plant)
    if fd and fd.get("planned_hours"):
        planned = float(fd["planned_hours"])
        rate = fd.get("ideal_output_rate")
        return {
            "planned_hours": planned,
            "ideal_output": (float(rate) * planned) if rate else None,
            "basis": fd.get("basis", ""),
            "source": "config",
        }
    return None
