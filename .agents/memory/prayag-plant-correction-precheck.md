---
name: Plant-correction precheck
description: Required source-history check before classifying daily-production month drift as an application defect.
---

Before treating a monthly record-count, output, rejection, or run-hours difference as a parser/read defect, compare the plant workbook's retained revisions and current source values. May, June, and July 2026 PIPE differences were all deliberate plant corrections, so this is a recurring source-maintenance pattern rather than three unrelated incidents.

**Why:** Assuming drift is an application defect risks rejecting valid plant corrections or freezing a superseded baseline. Google Drive revision `exportLinks` can provide historical XLSX snapshots for direct cell comparison even though the standard Sheets API does not expose cell-level revision history. Drive exposes the current last modifier, but not an editor identity for each historical revision.

**How to apply:** For each month considered for freeze, compare the accepted baseline/capture date with retained workbook revisions first. Identify changed cells, bound their timestamps, recompute canonical logical totals, and restate documented acceptance figures before previewing the freeze. If revision evidence cannot explain the drift, stop and investigate parser/read behavior.

When retained source revisions and raw cells agree but the application total differs,
also audit the Sheets transport rendering mode before classifying the result as a
plant change. GARDEN May 2026 was 53,234.48 kg in the source; 53,235 kg was
introduced by display-formatted value rounding before aggregation.

**Why:** A transport-layer precision defect is an application correction, not a
plant edit. Labeling it as a source correction would create a false operational
history and obscure the real cause.

**How to apply:** Preserve raw numeric values until after aggregation, retain
formatted date strings where parsers need them, and record any affected snapshot
as a transport correction while retaining the prior immutable version.

Drive may trim retained history over time: TANK_WB exposed 20 entries during the
first May audit but later exposed 10, while the latest revision remained 2202.
A shrinking revision count alone is not a data change; compare the latest
revision ID, timestamp, and current canonical totals. Treat the reduced window as
lost audit depth and capture required historical exports promptly.