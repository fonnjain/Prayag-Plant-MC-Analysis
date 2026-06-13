---
name: Replit connectors from Python
description: How to use a Replit OAuth connector (e.g. google-sheet) from a Python app — the SDK is JS-only
---

The Replit connectors SDK is JavaScript-only (`@replit/connectors-sdk`). There is **no** `replit-connectors` package on PyPI, so `uv add replit-connectors` fails.

**From Python, call the connectors credential API directly** (this is what the JS SDK does internally):
1. Read `REPLIT_CONNECTORS_HOSTNAME`. Build the auth header value: `"repl " + REPL_IDENTITY` (dev) or `"depl " + WEB_REPL_RENEWAL` (deployed).
2. GET `https://{hostname}/api/v2/connection?include_secrets=true&connector_names=<connector>` with header `X_REPLIT_TOKEN: <that value>`.
3. `items[0].settings.access_token` is a short-lived OAuth token (also under `settings.oauth.credentials.access_token`).
4. Use it as `Authorization: Bearer <token>` against the provider's REST API.

**Why:** tokens auto-rotate, so fetch fresh on every request — never cache. The connectors API handles refresh.

**How to apply:** any Python artifact needing a connector (Google Sheets/Drive, etc.). Wrap the credential fetch in try/except and surface a clear error when unauthorized/unreachable rather than 500.
