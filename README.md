# Duress

Put your integrations under pressure. Keep the proof.

**Status: M1 complete.** The repository contains the M0 experimental foundation
and the M1 execution engine: a versioned `exec@1` runner, a phased process
supervisor with confirmed termination, an isolated provider stub, the
`payments-ref-v1` invariant pack, trial evaluation with the plan's precedence,
aggregation, scenarios S1/S2/S3/S8, a fixture-mode control target, and the
`duress run` / `duress sweep` CLI.

The investigator, S5/S6/S9, Failure DNA, replay, verify-fix, reduction and the
web UI are M2–M5 and are **not** implemented yet. Nothing in this README claims
them.

The approved architecture and milestone plan is `plan.md` (Revision 4, frozen).
Measured results are in `docs/EVIDENCE.md`; implementation decisions are in
`docs/DECISIONS.md`.

## Layout

```
src/duress/
  limits.py          every C.14 derived total in one place
  models.py          schedules, attempts, observations, verdicts, signatures
  semantics/         exec_v1.json and its canonical identity
  runner.py          exec@1: ordered steps, group gate, retries, deadline
  target.py          phased supervisor P1–P6, loopback guard, pidfiles
  provider_stub.py   fixtures, latency, rendezvous, per-trial namespaces
  invariants/        payments-ref-v1, six members with hashed definitions
  expectations.py    effects, evidence and pass-condition vocabulary
  oracle.py          evaluate_trial, C.6 precedence
  aggregation.py     FAIL / PASS quota / INCONCLUSIVE
  scenarios.py       S1, S2, S3, S8
  operations.py      trial loop, evidence retention
  store.py           JSON writes, pidfile sweep
  cli.py             targets, run, sweep
targets/razorpay_ref/  vulnerable, fixed, fixed_2w, profile-declared launch commands
targets/fixture_modes/ eight oracle controls (no_sig_check, ack_without_effect, ...)
tests/m0/              independent M0 harness and E1–E6
tests/unit/            exec@1, limits, invariants, oracle, aggregation, phases
tests/integration/     deadline, isolation, matrix, controls, E8, M0 agreement
scripts/               m0_gate.sh, m0_evidence.py, m1_evidence.py
```

## Run the tests

```bash
uv sync
uv run pytest -q -m "not slow"        # fast suite (141 tests at the M1 checkpoint)
scripts/m0_gate.sh                    # M0 gate + measured experiments + EVIDENCE.md
scripts/m0_gate.sh --gates-only       # M0 gate only
uv run python scripts/m1_evidence.py  # run the fast suite and record M1 evidence
```

The M0 gate fails if its files do not collect exactly four tests, if the target
suite fails, if a gate experiment fails, or if pytest exits 4/5.

## Use the engine

```bash
uv run duress targets
uv run duress run razorpay-fixed --scenario S3 --copies 3 --fault rendezvous
uv run duress run razorpay-vulnerable --scenario S3 --copies 3 --fault rendezvous
uv run duress run fixture-ack_without_effect --scenario S1 --json
uv run duress sweep
```

Exit codes follow C.13: `0` PASS, `1` FAIL, `2` INCONCLUSIVE, `3` usage.
`--json` prints the full `ScenarioResult`, including every trial observation.

Each engine trial gets its own temporary directory, database, port, webhook
secret, provider namespace and instance id. The target is spawned in a new
session and process group with the command declared in its `profile.toml`
(`razorpay_ref.serve` for the reference target, never stock `uvicorn`). State is
read from a copy of `db`/`-wal`/`-shm` only after `killpg(pgid, 0)` confirms the
group is gone. A wrong health instance, a stale provider call, or an
unconfirmed kill makes the trial INCONCLUSIVE and aborts the operation.

## The reference target

```bash
PYTHONPATH=targets python -m razorpay_ref init-db \
  --db /tmp/app.db --seed targets/razorpay_ref/seed.json --variant fixed

PYTHONPATH=targets python -m razorpay_ref.serve --variant fixed --port 8001
```

Environment: `DATABASE_PATH`, `PROVIDER_BASE_URL`, `WEBHOOK_SECRET`,
`DURESS_INSTANCE`. Routes: `GET /healthz` and `POST /webhooks/razorpay`
(`X-Razorpay-Signature` over the raw body, `X-Razorpay-Event-Id`).

The vulnerable variant is intentionally defective. Do not reuse it outside the
benchmark. The fixture-mode target exists only to exercise the oracle's positive
controls and is not a realistic integration.

## Scope and honesty

- PASS and FAIL are bounded empirical statements about the trials that ran, not
  proofs.
- The reference defect is a seeded benchmark. E5 measured a high black-box rate
  for this specific handler; it is not a general prediction.
- Duress stores JSON files and uses SQLite for target state.
- Managed targets bind `127.0.0.1` only. There is no external-target support.
- The container currently runs the M0 gate; the M6 release step will add the
  hosted web service.