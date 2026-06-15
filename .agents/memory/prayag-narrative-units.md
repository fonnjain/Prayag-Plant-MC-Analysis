---
name: Plausibility narrative units (Claude guesses if omitted)
description: Why deterministic figure-citing tier messages must embed their unit, and MOULDING's correct unit.
---

# Figure-citing tier messages must carry their unit

The plausibility-tier narrative (narrative.claude_sanity_check) is written by
Claude FROM the deterministic four-tier issue messages — Claude is NOT passed
the per-plant unit and does NOT see raw rows. So if a tier message cites an
output figure WITHOUT a unit (e.g. "output 147 is 0.0× the median"), Claude
invents a plausible unit in its prose — it guessed "pieces" for moulding output.

**Rule:** any deterministic issue message that quotes an output/quantity figure
must embed the unit (read from the Record's `.unit`), so the narrative echoes the
correct unit instead of guessing.

**Why:** users read the Claude narrative as authoritative; a wrong unit
("pieces" vs "kg") misrepresents the value being compared.

**How to apply:** when adding/editing tier messages that cite a magnitude,
append the unit (group/row unit). Don't rely on the narrative prompt to know it.

# MOULDING is kg, not pcs

MOULDING output is the "Wt in Kgs" column (Report-12), so its plant unit in
sources.ANNUAL_SOURCES is `kg`. A stale `pcs` declaration mislabels it
everywhere (records, verify kg/pcs bucketing, tier4 plant_unit, narrative).
