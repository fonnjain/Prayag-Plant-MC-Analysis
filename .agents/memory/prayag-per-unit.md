---
name: Prayag per-plant production units
description: Output unit is per-plant (read from each sheet), never a single global unit; rollups must never sum/compare across units; plus the L2-cache pickle gotcha when adding a Record field.
---

# Per-plant production units

Each plant's production figure carries its OWN unit, read from that plant's sheet —
never a single global unit. PIPE/GARDEN/HDPE/MOULDING/PTMT = kg; TANK = Ltr (primary),
with pcs/kg kept as display-only secondary measures of the SAME output.

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
