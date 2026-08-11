---
name: Prayag workflow proxy warmup
description: Why the artifact-managed workflow fails its health check on cold start and how to recover.
---

## The bug (cold-start only)

`restart_workflow "artifacts/prayag-web: web"` fails with "The preview endpoint did not respond with HTTP 200" **only on cold start** (fresh container/session, or immediately after the container comes up). It succeeds fine when the proxy is already warm.

Root cause: Replit's **external HTTPS proxy takes 2–3 seconds to warm up** after Flask binds port 21800. The artifact-managed workflow health check fires *immediately* after detecting the port is open, which is right inside that 502 window. The internal curl and localhost:80 proxy both return 200 instantly, but the external dev-domain check returns 502 for those first 2–3 seconds.

Evidence: `curl -sk "https://$REPLIT_DEV_DOMAIN/" --max-time 2` returns 502 at t=1s and t=2s after a fresh Flask start, then 200 from t=3s onward.

## Why port 21800 (not 8000)

Port 21800 is registered in `.replit [[ports]]` (externalPort=3001), so the workflow manager *can* detect it as open (passes DIDNT_OPEN_A_PORT). Port 8000 is NOT registered in `.replit [[ports]]`, so the workflow manager never sees it as open regardless of Flask running.

## Normal operation (warm proxy)

When the session/proxy is already warm, `WorkflowsRestart("artifacts/prayag-web: web")` succeeds normally. The artifact-managed workflow is the preferred one to keep running — it binds the canvas iframe correctly.

**To restart Flask after a code change:** always use `WorkflowsRestart("artifacts/prayag-web: web")`.
`WorkflowsRestart("Prayag App")` only restarts the echo no-op workflow and does NOT restart the Flask server — the gunicorn/Flask process keeps running with old code. This cost significant debug time when L1 cache couldn't be cleared because the wrong workflow was being restarted.

## Cold-start recovery (when artifact workflow fails health check)

If `artifacts/prayag-web: web` fails on cold start (proxy not yet warm):

```javascript
// Step 1: Start Flask via console workflow (no health check)
await configureWorkflow({
  name: "Prayag App",
  command: "cd /home/runner/workspace/artifacts/prayag && PORT=21800 python3 app.py",
  outputType: "console",
  autoStart: false
});
await restartWorkflow({ workflowName: "Prayag App", timeout: 30 });

// Step 2: Once proxy is warm (app loads in browser), switch back:
// a) Reconfigure Prayag App to no-op to free the port
await configureWorkflow({
  name: "Prayag App",
  command: "echo 'standby'",
  outputType: "console",
  autoStart: false
});
await restartWorkflow({ workflowName: "Prayag App", timeout: 10 });
// b) Then restart the artifact workflow
await restartWorkflow({ workflowName: "artifacts/prayag-web: web", timeout: 60 });
```

**Why step 2 works:** The proxy is warm after step 1, so the artifact workflow health check passes on retry.

## Port conflict rule

Both workflows CANNOT run Flask on port 21800 simultaneously. Before starting `artifacts/prayag-web: web`, ensure `Prayag App` is not holding the port (reconfigure it to a no-op echo first).

## Artifact.toml

`artifacts/prayag-web/.replit-artifact/artifact.toml` stays at `localPort=21800`. The artifact routing (paths=["/"] → localPort 21800) works regardless of *which* workflow Flask runs in — both serve the same port. But the canvas iframe "crashed" state is tied to the artifact workflow's run/fail status, so prefer keeping `artifacts/prayag-web: web` RUNNING.
