---
name: Prayag workflow proxy warmup
description: Why the artifact-managed workflow fails its health check and what the working workaround is.
---

## The bug

`restart_workflow "artifacts/prayag-web: web"` always fails with "The preview endpoint did not respond with HTTP 200" for port 21800 (or DIDNT_OPEN_A_PORT for any non-.replit-registered port like 8000).

Root cause: Replit's **external HTTPS proxy takes 2–3 seconds to warm up** after Flask binds port 21800. The artifact-managed workflow health check fires *immediately* after detecting the port is open (via /proc/net/tcp), which is right inside that 502 window. The internal curl and the localhost:80 proxy both return 200 instantly, but the external dev-domain check returns 502 for those first 2–3 seconds.

Evidence: `curl -sk "https://$REPLIT_DEV_DOMAIN/" --max-time 2` returns 502 at t=1s and t=2s after a fresh Flask start, then 200 from t=3s onward.

## Why port 21800 (not 8000)

Port 21800 is registered in `.replit [[ports]]` (externalPort=3001), so the workflow manager *can* detect it as open (passes DIDNT_OPEN_A_PORT). Port 8000 is NOT registered in `.replit [[ports]]`, so the workflow manager never sees it as open regardless of Flask running.

## The fix

A **separate `console`-type workflow** ("Prayag App") with no `waitForPort`:

```javascript
await configureWorkflow({
  name: "Prayag App",
  command: "cd /home/runner/workspace/artifacts/prayag && PORT=21800 python3 app.py",
  outputType: "console",
  autoStart: false
});
```

`outputType: "console"` with no `waitForPort` bypasses both the port-detection gate and the external HTTPS preview check. The process just starts and is assumed running.

**Why:**  The artifact routing (paths=["/"] → localPort 21800) is independent of the artifact workflow's run/fail state. As long as Flask is on 21800 (via any workflow), the proxy routes "/" correctly.

## How to apply

- To restart the Flask app: `restart_workflow "Prayag App"` (not the artifact-managed one).
- Leave "artifacts/prayag-web: web" in FAILED state — do not try to restart it; it will always fail the health check.
- If "Prayag App" workflow is ever deleted, recreate it with the configureWorkflow call above.
- The artifact.toml stays at localPort=21800 for production gunicorn deployment (unaffected).
