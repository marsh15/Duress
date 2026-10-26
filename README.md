# Duress

Put your integrations under pressure. Keep the proof.

**Status: M0 complete. M1 has not started.** This repository currently contains
the M0 experimental foundation only: a synthetic payment-webhook reference
integration in two variants (vulnerable and corrected), a mock provider with
latency and rendezvous faults, an independent trial harness, and the M0
acceptance gate. No Duress engine, oracle, DNA or UI exists yet.

The approved architecture and milestone plan is `plan.md` (Revision 4, frozen).
Measured results are in `docs/EVIDENCE.md`; implementation decisions are in
`docs/DECISIONS.md`.

## Layout

```
targets/razorpay_ref/
  common/            schema, DB lifecycle, signing, provider client, events, responses, classifier
  vulnerable/        intentional non-atomic check-then-act (the object of study)
  fixed/             BEGIN IMMEDIATE transition + UNIQUE constraints + precise error mapping
  serve.py           target launcher that drains on SIGTERM and exits 0
  tests/             sequential controls and the integrity classifier
tests/m0/
  stub.py            provider stub: latency, party rendezvous, per-trial namespaces
  harness.py         process group, readiness, signed HTTP, SIGTERM, confirmed exit, SQL copy
  test_e1..e3        gate experiments
  test_e4..e6        measured experiments (not gates)
scripts/
  m0_gate.sh         the acceptance gate
  m0_evidence.py     records gate runs and renders docs/EVIDENCE.md
docs/measurements/   raw JSON written by the runs EVIDENCE.md is built from
```

## Run the gate

```bash
uv sync
scripts/m0_gate.sh                 # gate + measured experiments + EVIDENCE.md
scripts/m0_gate.sh --gates-only    # gate only
```

The script fails if the gate files do not collect exactly four tests, if the
target suite fails, if any gate experiment fails, or if pytest reports a
collection error (exit 4) or collects nothing (exit 5).

## Run it in a container

```bash
docker build -t duress:m0 .
docker run --rm -e GATE_LABEL=docker \
  -v "$PWD/docs/measurements:/app/docs/measurements" \
  duress:m0 scripts/m0_gate.sh --gates-only
```

## The reference target

The target implements contract v1 for M0:

```bash
PYTHONPATH=targets python -m razorpay_ref init-db \
  --db /tmp/app.db --seed targets/razorpay_ref/seed.json --variant fixed

PYTHONPATH=targets python -m razorpay_ref.serve --variant fixed --port 8001
```

Environment: `DATABASE_PATH`, `PROVIDER_BASE_URL`, `WEBHOOK_SECRET`,
`DURESS_INSTANCE`. Routes: `GET /healthz` and `POST /webhooks/razorpay`
(`X-Razorpay-Signature` over the raw body, `X-Razorpay-Event-Id`).

The vulnerable variant is intentionally defective. Do not reuse it outside the
benchmark.