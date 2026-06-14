---
name: PTMT authoritative roster
description: Why PTMT has a hardcoded 55-machine roster while other grid-less plants stay dynamic, and where it plugs in.
---

# PTMT authoritative roster (55 machines, 5 process groups)

PTMT has no monthly-grid summary, so unlike PIPE/GARDEN/HDPE/MOULDING its roster
cannot be derived from the annual grid. Its roster is the factory's own machine
register, held in `sources.PTMT_GROUPS` (bare codes) → `PTMT_ROSTER` /
`ptmt_roster_ids()` (emitted ids "PTMT <code>") / `ptmt_group(code)`.

**Why hardcode it (vs. the dynamic "reporting machines = roster" used for
HDPE/TANK):** without a fixed roster, completeness silently redefines "complete"
as "whatever reported", so a machine that never reports can never be flagged. The
authoritative 55 lets completeness surface a never-reporting machine as a gap.

**How it plugs in:**
- `build_masters` (confirm.py) merges `ptmt_roster_ids()` into
  `masters["machines"]["PTMT"]` so tier-1 holds PTMT to all 55.
- Because masters now contains PTMT (which has no monthly grid), `_scope_plants`
  monthly grain must scope from `ANNUAL_SOURCES` plants, NOT
  `masters["machines"].keys()`, or a monthly/FY view shows all 55 as missing.
- `_ptmt_group` (sheets.py) calls `sources.ptmt_group` first; the old string
  heuristic is only a fallback for codes not in the roster.
- Grinding group = finishing (`PTMT_FINISHING_GROUP`); its KG stays out of plant
  output (Record.is_finishing).

**Non-per-machine plants (TANK):** do NOT fabricate gaps. tier-1 only emits
"appears in data but not in master roster" for segments/moulds when that plant
actually HAS a roster for that dimension (`if m_segs` / `if m_moulds`); a
plant-level item log like TANK has none, so it scores plant-level 1/1.
