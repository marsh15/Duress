# Final runtime verification

This records local runtime checks for the Docker and Render release configuration. The full investigation UI is enabled. No deployment or hosted model call was made.

## Runtime contract

- The local `duress serve` command defaults to `127.0.0.1:8000` and Uvicorn's default is one worker. Trial targets remain loopback-only.
- The container binds the public web service to `0.0.0.0` on `${PORT:-8000}`. Its shell uses `exec` so the server process receives container stop signals directly.
- Render uses the Dockerfile, checks `/api/health`, keeps automatic deploys off, and stores runtime data under `/tmp/duress`.
- Live model-backed investigation needs `DURESS_LLM_BASE_URL`, `DURESS_LLM_MODEL`, and `DURESS_LLM_API_KEY` when the provider requires a key. Cached, replay, and verify flows do not need those settings.

## Checks run

Environment: macOS on Apple Silicon, uv 0.12.19, Docker client/server 29.8.2 on OrbStack (`linux/arm64`). The focused pytest run used Python 3.12.12.

| Command | Result |
| --- | --- |
| `bash -n scripts/smoke.sh scripts/portability.sh` | Passed. |
| `uv run duress serve --help` | Passed. It reports loopback host `127.0.0.1`, port `8000`, and no worker-count option. |
| `uv run pytest -q tests/unit/test_runtime_release.py tests/unit/test_measure.py tests/unit/test_profiles.py tests/integration/test_isolation.py::test_target_bind_loopback tests/integration/test_api_lifecycle.py` | `20 passed, 1 warning in 1.67s`. The warning is Starlette's deprecation notice about `httpx` in `TestClient`. |
| `UV_PYTHON=3.12 UV_PROJECT_ENVIRONMENT=<fresh-temp>/project-venv bash scripts/portability.sh` | Passed in a fresh CPython 3.12.12 environment; uv installed 34 locked packages. The script built DNA `e8007dd56adae8ae` in home A, copied only its JSON file into empty home B, replayed it as `REPRODUCED` (expected command exit 1), verified the fix as `VERIFIED` (exit 0), then printed `== portability passed ==`. The same check also passed earlier on Python 3.14.7. |
| `docker build -t duress:web .` | Passed. Built the current integrated checkout as `duress:web`, manifest `sha256:7f6a9ff21c16fa6b5601bbbbe4a2e9b5935203406cca2e098dcdcad950e08980`. |
| `docker run --detach --publish 127.0.0.1::8000 --env PORT=8000 --name duress-final-smoke-e447dd33 duress:web`, then `bash scripts/smoke.sh http://127.0.0.1:32768 --dna showcase/dna/e8007dd56adae8ae.json` | Passed against the container. PID 1 was `/app/.venv/bin/python /app/.venv/bin/duress serve --host 0.0.0.0 --port 8000`; health, targets, and pages returned HTTP 200; portable replay returned `REPRODUCED`; verify-fix returned `VERIFIED`. `docker stop` returned 0 and the container state was `0 exited`. |
| `scripts/smoke.sh http://127.0.0.1:62364` against a temporary local server | Passed: health, targets, home, investigation detail, and DNA detail returned HTTP 200. No operation was started. After SIGTERM, Uvicorn logged `Shutting down`, `Application shutdown complete`, and `Finished server process`; the process status was `-15` (SIGTERM). |

The portability run used temporary runtime homes and an isolated Python environment. It did not set `DURESS_RECORD_MEASUREMENTS=1`; historical measurement files were not regenerated.

## Access status

- Render deployment access was not available through the configured tools: there is no Render-specific connected tool, the `render` CLI is missing, and `RENDER_API_KEY`, `RENDER_CLI_TOKEN`, and `RENDER_TOKEN` are unset. The dashboard opened at its login page. A hosted check needs an authenticated Render session with access to the service/workspace.
- No `DURESS_LLM_*` environment variables are set. Live inference needs the provider base URL and model in the Render service environment, plus an API key if that provider requires one. No LLM was invoked and no model weights were downloaded.
- The final local image was rebuilt after the integrated web assets were present. Render deployment and hosted verification remain unrun.

## Final packaging boundary

A later image-context audit found that `.recovery/` was Git-ignored but missing from `.dockerignore`. It is now excluded alongside `.duress/`. The dedicated runtime contract suite passed four tests after this correction. The image above is the earlier successful build; the final clean-checkout image and post-correction checks are recorded in the final evidence section of `EVIDENCE.md`.

## Final verified image and real operation lifecycle

The final application image was rebuilt from clean checkout `38076bb` as `duress:release`, identity `sha256:53f47629466fb4b800b059d837e6a6b4e731a7133fde725b2960846703a1cdf5`. Its 79-case focused suite passed on Linux/Python 3.12.15. Actual non-secret packaging probes confirmed `.recovery`, `.duress`, and `.git` were absent from `/app`.

The final container passed public pages/assets, token refusal, portable replay, fixed-target verification, cached investigation, reduction, differential comparison, and stop/restart during a real investigation. The interrupted job stayed failed with `cancelled`; the gate was clear and no managed target/pidfile remained. Each container exited 0. Exact records, commands, logs, image identity, and the earlier disk-full interruption are retained in [the final evidence](EVIDENCE.md) and [container observations](release/2026-10-27/container/summary.json). The low host disk space was handled without deleting unrelated data. Render and hosted inference remain externally blocked as described above.
