# Independent PostgreSQL reference application

The service is developed and versioned in a separate local Git repository, `Duress-ticketing`, outside Duress's source tree. It imports no Duress code. `examples/ticketing.bundle` transports its actual Git history without creating an additional public repository. This is AI-assisted development by a delegated implementation agent, followed by integration and lifecycle corrections by the primary agent; it is not evidence of an unrelated human author's work.

```bash
git clone /absolute/path/to/Duress/examples/ticketing.bundle /absolute/path/to/ticketing
python -m pip install /absolute/path/to/ticketing
python /absolute/path/to/Duress/scripts/setup_ticketing_example.py /absolute/path/to/ticketing
# Resolve DURESS_TICKETING_DB privately to an owned local database ending _test.
duress doctor --config /absolute/path/to/ticketing/duress/flawed.toml --json
duress check --config /absolute/path/to/ticketing/duress/flawed.toml --scenario concurrent-duplicate --output failed.json --junit failed.xml --artifacts artifacts/
# Expected exit 1, actual observed duplicate tickets.
duress check --config /absolute/path/to/ticketing/duress/fixed.toml --output fixed.json --junit fixed.xml
# Expected exit 0 if all configured bounded trials pass.
```

The application owns its package, PostgreSQL schema, transaction logic, HMAC webhook endpoint, Docker/Compose setup, fixtures and tests. `normal` and `fixed` use row locks, transactions and uniqueness. `flawed` contains a deliberately non-atomic check/await/write and lacks the protective booking uniqueness. The 35ms pause is an explicit test variant defect; reports must not portray it as an accidental production incident. Optional bounded delay controls are labelled fixture data.

This directory contains configuration only: independent seed data, event payloads, a contract and schedules. The setup script copies them into the external application's `duress/` directory and writes local TOML manifests. The same contract evaluates normal, flawed and fixed implementations. No ticketing table name or business identifier occurs in the generic engine.

The nine scenarios cover normal processing, sequential duplicates, three-way concurrent duplicates, distinct events for one booking, retry after ambiguous acknowledgement, delayed processing, out-of-order cancellation/payment, invalid amounts and invalid signatures. Legitimate acknowledged duplicates require one total business effect, not one write per 2xx. PostgreSQL observers check bookings, ticket ownership/value/linkage and event capacity.

Generated schemas are unique per trial and removed only after process-table-confirmed termination and normalized evidence collection. Use a dedicated test database and, where available, separate SELECT-only observation credentials through `observer_ref`. Never point these commands at production. The independent application's own guard requires an owned local test database and explicit `DURESS_ALLOW_TEST_DATABASE=1`, supplied by the trusted example manifest.

`scripts/ci_ticketing.py` runs installed-wheel controls and preserves actual outcomes. [Execution evidence](../../docs/productization/EVIDENCE.md) records measured results and development corrections. The Git bundle is the delivery mechanism for this reference fixture; no additional remote repository has been published.
