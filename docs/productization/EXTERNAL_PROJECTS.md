# External projects and local operation

Duress observes business effects under controlled delivery conditions. HTTP success alone is insufficient. External execution is local-first and requires an owned isolated application or an explicitly authorized staging destination. The hosted demonstration accepts neither external commands nor destination URLs.

## Install and first run

Build a wheel with `uv build --wheel`, then install that wheel into Python 3.12 (`python -m pip install /absolute/path/to/duress-0.2.0b1-py3-none-any.whl`). Add `[postgresql]` to the wheel requirement for PostgreSQL support. No checkout or current-directory resource assumption is required at runtime. A package index release has not been published.

```bash
mkdir owned-integration
cd owned-integration
duress init --project owned-integration
duress doctor --config duress.toml --json
duress targets --config duress.toml --json
duress investigate --config duress.toml --mode exhaustive --json
duress check --config duress.toml --output result.json --junit result.xml --artifacts artifacts/
duress register --config duress.toml
duress serve --host 127.0.0.1 --port 8000
```

The starter is a separate, runnable SQLite resource-activation service. `init` refuses existing files. Replace its application, manifest, event fixtures and contract with your own configuration. `doctor` validates configuration and read-only PostgreSQL connectivity; it never initializes an application, starts a process, or delivers an event. Managed readiness and per-trial tables are checked only after explicit execution. External investigations currently use registered deterministic scenarios; live/cached AI proposal selection remains supported for bundled payment targets only.

## Manifest v1

The generated starter and `examples/ticketing/` are executable examples. TOML is strict: unknown fields, kinds, versions and ambiguous references are errors. Paths are resolved relative to the manifest; `target.cwd` approves the application directory. Commands are explicit argv arrays with no shell interpolation. A first argument of `python` resolves to the interpreter running Duress. Install the application's dependencies into that environment or configure its interpreter explicitly.

```toml
[duress]
schema_version = 1
project = "my-project"
[target]
id = "my-local-target"
mode = "managed"
cwd = ".."
init = ["python", "-m", "myapp", "init"]
serve = ["python", "-m", "myapp", "serve"]
readiness_path = "/healthz"
max_processes = 8
memory_mb = 1024
isolated_test_database = true
[target.env]
APP_ENV = "test"
[target.env_refs]
OWNED_TEST_TOKEN = "MY_TEST_TOKEN"
[delivery]
adapter = "http-json@1"
endpoint = "/webhooks"
method = "POST"
signature = "hmac-sha256"
signature_header = "X-Signature"
event_id_header = "X-Event-Id"
[delivery.header_refs]
Authorization = "MY_AUTHORIZATION_HEADER"
[observation]
adapter = "postgresql@1"
database_ref = "OWNED_TEST_DATABASE"
# Optional restricted observation login; omitted means the dedicated test login
# is used in a transaction forced read-only by the observer.
observer_ref = "READ_ONLY_TEST_DATABASE"
[contract]
file = "contract.json"
[fixtures]
file = "seed.json"
events_file = "events.json"
[[scenarios]]
id = "duplicate-event"
deliveries = [{id="first", fixture="paid", event_id="e1"}, {id="again", fixture="paid", event_id="e1"}]
steps = [{deliveries=["first","again"]}]
require_overlap = true
expected_status = {first="2xx", again="2xx"}
# Context declares expected effects, required transitions and scope; see CONTRACT.md.
```

`project` is a lowercase local storage identifier. More than one project is supported; candidate projects may use a different project id while sharing identical contract/fixtures/delivery semantics. Managed targets have no configured URL: Duress chooses an ephemeral loopback port. `env` contains non-secret constants; `env_refs` resolves named environment variables. Reserved runtime variables cannot be overridden. Sensitive fields and literal authentication headers are refused; use references.

SQLite uses `adapter="sqlite@1"` and a fresh database file, with no DSN. PostgreSQL requires `isolated_test_database=true` and an explicit `database_ref`. Use a dedicated test database, never production. The application initializes a fresh `duress_<trial>` schema; the observer reads only declared tables/columns through a repeatable read-only transaction. A separate SELECT-only login is supported through `observer_ref`; grant it usage/select privileges on trial schemas during your trusted init operation. Schema cleanup uses only Duress-generated schema identifiers after confirmed process termination. A failed cleanup makes the trial inconclusive and retains a diagnostic.

The seed file is application-owned JSON. `events_file` independently supplies `{"events":{"name":{"payload":{...}}}}`; omit it only when the application fixture itself also contains that event mapping. An event may use `raw_json` instead of `payload` to preserve exact UTF-8 bytes, whitespace and newlines. Credential-like fixture fields, including inside raw JSON, are rejected before execution. `headers` contains literal non-secret HTTP headers; `header_refs` supplies complete secret header values. HMAC-SHA256 signs exact raw bytes with a new managed secret each trial. Signatures are regenerated for retries.

## Scenario and contract semantics

Each delivery has `id`, `fixture`, `event_id`, optional `attempt_timeout_ms`, `max_attempts`, and `invalid_signature`. Every delivery appears exactly once in `steps`; deliveries in one step share a release gate. Steps may declare `delay_before_ms`. `delivery_policy` declares `attempt_timeout_ms`, `max_attempts`, `retry_on` (`timeout`, `connect_error`, `response`, `cancelled`) and `backoff_ms`. Retry-on-response applies to any HTTP response. `expected_status` evaluates the **terminal received response** per delivery. Missing required responses remain inconclusive.

