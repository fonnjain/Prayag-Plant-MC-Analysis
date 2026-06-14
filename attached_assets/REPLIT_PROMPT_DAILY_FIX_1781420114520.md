# Fix: read the DAILY files (resolves the "0/35 machines" and 64 completeness warnings)

For sub-monthly periods (Yesterday, Last 7 days, Last week, custom < 1 month) the app is falling back to the **monthly summary** of the current month and reporting every machine as missing. The daily, date-stamped data **does exist** in the per-month "Date Sheet & Monthly Report" Google Sheets — the app must read those directly. Remove the message "the source data is monthly, so a daily breakdown isn't available yet" — it is wrong.

## 1. For any sub-monthly period, read the DAILY files (do not fall back to the monthly summary if a daily file exists)
List each plant's daily folder and read the file(s) overlapping the period. Use these (FY 26-27):

| Plant | Folder | Apr / May / Jun '26 |
|---|---|---|
| Pipe & Fitting | `1eE1xSVAvi8t4wO_eZnCvbxMjQiqBiRG6` | `1eNUSktOldFHRtM55VYfLiYp5nLDRk3ovOEdYYKfI0hU` / `17__f7pP28bIoctVXV-iku3WIlffAuonvRhCaViVu-bA` / `1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec` |
| PTMT | `1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1` | `16zsh5x4MdY8DX3H5_hw5iaOdkGixlUsPzesDVnwgfYo` / `1T1M5MT47P3D4wCwi7tX7KcL_sHVtx43NSuXFDP9Oq78` / `1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g` |
| Garden Pipe | `1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT` | `1mbxHLgvvruhI-3_d9zoqevZQxHhjxZY4cN0tIyxkzEo` / `1qmTMCWZWLsuA4kCzaAFC4fjG46Zf3rGz5VjOknv_Sy0` / `1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA` |
| HDPE | `1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60` | `1TTxcpSQyVyleermiOhYlxlcd3RE0Pay0dRHLnXSEohs` / `1-RCsS2gbtI3toyNG4uec29_coID42qCNsquaYdk-IIQ` / `1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8` |
| Tank | `1IsWgq01xLIkX0UZKnSolIL6lOToFefO_` (+ Apr in `1hlBedSVVMM7nbTn5Ylx4ecAeJ3CS1FJj`) | `1osCJ1ZF2okCdHXbhkBthvJ7T7x21warW1-NMGm-5xbc` / `1Zl8dvEZkQKGAkyWDTgLznC_yISNVznPf3pgUodHttm8` / `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo` |
| CP | `17thg66c3u0DMqy8bXjt6JSYp6sKqQISE` | latest is Jan '26 `1i0dExEu8VOSpitxsAHNcx6fly-LFpJHf3-x1Nq4DcsA` (different cycle) |

Prefer the daily file by month; list the folder so a newly-added month is picked up automatically. Only if **no** daily file exists for a needed month, fall back to that month's summary and label it monthly.

## 2. CRITICAL — Moulding daily data is INSIDE the Pipe & Fitting file
There is **no separate Injection-Moulding daily file.** The Pipe & Fitting "Date Sheet" workbook contains both:
- **Report-11 / "All Pipe M/C"** → the PIPE machines' daily M/C & item-wise production (KG & Pcs).
- **Report-12 / "All Moulding M/C"** → the MOULDING (injection) machines' daily production. Read this section for the MOULDING M/C roster.
- **Report-11(A) / "M/C Breakdown Error"** → daily breakdown/downtime (use for availability).

So when reading the Pipe daily file, parse **both** the Pipe M/C and the Moulding M/C tables.

## 3. Per-machine daily table shape (verified)
Each daily file has per-machine blocks with a per-day matrix. Example (Garden): blocks headed `MACHINE NO : 001 … 004`, with date rows `Jun 1, 2026`, `Jun 2, 2026` …, columns `CODE | SIZE | LENGTH | NOS(PCS) | TOTAL(MTR) | KG | TOTAL(KG)`; plus a per-machine per-day summary matrix with `Run Hours | Output in KG | Rejection in KG` for each date `Jun, 1 … Jun, 30`. Pipe/Moulding use `Pipe M/C-n` / Moulding rows with daily run-hours and output. Read:
- daily **output** = sum the day's KG/Pcs rows for that machine (or take the day column in the run-hours matrix),
- daily **run hours** and **rejection** from the per-day matrix,
- **breakdown hours** from Report-11(A) where present (for availability).
Aggregate days into the selected range. Never fabricate a zero for a day with no row — leave it absent.

## 4. Normalise machine names to the master roster
The daily sheets label machines differently from the roster. Map before matching:
- `Pipe M/C-1` / `Pipe M/C - 1` → `PIPE M/C - 1`
- Moulding rows (Report-12) → `MOULDING M/C - n`
- Garden `MACHINE NO : 001` / `MACHINE-1` → `GARDEN M/C - 1`
- HDPE machine → `HDPE M/C - 1`
Strip the operator prefix (e.g. `MR. NIKHIL/Pipe M/C-1` → `Pipe M/C-1`). Use a normalisation function (uppercase, collapse spaces, unify `M/C`/`M_C`/`MACHINE NO :`, strip leading zeros) so the daily machine matches the roster id.

## 5. Recompute completeness from the daily data, and right-size the warnings
- After reading daily, a machine that ran in the window shows data; the completeness card should reflect that (e.g. "Garden 4/4, Pipe n/n…"), not "0/35".
- For a **sub-monthly** window, a machine with no production on those specific days is **informational** ("no run recorded in this window"), not a per-machine WARNING. Do not emit dozens of identical warnings — summarise: "X of N machines had no run in 07–13 Jun (normal for idle/maintenance days)".
- A genuine gap (a machine that *should* report but the whole daily file is missing or unreadable) stays a warning.

## 6. Acceptance criteria
- "Last 7 days" reads the daily files; the banner no longer says daily isn't available.
- Moulding machines populate from Report-12 inside the Pipe file; Garden/HDPE/PTMT/Tank from their own daily files.
- The completeness card shows real machine coverage for the window, not "0/35"; per-machine "no data" notices are summarised, not one-per-machine.
- Machine names from daily sheets match the roster after normalisation.
- Daily output/hours reconcile to the month when summed (cross-check against the monthly summary in the confirmation layer).
