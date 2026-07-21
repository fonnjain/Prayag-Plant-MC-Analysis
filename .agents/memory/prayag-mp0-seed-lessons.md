---
name: MP-0 seed parser & upsert lessons
description: Hard-won bugs in mp_seed.py / mp_model.py for the Plumbing planning data model.
---

## Compound tab (_parse_compound_tab)

The COMPOUND COST - P / -F tabs have a WORKING section followed by an
ACTUAL section with identical block labels but different prices.
**Use `break`, NOT `continue`, on "total"/"wastage" rows** so the parser
stops at the end of the WORKING block and never bleeds into ACTUAL.
Multiple blocks may share the same label (e.g. three "CPVC PIPE" cols at
cols 7/12/17); pick the version with the most non-zero ratio_kg values.

**Why:** `continue` skips the row but reads on, consuming all 20 slots and
reaching the ACTUAL section — doubles the component list and inflates the
ratio sum (251 instead of 125.65 kg for CPVC pipe).

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
