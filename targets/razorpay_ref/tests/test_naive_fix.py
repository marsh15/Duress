"""Sequential controls for the naive_fix variant (plan C.1).

Event-id dedupe sits in front of the unchanged vulnerable handler, so sequential
behaviour matches vulnerable except for repeated events.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from razorpay_ref.common.db import init_db
from razorpay_ref.naive_fix.handler import create_app

from .conftest import (
    SEED_PATH,
    captured_fixtures,
    mock_provider,
    order_paid,
    payment_captured,
    post_webhook,
    query,
    snapshot,
)


@pytest.fixture
def secret() -> str:
    return secrets.token_hex(16)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    init_db(path, SEED_PATH, "naive_fix")
    return path


@pytest.fixture
def client(db_path: Path, secret: str):
    app = create_app(
        db_path=str(db_path),
        provider_base_url="http://provider.invalid",
        secret=secret,
        instance="trial-unit",
        provider_transport=mock_provider(captured_fixtures()),
    )
    with TestClient(app) as test_client:
        yield test_client


def events(db_path: Path) -> list[str]:
    return [row["event_id"] for row in query(db_path, "SELECT event_id FROM webhook_events")]


def test_single_capture_is_processed(client, secret, db_path):
    response = post_webhook(client, secret, payment_captured(), event_id="evt_1")

    assert response.status_code == 200
    assert response.json() == {"status": "processed"}
    fulfilments = query(db_path, "SELECT * FROM fulfilments WHERE order_id = 'order_A'")
    assert len(fulfilments) == 1
    assert fulfilments[0]["payment_id"] == "pay_A1"
    assert events(db_path) == ["evt_1"]


def test_repeated_event_id_is_an_event_duplicate(client, secret, db_path):
    first = post_webhook(client, secret, payment_captured(), event_id="evt_1")
    second = post_webhook(client, secret, payment_captured(), event_id="evt_1")

    assert first.json() == {"status": "processed"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "scope": "event"}
    assert len(query(db_path, "SELECT * FROM fulfilments")) == 1
    assert events(db_path) == ["evt_1"]


def test_invalid_signature_writes_nothing(client, secret, db_path):
    baseline = snapshot(db_path)

    response = post_webhook(client, secret, payment_captured(), signature="0" * 64, event_id="evt_1")

    assert response.status_code == 401
    assert response.json() == {"status": "rejected", "reason": "signature"}
    assert snapshot(db_path) == baseline
    assert events(db_path) == []


def test_missing_event_id_is_rejected_without_a_write(client, secret, db_path):
    import json

    from razorpay_ref.common.signing import sign

    body = json.dumps(payment_captured(), separators=(",", ":")).encode()
    baseline = snapshot(db_path)

    response = client.post(
        "/webhooks/razorpay",
        content=body,
        headers={"Content-Type": "application/json", "X-Razorpay-Signature": sign(secret, body)},
    )

    assert response.status_code == 400
    assert response.json() == {"status": "rejected", "reason": "missing_event_id"}
    assert snapshot(db_path) == baseline
    assert events(db_path) == []


def test_new_event_for_a_processed_payment_is_a_payment_duplicate(client, secret, db_path):
    """Sequentially the vulnerable T1 check still catches a different event for the same payment."""
    first = post_webhook(client, secret, payment_captured(), event_id="evt_capture")
    second = post_webhook(client, secret, order_paid(), event_id="evt_order_paid")

    assert first.json() == {"status": "processed"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "scope": "payment"}
    assert len(query(db_path, "SELECT * FROM fulfilments")) == 1
    assert events(db_path) == ["evt_capture", "evt_order_paid"]


def test_second_payment_after_fulfilment_is_order_duplicate(client, secret, db_path):
    """MP-1 (sequential): A2 is acknowledged as an order duplicate and not mutated."""
    first = post_webhook(client, secret, payment_captured("pay_A1"), event_id="evt_A1")
    assert first.json() == {"status": "processed"}

    a2_baseline = query(db_path, "SELECT * FROM payments WHERE id = 'pay_A2'")
    second = post_webhook(client, secret, payment_captured("pay_A2"), event_id="evt_A2")

    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "scope": "order"}
    fulfilments = query(db_path, "SELECT * FROM fulfilments WHERE order_id = 'order_A'")
    assert len(fulfilments) == 1
    assert fulfilments[0]["payment_id"] == "pay_A1"
    assert query(db_path, "SELECT * FROM payments WHERE id = 'pay_A2'") == a2_baseline
