# M3/M4 backend handoff

The backend branch is `feat/investigator-reduction`, based on `d08409c`.
`api.create_app()` installs `api_backend.install_backend_routes()` exactly once,
passing the existing workspace factory, admission functions and operation gate.
CLI and HTTP operations call `services.py`; neither owns an independent engine.
All targets are project-owned synthetic integrations, launched on loopback in
fresh isolated process groups. This opening section records the original backend handoff; the final integration below documents the completed web controls.

## Routes

| Method | Route | Contract |
|---|---|---|
| GET | `/api/health` | status, version, hosted, token_required, busy |
| GET | `/api/targets` | `{targets: [{target_id, profile, variant, contract_version, workers}]}` |
| GET | `/api/dna` | `{items: [...]}` DNA index |
| GET | `/api/dna/{dna_id}` | Complete Failure DNA v1 document |
| GET | `/api/operations/{operation_id}` | Saved replay/verify service payload and presentation metadata |
| POST | `/api/dna/{dna_id}/replay` | `{target_id?: str, policy?: "replay"\|"explore"}`; synchronous `{outcome: ReplayOutcome, preflight}` |
| POST | `/api/dna/replay` | `{dna: object, target_id?: str}`; synchronous submitted-artifact replay |
| POST | `/api/dna/{dna_id}/verify-fix` | `{vulnerable_target_id?: str, candidate_target_id?: str}`; synchronous VerifyOutcome |
| POST | `/api/investigations` | InvestigationRequest; 202 InvestigationAccepted |
| GET | `/api/investigations` | `{items: [InvestigationSummary], modes: ["live", "cached", "exhaustive"], creation_available: true}` |
| GET | `/api/investigations/{investigation_id}` | Full persisted InvestigationRecord |
| GET | `/api/investigations/{investigation_id}/hypotheses` | `{investigation_id, status, mode_effective, fallback_reason, usable, rejected, hypotheses}` |
| GET | `/api/investigations/{investigation_id}/findings` | `{investigation_id, status, verdict, findings, dna_ids, reductions, explanation, explanation_source}` |
| POST | `/api/dna/{dna_id}/reduce` | ReduceRequest; 202 ReductionAccepted |
| GET | `/api/reductions/{reduction_id}` | Full persisted reduction record |
| POST | `/api/differential` | DifferentialRequest; 202 DifferentialAccepted |
| GET | `/api/differential/{differential_id}` | Full persisted differential record |

New asynchronous endpoints return an ID and relative `status_url`; poll it until
`status` is `completed` or `failed`. HTTP 202 means admitted, not successful.
Unknown records/targets return 404, invalid bodies 422, a busy operation 409,
and exhausted admission quota 429. Authorized write routes accept
`X-Duress-Token` or `Authorization: Bearer ...` when `DURESS_DEMO_TOKEN` is set.
Anonymous investigation starts are restricted to cached mode; local operation
without a configured token permits every mode. Rate limits and the shared gate
also apply to anonymous cached starts. M2 replay/verify routes retain their
synchronous response contract; do not silently convert them to operation IDs.

## Request and record schemas

`InvestigationRequest` is `{target_id: str, mode: "live"|"cached"|"exhaustive"
= "cached", reduce: bool = false}`. `InvestigationAccepted` contains
`{investigation_id, status: "queued", mode_requested, target_id, status_url}`.
The full record contains timestamps, target, `mode_requested`, `mode_effective`,
`fallback_reason`, `fallback_detail`, model call/budget/provenance information,
source bundle digest/files/bytes, hypotheses, selected scenarios, executed
scenario IDs, findings, DNA IDs, reductions, explanation, verdict, trial/wall
budgets, events and errors. List summaries contain ID/type, timestamps, status,
phase, target, modes, fallback reason, verdict, DNA IDs and trials used.

Investigation lifecycle: `queued`, `running`, `completed`, `failed`. `phase`
tracks `queued`, `start`, `model`, `fallback`, `hypotheses`, `explore`,
`characterize`, `reduce`, `done`. Business verdict is separately
`PASS`, `FAIL`, `INCONCLUSIVE`, or null until resolved. A completed operation can
have an inconclusive verdict or a characterization error; inspect the data.

