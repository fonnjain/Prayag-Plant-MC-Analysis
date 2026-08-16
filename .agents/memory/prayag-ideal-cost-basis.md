---
name: Ideal Cost production basis
description: Which kg quantity to use for Ideal Power/Labour Cost sections in mgmt reports
---

## Rule
Ideal Power Cost and Ideal Labour Cost always use **gross** output = `total_count + reject_count`.  
PRAYAG_RULES: *"Production in KG has Rejection included."* The workbook's Ideal Power Cost col E (APR 90,038.43 Fittings) = gross, not net (89,152).

## How `_accumulate_seg_gross_reject` stores it
- `data[seg][ym]["net"]`    = `total_count`   (net output only)
- `data[seg][ym]["reject"]` = `reject_count`

In `_build_ideal_cost_section`, use **both**:
```python
net = gr["net"] + gr["reject"]   # gross
```
Not just `gr["net"]`.

**Why:** The workbook's Ideal Cost tab multiplies gross production by the per-kg rate.  
Using net undershoots by ~1% (the rejection fraction).  
For Tank, `gr["reject"]` is always 0.0 (Tank reject not accumulated from records), so gross = net there — no harm.

## The R-43 confusion
- Workbook **REJECTION & PRODUCTION** col F = **piece count** (APR 13,40,117 pcs) — wrong basis, inflates ~15×.
- Workbook **Ideal Power/Labour Cost** col E = **gross kg** (APR 90,038.43) — correct basis.
These are two different columns. R-43 is about col F, not col E.
