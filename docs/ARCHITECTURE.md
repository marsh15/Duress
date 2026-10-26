# Architecture

Duress is one Python application. FastAPI serves its JSON API, Jinja renders pages, HTMX refreshes record fragments, and a small local JavaScript file connects forms to existing JSON endpoints. Assets are bundled; browsing requires no CDN or model provider.

## Execution boundary

`services.py` owns the shared CLI/API execution path. The UI never constructs schedules, checks invariants, predicts a verdict, or launches a process. Existing replay and verification endpoints call the unchanged M2 services. Target profiles, `exec@1`, the supervisor, oracle, aggregation, preflight, DNA, replay and verification remain the trusted foundation.

## Presentation boundary

`web/` contains routes, view adapters, autoescaped Jinja templates, CSS and browser interactions. `records.py` reads investigation records and saves the exact JSON responses returned by replay/verification services, with an operation id, timestamp and target context. These records support result permalinks; they do not reinterpret outcomes.

The ordinary API additions are read-only investigation history/detail and operation detail. No investigation creation or reduction endpoint is added. The web controls explicitly describe those capabilities as pending.

## Records and showcase

Runtime files live below `DURESS_HOME`: `investigations/`, `dna/`, `operations/`, `evidence/`, and `run/`. Writes use the existing atomic JSON writer. Record ids are constrained to simple identifiers and record readers refuse symlinks.

`showcase/investigation.json` is a presentation record assembled from the original M2 artifact and `docs/measurements/m2.json`. It carries an explicit recorded label, source references, the retained aggregate and representative observations. It does not pretend to retain a complete trial stream. Its historical verification comparison is attributed to the retained portability command output. `scripts/build_showcase.py` packages this existing evidence without running an experiment.

Bundled DNA is copied into an empty runtime store on first workspace access so existing replay/verification endpoints can use it. Existing runtime artifacts are not replaced. The original hashed reproducer is preserved.

## Frontend behavior

Links use normal browser navigation. Replay, portable-file replay and verification submit to existing JSON routes and display service-provided outcomes. Buttons are disabled only while requests are pending; API errors are shown alongside the action. Saved result links are created after a successful API response. A missing record produces an HTML error page for browser routes and JSON 404 for API routes.

Reports render stored records into self-contained HTML with the same presentation components. They start no execution.

## Deliberate limits

The store is a local filesystem, not a user database. Runtime records are ephemeral on Render. The existing single-operation gate and quotas remain in the API. Multi-user access control, durable hosted storage, autonomous investigation and reduction are separate work.
