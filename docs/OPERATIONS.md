# Running and releasing the web application

## Local

From a checkout with Python 3.12+, run `uv sync --frozen`, then `uv run duress serve --host 127.0.0.1 --port 8000`. The default bind is loopback. `GET /api/health` reports version, hosted mode, token requirement and whether the operation gate is busy. Stop with Ctrl-C; `duress sweep` checks retained target pidfiles using the existing engine cleanup path.

The public web pages are `/`, `/targets`, `/investigations`, `/investigations/{id}`, `/dna`, `/dna/{id}`, `/operations/{id}`, `/reductions/{id}`, `/differential`, `/differential/{id}`, and retained `/evidence/{trial_id}` snapshots. The workspace also serves `/guide`, `/design-system`, the local-only `/projects` workbench (`/api/projects/...` for its API) and `/dna/{id}/download`. API documentation is at `/docs`.

## Container

Build with `docker build -t duress:web .`. Start with `docker run --rm -p 8000:8000 -e PORT=8000 duress:web`. The container command expands `$PORT` and binds the public service to `0.0.0.0`; trial processes still use the existing loopback-only manager.

Run `scripts/smoke.sh http://127.0.0.1:8000` for read-only API and page checks. Add `--dna showcase/dna/e8007dd56adae8ae.json` to exercise the existing portable replay and verification endpoints. Those operations consume the existing operation quota. If the server has a token configured, pass it through the `DURESS_DEMO_TOKEN` environment variable; the script does not print it. Enter the same token in the web action panel when using the browser.

## Browser verification

Run `uv run playwright install chromium` once, then `DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser`. Tests start only a local application against a temporary runtime home. Browser tests are opt-in so ordinary environments do not silently require browser downloads. axe-core 4.10.3 is vendored under `tests/browser/vendor/` with its license, solely for test-time checks. HTMX is vendored separately with its license under the web static assets.

## Evidence and release checks

`uv run python scripts/measure.py --run fast` executes a test suite and preserves raw JUnit in ignored runtime storage. With `DURESS_RECORD_MEASUREMENTS=1`, it additionally writes a uniquely named JSON/JUnit record under `docs/measurements/`. `--render` renders retained evidence and runs no tests. Use a temporary output file when reviewing generated Markdown.

Before a GitHub checkpoint, inspect `git diff --check`, execute focused page/API/browser checks, and review any execution-layer diff explicitly. The final pass adds schedule persistence and safer record reads; it does not change runner, oracle, preflight, replay, invariant, reduction, or target semantics. At release, run the full fast regression suite and the container smoke workflow. Record exact observed commands and results in `docs/EVIDENCE.md`; a failed or unexecuted hosted check must remain explicitly unverified.

## Render

The repository contains `render.yaml` for a Docker Free service with `/api/health` as its health check. Render generates the demo token. Free compute does not cap all workspace/bandwidth/build charges; follow the [$0 deployment handoff](openfire/RENDER_HANDOFF.md), inspect account billing, and do not upgrade without explicit approval. Provider credentials are unnecessary for cached or exhaustive investigation, replay and verification. Live investigation uses `DURESS_LLM_BASE_URL`, `DURESS_LLM_MODEL` and optional `DURESS_LLM_API_KEY`; genuine hosted inference remains unverified. Bind `DURESS_ROOT=/app` and use `/tmp/duress` as ephemeral runtime storage. Logs go to the existing uvicorn stdout/stderr stream; generated evidence stays in the runtime home, outside Git.

The public Free deployment is claimed only for the scope recorded in the [public release record](release/2026-10-27-public/README.md). The local Docker checks do not establish hosted capacity, performance or availability.

## Data and access

This is one workspace. Reads are public if you expose the server; the token protects the existing write operations. JSON exports may include source-independent synthetic entity identifiers and observations. Keep production credentials and data out of runtime records and Git. The browser uses text nodes for API-provided strings, Jinja autoescapes records, and embedded JSON uses the Jinja JSON encoder.

The API's existing quotas and in-memory gate are retained. Use a single application worker for the public demo; separate web workers do not share that gate. The quota key is the first `X-Forwarded-For` entry when that header is present, otherwise the socket peer. The application does not check that the header came from a trusted proxy, so a client that can reach the server directly (or through a proxy that appends rather than replaces the header) can choose its own key and avoid the per-IP quota. The single-operation gate, the token and the loopback-only targets still apply. Whether Render's edge overwrites or appends the header was not verified, so treat the quota as a courtesy limit, not an abuse boundary, and keep the demo token private. Multi-user authorization and durable storage are outside the supported scope.

