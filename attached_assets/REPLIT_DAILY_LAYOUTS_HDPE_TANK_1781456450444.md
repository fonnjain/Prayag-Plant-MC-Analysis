# Daily layouts — HDPE and Tank (verified from the live June files)

Read live from the June daily Google Sheets. Both have a clean **"Daily Report"** tab that is a ready-made per-machine daily matrix (same family as PTMT Report-5). Use that tab — not the per-item / per-machine detail tabs.

---
## A. HDPE — tab "Daily Report"  (file June `1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8`, folder `1YaS66Ef7wKOvTVtBHjMD5QFBquCX5r60`)

**Two earlier assumptions were WRONG — correct them:**
1. **HDPE has 6 machines, not 1.** Tabs MACHINE 1–6. Roster must list `HDPE M/C - 1 … HDPE M/C - 6` (plus a `DANA MC` tab — granulator/recycle; tag as support, not pipe output).
2. **HDPE has run hours AND an in-sheet ideal-output baseline.** Earlier "output only, no run hours" was wrong.

**Layout of "Daily Report":** one row per machine.
- Header r3: `MACHINE | Ideal Output | RUN HOURS | OUTPUT (KG) | REJECTION (KG) | Rejection % | Average Per Hour | %age of Efficiency | M/C Run Hour | M/C Efficiency | <date1> | | | <date2> | …`
- Under each date, three sub-columns (r4): `Run Hours | Output in KG | Rejection in KG`.
- Rows r5 TOTAL, then r6+ machines. Each machine row carries **`Ideal Output`** (e.g. 500/350/450/400/250/500) — this is HDPE's **in-sheet baseline**; use it for efficiency. Compute utilisation from RUN HOURS.
- Machine id = the canonical `M/C-1…6` (column 2). Column 1 is an alias/old code — ignore it.

**So for HDPE:** do **not** put HDPE in `baselines.json` — it supplies its own Ideal Output. Read per-machine daily Run Hours / Output (KG) / Rejection from the date columns. (June is mostly 0 so far — early month, expected; that's idle, not missing.)

---
## B. Tank — tabs "Daily Report" (machine-level) + "PROD. REPORT" (item-level)  (file June `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo`)

Tank has **both** a machine-level daily matrix and a per-item production report. It is primarily **per-item rotomoulding**, so report Tank at the **plant level** with item detail; the machine matrix is sparse.

- **"PROD. REPORT" (the authoritative output)** — one row per item per date: `DATE | ITEM CODE | SIZE (LTR.) | COLOR | TANK WEIGHT | PRODUCTION HOURS | NO. OF CYCLE | PRODUCTION IN PCS | PRODUCTION IN (…) | REJECTION IN PCS | REJECTION MOUTH | PRODUCTION IN KG | REJECTION IN KG`. Date rows like `2026-06-01`, item codes like `WT-ISI-10`, `WT-3LL-05`. **Daily Tank output = sum that day's PRODUCTION (pcs and/or kg) across items**; rejection likewise. There IS a `PRODUCTION HOURS` column here (sparsely filled) — use it where present, else report output-only.
- **"Daily Report"** — a machine-level matrix (`MACHINE-1 …` with per-date Run Hours / Output KG / Rejection) but it is **mostly empty** (Tank isn't tracked per machine). Don't rely on it; don't flag its blank machine rows as "missing machines".
- **There is a "Grinding" tab** — finishing; tag as support, don't add to tank output.

**So for Tank:** plant-level output/rejection/reject% from PROD. REPORT (by item), no machine roster, no machine-level OEE. "Tank May returned no data" = the parser didn't read PROD. REPORT — a **parse gap, not a missing file**. Score completeness as "Tank reporting yes/no", not against machines.

---
## C. Net corrections to apply
1. **HDPE roster = 6 machines** (`HDPE M/C-1…6`) + DANA MC (support). Not 1.
2. **HDPE uses its in-sheet Ideal Output** (like PTMT) → remove HDPE from the `baselines.json` requirement; utilisation/efficiency ARE computable for HDPE.
3. **Tank = plant-level, item-based** (PROD. REPORT); no machine roster, no machine OEE; "no data" was a parse gap.
4. Both files have data — "missing workbook / re-export" is the wrong diagnosis; finish the parser. A file that opens with 0 extracted rows must flag **"parse failed"**, never "missing".

## D. Acceptance
- HDPE shows 6 machines with daily Run Hours / Output / Rejection from "Daily Report"; efficiency uses the in-sheet Ideal Output; HDPE not flagged "no baseline".
- Tank shows plant-level daily output + rejection (+ reject%) from "PROD. REPORT" by item; no per-machine gaps flagged for Tank.
- Neither HDPE June nor Tank May reports as "missing workbook".
