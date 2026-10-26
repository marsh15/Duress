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
    ScenarioSpec,
    Schedule,
    SeedDocument,
    Step,
)
from .profiles import captured_fixtures
from .schedule import assert_valid_schedule

TEMPLATE_VERSION = 1


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


def scenario_id_for(template_id: str, params: dict[str, Any]) -> str:
    """Stable, human-legible id: ``S3[copies=2,fault=rendezvous,latency_ms=200]``."""
    if not params:
        return template_id
    parts = ",".join(f"{key}={params[key]}" for key in sorted(params))
    return f"{template_id}[{parts}]"


def _scenario_id(template_id: str, params: dict[str, Any]) -> str:
    return scenario_id_for(template_id, params)


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


def _build_s5(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    """A payment event and an order event for the same payment, released together."""
    fault = params.get("fault", "rendezvous")
    latency_ms = int(params.get("latency_ms", 200))
    if fault not in {"none", "latency", "rendezvous"}:
        raise ValueError("S5 fault must be one of none, latency, rendezvous")
    d1 = DeliverySpec(
        id="d1",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A1"
        ),
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
    )
    d2 = DeliverySpec(
        id="d2",
        event=EventSpec(type="order.paid", order_role="order:A", payment_role="payment:A1"),
        effect_intent="capture_and_fulfil",
        event_id="evt_A_order_paid",
    )
    faults = []
    if fault == "rendezvous":
        faults.append(RendezvousFault(parties=2, timeout_ms=2000))
    elif fault == "latency":
        faults.append(LatencyFault(ms=latency_ms))
    schedule = Schedule(
        deliveries=[d1, d2],
        steps=[Step(deliveries=["d1", "d2"])],
        faults=faults,
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S5.exactly_once_two_events",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        footprint=["order:A"],
        required_evidence=[
            EvidenceRequirement(kind="group_overlap"),
            EvidenceRequirement(kind="answered", delivery="d1"),
            EvidenceRequirement(kind="answered", delivery="d2"),
        ],
    )
    return BuiltScenario(
        template_id="S5",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S5", {"fault": fault, "latency_ms": latency_ms}),
        params={"fault": fault, "latency_ms": latency_ms},
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


def _build_s6(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    """A slow provider turns the first attempt into a timeout, then a retry races it."""
    if params:
        raise ValueError("S6 takes no parameters")
    latency_ms = 2000
    attempt_timeout_ms = 1000
    d1 = DeliverySpec(
        id="d1",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A1"
        ),
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
        attempt_timeout_ms=attempt_timeout_ms,
        max_attempts=2,
    )
    schedule = Schedule(
        deliveries=[d1],
        steps=[Step(deliveries=["d1"])],
        faults=[LatencyFault(ms=latency_ms)],
        delivery_policy=_policy(
            attempt_timeout_ms=5000,
            max_attempts=1,
            retry_on=["timeout"],
            backoff_ms=[200],
        ),
    )
    expectation = Expectation(
        id="S6.retry_exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        footprint=["order:A"],
        required_evidence=[
            EvidenceRequirement(kind="attempt_timed_out", delivery="d1"),
            EvidenceRequirement(kind="retried", delivery="d1"),
        ],
    )
    return BuiltScenario(
        template_id="S6",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S6", params),
        params=params,
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=roles,
    )


def _build_s9(
    params: dict[str, Any], roles: dict[str, str], seed: SeedDocument
) -> BuiltScenario:
    """A second payment for an order already fulfilled by the first."""
    if params:
        raise ValueError("S9 takes no parameters")
    d1 = DeliverySpec(
        id="d1",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A1"
        ),
        effect_intent="capture_and_fulfil",
        event_id="evt_A1_payment_captured",
    )
    d2 = DeliverySpec(
        id="d2",
        event=EventSpec(
            type="payment.captured", order_role="order:A", payment_role="payment:A2"
        ),
        effect_intent="order_duplicate",
        event_id="evt_A2_payment_captured",
    )
    schedule = Schedule(
        deliveries=[d1, d2],
        steps=[Step(deliveries=["d1"]), Step(deliveries=["d2"])],
        delivery_policy=_policy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="S9.second_payment_after_fulfilment",
        effects=[
            Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1"),
            Effect(kind="payment_unchanged", payment_role="payment:A2"),
        ],
        footprint=["order:A"],
        required_evidence=[
            EvidenceRequirement(kind="answered", delivery="d1"),
            EvidenceRequirement(kind="answered", delivery="d2"),
            EvidenceRequirement(kind="send_after_end", delivery="d2", other="d1"),
        ],
        pass_conditions=[
            PassCondition(delivery="d1", expected="2xx"),
            PassCondition(delivery="d2", expected="2xx"),
        ],
    )
    return BuiltScenario(
        template_id="S9",
        template_version=TEMPLATE_VERSION,
        scenario_id=_scenario_id("S9", params),
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
    "S5": _build_s5,
    "S6": _build_s6,
    "S8": _build_s8,
    "S9": _build_s9,
}

TEMPLATES = tuple(_BUILDERS)

# C.8: seven entries, with S3 represented at two fault settings. S9 is a
# domain-semantics scenario: selectable, in the matrix and verify suites, but
# deliberately outside the exhaustive control set.
EXHAUSTIVE_SPECS: tuple[ScenarioSpec, ...] = (
    ScenarioSpec("S1", {}),
    ScenarioSpec("S2", {}),
    ScenarioSpec("S3", {"copies": 3, "fault": "rendezvous", "latency_ms": 200}),
    ScenarioSpec("S3", {"copies": 3, "fault": "latency", "latency_ms": 200}),
    ScenarioSpec("S5", {"fault": "rendezvous", "latency_ms": 200}),
    ScenarioSpec("S6", {}),
    ScenarioSpec("S8", {}),
)
EXHAUSTIVE_SET: tuple[str, ...] = tuple(spec.scenario_id for spec in EXHAUSTIVE_SPECS)


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

