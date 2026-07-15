---
name: Prayag per-plant production units
description: Output unit is per-plant (read from each sheet), never a single global unit; rollups must never sum/compare across units; plus the L2-cache pickle gotcha when adding a Record field.
---

# Per-plant production units

Each plant's production figure carries its OWN unit, read from that plant's sheet —
never a single global unit. PIPE/GARDEN/HDPE/MOULDING/PTMT = kg; TANK = Ltr (primary),
with pcs/kg kept as display-only secondary measures of the SAME output.

## Tank multi-unit model (all three streams: KH, VN, WB)

All three Tank streams use the same `parse_tank_prod` parser with header-based column
detection. Column letters differ per stream — NEVER hardcode indices. Headers differ too
(`PRODUCTION IN PCS` vs `PRODUCTION IN PCS.` with trailing period — normalise before matching).

**DAILY_SOURCES now covers all three streams:**
- `TANK` (KH) — already had Apr-Jun 2026
- `TANK_VN` — Jun & Jul 2026 wired
- `TANK_WB` — Jul 2026 wired
`_DAILY_LAYOUTS` has matching entries; `gen_tank_vn` / `gen_tank_wb` prefer daily records and fall back to annual summary.

**Rejection model is compound, kg-basis, stream-specific:**
- VN: `REJECTION MOUTH LID IN KG` only (column detected as `rej_mouth_kg`, before plain `rej_kg`)
- WB: `REJECTION MOUTH LID IN KG` + `REJECTION IN KG` — both summed
- KH: `REJECTION IN PCS` only (pcs basis, no kg %)
- `secondary_counts["rej_kg"]` = mouth_lid + base_kg; `secondary_counts["rej_pcs"]` = pcs rej
- Rejection % always = `rej_kg / prod_kg` (even when Ltr is primary output unit)

**Column detection guard (parsers.py):** `MOUTH` check must come BEFORE plain `KG` check
inside the `REJECT` branch — both match `KG in u`, so order matters to avoid misfiling mouth-lid as plain rejection.

**Acceptance (verified against live sheets, 15-Jul-2026):**
- KH Jun'26: 1,419,500 L / 1,781 pcs / 30,490.5 kg / 90 pcs rej
- VN Jul'26: 222,000 L / 336 pcs / 5,960.8 kg / 129.7 kg → 2.18%
- WB Jul'26: 786,500 L / 961 pcs / 19,251.5 kg / 935.5 kg → 4.86%

**Rule:** never sum or compare output/reject across units. A blended rejection % over
kg+Ltr is meaningless.

**How it's enforced:** `MetricsResult` carries `output_by_unit`, `reject_by_unit`,
`rejection_pct_by_unit`, `is_mixed_unit`, and `unit` (sole unit, else "" when mixed).
`total_count`/`reject_count`/`rejection_pct` stay summed only for internal/backward-compat;
the UI must read the per-unit dicts and, when `is_mixed_unit`, render a per-unit breakdown
and suppress the single blended figure. TANK secondary pcs/kg live in
`Record.secondary_counts` (display-only, never summed into a total or a ratio).

**TANK annual report sources:** the 25-26 tabs are literally named "SUMMARY (LTR)" → unit
Ltr; the 26-27 "Sheet1" has a "TOTAL PCS" column so its monthly production is pieces → unit
pcs. Each figure is labeled with its own unit, so the per-FY split is honest, not a bug.

# L2-cache pickle gotcha (adding a Record dataclass field)

`Record` objects are serialized into a Postgres L2 cache (`store.pg_cache_*`, shared across
gunicorn workers and surviving process restarts). A dataclass default only applies at
construction, NOT at unpickle — so an OLD cached Record deserialized after you add a new
field will lack that attribute, and a bare `r.newfield` access raises at runtime (500s).

**Why:** restarting the workflow clears in-memory caches but NOT the Postgres L2 cache.
Production has a SEPARATE L2 store from dev, so a deploy can hit stale pickles even after a
clean dev run.

**How to apply:** when adding a field consumed in `compute_metrics` (or any L2-read path),
(1) clear the L2 cache (`sheets.clear_caches()` clears in-memory + Postgres) and restart,
AND (2) guard the access with `getattr(r, "field", default)` so stale payloads can't crash.