## Investigation and analysis actions

Open `/investigations` to choose a registered target and mode. Cached mode can be admitted anonymously; configured tokens protect exhaustive/live mode and other writes. Missing provider configuration disables live selection in the page. Cached responses may be authored fixtures and are labelled. Exhaustive mode executes the registered scenario set. API admission returns 202 and a status URL; it is not a business verdict.

Reduction starts from a DNA page and records search, preflight, independent revalidation, and the required fault-free characterization. The retained primary ID is authoritative. NOT_REDUCED and SKIPPED are meaningful outcomes, not successful removals. Compare fixes defaults to Explore (five valid trials per cell); Matrix uses twenty valid trials per cell and consumes more runtime. Both compare S3/S5/S9 by exact scenario ID.

The default quotas are three operations per IP per hour and thirty per IP per day. A complete demonstration spanning investigation, replay, verification, reduction, and differential exceeds the hourly default. Run the local browser acceptance fixture for that full sequence; it explicitly raises its isolated test server's quotas, without changing hosted defaults. For a public demo, schedule actions within quota or configure an appropriate server policy deliberately.

On startup, stale queued/running jobs become failed with `interrupted_by_restart`. Shutdown cancels owned tasks and awaits target cleanup. These jobs do not resume automatically. Normal browser navigation leaves server-side work running. A missing or expired record returns 404; the UI reports unavailable progress instead of claiming completion.

## Public release status and remaining account checks

The existing owner-operated Free service at https://duress.onrender.com serves verified executor commit `344db907adfb61519cab7023ce6db3772dcc3824`. Health, the redesigned UI/assets/fonts, desktop/mobile dark/light states and one real bounded cached investigation passed the scoped public acceptance. That investigation completed in 330.72 seconds with the expected business FAIL, 31 attempts/30 valid/one invalid target-unavailable attempt, and portable Failure DNA. [Exact exported public evidence](release/2026-10-27-public/README.md).

Authenticated public replay and correction verification remain pending owner execution with the private demo token. The available agent browser cannot inspect Render Billing: the owner confirms Free and Billing reviewed, while credit eligibility/expiry, current charges and future exposure remain independently unverified. Paid resources are not authorized. Keep one worker, the generated private token, quotas and loopback-only targets. Preserve the working service; documentation-only checkpoints use `[skip render]` to avoid replacing its ephemeral records. GitHub CI is separate and remains enabled.

Runtime files are lost on restart/redeploy/idle spin-down; download DNA before those events. A previous verified commit can be manually redeployed for rollback. Genuine Duress hosted-model interoperability and the live-versus-exhaustive experiment remain unverified; cached/exhaustive synthetic execution does not need model credentials. The live path is covered by local mock-provider tests (`tests/integration/test_live_mock_server.py`) and by `scripts/live_llm_check.py` and `scripts/compare_modes.py`, which the owner runs with a real key. No real provider has been called.

Open items:

- The one invalid public trial (`harness:target_unavailable` in `inv-20261027T134459-5dffbe`) is diagnosed in [INVALID_TRIAL.md](investigations/INVALID_TRIAL.md) as a probable readiness-budget expiry. A slow cold start is the leading hypothesis, but it is unproven: per-phase timings and target logs were not retained. Trial rows now record per-phase timings so the next occurrence can be measured; no budget change is made without measurements. Non-PASS trials already keep `target.log` under `evidence/`; export it before the Free instance restarts.
- A repair-call `transport_error` is now recorded as `transport_error` (fixed in `orchestrator.py`; regression test no longer xfail).
- `docs/productization/core-freeze.json` was re-frozen to the current hashes of seven files that had drifted (`cli.py`, `orchestrator.py`, `external/{manifest,reporting,services,web}.py`, `web/__init__.py`); the runner, oracle, preflight, replay and invariant core hashes were not changed.
- Authenticated public replay and correction verification are pending owner operation with the private token.

Build published distributions from an exact clean Git export. Local `.playwright-cli/` browser-session files and `brag-output*/` video-render workspaces are excluded from Git/source-build and Docker contexts; preserve them locally instead of shipping them. Validate archive contents and checksums before uploading release assets.
