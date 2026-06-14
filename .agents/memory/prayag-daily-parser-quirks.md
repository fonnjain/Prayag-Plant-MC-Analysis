---
name: Prayag daily parser quirks
description: Non-obvious bugs in parse_daily_long and parse_daily_blocks that caused double-counting and GARDEN returning no records.
---

## parse_daily_long — TOTAL-skip must use substring, not exact-set

**Rule:** The machine-label skip in `parse_daily_long` must use `"TOTAL" in u_label` in addition to the exact `_DAILY_SKIP_LABELS` set.

**Why:** The PIPE/MOULDING workbooks (Report-11/Report-12) include month-end summary rows whose machine column reads "GRAND TOTAL", "M/C-1 TOTAL", "NET TOTAL", "TOTAL OUTPUT", etc. These rows carry a valid date (the last day of the month) so they pass the date filter. The old exact-match set only caught literal `"TOTAL"`, causing the plant's whole-month total to be added on top of the detail rows → +31.8% PIPE / +17.2% MOULDING reconcile gap.

**How to apply:** The skip condition in `parse_daily_long`:
```python
if not label or u_label in _DAILY_SKIP_LABELS or "TOTAL" in u_label:
    continue
```

## parse_daily_blocks — column detection must scan both header rows

**Rule:** Scan BOTH `values[header_idx]` (the DATE row) AND `values[header_idx + 1]` (optional sub-header) for the output column. Use `"KG" in h` not `h == "KG"`.

**Why:** GARDEN per-machine tabs put all column names in the same row as DATE with the header "TOTAL(KG)" — exact `h == "KG"` never matched, so `out_c` stayed -1 and the parser returned `[]` (mis-reported as parse failure rather than producing records).

**How to apply:**
```python
for scan_row in (values[header_idx],
                 values[header_idx + 1] if header_idx + 1 < len(values) else []):
    for c, v in enumerate(scan_row):
        h = str(v).strip().upper()
        if out_c < 0 and "KG" in h and not any(x in h for x in ("KG/H", "/KG", "RATE", "PER KG")):
            out_c = c
        ...
    if out_c >= 0:
        break
```

Also: `data_start` must be found dynamically (first row after header band with a valid date) — hardcoding `header_idx + 2` skips the first data row when the tab has only one header row.

## _long_date_day — must handle all-numeric dates

**Rule:** Add a regex for `DD/MM/YYYY`, `DD-MM-YYYY`, and `YYYY-MM-DD` (ISO). If the first segment > 31 it is the year (ISO); otherwise treat as DD/MM.

**Why:** TANK PROD. REPORT and some GARDEN/HDPE tabs write dates numerically. Without this, `_long_date_day` returned None for every row and the parser produced no records.
