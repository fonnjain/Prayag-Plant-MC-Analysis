---
name: Replit connector API filter bug
description: The connector_names query parameter is silently ignored by connectors.replit.com; use id-prefix selection instead.
---

## Rule

Never use `connector_names=<name>` as a query filter on `connectors.replit.com/api/v2/connection`.

**Why:** The parameter is silently ignored — the API returns 0 items when this filter is present, even though the connection is healthy and active. Fetching without any connector filter returns all connections successfully.

**How to apply:** Always call `/api/v2/connection?include_secrets=true` (no `connector_names` param) and then pick the right connection by its `id` prefix:
- Google Sheets: `id` starts with `conn_google-sheet_`
- Google Drive: `id` starts with `conn_google-drive_`

Also use `X-Replit-Token` (hyphens) not `X_REPLIT_TOKEN` (underscores) as the header name in Python `urllib.request.Request`. The hyphen form is what curl confirms works.

**The proposeIntegration step:** Even when a connection shows `status=added` in `searchIntegrations()`, if the credential proxy returns 0 items at runtime, call `proposeIntegration` with the connection ID to refresh the platform-side binding. Without it, the proxy returns nothing for this Repl.
