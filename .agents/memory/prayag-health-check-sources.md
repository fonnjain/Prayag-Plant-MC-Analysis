---
name: Prayag dev health-check & report-only sources
description: Why report-only Google Sheet sources must load on-demand, not via ANNUAL_SOURCES, and the /reports duplicate-route shadowing trap.
---

# Dev health check hits previewPath "/" — keep it cheap

The dev workflow health check probes the artifact's `previewPath` ("/"). The "/" route cold-loads the dashboard dataset (one Google Sheets read per source in `ANNUAL_SOURCES`). Adding report-only workbooks to `ANNUAL_SOURCES` bloated the cold "/" load (4 → 24 workbooks, ~100s+), so the health check timed out and the platform SIGKILLed the process — the server banner appeared with NO traceback, which looks like a hang/crash but is purely load-time.

**Rule:** sources that only back `/reports/*` pages must NOT go in `ANNUAL_SOURCES`. Put them in `REPORT_SOURCES` and load them lazily/per-request via `sheets.load_report_records(family)` (TTL-cached, per-source error-isolated, demo-guarded). This keeps "/" at the 4 dashboard grids.

**Why:** prod health uses the lightweight `/health` path so prod was fine; only dev (which hits "/") failed — masking the real cause.

**How to apply:** when wiring any new sheet source, ask "does the main dashboard need it on first paint?" If no, it is a report source → `REPORT_SOURCES` + `load_report_records`, and remember to add its cache to `clear_caches()`.

# /reports duplicate-route shadowing

Flask resolves the FIRST registered rule for a path. Two `@app.route("/reports")` handlers existed; the earlier `reports()` shadowed the newer hierarchical `reports_index()`, so the new index silently never rendered (still 200, just the wrong/empty template). When a "rebuilt" page appears not to take effect, grep for duplicate `@app.route` decorators on that path before debugging the template.

# Test heavy network loads via a file-logging script, not curl-on-server

The sandbox SIGKILLs bash commands that do the slow cold Sheets load (no stdout survives). To measure/verify a load, run a standalone python script that writes timestamped lines to a file with `flush()` after each, bound it with `timeout 30`, and `cat` the file in the SAME command. Trivial python (imports, py_compile) runs fine — only the long network I/O gets killed.
