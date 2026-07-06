---
name: Google Drive folder discovery
description: With the google-drive connection bound, files.list on the pinned daily folders works — overturning the drive.file "no discovery" assumption in sources.py.
---

# Google Drive folder discovery is possible once the google-drive connection is bound

`sources.py` carries a long-standing comment block asserting that folder
auto-discovery is impossible because the connected Google account uses the
`drive.file` scope (which normally only exposes files the app itself
created/opened). That assumption was true when ONLY the `google-sheet`
connector was bound.

**Finding:** after binding the separate **`google-drive`** connection
(addIntegration + proposeIntegration) to this Repl, fetching a Drive token via
`connector_names=google-drive` and calling
`GET /drive/v3/files?q='<folder_id>' in parents` **succeeds** and returns the
real monthly workbooks — even though the reported scope string still only lists
`drive.file` (plus appdata/photos.readonly/apps/docs/spreadsheets, NOT
`drive.readonly`). Verified against the PIPE daily folder
(`1eE1xSVAvi8t4wO_eZnCvbxMjQiqBiRG6`) → returned Apr/May/Jun/Jul 2026 files.

**Why it works despite drive.file:** the daily folders/files are shared with the
connected account, and `files.list` scoped by an explicit `parents` folder id
returns those shared items. Use `supportsAllDrives=true&includeItemsFromAllDrives=true`.

**How to apply:** auto-discovery of new monthly files (instead of hand-pinning
each ID in `DAILY_SOURCES`) is now feasible — the `folder_ids` already stored per
plant in `sources.py` can be listed at runtime. Two connectors are in play: the
app reads sheet CELLS via `google-sheet`, but Drive metadata/listing needs a
`google-drive` token (`connector_names=google-drive`). Don't assume one token
covers both. If discovery ever 403s again, check the `google-drive` connection is
still bound to the Repl (status added/healthy), not just authorized at account level.

## Implemented auto-discovery — durable lessons

Discovery lists each plant's `folder_ids`, maps each spreadsheet filename to a
month, and ADDS any month not already pinned to `DAILY_SOURCES[plant]["files"]`.

- **Pins are authoritative; discovery is additive only.** A discovered file for
  an already-pinned month is ignored (even if it has a different id / newer
  mtime). Discovery can only surface a brand-new month, never change an existing
  month's figures. Filename→month parsing is conservative: needs a recognised
  month word + a 20xx year, else the file is skipped (never guessed) — honours
  the "never fabricate" invariant.
- **Gate to plants with a daily layout (`_DAILY_LAYOUTS`).** CP has `folder_ids`
  but no layout, so it is deliberately NOT auto-populated — otherwise you create
  phantom "file exists but no parseable data" states in freshness/confirm.
- **Copy-on-write when mutating the shared `DAILY_SOURCES`.** Discovery runs from
  background threads (startup warmup, refresh loop, /refresh) while request
  handlers iterate `DAILY_SOURCES`. Do NOT mutate `cfg["files"]` in place — build
  a new dict and reassign `cfg["files"] = new_files` (atomic reference swap under
  the GIL). In-place mutation risks `RuntimeError: dictionary changed size during
  iteration` under load.
- **Keep discovery OFF the request/health path.** A `before_request` hook that
  ran a synchronous Drive scan on the first request made the health probe to `/`
  fail the workflow restart. Trigger it only from background threads + manual
  `/refresh` (TTL-guarded, best-effort, never raises). Consequence: pickup is
  eventually consistent (≤ refresh interval, or immediate via the Refresh button /
  next cold-start warmup), not on the very first request after a new file lands.
