# Architecture

Duress is one Python application. FastAPI serves its JSON API, Jinja renders pages, HTMX refreshes record fragments, and small local JavaScript files connect forms to existing JSON endpoints. Assets are bundled; browsing requires no CDN or model provider.

## Execution boundary

`services.py` owns the shared CLI/API execution path. The UI never constructs schedules, checks invariants, predicts a verdict, or launches a process. Existing replay and verification endpoints call the unchanged M2 services. Target profiles, `exec@1`, the supervisor, oracle, aggregation, preflight, DNA, replay and verification remain the trusted foundation.

## Presentation boundary

`web/` contains routes, view adapters, autoescaped Jinja templates, CSS and browser interactions. `records.py` reads investigation records and saves the exact JSON responses returned by replay/verification services, with an operation id, timestamp and target context. These records support result permalinks; they do not reinterpret outcomes.

`api_backend.install_backend_routes` owns investigation GET/POST, hypothesis/findings, reduction and differential APIs. Its shared record readers include both live investigations and the historical showcase. `api.create_app` retains operation detail and saved replay/verification results, and installs the web routes once. The web forms submit investigation, reduction, and differential requests to those existing routes. HTTP 202 creates a persisted operation; progress pages poll until its lifecycle terminates. The view keeps lifecycle and business verdict separate.

## Records and showcase

Runtime files live below `DURESS_HOME`: `investigations/`, `dna/`, `operations/`, `reductions/`, `differentials/`, `evidence/`, and `run/`. Writes use the existing atomic JSON writer. Record ids are constrained to simple identifiers and record readers refuse symlinks.

`showcase/investigation.json` is a presentation record assembled from the original M2 artifact and `docs/measurements/m2.json`. It carries an explicit recorded label, source references, the retained aggregate and representative observations. It does not pretend to retain a complete trial stream. Its historical verification comparison is attributed to the retained portability command output. `scripts/build_showcase.py` packages this existing evidence without running an experiment.

Bundled DNA is copied into an empty runtime store on first workspace access so existing replay/verification endpoints can use it. Existing runtime artifacts are not replaced. The original hashed reproducer is preserved.

## Frontend behavior

Links use normal browser navigation. Investigation fragments refresh through HTMX every two seconds; reduction and differential pages poll their API records with bounded retry and visible errors. Completed pages stop polling. Replay, portable-file replay and verification submit to existing JSON routes and display service-provided outcomes. Buttons are disabled only while requests are pending; API errors are shown alongside the action. Saved result links are created after a successful API response. A missing record produces an HTML error page for browser routes and JSON 404 for API routes.

Reports render stored records into self-contained HTML with the same presentation components. They start no execution.

## Deliberate limits

The store is a local filesystem, not a user database. Runtime records are ephemeral on Render. The existing single-operation gate and quotas remain in the API. Multi-user access control and durable hosted storage remain separate work. M3/M4 investigation and reduction run through the shared service layer, with bounded budgets and empirical, schedule-specific claims. Hosted model inference is unverified.

## Final web integration

New investigation records retain the actual normalized built schedule and control level beside each parameterized scenario. Presentation never recreates historical schedules from the current registry; older records explicitly report unavailable schedule details. Hypotheses remain proposals until recorded execution outcomes establish confirmation. Authored cached responses retain their provenance label.

`/evidence/{trial_id}` reads only the supervisor's retained `snapshot/app.db` copy, with read-only SQLite access and a fixed whitelist of business tables. It refuses missing/corrupt files and symlink components, exposes no arbitrary evidence path or target logs, and computes no new verdict. Passing trials normally have no retained snapshot. Trial validity and settlement remain part of the originating execution record.

The shared JSON reader now handles investigation, operation, reduction, and differential retrieval consistently: constrained identifiers, object roots, valid JSON/UTF-8, and no symlink records. No execution queue, database, or separate frontend engine was added.
