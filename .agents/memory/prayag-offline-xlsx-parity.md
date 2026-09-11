---
name: Offline XLSX parser parity
description: Input normalization required when retained XLSX revisions are used to reproduce Google Sheets parser behavior.
---

When an XLSX export is used as an offline stand-in for Google Sheets values, convert blank cells from `None` to `""` and Excel date objects to the date-label strings the Sheets API returns before invoking the production parser.

**Why:** `str(None)` becomes the non-empty label `"None"`. In a wide matrix this can make TOTAL/PART rows with blank machine cells look like real machines, duplicating aggregate values alongside their detail rows. Excel date objects can also prevent date-group detection.

**How to apply:** Normalize the complete two-dimensional cell array first, then require the offline parser's record count and unchanged totals to match the live parser before using it for retained-revision arithmetic.