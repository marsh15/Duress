"""Sequential controls, run against both variants (plan M0 target suite)."""

from __future__ import annotations

from .conftest import (
    order_paid,
    payment_captured,
    post_webhook,
    query,
    snapshot,
)


def test_first_capture(client, secret, db_path):
    response = post_webhook(client, secret, payment_captured())

    assert response.status_code == 200
    assert response.json() == {"status": "processed"}

    fulfilments = query(db_path, "SELECT * FROM fulfilments WHERE order_id = 'order_A'")
    assert len(fulfilments) == 1
    assert fulfilments[0]["payment_id"] == "pay_A1"
    assert query(db_path, "SELECT status FROM payments WHERE id = 'pay_A1'")[0]["status"] == "captured"
    assert query(db_path, "SELECT status FROM orders WHERE id = 'order_A'")[0]["status"] == "fulfilled"


def test_sequential_duplicate(client, secret, db_path):
    first = post_webhook(client, secret, payment_captured())
    second = post_webhook(client, secret, payment_captured())

    assert first.json() == {"status": "processed"}
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "scope": "payment"}
    assert len(query(db_path, "SELECT * FROM fulfilments")) == 1


def test_invalid_signature_changes_nothing(client, secret, db_path):
    baseline = snapshot(db_path)

    response = post_webhook(client, secret, payment_captured(), signature="0" * 64)

    assert response.status_code == 401
    assert response.json() == {"status": "rejected", "reason": "signature"}
    assert snapshot(db_path) == baseline


def test_amount_mismatch_changes_nothing(client, secret, db_path, fixtures):
    fixtures["pay_A1"]["amount"] = 4000
    baseline = snapshot(db_path)

    response = post_webhook(client, secret, payment_captured())

    assert response.status_code == 400
    assert response.json() == {"status": "rejected", "reason": "amount_mismatch"}
    assert snapshot(db_path) == baseline


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
    assert query(db_path, "SELECT status FROM orders WHERE id = 'order_A'")[0]["status"] == "fulfilled"


def test_order_paid_event_captures_and_fulfils(client, secret, db_path):
    response = post_webhook(client, secret, order_paid(), event_id="evt_order_paid")

    assert response.status_code == 200
    assert response.json() == {"status": "processed"}
    fulfilments = query(db_path, "SELECT * FROM fulfilments WHERE order_id = 'order_A'")
    assert len(fulfilments) == 1
    assert fulfilments[0]["payment_id"] == "pay_A1"


def test_unknown_payment_is_rejected(client, secret, db_path):
    baseline = snapshot(db_path)

    response = post_webhook(client, secret, payment_captured("pay_missing"))

    assert response.status_code == 404
    assert response.json() == {"status": "rejected", "reason": "unknown_payment"}
    assert snapshot(db_path) == baseline


def test_unsupported_event_is_rejected(client, secret, db_path):
    baseline = snapshot(db_path)

    response = post_webhook(client, secret, {"event": "refund.created", "payload": {}})

    assert response.status_code == 400
    assert response.json() == {"status": "rejected", "reason": "unsupported_event"}
    assert snapshot(db_path) == baseline
