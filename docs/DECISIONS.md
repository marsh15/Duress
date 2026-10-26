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