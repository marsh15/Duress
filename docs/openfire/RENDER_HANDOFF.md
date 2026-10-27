# Owner-operated Render deployment

The original Dockerfile built successfully for accepted code `3284471`; Linux tests,
static/font routes, authorization and a real replay passed. The repository includes
`render.yaml`. No public deployment URL has been verified by the release agent.

1. Sign in to Render and connect the public `marsh15/Duress` repository.
2. Create a Blueprint from `render.yaml`, selecting the verified release branch/tag.
   Review the configured **starter** plan and its price before creating the service.
3. Keep the Docker runtime, `/api/health` health check and **one application worker**.
   The image's command already starts one worker on `0.0.0.0:$PORT`.
4. Preserve `DURESS_HOSTED=true`, `DURESS_ROOT=/app`, `DURESS_HOME=/tmp/duress` and the
   generated `DURESS_DEMO_TOKEN`. Do not put the token into a URL, README or frontend.
   Do not add arbitrary external target commands, manifests or production database access.
5. Deploy, inspect build/runtime logs, and wait for a healthy service. Dependency-download
   timeouts were observed locally before a successful retry; do not mistake them for a
   passing build. Cached/exhaustive demonstration needs no model-provider credential.
6. Open the actual HTTPS service URL in a signed-out browser. Run the smoke commands below.
   Then supply the URL for independent browser verification and README/submission updates.

```sh
curl --fail --silent --show-error https://YOUR-SERVICE.onrender.com/api/health
scripts/smoke.sh https://YOUR-SERVICE.onrender.com
```

In a browser verify overview, investigation list, reference targets, `/guide`, historical
record labels, Failure DNA and bundled fonts in both themes/mobile. Start one bounded
cached investigation of the trusted vulnerable reference target. Inspect its observed
business failure and generated DNA. Use the configured token in the page's memory-only
token field for authorized replay and correction verification; confirm persisted results.
No raw external application registration/execution should be available in hosted mode.

Storage is ephemeral. Download evidence before redeploying. The public synthetic
demonstration and local-first owned PostgreSQL/SQLite integrations are separate scopes.

Deployment is complete only after the real URL and workflow pass. Do not submit localhost
or an untested URL. The owner chose to operate this deployment; the release does not claim
Render authentication, deployment, capacity or a public smoke test on the owner's behalf.
