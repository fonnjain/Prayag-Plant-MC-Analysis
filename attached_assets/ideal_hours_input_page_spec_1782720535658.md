# Build Spec: Ideal Hours Input page

**Goal:** A `/input` ("Ideal hours input") page where a user views and overrides the
**monthly ideal run hours per machine**. Defaults come live from the Google Sheets (or an
app-logic default); the user can override per machine per month. Overrides are stored in the
app and drive utilisation/efficiency — they are **never written back to the source sheet**.

This formalises the baseline layer the app has been missing (PTMT, PIPE both have real
sheet baselines — see prior findings).

---

## 1. The resolution order (the core of the page)

For each machine × month, the **effective ideal hours** resolves in this precedence:

1. **User override** (stored in app) — if set, it wins.
2. **Google Sheet value** — read live:
   - PTMT: Report-5 **Col E (IDEAL HOUR)** — direct monthly figure (currently 572).
   - PIPE: Report-5 **Col D (Ideal Run Hour Per Day, =22)** × days → derived monthly.
     (Decide days basis once: calendar days, planned working days, or Col E run days —
     default to calendar days; make it a config constant, not a guess.)
   - Other plants: their summary tab's ideal-hours column if it exists (audit pending).
3. **App-logic default** — an explicit per-plant default in config, if one is set.
4. **Not set** — no baseline → utilisation/efficiency stay suppressed for that machine.

The page must show, per machine: the sheet value, the override field, the resulting
**effective** value, and a **source badge** (from sheet / derived / override / app default /
not set) so provenance is always visible.

---

## 2. Page layout

- **Header (navy):** "Ideal hours input", month selector, plant filter.
- **Notice line:** "Defaults are read live from the source Google Sheets. Overrides are stored
  in the app only — never written back to the sheet — and take precedence when figures are
  computed."
- **Table:** Machine | From sheet | Override (editable number) | Effective | Source badge.
  Blank override = use sheet default. A small per-row note can explain derivation
  (e.g. "22 hr/day × 26 days") or override reason.
- **Footer:** validation hint + "Save overrides" button. Optional per-row reset-to-default.

---

## 3. Storage & semantics

- Store overrides keyed by **(plant, machine, month/FY)** in the app DB (or a
  `overrides.json` / table) — NOT in `baselines.json` of sheet-derived values, and NOT in
  Google Sheets. drive.file scope is read-only for this; never write to the workbook.
- An override applies to the month it was set for. Decide and document whether it carries
  forward to future months (recommend: no — overrides are month-specific; default re-reads
  the sheet each month so a sheet change isn't masked by a stale override).
- Keep an audit field: who set it + timestamp.
- **Revert:** clearing the override field returns the machine to the sheet/default value.

---

## 4. Validation

- Cap at **24 × days-in-month** (a machine can't ideally run more than 24 h/day). Warn (don't
  silently clamp) if an entry exceeds it.
- Non-negative integers (or 1-decimal) only.
- If override = 0, treat as "machine not expected to run" (utilisation suppressed, not 0%) —
  consistent with the idle-machine blank-guard elsewhere.

---

## 5. How it feeds the engine

- The metrics layer's ideal-hours lookup changes from "sheet value (or empty)" to
  "**resolve(plant, machine, month)**" using the precedence in §1.
- utilisation = actual run hours / effective ideal hours (suppressed if effective = not set).
- This does NOT change the "recompute ratios from raw cells" rule — it only changes where the
  **denominator** comes from. Stored % cells in the sheet are still ignored.
- Setting/overriding a baseline here is what flips a plant from "No baseline set" to a real
  utilisation figure on the dashboard.

---

## 6. Replit prompt (paste-ready)

```
Add a new /input ("Ideal hours input") page for viewing and overriding the
MONTHLY ideal run hours per machine. Overrides are stored in the app and are
NEVER written back to the Google Sheets.

Effective ideal hours resolves per machine x month in this precedence:
  1. user override (stored in app) — wins if set
  2. Google Sheet value, read live:
       PTMT  = Report-5 Col E (IDEAL HOUR), direct monthly (e.g. 572)
       PIPE  = Report-5 Col D (Ideal Run Hour Per Day, 22) x days-in-month (derived)
       others = their summary-tab ideal-hours column if present
  3. app-logic default (per-plant config), if set
  4. not set -> utilisation/efficiency suppressed for that machine

Page:
- Navy header: "Ideal hours input", month selector, plant filter.
- Notice: defaults read live from sheets; overrides stored in app only, never
  written back, and take precedence in calculations.
- Table: Machine | From sheet | Override (editable number) | Effective |
  Source badge (from sheet / derived / override / app default / not set).
  Blank override = use sheet default.
- "Save overrides" button; clearing a field reverts to sheet/default.

Storage: overrides keyed by (plant, machine, month), stored in app DB/table
(not baselines.json, not the sheet). Keep who+timestamp. Overrides are
month-specific (do not auto-carry to future months; default re-reads the sheet
each month).

Validation: cap at 24 x days-in-month (warn if exceeded, don't silently clamp);
non-negative; override 0 = "not expected to run" (suppress, not 0%).

Engine wiring: change the ideal-hours lookup to resolve(plant, machine, month)
using the precedence above. utilisation = run hours / effective ideal hours.
Keep recomputing all ratios from raw cells (still ignore stored % cells); this
only changes where the denominator comes from. A set baseline here is what flips
a plant from "No baseline set" to a real figure.

Brand: navy #1F3864 + terracotta #C55A11, DD-MM-YYYY, sentence case, no emoji,
mobile-first. Link /input in the main nav.
```

---

## 7. Notes / open decisions

- **Days basis for PIPE derivation** (calendar vs planned-working vs run days) materially
  changes utilisation — pick one in config and state it on the page (the mockup assumes
  calendar days). Best resolved with the plant.
- Could extend the same page later to override **ideal output per hour** (PIPE Col I) for
  efficiency, not just ideal hours — same override mechanism, second column set. Out of scope
  for v1 unless wanted.
- Pairs naturally with the Data Health page: a machine showing "no baseline" there links here
  to set one.
