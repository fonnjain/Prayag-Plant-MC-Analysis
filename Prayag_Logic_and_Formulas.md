# Prayag Production Analytics — Logic & Formulas Reference

> **Principle:** Every ratio is recomputed deterministically in Python from raw cell values.  
> Stored percentages in Google Sheets are **never trusted**. A figure that cannot be computed is shown **blank / "needs review"** — never zero.

---

## Table of Contents

1. [OEE, Utilisation & Efficiency](#1-oee-utilisation--efficiency)
2. [Ideal-Hours Precedence Chain](#2-ideal-hours-precedence-chain)
3. [PIPE Report-5 ↔ Report-11 Reconciliation](#3-pipe-report-5--report-11-reconciliation)
4. [Labour Cost Calculations](#4-labour-cost-calculations)
5. [Power Cost Calculations](#5-power-cost-calculations)
6. [Costing Analysis — KPI Formulas](#6-costing-analysis--kpi-formulas)
7. [Cost Bridge Waterfall](#7-cost-bridge-waterfall)
8. [Volume Sensitivity Analysis](#8-volume-sensitivity-analysis)
9. [Data Confirmation — Four-Tier Gate](#9-data-confirmation--four-tier-gate)
10. [Reconciliation Badge Logic](#10-reconciliation-badge-logic)
11. [Data Verification Checks](#11-data-verification-checks)
12. [Last Updated / Default Period Resolution](#12-last-updated--default-period-resolution)
13. [Spreadsheet-Change Fingerprinting](#13-spreadsheet-change-fingerprinting)
14. [Machine Planning — Scheduler Logic](#14-machine-planning--scheduler-logic)
15. [Machine Planning — Follow-up Warnings](#15-machine-planning--follow-up-warnings)
16. [Rejection & Wastage Stats](#16-rejection--wastage-stats)
17. [Per-Plant Notes & Special Rules](#17-per-plant-notes--special-rules)
18. [Manager Sign-off & Gate Release](#18-manager-sign-off--gate-release)
19. [Caching & Fingerprint Invariants](#19-caching--fingerprint-invariants)

---

## 1. OEE, Utilisation & Efficiency

> **Source module:** `metrics.py` → `compute_metrics()`

### 1.1 OEE (Daily grain only)

```
Availability  = Run Time / Planned Production Time (PPT)
              = (PPT − Downtime) / PPT

Performance   = Total Output / Ideal Theoretical Output
              where Ideal Theoretical Output = (Run Time / 60) × Ideal Rate (pcs or kg/hr)
              CLAMPED to 1.0 for OEE calculation
              performance_raw is unclamped (used for validity checks)

Quality       = (Total Output − Rejection) / Total Output

OEE           = Availability × Performance × Quality
```

OEE is only computed for **daily grain** records. Monthly records use Utilisation and Output Efficiency instead.

### 1.2 Utilisation

```
Utilisation   = Actual Run Hours / Ideal Run Hours (denominator)
```

- Denominator is resolved via the **Ideal-Hours Precedence Chain** (Section 2).
- Suppressed (shown blank) for `PLANTS_WITHOUT_RUNHOURS` (TANK always; GARDEN on days/months with no logged run hours).
- A separate `util_ideal` denominator gates the util suppression independently of the output denominator.
- Per-day ideal hours are **spread only across days that logged run hours** so a no-run-hour day stays blank while the full-month rollup still reconciles.

### 1.3 Output Efficiency

```
Output Efficiency = Actual Output / Ideal Output
```

- Ideal Output = `Ideal Rate (kg/hr or pcs/hr) × Actual Run Hours`
- For MOULDING: efficiency n/a (no in-sheet ideal-output rate).

### 1.4 Machine (M/C) Efficiency — PIPE only

```
M/C Efficiency = Actual Run Hours / Ideal Month Hours
               where Ideal Month Hours = Report-5 Col M value (e.g. 572 h/machine/month for PTMT)
```

- **Unclamped** — can exceed 100% for overtime/extra shifts.
- Total M/C Efficiency denominator comes directly from `sheets.pipe_run5_parsed`, not from the records accumulator. Idle machines (no records) still count in the denominator.

### 1.5 Rejection %

```
Standard:   Rejection % = Rejection Count / Total Output
Tank (kg):  Rejection % = Rejection (kg) / Total Production (kg)
            (Production unit is Litres; rejection is always kg-basis)
```

**PTMT special rule:** Whole-month rejection is lumped onto the **last day** of the month (no per-date rejection column). The reject > output impossibility check must therefore run at **machine-month aggregate grain**, never per daily row.

### 1.6 Rollup Aggregation

- `rollup_by_plant()` — sums daily → monthly → FY; suppresses utilisation on output-only days.
- `rollup_by_machine()` — same but per machine within a plant.
- Auxiliaries (grinders, pulverizers, sockets, mixers) have no daily tab → synthesised as month-grain finishing records, **excluded from plant headline**.

---

## 2. Ideal-Hours Precedence Chain

> **Source module:** `ideal_hours.py`

Priority order (highest first — first non-None value wins):

| Priority | Source | Description |
|----------|--------|-------------|
| 1 | **User OVERRIDE** | Stored in `ideal_hours_overrides` table (`store.py`). Value `0` is valid — means machine not expected to run. |
| 2 | **Live SHEET — HDPE** | `ideal_output_rate (kg/hr)` column in-sheet. |
| 3 | **Live SHEET — PTMT** | Direct monthly `IDEAL HOUR` column from sheet. |
| 4 | **Report-5 Derived — PIPE** | `Ideal Run Hour Per Day` × calendar days in month. |
| 5 | **baselines.json** | Machine-specific configured baseline hours. |
| 6 | **App Default** | `APP_DEFAULT_IDEAL_HOURS`: GARDEN=500, TANK=500, HDPE=550 h/month. |
| 7 | **None** | No source available — metric suppressed (shown as "No baseline set"). |

**Note:** The monthly summary grid's flat 500 "Ideal Hours" column is a **placeholder, NOT a precedence step**.

**Overrides:** Applied via `dataclasses.replace()` — never mutate L1-cached rows. A plant with no machine identity (TANK) takes a plant-level override (`machine=""`).

---

## 3. PIPE Report-5 ↔ Report-11 Reconciliation

> **Source module:** `pipe_reconcile.py`

```
For each (machine, date):
    Output = MAX(Report-5 output, Report-11 output)
    Rejection = MAX(Report-5 rejection, Report-11 rejection)
```

- Union of both sources — Report-11 logs days that Report-5 omits; Report-5 covers run hours.
- Type split (CPVC/UPVC/SWR/AGRI) is **audit-only** — never reduces the headline.
- Type split uses **pro-rata** allocation from typed rows + untyped pickup (remaining after typed sum).
- Report-5's own TOTAL row sums a **stale machine range** — the sum of real M/C-n rows is authoritative (not the TOTAL row).

---

## 4. Labour Cost Calculations

> **Source module:** `costing_labour.py`

### 4.1 Core Labour Formulas

```
Total Labour Cost    = Paid Wages + Contractor Wages
Per-KG Labour Cost   = Total Labour Cost / Total Production (kg)
Per-Hour Cost (Paid) = Paid Wages / Paid Hours
Per-Hour Cost (Act.) = Total Labour Cost / Actual Hours
Hours Gap            = Ideal Hours − Actual Hours   (positive = underperformance)
Hours Gap %          = Hours Gap / Ideal Hours × 100
```

### 4.2 Fittings Production Authority

**Authoritative source: Report-12** (not the labour sheet).

```
fitting_prod_kg = SUM("Wt in Kgs" column) + SUM("Actual Rejection Weight" column)
                  (gross actual = good output + rejected weight)
```

Reason: The labour sheet's "Fittings Production (KGS)" column in FY2026-27 actually contains **pieces, not kilograms** (mislabelled). FY2025-26 and older have kg but Report-12 is always preferred for accuracy.

### 4.3 J-vs-M Variance Flag

For each month, the system computes:
```
variance_pct = |Weight of Total Production − Wt in Kgs| / Weight of Total Production
```

If `variance_pct > 2%`, a warning is raised naming the divergent rows (item, machine, pcs, both weights). "Wt in Kgs" is hand-keyed; "Weight of Total Production" is formula-driven (pcs × standard weight / 1000).

### 4.4 Machine Labour Allocation (Report-22)

```
Machine Labour Cost = Total Machine Hours × Per-Hour Cost (plant-level)
```

Report-22 (A) splits plant-level manpower between production machines and departments.

### 4.5 Per-FY Tab Name Mapping

| FY | Plumbing Tab Name | Ideal Rates Tab |
|----|-------------------|-----------------|
| 2627 (FY2026-27) | `Plumbing` | `Ideal Labour Cost` |
| 2526 (FY2025-26) | `Plumbing` | `Ideal Labour Cost` |
| 2324 (FY2023-24) | `Plumbing & Garden Pipe` | `Ideal Labour Cost` |
| 2223 (FY2022-23) | `KEHRANI PLANT` | *(tab absent — use defaults)* |

Default ideal rates when tab absent: **Pipe = Rs 2.50/kg**, **Fittings = Rs 6.50/kg**.

---

## 5. Power Cost Calculations

> **Source module:** `costing_power.py`

### 5.1 Core Power Formulas

```
Net kWh Drawn        = Grid kWh − (Solar1 kWh + Solar2 kWh)
Power Cost (7.08)    = (Net kWh × 7.08) + JVVL Demand Charges
Power Cost (11.50)   = (Net kWh × 11.50) + JVVL Demand Charges
Power Intensity      = Net kWh / PVC Production (kg)      ← Plumbing-only denominator
Rs/kg Power (7.08)   = Power Cost (7.08) / PVC Production (kg)
Rs/kg Power (11.50)  = Power Cost (11.50) / PVC Production (kg)
```

### 5.2 Labour from UNIT-2

```
UNIT-2 Labour Rs/kg = (UNIT-2 Paid Wages + UNIT-2 Contractor Wages) / PVC Production (kg)
```

Note: `contractor_wages_u2` comes from **D-tab column 8** (found by header scan), not from the main wages column.

### 5.3 Power Denominators

| Denominator | Used For |
|-------------|----------|
| `pvc_prod_kg` | Plumbing-only power Rs/kg |
| `total_prod_kg_u2` | All-plants combined denominator (HDPE + GARDEN + TANK included) |

**Important:** Combined Rs/kg figures mix denominators — always labelled in the UI.

### 5.4 Ideal vs Actual Power

From the "Ideal Power Cost" tab:
```
Ideal Power Gap = Ideal Power Total − Actual Power Total
Ideal Power Rs/kg = Ideal Power Total / ideal_kg_output
```

---

## 6. Costing Analysis — KPI Formulas

> **Source module:** `costing_analysis.py` → `get_analysis_view()`

### 6.1 Labour KPIs

```
Labour excl. Contractor (Rs/kg) = Paid Wages / Total Production (kg)
Labour incl. Contractor (Rs/kg) = (Paid Wages + Contractor Wages) / Total Production (kg)
Ideal Labour (Rs/kg)            = (Pipe kg × Pipe Ideal Rate) + (Fittings kg × Fitting Ideal Rate)
                                   / Total Production (kg)
Labour Gap (Rs/kg)              = Actual − Ideal
Labour Gap %                    = Gap / Ideal × 100
```

### 6.2 Power KPIs

```
Actual Power (Rs/kg) = Power Cost (at chosen rate) / PVC Production (kg)
Ideal Power (Rs/kg)  = From "Ideal Power Cost" tab
Power Gap (Rs/kg)    = Actual − Ideal
Power Gap %          = Gap / Ideal × 100
```

### 6.3 Combined Cost KPI

```
Combined Actual (Rs/kg) = Labour incl. Contractor + Power (at chosen rate)
Combined Ideal (Rs/kg)  = Ideal Labour + Ideal Power
Combined Gap (Rs/kg)    = Combined Actual − Combined Ideal
Combined Gap %          = Gap / Ideal × 100
```

**Note:** Labour denominator = Plumbing production; Power denominator = Plumbing production (pvc_prod_kg). When all-plants denominator is used, the label clearly states "all-plants".

### 6.4 Validated Acceptance Figures (FY2026-27 Plumbing, May 2026)

| Metric | Value |
|--------|-------|
| Labour excl. Contractor | Rs 6.12/kg |
| Labour incl. Contractor | Rs 6.43/kg |
| Ideal Labour | Rs 3.67/kg |
| Power (7.08 rate) | Rs 8.24/kg |
| Ideal Power | Rs 4.86/kg |
| Combined Actual | Rs 14.67/kg |
| Combined Ideal | Rs 8.53/kg |
| Combined Gap | +72% |
| Hours Gap | 11,028 h (11.8%) |

---

## 7. Cost Bridge Waterfall

> **Source module:** `costing_analysis.py` → `_build_cost_bridge()`

Decomposes the change in Rs/kg between current and prior FY into three factors:

```
Let:
  W₁ = Current total wages
  W₀ = Prior total wages
  V₁ = Current volume (kg)
  V₀ = Prior volume (kg)

Total Δ (Rs/kg) = W₁/V₁ − W₀/V₀

Rate Effect   = (W₁ − W₀) / V₁
Volume Effect = W₀ × (1/V₁ − 1/V₀)
Mix Effect    = (W₁/V₁ − W₀/V₀) − Rate Effect − Volume Effect
              (structural change due to product split change)
Residual      = Total Δ − (Rate + Volume + Mix)
```

A small residual (~2%) is expected due to product mix changes within the period. Test tolerance is 5%.

**Trigger:** Prior FY labour data must be loaded (frozen snapshot) for the cost bridge to compute. If unavailable, an honest "Prior FY not loaded" banner is shown — the bridge is never fabricated.

---

## 8. Volume Sensitivity Analysis

> **Source module:** `costing_analysis.py` → `_build_volume_sensitivity()`

Models "what-if" Rs/kg at +10%, +20%, +30% production volume.

**Assumptions:**
- Labour: **90% Fixed** (salaried workforce), **10% Variable** (overtime/piece-rate)
- Power: **Fixed portion** = JVVL Demand Charges; **Variable portion** = Unit consumption cost

```
For each multiplier M (1.1, 1.2, 1.3):

  Fixed Labour    = Labour Total × 0.90
  Variable Labour = Labour Total × 0.10 × M

  Fixed Power    = JVVL Demand Charges
  Variable Power = (Power Total − JVVL) × M

  New Labour Rs/kg = (Fixed Labour + Variable Labour) / (V × M)
  New Power Rs/kg  = (Fixed Power + Variable Power) / (V × M)
  Combined Rs/kg   = New Labour + New Power
```

---

## 9. Data Confirmation — Four-Tier Gate

> **Source module:** `confirm.py` → `full_confirm()`

Runs on **unfiltered** period rows (filtering never makes data look incomplete).

### Tier Classification

| Tier | Name | Severity | Gate Behaviour |
|------|------|----------|---------------|
| 1 | Validity | **Error** | Gates figure — shown as "needs review" |
| 2 | Internal Reconciliation Mismatch | **Error** | Gates figure |
| 3 | Completeness | **Warning** | Shows with amber flag; does not gate |
| 4 | Plausibility (outlier) | **Warning** | Shows with amber flag; does not gate |

### Tier 1 — Validity Checks (Error)

```
Downtime > Shift Length
Rejection > Output  (checked at machine-month aggregate for PTMT, not per daily row)
Actual Hours > Planned Hours × 1.5   (extreme overtime)
```

### Tier 2 — Internal Reconciliation (Error)

```
|Σ(Machine Rows) − Sheet Total Row| / Sheet Total > 3%
```

Flags when individual machine rows don't sum to the sheet's own TOTAL row.

### Tier 3 — Completeness (Warning)

```
Completeness Score = (Files Found / Expected Files)
                   + (Machines Present / Roster Size)
                   + (Months Populated / Months Due)

Score < threshold → Completeness warning
```

The **full-FY grid** is the master roster. A cell that is blank in a later month is a **gap**, not a zero — the master roster defines what is expected.

### Tier 4 — Plausibility (Warning)

```
Machine Output > 6× Plant Median    → outlier high
Machine Output < Plant Median / 6   → outlier low
```

**Sign-off behaviour:**
- Manager sign-off releases the **Error gate only**.
- Sign-off binds to the **data fingerprint** — any sheet change re-gates automatically.
- Warning items can be individually acknowledged without a full sign-off (`confirmation_issue_acks`).
- No authentication — approver name is attestation only.

---

## 10. Reconciliation Badge Logic

> **Source module:** `recon.py`

Compares daily-first aggregated totals against the monthly summary grid.

```
Delta = Daily-First Total − Summary Grid Total

Standard Tolerance (TOL) = 3%  (0.03)
```

### Status Classification

| Status | Condition | Meaning |
|--------|-----------|---------|
| `ok` | `|Delta| / Grid ≤ 3%` or `expect_exceeds` rule met | Within tolerance |
| `info` | No summary grid wired | "Recomputed only" — no cross-check possible |
| `warn` | Cell-level mismatch: daily-first < grid by > 3% | Daily underreports vs grid (unexpected) |
| `fail` | Total delta: daily-first > 3% below grid | Daily significantly trails grid |

### Expect-Exceeds Rule (all plants)

The monthly summary grid **undercounts** for all plants (it sums a subset of machines or omits some dates). Therefore:

```
If daily-first Total > Grid Total → this is EXPECTED → status = "info" (positive delta is normal)
Only flag if daily-first FALLS SHORT of the grid → status = "warn" or "fail"
```

Flagged cells (where daily < grid per cell) drive status before the expected-undercount info branch.

---

## 11. Data Verification Checks

> **Source module:** `verify.py`

Three deterministic checks, all at **0.5% tolerance**:

| Check | Formula | Notes |
|-------|---------|-------|
| Production sum | `|Σ daily records − monthly total| / monthly total` | Cross-verifies daily vs monthly aggregation |
| Rejection sum | `|Σ daily rejections − monthly rejection| / monthly rejection` | Same cross-check for rejections |
| OEE consistency | `|Computed OEE − Stored OEE| / Stored OEE` | Detects if stored % was modified |

All checks are **read-only** — verify never corrects a figure. Results are written as append-only log to `verification_resolutions` table.

---

## 12. Last Updated / Default Period Resolution

> **Source:** `app.py` — default period logic

The default period ("Last Updated") resolves **per plant** to each plant's own most-recent day with real production data:

```
For each plant:
    Find max(date) where:
        total_count > 0  OR  actual_hours > 0
        AND NOT a future/in-progress day (date ≤ today)
        AND EXCLUDE reject-only days (daily matrices lump month rejection on last day)
```

**Why per-plant:** Different plants update at different cadences. A dormant plant is simply not shown — never fabricated. An active plant always lands on real figures with its own real date.

**PTMT exclusion:** Month-end rejection lump is excluded from the "has data" check so the last-day reject entry doesn't make an otherwise-empty day appear active.

---

## 13. Spreadsheet-Change Fingerprinting

> **Source module:** `freshness.py` + `store.py`

```
fingerprint = SHA256(
    sorted(
        normalise_row(row) for row in all_cell_rows
    )
)

normalise_row(row):
    for each cell:
        try: value = int(float(cell))   # "1,234.0" → 1234
        except: value = str(cell).strip()
    return canonical_string(value)
```

**Invariants:**
- Must be **cross-process deterministic** — same data, same fingerprint, regardless of which worker computes it.
- Normalise int/float to avoid `"1234"` ≠ `"1234.0"` false-change detection.
- Sort rows before hashing — row order in API responses is not guaranteed.
- After any formula change: `TRUNCATE source_fingerprints` to re-baseline (otherwise every page load false-flags a change).

**Sign-off binding:** When a manager signs off, the current fingerprint is stored. On each subsequent page load, the live fingerprint is compared to the stored one. If they differ, the sign-off is **automatically revoked** and the figure re-gates.

---

## 14. Machine Planning — Scheduler Logic

> **Source module:** `mp_engine.py` — LPT (Longest Processing Time) scheduler

### 14.1 Scheduling Algorithm

```
Input:  Demand (pcs per item code), Machine Roster, Routing, Per-Hour Rates
Output: Schedule blocks (machine × week × day × shift × item × pcs)
```

1. **Demand conversion:** `demand_pcs → demand_kg` using `mp_bom_weight.weight_per_pc_kg`
2. **Rate resolution:** Per-item, per-machine rate from `mp_per_hour`. Fallback chain:
   ```
   Exact match (item_code, machine) 
   → SWR/AGRI material average 
   → Overall plant average
   rate_estimated = True if fallback used
   ```
3. **LPT dispatch:** Items sorted by processing time (longest first). Assigned to machines greedily by available capacity.
4. **Parallel-split:** Large items split across multiple machines if a single machine cannot complete them within the planning period.
5. **Excess flag:** `is_excess = True` when scheduled beyond demand (capacity fill).
6. **Idle flag:** `is_idle = True` for machine blocks with no demand.

### 14.2 Capacity Calculation

```
Monthly Capacity (h) = shifts_per_day × hours_per_shift × working_days_month
                      (stored in mp_machine; default: 2 × 10 × 25 = 500 h)
```

Downtime deduction:
```
Effective Capacity = Monthly Capacity − Σ(open downtime days × hours_per_shift × shifts_per_day)
```

### 14.3 Week Structure

Configurable via `mp_params.week_days` (JSON array, 4 elements for a 4-week month):
```
Default: [6, 6, 6, 7]  (6 days × 3 weeks + 7 days in week 4 = 25 working days)
```

### 14.4 Material Rates

Configurable per material type in `mp_params`:
```
cpvc_mat_rate, upvc_mat_rate, swr_mat_rate, agri_mat_rate  (Rs/kg)
```

### 14.5 RAG Thresholds

```
Green  → actual within rag_amber_pct% of plan
Amber  → actual within rag_red_pct% of plan
Red    → actual exceeds rag_red_pct% deviation from plan
```

Default: `rag_amber_pct = 10%`, `rag_red_pct = 25%`.

---

## 15. Machine Planning — Follow-up Warnings

> **Source module:** `mp_followup.py`

Actuals are ingested from Report-11 (PIPE fittings) and Report-12 (MOULDING). Join key: `norm_machine() × norm_item()`.

**Plan-to-date** = slice of planned blocks elapsed up to today.

### 9 Warning Types (severity-sorted)

| Priority | Type | Trigger Condition |
|----------|------|-------------------|
| 1 | `OVERDUE_START` | Plan start has passed; no actual production recorded |
| 2 | `COMPLETION_RISK` | At current pace, item will not complete by plan end date |
| 3 | `EXCESS_PRODUCTION` | Actual > Plan by > `rag_red_pct%` |
| 4 | `ZERO_ACTUAL` | Plan assigned but zero actual for period |
| 5 | `RATE_DEVIATION` | Actual hours/kg deviates from planned rate by > `hours_dev_pct%` |
| 6 | `MACHINE_IDLE` | Machine has capacity but no actuals logged |
| 7 | `UNPLANNED_ITEM` | Item in actuals has no plan line (surprise production) |
| 8 | `SHORTFALL` | Actual < Plan-to-date by > `rag_amber_pct%` |
| 9 | `ON_TRACK` | Actual ≥ Plan-to-date within tolerance (informational) |

---

## 16. Rejection & Wastage Stats

> **Source module:** `mp_rejection.py` + `mp_wastage.py`

### 16.1 Rejection Rate

```
Rejection Rate = Σ rejection_kg / Σ prod_kg
               (aggregated per material type and item code over n_months)
```

Stored in `mp_rejection_summary` (by material) and `mp_rejection_item` (by item code).

**Rej basis:** Configurable `rej_basis_formula` = `'gross'` (production includes rejection) or `'net'` (production excludes rejection). Default: `'gross'`.

### 16.2 Wastage

```
Wastage Rate = Σ wastage_kg / Σ prod_kg
             (by material type_key: e.g. CPVC, UPVC, SWR, AGRI)
```

Stored in `mp_wastage_summary`. Recomputed from historical daily data by `mp_wastage.recompute_wastage()`.

---

## 17. Per-Plant Notes & Special Rules

### 17.1 PIPE

- Output & rejection = date-wise **MAX** of Report-5 and Report-11 (union).
- Report-5 TOTAL row sums a stale machine range — **sum the real M/C-n rows directly**.
- Auxiliaries (grinders, sockets, pulverizers, mixers): month-grain, no daily tab, excluded from headline.
- Ideal hours derived from Report-5: `Ideal Hours Per Day × calendar days in month`.
- No `baselines.json` entry needed (grid supplies ideal).

### 17.2 MOULDING

- Output unit: **kg** (Report-12 "Wt in Kgs" column — gross actual).
- Efficiency n/a — no in-sheet ideal-output rate.
- Fittings production: always from Report-12, never the labour sheet.

### 17.3 PTMT

- Roster: **55-machine register** (`sources.PTMT_GROUPS`) — no monthly grid.
- Machine segments contain process-group labels (`PTMT – Injection …`); filters match by **prefix**, never flat `PTMT`.
- Utilisation: Report-5 Col E, flat **572 h/machine/month**.
- Rejection: Whole-month lump on last day → validity check at **machine-month aggregate** grain.
- PTMT queue in MP is a **family-join** (PSF codes ≠ product codes, zero overlap).

### 17.4 HDPE

- Publishes its own ideal output (kg/hr) + run hours → utilisation and efficiency both computable.
- No monthly summary grid cross-check.
- App default ideal hours: **550 h/month**.

### 17.5 GARDEN PIPE

- Output from per-machine block tabs.
- Run hours joined from "Daily Report" matrix.
- Utilisation against app-default **500 h/month**.
- Suppressed when no run hours logged for a day or month.

### 17.6 TANK

- Logged **per item** — no machine identity.
- Output unit: **Ltr** (primary) → **pcs** → **kg** (chosen by data presence).
- Utilisation: always suppressed (`PLANTS_WITHOUT_RUNHOURS`).
- Rejection: always **kg-basis** regardless of headline unit.
- Plant-level override (`machine=""`) because there is no per-machine identity.
- Headline unit chosen by data presence: Ltr → pcs → kg.

---

## 18. Manager Sign-off & Gate Release

> **Source module:** `store.py` + `confirm.py`

### Sign-off Flow

```
1. Confirmation runs → identifies errors → figures gated
2. Manager visits /confirmation → sees error list
3. Manager types name + note → POST /confirmation/approve
4. System stores: {period_key, fingerprint, approver, status_at, ...}
5. On next page load:
   live_fingerprint = freshness.compute(current_data)
   stored_fingerprint = store.latest_signoff(period_key).fingerprint
   if live_fingerprint != stored_fingerprint:
       → re-gate (sheet has changed)
   else:
       → release figure
```

### Issue Acknowledgement (separate from sign-off)

- **Issue acks** use a **stable key** (plant+machine+type) — NO fingerprint in key.
- Acks survive fingerprint drift — they are "I know about this issue" markers.
- Full sign-off is still required to release error-gated figures.

### Stale Rollup Acks (compound alerts)

- Stale rollup acks include the **fingerprint** in their store key.
- They **re-surface** when the fingerprint drifts (opposite of issue acks).
- Logic: "I acknowledged this alert at version X; if the data changed, re-alert me."

---

## 19. Caching & Fingerprint Invariants

### L1 Cache Rules

- All cache reads go through **single-flight locks** (`threading.Lock` per key) to prevent thundering herd.
- `force-refresh` must **evict under the lock** — never use bare `dict.pop()` outside the lock.
- Cache keys must be deterministic: `f"{file_id}:{tab_name}"`.

### L2 Postgres Cache Rules

- `clear_caches()` must call both `_data_cache.clear()` AND `pg_cache_clear()` — they are not automatically linked.
- Pickle format — changing the Python class of cached objects requires explicit cache eviction.
- `cache_key` must be **cross-process deterministic** — same key in different gunicorn workers for the same data.

### Fingerprint Formula Change Protocol

```
IF you change the fingerprint normalisation formula:
    1. TRUNCATE source_fingerprints;   -- re-baseline all files
    2. All prior sign-offs automatically re-gate on next load (fingerprint mismatch)
    3. Users must re-sign-off on all periods
```

This is intentional: the fingerprint is a **version** of the data, and the formula is part of that version.

---

*Last updated: 2026-07-25. All formulas implemented in `artifacts/prayag/`. Run `python3 -m pytest` to verify acceptance figures.*
