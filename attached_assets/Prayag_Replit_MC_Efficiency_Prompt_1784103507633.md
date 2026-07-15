# Replit Agent Prompt — Add **M/C Efficiency** as a new parameter (Pipe & Fittings)

> Layers on top of the existing Management Reports work. Adds ONE new metric to the machine cards and to the (A)/(B) reports. Verified against the Jun'26 and Jul'26 Pipe & Fitting workbooks.

---

## 1. What the metric is (verified from source, not assumed)

In **Report-5** of the Pipe & Fitting workbook, column **N** is headed **"M/C Efficiency in Hours"** (header in **row 3**). Its live formula is:

```
N = F / M
```

- **F** = `"TOTAL" / "RUN HOURS"` — actual run hours in the month (header row 3 = "TOTAL", row 4 = "RUN HOURS")
- **M** = `"M/C Run Hour in a Month"` — the **ideal** machine run hours for the month

So: **M/C Efficiency = Actual Run Hours ÷ Ideal M/C Run Hours in the Month.**

It is a **time/availability** metric. It is NOT the same as the two you already show:

| Metric | Source | Formula | Meaning |
|---|---|---|---|
| **Out.Eff** (already in UI) | Report-5 col **K** ("%age of Efficiency in KG") | `J / I` = Avg output per hour ÷ Ideal output per hour | throughput **while running** |
| **Util.** (already in UI) | app-side | run hours ÷ hours available | how hard it ran **on days it ran** |
| **M/C Efficiency** (NEW) | Report-5 col **N** ("M/C Efficiency in Hours") | `F / M` = run hours ÷ ideal month hours | how much of the **whole month** the machine was actually producing — **idle days drag it down** |

**This is exactly why it's wanted.** In the current UI, Pipe M/C-2 shows `Util. 100.0%` — but for June its M/C Efficiency is **36.2%** (181 run hours out of a 500-hour ideal month). Util says "when it ran, it ran flat out"; M/C Efficiency says "it was idle most of the month." Pipe M/C-5: `Util. 100.0%` vs M/C Efficiency **12.2%**. Show both — they answer different questions.

---

## 2. Implementation rules (do NOT hardcode)

1. **Parse by header text**, not column index. Find `"M/C Efficiency in Hours"` and `"M/C Run Hour in a Month"` in **row 3** of Report-5; find run hours via the `"TOTAL"` / `"RUN HOURS"` header pair. Layouts drift between months.
2. **The ideal (denominator) is NOT always 500.** In the June workbook:
   - `500` → pipe machines, moulding machines, socket, mixer (43 machines)
   - `300` → **grinders and pulverizers** (5 machines)
   Read **M per row** from the source. Never assume 500.
3. **Recompute; never copy the stored N value or the stored total.** (See the bug in §3.)
4. **Do not cap at 100%.** A machine can exceed its ideal month. Real June values: `Grinder-1 = 150.3%`, `A06(C-150) = 103.4%`, `C07(U-250) = 100.2%`. Render >100% honestly (e.g. a distinct colour), don't clamp.
5. **Zero-output machines are real zeros** (e.g. Pipe M/C-8 ran 0 hours → 0.0%). Do not confuse with "awaiting source".
6. Applies to **all machine blocks** in Report-5: pipe, socket, mixer, grinder, pulverizer, and moulding. Add the column to both **(A) Pipe M/C Summary** and **(B) Moulding M/C Summary**, and as a 5th chip on the machine cards.

### Grain matters — pick the right denominator
Column N is a **monthly** metric (month run hours ÷ ~500 ideal month hours).
- **Monthly card / report row:** `M/C Eff = month run hours ÷ M (per-row ideal, 500 or 300)`.
- **Daily card:** the 500 ideal is monthly — do **not** divide a single day's hours by 500. Use the per-day ideal from Report-5 col **D**, `"Ideal Run Hour Per Day"` (= 22): `daily M/C Eff = day run hours ÷ 22`. Label the grain in the tooltip so the number is never ambiguous.

---

## 3. A real bug in the source — recompute, don't inherit it

The **pipe TOTAL row's** ideal-hours cell is `=SUM(M5:M12)` → **4000**. It **misses row 13 (Pipe M/C-9)**. There are **9 pipe machines**, so the correct ideal is **9 × 500 = 4500**.