Each hypothesis contains `draft` (ID, title, mechanism/kind, citations,
predicted check/kind/role, scenario picks), `source: "model"`, `usable`,
`scenario_ids`, normalized `scenario_specs` and `status`. Citation/linkage
statuses are `valid|invalid`; structure is `pass|fail|not_applicable`; outcome is
`not_run|confirmed|refuted|different_failure|inconclusive`. Details and validation
errors are retained. Only `confirmed` hypotheses are model findings. Authored
cached responses carry `model.provenance.source: "authored_fixture"`; they are
not hosted inference evidence. Findings independently report executed failure
signatures, counts, linked hypothesis IDs, DNA ID/error and a data-only explanation.

Scenario entries contain normalized `scenario_id`, `template_id`, `params`,
`selected_by: control|hypothesis|exhaustive`, lifecycle status, aggregate result,
and trial rows. Aggregates include status, valid/fail/inconclusive/trial counts,
primary signature, signature counts, reasons and `budget_exhausted`. Trial rows
include trial ID, status/reasons/signature, settlement, release and duration.
Do not join hypotheses/results on template names alone: S3 has multiple
parameterized IDs. Experiments omitted by selection or a cap cannot refute a
hypothesis.

`ReduceRequest` is `{target_id?: str, black_box: true = true}`. Fault-free
characterization is required by C.11; explicitly disabling it returns 422.
`ReductionAccepted` contains `{reduction_id, status: "queued", dna_id,
target_id, status_url}`. The completed record contains `record_type`, ID,
status, target/time, `outcome`, `reason`, `original_dna_id`, `primary_dna_id`,
`reduced_dna_id`, `claim`, `result`, `reduced_document`, `revalidation`,
`black_box`, `preflight` and `summary`. Outcome vocabulary is
`REDUCED|NOT_REDUCED|SKIPPED|REJECTED|INCONCLUSIVE`; result/search statuses use
lowercase `reduced|not_reduced|no_admissible_reductions|skipped`.
Candidate records contain index, removed delivery, remaining deliveries,
admissibility/reasons, verdict `accept|reject|unknown|inadmissible`, trials,
valid/fail/passed counts, signature counts and release counts. Use
`primary_dna_id` for the retained reproducer; inspect revalidation before
promoting a reduced artifact. No global minimality is claimed.

`DifferentialRequest` is `{target_ids?: [str] (1–5), scenarios?:
[{template_id, params}] (1–7), policy: "explore"|"matrix" = "explore"}`.
Omitted lists select vulnerable/naive_fix/fixed and S3-rendezvous/S5/S9.
`DifferentialAccepted` contains `{differential_id, status: "queued", status_url}`.
The final record contains status, targets, scenarios, cells, policy and summary.
Each cell exposes expected and observed verdicts, `agrees: true|false|null`,
signature, counts and reasons. INCONCLUSIVE is not agreement. Summary contains
cells, compared, agree/disagree/inconclusive counts and unexpected-cell details.

## Persistence and retrieval

`DURESS_HOME` selects storage (otherwise `.duress` under the repository).
Atomic JSON writes go to `investigations/{id}.json`, `reductions/{id}.json`,
`differentials/{id}.json`, and `dna/{content_id}.json`. Investigation records
are updated during execution, enabling polling without in-memory session state.
Evidence remains under `evidence/{trial_id}` for failed/inconclusive trials;
pidfiles stay in `run` until the process group is confirmed gone.

Reduction records link original and reduced DNA; the original's unhashed
`reduction` metadata points to the reduction ID and primary artifact. The
hashed reproducer is never edited in place. Reduced DNA embeds the complete
reproducer and independently passes preflight/replay. Read routes retrieve
persisted data; background tasks and the admission gate belong to one API
process. Graceful shutdown cancels owned tasks, awaits cleanup and records cancellation
as failed. Startup marks stale queued/running records failed with
`interrupted_by_restart`; it does not resume them. Ephemeral hosted storage
disappears on redeploy. Use one API worker for this gate contract.

## Examples

```http
POST /api/investigations
Content-Type: application/json

{"target_id":"razorpay-vulnerable","mode":"cached","reduce":false}
```

```json
{"investigation_id":"inv-20261026T150000-a1b2c3","status":"queued","mode_requested":"cached","target_id":"razorpay-vulnerable","status_url":"/api/investigations/inv-20261026T150000-a1b2c3"}
```

Poll the returned `status_url`. Example final record excerpt (counts are
illustrative; consume actual persisted values):

```json
{"status":"completed","mode_effective":"cached","fallback_reason":null,"verdict":"FAIL","trials_used":30,"dna_ids":["0edb63c480dbc29b"],"model":{"provenance":{"source":"authored_fixture"}}}
```

