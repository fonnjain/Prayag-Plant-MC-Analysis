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
