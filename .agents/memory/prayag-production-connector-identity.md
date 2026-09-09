---
name: Prayag production connector proxy
description: Supported Google connector transport for published Prayag runtimes.
---

Published runtimes must not request raw Google OAuth secrets through the connection-list endpoint. Mint an audience-scoped deployment identity through the local hosting identity endpoint, then route Google API paths through the connector proxy with the matching connector name. Development can continue using the direct OAuth-token path.

**Why:** Raw deployment renewal credentials are rejected, and even a correctly minted deployment identity receives 401 when a published app asks for `include_secrets=true`. The supported connector SDK uses the proxy so OAuth tokens are injected and refreshed without exposure.

**How to apply:** Detect deployment markers and follow the SDK mint order: `replit identity create --audience` first, then hostingpid1 loopback fallback under one bounded deadline. Call `/api/v2/proxy/<provider-path>` with `Connector-Name` plus the deployment identity, and retry once on proxy 401 with a fresh mint.