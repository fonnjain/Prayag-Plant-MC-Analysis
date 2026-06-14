---
name: Prayag idle-vs-parse & transient reads
description: Why some daily plants legitimately return 0 rows, and which build-state failures are transient — read before re-investigating "missing" daily data.
---

# Idle (genuinely empty source) is not a parse bug

Some real source workbooks are blank templates for a given period. A daily reader
opening the right tab and returning 0 rows is the EXPECTED idle state, not a defect:

- **TANK May 2026** — `PROD. REPORT` exists but is an empty template (TOTAL row = 0/0,
  the first data row under the DATE header is blank). `_emit_tank` correctly flags it
  "present but no output recorded yet" via `_has_date_header`.
- **HDPE June 2026** — `Daily Report` matrix is present but early-month with no production
  yet; flagged "present but no production recorded" via `_matrix_has_dates`.

**Why:** The classifier distinguishes parse-failure ("layout not recognised") from
idle ("present but empty") on purpose. Returning 0 rows after a successful tab read is
idle; only an unrecognised layout is a parse failure.
**How to apply:** Before "fixing" a plant that shows no daily rows, read the actual
source tab's raw values. If the tab opens and the data area is blank/zeroed, it is
idle — do nothing. Do not fabricate rows.

# PIPE needs no baselines.json entry

PIPE has a monthly grid, so its utilisation/efficiency denominator comes from the grid
(grid-ideal precedence beats config baseline). `baselines.json` is only for machines a
grid does NOT cover (currently MOULDING M/C-4, M/C-22). HDPE/PTMT self-supply in-sheet;
GARDEN/TANK are output-only.
**Why:** "baselines needed for PIPE+MOULDING only" means those are the only baseline-
ELIGIBLE plants, not that PIPE must have explicit entries. Inventing PIPE planned_hours
would fabricate numbers.
**How to apply:** Only add PIPE baselines if the business supplies real planned-hours.

# build-state #7 (GARDEN+TANK June) can fail transiently

`get_daily_records` raises "Couldn't read any daily workbook" only when EVERY workbook
read fails — typically Google Sheets throttling on a cold read. Re-run build-state
before treating a #7 failure as a real bug; a parse gap would surface as a warning with
rows, not this raise.
