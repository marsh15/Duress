"""Scenario registry for M1: S1, S2, S3 and S8 (plan C.5/C.8)."""

from __future__ import annotations

from typing import Any, Callable

from .limits import Limits
from .models import (
    AttemptPolicy,
    BuiltScenario,
    DeliverySpec,
    Effect,
    EventSpec,
    EvidenceRequirement,
    Expectation,
    LatencyFault,
    PassCondition,
    RendezvousFault,
    Schedule,
    SeedDocument,
    Step,
    canonical_json,
)
from .profiles import captured_fixtures
from .schedule import assert_valid_schedule

TEMPLATE_VERSION = 1
EXHAUSTIVE_SET: tuple[str, ...] = ()  # populated in M2 with S1, S2, S3x2, S5, S6, S8


def _payment_id(roles: dict[str, str], role: str) -> str:
    try:
        return roles[role]
    except KeyError:
        raise KeyError(f"scenario requires seed role {role!r}") from None


def _event_payload(event_type: str, order_id: str, payment_id: str) -> dict[str, Any]:
    if event_type == "payment.captured":
        return {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": 5000,
                        "currency": "INR",
                        "status": "captured",
                    }
                }
            },
        }
    if event_type == "order.paid":
        return {
            "event": "order.paid",
            "payload": {
                "order": {
                    "entity": {
                        "id": order_id,
                        "amount": 5000,
                        "currency": "INR",
                    }
                },
                "payment": {"entity": {"id": payment_id}},
            },
        }
    raise ValueError(f"unsupported event {event_type!r}")


def event_payload(schedule: Schedule, delivery: DeliverySpec, roles: dict[str, str]) -> dict[str, Any]:
    order_id = _payment_id(roles, delivery.event.order_role)
    payment_id = _payment_id(roles, delivery.event.payment_role)
    return _event_payload(delivery.event.type, order_id, payment_id)


def _policy(**kwargs: Any) -> AttemptPolicy:
    return AttemptPolicy(**kwargs)


def _scenario_id(template_id: str, params: dict[str, Any]) -> str:
    if not params:
        return template_id
    return f"{template_id}[{canonical_json(params)}]"


def _build_s1(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    if params:
        raise ValueError("S1 takes no parameters")
    d1 = DeliverySpec(
        id="d1",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A1"
        ),
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
    )
    schedule = Schedule(
        deliveries=[d1],
        steps=[Step(deliveries=["d1"])],
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S1.exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        footprint=["order:A"],
        required_evidence=[EvidenceRequirement(kind="answered", delivery="d1")],
    )
    return BuiltScenario(
        template_id="S1",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S1", params),
        params=params,
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


def _build_s2(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    if params:
        raise ValueError("S2 takes no parameters")
    event = EventSpec(
        type="payment.captured", order_role="order:A", payment_role="payment:A1"
    )
    # Same event delivered twice, sequentially.
    d1 = DeliverySpec(
        id="d1",
        event=event,
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
    )
    d2 = DeliverySpec(
        id="d2",
        event=event,
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
    )
    schedule = Schedule(
        deliveries=[d1, d2],
        steps=[Step(deliveries=["d1"]), Step(deliveries=["d2"])],
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S2.exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        footprint=["order:A"],
        required_evidence=[
            EvidenceRequirement(kind="answered", delivery="d1"),
            EvidenceRequirement(kind="answered", delivery="d2"),
            EvidenceRequirement(kind="send_after_end", delivery="d2", other="d1"),
        ],
    )
    return BuiltScenario(
        template_id="S2",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S2", params),
        params=params,
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


def _build_s3(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    copies = int(params.get("copies", 3))
    fault = params.get("fault", "rendezvous")
    latency_ms = int(params.get("latency_ms", 200))
    if not 2 <= copies <= 10:
        raise ValueError("S3 copies must be in [2, 10]")
    if fault not in {"none", "latency", "rendezvous"}:
        raise ValueError("S3 fault must be one of none, latency, rendezvous")

    event = EventSpec(
        type="payment.captured", order_role="order:A", payment_role="payment:A1"
    )
    deliveries = [
        DeliverySpec(
            id=f"d{index + 1}",
            event=event,
            effect_intent="capture_and_fulfil",
            event_id="evt_A1_payment_captured",
        )
        for index in range(copies)
    ]
    faults = []
    if fault == "rendezvous":
        faults.append(RendezvousFault(parties=copies, timeout_ms=2000))
    elif fault == "latency":
        faults.append(LatencyFault(ms=latency_ms))

    schedule = Schedule(
        deliveries=deliveries,
        steps=[Step(deliveries=[delivery.id for delivery in deliveries])],
        faults=faults,
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S3.exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        footprint=["order:A"],
        required_evidence=[
            EvidenceRequirement(kind="group_overlap"),
            EvidenceRequirement(kind="answered", delivery="d1"),
        ],
    )
    return BuiltScenario(
        template_id="S3",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S3", {"copies": copies, "fault": fault, "latency_ms": latency_ms}),
        params={"copies": copies, "fault": fault, "latency_ms": latency_ms},
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


def _build_s8(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    if params:
        raise ValueError("S8 takes no parameters")
    d1 = DeliverySpec(
        id="d1",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A1"
        ),
        effect_intent="none",
        event_id="evt_A1_invalid",
        invalid_signature=True,
    )
    d2 = DeliverySpec(
        id="d2",
        event=EventSpec(
            type="payment.captured", order_role="order:B", payment_role="payment:B1"
        ),
        effect_intent="capture_and_fulfil",
        event_id="evt_B1_payment_captured",
    )
    schedule = Schedule(
        deliveries=[d1, d2],
        steps=[Step(deliveries=["d1"]), Step(deliveries=["d2"])],
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S8.control_then_effect",
        effects=[Effect(kind="exactly_once", order_role="order:B", payment_role="payment:B1")],
        footprint=["order:B"],
        required_evidence=[
            EvidenceRequirement(kind="answered", delivery="d1"),
            EvidenceRequirement(kind="answered", delivery="d2"),
        ],
        pass_conditions=[
            PassCondition(delivery="d1", expected="4xx"),
            PassCondition(delivery="d2", expected="2xx"),
        ],
    )
    return BuiltScenario(
        template_id="S8",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S8", params),
        params=params,
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


_BUILDERS: dict[str, Callable[[dict[str, Any], dict[str, str], SeedDocument], BuiltScenario]] = {
    "S1": _build_s1,
    "S2": _build_s2,
    "S3": _build_s3,
    "S8": _build_s8,
}

TEMPLATES = tuple(_BUILDERS)


def build_scenario(
    template_id: str,
    params: dict[str, Any],
    roles: dict[str, str],
    seed: SeedDocument,
    *,
    limits: Limits | None = None,
) -> BuiltScenario:
    try:
        builder = _BUILDERS[template_id]
    except KeyError:
        raise KeyError(f"unknown scenario {template_id!r}; known: {', '.join(TEMPLATES)}") from None
    built = builder(dict(params), roles, seed)
    assert_valid_schedule(built.schedule, limits)
    return built