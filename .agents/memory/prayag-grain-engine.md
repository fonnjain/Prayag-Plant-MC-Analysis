---
name: Prayag daily/monthly grain engine
description: Durable invariants for mixing daily and monthly Records in the metrics engine and reconciling the two grains.
---

# Daily Records come in two shapes — keep them distinct

There are two kinds of daily `Record`, and the metrics engine treats them differently:
- **shift-log** rows carry shift timing (`shift_len_min > 0`) → hours come from the time model and they can feed OEE.
- **daily-matrix** rows (per-date production grids) carry hours/output directly and leave `shift_len_min == 0` → they must be summed like monthly rows.

**Why:** a daily-matrix row with `shift_len_min == 0` run through the shift-log time model yields 0 worked/ideal hours, so utilisation and output-efficiency silently collapse to 0. This is invisible to reconciliation checks because those sum raw fields, not the metric output.

**How to apply:** when adding any new daily ingestion path, pick a shape deliberately — set shift timing OR set hours/output directly, never half of each. After changes, verify a full-month daily rollup's utilisation equals the monthly grid's for the same machines.

# Daily↔monthly reconcile by distributing the monthly ideal

Per-day ideal hours = the machine's monthly ideal hours ÷ its active days that month, so a full-month daily rollup collapses back to the monthly total and the two grains reconcile exactly. Absolute utilisation is genuinely low — machines run a few hundred hours against a flat ~500 hr/month/machine theoretical capacity, so low numbers are not a bug.

# Sub-monthly windows can't borrow monthly totals

Sub-monthly windows (yesterday / 7d / rolling 30d / custom < 27d) never align to calendar months, so monthly totals cannot be sliced into them. Never mix non-sliceable monthly totals into a partial window — read real daily rows for the window instead.

# Daily ingestion covers EVERY plant with a daily file, via a per-plant layout config

Each plant declares its daily tab + layout (`matrix` = wide per-date grid, or `long` = one row per machine per date). One workbook may emit several logical plants (e.g. the PIPE workbook emits both PIPE and a `MOULDING `-prefixed plant from a second tab). Do not hard-skip plants that lack a monthly grid — read them and resolve the ideal denominator by precedence.

**Why:** the original engine only ingested plants with BOTH a daily file and a monthly grid, so sub-monthly windows fell back to a misleading monthly summary ("0/N machines"). The fix reads daily for all configured plants and degrades the *baseline*, not the *data*.

# Ideal-denominator precedence + the "no baseline set" honest fallback

Per-machine ideal hours/output resolve in strict order: **monthly grid → in-sheet ideal column (e.g. PTMT) → baseline master → none**. When none resolves, utilisation/output-efficiency are *suppressed* (not computed as a misleading 0%) and the figure is flagged "No baseline set"; raw hours + output are still shown.

**How to apply:** metrics expose `util_available`/`eff_available`/`headline_available` and `headline` falls OEE→eff→util→"No baseline set". Templates must gate on these flags, **not just `oee_available`** — the OEE-vs-OutputEfficiency binary is wrong for util-only and no-baseline plants. Use `headline_label`-style branching (OEE / Output Efficiency / Utilisation / No baseline) for the headline caption everywhere it appears.

# Detected-sources screen shows the full configured inventory

The `/sources` screen must list every configured workbook (annual grids + daily descriptors) regardless of the selected period — build it from the full inventory, not the period's loaded subset, or daily/annual entries disappear depending on grain.
