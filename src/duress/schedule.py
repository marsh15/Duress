"""Schedule validation: C.7 validators, party counts and the 25 s worst case."""

from __future__ import annotations

from . import limits as limits_module
from .limits import Limits
from .models import DeliverySpec, RendezvousFault, Schedule, Step


def delivery_worst_case_ms(schedule: Schedule, delivery: DeliverySpec) -> int:
    timeout, attempts, backoff = schedule.resolve_attempts(delivery)
    return timeout * attempts + sum(backoff)


def planned_worst_case_ms(schedule: Schedule) -> int:
    """C.2.1 clause 5, exactly as written in the frozen plan."""
    by_id = {delivery.id: delivery for delivery in schedule.deliveries}
    total = 0
    for step in schedule.steps:
        worst = max(
            (
                delivery_worst_case_ms(schedule, by_id[delivery_id])
                for delivery_id in step.deliveries
                if delivery_id in by_id
            ),
            default=0,
        )
        total += step.delay_before_ms + worst
    return total


def _matching_deliveries(
    schedule: Schedule,
    step: Step,
    payments: list[str] | None,
) -> list[DeliverySpec]:
    by_id = {delivery.id: delivery for delivery in schedule.deliveries}
    wanted = set(payments) if payments is not None else None
    matched = []
    for delivery_id in step.deliveries:
        delivery = by_id[delivery_id]
        if wanted is None or delivery.event.payment_role in wanted:
            matched.append(delivery)
    return matched


def validate_schedule(schedule: Schedule, limits: Limits | None = None) -> list[str]:
    limits = limits or limits_module.DEFAULT_LIMITS
    errors: list[str] = []

    if len(schedule.deliveries) > limits_module.MAX_DELIVERIES:
        errors.append(
            f"deliveries={len(schedule.deliveries)} exceeds {limits_module.MAX_DELIVERIES}"
        )

    ids = [delivery.id for delivery in schedule.deliveries]
    if len(set(ids)) != len(ids):
        errors.append("duplicate delivery id")

    seen: list[str] = []
    for index, step in enumerate(schedule.steps):
        if len(step.deliveries) > limits_module.MAX_GROUP_SIZE:
            errors.append(
                f"step {index} group={len(step.deliveries)} exceeds {limits_module.MAX_GROUP_SIZE}"
            )
        for delivery_id in step.deliveries:
            if delivery_id not in ids:
                errors.append(f"step {index} references unknown delivery {delivery_id}")
            seen.append(delivery_id)
        for delivery_id in step.deliveries:
            delivery = next((item for item in schedule.deliveries if item.id == delivery_id), None)
            if delivery is None:
                continue
            timeout, attempts, _ = schedule.resolve_attempts(delivery)
            if attempts > limits_module.MAX_ATTEMPTS_PER_DELIVERY:
                errors.append(
                    f"delivery {delivery_id} attempts={attempts} exceeds "
                    f"{limits_module.MAX_ATTEMPTS_PER_DELIVERY}"
                )
            if timeout > limits_module.MAX_ATTEMPT_TIMEOUT_MS:
                errors.append(
                    f"delivery {delivery_id} attempt_timeout_ms={timeout} exceeds "
                    f"{limits_module.MAX_ATTEMPT_TIMEOUT_MS}"
                )

    if sorted(seen) != sorted(ids):
        missing = sorted(set(ids) - set(seen))
        repeated = sorted({item for item in seen if seen.count(item) > 1})
        if missing:
            errors.append(f"unscheduled deliveries: {', '.join(missing)}")
        if repeated:
            errors.append(f"deliveries scheduled more than once: {', '.join(repeated)}")

    worst = planned_worst_case_ms(schedule)
    if worst > limits.planned_worst_case_ms:
        errors.append(
            f"planned worst case {worst} ms exceeds {limits.planned_worst_case_ms} ms"
        )

    for fault in schedule.faults:
        if not isinstance(fault, RendezvousFault):
            continue
        matched_any = False
        for index, step in enumerate(schedule.steps):
            matched = _matching_deliveries(schedule, step, fault.payments)
            if not matched:
                continue
            matched_any = True
            if len(matched) != fault.parties:
                errors.append(
                    f"step {index} rendezvous parties={fault.parties} but "
                    f"{len(matched)} matching deliveries"
                )
        if not matched_any:
            errors.append("rendezvous fault matches no deliveries")

    return errors


def assert_valid_schedule(schedule: Schedule, limits: Limits | None = None) -> Schedule:
    errors = validate_schedule(schedule, limits)
    if errors:
        raise ValueError("invalid schedule: " + "; ".join(errors))
    return schedule