"""DL-1, DL-2 and DL-3 (plan M1)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duress.limits import Limits
from duress.models import (
    AttemptPolicy,
    BuiltScenario,
    DeliverySpec,
    Effect,
    EventSpec,
    EvidenceRequirement,
    Expectation,
    LatencyFault,
    RendezvousFault,
    Schedule,
    Step,
    TableSnapshot,
)
from duress.oracle import evaluate_trial
from duress.profiles import captured_fixtures, load_seed
from duress.target import TrialConfig, run_trial


def deadline_schedule() -> tuple[Schedule, Expectation]:
    event_a = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A1")
    event_b = EventSpec(type="payment.captured", order_role="order:B", payment_role="payment:B1")
    deliveries = [
        DeliverySpec(id="d1", event=event_a, effect_intent="capture_and_fulfil", event_id="evt_a1"),
        DeliverySpec(id="d2", event=event_a, effect_intent="capture_and_fulfil", event_id="evt_a1"),
        DeliverySpec(id="d3", event=event_b, effect_intent="capture_and_fulfil", event_id="evt_b1"),
        DeliverySpec(id="d4", event=event_b, effect_intent="capture_and_fulfil", event_id="evt_b1"),
    ]
    schedule = Schedule(
        deliveries=deliveries,
        steps=[Step(deliveries=["d1", "d2"]), Step(deliveries=["d3", "d4"])],
        faults=[
            RendezvousFault(parties=2, payments=["payment:A1"]),
            LatencyFault(ms=5000, payments=["payment:B1"]),
        ],
        delivery_policy=AttemptPolicy(attempt_timeout_ms=5000, max_attempts=1),
    )
    expectation = Expectation(
        id="DL.exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        # Step 2 is the control lunge that the deadline cuts off. If the target
        # commits its effect after the client cancels, that is authorised.
        footprint=["order:A", "order:B"],
        required_evidence=[],
    )
    return schedule, expectation


async def run_deadline_trial(registry, stub, workdir: Path, target_id: str):
    profile = registry.get(target_id)
    seed = load_seed(profile)
    schedule, expectation = deadline_schedule()
    scenario = BuiltScenario(
        template_id="DL",
        template_version=1,
        scenario_id="DL",
        params={},
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=profile.roles,
    )
    observation = await run_trial(
        TrialConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=workdir,
            limits=Limits(execution_deadline_s=2.0, planned_worst_case_ms=100_000),
            home=workdir,
        )
    )
    verdict = evaluate_trial(
        observation,
        schedule=schedule,
        expectation=expectation,
        roles=profile.roles,
        baseline=TableSnapshot(tables=seed.tables()),
        seed_order_ids={row["id"] for row in seed.orders},
    )
    return observation, verdict


@pytest.mark.asyncio
async def test_dl1_fixed_deadline_is_inconclusive_not_pass(registry, stub, tmp_path):
    observation, verdict = await run_deadline_trial(registry, stub, tmp_path, "razorpay-fixed")
    assert observation.execution_deadline_exceeded is True
    assert observation.settlement == "clean"
    assert verdict.status == "INCONCLUSIVE"
    assert verdict.reasons == ["trial_timeout"]
    assert observation.cancelled_attempts == 2


@pytest.mark.asyncio
async def test_dl2_vulnerable_committed_violation_still_fails(registry, stub, tmp_path):
    observation, verdict = await run_deadline_trial(registry, stub, tmp_path, "razorpay-vulnerable")
    assert observation.execution_deadline_exceeded is True
    assert verdict.status == "FAIL"
    assert verdict.signature.check == "INV-1@1"


@pytest.mark.asyncio
async def test_dl3_planned_timeouts_do_not_set_the_deadline_flag(registry, stub, tmp_path):
    profile = registry.get("razorpay-fixed")
    seed = load_seed(profile)
    event = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A1")
    delivery = DeliverySpec(
        id="d1",
        event=event,
        effect_intent="capture_and_fulfil",
        event_id="evt_a1",
        attempt_timeout_ms=1000,
        max_attempts=2,
    )
    schedule = Schedule(
        deliveries=[delivery],
        steps=[Step(deliveries=["d1"])],
        faults=[LatencyFault(ms=2000)],
        delivery_policy=AttemptPolicy(
            attempt_timeout_ms=1000,
            max_attempts=1,
            retry_on=["timeout"],
            backoff_ms=[100],
        ),
    )
    expectation = Expectation(
        id="S6.exactly_once",
        effects=[Effect(kind="exactly_once", order_role="order:A", payment_role="payment:A1")],
        # Step 2 is the control lunge that the deadline cuts off. If the target
        # commits its effect after the client cancels, that is authorised.
        footprint=["order:A", "order:B"],
        required_evidence=[
            EvidenceRequirement(kind="attempt_timed_out", delivery="d1"),
            EvidenceRequirement(kind="retried", delivery="d1"),
        ],
    )
    scenario = BuiltScenario(
        template_id="S6",
        template_version=1,
        scenario_id="S6",
        params={},
        schedule=schedule,
        expectation=expectation,
        fixtures=captured_fixtures(seed),
        roles=profile.roles,
    )
    observation = await run_trial(
        TrialConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=tmp_path,
            home=tmp_path,
        )
    )
    verdict = evaluate_trial(
        observation,
        schedule=schedule,
        expectation=expectation,
        roles=profile.roles,
        baseline=TableSnapshot(tables=seed.tables()),
        seed_order_ids={row["id"] for row in seed.orders},
    )
    assert [attempt.outcome for attempt in observation.attempts] == ["timeout", "timeout"]
    assert observation.execution_deadline_exceeded is False
    assert verdict.status == "PASS"