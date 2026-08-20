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
- **GARDEN early-month run hours** — GARDEN OUTPUT comes from its per-machine block tabs,
  but RUN HOURS are joined from a separate "Daily Report" matrix tab (`runhours_tab`). An
  early/in-progress month can have output present but the matrix not yet filled — that is
  idle for run hours, NOT a parse bug: utilisation is correctly suppressed (no fake 0%),
  output still shows. `_emit_blocks` warning distinguishes matrix-tab-missing vs
  parse-failure vs no-hours-yet, so check the warning text before treating it as a defect.

**Why:** The classifier distinguishes parse-failure ("layout not recognised") from
idle ("present but empty") on purpose. Returning 0 rows after a successful tab read is
idle; only an unrecognised layout is a parse failure.
**How to apply:** Before "fixing" a plant that shows no daily rows, read the actual
source tab's raw values. If the tab opens and the data area is blank/zeroed, it is
idle — do nothing. Do not fabricate rows.

# TANK unit-column selection: header presence ≠ data presence

The TANK `PROD. REPORT` logs the same run in three unit columns — PRODUCTION IN PCS./LTR./KG.
— plus a `SIZE (LTR.)` descriptor column. Two traps cost a real outage where TANK silently
vanished from "Last updated" despite June having ~800 pcs of real production:

- **`SIZE (LTR.)` is NOT the litres output column.** Its label contains "LTR", so a loose
  `"LTR" in u` header match grabbed the (blank) size column as `cols["ltr"]`. Match the size
  column FIRST with `"SIZE" in u` (an exact `u == "SIZE"` misses `SIZE (LTR.)`).
- **A unit column can be present-but-empty.** Some workbooks publish the litres header yet log
  only pcs/kg. Primary-unit precedence (litres→pcs→kg) must pick the first column that has
  ACTUAL non-zero data over the date rows, not the first whose header merely exists — otherwise
  every row aggregates to 0 output and is dropped (the "nothing produced" skip), zeroing the plant.

**Why:** picking an all-blank column as primary makes a producing plant look idle, and TANK
then disappears from the per-plant freshest-snapshot view — indistinguishable from a genuine
empty template unless you read the raw cells.
**How to apply:** for any multi-unit per-item log, select the headline unit by data presence,
not header presence; and detect descriptor columns (SIZE, etc.) before the unit columns so a
unit token in their label can't hijack them. Genuinely all-empty (every unit 0) still → `[]`.

# PIPE/MOULDING have NO real planned-hours baseline — show "baseline not set"

The monthly grid's "Ideal Hours" column is a FLAT PLACEHOLDER (500 for every machine),
NOT a real baseline. It must never be a utilisation/efficiency denominator. The grid is
therefore NOT a precedence step (the old `_grid_ideal_for` path was removed). Real
baselines come only from: HDPE in-sheet ideal-output rate, PTMT in-sheet IDEAL HOUR
column, or a real config entry in baselines.json. PIPE/MOULDING have no shift-pattern
data, so they resolve to `ideal_source="none"` ("baseline not set"): raw run hours +
output publish; utilisation/efficiency are suppressed; the flag is an advisory,
non-blocking WARNING (never gates sign-off). baselines.json ships with NO machine
entries — do NOT add estimates (e.g. the old "2 shifts x 12h x 26 days = 624h") or the
500-h placeholder; only real, business-supplied planned hours.
**Why:** Computing a ratio against a 500-h placeholder is a fabricated figure; the user
explicitly disavowed estimates and the placeholder.
**How to apply:** If asked why PIPE shows no utilisation, that is correct/intended. Only
add a baselines.json entry when the team supplies real planned hours.

# build-state #7 (GARDEN+TANK June) can fail transiently

`get_daily_records` raises "Couldn't read any daily workbook" only when EVERY workbook
read fails — typically Google Sheets throttling on a cold read. Re-run build-state
before treating a #7 failure as a real bug; a parse gap would surface as a warning with
rows, not this raise.

# A registered monthly payroll source can temporarily render as unparsed

After a cold restart or a concentrated set of Sheets reads, a Management Report can
show “wages file found but parse returned None” even when the linked KH-1 workbook
and its segment parser are valid. This is a transient Sheets 429 condition, distinct
from `AWAITING` (which means no workbook is registered).

**Why:** the page reads several monthly workbooks together; Sheets may reject that
burst while a single source read succeeds after a cooldown.
**How to apply:** retain the source registration and retry after backoff before
changing data or treating the month as missing. Do not turn a temporary parse warning
into an `AWAITING` state.

# A daily pair can report success without being complete

The daily loader's failed-pair marker covers thrown pair-level reads, not every
zero/partial parser outcome. A workbook/tab can therefore yield fewer records
while the dashboard remains daily-first and displays the lower total as normal.

**Why:** a transiently incomplete matrix response, a missing expected tab that
returns a warning, or a cached empty parse does not necessarily raise the pair
failure used by the UI's source-failure signal.

**How to apply:** when a plant population changes unexpectedly, compare requested
months, configured file IDs, per-month parsed row counts, and source reports
against the dashboard's post-read rows before changing arithmetic. Never treat an
empty failed-pair list as proof that every requested source parsed completely.
