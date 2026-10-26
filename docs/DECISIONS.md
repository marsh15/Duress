# Decisions

Decisions are recorded here with the milestone that introduced them. Revision 4 of
`plan.md` is the frozen contract; this file records how M0 implements it and why.

## M0 — reference integration and concurrency reproduction

### D-M0-1 — Target launcher suppresses uvicorn's post-drain signal re-raise

**Status:** implemented.

**Context.** Target contract C.1 requires SIGTERM to stop accepting, finish
in-flight requests and exit 0. uvicorn ≥0.4x gracefully shuts down but then
re-raises the captured signal, so the OS reports exit status `-15`.

**Decision.** The reference target launches through `python -m razorpay_ref.serve`,
a thin `uvicorn.Server` subclass that keeps uvicorn's drain and omits the re-raise.
The harness never treats `-15` as success.

**Consequence.** The gate asserts exit 0 as written. Duress M1 must launch targets
through the profile's `serve` command (or reproduce this wrapper) rather than
`python -m uvicorn`.

### D-M0-2 — Variants are separate ASGI packages; the profile declares both

**Status:** implemented.

`razorpay_ref.vulnerable.app:app` and `razorpay_ref.fixed.app:app` each satisfy the
`<pkg>.app:app` contract with the variant as part of the package. `init-db` takes
`--variant`, and `profile.toml` records the init and serve commands for both. This
keeps the intentional defect confined to `vulnerable/handler.py` and prevents a
runtime flag from switching behaviour inside one code path.

### D-M0-3 — SQLite transaction discipline (rule R-TX)

**Status:** implemented.

One connection per request (`isolation_level=None`, `timeout=5`, closed in
`finally`), `foreign_keys=ON`, `busy_timeout=5000`, WAL set once at `init-db`.
No `await` occurs between `BEGIN` and `COMMIT`/`ROLLBACK` in either handler. The
provider call is the only await and sits outside every transaction.

### D-M0-4 — The vulnerable defect is preserved, not simulated

**Status:** implemented.

The vulnerable handler is a genuine read-then-write across an awaited provider
call, with no UNIQUE constraint on `fulfilments`. No sleeps, hooks or test-only
branches widen the window. The rendezvous is in the provider stub, which is the
controlled dependency named by the plan.

### D-M0-5 — Fixed handler: pre-check plus database guarantees

**Status:** implemented.

`BEGIN IMMEDIATE` holds the write lock across the fulfilment pre-check, the
conditional capture UPDATE, the fulfilment INSERT and the order UPDATE. The
UNIQUE constraints on `fulfilments.order_id` and `fulfilments.payment_id` remain
the guarantee for every path, including a second worker (tested in M1).

Integrity errors are classified by `sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"`
**and** the exact message for one of the two named constraints. Everything else
(the other UNIQUE tables, FK, CHECK, NOT NULL) rolls back and returns
`500 integrity_error`. The classifier is tested against real in-memory SQLite
errors, and the handler's 500 path is tested by raising a real CHECK error from
inside its transaction.

### D-M0-6 — M0 harness is independent of the engine

**Status:** implemented.

`tests/m0` contains no Duress engine code. Each trial allocates its own process
group, database, port, webhook secret and provider namespace. State is counted
only from a copy of `-wal`/`-shm` taken after `killpg(pgid, 0)` confirms the whole
group is gone. Readiness must return the matching `DURESS_INSTANCE`.

Valid trials require: instance-matched readiness, every attempt answered, exit 0
within 10 s, and a confirmed-gone group. Up to two replacements are allowed per
gate. Invalid trials are reported, never silently dropped.

### D-M0-7 — Provider stub is one process with per-trial namespaces

**Status:** implemented.

`tests/m0/stub.py` serves `/t/{trial}/v1/payments/{id}` from an in-process uvicorn
thread. Latency and a party-count rendezvous are per namespace; a retired
namespace answers 410 and increments `stale_calls`. This matches the plan's
isolation intent while keeping the stub small.

### D-M0-8 — Gate and measurements are separate, and evidence is generated

**Status:** implemented.

`scripts/m0_gate.sh` asserts exactly four collected gate tests (E1, E2[2/2],
E2[5/5], E3), runs the target suite and the gate suites, and only then runs E4–E6
as measurements. `scripts/m0_evidence.py` renders `docs/EVIDENCE.md` from
`docs/measurements/*.json` written by those runs. Missing measurements render as
"not run"; nothing is hand-entered.

### D-M0-9 — Explicitly out of scope

