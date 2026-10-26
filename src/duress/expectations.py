"""Expectation vocabulary v1: effects, evidence and pass conditions (C.5/C.11)."""

from __future__ import annotations

from typing import Any, Iterable

from .models import (
    AttemptRecord,
    Expectation,
    Schedule,
    TableSnapshot,
    Violation,
)

EVIDENCE_KINDS = (
    "answered",
    "group_overlap",
    "attempt_timed_out",
    "retried",
    "send_after_end",
)
EFFECT_KINDS = ("exactly_once", "payment_unchanged")
PASS_CONDITION_KINDS = ("status_class",)


def _role_entity(roles: dict[str, str], role: str | None) -> str | None:
    if role is None:
        return None
    return roles.get(role)


def _row_for(rows: Iterable[dict[str, Any]], key: str, value: Any) -> dict[str, Any] | None:
    for row in rows:
        if row.get(key) == value:
            return row
    return None


def evaluate_effects(
    expectation: Expectation,
    snapshot: TableSnapshot,
    roles: dict[str, str],
    baseline: TableSnapshot,
) -> list[Violation]:
    misses: list[Violation] = []
    for effect in expectation.effects:
        if effect.kind == "exactly_once":
            order_id = _role_entity(roles, effect.order_role)
            payment_id = _role_entity(roles, effect.payment_role)
            if order_id is None or payment_id is None:
                misses.append(
                    Violation(
                        check="expected-effect@1",
                        kind="expected_effect_missing",
                        roles=sorted(
                            role for role in (effect.order_role, effect.payment_role) if role
                        ),
                        entity_ids=sorted(
                            entity for entity in (order_id, payment_id) if entity
                        ),
                    )
                )
                continue
            linked = [
                row
                for row in snapshot.rows("fulfilments")
                if row.get("order_id") == order_id and row.get("payment_id") == payment_id
            ]
            payment = _row_for(snapshot.rows("payments"), "id", payment_id)
            order = _row_for(snapshot.rows("orders"), "id", order_id)
            ok = (
                len(linked) == 1
                and payment is not None
                and payment.get("status") == "captured"
                and order is not None
                and order.get("status") == "fulfilled"
            )
            if not ok:
                misses.append(
                    Violation(
                        check="expected-effect@1",
                        kind="expected_effect_missing",
                        roles=sorted([effect.order_role or "", effect.payment_role or ""]),
                        entity_ids=sorted([order_id, payment_id]),
                    )
                )
        elif effect.kind == "payment_unchanged":
            payment_id = _role_entity(roles, effect.payment_role)
            if payment_id is None:
                misses.append(
                    Violation(
                        check="expected-effect@1",
                        kind="expected_effect_missing",
                        roles=[effect.payment_role] if effect.payment_role else [],
                    )
                )
                continue
            baseline_row = _row_for(baseline.rows("payments"), "id", payment_id)
            current = _row_for(snapshot.rows("payments"), "id", payment_id)
            if baseline_row != current:
                misses.append(
                    Violation(
                        check="expected-effect@1",
                        kind="expected_effect_missing",
                        roles=[effect.payment_role or ""],
                        entity_ids=[payment_id],
                    )
                )
    return misses


def _attempts_for(attempts: Iterable[AttemptRecord], delivery_id: str) -> list[AttemptRecord]:
    return [attempt for attempt in attempts if attempt.delivery_id == delivery_id]


def _overlap(left: AttemptRecord, right: AttemptRecord) -> bool:
    if left.outcome == "cancelled" or right.outcome == "cancelled":
        return False
    return left.started_s < right.ended_s and right.started_s < left.ended_s


def evaluate_evidence(
    expectation: Expectation,
    schedule: Schedule,
    attempts: list[AttemptRecord],
) -> list[str]:
    missing: list[str] = []
    for requirement in expectation.required_evidence:
        if requirement.kind == "answered":
            delivery_id = requirement.delivery
            if not delivery_id or not any(
                attempt.outcome == "response"
                for attempt in _attempts_for(attempts, delivery_id)
            ):
                missing.append(f"answered:{delivery_id}")
        elif requirement.kind == "group_overlap":
            found = False
            for step in schedule.steps:
                group: list[AttemptRecord] = []
                for delivery_id in step.deliveries:
                    group.extend(_attempts_for(attempts, delivery_id))
                if any(
                    _overlap(left, right)
                    for index, left in enumerate(group)
                    for right in group[index + 1 :]
                ):
                    found = True
                    break
            if not found:
                missing.append("group_overlap")
        elif requirement.kind == "attempt_timed_out":
            delivery_id = requirement.delivery
            if not delivery_id or not any(
                attempt.outcome == "timeout"
                for attempt in _attempts_for(attempts, delivery_id)
            ):
                missing.append(f"attempt_timed_out:{delivery_id}")
        elif requirement.kind == "retried":
            delivery_id = requirement.delivery
            if not delivery_id or not any(
                attempt.attempt >= 2
                for attempt in _attempts_for(attempts, delivery_id)
            ):
                missing.append(f"retried:{delivery_id}")
        elif requirement.kind == "send_after_end":
            later = requirement.delivery
            earlier = requirement.other
            earlier_attempts = _attempts_for(attempts, earlier or "")
            later_attempts = _attempts_for(attempts, later or "")
            if not earlier_attempts or not later_attempts:
                missing.append(f"send_after_end:{earlier}->{later}")
                continue
            if min(item.started_s for item in later_attempts) <= max(
                item.ended_s for item in earlier_attempts
            ):
                missing.append(f"send_after_end:{earlier}->{later}")
    return missing


def evaluate_pass_conditions(
    expectation: Expectation, attempts: list[AttemptRecord]
) -> list[str]:
    failures: list[str] = []
    for condition in expectation.pass_conditions:
        responses = [
            attempt
            for attempt in _attempts_for(attempts, condition.delivery)
            if attempt.outcome == "response"
        ]
        statuses = [attempt.status_code or 0 for attempt in responses]
        if condition.expected == "4xx":
            if any(200 <= status < 300 for status in statuses):
                failures.append(f"accepted_without_effect:{condition.delivery}")
            elif not any(400 <= status < 500 for status in statuses):
                failures.append(f"rejection_unobserved:{condition.delivery}")
        elif condition.expected == "2xx":
            if not any(200 <= status < 300 for status in statuses):
                failures.append(f"control_failed_to_execute:{condition.delivery}")
    return failures


def response_semantics_mismatches(
    attempts: list[AttemptRecord], delivery_intents: dict[str, str]
) -> list[str]:
    mismatches: list[str] = []
    for attempt in attempts:
        if not attempt.is_2xx:
            continue
        intent = delivery_intents.get(attempt.delivery_id)
        if intent == "order_duplicate":
            if attempt.body_status != "duplicate" or attempt.body_scope != "order":
                mismatches.append(
                    f"{attempt.delivery_id}:expected duplicate/order got "
                    f"{attempt.body_status}/{attempt.body_scope}"
                )
        elif intent == "capture_and_fulfil":
            valid = attempt.body_status == "processed" or (
                attempt.body_status == "duplicate"
                and attempt.body_scope in {"event", "payment", "order"}
            )
            if not valid:
                mismatches.append(
                    f"{attempt.delivery_id}:expected processed or duplicate got "
                    f"{attempt.body_status}/{attempt.body_scope}"
                )
    return mismatches