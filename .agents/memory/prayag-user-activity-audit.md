---
name: Prayag user activity audit
description: Privacy and timing rules for the dashboard's authenticated user activity reports.
---

Record authenticated usage as server-accounted session intervals plus safe, route-derived event labels. Do not log passwords, form data, API keys, query strings, arbitrary URLs, keystrokes, screenshots, or screen activity.

**Why:** Audit reporting must show meaningful active/idle time, pages, and actions without turning the dashboard into surveillance or allowing a suspended browser tab to invent hours of usage.

**How to apply:** Credit only the prior heartbeat interval using its server-stored state, cap missed intervals, and preserve historical audit rows independently of account deletion. Allow the heartbeat during mandatory password change; it is the only non-password endpoint permitted in that forced-change state.