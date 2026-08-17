"""Report registry — the single source of truth for the management-report set.

``REPORTS`` lists every report (id, label, plant/location, generator, enabled).
``build_report`` runs a generator for a month; ``report_workbook`` renders it to
an openpyxl workbook; ``report_bytes``/``report_filename`` package a single
download; ``zip_bytes`` bundles a plant's (or all) reports into one archive.

A report's figures are recomputed by its generator — this registry only wires
ids to generators and handles packaging (filenames, the ZIP, the index view).
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import generators as gen
from . import xlsx
from .model import ReportModel
from .period import month_slug


@dataclass
class ReportDef:
    id: str
    label: str
    plant: str            # location bucket: KH | Bhiwari | VN | WB | ALL
    fn: Callable
    enabled: bool = True
    slug: str = ""        # filename slug; defaults to id


REPORTS: List[ReportDef] = [
    ReportDef("pipe",           "(A) Pipe M/C Summary",        "KH",      gen.gen_pipe),
    ReportDef("moulding",       "(B) Moulding M/C Summary",    "KH",      gen.gen_moulding),
    ReportDef("gom",            "(C) Group of Moulding",       "KH",      gen.gen_gom),
    ReportDef("pipe_moulds",    "(D) Pipe Moulds Summary",     "KH",      gen.gen_pipe_moulds),
    ReportDef("garden",         "Garden Pipe Summary",         "KH",      gen.gen_garden),
    ReportDef("hdpe",           "HDPE M/C Summary",            "KH",      gen.gen_hdpe),
    ReportDef("mould_eff",      "Mould Age-in-Efficiency",     "KH",      gen.gen_mould_eff),
    ReportDef("tank_kh",        "Tanks (Kaharani)",            "KH",      gen.gen_tank_kh),
    ReportDef("ptmt_moulds",    "PTMT Moulds Summary",         "Bhiwari", gen.gen_ptmt_moulds),
    ReportDef("ptmt_eff",       "PTMT Efficiency by Tonnage",  "Bhiwari", gen.gen_ptmt_eff),
    ReportDef("tank_vn",        "Tanks (Varanasi)",            "VN",      gen.gen_tank_vn),
    ReportDef("tank_wb",        "Tanks (West Bengal)",         "WB",      gen.gen_tank_wb),
    ReportDef("segment_labour", "Segment Labour / Power / Solar", "ALL",  gen.gen_segment_labour),
    # Compound / Material: mass-balance from mixer-logbook tabs (Reports 6–10,
    # CG 122). Also available as an interactive page at /compound.
    ReportDef("compound",       "Compound / Material",         "KH",      gen.gen_compound),
]

_BY_ID = {r.id: r for r in REPORTS}
_LOCATION_ORDER = ["KH", "Bhiwari", "VN", "WB", "ALL"]
_LOCATION_NAMES = {"KH": "Kaharani", "Bhiwari": "Bhiwadi (RICO)",
                   "VN": "Varanasi", "WB": "West Bengal", "ALL": "All Locations"}


def get(rid: str) -> Optional[ReportDef]:
    return _BY_ID.get(rid)


def enabled_reports() -> List[ReportDef]:
    return [r for r in REPORTS if r.enabled and r.fn is not None]


def build_report(rid: str, ym: str) -> ReportModel:
    rd = _BY_ID.get(rid)
    if rd is None or rd.fn is None:
        raise KeyError(rid)
    return rd.fn(rid, rd.label, rd.plant, ym)


def report_workbook(rid: str, ym: str):
    return xlsx.render_workbook(build_report(rid, ym))


def _slug(rd: ReportDef) -> str:
    return (rd.slug or rd.id).replace(" ", "_")


def report_filename(rid: str, ym: str) -> str:
    from datetime import datetime
    rd = _BY_ID.get(rid)
    label = _slug(rd) if rd else rid
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    return f"Prayag_{(rd.plant if rd else 'ALL')}_{label}_{month_slug(ym)}_{ts}.xlsx"


def report_bytes(rid: str, ym: str) -> bytes:
    return xlsx.workbook_bytes(report_workbook(rid, ym))


@dataclass
class ZipResult:
    data: bytes
    built: int          # reports successfully written
    total: int          # reports that were in scope
    skipped: List[str]  # ids that failed to build


def zip_bundle(ym: str, plant: Optional[str] = None) -> ZipResult:
    """Bundle every enabled report (optionally one location) into a ZIP.

    A single failing report is skipped (never sinks the whole archive) but is
    reported back in ``skipped`` so the caller can fail loudly if NOTHING built
    — an empty ZIP must never be served as a silent success.
    """
    in_scope = [rd for rd in enabled_reports()
                if not (plant and plant != "ALL" and rd.plant != plant)]
    skipped: List[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rd in in_scope:
            try:
                data = report_bytes(rd.id, ym)
            except Exception:  # one bad report must not sink the whole ZIP
                skipped.append(rd.id)
                continue
            zf.writestr(report_filename(rd.id, ym), data)
    return ZipResult(buf.getvalue(), len(in_scope) - len(skipped),
                     len(in_scope), skipped)


def zip_bytes(ym: str, plant: Optional[str] = None) -> bytes:
    """Back-compat convenience: the raw ZIP bytes (see ``zip_bundle``)."""
    return zip_bundle(ym, plant).data


def index_view(ym: str) -> List[dict]:
    """Location-grouped list of enabled reports for the download page."""
    from collections import defaultdict
    by_loc = defaultdict(list)
    for rd in enabled_reports():
        by_loc[rd.plant].append({"id": rd.id, "label": rd.label,
                                 "filename": report_filename(rd.id, ym)})
    out = []
    for loc in _LOCATION_ORDER:
        items = by_loc.get(loc)
        if items:
            out.append({"id": loc, "name": _LOCATION_NAMES.get(loc, loc),
                        "reports": items})
    return out
