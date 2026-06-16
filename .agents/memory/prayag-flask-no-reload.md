---
name: Prayag Flask no auto-reload
description: The running dashboard does not hot-reload code; you must restart the workflow before curl-testing edits.
---

The Prayag Flask app runs in production-style mode (debug/reloader OFF) under the
`artifacts/prayag-web: web` workflow. Editing a `.py` file and re-importing it in a
fresh `python3 -c` shell proves the file parses, but the **already-running** server
keeps the OLD code in memory.

**Rule:** after any edit to `app.py` (or any module the server imports), `restart_workflow("artifacts/prayag-web: web")` BEFORE curling endpoints, or you will be debugging stale behaviour.

**Why:** a newly-added route guard returned the pre-edit response on curl until the
workflow was restarted — looked like a logic bug, was actually a stale process.
