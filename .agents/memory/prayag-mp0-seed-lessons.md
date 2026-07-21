---
name: MP-0 seed parser & upsert lessons
description: Hard-won bugs in mp_seed.py / mp_model.py for the Plumbing planning data model.
---

## Compound tab (_parse_block_explicit)

The COMPOUND COST - P / -F tabs have a WORKING section followed by an
ACTUAL section with identical block labels but different prices.
Fragile block-finder replaced with `_parse_block_explicit(rows, comp_col,
ratio_col, price_col)` reading explicit column indices from
`_PIPE_EXPLICIT_COLS` / `_FITTING_EXPLICIT_COLS`.

**WASTAGE-before-Total (CPVC-Fitting regression):** Some blocks (e.g.
CPVC-Fitting) have NO "Total" row — the block ends directly at the WASTAGE
row.  Check for "wastage" BEFORE "total" in the scan loop and `break`
immediately, capturing `wastage_factor` from the ratio column.  If "total"
is checked first and "wastage" falls in the post-total branch only, the
parser treats the WASTAGE row as a component, then reads into the ACTUAL
section — giving wrong component count and inflated effective rate.

**Why:** CPVC-Fitting: 1 component CG-122 (50 kg × 175.00) → eff=176.75.
Without the fix: 2 components, ratio=51.01, eff=179.82.

## parse_per_hour — degenerate-header guard

Title rows like `"CPVC FITTING CYCLE TIME"` at col 0 have "cycle time" in
them AND map every column to the same header key → vc=0, cc_fallback=0.
**Guard: if `cc == vc` after fallback, `continue` to next row.**

Track whether `cc_explicit` was found by label or only via fallback (0).
The guard fires only when the fallback 0 == vc, not when cc was labeled.

**Why:** Without the guard, hdr_idx=0 (title row), code_col=value_col=0 →
all rows skipped because raw_val is the same cell as raw_code.

## parse_per_hour — offset item-code column (UPVC PIPE)

UPVC PIPE tab has 6 empty columns before the data block; `'PRODUCTION PER
HOUR'` is in row 0 (UPVC label row) but `'ITEM CODE'` is in row 2 (a
sub-header row).  When `cc` was not found explicitly in the header row
(**`cc_explicit is None`** and `code_col == 0` and `value_col != 0`),
scan ±5 rows around `hdr_idx` for any row containing "item"/"code" in its
header map.  Skip candidates where `cc2 == value_col`.

**Why:** Falling back to col 0 for UPVC reads empty cells (cols 0-5 have
MRP/rate data for a different purpose); the real item codes are at col 6.

## Per-hour & compound item code filtering

Product codes always contain at least one letter.  Use:
```python
if not any(c.isalpha() for c in nc):
    continue
```
This correctly rejects both pure-integer ERP IDs ("33000778") and
decimal OD sizes ("104.8", "11.8") that appear in planning sheets.
`re.match(r'^\d+$', nc)` misses decimal codes.

## Per-hour tab selection — specificity sort

Multiple tabs may match `_match_per_hour_tab` for the same material×type
(e.g. `" CPVC PRODUCTION PLANING"` and `"  CPVC PRODUCTION PLANING PIPE"`
both pass for CPVC/pipe).  Sort matches so tabs whose name explicitly
contains the type keyword ("pipe"/"fitting") come before generic tabs:
```python
matched.sort(key=lambda t: 0 if mat_type in t.lower() else 1)
```

## Pipe vs fitting tab matcher — positive keyword required

A pipe tab matcher that only checks `NOT "fitting"` wrongly matches
`"CPVC TOP ITEM"`.  Require `"pipe" OR "planing"/"planning"` as a
positive keyword for pipe tabs.

## DELETE + INSERT for all mp_ upserts

`ON CONFLICT DO UPDATE` never removes stale rows from prior seeds
(e.g. numeric codes removed by a later filter).  All three table upserts
(`upsert_bom_weights`, `upsert_per_hour`, `upsert_compound_recipe`) must
`DELETE WHERE segment=%s AND effective_month=%s` inside the same
transaction before executing the batch insert.

## Known data gaps (not bugs)

- `' SWR PLANING'` tab has no "PRODUCTION PER HOUR" column → rows=0/empty, correct.
- AGRI pipe, SWR/AGRI fitting: no dedicated planning tab → tab_not_found, correct.
- CPVC/UPVC fitting: tabs exist but no cycle-time column found → empty, acceptable.
- SWR/AGRI pipe compound: no ingredient-level formula on file → needs_recipe=True, correct.
