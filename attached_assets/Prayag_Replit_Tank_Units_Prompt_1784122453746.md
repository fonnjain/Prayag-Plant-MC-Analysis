# Replit Agent Prompt — Tank reports: handle **Pcs + Litres + KG** as a distinct unit model

> Layers on the existing Management Reports work. Covers the three Tank streams (KH, VN, WB). Verified against live source workbooks on 15-Jul-2026.

---

## 1. Why Tank is different

Every other segment (pipe, moulding, garden, HDPE, PTMT) reports in **kg**. **Tank does not.** A water tank's commercial measure is **litres of capacity**, its count is **pcs**, and **kg** is only the material consumed. The source's own DASHBOARD leads with **"Total Production (Ltr)"** — litres is the headline for tanks.

**Do not roll Tank kg into a cross-segment "total output kg" figure.** VN's July kg (5,961) next to pipe's ~170,000 kg makes tanks look trivial, which misrepresents the business — those 5,961 kg are **222,000 litres** of tank capacity. Tank is reported on its own unit model.

In the **`PROD. REPORT`** worksheet, the three metrics to take are:
- **PRODUCTION IN PCS.** — the base count
- **PRODUCTION IN LTR.** — the headline
- **PRODUCTION IN KG.** — material, secondary

---

## 2. ⚠️ The column letters are NOT stable across streams — parse by HEADER TEXT

The instruction "take columns **J, K, N**" is correct **only for the VN / Tank (PRV) workbook**. It is **wrong for the other two streams**. Verified across all three live files:

| Metric | **KH** | **VN (PRV)** | **WB (PDWB)** |
|---|:--:|:--:|:--:|
| PRODUCTION IN PCS | **I** | **J** | **G** |
| PRODUCTION IN LTR | **J** | **K** | **H** |
| PRODUCTION IN KG | **M** | **N** | **L** |
| REJECTION IN KG | N | O | M |
| REMARK'S | O | P | N |

Read that table carefully — hardcoding `J/K/N` would silently produce **garbage** on two of the three streams:
- **KH:** `J` is *litres* (not pcs), and **`N` is REJECTION IN KG** — you would publish rejection as production.
- **WB:** **`N` is REMARK'S** — a *text* column.

**Rule: resolve every column by matching the header text in row 5.** Normalise before matching (`strip()`, uppercase, drop trailing `.`), because the headers themselves differ: KH reads `"PRODUCTION IN PCS"` while VN/WB read `"PRODUCTION IN PCS."` (trailing period).

### Other per-stream differences (don't assume a uniform schema)
| | KH | VN (PRV) | WB (PDWB) |
|---|---|---|---|
| Weight column | `TANK WEIGHT` | `STANDARD TANK WEIGHT` **+** `OK TANK WEIGHT` | `TANK WEIGHT` |
| Production hours / No. of cycle | yes | yes | **absent** |
| `REJECTION IN LTR.` | no | no | **yes** |

For VN, the kg figure is built from **`OK TANK WEIGHT`**, not `STANDARD TANK WEIGHT`.

---

## 3. Sheet geometry (will break naive parsers)

- Worksheet name: **`PROD. REPORT`**
- **Header row = 5**
- **Data starts row 6**
- **⚠️ The TOTAL row is row 4 — ABOVE the header, not at the bottom.** Any logic that scans for a total at the end of the table will miss it.
- Recompute totals from the data rows; use row 4 only to validate. (It happens to tie exactly in all three files — see §5 — but the principle stands.)

---

## 4. The unit relationships (verified, 298/298 rows, zero mismatches)

```
PRODUCTION IN LTR = PRODUCTION IN PCS × SIZE (LTR.)
PRODUCTION IN KG  = PRODUCTION IN PCS × TANK WEIGHT      (VN: use OK TANK WEIGHT)
```

Confirmed row-by-row: KH 91/91, VN 39/39, WB 168/168 — **0 mismatches**. So **pcs is the base unit** and litres/kg are both derived from it. Use these as validation identities: if a row's stored litres ≠ pcs × size, flag the row rather than silently trusting it.

### Rejection is its own model — and differs by stream
```
Total rejection (kg) = REJECTION MOUTH LID IN KG + REJECTION IN KG
Rejection %          = Total rejection (kg) ÷ PRODUCTION IN KG
```
Verified: VN `129.7 + 0 = 129.7` → `129.7 / 5,960.8 = 2.18%` ✅ (matches its DASHBOARD). WB `471 + 464.5 = 935.5` → `935.5 / 19,251.5 = 4.86%` ✅ (matches its DASHBOARD).

