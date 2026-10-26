"""Event parsing for the two webhook events in the reference contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class EventError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Delivery:
    event_type: str
    order_id: str
    payment_id: str


SUPPORTED_EVENTS = ("payment.captured", "order.paid")


def _entity(payload: dict[str, Any], *path: str) -> dict[str, Any]:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise EventError("invalid_payload")
        node = node[key]
    if not isinstance(node, dict):
        raise EventError("invalid_payload")
    return node


def _required(entity: dict[str, Any], key: str) -> str:
    value = entity.get(key)
    if not isinstance(value, str) or not value:
        raise EventError("invalid_payload")
    return value


def parse_event(payload: Any) -> Delivery:
    if not isinstance(payload, dict):
        raise EventError("invalid_payload")
    event_type = payload.get("event")
    if not isinstance(event_type, str):
        raise EventError("invalid_payload")
    if event_type not in SUPPORTED_EVENTS:
        raise EventError("unsupported_event")

    if event_type == "payment.captured":
        payment = _entity(payload, "payload", "payment", "entity")
        return Delivery(
            event_type=event_type,
            order_id=_required(payment, "order_id"),
            payment_id=_required(payment, "id"),
        )

    # order.paid carries the payment id that paid the order.
    order = _entity(payload, "payload", "order", "entity")
    payment = _entity(payload, "payload", "payment", "entity")
    return Delivery(
        event_type=event_type,
        order_id=_required(order, "id"),
        payment_id=_required(payment, "id"),
    )
