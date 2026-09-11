---
name: Plant-correction precheck
description: Required source-history check before classifying daily-production month drift as an application defect.
---

Before treating a monthly record-count, output, rejection, or run-hours difference as a parser/read defect, compare the plant workbook's retained revisions and current source values. May, June, and July 2026 PIPE differences were all deliberate plant corrections, so this is a recurring source-maintenance pattern rather than three unrelated incidents.

**Why:** Assuming drift is an application defect risks rejecting valid plant corrections or freezing a superseded baseline. Google Drive revision `exportLinks` can provide historical XLSX snapshots for direct cell comparison even though the standard Sheets API does not expose cell-level revision history. Drive exposes the current last modifier, but not an editor identity for each historical revision.

**How to apply:** For each month considered for freeze, compare the accepted baseline/capture date with retained workbook revisions first. Identify changed cells, bound their timestamps, recompute canonical logical totals, and restate documented acceptance figures before previewing the freeze. If revision evidence cannot explain the drift, stop and investigate parser/read behavior.

Drive may trim retained history over time: TANK_WB exposed 20 entries during the
first May audit but later exposed 10, while the latest revision remained 2202.
A shrinking revision count alone is not a data change; compare the latest
revision ID, timestamp, and current canonical totals. Treat the reduced window as
lost audit depth and capture required historical exports promptly.