The invariant pack, oracle, DNA, scenario registry, investigator, UI, `naive_fix`
and reduction are M1–M6. M0 does not import or pre-build them.
## M1 — trusted execution, oracle and isolation

### D-M1-1 — Profiles declare the real launch command (F1)

**Status:** implemented and tested.

`profile.toml` now carries `init` and `serve` commands with `{db}`, `{seed}`,
`{host}`, `{port}` and `{workers}` placeholders. The supervisor formats and
executes that command. For the reference target it is
`python -m razorpay_ref.serve ...`, the launcher that suppresses uvicorn's
post-drain signal re-raise; no engine component invokes stock `uvicorn`.
`tests/integration/test_declared_launch.py` wraps the declared command in a
recorder and proves the supervisor ran it, exited 0, and settled cleanly.
`fixed_2w` declares two workers for E8.

### D-M1-2 — E1 asserts the linked payment (F2)

**Status:** implemented.

E1 now requires both duplicate fulfilment rows to reference `pay_A1`, the
delivery's payment, in addition to the count, party release and arrival checks.
`pay_A2` exists in the seed, so a row linked to the wrong payment can no longer
pass the gate.

### D-M1-3 — exec@1 is a document, not prose

**Status:** implemented.

`src/duress/semantics/exec_v1.json` is the versioned contract; its identity is
`exec@1#sha256(canonical spec)`. The runner implements ordered steps, a build
barrier before group release, planned attempt timeouts, retries with the same
body/event id and a regenerated signature, and a deadline that cancels and
records `cancelled_attempts` / `undispatched`. `tests/unit/test_exec_semantics.py`
has one conformance test per clause, including live stub latency and rendezvous
behaviour.

### D-M1-4 — Phased supervisor and settlement

**Status:** implemented.

P1–P6 have independent timeouts and a 65 s watchdog. A trial starts only if its
operation has the 65 s margin (the operation loop enforces this when a wall
deadline is supplied). `unconfirmed` kills leave the pidfile in place, return no
snapshot, and abort the operation; the CLI sweeps pidfiles before a run, and
`duress sweep` does it on demand. A mismatched recorded start time is never
killed, because the pid may have been reused. The supervisor refuses any host
other than `127.0.0.1`.

### D-M1-5 — Oracle precedence and the S8 control ordering

**Status:** implemented.

`evaluate_trial` follows C.6 steps 1–10. Independent violations FAIL even with
the deadline flag or a forced settlement. A deadline without an independent
violation is `trial_timeout`, never PASS. Required evidence precedes other
findings. One deliberate refinement: when S8's positive control did not execute
(`control_failed_to_execute`), the result is INCONCLUSIVE even though the
expected effect on B is also missing. That follows C.5's named S8 outcome; the
scenario FAIL would otherwise mask a control failure. `test_evaluate_trial`
covers this path and every other precedence step.

### D-M1-6 — Invariant pack is SQL plus explicit intent checks

**Status:** implemented.

Each member carries its SQL/flags, an evaluator-source hash and a canonical
definition hash. `payments-ref-v1` identity is the digest of the sorted member
identities. INV-5 joins the delivery's effect intent with the baseline and the
committed rows; every 2xx is checked, so a duplicate acknowledgement of a
first capture cannot pass. MP-1, MP-2 and MP-3 are fixture-tested at the
database level, and the matrix exercises them end to end.

### D-M1-7 — Stale calls are attributed to the trial window

**Status:** implemented.

The stub keeps per-namespace counters plus a global stale counter. Each trial
records the global value at construction and reports the delta at the end, so a
call to a retired namespace during the trial is `harness:isolation` even though
the namespace it touches belongs to an earlier trial.

### D-M1-8 — Fixture modes are controls, not targets

**Status:** implemented.

`targets/fixture_modes` implements the eight modes from C.1 using the vulnerable
schema. `hang_on_shutdown` ignores SIGTERM until SIGKILL, `dup_then_hang`
commits two fulfilments first, and `spawn_orphan` starts a child in the trial's
process group that writes only after its parent dies. These modes exist to prove
the oracle can see behaviour, not as examples of integrations.

### D-M1-9 — Not in M1

**Status:** deferred by plan.

S5/S6/S9 templates, Failure DNA, preflight, replay, verify-fix, reduction, the
investigator and the web UI are M2–M5. `EXHAUSTIVE_SET` is intentionally empty
until M2 adds the registry entries. The M1 lets the DL-3 test build an S6-shaped
schedule directly; it is not a registry template yet.

## M1 recovery — process termination is read from the process table

