"""
Source registry — the single place that maps each report "family" to a live
Google Sheet file ID, the data tab(s) to read, and the parser layout to use.

MONTHLY WORKBOOK RESOLUTION POLICY (DAILY_SOURCES)
  1. Pinned IDs in this file are authoritative and are never overwritten.
  2. For PIPE monthly workbooks, source_registry.py performs automatic
     title-based Drive discovery for months not pinned here.  Resolved IDs are
     cached in Postgres (daily_source_registry) and in-process memory so
     subsequent requests are fast.  Callers use sheets._daily_file_id() which
     falls through to source_registry when no pin is present.

Why some months are still pinned explicitly:
  - Some workbooks live in non-standard folders or are owned by accounts
    whose files are not returned by a plain title search.
  - Pinning freezes a specific verified file — useful to prevent accidentally
    picking up a renamed or duplicate workbook.

All pinned IDs were verified accessible via the `google-sheet` connection.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Plant location registry — KH = Khandala, Bhiwari, VN = Vasna, WB = Wambori
# Used to route tank + segment-labour reports per location.
# ---------------------------------------------------------------------------
PLANT_LOCATIONS: dict[str, str] = {
  "PIPE":     "KH",
  "GARDEN":   "KH",
  "HDPE":     "KH",
  "MOULDING": "KH",
  "PTMT":     "Bhiwari",
  "CP":       "KH",
  "TANK":     "KH",     # KH daily workbook — location tag for daily records
  "TANK_VN":  "VN",
  "TANK_WB":  "WB",
  "GOM":      "KH",
}

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
      "unit": "kg",
  },
  # ---- PTMT annual Moulds Summary (segment-level monthly; no per-machine records) ----
  {
      "family": "ptmt_moulds_summary",
      "title": "PTMT Annual Moulds Summary (26-27)",
      "file_id": "1kc6AOZJR8b29TBIMprNMxQ85VbAk2BgBU0Iz5u5Se2M",
      "tab": "SUMMARY",
      "kind": "ptmt_moulds_summary",
      "segment": "PTMT",
      "plant": "PTMT",
      "unit": "kg",
      "fy": "26-27",
  },
]

# ---------------------------------------------------------------------------
# Report-only annual sources — deliberately NOT loaded by the main dashboard
# ("/"). They back the dedicated /reports/* pages and are loaded on-demand
# (sheets.load_report_records) so the main dashboard cold-start stays fast.
# Adding them to the main load path made the "/" cold load read 24 workbooks
# and time out the dev health check.
# ---------------------------------------------------------------------------
REPORT_SOURCES: list[dict] = [
  # ---- Group-of-Moulding (tonnage-band summary, both FYs) ----
  {
      "family": "gom",
      "title": "Group-of-Moulding Summary (26-27)",
      "file_id": "1WWaRBBmT8_kPWz8txQND3yrNla3nlzg6ApQIzH-h9YU",
      "tab": "Moulding M/C 26-27",
      "kind": "gom_grid",
      "segment": "Group-of-Moulding",
      "plant": "GOM",
      "unit": "kg",
      "fy": "26-27",
  },
  {
      "family": "gom",
      "title": "Group-of-Moulding Summary (25-26)",
      "file_id": "1dItgua4DeVq5ixGiskjiZTwuSsxt_ETKGzF7dfdCmAM",
      "tab": "Moulding M/C 25-26",
      "kind": "gom_grid",
      "segment": "Group-of-Moulding",
      "plant": "GOM",
      "unit": "kg",
      "fy": "25-26",
  },
  # ---- Tank VN annual (summary-only, no daily workbook for VN/WB) ----
  {
      "family": "tank_vn",
      "title": "Tank VN Annual (26-27)",
      "file_id": "1Wa2jFV66NS-ntlSKqo8jzFFwgZfcdvgJYEAuFU0qdAI",
      "tab": "Sheet1",
      "kind": "tank_annual_2627",
      "segment": "Tanks",
      "plant": "TANK_VN",
      "unit": "Ltr",
      "fy": "26-27",
      "location": "VN",
      "grain": "summary-only",
  },
  {
      "family": "tank_vn",
      "title": "Tank VN Annual (25-26)",
      "file_id": "1fe2ZgL8EcuUVkvjC3-mZ5Pr8WkXWQ5V70AiwkbDUh-0",
      "tab": "SUMMARY (LTR)",
      "kind": "tank_annual_2526",
      "segment": "Tanks",
      "plant": "TANK_VN",
      "unit": "Ltr",
      "fy": "25-26",
      "location": "VN",
      "grain": "summary-only",
  },
  # ---- Tank WB annual (summary-only, no daily workbook for VN/WB) ----
  {
      "family": "tank_wb",
      "title": "Tank WB Annual (26-27)",
      "file_id": "1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag",
      "tab": "Sheet1",
      "kind": "tank_annual_2627",
      "segment": "Tanks",
      "plant": "TANK_WB",
      "unit": "Ltr",
      "fy": "26-27",
      "location": "WB",
      "grain": "summary-only",
  },
  {
      "family": "tank_wb",
      "title": "Tank WB Annual (25-26)",
      "file_id": "1mtgkCbNsWsSrgjJfN2zc7SDb2ysHoH11xG3afr0oovc",
      "tab": "SUMMARY (LTR)",
      "kind": "tank_annual_2526",
      "segment": "Tanks",
      "plant": "TANK_WB",
      "unit": "Ltr",
      "fy": "25-26",
      "location": "WB",
      "grain": "summary-only",
  },
  # ---- Tank KH 25-26 history (already daily for 26-27; this covers last FY) ----
  {
      "family": "tank_kh",
      "title": "Tank KH Annual (25-26)",
      "file_id": "1_6Foa8TXXP-xr0KIx04q7i8iigkrLjuT8r7uG62x8qQ",
      "tab": "SUMMARY (LTR)",
      "kind": "tank_annual_2526",
      "segment": "Tanks",
      "plant": "TANK",
      "unit": "Ltr",
      "fy": "25-26",
      "location": "KH",
      "grain": "summary-only",
  },
  # ---- Tank KH 26-27 annual (JUN only in source; KH daily covers APR+MAY+JUN) ----
  {
      "family": "tank_kh",
      "title": "Tank KH Annual (26-27)",
      "file_id": "1T4RDvDNqxqbsL3zRWoTPcijdvQGPQjtBTw8S0qe98rs",
      "tab": "Sheet1",
      "kind": "tank_annual_2627",
      "segment": "Tanks",
      "plant": "TANK",
      "unit": "Ltr",
      "fy": "26-27",
      "location": "KH",
      "grain": "summary-only",
  },
  # ---- Segment Labour (both FYs) ----
  {
      "family": "seg_labour",
      "title": "Segment Labour (26-27)",
      "file_id": "1ttlpHLrlTsimcdSmk3-HGnPu14PX7SGtk9Of2Q5pDvw",
      "tab": None,          # multi-tab: UNIT-1 / UNIT-2 / UNIT-3
      "kind": "seg_labour",
      "segment": "Labour",
      "plant": "ALL",
      "unit": "cost",
      "fy": "26-27",
  },
  {
      "family": "seg_labour",
      "title": "Segment Labour (25-26)",
      "file_id": "1N6gVEZyv1CLs5ARQHeebjAxOyvdkwOJFPqDWHUUOy_g",
      "tab": None,
      "kind": "seg_labour",
      "segment": "Labour",
      "plant": "ALL",
      "unit": "cost",
      "fy": "25-26",
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
          # FY2026-27 (current year).
          "2026-04": "1eNUSktOldFHRtM55VYfLiYp5nLDRk3ovOEdYYKfI0hU",
          "2026-05": "17__f7pP28bIoctVXV-iku3WIlffAuonvRhCaViVu-bA",
          "2026-06": "1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec",
          # July-2026 is owned by bhawna@ (NOT preeti@). Cross-owner, but still
          # readable because it was shared with the connected account — discovery
          # by folder is impossible (drive.file scope), so it is pinned by ID.
          "2026-07": "1y2HRoJNQmE2BthE0f18YU1w0ly1LMvyqP98f2_4Wero",
          # Aug-2026 owned by preeti.chauhan@prayagindia.com.
          # Title: "5. Pipe & Fitting Plant Date Sheet & Monthly Report - AUG ' 2026"
          "2026-08": "1Crlyfg8EpBRwR5sylgqCxF7r1boeD3UjSDQwLB10g7E",
          # FY2025-26 (prior year). The header-based Report-5/Report-11/Report-12
          # readers parse the older FY2025-26 layout with no code change, so these
          # feed PIPE (Report-5 ↔ Report-11 reconciliation) AND MOULDING (Report-12)
          # for the prior year. Owned by preeti.chauhan@prayagindia.com.
          "2025-04": "10GHWEV7pY_qcpFLmXqKwwDtmidAjL6FG0MOtAwUJ484",
          "2025-05": "13cC3moR19el7Q3pYYNq08P5YXXZBh_lbgxGt9yagrRQ",
          "2025-06": "1xkWuDcyTegPJCOAzOMdIzBl75xzhXUm2MwXO5TZdkgg",
          "2025-07": "1zE3D83XSgTE-Z4tuLvwP1bQfnPzEjefqrTOJ7vexix8",
          # Aug-2025: EMPTY workbook (Report-5 AND Report-11 all zeros) — listed in
          # EMPTY_SOURCES so it is surfaced as "empty / awaiting source", never a
          # real zero-output month. The idle detector already yields no rows.
          "2025-08": "1zVCB6taXefFOR6U3tJh5QjokQCtt9wTJBitQxm0-w2w",
          "2025-09": "1ATjAaTkoqf3Bz5BHYXdb8fzgc1ah0RDz5L_07KIGdTI",
          "2025-10": "1zzaNoN1F9LC7FX3FAI2PKMR5Y35MT94hqSBXPoTcMZw",
          "2025-11": "1oYDIFrPYJ9BhLS35Ss4RUNFUz1gJW5Dsl76uJL_omX8",
          "2025-12": "1wyMZVW8q0AxSjOS0JKAPLtf4eV57_K_h47t9VwwlTko",
          "2026-01": "1vaj-Ex3rgWV6QA4VQlgIhOubMyagWv1XeoHBvTS6bU8",
          "2026-02": "1cdrzhx5hYwU8dLo0AT65YWy66J8SmtF_vKRgzPK2UAI",
          "2026-03": "1waJo0TZivjwg-JLPdV_JXaBH4oBiP16CqY_Wme5l0ns",
      },
  },
  "PTMT": {
      "folder_ids": ["1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1"],
      "files": {
          "2026-04": "16zsh5x4MdY8DX3H5_hw5iaOdkGixlUsPzesDVnwgfYo",
          "2026-05": "1T1M5MT47P3D4wCwi7tX7KcL_sHVtx43NSuXFDP9Oq78",
          "2026-06": "1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g",
          "2026-07": "1AjMLfcBkI0rGY8JdYP3MO8Ocn8lO-HIpol1tHgvK9O8",
      },
  },
  "GARDEN": {
      "folder_ids": ["1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT"],
      "files": {
          "2026-04": "1mbxHLgvvruhI-3_d9zoqevZQxHhjxZY4cN0tIyxkzEo",
          "2026-05": "1qmTMCWZWLsuA4kCzaAFC4fjG46Zf3rGz5VjOknv_Sy0",
          "2026-06": "1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA",
          "2026-07": "1e-JqC-_htMWp6jPYze2uJx3qHEJEd_qoAZybwbdmuQg",
      },
  },
  "HDPE": {
      "folder_ids": ["1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60"],
      "files": {
          "2026-04": "1TTxcpSQyVyleermiOhYlxlcd3RE0Pay0dRHLnXSEohs",
          "2026-05": "1-RCsS2gbtI3toyNG4uec29_coID42qCNsquaYdk-IIQ",
          "2026-06": "1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8",
          "2026-07": "1M4oXFOmKelpCYpGDatjReTF5scJwcg8saFVPcRRORtA",
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
  # Tank VN — Vasna stream (Tank (PRV) workbooks, one per month).
  "TANK_VN": {
      "folder_ids": ["1kI-g46eR-GBEr0-8sUGPV_ZEngFlC_Tt"],
      "files": {
          "2026-06": "1Vsba-WDcYwSstEZsX37ntm_N05yPn0T5DzSkls9zRUw",
          "2026-07": "1lUSTSM_m2yywxGeeE7oemRbBMNsyM37ICv1lKBClGtQ",
      },
  },
  # Tank WB — Wambori stream (Tank (PDWB) workbooks, one per month).
  "TANK_WB": {
      "folder_ids": ["14Wp1OGomlm6FeOLs0AcCFeIMxQ_zmjLx"],
      "files": {
          "2026-07": "1-JVeDFTnFfoMjDMhvkOV5BE1rKjjO00chRKtUpO5iqQ",
      },
  },
  # CP runs on a different cycle; no Apr-26+ daily file yet (flagged at load).
  "CP": {
      "folder_ids": ["17thg66c3u0DMqy8bXjt6JSYp6sKqQISE"],
      "files": {},
  },
}

# (plant, "YYYY-MM") workbooks that are wired but KNOWN to be empty templates
# (all-zero Report tabs). They are listed so the file ID is retained, but the
# loader skips the read and surfaces them as "empty / awaiting source" — never a
# real zero-output month (which would wrongly drag down averages).
EMPTY_SOURCES: set[tuple[str, str]] = {
  ("PIPE", "2025-08"),
}

# Friendly names for plants/segments shown in the UI.
PLANT_NAMES = {
  "PIPE":     "Pipe & Fitting",
  "GARDEN":   "Garden Pipe",
  "HDPE":     "HDPE",
  "MOULDING": "Injection Moulding",
  "PTMT":     "PTMT",
  "CP":       "CP Fittings",
  "TANK":     "Tanks (KH)",
  "TANK_VN":  "Tanks (VN)",
  "TANK_WB":  "Tanks (WB)",
  "GOM":      "Group-of-Moulding",
  "ALL":      "All Plants",
}

FY_MONTHS = [
  "2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09",
  "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03",
]

FY_MONTHS_2526 = [
  "2025-04", "2025-05", "2025-06", "2025-07", "2025-08", "2025-09",
  "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03",
]

# Tonnage-band definitions for Group-of-Moulding.
# Machine counts in each band (25 total) — used to validate the grid.
GOM_BANDS: list[str] = ["150", "200", "250", "275", "350", "450"]

# Machine-label prefix patterns → tonnage band (first match wins).
GOM_BAND_PREFIXES: list[tuple[str, str]] = [
  ("C-150", "150"),
  ("C-200", "200"),
  ("C-250", "250"),
  ("C-275", "275"),
  ("C-350", "350"),
  ("C-450", "450"),
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


def gom_band(label: str) -> str:
  """Tonnage band for a GOM machine label (e.g. 'C-150-1' → '150')."""
  u = str(label).strip().upper()
  for prefix, band in GOM_BAND_PREFIXES:
      if u.startswith(prefix.upper()):
          return band
  return "Other"


# ---------------------------------------------------------------------------
# Planning sources — demand / stock / piece-count tabs inside existing daily
# workbooks. NEVER read on "/" — loaded on-demand by /planning routes only.
# File IDs are deliberately NOT duplicated here; use planning_file_id() which
# delegates to DAILY_SOURCES so there is a single source of truth.
# ---------------------------------------------------------------------------
PLANNING_SOURCES: dict = {
    "PIPE": {
        "tabs": [
            {"tab": "Report-1",     "family": "CPVC", "parser": "pipe_report1"},
            {"tab": "Report-1 (A)", "family": "UPVC", "parser": "pipe_report1"},
            {"tab": "Report-1 (B)", "family": "AGRI", "parser": "pipe_report1"},
            {"tab": "Report-1 (C)", "family": "SWR",  "parser": "pipe_report1"},
        ],
        # Phase 2B — material / BOP / packaging stock readiness (weekly snapshot).
        # 'Stock Days' in PIPE is a pre-computed sheet cell; the parser stores it
        # as stock_days_sheet for cross-check and always recomputes days_of_cover.
        "material_tabs": [
            {"tab": "Report-2", "category": "RM",   "parser": "material_stock"},  # 42 items
            {"tab": "Report-3", "category": "BOP",  "parser": "material_stock"},  # 31 items (Buffer Stock in Days)
            {"tab": "Report-4", "category": "PACK", "parser": "material_stock"},  # 15 items
        ],
        # Phase 2C — maintenance master (Report-16) + manpower per machine per date (Report-22).
        # Header row 3 (1-indexed); 73 PIPE machines.
        "maintenance_tabs": [
            {"tab": "Report-16", "parser": "maintenance"},          # 73 machines
        ],
        # Report-22 (A) and (B) share the same layout; each covers a portion of the month.
        # True header spans rows 2-3 (1-indexed): dates in row 2, TOTAL MANPOWER/TOTAL HOURS in row 3.
        "manpower_tabs": [
            {"tab": "Report-22 (A)", "parser": "pipe_manpower", "shift": "all"},
            {"tab": "Report-22 (B)", "parser": "pipe_manpower", "shift": "all"},
        ],
        # Phase 2D — daily production pivot (kg) with wastage + pulverizer per type (Report-15);
        # daily pcs by pipe type (Report-13) and fittings type (Report-14).
        # Header auto-detected; NEVER loaded on "/".
        "yield_tabs": [
            {"tab": "Report-15", "parser": "yield_report15"},   # kg: prod+wastage+pulverizer per type
            {"tab": "Report-13", "parser": "yield_report13"},   # pcs: CPVC/UPVC/SWR/AGRI daily
            {"tab": "Report-14", "parser": "yield_report14"},   # pcs: fittings daily
        ],
        # Phase 2D — compound mixer batch logs (Report-5(A/B/C/D)).
        # DISTINCT from /compound CP-fittings mass-balance (compound.py).
        # mixer_availability = running_hours / (running_hours + breakdown_hours).
        "mixer_tabs": [
            {"tab": "Report-5(A)", "parser": "mixer_batch", "mixer_id": "A"},
            {"tab": "Report-5(B)", "parser": "mixer_batch", "mixer_id": "B"},
            {"tab": "Report-5(C)", "parser": "mixer_batch", "mixer_id": "C"},
            {"tab": "Report-5(D)", "parser": "mixer_batch", "mixer_id": "D"},
        ],
        # Phase 2D — toolroom job log (Report-21); ~24 job rows per month.
        "toolroom_tabs": [
            {"tab": "Report-21", "parser": "toolroom"},
        ],
    },
    "PTMT": {
        "tabs": [
            {"tab": "Report-1",     "family": "faucet",    "parser": "ptmt_report1"},
            {"tab": "Report-1(A)",  "family": "cistern",   "parser": "ptmt_report1"},
            {"tab": "Report-1(B)",  "family": "seatcover", "parser": "ptmt_report1"},
        ],
        "report7_tab": "Report-7",
        "master_tab":  "MASTER",
        # Phase 2B — PTMT material stock readiness.
        # R2/R3 header at sheet row 3; R4 header at row 4 (auto-detected).
        # No 'Stock Days' column — days_of_cover computed only.
        # Report-4 uses 'CODE' instead of 'ITEM CODE' for the item-code header.
        "material_tabs": [
            {"tab": "Report-2", "category": "BOP",  "parser": "material_stock"},  # 53 items
            {"tab": "Report-3", "category": "PACK", "parser": "material_stock"},  # 32 items
            {"tab": "Report-4", "category": "RM",   "parser": "material_stock"},  # 21 items
        ],
        # Phase 2C — maintenance master (Report-8) + manpower per shift (Report-6 A/B/C).
        # Header row 3 (1-indexed); 60 PTMT machines.
        "maintenance_tabs": [
            {"tab": "Report-8", "parser": "maintenance"},           # 60 machines
        ],
        # Report-6 (A/B/C) = 1st/2nd/3rd shift; header row 3 (1-indexed) = dates.
        # Row 4 = sub-headers (shift label + P/C type per date). Data from row 5.
        # CRITICAL: These are manpower/shift rosters — NEVER read as production output.
        "manpower_tabs": [
            {"tab": "Report-6 (A)", "parser": "ptmt_manpower", "shift": "1st"},
            {"tab": "Report-6 (B)", "parser": "ptmt_manpower", "shift": "2nd"},
            {"tab": "Report-6 (C)", "parser": "ptmt_manpower", "shift": "3rd"},
        ],
        # Phase 2D — scrap/wastage recovery master (Report-10); ~33 rows.
        # Unit varies (KG/PCS/LTR) — NEVER sum across units.
        "wastage_tabs": [
            {"tab": "Report-10", "parser": "wastage"},
        ],
    },
}

PLANNING_FAMILY_LABELS: dict = {
    "CPVC":      "CPVC Pipe & Fittings",
    "UPVC":      "UPVC Pipe & Fittings",
    "AGRI":      "Agri Pipe & Fittings",
    "SWR":       "SWR Pipe & Fittings",
    "faucet":    "Faucets (PTMT)",
    "cistern":   "Cisterns (PTMT)",
    "seatcover": "Seat Covers & Accessories (PTMT)",
}


def planning_file_id(plant: str, ym: str) -> str | None:
    """Return the workbook file_id for planning tabs of *plant* in *ym*.

    The planning tabs live inside the same workbooks as DAILY_SOURCES so no
    separate file registration is needed.
    """
    src = DAILY_SOURCES.get(plant, {})
    return src.get("files", {}).get(ym)


def planning_months(plant: str) -> list[str]:
    """Sorted list of year-months available in DAILY_SOURCES for *plant*."""
    src = DAILY_SOURCES.get(plant, {})
    return sorted(src.get("files", {}).keys(), reverse=True)
