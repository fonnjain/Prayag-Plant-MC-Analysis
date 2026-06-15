---
name: Prayag spreadsheet-change tracking
description: How the "last updated / what changed" panel detects sheet edits via content fingerprints, and the determinism + baseline rules that keep it honest.
---

# Spreadsheet-change tracking (dashboard-detected "last updated")

The connected Google account has only the `drive.file` scope, so Drive's
`modifiedTime` returns 404 for every workbook the app did not create —
**Google's true edit time is unreachable**. So "last updated / what changed" is
derived from a **content fingerprint**: hash the parsed values read from each
source workbook and persist (Postgres) one row per distinct version seen. Newest
snapshot's timestamp = "when the dashboard last detected this version".

Pure/network-free assembly reading already-cached records + the durable store;
surfaced identically on `/confirmation` and `/sources`.

## Rule: the fingerprint MUST be cross-process deterministic
**Why:** the value persists across worker restarts and is compared between
separate processes (a baseline recorded by one worker vs. a re-read by another).
If two processes hash the same data differently, every page load false-flags a
change and badges every sheet "updated".
**How to apply:**
- Sort the canonical lines before hashing (record order from threaded reads
  varies; sorting makes the multiset order-independent).
- Normalise numerics: `_num` coerces int and float to the SAME fixed 4-dp form.
  An integer `100` and float `100.0` for the same cell must hash identically —
  parsers can emit either. (This was a real bug a test caught.)
- Fingerprint only sheet-sourced fields (`_FP_FIELDS`); exclude derived/config
  fields like the *used* `ideal_hours` (it can come from baselines.json, so a
  config change would masquerade as a sheet edit).
- Verify cross-process determinism by dumping fingerprints from two *separate*
  `python3` invocations and diffing — not two calls in one process (cached
  objects make in-process comparison trivially equal and prove nothing).

## Rule: re-baseline after any fingerprint-formula change
**Why:** changing the formula (or `_FP_FIELDS`/`_num`) makes the same data hash
differently, so the next run sees a "change" and appends a spurious snapshot.
During development this shows up as `distinct > 1` snapshots per file that are
pure dev noise, which then badge as falsely "updated" for `RECENT_DAYS`.
**How to apply:** after settling the formula, `TRUNCATE source_fingerprints` and
let the next page load record clean baselines. The table holds no user data,
only detected snapshots — truncation is safe.

## Rule: one row per version, ON CONFLICT DO UPDATE (revert-safe + concurrency-safe)
`(file_id, fingerprint)` is UNIQUE; a write is issued only on a real transition
(first sight, or current ≠ last-seen version), never on an unchanged re-read.
On conflict the row is **touched** (`observed_at = now()`), not left alone.
**Why:** two cases must both stay correct — (1) concurrent duplicate inserts of
the same transition must collapse to one row (DO NOTHING + a race could
otherwise inflate the snapshot count into a false "updated"); (2) a *revert*
A→B→A must re-establish A as the latest so the next read converges (current ==
latest) instead of re-detecting a change every load forever. DO NOTHING leaves B
as "latest" and never converges. Touching on conflict fixes both, and snapshot
count stays "distinct versions seen" (drives `ever_changed`).
**How to apply:** `RETURNING *` returns the row on both insert and update, so no
fallback SELECT is needed. The unique index requires no pre-existing duplicate
pairs — a brand-new table is created with the index up front, so it never fails.

## Rule: "partial check" = total category read failure only
The panel's amber "partial" banner fires only when an ENTIRE read category
(all monthly grids, or all daily workbooks) raises. **Why:** an isolated
per-file daily failure doesn't raise (the reader recovers the rest); that file
just drops out and is listed from its last-known snapshot — already honest. Do
NOT treat reader *warnings* as partial: the daily reader always emits a benign
daily-vs-grid reconciliation note, which would false-alarm on every load.

## View-independence
`_build_freshness()` fingerprints the FULL current dataset (`get_records` +
`get_daily_records` over `months_with_data()`), NOT the page's selected period —
so the same change state shows identically on every page regardless of filters.
First sight = baseline (never flagged); a change within `RECENT_DAYS` (7) =
"updated". Degrades to listing-only (nothing flagged) without `DATABASE_URL`;
skipped entirely in demo mode.
