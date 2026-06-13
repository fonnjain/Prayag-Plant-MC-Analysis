# Prayag Production Analytics

A mobile-first Flask dashboard that reads Prayag's real production Google Sheets
and computes OEE / utilisation / efficiency / rejection metrics. **All arithmetic
is deterministic Python** — Claude is used only to write narrative prose from
already-computed numbers. The stored ratio cells in the sheets (Utilisation %,
Output Efficiency %) are known to be wrong and are **never trusted**; every ratio
is recomputed from raw hours and output.

Brand: navy `#1F3864`, terracotta `#C55A11`. Dates display as `dd-mm-yyyy`.

## Run

```bash
cd artifacts/prayag && PORT=21800 python3 app.py
```

(The workflow `artifacts/prayag-web: web` does this for you.)

## Data grain — important

The live source today is **monthly**. Each annual M/C summary workbook
(`Pipe / Garden / HDPE / Moulding M/C 26-27`) holds one row per month per machine.

- The headline KPI is **OEE only when daily shift data is available**. With
  monthly data there is no availability/performance/quality split, so the app
  shows **Output Efficiency** (actual output ÷ ideal output) and **Utilisation**
  (actual hours ÷ ideal hours) instead, and labels the card accordingly.
- Picking a sub-monthly period (Yesterday / Last 7 / Last 30 / a short custom
  range) resolves to the **calendar month(s) it overlaps**, with a banner
  explaining that a daily breakdown isn't in the source yet.
- Months with no data yet show a "No data yet for …" banner rather than zeros
  pretending to be real.

When true daily workbooks are wired in (see below), the engine will prefer daily
records and the OEE path will light up automatically.

## How data is read (no folder auto-discovery)

The connected Google account's scope is `drive.file`, which **cannot list Drive
folders** or run a `files.list` search (verified: returns 0 results). So we read
every workbook by its **explicit file ID**, registered in `sources.py`:

- `ANNUAL_SOURCES` — the monthly M/C summary workbooks (currently parsed).
- `DAILY_SOURCES` — per-plant, per-month daily workbooks, keyed
  `PLANT -> { "YYYY-MM": file_id }`. `folder_ids` are kept for manual lookup
  only; they cannot be auto-listed.

### Adding a new month's file

1. Open the new monthly workbook in Drive and copy its file ID from the URL
   (`https://docs.google.com/spreadsheets/d/<FILE_ID>/edit`).
2. Add it under the right plant in `sources.py`:

   ```python
   DAILY_SOURCES["PIPE"]["files"]["2026-07"] = "<FILE_ID>"
   ```

3. Make sure the connected Google account has access to that file, then reload.

The **Detected Sources** screen (database icon in the header, route `/sources`)
lists every workbook the engine reads, its grain, the months that hold data, the
machine/detail tabs found, and whether each family reconciles to the sheet's own
grid TOTAL row.

## Reconciliation

For each annual family the engine sums the parsed per-machine month rows and
compares the total to the grid's own `TOTAL` row (within 2%). Some grids carry a
trailing grand-total OUTPUT column; the reader detects it (the column equal to the
sum of the rest) and avoids double-counting. Mismatches surface as validation
flags on every page and on the Detected Sources screen.

## Files

- `sources.py` — source registry (file IDs, tabs, plant/segment mapping).
- `sheets.py` — Google Sheets reader (token caching, batchGet, demo fallback)
  plus `get_records`, `detected_sources`, `months_with_data`.
- `parsers.py` — deterministic per-layout parsers (`parse_mc_detail`,
  `grid_total_output`, month-label parsing).
- `metrics.py` — `Record` model, `MetricsResult`, `compute_metrics`, rollups.
- `validate.py` — row/metric validation + TOTAL-mismatch reconciliation.
- `app.py` — Flask routes, the grain-aware period engine, view wiring.
- `narrative.py` / `pdf_export.py` / `glossary.py` — prose, PDF export, glossary.
- `templates/` — Jinja templates (mobile-first, Tailwind via CDN).