```http
POST /api/dna/0edb63c480dbc29b/reduce
Content-Type: application/json
X-Duress-Token: <configured-token>

{"target_id":"razorpay-vulnerable","black_box":true}
```

```json
{"reduction_id":"red-a1b2c3d4e5f6","status":"queued","dna_id":"0edb63c480dbc29b","target_id":"razorpay-vulnerable","status_url":"/api/reductions/red-a1b2c3d4e5f6"}
```

```http
POST /api/differential
Content-Type: application/json
X-Duress-Token: <configured-token>

{"target_ids":["razorpay-vulnerable","razorpay-naive-fix","razorpay-fixed"],"policy":"explore"}
```

CLI equivalents: `duress investigate razorpay-vulnerable --mode cached --json`,
`duress reduce <dna-id> --target razorpay-vulnerable --json`,
`duress differential --policy explore --json`. Investigation exit codes are
0 PASS, 1 FAIL, 2 INCONCLUSIVE/failed; reduction is 0 REDUCED/NOT_REDUCED,
2 SKIPPED/INCONCLUSIVE, 4 REJECTED; usage errors are 3. Differential exit codes
are 1 for disagreements, 2 for inconclusive cells without a disagreement, and
0 when all compared cells agree and none is inconclusive.

## Resolved frontend integration

On explicit user request, upstream main `b30edc4` was merged into the backend
feature branch in `Duress-core`. The separate primary checkout was not changed.
The five conflicting files retained both intents and historical evidence.

There is one `create_app`, workspace factory, rate limiter, operation gate and
`install_backend_routes` call. Backend routes own investigation list/start/detail,
including GET; the duplicate upstream GET handlers were removed. Shared readers
in `records.py` provide local records and historical showcase fallback without
masking an invalid or symlinked local record. The index includes both record
sources and advertises `creation_available: true`. The existing web adapter
reads M3 nested hypotheses/citations/statuses and scenario trial rows, while
retaining legacy flat records and the historical M2 showcase. No new web start
or reduction controls were added.

Upstream page/static/report routes and saved operation lookups remain installed.
Replay and verify responses preserve their previous outcome/preflight fields,
with the upstream additive `operation_id` for result permalinks. Operation IDs
remain distinct from investigation/reduction IDs. The backend lifespan still
owns cancellation cleanup and restart reconciliation. Focused merge tests check
route uniqueness, shared admission, showcase browsing, saved payload equality,
and rendering of both legacy and current records. Combined verification results
are recorded in `IMPLEMENTATION_LOG.md`.

## Integration prerequisites and limits

Use Python 3.12 and `uv sync --python 3.12`. The launcher, registry/seed files,
scenario library, provider stub, invariant pack, DNA/preflight/replay services
and writable storage must accompany the API. `naive_fix` is an intentional
insufficient repair: S3 same-event PASS, S5 cross-event FAIL, S9 PASS.

Live provider settings are `DURESS_LLM_BASE_URL`, `DURESS_LLM_MODEL`, optional
`DURESS_LLM_API_KEY`. The client uses OpenAI-compatible `/chat/completions`
JSON mode with bounded calls/tokens/time and at most one output repair. Cached
mode performs no provider HTTP; missing/stale cache entries use the seven
real exhaustive scenarios. Committed fixtures are explicitly authored, keyed
by model/prompt; regenerate with `uv run python scripts/build_llm_fixture.py`
after relevant source/prompt edits. Genuine hosted inference and the five-run
live-versus-exhaustive experiment have not been verified; controlled HTTP
fixtures verify client behavior. See `M3_M4.md` and `IMPLEMENTATION_LOG.md` for
executed acceptance evidence and remaining deployment limitations.

## Final product integration

The integrated Jinja/HTMX application now exposes investigation creation and monitoring, source-grounded hypotheses and actual findings, reduction, and differential comparison. Browser forms use the API contracts above and the existing admission gate. Replay/verification remain synchronous saved operations. No backend execution logic was duplicated.

Browser routes added: `/reductions/{id}`, `/differential`, `/differential/{id}`, and read-only retained `/evidence/{trial_id}` business snapshots. Investigation pages retain the existing progress fragment. New scenario records include the exact normalized `schedule` and `control_level` built for execution; older records are not retroactively reconstructed. Reduction and differential readers now use the same corruption/symlink boundary as other records.

Live availability in the creation page means server base URL/model configuration is present, not that provider authentication or hosted inference has been verified. Authored fixture provenance remains visible. Full release observations are recorded separately in `EVIDENCE.md` and `FINAL_RUNTIME.md`.
