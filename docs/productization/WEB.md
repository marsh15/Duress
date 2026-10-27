# External projects in the local web workbench

The external project web surface is installed with
`duress.external.web.install_external(app, settings, authorize, begin_operation, gate)`.
The main API app should install it before returning from `create_app`, passing
the same settings object, authorization dependency, operation admission
function, and gate used by the existing API routes.

## Local onboarding

Configuration and registration stay in the project directory and terminal:

```sh
duress init --project my-integration
# Edit duress.toml, the contract, and fixtures for the integration.
duress doctor --config duress.toml
duress register --config duress.toml
```

The workbench lists only IDs in `registered_projects()`. It loads each approved
manifest locally with `load_project()` and exposes only the project ID, target
ID, target mode, contract ID/version, adapter versions, scenario IDs, and
registration timestamps. It does not return a manifest path, command argv,
environment reference, credential, fixture payload, or caller-selected URL.
The browser cannot register a project or edit its configuration.

Open `/projects` to browse registered integrations. A project page offers a
read-only doctor check, a bounded investigation over all registered scenarios
or one selected scenario, persisted recent runs, Failure DNA download, replay,
reduction, and verification against another registered project. Run pages show
the declared invariant results, delivery attempts, and actual before/after
state snapshots. Static reports are generated from stored investigation
records; rendering a report never executes an integration.

## HTTP routes

| Method and route | Purpose |
| --- | --- |
| `GET /projects` | Registered project list |
| `GET /projects/{project}` | Contract, adapters, scenarios, recent runs, and artifacts |
| `GET /projects/{project}/investigations/{id}` | Recorded invariant and persisted-state evidence |
| `GET /projects/{project}/investigations/{id}/report` | Static HTML report from the saved record |
| `GET /api/projects` | Safe registered project list |
| `GET /api/projects/{project}` | Safe registered project details |
| `GET /api/projects/{project}/operations/{id}` | Persisted operation envelope and result |
| `GET /api/projects/{project}/investigations/{id}` | Persisted investigation envelope and evidence |
| `GET /api/projects/{project}/dna/{id}/download` | Download a locally stored external DNA artifact |
| `POST /api/projects/{project}/doctor` | Queue the read-only diagnostic |
| `POST /api/projects/{project}/investigations` | Queue an investigation; JSON may contain only `scenario_id` |
| `POST /api/projects/{project}/dna/{id}/replay` | Queue replay of local Failure DNA |
| `POST /api/projects/{project}/dna/{id}/verify` | Queue verification with a registered `candidate_project_id` |
| `POST /api/projects/{project}/dna/{id}/reduce` | Queue bounded reduction of local Failure DNA |

Accepted operations return HTTP 202 with `operation_id`, `poll_url`, and
`operation_url`. Polling returns a `ProjectStore` envelope whose payload moves
through `queued`, `running`, and a terminal status. The server holds the shared
operation gate for the task and applies a five-minute lifecycle bound. Failures
return a generic public error code; local configuration paths and exception
details are not exposed.

All external project pages and APIs require a loopback client and a loopback
or `localhost` Host. Every POST also requires an Origin with the same authority
as Host, and uses the app's existing `authorize` dependency and
`begin_operation` rate/gate admission. Execution is rejected when
`settings.hosted` is true or `DURESS_HOSTED` is enabled. These checks are in
addition to requiring a locally registered project and IDs already present in
its manifest or project store. HTTP requests do not accept filesystem paths,
commands, URLs, manifests, or raw DNA documents.

## Evidence limits

PASS reports the declared checks across valid bounded trials. FAIL reports an
observed declared invariant or response failure. INCONCLUSIVE covers missing,
invalid, unsettled, or incomplete evidence. These outcomes apply to the chosen
contract, scenarios, adapter behavior, and observations. They do not establish
universal application correctness. Staging mode retains its explicit
quiescence limitation in the trial evidence; a successful HTTP response alone
does not prove a business effect.