Note the streams reject differently, so don't hardcode one basis:
- **KH:** rejection in **pcs** only (90 pcs); kg rejection = 0.
- **VN:** **mouth-lid kg** only (129.7); rejection pcs = 0.
- **WB:** all four — 20 pcs, 18,500 ltr, 471 mouth-lid kg, 464.5 kg.

**Rejection % is computed on the KG basis**, even though production headlines in litres. Don't compute rejection against litres.

---

## 5. Acceptance numbers (recompute must reproduce; each ties to its stored TOTAL exactly)

| Stream | Month | Pcs | **Litres** | KG | Rejection |
|---|---|--:|--:|--:|--:|
| **KH** | Jun'26 | 1,781 | **1,419,500** | 30,490.5 | 90 pcs |
| **VN (PRV)** | Jul'26 (to 14th) | 336 | **222,000** | 5,960.8 | 129.7 kg → 2.18% |
| **WB (PDWB)** | Jul'26 (to 14th) | 961 | **786,500** | 19,251.5 | 935.5 kg → 4.86% |

VN extras: 96 running hrs, 84 cycles, output/hr 62.09 kg. Breakdown by size (VN Jul): 500 L → 208 pcs/104,000 L · 1000 L → 88 pcs/88,000 L · 750 L → 40 pcs/30,000 L. By colour: WHITE 304 pcs, BLACK 32 pcs.

July files are the **live current month** (modified 15-Jul-2026) — treat these as method checks, not frozen targets; they will grow. KH Jun'26 is closed and is the exact-match test.

---

## 6. Source IDs — this resolves the "VN/WB streams still to discover" open item

| Stream | Folder | Files |
|---|---|---|
| **KH** — `TANK Date Sheet & Monthly Report` | `1IsWgq01xLIkX0UZKnSolIL6lOToFefO_` | May'26 `1Zl8dvEZkQKGAkyWDTgLznC_yISNVznPf3pgUodHttm8` · Jun'26 `1xl-k9i4BteCWtHmVcdjEIUXEiZnWzlTpkJuqPPHFLQo` |
| **VN** — `Tank (PRV)` | `1kI-g46eR-GBEr0-8sUGPV_ZEngFlC_Tt` | Jun'26 `1Vsba-WDcYwSstEZsX37ntm_N05yPn0T5DzSkls9zRUw` · Jul'26 `1lUSTSM_m2yywxGeeE7oemRbBMNsyM37ICv1lKBClGtQ` |
| **WB** — `Tank (PDWB)` | `14Wp1OGomlm6FeOLs0AcCFeIMxQ_zmjLx` | Jul'26 `1-JVeDFTnFfoMjDMhvkOV5BE1rKjjO00chRKtUpO5iqQ` |

Discover by **folder** and `title contains`, not by owner. Naming differs per stream (`TANK Date Sheet...` vs `Tank (PRV)` vs `Tank (PDWB)`), so match loosely on `Tank`.

---

## 7. UI / report requirements

**Machine/segment cards & the three TANK reports — show the multi-unit model explicitly:**
- **Headline: Litres** (e.g. `222,000 L`) — the primary tank measure, formatted with thousands separators.
- **Secondary: Pcs** (e.g. `336 tanks`) — the count.
- **Tertiary: KG** (e.g. `5,961 kg`) — material consumed, clearly labelled as material, not output.
- **Rejection %** on the kg basis, with a tooltip stating it is kg-based.
- Where the stream has them (KH, VN): Running Hrs, No. of Cycle, Output/Hr (kg).

**Never** display a blended "output kg" that sums Tank with pipe/moulding. If a group total is needed, total tanks in **litres** and keep kg in its own column.

**Breakdowns to offer** (all recomputable from `PROD. REPORT`): by **SIZE (capacity band)** — the most useful, since litres is capacity-weighted; by **COLOR**; by **ITEM CODE**; by **DATE**.

**Report exports:** the three tank reports — `TANK (KH) Ltr. Summary`, `TANK (VN) Ltr. Summary`, `TANK (WB) Ltr. Summary` — each as its own `.xlsx`, with columns **Pcs | Litres | KG | Rejection**, litres leading. Units in the header (`Ltr`, `Pcs`, `Kg`); `dd-mm-yyyy` dates.

---

## 8. Unchanged principles

Every number recomputed from the daily `PROD. REPORT` rows; stored TOTAL row and DASHBOARD used **only** for validation; parse by header text (never column index — see §2); flag `awaiting source` rather than emitting zeros for an empty month; AI writes narrative only, never numbers.
