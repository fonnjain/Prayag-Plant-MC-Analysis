# Daily layouts + machine rosters — Garden and PTMT (verified from the live June files)

Read live from the June daily Google Sheets. Two distinct layouts; the Pipe-style parser does not match either, which is why Garden / HDPE / Tank / PTMT return nothing. Add a handler per layout.

---
## A. PTMT — Report-5 "Daily Report (Output – Moulding M/C)"
File (June): `1nEDFjrVu6pnNkzZ9tJhvGvBDMUHjLStcc0RP2uHig4g` · folder `1cyRndUCOgirU3PsOgtqAPvJMw7Qx0wR1`.

**Layout (verified):** one row per machine. Fixed left columns then repeating per-day pairs.
- Header row: `M/C NO. | TOTAL RUN HOUR | IDEAL HOUR | %age of Utilisation | TOTAL(Output Kg) | 01-Jun-26 | | 02-Jun-26 | | 03-Jun-26 | …`
- Sub-header: under each date, two columns: `Run Hours | Output in Kg` (the blank header cell above each date's second column is "Output in Kg").
- So per machine row: `[code, total_run_hour, ideal_hour, util%, total_output_kg, (run_hrs_d1, out_kg_d1), (run_hrs_d2, out_kg_d2), …]`.

**Parse:** machine code = column 1. For each date column pair, read `run_hours` and `output_kg` for that day. Daily output = `output_kg`; daily run hours = `run_hours`. The sheet's `IDEAL HOUR` (e.g. 572 per machine, 29744 total) is the **planned-hours baseline already present in the PTMT sheet** — use it as the utilisation denominator for PTMT (don't treat PTMT as "no baseline"). Ignore the stored `%age of Utilisation`; recompute = run_hours / ideal_hour.

**PTMT machine roster (31 machines, exact codes):**
`GRINDER-1 (M)`, `GRINDER-2 (S)`, `GRINDER-3 (B)`, `80-1`, `80-2`, `80-3`, `80-4`, `80-5`, `80-6`, `110-1`, `110-2`, `110-3`, `125-1`, `125-2`, `150-1`, `150-2`, `150-3`, `150-4`, `150-5`, `150-6`, `150-7`, `150-8`, `200-2`, `200-3`, `250-1`, `250-2`, `250-3`, `250-4`, `250-5`, `250-6`, `350-1`.

**Roster fix:** PTMT is a **real plant** — add it and these 31 machines to the master roster. Normalise to `PTMT <code>` (e.g. `PTMT 125-1`, `PTMT GRINDER-1 (M)`). These are tonnage moulding machines of different sizes (80/110/125/150/200/250/350 = clamping tonnage); their outputs legitimately differ a lot, so compare each machine to **its own history**, not the PTMT median — that removes the false "PTMT 125-1 / GRINDER-1 outlier" flags. (There is also a `MACHINE OPERATOR/<code>` block below — operator names/shift letters, not production; skip it.)

---
## B. Garden Pipe — per-machine blocks + per-day matrix
File (June): `1fIpsiS5De9xzyK5We0r9_kdIVrwXC09UIQpe4lWmATA` · folder `1NbzEo0JdWAQSmT3fGhD9DuFZkBvOvnzT`.

**Layout (verified):** two usable shapes in the file —
1. Per-machine item blocks: header `MACHINE NO : 001` (… 002, 003, 004), then rows `DATE | CODE | SIZE | LENGTH(MTR) | NOS(PCS) | TOTAL(MTR) | KG | TOTAL(KG)` with date rows like `Jun 1, 2026`, `Jun 3, 2026`. Daily output = sum the day's `TOTAL(KG)` (and PCS) for that machine.
2. A per-day matrix: row labels `MACHINE-1 … MACHINE-4`, columns repeating per date `Jun, 1 … Jun, 30` each with `Run Hours | Output in KG | Rejection in KG`. Use this for daily **run hours** and **rejection**.

**Parse:** machine = `MACHINE NO : 00n` / `MACHINE-n`. Output/PCS from block (1) by date; run-hours + rejection from matrix (2) by date. Normalise to `GARDEN M/C - n` (strip leading zeros: `001`→`1`).

**HDPE and Tank** use the same family of small layouts (per-machine blocks / per-day matrix). HDPE June `1_vKZGOctS_ADPxDD2OypxasHVQ5MgmHjTWcvWKEDyi8`; Tank June `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo`. Apply the Garden-style handler; if a file opens but yields 0 machine rows, raise a **"parse failed"** flag — never report it as "source missing / no data".

---
## C. Normalisation summary (so daily codes match the roster)
- Pipe file Report-11 → `PIPE M/C - n`; Report-12 → `MOULDING M/C - n`.
- Garden `MACHINE NO : 001` / `MACHINE-1` → `GARDEN M/C - 1`.
- HDPE machine → `HDPE M/C - 1`.
- PTMT Report-5 codes → `PTMT <code>` (keep the size-tonnage code and the `(M)/(S)/(B)` grinder suffixes).
- Strip operator prefixes (`MACHINE OPERATOR/…`, `MR. X/…`), uppercase, collapse spaces, strip leading zeros on the numeric part.

## D. Acceptance
- PTMT shows 31 machines with daily run-hours + output for June; utilisation uses the sheet's IDEAL HOUR (572/machine), not the 500 placeholder.
- Garden/HDPE/Tank June parse to per-machine daily rows (no "source missing").
- A daily file that opens but yields 0 rows is flagged "parse failed", not "no data".
- PTMT added to the master roster; outliers compared to each machine's own history.
