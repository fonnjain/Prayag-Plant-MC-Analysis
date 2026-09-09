---
name: Prayag production connector identity
description: Credential precedence for Replit connector calls from published Prayag runtimes.
---

When requesting Replit connector credentials, try the deployment renewal identity before the repl identity when both are available, while retaining the repl identity as a fallback.

**Why:** A published VM exposed both identity variables. Selecting the repl identity first caused connector-service 401 responses for Google Sheets and Drive even though development reads and the connected-account status were healthy.

**How to apply:** Any shared connector-token helper used in both development and publishing must prefer the deployment-scoped credential and may fall back to the repl-scoped credential if the first request fails.