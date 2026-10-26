"""``exec@1`` runner (plan C.2.1).

The runner controls interleaving, not outcomes: planned attempt timeouts are
ordinary attempt outcomes, and only the execution deadline cancels work.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import signing
from .limits import Limits
from .models import (
    AttemptPolicy,
    AttemptRecord,
    DeliverySpec,
    Schedule,
    Step,
    canonical_json,
)
from .scenarios import event_payload


@dataclass
class ExecutionResult:
    attempts: list[AttemptRecord] = field(default_factory=list)
    execution_deadline_exceeded: bool = False
    cancelled_attempts: int = 0
    undispatched: int = 0
    error: str | None = None
    duration_s: float = 0.0


def _planned_attempts(schedule: Schedule) -> int:
    total = 0
    for delivery in schedule.deliveries:
        _, attempts, _ = schedule.resolve_attempts(delivery)
        total += attempts
    return total


async def run_schedule(
    schedule: Schedule,
    *,
    base_url: str,
    secret: str,
    roles: dict[str, str],
    limits: Limits,
    signature_header: str = signing.HEADER,
    event_id_header: str = signing.EVENT_ID_HEADER,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ExecutionResult:
    base = time.monotonic()
    records: list[AttemptRecord] = []
    url = f"{base_url.rstrip('/')}/webhooks/razorpay"
    result = ExecutionResult()

    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:

        async def send(
            delivery: DeliverySpec,
            attempt: int,
            body: bytes,
            timeout_s: float,
        ) -> tuple[AttemptRecord, bool]:
            started = time.monotonic() - base
            headers = {
                "Content-Type": "application/json",
                event_id_header: delivery.event_id,
                # C4: the signature is regenerated for every attempt.
                signature_header: (
                    signing.invalid_signature(secret, delivery.id, body)
                    if delivery.invalid_signature
                    else signing.sign(secret, body)
                ),
            }
            try:
                response = await client.post(
                    url,
                    content=body,
                    headers=headers,
                    timeout=httpx.Timeout(timeout_s),
                )
                ended = time.monotonic() - base
                parsed: dict[str, Any] | None
                try:
                    parsed = response.json()
                except ValueError:
                    parsed = None
                record = AttemptRecord(
                    delivery_id=delivery.id,
                    attempt=attempt,
                    outcome="response",
                    status_code=response.status_code,
                    body_status=parsed.get("status") if isinstance(parsed, dict) else None,
                    body_scope=parsed.get("scope") if isinstance(parsed, dict) else None,
                    started_s=started,
                    ended_s=ended,
                )
                return record, False
            except asyncio.CancelledError:
                return (
                    AttemptRecord(
                        delivery_id=delivery.id,
                        attempt=attempt,
                        outcome="cancelled",
                        started_s=started,
                        ended_s=time.monotonic() - base,
                    ),
                    True,
                )
            except httpx.TimeoutException as exc:
                return (
                    AttemptRecord(
                        delivery_id=delivery.id,
                        attempt=attempt,
                        outcome="timeout",
                        started_s=started,
                        ended_s=time.monotonic() - base,
                        error=str(exc) or "timeout",
                    ),
                    False,
                )
            except httpx.HTTPError as exc:
                return (
                    AttemptRecord(
                        delivery_id=delivery.id,
                        attempt=attempt,
                        outcome="connect_error",
                        started_s=started,
                        ended_s=time.monotonic() - base,
                        error=str(exc),
                    ),
                    False,
                )

        async def run_delivery(delivery: DeliverySpec, gate: asyncio.Event, built: asyncio.Event, built_state: dict[str, int]) -> None:
            body = canonical_json(event_payload(schedule, delivery, roles)).encode()
            timeout_ms, attempts, backoff = schedule.resolve_attempts(delivery)
            built_state["count"] += 1
            if built_state["count"] == built_state["total"]:
                built.set()
            await gate.wait()

            for attempt in range(1, attempts + 1):
                record, cancelled = await send(delivery, attempt, body, timeout_ms / 1000)
                records.append(record)
                if cancelled:
                    raise asyncio.CancelledError()
                if record.outcome not in schedule.delivery_policy.retry_on:
                    break
                if attempt == attempts:
                    break
                delay = backoff[attempt - 1] if attempt - 1 < len(backoff) else 0
                if delay:
                    await asyncio.sleep(delay / 1000)

        async def run_step(step: Step) -> None:
            if step.delay_before_ms:
                await asyncio.sleep(step.delay_before_ms / 1000)
            gate = asyncio.Event()
            built = asyncio.Event()
            built_state = {"count": 0, "total": len(step.deliveries)}
            tasks = [
                asyncio.create_task(
                    run_delivery(schedule.delivery(delivery_id), gate, built, built_state)
                )
                for delivery_id in step.deliveries
            ]
            await built.wait()
            gate.set()
            await asyncio.gather(*tasks)

        async def run_all() -> None:
            for step in schedule.steps:
                await run_step(step)

        try:
            await asyncio.wait_for(run_all(), timeout=limits.execution_deadline_s)
        except asyncio.TimeoutError:
            result.execution_deadline_exceeded = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a harness bug, not a trial outcome
            result.error = f"{type(exc).__name__}: {exc}"

    result.attempts = sorted(records, key=lambda item: (item.started_s, item.delivery_id, item.attempt))
    result.cancelled_attempts = sum(1 for item in result.attempts if item.outcome == "cancelled")
    result.undispatched = max(0, _planned_attempts(schedule) - len(result.attempts))
    result.duration_s = time.monotonic() - base
    return result


def resolved_policy(schedule: Schedule, delivery: DeliverySpec) -> AttemptPolicy:
    timeout, attempts, backoff = schedule.resolve_attempts(delivery)
    return AttemptPolicy(
        attempt_timeout_ms=timeout,
        max_attempts=attempts,
        retry_on=list(schedule.delivery_policy.retry_on),
        backoff_ms=backoff,
    )


def json_body(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()