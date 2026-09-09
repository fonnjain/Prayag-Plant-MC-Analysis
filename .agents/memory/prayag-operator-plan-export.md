---
name: Prayag operator machine-plan exports
description: Durable boundary between floor-ready machine-plan workbooks and specialist planning reports.
---

Primary machine-plan downloads must use one shared floor-operator workbook format across current, frozen, ZIP, and temporary customized runs. Operator-facing time is committed whole block hours, not productive hours; figures come unchanged from existing engine and scheduler results.

**Why:** Operators need one consistent day/shift/machine document, while Report 11/12, capacity, comparison, corrective, and Follow-Up workbooks have distinct analytical purposes. Replacing those specialist reports would remove needed evidence rather than merely standardize layout.

**How to apply:** Route the main machine-plan workbook through the shared operator renderer. Add specialist reports alongside it. Never recompute optimization, routing, rates, quantities, or schedules inside the renderer, and never persist a temporary custom run merely to make its workbook downloadable.