### D-M1-10 — `EPERM` from `killpg` is never evidence of termination

**Status:** implemented and tested.

**Context.** The first M1 verification run failed `test_wal_copy_after_sigkill_is_readable`
with `PermissionError` from `killpg`. The immediate fix was to treat `EPERM` as
"already exited", in both `TrialSupervisor._signal_group` and `group_exists`.
A direct probe of this host showed that assumption is wrong in the dangerous
direction: macOS returns `EPERM` from `killpg(pgid, 0)` while members are
zombies, and `killpg(pgid, SIGKILL)` has been observed to return `EPERM` even
though the signal landed. A zombie-only group is not reachable by a signal
probe, but a group with a live member can also report `EPERM` in the middle of
teardown.

**Decision.** Liveness is never inferred from a signal's return code.
`store.group_exists` reads the process table: `/proc` on Linux, `ps -o
pid=,pgid=,stat=` on macOS. A member counts as alive only when its state is not
`Z`; a zombie cannot run user code or write. `EPERM`/`ESRCH` from `killpg` are
only failed signal attempts. `force_kill_members` escalates to a direct
`os.kill(pid, SIGKILL)` for live members, which closes the case where a group
signal is refused while the target is alive; it refuses to signal Duress's own
process group. `_confirm_group_gone` waits, escalates, then waits again, and a
group that survives both stays `unconfirmed` — state is not read and the trial
does not become a verdict.

**Consequence.** The pidfile sweep no longer deletes a record on `EPERM`; a group
that survives escalation is reported as `unresolved` and its pidfile is kept so
the next sweep retries. `tests/unit/test_group_liveness.py` pins every case:
a refused signal over a live group, a zombie-only group, per-pid escalation,
own-group refusal, and a sweep that must not claim an undead group was killed.

### D-M1-11 — A flaky exec@1 retry test is fixed at its cause, not its margin

**Status:** implemented.

**Context.** `test_c4_retry_reuses_body_and_event_id_and_regenerates_signature`
failed once in a full verification run with `["timeout", "timeout"]`, then
passed repeatedly with the same parameters (10/10 on a quiet host). The test
designed its own sub-100 ms round-trip budget: a 100 ms planned attempt timeout
for a retry whose second attempt needed some of that budget for the socket
round trip. The subject of the test is retry semantics — same body, same event
id, fresh signature, backoff — not a scheduler-latency bound.

**Decision.** The first handler now sleeps 2 s against a 1000 ms planned
timeout, so the first attempt times out by design and the second attempt has a
1 s budget for an immediate response. No assertion was removed or weakened:
outcomes, backoff ordering, body equality and both signatures are still
checked. The timing-only margins in C2/C3 were widened for the same reason and
are documented in the test comments.

**Consequence.** The retry path is exercised with roughly an order of magnitude
more headroom, and a failure now means a real regression rather than a loaded
host. The historical interrupted measurement is superseded in EVIDENCE.md.

## M2 — Failure DNA, replay, verify-fix and the API

### D-M2-1 — The registry has scenario *specs*, not just template names

**Status:** implemented.

`EXHAUSTIVE_SET` must contain S3 twice (rendezvous and 200 ms latency) while
`TEMPLATES` contains each template once. `ScenarioSpec(template_id, params)`
carries both, and `scenario_id_for` produces a stable human-legible id
(`S3[copies=3,fault=rendezvous,latency_ms=200]`). The exhaustive fallback in M3
therefore iterates the same registry objects the investigator selects from, not
a parallel list.

### D-M2-2 — The DNA hashes the loaded mapping, not a re-serialised model

**Status:** implemented.

`DnaDocument.reproducer` is `dict[str, Any]`. If it were a typed model, checking
the digest after a Pydantic round trip would hash a normalised shape and could
either accept a mutation or reject an untouched artifact. Preflight therefore
parses the payload, checks `canonical_sha256(payload["reproducer"])` against the
stored digest, and only then validates the schedule and expectation into models
for execution.

**Consequence.** A mutation to any embedded field without recomputing the outer
digest is `tampered:digest`. `tampered:expectation` and
`tampered:invariant_pack` are the honest outcomes when the outer digest is
recomputed but an inner definition hash is stale, which is how the acceptance
tests reach those branches.

### D-M2-3 — The fixture control target declares the reference contract

**Status:** implemented.

The verify-fix truth table includes `reject_all` and `crash_on_boot`, which are
fixture modes. Preflight step 6 compares the artifact's `target_contract.profile`
with the runtime profile, so the fixture target would otherwise be rejected as
`incompatible:target_contract` before producing the intended outcome. The
fixture target implements the same schema and webhook contract as the reference
integration, so its profile id is `razorpay-ref`; its *target ids* stay
`fixture-*`, and README continues to say it is a control, not an integration.

