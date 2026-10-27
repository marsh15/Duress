# Owner-operated Render deployment

The original Dockerfile built successfully for accepted code `3284471`; Linux tests,
static/font routes, authorization and a real replay passed. The repository includes
`render.yaml`. The existing public Free service is verified at https://duress.onrender.com; see the deployment checkpoint below.

1. Sign in to Render and inspect existing Blueprints/services/deploys first. Preserve a working deployment. Do not create a duplicate. Connect `marsh15/Duress` only if no existing Duress service can be used.
2. If there is no existing service, create a Blueprint from `render.yaml`; otherwise preserve/use the existing service and review its configuration. Select the verified release branch/tag.
   Use the configured **Free** compute plan. Prefer final merged main; otherwise use the verified release branch, and record its full deployed SHA. The $50 credit balance is owner-reported, not independently verified; paid compute is not authorized.
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

## $0 out-of-pocket constraint

The owner reports $50 of Render credits and permits **$0 out-of-pocket expenditure**. This Blueprint selects Free compute and adds no database, disk, worker, scaling or preview resources. A Free compute plan alone is not a universal billing cap.

Before starting or changing a service, inspect Billing: credit eligibility, remaining balance, expiration/restrictions, payment-method exposure, current charges, workspace plan, outbound bandwidth and build-pipeline allowance. Set the build-pipeline spending limit to $0 where supported, and review bandwidth separately. Render documents supplementary bandwidth/build billing when allowances are exceeded and a payment method is present; a pipeline limit does not cap all charges. Do not claim a guaranteed $0 total from promotional credits or Free compute.

No paid upgrade is authorized. If Free cannot complete a bounded real investigation, retain the actual crash/timeout/resource evidence and stop. Propose a specific paid instance only after verifying credit terms and all exposure, with estimated consumption; obtain explicit owner approval before changing plans. If a $0 out-of-pocket outcome cannot be established, do not upgrade. If paid compute is later approved, record expiration and the exact service Settings → Instance Type downgrade path (or service Settings → Delete Web Service removal) before credits expire.

## Free acceptance and evidence

After Live, record actual URL, service name, plan and deployed SHA. Verify health, current UI/static/font assets, both themes, investigations, historical evidence, DNA and registered synthetic targets in an unauthenticated browser. Run one permitted actual investigation within quotas; retain lifecycle, verdict, valid/inconclusive counts, timing and exported DNA. Test authorized replay/correction where feasible without publishing the generated private token. Test hosted external-execution refusal. Inspect logs/metrics for memory pressure, restarts, worker exits, cold-start behavior and timeouts; a loaded homepage is insufficient. Do not weaken supervision or invariants.

Free instances spin down after inactivity, and runtime files are lost on spin-down/restart/redeploy. Export new evidence promptly. Bundled historical showcase evidence remains reproducible; runtime persistence is not durable cloud storage. No paid disk is needed for this demo.

Current agent status: Render Dashboard redirects to sign-in. Existing services, deploys, credit terms, billing exposure and current charges cannot be inspected without account access. The public deployment was independently tested through its HTTPS URL; no account billing or future financial guarantee is claimed. Owner operates Render.

Sources checked 27 October: [Render Free limitations](https://render.com/docs/free), [Blueprint reference](https://render.com/docs/blueprint-spec).

## Verified deployment checkpoint

The existing service `srv-dbga5mvb9u1s73cn8h60` now serves https://duress.onrender.com with the redesigned UI and the exact merged-main workspace asset hash. Deployment record: `344db907adfb61519cab7023ce6db3772dcc3824`; owner manually deployed the current commit. Owner confirms Free and Billing reviewed; the release agent did not create or upgrade resources. One actual public cached investigation completed in 330.72s with the intended business FAIL and generated DNA. [Full public acceptance](../release/2026-10-27-public/README.md). Account credit eligibility/expiry, current charges and memory metrics remain independently inaccessible. Authenticated public replay/correction remain pending owner operation at this checkpoint.
