---
name: Prayag daily parser quirks
description: Durable layout traps in the daily parsers (parse_daily_long / parse_daily_blocks) that silently corrupt totals.
---

## Month-end TOTAL rows masquerade as data (parse_daily_long)

**Rule:** Any machine-label containing "TOTAL" (not just the literal word) must be skipped, not only an exact skip-set.

**Why:** PIPE/MOULDING daily workbooks (Report-11/Report-12) embed month-end summary rows — "GRAND TOTAL", "M/C-1 TOTAL", "NET TOTAL", "TOTAL OUTPUT". They carry the month's last-day date, so they pass the date filter and get added on top of the detail rows (observed +31.8% PIPE / +17.2% MOULDING over-count).

**How to apply:** When auditing a daily reconcile gap that is suspiciously close to "double the real total", suspect a TOTAL-row leaking through the label skip.

## Block tabs have MULTIPLE KG columns — pick cumulative TOTAL (parse_daily_blocks)

**Rule:** GARDEN/HDPE per-machine "block" tabs expose three KG-mentioning columns: a per-metre weight decoy (KG header / MTR sub), raw-material consumption (RP CONSUMPTION), and the true cumulative output (TOTAL … KG). Select the TOTAL output column; exclude per-metre, consumption, and rate columns.

**Why:** Grabbing the first "KG" header binds the per-metre decoy and collapses output to near-zero (HDPE May 1369→~1). Column names may sit in the DATE row OR a separate sub-header row, and the first data row starts dynamically after the header band — a hardcoded offset drops a row or returns [] (mis-read as parse failure).

**How to apply:** If a block-tab plant suddenly reports a tiny or empty output, the column picker has latched onto a decoy KG column or a static header offset.

## Matrix machine-column must be selected the SAME way by both readers (parse_daily_matrix)

**Rule:** A matrix tab's machine-id column is read by TWO independent code paths — `parse_daily_matrix` (the per-date Records) and `parse_matrix_summary_col` (the in-sheet ideal-rate/hours summary). They MUST select the same column. When a layout has an alias column beside the canonical "MACHINE" column (HDPE "Daily Report"), pass the layout's `summary_mc_header` (e.g. `("eq","MACHINE")`) into BOTH; otherwise the generic heuristic ("MACHINE" or "M/C NO") can latch onto the alias in one reader only.

**Why:** if the two readers key off different columns, the `sheet_rate`/`sheet_hours` dicts are keyed on one label set and the Records on another, so the join silently misses — HDPE's in-sheet baseline falls through to grid/none and `ideal_source` is no longer "sheet" (utilisation/efficiency silently wrong, not an error).

**How to apply:** when a matrix plant's `ideal_source` unexpectedly isn't "sheet" despite the sheet publishing its rate, suspect a machine-column mismatch between the two readers; ensure the layout's `summary_mc_header` is threaded into `parse_daily_matrix` (`mc_header_spec`), not just the summary reader.
