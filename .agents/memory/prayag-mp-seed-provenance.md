---
name: MP seed provenance module
description: mp_seed_provenance.py tracks per-table seed freshness (Drive modifiedTime comparison + age). Tests that mock Drive calls must wipe+restore all rows because build_staleness_warnings queries ALL provenance rows, not just those for a segment.
---

## Rule
`mp_seed_provenance.py` is the single source of truth for seed freshness. Key design:

- **`mp_seed_provenance` table** (PK = `table_name`): stores Drive file IDs, stored modifiedTime at seed time, `seeded_at`, and `row_count`.
- **`record_seed()`** — upsert; called at end of each seed function.
- **`build_staleness_warnings(segment, drive_token)`** — returns warning strings for plan-time display. Two checks: (1) rejection/wastage row count = 0 (silent zero risk); (2) Drive file newer than stored modifiedTime. Returns `[]` on any failure (never blocks plan).
- **`get_status_panel(drive_token)`** — all 7 known tables, freshness dots (green/amber/red/missing). Amber threshold = 14 days old.

**Why:** Before this module, plans silently used 0% rejection and 0% wastage when seed tables were empty, understating material by 8-11%.

**How to apply:**
- When adding a new seeded mp_* table: call `record_seed("new_table", ...)` at the end of the seed function, and add the table to `TABLE_LABELS`.
- **Tests that use Drive mocking** MUST call `_wipe_all_and_save()` before the Drive call and restore in tearDown. `build_staleness_warnings` queries ALL rows with `source_file_ids != ''`, so real provenance rows from a previous seed_all will interfere with mock-controlled tests.
- Rejection/wastage use `source_file_ids=""` (many source workbooks); Drive comparison is skipped for them; only `row_count == 0` triggers a warning.
