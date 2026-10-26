"""One conformance test per exec@1 clause (plan C.2.1)."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import pytest

from duress import semantics, signing
from duress.expectations import EFFECT_KINDS, EVIDENCE_KINDS, PASS_CONDITION_KINDS
from duress.limits import Limits
from duress.models import (
    AttemptPolicy,
    DeliverySpec,
    EventSpec,
    LatencyFault,
    RendezvousFault,
    Schedule,
    Step,
)
from duress.provider_stub import RendezvousRule
from duress.runner import run_schedule

from ..recording_target import RecordingTarget

ROLES = {
    "order:A": "order_A",
    "payment:A1": "pay_A1",
    "order:B": "order_B",
    "payment:B1": "pay_B1",
}
SECRET = "exec-test-secret"


def delivery(delivery_id: str, payment_role: str = "payment:A1", **kwargs) -> DeliverySpec:
    event = EventSpec(
        type="payment.captured",
        order_role="order:A" if payment_role == "payment:A1" else "order:B",
        payment_role=payment_role,
    )
    data = {
        "id": delivery_id,
        "event": event,
        "effect_intent": "capture_and_fulfil",
        "event_id": f"evt_{delivery_id}",
    }
    data.update(kwargs)
    return DeliverySpec(**data)


async def run(target: RecordingTarget, schedule: Schedule, limits: Limits | None = None):
    return await run_schedule(
        schedule,
        base_url=target.base_url,
        secret=SECRET,
        roles=ROLES,
        limits=limits or Limits(),
    )


def record_for(target: RecordingTarget, event_id: str) -> dict:
    for record in target.records:
        if record["event_id"] == event_id:
            return record
    raise AssertionError(f"no record for {event_id}")


@pytest.mark.asyncio
async def test_c1_steps_run_in_order():
    with RecordingTarget(delays=[0.02]) as target:
        schedule = Schedule(
            deliveries=[delivery("d1"), delivery("d2")],
            steps=[Step(deliveries=["d1"]), Step(deliveries=["d2"], delay_before_ms=200)],
            delivery_policy=AttemptPolicy(attempt_timeout_ms=2000, max_attempts=1),
        )
        result = await run(target, schedule)
    first = record_for(target, "evt_d1")
    second = record_for(target, "evt_d2")
    assert result.execution_deadline_exceeded is False
    assert second["started"] >= first["ended"] + 0.15


@pytest.mark.asyncio
async def test_c2_concurrent_group_releases_first_attempts_together():
    with RecordingTarget(delays=[0.3]) as target:
        schedule = Schedule(
            deliveries=[delivery(f"d{i}") for i in range(1, 4)],
            steps=[Step(deliveries=["d1", "d2", "d3"])],
            delivery_policy=AttemptPolicy(attempt_timeout_ms=2000, max_attempts=1),
        )
        await run(target, schedule)
    starts = [record["started"] for record in target.records]
    ends = [record["ended"] for record in target.records]
    assert len(starts) == 3
    assert max(starts) - min(starts) < 0.1
    assert all(start < min(ends) for start in starts)


@pytest.mark.asyncio
async def test_c3_planned_attempt_timeout_is_an_outcome():
    with RecordingTarget(delays=[0.6]) as target:
        schedule = Schedule(
            deliveries=[delivery("d1", attempt_timeout_ms=100)],
            steps=[Step(deliveries=["d1"])],
            delivery_policy=AttemptPolicy(attempt_timeout_ms=5000, max_attempts=1),
        )
        result = await run(target, schedule)
    assert [attempt.outcome for attempt in result.attempts] == ["timeout"]
    assert result.execution_deadline_exceeded is False
    assert result.duration_s < 0.5


@pytest.mark.asyncio
async def test_c4_retry_reuses_body_and_event_id_and_regenerates_signature():
    # The subject is retry semantics, not a sub-100 ms scheduling budget: the
    # first attempt is made to overrun its planned timeout by a wide margin, so
    # a busy host cannot turn attempt 2 into a spontaneous timeout.
    with RecordingTarget(delays=[2.0, 0.0]) as target:
        schedule = Schedule(
            deliveries=[delivery("d1", attempt_timeout_ms=1000, max_attempts=2)],
            steps=[Step(deliveries=["d1"])],
            delivery_policy=AttemptPolicy(
                attempt_timeout_ms=5000,
                max_attempts=1,
                retry_on=["timeout"],
                backoff_ms=[100],
            ),
        )
        result = await run(target, schedule)
    assert [attempt.outcome for attempt in result.attempts] == ["timeout", "response"]
    assert result.attempts[1].started_s - result.attempts[0].ended_s >= 0.09
    first, second = target.records[0], target.records[1]
    assert first["body"] == second["body"]
    assert first["event_id"] == second["event_id"]
    assert first["signature"] == signing.sign(SECRET, first["body"])
    assert second["signature"] == signing.sign(SECRET, second["body"])


def test_c5_planned_worst_case():
    schedule = Schedule(
        deliveries=[delivery("d1", max_attempts=2)],
        steps=[Step(deliveries=["d1"], delay_before_ms=100)],
        delivery_policy=AttemptPolicy(
            attempt_timeout_ms=1000, max_attempts=1, backoff_ms=[200]
        ),
    )
    from duress.schedule import planned_worst_case_ms

    assert planned_worst_case_ms(schedule) == 100 + (1000 * 2 + 200)


@pytest.mark.asyncio
async def test_c6_execution_deadline_cancels_and_records():
    with RecordingTarget(delays=[1.0]) as target:
        schedule = Schedule(
            deliveries=[delivery("d1"), delivery("d2")],
            steps=[Step(deliveries=["d1", "d2"])],
            delivery_policy=AttemptPolicy(attempt_timeout_ms=5000, max_attempts=1),
        )
        result = await run(target, schedule, Limits(execution_deadline_s=0.2))
    assert result.execution_deadline_exceeded is True
    assert result.cancelled_attempts == 2
    assert result.undispatched == 0


@pytest.mark.asyncio
async def test_c7_signing_and_invalid_signature():
    with RecordingTarget() as target:
        schedule = Schedule(
            deliveries=[
                delivery("d1"),
                delivery("d2", invalid_signature=True),
            ],
            steps=[Step(deliveries=["d1"]), Step(deliveries=["d2"])],
            delivery_policy=AttemptPolicy(attempt_timeout_ms=2000, max_attempts=1),
        )
        await run(target, schedule)
    valid = record_for(target, "evt_d1")
    invalid = record_for(target, "evt_d2")
    assert valid["signature"] == signing.sign(SECRET, valid["body"])
    assert invalid["signature"] == signing.invalid_signature(SECRET, "d2", invalid["body"])
    assert invalid["signature"] != signing.sign(SECRET, invalid["body"])


@pytest.mark.asyncio
async def test_c8_stub_fixtures_and_stale_calls(stub):
    trial_id = "stub-" + uuid.uuid4().hex[:8]
    stub.create_namespace(
        trial_id,
        {"pay_A1": {"id": "pay_A1", "order_id": "order_A", "amount": 5000, "currency": "INR", "status": "captured"}},
    )
    before = stub.registry.stale_total()
    async with httpx.AsyncClient() as client:
        found = await client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1")
        missing = await client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_missing")
        stale = await client.get(f"{stub.base_url}/nowhere")
    assert found.status_code == 200
    assert found.json()["status"] == "captured"
    assert missing.status_code == 404
    assert stale.status_code == 410
    assert stub.registry.stale_total() == before + 1
    stub.retire(trial_id)


@pytest.mark.asyncio
async def test_c9_latency_fault_delays_matching_calls(stub):
    trial_id = "lat-" + uuid.uuid4().hex[:8]
    stub.create_namespace(
        trial_id,
        {"pay_A1": {"id": "pay_A1", "order_id": "order_A", "amount": 5000, "currency": "INR", "status": "captured"}},
        default_latency_ms=200,
    )
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1")
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed >= 0.18
    stub.retire(trial_id)


@pytest.mark.asyncio
async def test_c10_rendezvous_releases_by_parties_then_timeout_then_not_at_all(stub):
    trial_id = "rv-" + uuid.uuid4().hex[:8]
    stub.create_namespace(
        trial_id,
        {"pay_A1": {"id": "pay_A1", "order_id": "order_A", "amount": 5000, "currency": "INR", "status": "captured"}},
        default_rendezvous=RendezvousRule(parties=2, timeout_ms=1000),
    )
    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1"),
            client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1"),
        )
    stats = stub.stats(trial_id)
    assert stats["released_by"] == "parties"
    assert stats["arrivals"] == 2
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        await client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1")
    assert time.monotonic() - started < 0.05  # later calls are not held
    stub.retire(trial_id)

    trial_id = "rvto-" + uuid.uuid4().hex[:8]
    stub.create_namespace(
        trial_id,
        {"pay_A1": {"id": "pay_A1", "order_id": "order_A", "amount": 5000, "currency": "INR", "status": "captured"}},
        default_rendezvous=RendezvousRule(parties=2, timeout_ms=100),
    )
    started = time.monotonic()
    async with httpx.AsyncClient() as client:
        await client.get(f"{stub.base_url}/t/{trial_id}/v1/payments/pay_A1")
    assert time.monotonic() - started >= 0.09
    assert stub.stats(trial_id)["released_by"] == "timeout"
    stub.retire(trial_id)


def test_c11_expectation_vocabulary():
    assert set(EVIDENCE_KINDS) == {
        "answered",
        "group_overlap",
        "attempt_timed_out",
        "retried",
        "send_after_end",
    }
    assert set(EFFECT_KINDS) == {"exactly_once", "payment_unchanged"}
    assert set(PASS_CONDITION_KINDS) == {"status_class"}


def test_c12_settlement_vocabulary_matches_the_spec():
    assert set(semantics.spec()["settlements"]) == {
        "clean",
        "drain_unverified",
        "forced",
        "crashed",
        "unconfirmed",
    }
    assert semantics.identity().startswith("exec@1#")
    assert len(semantics.digest()) == 64