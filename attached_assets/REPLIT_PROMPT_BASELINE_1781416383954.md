# Add a per-machine planned-hours baseline (config master) for utilisation/efficiency

The source sheet can't be edited, and its `Ideal Hours` is a flat `500` placeholder for every machine, which makes utilisation read wrong. Add the real **planned-hours baseline as app configuration** and use it as the utilisation/efficiency denominator. **This is config, not a data edit:** actual hours, output and reject are still read from the sheet and are NEVER changed — only the *planning baseline* (the denominator) comes from config.

## 1. Add a baseline master (versioned config)
Create a `machine_baseline` table/config keyed by `plant` + `machine`, with:
- `planned_hours` — real planned/available hours for the period (the utilisation denominator),
- `ideal_output_rate` (optional, units/hour) — for efficiency; `ideal_output = ideal_output_rate × planned_hours`,
- `basis` (text) — how the figure was derived (e.g. "2 shifts × 12h × 26 working days"), required,
- `effective_from` (year-month) and optional per-month overrides (planned hours vary with month length/holidays),
- `set_by`, `set_at`, `source = 'config'`.
Keep it in a place that's easy to edit without code (a config table or a `baselines.json`/`.yaml`), so values can be maintained by the team.

## 2. Two ways to populate (per-machine explicit wins over the formula)
- **Explicit:** a `planned_hours` per machine (and per month if needed).
- **Formula default:** `planned_hours = working_days × shifts_per_day × hours_per_shift − planned_stops`, configurable per plant. Use this when no explicit per-machine value is set.

## 3. Engine uses the baseline as the denominator
- `utilisation = actual_hours / baseline.planned_hours`
- `efficiency  = actual_output / ideal_output`  (ideal_output from config, or `ideal_output_rate × planned_hours`)
- `actual_hours` and `actual_output` remain exactly as read from the sheet.
- If a machine has **no baseline set**, fall back to the sheet's `Ideal Hours` value AND raise a completeness flag: *"planned-hours baseline not set for {plant}/{machine} — using the sheet placeholder; utilisation may be unreliable."*

## 4. Keep both values + provenance (transparency, not substitution)
For every row store: `ideal_hours_sheet` (what the sheet held, e.g. 500), `ideal_hours_used` (the baseline applied), and `ideal_source` (`'config'` or `'sheet'`). Never overwrite the sheet value — record both. The audit must be able to show "sheet said 500, config baseline 624 used".

## 5. Surface it in the Data Confirmation / audit view
Show which machines are using a config baseline vs the sheet placeholder, and **list any machine with no real baseline set**. Defaults must be visible, never silent.

## 6. Guardrail — this must not become fudging
- Baselines reflect the **real plan** (shift pattern), documented in `basis`, and are set **independently of the results**. The app must never auto-pick or adjust a baseline to hit a utilisation target or clear a flag.
- Only the planning denominator (planned hours / ideal output) comes from config. Measured facts (actual hours, output, reject) are always from the sheet, unmodified.

## 7. Interaction with the validity rules
With true baselines, utilisation reads correctly and over-100% becomes rare and meaningful (shown as a warning, per the validity-fix rules). The hard-error rule — `actual_hours > calendar_hours_in_period` — stays as the safety net for genuine impossibilities (e.g. 1527 h in a 744-h month).

## 8. Acceptance criteria
- Setting a real `planned_hours` for MOULDING M/C-4 / M/C-22 makes their utilisation read correctly (no longer ~101% against a 500 placeholder); their warnings clear if utilisation ≤ 100%.
- Sheet `Ideal Hours` (500) is preserved and visible alongside the config baseline; `ideal_source` records which was used.
- A machine with no baseline set falls back to the sheet value and is flagged as "baseline not set".
- Actual hours/output/reject are byte-for-byte the sheet values; nothing in the fact tables is altered to fit a baseline.
- PIPE M/C-4's 1527 h still trips the calendar-max hard error regardless of baseline.
