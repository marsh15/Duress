# Failure DNA v1

A DNA is the portable form of a failure that actually happened. It is not a log
and not a test file: it is a canonical, content-addressed reproducer that another
Duress process can preflight, replay and verify without the investigation that
created it.

## Shape

`reproducer` contains everything that determines execution:

| Field | Why it is inside the hashed payload |
|---|---|
| `domain`, `target_contract` | the same artifact must not silently run against a different contract or worker count |
| `execution_semantics` | `exec@1` plus its digest; a runner with different timing semantics refuses it |
| `seed.roles` and `seed.tables` | role names and the exact baseline the oracle compares against |
| `provider_fixtures` | external truth: the stub's answer for each payment |
| `signing` | scheme, headers and `secret_ref` (never a secret) |
| `schedule` | events, deliveries, effect intents, steps, faults, retry policy |
| `expectation` | effects, footprint, required evidence, pass conditions, and its definition hash |
| `invariant_pack` | all six members with definitions and hashes, plus the pack digest |
| `failure_signature` | the check, kind and sorted roles that must recur |
| `release_condition`, `control_level`, `trial_policy` | how the failure was synchronised and how it must be re-run |

`reproducer_sha256` is `sha256(canonical(reproducer))`; `dna_id` is its first 16
hex characters. Canonical JSON sorts object keys, uses `,`/`:` separators and
ASCII escaping, so the same logical value always hashes the same way.

## Integrity versus authenticity

The digest detects alteration. It does not prove who created the artifact. A
caller that requires provenance must supply it out of band.

## Preflight

No target starts until every check passes. The first failure is the answer:

| # | Check | Rejection |
|---|---|---|
| 1 | schema version supported | `unsupported_version` / `invalid:document` |
| 2 | reproducer digest and id prefix | `tampered:digest` |
| 3 | embedded expectation hashes and equals the local registry entry | `tampered:expectation` / `incompatible:expectation` |
| 4 | every pack member hashes, the pack digest recomputes, the local pack has no missing or extra member, and the signature check is known | `tampered:invariant_pack` / `incompatible:invariant_pack` |
| 5 | execution semantics id, version and digest | `incompatible:execution_semantics` |
| 6 | profile, contract version, worker count, seed baseline, `init-db` and schema probe in a scratch directory | `incompatible:target_contract` / `schema_mismatch` |
| 7 | schedule validators: party counts, W <= 25 s, intents | `invalid:schedule` |
| 8 | `secret_ref` resolves (or the trial provisions the secret) | `secret_unresolved` |

Because step 2 hashes the whole reproducer, an edit to the expectation without
recomputing the outer digest is reported as `tampered:digest`. `tampered:expectation`
and `tampered:invariant_pack` are reached when the outer digest is honest but the
inner definition hash is stale.

## Replay

Replay starts a fresh process group, database, port, secret and provider
namespace for every trial, evaluates the **whole** invariant pack, and compares
the primary signature:

| Outcome | Meaning |
|---|---|
| `REPRODUCED` | at least one FAIL with the original signature |
| `NOT_REPRODUCED` | the policy's PASS quota was met |
| `DIFFERENT_FAILURE` | failures, none with the original signature |
| `INCONCLUSIVE` | neither: insufficient valid trials, deadline, or a harness condition |
| `REJECTED` | preflight refused it |

## verify-fix

The control run re-establishes the failure on the vulnerable target. `k` is the
number of same-signature failures and `v` the valid control trials; `p_hat = k/v`.
The candidate run is sized from the *observed* rate, not a claimed one:

`n_c = max(20, ceil(ln 0.01 / ln(1 - p_a)))`, with `p_a = min(p_hat, 0.99)`.

A high observed failure rate therefore needs fewer candidate trials; a rare
failure needs more than the 44-trial cap and is reported as
`reproduction_too_unreliable` instead of being verified on hope.

The result is `VERIFIED` only when the candidate produced no same-signature and
no other failure across the required valid trials. The statement printed with
the result is conditional and includes the independence assumption.

## Reduction (M4)

Reduction removes deliveries while retaining the original failure signature,
execution semantics, seed/business roles, fixtures, expectation and invariant
pack. It preserves the surviving delivery order, affected-role concurrency and
rendezvous release requirements. Each accepted removal needs three valid
same-signature, release-preserving failures; unknown evidence keeps the delivery.
The claim concerns admissible single-delivery removal under this predicate,
never global minimality.

A changed schedule produces a new digest and DNA ID. The artifact passes
preflight and independent replay revalidation (at least ten valid trials,
p >= 0.9, uniform original release) before becoming primary. Failed revalidation
returns INCONCLUSIVE and retains the original primary; the proposed artifact may
remain available for inspection. The required fault-free characterization of the
original schedule records `characterization.black_box_rate` outside its hashed
reproducer. Reduction metadata links original, candidate, primary and reduction
record IDs without changing the original identity.

See [M3/M4 backend](M3_M4.md) for the bounded algorithm and
[backend handoff](BACKEND_HANDOFF.md) for API/CLI schemas and retrieval.
