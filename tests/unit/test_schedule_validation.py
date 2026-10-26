"""C.7 validators: party count, W <= 25 s and effect intents."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from duress.limits import DEFAULT_LIMITS
from duress.models import (
    AttemptPolicy,
    DeliverySpec,
    EventSpec,
    LatencyFault,
    RendezvousFault,
    Schedule,
    Step,
)
from duress.schedule import planned_worst_case_ms, validate_schedule

EVENT = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A1")


def delivery(delivery_id: str, **kwargs) -> DeliverySpec:
    data = {
        "id": delivery_id,
        "event": EVENT,
        "effect_intent": "capture_and_fulfil",
        "event_id": f"evt_{delivery_id}",
    }
    data.update(kwargs)
    return DeliverySpec(**data)


def test_planned_worst_case_formula():
    schedule = Schedule(
        deliveries=[delivery("d1", max_attempts=2), delivery("d2"), delivery("d3")],
        steps=[
            Step(deliveries=["d1"], delay_before_ms=100),
            Step(deliveries=["d2", "d3"]),
        ],
        delivery_policy=AttemptPolicy(
            attempt_timeout_ms=5000, max_attempts=1, backoff_ms=[200]
        ),
    )
    # step 1: 100 + (5000*2 + 200) = 10300; step 2: 0 + 5000 = 5000.
    assert planned_worst_case_ms(schedule) == 15300
    assert validate_schedule(schedule) == []


def test_worst_case_over_25s_is_rejected():
    schedule = Schedule(
        deliveries=[delivery("d1", max_attempts=3)],
        steps=[Step(deliveries=["d1"])],
        delivery_policy=AttemptPolicy(attempt_timeout_ms=9000, max_attempts=1),
    )
    assert planned_worst_case_ms(schedule) == 27000
    errors = validate_schedule(schedule)
    assert any("planned worst case" in error for error in errors)


def test_rendezvous_party_count_must_match_the_group():
    schedule = Schedule(
        deliveries=[delivery(f"d{i}") for i in range(1, 4)],
        steps=[Step(deliveries=["d1", "d2", "d3"])],
        faults=[RendezvousFault(parties=2)],
        delivery_policy=AttemptPolicy(attempt_timeout_ms=1000, max_attempts=1),
    )
    errors = validate_schedule(schedule)
    assert any("rendezvous parties=2" in error for error in errors)


def test_rendezvous_matching_no_deliveries_is_rejected():
    schedule = Schedule(
        deliveries=[delivery("d1")],
        steps=[Step(deliveries=["d1"])],
        faults=[RendezvousFault(parties=2, payments=["payment:B1"])],
    )
    assert any("matches no deliveries" in error for error in validate_schedule(schedule))


def test_too_many_attempts_and_too_long_timeout():
    schedule = Schedule(
        deliveries=[delivery("d1", max_attempts=4, attempt_timeout_ms=11_000)],
        steps=[Step(deliveries=["d1"])],
    )
    errors = validate_schedule(schedule)
    assert any("attempts=4" in error for error in errors)
    assert any("attempt_timeout_ms=11000" in error for error in errors)


def test_group_and_delivery_limits():
    ids = [f"d{i}" for i in range(11)]
    schedule = Schedule(
        deliveries=[delivery(item) for item in ids],
        steps=[Step(deliveries=ids)],
        delivery_policy=AttemptPolicy(attempt_timeout_ms=100, max_attempts=1),
    )
    errors = validate_schedule(schedule)
    assert any("deliveries=11" in error for error in errors)
    assert any("group=11" in error for error in errors)


def test_unscheduled_and_unknown_deliveries():
    schedule = Schedule(
        deliveries=[delivery("d1"), delivery("d2")],
        steps=[Step(deliveries=["d1", "d3"])],
    )
    errors = validate_schedule(schedule)
    assert any("unknown delivery d3" in error for error in errors)
    assert any("unscheduled deliveries: d2" in error for error in errors)


def test_duplicate_delivery_id_is_rejected():
    schedule = Schedule(
        deliveries=[delivery("d1"), delivery("d1")],
        steps=[Step(deliveries=["d1"])],
    )
    assert any("duplicate delivery id" in error for error in validate_schedule(schedule))


def test_effect_intent_is_mandatory():
    with pytest.raises(ValidationError):
        DeliverySpec(id="d1", event=EVENT, event_id="evt")


def test_latency_fault_is_accepted():
    schedule = Schedule(
        deliveries=[delivery("d1"), delivery("d2")],
        steps=[Step(deliveries=["d1", "d2"])],
        faults=[LatencyFault(ms=200)],
        delivery_policy=AttemptPolicy(attempt_timeout_ms=1000, max_attempts=1),
    )
    assert validate_schedule(schedule, DEFAULT_LIMITS) == []