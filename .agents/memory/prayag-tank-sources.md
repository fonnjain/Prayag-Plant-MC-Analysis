---
name: Tank source wiring
description: Correct file IDs and unit for Tank VN/WB/KH 26-27 annual sources, and integrity flags
---

## Verified file IDs (26-27 annual)
- **VN**: `1Wa2jFV66NS-ntlSKqo8jzFFwgZfcdvgJYEAuFU0qdAI`
- **WB**: `1_ugk2V3Vs8MrKLsSeElz8L3M6YnSy6BM6TgUH2iydag`
- **KH**: `1T4RDvDNqxqbsL3zRWoTPcijdvQGPQjtBTw8S0qe98rs`

All three are `kind: "tank_annual_2627"`, `unit: "Ltr"`, in REPORT_SOURCES.

**Why:** VN previously held WB's ID (plants were swapped). WB held an unknown ID. KH 26-27 was not registered at all.

## Integrity flags (surfaced in report_tank_location.html)
- **KH**: Annual has JUN only (Apr/May/Jul zero). KH daily exists for Apr+May+Jun. JUL daily file not yet registered. Flag fires when `len(months_with_prod) < 3`.
- **WB**: Item-detail strip has Jul 2,198 pcs vs TOTAL row 1,834 pcs / 1,432,000 Ltr. Parser uses item-level Ltr (correct); pcs discrepancy flagged as advisory.

## Acceptance targets
- KH 633,500 Ltr (JUN only) · VN 1,852,250 · WB 4,325,100
- Combined: 6,810,850 Ltr / rejection 86,500 Ltr (1.27%)
- By month: APR 636,250 · MAY 1,582,500 · JUN 2,596,600 · JUL 1,995,500

## tank_kh route
Previously redirected to `/?plant=TANK`. Fixed to call `_tank_location_report("tank_kh", "TANK", "KH", ...)`.
