---
name: Prayag sheet reconciliation quirks
description: Non-obvious behaviors of Prayag's live Google Sheet M/C summary grids when reconciling parsed totals against the grid TOTAL row.
---

# Prayag M/C grid reconciliation quirks

When cross-checking parsed per-machine OUTPUT against a family grid's `TOTAL` row:

- **Some family grids carry a trailing grand-total OUTPUT column** (e.g. Garden,
  HDPE) in addition to the per-machine OUTPUT columns. Naively summing every
  column labelled "OUTPUT" on the TOTAL row double-counts (exactly 2× for
  single-machine-block months). Pipe/Moulding grids have NO such column.
  **Why:** the layout differs per family and isn't inferable from headers alone.
  **How to apply:** detect the grand-total column as the OUTPUT value equal to
  the sum of the rest (~1% tol) and use it instead of summing all columns.

- **`drive.file` scope cannot list Drive folders** (`files.list` returns 0).
  Every workbook must be read by an explicit pinned file ID in `sources.py`;
  new months are added by hand. Do not attempt folder auto-discovery.

- **Stored ratio cells (Utilisation %, Output Efficiency %) are wrong** in the
  source and must always be recomputed from raw hours/output.

- **Fiscal year is Apr–Mar.** When resolving a bare month number to a year,
  Apr–Dec map to the FY start year and Jan–Mar to the FY start year + 1.

- **Monthly-grain data has no true OEE** (no per-shift availability/performance/
  quality), so OEE/A/P/Q must never be shown as real numbers at that grain.
  **Why:** the monthly grids record total hours+output only; A/P/Q would read 0%
  and mislead. **How to apply:** every view/report/template must branch on
  `MetricsResult.oee_available` and fall back to the `headline`/`headline_label`/
  `headline_rating` props (OEE when available, else Output Efficiency) plus
  Utilisation; hide A/P/Q, downtime, and the loss Pareto. New surfaces that show
  OEE are incomplete until they honour this flag.
