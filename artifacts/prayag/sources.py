"""
Source registry — the single place that maps each report "family" to a live
Google Sheet file ID, the data tab(s) to read, and the parser layout to use.

Why explicit file IDs (not folder discovery): the connected Google account's
scope is `drive.file`, which CANNOT list arbitrary Drive folders or run a
`files.list` search (verified: returns 0). So auto-discovery of "the whole
daily folder" is impossible with the current connection. We read each workbook
by its pinned ID. When a new monthly file is added to Drive, its ID must be
added here (see DAILY_SOURCES) — this is documented in the README.

All IDs below were verified accessible via the `google-sheet` connection.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Annual (monthly-grain) summary workbooks — native Google Sheets.
# kind drives which parser in parsers.py handles the tab.
# ---------------------------------------------------------------------------
ANNUAL_SOURCES: list[dict] = [
    {
        "family": "pipe",
        "title": "Pipe M/C Summary (26-27)",
        "file_id": "1EHJvI7KxIahlfZ5ODiAea3Pj2Zk3z1T6cCybZJuDAMQ",
        "tab": "Pipe M/C 26-27",
        "kind": "mc_grid",
        "segment": "Pipe",
        "plant": "PIPE",
        "unit": "kg",
    },
    {
        "family": "garden",
        "title": "Garden Pipe M/C Summary (26-27)",
        "file_id": "1OYQq7YR_-6PyqzppOOamEaJl14VjbOQ6x-O_cpDy0gc",
        "tab": "GARDEN M/C 26-27",
        "kind": "mc_grid",
        "segment": "Garden Pipe",
        "plant": "GARDEN",
        "unit": "kg",
    },
    {
        "family": "hdpe",
        "title": "HDPE M/C Summary (26-27)",
        "file_id": "1TRpfk3UzsAow4InJ6HyT_smayOdHfyoZ67xmHIwD2tw",
        "tab": "HDPE M/C 26-27",
        "kind": "mc_grid",
        "segment": "HDPE",
        "plant": "HDPE",
        "unit": "kg",
    },
    {
        "family": "moulding",
        "title": "Moulding M/C Summary (26-27)",
        "file_id": "1ZCHZp5io1ctdvm92xlHI7x5FtBC-nLoAb2FRMkfXzBI",
        "tab": "Moulding M/C 26-27",
        "kind": "mc_grid",
        "segment": "Moulding",
        "plant": "MOULDING",
        "unit": "pcs",
    },
]

# ---------------------------------------------------------------------------
# Daily per-month workbooks (true day-level data). plant -> {YYYY-MM: file_id}.
# These are read directly for sub-monthly periods. Add new months here as the
# factory creates them (folder auto-listing is not possible — see module note).
# folder_ids kept for reference / manual lookup only.
# ---------------------------------------------------------------------------
DAILY_SOURCES: dict[str, dict] = {
    "PIPE": {
        "folder_ids": ["1eE1xSVAvi8t4wO_eZnCvbxMjQiqBiRG6"],
        "files": {
            "2026-04": "1eNUSktOldFHRtM55VYfLiYp5nLDRk3ovOEdYYKfI0hU",
            "2026-05": "17__f7pP28bIoctVXV-iku3WIlffAuonvRhCaViVu-bA",
            "2026-06": "1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec",
        },
    },
    "PTMT": {
        "folder_ids": ["1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1"],
        "files": {
            "2026-04": "16zsh5x4MdY8DX3H5_hw5iaOdkGixlUsPzesDVnwgfYo",
            "2026-05": "1T1M5MT47P3D4wCwi7tX7KcL_sHVtx43NSuXFDP9Oq78",
            "2026-06": "1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g",
        },
    },
    "GARDEN": {
        "folder_ids": ["1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT"],
        "files": {
            "2026-04": "1mbxHLgvvruhI-3_d9zoqevZQxHhjxZY4cN0tIyxkzEo",
            "2026-05": "1qmTMCWZWLsuA4kCzaAFC4fjG46Zf3rGz5VjOknv_Sy0",
            "2026-06": "1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA",
        },
    },
    "HDPE": {
        "folder_ids": ["1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60"],
        "files": {
            "2026-04": "1TTxcpSQyVyleermiOhYlxlcd3RE0Pay0dRHLnXSEohs",
            "2026-05": "1-RCsS2gbtI3toyNG4uec29_coID42qCNsquaYdk-IIQ",
            "2026-06": "1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8",
        },
    },
    "TANK": {
        "folder_ids": [
            "1IsWgq01xLIkX0UZKnSolIL6lOToFefO_",
            "1hlBedSVVMM7nbTn5Ylx4ecAeJ3CS1FJj",
        ],
        "files": {
            "2026-04": "1osCJ1ZF2okCdHXbhkBthvJ7T7x21warW1-NMGm-5xbc",
            "2026-05": "1Zl8dvEZkQKGAkyWDTgLznC_yISNVznPf3pgUodHttm8",
            "2026-06": "1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo",
        },
    },
    # CP runs on a different cycle; no Apr-26+ daily file yet (flagged at load).
    "CP": {
        "folder_ids": ["17thg66c3u0DMqy8bXjt6JSYp6sKqQISE"],
        "files": {},
    },
}

# Friendly names for plants/segments shown in the UI.
PLANT_NAMES = {
    "PIPE": "Pipe & Fitting",
    "GARDEN": "Garden Pipe",
    "HDPE": "HDPE",
    "MOULDING": "Injection Moulding",
    "PTMT": "PTMT",
    "CP": "CP Fittings",
    "TANK": "Tanks",
}

FY_MONTHS = [
    "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09",
    "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03",
]

# ---------------------------------------------------------------------------
# PTMT authoritative machine roster (55 machines in 5 process groups).
#
# PTMT has NO monthly-grid summary, so its roster cannot be derived from the
# annual grid like the other plants. This list — the factory's own machine
# register (Report-5 "Daily Output – Moulding M/C") — is therefore the
# authoritative roster: completeness is measured against all 55, so a machine
# that never reports is surfaced as a gap rather than the dataset silently
# redefining "complete" as "whatever reported". Process groups also come from
# this file (not a heuristic), so each machine is compared within its own
# process group, never across the whole plant.
#
# Codes are the bare machine label as it appears in the daily matrix; the
# emitted machine id is "PTMT <code>" (see PTMT_ROSTER_IDS).
# ---------------------------------------------------------------------------
PTMT_GROUPS: dict[str, list[str]] = {
    "PTMT – Injection (standard)": [
        "80-1", "80-2", "80-3", "80-4", "80-5", "80-6",
        "110-1", "110-2", "110-3", "125-1", "125-2",
        "150-1", "150-2", "150-3", "150-4", "150-5", "150-6", "150-7", "150-8",
        "200-2", "200-3",
        "250-1", "250-2", "250-3", "250-4", "250-5", "250-6",
        "350-1", "350-2", "130-TON", "450-1",
    ],
    "PTMT – Injection (N-line)": [
        "N-80A", "N-80B",
        "N-110A", "N-110B", "N-110C", "N-110D", "N-110E", "N-110F",
        "N-200A", "N-200B", "N-200C", "N-200D", "N-200E", "N-200F",
        "N-200G", "N-200H", "N-200I",
    ],
    "PTMT – Blow Moulding": ["Blow Mould 1", "Blow Mould 2", "Blow Mould 3"],
    "PTMT – Corrugator": ["Corrugater"],
    "PTMT – Grinding": ["GRINDER-1 (M)", "GRINDER-2 (S)", "GRINDER-3 (B)"],
}

# The grinding group is regrind/finishing: its KG is never added to plant output.
PTMT_FINISHING_GROUP = "PTMT – Grinding"

# bare machine code -> process group (flat lookup for routing).
PTMT_ROSTER: dict[str, str] = {
    code: group for group, codes in PTMT_GROUPS.items() for code in codes
}


def ptmt_roster_ids() -> set[str]:
    """The 55 authoritative PTMT machine ids, in emitted form ("PTMT <code>")."""
    return {f"PTMT {code}" for code in PTMT_ROSTER}


def ptmt_group(code: str) -> tuple[str, bool] | None:
    """(process_group, is_finishing) for a bare PTMT code, or None if unknown."""
    group = PTMT_ROSTER.get(str(code).strip())
    if group is None:
        return None
    return group, (group == PTMT_FINISHING_GROUP)
