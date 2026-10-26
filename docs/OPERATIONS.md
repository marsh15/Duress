# Running and releasing the web application

## Local

From a checkout with Python 3.12+, run `uv sync --frozen`, then `uv run duress serve --host 127.0.0.1 --port 8000`. The default bind is loopback. `GET /api/health` reports version, hosted mode, token requirement and whether the operation gate is busy. Stop with Ctrl-C; `duress sweep` checks retained target pidfiles using the existing engine cleanup path.

The public web pages are `/`, `/targets`, `/investigations`, `/investigations/{id}`, `/dna`, `/dna/{id}` and `/operations/{id}`. API documentation is at `/docs`.

## Container

Build with `docker build -t duress:web .`. Start with `docker run --rm -p 8000:8000 -e PORT=8000 duress:web`. The container command expands `$PORT` and binds the public service to `0.0.0.0`; trial processes still use the existing loopback-only manager.

Run `scripts/smoke.sh http://127.0.0.1:8000` for read-only API and page checks. Add `--dna showcase/dna/e8007dd56adae8ae.json` to exercise the existing portable replay and verification endpoints. Those operations consume the existing operation quota. If the server has a token configured, pass it through the `DURESS_DEMO_TOKEN` environment variable; the script does not print it. Enter the same token in the web action panel when using the browser.

## Browser verification

Run `uv run playwright install chromium` once, then `DURESS_BROWSER_TESTS=1 uv run pytest -q tests/browser`. Tests start only a local application against a temporary runtime home. Browser tests are opt-in so ordinary environments do not silently require browser downloads. axe-core 4.10.3 is vendored under `tests/browser/vendor/` with its license, solely for test-time checks. HTMX is vendored separately with its license under the web static assets.

## Evidence and release checks

`uv run python scripts/measure.py --run fast` executes a test suite and preserves raw JUnit in ignored runtime storage. With `DURESS_RECORD_MEASUREMENTS=1`, it additionally writes a uniquely named JSON/JUnit record under `docs/measurements/`. `--render` renders retained evidence and runs no tests. Use a temporary output file when reviewing generated Markdown.

Before a GitHub checkpoint, inspect `git diff --check`, execute focused page/API/browser checks, and verify that no engine or target files changed. At release, run the full fast regression suite and the container smoke workflow. Record exact observed commands and results in `docs/EVIDENCE.md`; a failed or unexecuted hosted check must remain explicitly unverified.

## Render

The repository contains `render.yaml` for a Docker Starter service with `/api/health` as its health check. Render generates the demo token. Provider credentials are unnecessary for cached or exhaustive investigation, replay and verification. Live investigation uses `DURESS_LLM_BASE_URL`, `DURESS_LLM_MODEL` and optional `DURESS_LLM_API_KEY`; genuine hosted inference remains unverified. Bind `DURESS_ROOT=/app` and use `/tmp/duress` as ephemeral runtime storage. Logs go to the existing uvicorn stdout/stderr stream; generated evidence stays in the runtime home, outside Git.

No Render deployment is claimed without an actual deployment and HTTP/browser checks. The local Docker checks do not establish hosted capacity, performance or availability.

## Data and access

This is one workspace. Reads are public if you expose the server; the token protects the existing write operations. JSON exports may include source-independent synthetic entity identifiers and observations. Keep production credentials and data out of runtime records and Git. The browser uses text nodes for API-provided strings, Jinja autoescapes records, and embedded JSON uses the Jinja JSON encoder.

The API's existing quotas and in-memory gate are retained. Use a single application worker for the public demo; separate web workers do not share that gate. Deploy behind a correctly configured trusted reverse proxy when using forwarded client addresses. Multi-user authorization and durable storage are outside this frontend release.
