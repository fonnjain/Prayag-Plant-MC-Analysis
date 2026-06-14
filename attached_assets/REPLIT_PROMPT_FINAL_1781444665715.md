# Prayag Analytics — consolidated build spec (current task)

This is the single source of truth for the current build. It supersedes the scattered fix notes. Everything below is verified against the live Google Sheets. **Scope now = make the existing Sheets-based app read and compute correctly.** The Postgres migration / daily-only refresh is a separate, deferred milestone (last section) — do not start it until daily parsing is verified for every plant.

## 0. Core principles (never violate)
- **No LLM in the data path.** Reading, parsing and all arithmetic are deterministic code. Claude is used only for advisory review and prose — never to produce, read-as-authoritative, or alter a number.
- **Daily Date Sheets are the source of truth.** Read them directly. Never silently fall back to a monthly summary; summaries are incomplete (proven below).
- **Nothing is fudged or auto-corrected.** A blank is a gap, never a zero. An impossible value is quarantined and flagged for source correction — never transformed.
- **Compute from raw.** Recompute every ratio (utilisation, efficiency, OEE) from raw hours/output. Ignore stored `%` cells.

## 1. Source read order
1. **Daily files first** for any period (sub-monthly and monthly): list each plant's daily folder, read the file(s) overlapping the window. Aggregate days → the requested range. Monthly/FY = sum the daily facts.
2. If a daily file for a needed month genuinely does not exist (e.g. CP), show "no daily data for this period" (informational) — do **not** substitute a summary into the live figure.
3. Summaries are **not** a live source — they are only an on-demand reconciliation check (Section 7).

### Daily files (FY 26-27) — list the folder so new months auto-appear
| Plant | Folder | Apr / May / Jun '26 |
|---|---|---|
| Pipe & Fitting | `1eE1xSVAvi8t4wO_eZnCvbxMjQiqBiRG6` | `1eNUSktOldFHRtM55VYfLiYp5nLDRk3ovOEdYYKfI0hU` / `17__f7pP28bIoctVXV-iku3WIlffAuonvRhCaViVu-bA` / `1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec` |
| PTMT | `1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1` | `16zsh5x4MdY8DX3H5_hw5iaOdkGixlUsPzesDVnwgfYo` / `1T1M5MT47P3D4wCwi7tX7KcL_sHVtx43NSuXFDP9Oq78` / `1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g` |
| Garden Pipe | `1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT` | `1mbxHLgvvruhI-3_d9zoqevZQxHhjxZY4cN0tIyxkzEo` / `1qmTMCWZWLsuA4kCzaAFC4fjG46Zf3rGz5VjOknv_Sy0` / `1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA` |
| HDPE | `1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60` | `1TTxcpSQyVyleermiOhYlxlcd3RE0Pay0dRHLnXSEohs` / `1-RCsS2gbtI3toyNG4uec29_coID42qCNsquaYdk-IIQ` / `1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8` |
| Tank | `1IsWgq01xLIkX0UZKnSolIL6lOToFefO_` (+Apr `1hlBedSVVMM7nbTn5Ylx4ecAeJ3CS1FJj`) | `1osCJ1ZF2okCdHXbhkBthvJ7T7x21warW1-NMGm-5xbc` / `1Zl8dvEZkQKGAkyWDTgLznC_yISNVznPf3pgUodHttm8` / `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo` |
| CP | `17thg66c3u0DMqy8bXjt6JSYp6sKqQISE` | latest Jan '26 `1i0dExEu8VOSpitxsAHNcx6fly-LFpJHf3-x1Nq4DcsA` (different cycle) |

## 2. Parsing — ONE authoritative tab per metric (fixes the double-count)
These daily workbooks repeat the same output in several tabs. Summing more than one inflates the total (this caused the +31.8% Pipe / +17.2% Moulding gap). Sum **detail rows of exactly one tab per metric**, then assert it equals that tab's TOTAL row as the reconciliation check.

- **Pipe output** → file's **Report-11**, column **"Weight"** (actual output KG), rows with a real date only. *Verified: May detail = TOTAL = 107,609 KG.* Do NOT also add Report-13 / Summary / Summary-of-Report-5 — they re-present the same output.
- **Moulding (injection) output** → **same Pipe file, Report-12**, column **"Wt in Kgs"**, dated rows only. *Verified: May = 75,771 KG.* (There is no separate Moulding daily file.)
- **Breakdown / downtime** → Report-11(A).
- **PTMT** → **Report-5 "Output & M/C Hours"**: one row per machine; fixed left cols `M/C NO. | TOTAL RUN HOUR | IDEAL HOUR | %util | TOTAL(Kg)` then repeating per-date pairs `Run Hours | Output in Kg`. Use IDEAL HOUR as PTMT's baseline (in-sheet). Read the grinder sub-table separately; tag grinders as finishing (don't add grinder KG to output).
- **Garden / HDPE / Tank** → per-machine blocks `MACHINE NO : 001…` with date rows (`Jun 1, 2026`) for output/PCS/KG, plus a per-day matrix `Run Hours | Output in KG | Rejection in KG`. If a file opens but yields 0 rows → raise **"parse failed"**, never "no data / source missing".

