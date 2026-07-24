---
name: Costing labour auto-sources
description: Design decisions for costing_employee.py and costing_wages.py — parsing Employee Data D-tabs and monthly KH-1 wages files.
---

## The rule

Plumbing labour hours and wages are sourced directly from two dedicated files:
- **Hours/headcount**: `EMPLOYEE DATA DETAILS (COST)` tabs D-1/D-2/D-3.
  Plumbing = PIPE & FITTING TOTAL − GARDEN PIPE − HDPE PIPE − ADMIN.
- **Wages**: Monthly KH-1 wages files, DEPARTMENT == "CPVC" filter, TOTAL PAYABLE column.

The segment labour-cost sheet's Plumbing tab is kept as a cross-check reference (for pipe_prod_kg and reconciliation), but its hours/wages are overwritten by the auto sources.

## Critical implementation gotchas

### 1. Block finder: MUST fall-through on the first row

The PIPE & FITTING block's **first row** contains both the segment label (col B = "PIPE & FITTING") and a sub-department (col C = "ADMIN"). The original `if not in_block: ... continue` structure skipped this row because `continue` fired even after setting `in_block = True`. Correct structure:

```python
if not in_block:
    if "PIPE" in seg_cell and "FITTING" in seg_cell:
        in_block    = True
        block_start = ri
        # Fall through — do NOT continue; process this first row (ADMIN)
    else:
        continue
```

Without this, ADMIN is never collected into `dept_rows`, and Plumbing = TOTAL instead of TOTAL − GARDEN − HDPE − ADMIN.

### 2. Month columns run in REVERSE (latest first)

Col D = MAR'26 ... col O = APR'25 for FY2025-26. The parser extracts month labels from "MAR'26" → "MAR" using a 3-letter prefix regex and builds a `{col_idx: month_label}` dict. Never access months by column position — always look up by the header-derived label.

### 3. `_parse_month_col_map` threshold

Require only `>= 1` validated month abbreviation in the header row (not 4). The 3-letter month regex already prevents false positives. Using ≥ 4 breaks 3-month unit test fixtures.

### 4. TOTAL PAYABLE column MUST be located by header text

The TOTAL PAYABLE column shifts between monthly files (AM in Apr-2025, AN in Mar-2026, AO in KH-2). Any hardcoded column index silently picks up the adjacent BANK ACCOUNT NUMBER column, which is numeric and produces a large but wrong total. The parser asserts "TOTAL PAYABLE" is in the located column's header label before summing.

### 5. Reconciliation cross-check

After loading auto sources, compare auto vs segment sheet for each month:
- `|auto_paid_hours − seg_paid_hours| / seg_paid_hours > 0.5%` → RECON WARNING
- `|auto_wages − seg_wages| / seg_wages > 0.5%` → RECON WARNING

Both figures are named in the warning message. Neither is suppressed — the caller sees both.

## Source file IDs

- Employee Data FY2526: `1b34kCxmbwIWQJdZNL4-I4wuWGU5EzG0NJTg_V7QfYEs`
- Employee Data FY2627: `1Mfjo-CaxboN52hUO_IzrKqEAFxHgegJI4BHQb6H4VYM`
- Wages FY2526 Apr-2025: `1jgp3ftEr1xlEk8kXZp1Wo_ojrd2GrhGF7OZ5kNZO9z8` → 1,904,701
- Wages FY2526 Mar-2026: `1hl0FMeR6IxvXZonXVUpSVEtWJuqy7lZ9E3K8r4ZiOzE` → 1,529,429
- 10 of 12 FY2526 wages months still to be registered in `costing_model.WAGES_SOURCES`

## Acceptance figures (FY2025-26)

D-1 paid hours: **431,468** | D-2 actual hours: **362,225** | D-3 headcount: **1,438**
Full-year wages: **21,452,790**

**Why:** These must match the segment sheet Plumbing tab TOTAL row to within 0.5%. Any divergence fires a RECON WARNING in `load_labour_fy` but does not suppress either figure.
