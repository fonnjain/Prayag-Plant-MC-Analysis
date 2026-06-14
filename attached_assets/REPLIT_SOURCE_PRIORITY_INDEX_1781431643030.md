# Source Priority Index — read order (live daily first, summaries/legacy as fallback + history)

This is the **single ordered source-of-truth list**. For any period, the app resolves data in this priority order and stops at the first tier that supplies it. Daily "Date Sheet" workbooks are the **live, authoritative** source; the annual summaries and prior-year files are **fallback + analytics history**, not the primary read.

## Priority order (highest first)

### P1 — LIVE DAILY (authoritative; read first)
The current-FY per-month "Date Sheet & Monthly Report" Google Sheets. These are updated daily and are the source of truth for current figures and for any sub-monthly period (yesterday / last 7 days / last week / custom). For monthly/FY too, prefer the daily files and aggregate up; only drop to P2 if a daily file is missing/unreadable.

| Plant | Daily folder | Apr / May / Jun '26 file IDs | Notes |
|---|---|---|---|
| Pipe & Fitting | `1eE1xSVAvi8t4wO_eZnCvbxMjQiqBiRG6` | `1eNUSktOldFHRtM55VYfLiYp5nLDRk3ovOEdYYKfI0hU` / `17__f7pP28bIoctVXV-iku3WIlffAuonvRhCaViVu-bA` / `1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec` | **Pipe M/C = Report-11; Moulding M/C = Report-12 (same file)**; breakdown = Report-11(A) |
| PTMT | `1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1` | `16zsh5x4MdY8DX3H5_hw5iaOdkGixlUsPzesDVnwgfYo` / `1T1M5MT47P3D4wCwi7tX7KcL_sHVtx43NSuXFDP9Oq78` / `1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g` | Report-5 "Output & M/C Hours"; IDEAL HOUR baseline is in-sheet |
| Garden Pipe | `1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT` | `1mbxHLgvvruhI-3_d9zoqevZQxHhjxZY4cN0tIyxkzEo` / `1qmTMCWZWLsuA4kCzaAFC4fjG46Zf3rGz5VjOknv_Sy0` / `1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA` | per-machine blocks + per-day matrix |
| HDPE | `1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60` | `1TTxcpSQyVyleermiOhYlxlcd3RE0Pay0dRHLnXSEohs` / `1-RCsS2gbtI3toyNG4uec29_coID42qCNsquaYdk-IIQ` / `1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8` | Garden-style layout |
| Tank | `1IsWgq01xLIkX0UZKnSolIL6lOToFefO_` (+Apr `1hlBedSVVMM7nbTn5Ylx4ecAeJ3CS1FJj`) | `1osCJ1ZF2okCdHXbhkBthvJ7T7x21warW1-NMGm-5xbc` / `1Zl8dvEZkQKGAkyWDTgLznC_yISNVznPf3pgUodHttm8` / `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo` | small layout |
| CP | `17thg66c3u0DMqy8bXjt6JSYp6sKqQISE` | latest Jan '26 `1i0dExEu8VOSpitxsAHNcx6fly-LFpJHf3-x1Nq4DcsA` | different cycle; may lag |

List each folder so a newly-added month's file is picked up automatically.

### P2 — CURRENT-FY SUMMARY (fallback + reconciliation)
The 14 annual summary workbooks (FY 26-27). Use **only** when a P1 daily file for the needed month is missing/unreadable, **and** always as the reconciliation cross-check (daily summed to a month must match the summary). Never the primary read when P1 exists. (IDs are in `Source_File_Index.xlsx`.)

### P3 — LEGACY / PRIOR-YEAR (analytics history only)
Prior-year daily folders and prior-year summary Sheets (FY 25-26, 24-25, 23-24). Used for **YoY and trend analytics only** — loaded once, then frozen (immutable). Never used for current figures.

## Resolution rule (what the loader does)
```
for the requested period:
  1. P1: for each plant, read the daily file(s) overlapping the period.
         - if found and parses → USE IT. stop.
         - if file opens but yields 0 rows → flag "parse failed" (do NOT fall through silently).
         - if no daily file exists for that month → fall to P2 for that plant/month, label "monthly".
  2. P2: read the summary workbook for the month (fallback). Always also compute the
         daily-vs-summary reconciliation check.
  3. P3: only for YoY/history windows.
```
- **Freshness:** when both a daily and a summary value exist for the same month, the **daily value wins** as the published figure; the summary is the cross-check, not the source.
- **Provenance:** every stored row records which tier it came from (`source_tier = P1_daily | P2_summary | P3_legacy`) and the file id — so the dashboard/audit can show "live daily" vs "from monthly summary (daily unavailable)".
- **Never** prefer a summary over an available daily file. Never use P3 for current figures.

## Why this order
- Daily files are updated continuously and carry true day-grain + run/breakdown hours → correct current figures and real OEE.
- Summaries are monthly roll-ups → good when daily is briefly unavailable, and as an independent reconciliation.
- Prior-year files are stable history → ideal for YoY/trend, but must not influence current numbers.

## Acceptance
- For a current/sub-monthly period, the app reads P1 daily for every plant that has a daily file; the dashboard labels the figure "live daily".
- P2 is used only where a daily file is genuinely absent, and that figure is labelled "monthly (daily unavailable)".
- Daily-vs-summary reconciliation runs regardless and is shown.
- `source_tier` is recorded on every row; no current figure is ever sourced from P3.
