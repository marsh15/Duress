"""Trial evaluation and precedence (plan C.6 / P1)."""

from __future__ import annotations

from .expectations import (
    evaluate_effects,
    evaluate_evidence,
    evaluate_pass_conditions,
    response_semantics_mismatches,
)
from .invariants import (
    PackContext,
    evaluate_pack,
    primary_violation,
)
from .models import (
    Expectation,
    FailureSignature,
    Schedule,
    TableSnapshot,
    TrialObservation,
    TrialVerdict,
    Violation,
)


def _harness_reason(observation: TrialObservation) -> str | None:
    if observation.watchdog_fired:
        return "harness:watchdog"
    if observation.stub.stale_calls:
        return "harness:isolation"
    if not observation.instance_ok:
        return "harness:instance_mismatch"
    if not observation.baseline_ok:
        return "harness:baseline_mismatch"
    if observation.settlement == "unconfirmed":
        return "harness:unconfirmed"
    if observation.harness_errors:
        return f"harness:{observation.harness_errors[0]}"
    return None


def _fail(
    reasons: list[str],
    violations: list[Violation],
    misses: list[Violation],
    evidence_missing: list[str],
    pass_failures: list[str],
) -> TrialVerdict:
    primary = primary_violation(violations) or primary_violation(misses)
    signature = FailureSignature.from_violation(primary) if primary else None
    if signature:
        reasons.append(f"failure_signature:{signature.key}")
    return TrialVerdict(
        status="FAIL",
        reasons=reasons,
        signature=signature,
        violations=violations,
        expected_effect_misses=misses,
        evidence_missing=evidence_missing,
        pass_condition_failures=pass_failures,
    )


def evaluate_trial(
    observation: TrialObservation,
    *,
    schedule: Schedule,
    expectation: Expectation,
    roles: dict[str, str],
    baseline: TableSnapshot,
    seed_order_ids: set[str],
) -> TrialVerdict:
    # 1. Harness validity. A stale call, a wrong instance, an unconfirmed kill
    #    and the watchdog all make the observation unusable, whatever the state.
    reason = _harness_reason(observation)
    if reason:
        return TrialVerdict(status="INCONCLUSIVE", reasons=[reason])

    # 2. The target never became ready.
    if not observation.target_ready:
        return TrialVerdict(status="INCONCLUSIVE", reasons=["target_unavailable"])

    # 3. The state copy is unreadable or does not match the pack schema.
    if observation.snapshot is None:
        return TrialVerdict(status="INCONCLUSIVE", reasons=["state_unreadable"])
    if not observation.schema_ok:
        return TrialVerdict(status="INCONCLUSIVE", reasons=["schema_mismatch"])

    delivery_intents = {delivery.id: delivery.effect_intent for delivery in schedule.deliveries}
    delivery_payments = {
        delivery.id: roles.get(delivery.event.payment_role, delivery.event.payment_role)
        for delivery in schedule.deliveries
    }
    delivery_orders = {
        delivery.id: roles.get(delivery.event.order_role, delivery.event.order_role)
        for delivery in schedule.deliveries
    }
    context = PackContext(
        roles=roles,
        baseline=baseline,
        attempts=observation.attempts,
        delivery_intents=delivery_intents,
        delivery_payments=delivery_payments,
        delivery_orders=delivery_orders,
        seed_order_ids=seed_order_ids,
        footprint=set(expectation.footprint),
    )
    violations = evaluate_pack(context, observation.snapshot)
    misses = evaluate_effects(expectation, observation.snapshot, roles, baseline)
    evidence_missing = evaluate_evidence(expectation, schedule, observation.attempts)
    pass_failures = evaluate_pass_conditions(expectation, observation.attempts)
    mismatch = response_semantics_mismatches(observation.attempts, delivery_intents)

    # 4. A violation that an interrupted process cannot cause is attributable
    #    committed state; it FAILs even if the deadline was exceeded or the
    #    process had to be forced down.
    independent = [violation for violation in violations if violation.independent]
    if independent:
        return _fail(
            ["independent_violation"], violations, misses, evidence_missing, pass_failures
        )

    # 5. A deadline-truncated trial can never PASS.
    if observation.execution_deadline_exceeded:
        return TrialVerdict(
            status="INCONCLUSIVE",
            reasons=["trial_timeout"],
            violations=violations,
            expected_effect_misses=misses,
            evidence_missing=evidence_missing,
            pass_condition_failures=pass_failures,
        )

    # 6. Non-independent findings need a clean settlement.
    if observation.settlement in {"forced", "crashed", "drain_unverified"}:
        return TrialVerdict(
            status="INCONCLUSIVE",
            reasons=[f"settlement:{observation.settlement}"],
            violations=violations,
            expected_effect_misses=misses,
            evidence_missing=evidence_missing,
            pass_condition_failures=pass_failures,
        )

    # 7. Required evidence.
    if evidence_missing:
        return TrialVerdict(
            status="INCONCLUSIVE",
            reasons=[f"insufficient_evidence:{item}" for item in evidence_missing],
            violations=violations,
            expected_effect_misses=misses,
            evidence_missing=evidence_missing,
            pass_condition_failures=pass_failures,
        )

    # S8 names a failed control as INCONCLUSIVE rather than a scenario FAIL
    # (C.5): when the positive control never executed, the expected effect was
    # never given a chance to happen.
    control_failures = [
        item for item in pass_failures if item.startswith("control_failed_to_execute")
    ]
    if control_failures:
        return TrialVerdict(
            status="INCONCLUSIVE",
            reasons=control_failures,
            violations=violations,
            expected_effect_misses=misses,
            evidence_missing=evidence_missing,
            pass_condition_failures=pass_failures,
        )

    # 8. Any remaining violation or expected-effect miss.
    if violations or misses:
        return _fail(["violation"], violations, misses, evidence_missing, pass_failures)

    # 9. Declared pass conditions.
    if pass_failures:
        return TrialVerdict(
            status="INCONCLUSIVE",
            reasons=pass_failures,
            pass_condition_failures=pass_failures,
        )

    # 10.
    reasons = ["pass"]
    if mismatch:
        reasons.extend(f"response_semantics_mismatch:{item}" for item in mismatch)
    return TrialVerdict(status="PASS", reasons=reasons)