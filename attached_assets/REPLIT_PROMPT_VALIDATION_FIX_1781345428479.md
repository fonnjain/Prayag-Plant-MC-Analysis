# Fix the validation rules and sign-off gating (false-positive "impossible" errors)

The confirmation layer is over-flagging legitimate data as hard errors, which is wrongly blocking sign-off. Make the following changes. **Do not change how data is read or how metrics are computed — only the classification, gating and messages.** Never auto-edit or auto-transform a source value.

## 1. Tier 3 (Validity) — classify by physical possibility, not against the planned baseline
The current rule treats `actual_hours > ideal_hours` as impossible. It is not: `ideal_hours` here is a **planned/standard** figure (note every machine shows ideal = 500), not the physical ceiling. A machine can run above its planned baseline (utilisation just over 100%). Replace the single rule with two:

- **HARD ERROR (truly impossible → quarantine the row, exclude from published metrics):**
  - `actual_hours > calendar_hours_in_period` where `calendar_hours_in_period = days_in_period × 24` (e.g. May = 31×24 = 744). → *PIPE M/C-4's 1527 stays an error.*
  - `reject > total`, negative hours/output, or a required numeric cell that is non-numeric.
- **WARNING (possible but worth review → show, do NOT block, do NOT quarantine):**
  - `ideal_hours < actual_hours ≤ calendar_hours_in_period` → message: *"Utilisation over 100% — ran above the planned baseline (ideal {ideal}h). Verify the ideal-hours baseline or the logged hours."* → *MOULDING M/C-4 (507) and M/C-22 (504) become warnings, not errors.*
  - Any computed `utilisation > 100%` or `efficiency > 100%` that is within calendar limits → warning.

Keep utilisation/efficiency **recomputed in code** from raw hours/output; never read the sheet's stored `%` cells (they carry the 305%-type errors). (This is the A2 rule — confirm it's in force.)

## 2. Never auto-correct a flagged value
Do not transform suspicious values (e.g. do NOT convert 1527 → 25.45, and do not "round" the marginal overages away). A quarantined row keeps its raw value + full provenance and is flagged for **source correction by the data owner**. Corrections happen in the Google Sheet; the next pull resolves them. Claude's review may *suggest* a likely cause, but the app must never apply it.

## 3. Row-level quarantine + period sign-off (one bad cell must not block the period)
- A hard-error row is written to `quarantine` and **excluded** from the published metrics; all other rows in the period publish normally.
- **Sign-off readiness verdict** = READY when there are **no un-quarantined hard errors** in the selected, *completed* months. Quarantined rows and all warnings are listed as **notes**, not blockers. So a period can be signed off with the 1527 row held aside and noted, while the clean rows are approved.

## 4. Tier 1 (Completeness) — distinguish in-progress from overdue
Classify each expected month against the current date:
- **In progress** — the month has not ended yet (or its reporting cut-off has not passed). Show as informational/expected (e.g. *"Month {ym} is the current period and still in progress — partial or no data is expected."*). It must **not** count against completeness or block sign-off. → *June 2026 today.*
- **Overdue / missing** — the month has ended and its reporting window has passed but no data is present. Warning that counts toward incompleteness.
Drop the word "overdue" for the current in-progress month.

## 5. Tier 4 (Plausibility) — optional refinement to cut false outliers
Compare a machine's output to **its own recent baseline** (median of its prior months) in addition to the plant median, and only warn when it is an outlier on **both**. This stops structurally high/low machines (e.g. GARDEN M/C-3) from always tripping the plant-median test. Keep these as warnings either way.

## 6. Acceptance criteria
- MOULDING M/C-4 (507) and M/C-22 (504) appear as **warnings**, not errors, and do **not** block sign-off.
- PIPE M/C-4 (1527) remains a **hard error**, is **quarantined and excluded**, and is listed as a note; the rest of May can be signed off.
- June 2026 shows as **in progress** (not "overdue") and does not block sign-off of completed months.
- No source value is ever modified by the app; quarantined rows retain raw value + provenance.
- Utilisation/efficiency are computed from raw; stored `%` cells are ignored.
