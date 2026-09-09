---
name: Prayag production connector identity
description: Credential precedence for Replit connector calls from published Prayag runtimes.
---

When requesting Replit connector credentials from a published runtime, mint an audience-scoped deployment identity through the local hosting identity endpoint. Never send the raw deployment renewal credential to the connector service. Development can continue using the repl identity.

**Why:** Raw deployment renewal credentials are explicitly rejected by the connector service. Sending one caused 401 responses for Google Sheets and Drive even though development reads and the connected-account status were healthy.

**How to apply:** Detect deployment markers, mint against the connector audience through hostingpid1's loopback endpoint with a bounded startup retry, and send the resulting token as a deployment identity. Fail closed if minting fails.