`require_overlap` requires measured request interval overlap for concurrent groups; it does not establish a server-side critical-section rendezvous. `require_retry` requires an actual second attempt. `settle_ms` is a bounded post-delivery wait (0–2000ms); only the subsequent verified process drain establishes managed settlement. Test-only application delay controls are declared fixture data and labelled separately from normal processing.

`acknowledgements` maps a **delivery id** to `{intent, entity, match}`. It consumes that delivery's actual responses, including responses before a retry. Another delivery's 2xx cannot supply its evidence. Effects are compared across the declared scenario's independently observed before/final state; the result does not claim causal attribution to one concurrent handler. `create` means an effect became present within this scenario, not one new write for every 2xx. `duplicate` requires an existing unchanged effect. A non-success acknowledgement makes no success-effect claim; expected-effect and response checks still run. Legacy `context.acknowledgement` requires `ack_delivery_id` when more than one delivery exists. See [CONTRACT.md](CONTRACT.md) for all seven invariant families and typed context.

Bounds preserve the existing execution envelope: 10 deliveries, groups of at most 10, at most three attempts and 10-second attempt timeouts, a 25-second planned execution budget and 30-second dispatch deadline, phased settlement and 65-second watchdog. An investigation has at most 130 trials. Observer rows, contract bytes/entities/invariants/columns and input files are bounded. Managed process groups have count and memory monitoring, CPU/file/descriptor limits, and Linux address-space limits. macOS memory enforcement is periodic RSS monitoring, not a kernel reservation; local process groups are supervision, not a security sandbox for malicious code.

## Staging

Staging declares `mode="staging"`, a `base_url` containing an exact origin, matching `allowed_origins`, and `staging_authorized=true`. It cannot declare init/serve commands. It requires independently observed PostgreSQL and local secret references for signing. Redirects are not followed. Staging authorization is a declaration by the local operator, not an inference of ownership.

The first staging adapter records before/final state and real delivery attempts, but deliberately returns INCONCLUSIVE because remote writer quiescence is unverified. A successful HTTP response is never treated as process termination. Do not interpret this workflow as verified managed execution or portable staging replay. Managed local targets provide the stronger acceptance path.

## Failure DNA, replay and verification

```bash
duress dna list --config duress.toml --json
duress dna export DNA_ID --config duress.toml --output failure.json
duress replay failure.json --config duress.toml --json
duress verify-fix failure.json --config flawed.toml --candidate-config fixed.toml --json
duress reduce failure.json --config flawed.toml --json
duress report INVESTIGATION_ID --config duress.toml --output report.html
```

`external-dna@1` is a separate additive artifact contract. Existing payment DNA is unchanged. External artifacts contain the full declarative contract, seed/event fixtures, exact event bytes, schedule, expectations, adapter versions, implementation digest, observed type-aware schema fingerprint, measured failure sample and failure signature. They contain no executable commands, working directories, live signing keys, database credentials or destination origins. The receiving operator provides an independently authorized local manifest. Changed contract, fixtures, signing/delivery definitions, adapter code or versions are refused before business delivery. The prepared baseline must match the observed schema fingerprint before any event is sent. Implementation identity intentionally requires the same release build; upgrading replay semantics requires explicit compatibility work rather than silent reinterpretation.

Replay uses fresh processes/state/credentials. Fix verification re-establishes the vulnerable signature, then uses the existing control-calibrated sample-size function (20–44 candidate trials, bounded total budget). A corrected candidate that exposes another violation is not verified. No result proves absence of all bugs. Reduction preserves contract, intent, schedules, required response conditions and concurrency; accepted changes receive independent replay revalidation. Original artifacts remain available. A no-removal result is valid.

## Records, recovery and privacy

External records default to `~/.local/share/duress/projects/PROJECT/`; `DURESS_HOME` selects a common alternative root. Project identity and integrity are checked at every load. Atomic writes use unique temporary names; corrupt/partial records do not hide intact history. CLI-only registration stores the approved manifest path locally. Source credentials are environment references, never portable values. Logs and whole databases are discarded after normalized allowlisted evidence collection. No external source code is submitted to a model by the deterministic workflow.

```bash
duress project-store --config duress.toml --action diagnostics
duress project-store --config duress.toml --action reconcile
duress project-store --config duress.toml --action cleanup --retain-days 30
# Add --apply to execute the displayed retention plan.
duress project-store --config duress.toml --action export --destination project-export.json
```

Reconciliation uses owner PID and process start time; it does not interrupt live work. `sweep` retains the existing bundled-workspace behavior. For external orphan recovery, use `duress project-store ... --action reconcile` plus the process-group sweep for that project's home as documented in STORAGE.md. Imports require an explicit source and destination and preserve project/integrity boundaries. Runtime project registration is not included in portable evidence exports.

The web workbench at `/projects` displays registered projects and real saved evidence. Registration/configuration remains a local CLI operation. Execution routes require loopback client/Host, same-origin browser requests, existing token authorization when configured, operation admission and quotas; hosted mode rejects execution. Local state reads also require loopback. This is a single-user local tool, not multi-tenant hosted SaaS. See [WEB.md](WEB.md), [STORAGE.md](STORAGE.md) and [CI.md](CI.md).