## 3. Machine roster + normalisation
Normalise daily machine codes to roster ids: uppercase, collapse spaces, unify `M/C`/`M_C`/`MACHINE NO :`, strip leading zeros, strip operator prefixes (`MR. X/…`, `MACHINE OPERATOR/…`).
- Pipe Report-11 → `PIPE M/C - n`; Report-12 → `MOULDING M/C - n`; Garden `MACHINE NO : 001` → `GARDEN M/C - 1`; HDPE → `HDPE M/C - 1`.
- **PTMT = 55 machines, grouped by process** (add all to the roster as `PTMT <code>`):
  - **Injection (standard):** 80-1…80-6, 110-1/2/3, 125-1/2, 150-1…150-8, 200-2/3, 250-1…250-6, 350-1/2, 130-TON, 450-1
  - **Injection (N-line):** N-80A/B, N-110A–F, N-200A–I
  - **Blow Moulding:** Blow Mould 1/2/3
  - **Corrugator:** Corrugater
  - **Grinding:** GRINDER-1 (M), GRINDER-2 (S), GRINDER-3 (B)
- **Tank has NO machine roster** — it is per-item rotomoulding. Report Tank at the **plant level** (daily output + rejection + reject%), with item-code detail underneath. Score Tank completeness as "reporting yes/no for the period," not against a machine roster. Tank has no machine-hours → no OEE/utilisation/efficiency (show "output only", not blank A/P/Q).

## 4. Calculations + planned-hours baseline
- OEE = A×P×Q; Attainment = actual/planned; Reject% = reject/total; Utilisation = actual_hours / planned_hours; Efficiency = actual_output / ideal_output. Recompute all from raw.
- **Planned-hours baseline (utilisation/efficiency denominator) is config, not a measured fact:**
  - **PTMT** uses its in-sheet `IDEAL HOUR` — do NOT flag PTMT as "no baseline".
  - **PIPE / MOULDING / GARDEN / HDPE** read planned hours from `baselines.json` (per machine). Until set, show raw output + hours and mark utilisation/efficiency **"no baseline set"** (do not compute against the 500-h placeholder, do not hide the plant).
  - Keep both the sheet's ideal value and the config baseline; record `ideal_source` (config|sheet). Never alter measured facts.

## 5. Validation tiers + sign-off
- **Tier 1 Completeness** (vs roster): a machine in the roster with no data = gap (not zero). A month that **hasn't ended yet = "in progress" (informational, non-blocking)**, not "overdue". For sub-monthly windows, machines that simply didn't run are summarised ("X of N had no run in window"), not one warning each.
- **Tier 2 Reconciliation:** detail rows == the tab's own TOTAL (Section 2). 
- **Tier 3 Validity (hard error → quarantine the row, exclude it, keep the rest):** `actual_hours > calendar_hours_in_period` (days×24), reject > total, downtime > shift, negatives. (Utilisation > 100% is NOT here.)
- **Tier 4 Plausibility (warning, never block):** utilisation over 100% (above planned baseline), outliers vs the machine's **own history / process group** (not a cross-plant or cross-process median), sudden zeros, duplicates, unit mismatch.
- **Never auto-correct.** Quarantine + flag for source fix; the next pull clears it.
- **Sign-off ready** when there are no un-quarantined hard errors in the completed months. Quarantined rows + warnings are listed as notes.

## 6. Claude review + model tiering (advisory only)
- After the deterministic checks, Claude reviews the flags and writes a plain-English data-quality assessment + publish/review/hold recommendation. It never computes, changes, or auto-publishes a number; the deterministic gate decides what is blocked.
- Model by cadence (from config): fast (`claude-sonnet-4-6`) for daily/weekly; deep (`claude-opus-4-8`) for monthly/quarterly/board; record the model used. Tiering changes prose depth only, never the numbers.

## 7. Verification (read-only)
Keep the verification view, but make the daily-vs-summary check **like-for-like (gross vs gross)** and **non-blocking**. It is a periodic check, not a gate. Verified facts: Pipe May daily = 107,609 KG; Moulding May daily = 75,771 KG; these are correct — the summaries are the incomplete side (Pipe summary tracks only 6 machines; the Moulding summary's own SUMMARY tab is misaligned with its grid for M/C-17/M/C-20). Do not "reconcile down" to a summary.

## 8. Acceptance criteria
- Yesterday / Last-7-days read daily files; no "daily breakdown not available" banner; real machine coverage, not "0/35".
- Pipe May reconciles to 107,609 and Moulding May to 75,771 (one authoritative tab per metric).
- Garden/HDPE/Tank/PTMT parse to per-machine (or plant-level for Tank) daily rows; a file that opens with 0 rows is flagged "parse failed".
- PTMT shows 55 machines in 5 process groups; outliers compared within group/own history; PTMT utilisation uses in-sheet IDEAL HOUR.
- Validity: an impossible-hours row is quarantined and the rest of the period still signs off; a marginal over-100% utilisation is a warning, not an error.
- No source value is ever modified by the app.

---
## DEFERRED — next milestone (do NOT start yet)
Only after Section 8 passes for every plant: migrate to Postgres as the read store; make the **daily files the only recurring source**; read summaries/prior-year **once** as a frozen one-time backfill (immutable, deduped via natural key + upsert); aggregate monthly/FY from daily facts in the DB; keep the daily-vs-summary check on-demand on a gross-vs-gross basis. This is a separate, planned change — not part of the current task.
