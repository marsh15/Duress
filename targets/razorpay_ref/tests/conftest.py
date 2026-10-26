"""Shared fixtures for the reference-target suite.

These tests drive the real ASGI app and the real SQLite schema. Only the payment
provider is replaced by an in-memory transport; the concurrency experiments in
``tests/m0`` use a real provider process instead.
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from razorpay_ref.common.db import init_db, rows_as_dicts
from razorpay_ref.common.signing import sign
from razorpay_ref.fixed.handler import create_app as create_fixed_app
from razorpay_ref.vulnerable.handler import create_app as create_vulnerable_app

SEED_PATH = Path(__file__).resolve().parents[1] / "seed.json"
VARIANTS = ("vulnerable", "fixed")

TABLES = ("orders", "payments", "fulfilments")


def captured_fixtures() -> dict[str, dict]:
    return {
        "pay_A1": {
            "id": "pay_A1",
            "order_id": "order_A",
            "amount": 5000,
            "currency": "INR",
            "status": "captured",
        },
        "pay_A2": {
            "id": "pay_A2",
            "order_id": "order_A",
            "amount": 5000,
            "currency": "INR",
            "status": "captured",
        },
    }


def mock_provider(fixtures: dict[str, dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payment_id = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        if payment_id not in fixtures:
            return httpx.Response(404, json={"error": "not_found"})
        return httpx.Response(200, json=fixtures[payment_id])

    return httpx.MockTransport(handler)


def payment_captured(payment_id: str = "pay_A1", order_id: str = "order_A") -> dict:
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


def order_paid(payment_id: str = "pay_A1", order_id: str = "order_A") -> dict:
    return {
        "event": "order.paid",
        "payload": {
            "order": {"entity": {"id": order_id, "amount": 5000, "currency": "INR"}},
            "payment": {"entity": {"id": payment_id}},
        },
    }


def post_webhook(
    client: TestClient,
    secret: str,
    payload: dict,
    *,
    signature: str | None = None,
    event_id: str = "evt_1",
) -> httpx.Response:
    import json

    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
        "X-Razorpay-Signature": signature if signature is not None else sign(secret, body),
    }
    return client.post("/webhooks/razorpay", content=body, headers=headers)


def snapshot(db_path: Path) -> dict[str, list[dict]]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {table: rows_as_dicts(conn, f"SELECT * FROM {table} ORDER BY rowid") for table in TABLES}
    finally:
        conn.close()


def query(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    try:
        return rows_as_dicts(conn, sql, params)
    finally:
        conn.close()


@pytest.fixture(params=VARIANTS)
def variant(request) -> str:
    return request.param


@pytest.fixture
def secret() -> str:
    return secrets.token_hex(16)


@pytest.fixture
def db_path(tmp_path: Path, variant: str) -> Path:
    path = tmp_path / "app.db"
    init_db(path, SEED_PATH, variant)
    return path


@pytest.fixture
def fixtures() -> dict[str, dict]:
    return captured_fixtures()


@pytest.fixture
def app(variant: str, db_path: Path, secret: str, fixtures: dict[str, dict]):
    factory = create_vulnerable_app if variant == "vulnerable" else create_fixed_app
    return factory(
        db_path=str(db_path),
        provider_base_url="http://provider.invalid",
        secret=secret,
        instance="trial-unit",
        provider_transport=mock_provider(fixtures),
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client
