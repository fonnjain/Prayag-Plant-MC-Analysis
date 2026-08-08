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

## _long_date_day requires recognisable date strings in synthetic test fixtures

**Rule:** When writing offline tests that call ``parse_tank_prod`` (or any daily parser), supply real date strings in the DATE column — e.g. ``"01-Jun-2026"`` — not bare integers like ``1``.

**Why:** ``_long_date_day(1)`` returns ``None``; the parser silently discards every data row and returns ``[]``.  The test then fails with "no records returned" rather than an assertion on the actual field.

**How to apply:** If an offline parse test returns an unexpectedly empty record list, check the DATE column in the synthetic fixture first.


## _tank_model rej_kg must read from r.reject_count, not secondary_counts

**Rule:** In ``reports/generators.py:_tank_model``, accumulate KG rejection as ``r.reject_count``, not ``sc.get("rej_kg", 0.0)``.

**Why:** ``parse_tank_prod`` stores the combined mouth-lid + base KG rejection in ``Record.reject_count`` and never puts it in ``secondary_counts``.  Reading from ``secondary_counts["rej_kg"]`` always returns 0.0, so the generator falls through to the pcs-basis % for ALL streams — including VN/WB which have real KG rejection columns.

**How to apply:** If VN or WB tank reports show 0% rejection (or rejection jumps to pcs-basis incorrectly), recheck that ``_tank_model`` accumulates from ``r.reject_count``.


## Matrix machine-column must be selected the SAME way by both readers (parse_daily_matrix)

**Rule:** A matrix tab's machine-id column is read by TWO independent code paths — `parse_daily_matrix` (the per-date Records) and `parse_matrix_summary_col` (the in-sheet ideal-rate/hours summary). They MUST select the same column. When a layout has an alias column beside the canonical "MACHINE" column (HDPE "Daily Report"), pass the layout's `summary_mc_header` (e.g. `("eq","MACHINE")`) into BOTH; otherwise the generic heuristic ("MACHINE" or "M/C NO") can latch onto the alias in one reader only.

**Why:** if the two readers key off different columns, the `sheet_rate`/`sheet_hours` dicts are keyed on one label set and the Records on another, so the join silently misses — HDPE's in-sheet baseline falls through to grid/none and `ideal_source` is no longer "sheet" (utilisation/efficiency silently wrong, not an error).

**How to apply:** when a matrix plant's `ideal_source` unexpectedly isn't "sheet" despite the sheet publishing its rate, suspect a machine-column mismatch between the two readers; ensure the layout's `summary_mc_header` is threaded into `parse_daily_matrix` (`mc_header_spec`), not just the summary reader.

## Long-parser header band must include the row ABOVE the DATE row
Some months shift the row-label headers (DATE / Moulding Machine) DOWN into the sub-row while measure headers (e.g. "Actual Rejection Weight (in Kgs)") stay one row above (seen Report-12 Jul-2026). parse_daily_long's band now also scans header_idx-1 at lowest precedence.
**Why:** anchoring only on the DATE row silently dropped the rejection column — output parsed fine but rejection read 0, so a broken parse looked like a genuine 0% month.
**How to apply:** any header-anchored parser change must keep the row-above scan; when a plant shows 0 rejection with real output for one month only, suspect a header-row shift before suspecting data.

