---
name: M/C Efficiency idle-machine denominator
description: Why the TOTAL M/C Efficiency denominator in the (A)/(B) reports must be read from Report-5 directly, not accumulated from production records.
---

# M/C Efficiency TOTAL row denominator

## The rule
The TOTAL row denominator for M/C Efficiency in `gen_pipe` and `gen_moulding` must
be sourced from `sheets.pipe_run5_parsed(ym)` (the full Report-5 machine list),
NOT from `t_mc_eff_den` (the records-based accumulator).

**Why:** Idle machines (0 run hours that month) have no production records and
therefore contribute nothing to `t_mc_eff_den`, but their ideal hours still belong
in the denominator (they were available to run).  For example:
- June 2026 PIPE: M/C-8 idle → records-based den = 8×500 = 4,000 (wrong);
  R5-based den = 9×500 = 4,500 → TOTAL = 1,009÷4,500 = **22.4%** (correct).
- July 2026 PIPE: M/C-7 & M/C-8 idle → den = 9×500 = 4,500 → 691÷4,500 = **15.4%**.

**How to apply:**
```python
r5_ideal = sum(
    info.get("ideal_month_hours", 0.0)
    for lbl, info in sheets.pipe_run5_parsed(ym).items()
    if _MC_RE.search(lbl)           # extruders only (gen_pipe)
    # for moulding: not _MC_RE.search(lbl) and not _AUX_RE.search(lbl)
)
total_mc_eff_den = r5_ideal if r5_ideal > 0 else t_mc_eff_den  # fallback on unavailable
```

`sheets.pipe_run5_parsed(ym)` reads the PIPE workbook's Report-5 via the L1
in-process cache (no extra network call when `_load_daily` already fetched the tab
in the same request).

## Machine filters in Report-5 (PIPE workbook)
- **Extruders** (`gen_pipe`): `_MC_RE.search(lbl)` — labels like "PIPE M/C-1"
- **Moulding** (`gen_moulding`): `not _MC_RE.search(lbl) and not _AUX_RE.search(lbl)`
- **Auxiliaries** (`_AUX_RE`): grinders, pulverizers, sockets, mixers — excluded from both

## Stored TOTAL cell in col M is also wrong
The stored TOTAL row in Report-5 col M is wrong independently (misses M/C-9 in
June 2026: 4,000 instead of 4,500).  Reading and summing per-machine values from R5
fixes BOTH the idle-machine gap and the stored-total error at the same time.