Result: the workbook's stored pipe-total M/C Efficiency is **overstated**:

| Month | Run hours | Stored (÷4000) | **Correct (÷4500)** |
|---|--:|--:|--:|
| Jun'26 | 1,009 | 25.2% ❌ | **22.4%** ✅ |
| Jul'26 | 641 | 16.0% ❌ | **14.2%** ✅ |

**This bug is present in BOTH the June and July workbooks — it is systematic, not a one-off.** Compute the total's denominator as the **sum of the per-machine ideals over the machines actually in the roster**, never from the stored total cell.

(Related fragility, no numeric impact today: the row formulas for Pipe M/C-5..9 are anchored to `$M$8` instead of their own row's M. It happens to be harmless because every pipe row's M is 500 — but it means the stored cells would silently break if any machine's ideal ever changed. Another reason to recompute from F and the per-row M.)

---

## 4. Acceptance numbers (self-check — recompute must reproduce)

**June 2026 — pipe machines** (`run hours ÷ 500`):

| Machine | Run hrs | M/C Eff |
|---|--:|--:|
| Pipe M/C-1 | 217 | 43.4% |
| Pipe M/C-2 | 181 | 36.2% |
| Pipe M/C-3 | 112 | 22.4% |
| Pipe M/C-4 | 201 | 40.2% |
| Pipe M/C-5 | 61 | 12.2% |
| Pipe M/C-6 | 161 | 32.2% |
| Pipe M/C-7 | 22 | 4.4% |
| Pipe M/C-8 | 0 | 0.0% |
| Pipe M/C-9 | 54 | 10.8% |
| **TOTAL** | **1,009** | **22.4%** (÷4,500 — *not* the stored 25.2%) |

**July 2026 — pipe machines:** M/C-1 25.6% · M/C-2 19.4% · M/C-3 20.2% · M/C-4 16.8% · M/C-5 17.2% · M/C-6 29.0% · M/C-7 0.0% · M/C-8 0.0% · M/C-9 10.0% · **TOTAL 14.2%** (641 ÷ 4,500 — *not* the stored 16.0%).

**June 2026 — moulding:** TOTAL = 9,771 run hrs ÷ 13,500 ideal = **72.4%** (27 machines × 500). Sample machines: A02(U-150) 99.4% · A06(C-150) 103.4% · C07(U-250) 100.2% · C03(U-250) 0.0% · B07(NU-350) 0.0%.

**June 2026 — grinders/pulverizers (ideal = 300):** Grinder-1 (PIPE) 451 hrs → **150.3%** · Grinder-3 (PIPE) 190 → 63.3% · Pulverizer-2 (PIPE) 365 → 121.7%.

If a computed figure misses its target, treat it as a **build failure**: check (a) you read M per row rather than hardcoding 500, and (b) the total denominator sums the full roster (4,500 for pipe, not the stored 4,000).

---

## 5. UI

- Add **M/C Eff.** as a fifth chip on each machine card, beside `Util.` / `Out.Eff` / `Rej.` / `Att.`
- Tooltip: *"M/C Efficiency = actual run hours ÷ ideal machine run hours for the period. Unlike Util., idle days count against it."*
- Colour: same band logic as the other chips, but allow a distinct treatment for **>100%** (ran beyond the ideal month) rather than clamping.
- Add an **M/C Efficiency %** column to the (A) and (B) report exports, and to the per-report `.xlsx` downloads.
- Show the grain in the label/tooltip (monthly vs daily), since the denominator differs (500/300 monthly vs 22 hrs/day).

---

## 6. Sources used to verify this spec

- Jun'26 Pipe & Fitting — `1uwuhCylN3h9HizK5qNUCH-sjktE3GEH74Y_UeNq6eec` → Report-5
- Jul'26 Pipe & Fitting — `1y2HRoJNQmE2BthE0f18YU1w0ly1LMvyqP98f2_4Wero` → Report-5 (owner `bhawna@`)

Unchanged and still non-negotiable: every number recomputed from the daily source workbooks; final/summary sheets and stored total cells are for format + validation only; parse by header text; AI writes narrative only.