### D-M2-4 — Candidate sizing uses the observed control rate

**Status:** implemented.

`n_c = max(20, ceil(ln 0.01 / ln(1 - p_a)))` with `p_a = min(p_hat, 0.99)`. The
direction matters and is easy to misread: a *high* observed control failure rate
makes a candidate failure easy to rule out (2 trials at p_a = 0.9), while a rare
failure needs many trials. If the formula needs more than 44 candidate trials,
the operation is INCONCLUSIVE `reproduction_too_unreliable` rather than a weak
VERIFIED. `tests/unit/test_verify_decisions.py` pins p_a = 0.1 -> 44 and
p_a = 0.09 -> too unreliable.

The candidate run stops at the first same-signature failure. A FAIL outcome
therefore takes precedence over an incomplete run, and verification does not
spend the remaining budget after the answer is known.

### D-M2-5 — Secret resolution for managed replay

**Status:** implemented, with one explicit interpretation.

The DNA stores `secret_ref` and never a secret. A managed trial lets Duress
provision the webhook secret: the supervisor generates one, injects it into the
target environment and signs with it. Absence of the environment variable is
therefore not an error in normal operation. Preflight accepts `env:NAME` and
returns no fixed value, meaning "provision at trial time"; an unknown scheme
always fails with `secret_unresolved`, and a caller that requires the environment
value (`secret_required=True`) also fails when it is unset. Both branches are
tested. A replay under a completely different environment secret still
reproduces, which is the property that matters: signatures are regenerated, not
stored.

### D-M2-6 — API admission is explicit, not incidental

**Status:** implemented.

Write routes require a token when `DURESS_DEMO_TOKEN` is set; without one the
service is a local development server and allows the caller. Admission is
ordered: token (401), rate limit (429), single-operation gate (409), then target
and artifact lookup (404). Unknown target ids and unknown DNA ids are refused;
there is no path or command parameter anywhere in the API. Request bodies are
capped at 1 MB. A refused admission is checked before the gate is acquired.

### D-M2-7 — The container's only public bind is the web service

**Status:** implemented.

The Dockerfile's default command is `duress serve --host 0.0.0.0 --port $PORT`.
The trial supervisor still refuses any managed target host other than
`127.0.0.1` (M1 test), and preflight's `init-db` runs with `127.0.0.1`. The
container smoke test observes the public API answering on `0.0.0.0` while the
trial targets remain loopback-only.

## Web application release

### D-WEB-1 — Presentation uses the existing execution boundary

The web application renders evidence and calls the existing replay and verification API. It adds read-only record endpoints and stores returned operation results for permalinks. It does not introduce another execution path. Autonomous investigation creation and reduction remain unavailable, with explicit pending states, because those backends are not part of this release.

### D-WEB-2 — A showcase retains the granularity of its source evidence

The M2 DNA and measurement output contain a characterization aggregate and representative observations, not a complete saved investigation stream. The showcase therefore displays those aggregates, delivery observations and a labelled historical comparison. It supplies no invented trial rows, hypothesis citations or model findings. The packaging script validates the artifact identity and checks that the retained verification output supports the comparison counts.

### D-WEB-3 — Local assets and autoescaped records

Jinja and vendored HTMX fit the frozen architecture. A small JavaScript file submits JSON to the existing API and creates text nodes for returned values. Fonts use the system stack. CSS, JavaScript and HTMX are local, so browsing recorded evidence has no external asset dependency. Jinja autoescaping and its JSON encoder protect presentation of record strings. Browser testing found insufficient muted-text contrast; the palette was corrected and is checked with vendored, test-only axe-core.

### D-WEB-4 — Saved results are observations, not another verdict model

An operation record retains the exact response from the existing service together with a timestamp, operation kind and target context. Result pages display that payload without recalculating PASS, FAIL or VERIFIED. These records share the runtime filesystem and its ephemeral-hosting limitation. Exported DNA remains the portable reproducer; an operation permalink is local to its workspace.

### D-WEB-5 — UI labels never become portable artifact fields

Final inspection caught a showcase label being attached to the DNA mapping used by the page. Although downloads remained unchanged, copying the displayed JSON could include an unsupported extra field. The label now lives exclusively in the view context. A regression compares embedded JSON with the actual download and validates the document schema. The reproducer and existing preflight logic were not changed.
