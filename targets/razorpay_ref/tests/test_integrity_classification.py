"""P4d / A11: only the two intended UNIQUE conflicts are duplicates.

Every error used here is produced by a real in-memory SQLite engine, so the
classifier is tested against the actual ``sqlite_errorname`` and message text of
the Python 3.12 ``sqlite3`` module.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from razorpay_ref.common import integrity
from razorpay_ref.common.db import connect, init_db
from razorpay_ref.fixed import handler as fixed_handler
from razorpay_ref.fixed.handler import create_app

from .conftest import (
    SEED_PATH,
    captured_fixtures,
    mock_provider,
    payment_captured,
    post_webhook,
    query,
    snapshot,
)


def _integrity_error(db_path, sql: str, params: tuple = ()) -> sqlite3.IntegrityError:
    conn = connect(db_path)
    try:
        conn.execute(sql, params)
    except sqlite3.IntegrityError as exc:
        return exc
    finally:
        conn.close()
    raise AssertionError("statement did not raise IntegrityError")


@pytest.fixture
def fixed_db(tmp_path):
    path = tmp_path / "fixed.db"
    init_db(path, SEED_PATH, "fixed")
    conn = connect(path)
    try:
        conn.execute(
            "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
            " VALUES ('order_A', 'pay_A1', 5000, '2026-01-01T00:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO orders (id, amount, currency, status) VALUES ('order_C', 5000, 'INR', 'created')"
        )
    finally:
        conn.close()
    return path


def test_duplicate_order_id_is_a_duplicate(fixed_db):
    exc = _integrity_error(
        fixed_db,
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES ('order_A', 'pay_A2', 5000, '2026-01-01T00:00:00+00:00')",
    )

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"
    assert integrity.classify(exc) == "order"


def test_duplicate_payment_id_is_a_duplicate(fixed_db):
    exc = _integrity_error(
        fixed_db,
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES ('order_C', 'pay_A1', 5000, '2026-01-01T00:00:00+00:00')",
    )

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"
    assert integrity.classify(exc) == "payment"


def test_foreign_key_violation_is_not_a_duplicate(fixed_db):
    exc = _integrity_error(
        fixed_db,
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES ('order_missing', 'pay_A2', 5000, '2026-01-01T00:00:00+00:00')",
    )

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_FOREIGNKEY"
    assert integrity.classify(exc) is None


def test_check_violation_is_not_a_duplicate(fixed_db):
    exc = _integrity_error(
        fixed_db,
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES ('order_C', 'pay_A2', 0, '2026-01-01T00:00:00+00:00')",
    )

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_CHECK"
    assert integrity.classify(exc) is None


def test_not_null_violation_is_not_a_duplicate(fixed_db):
    exc = _integrity_error(
        fixed_db,
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES ('order_C', 'pay_A2', 5000, NULL)",
    )

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_NOTNULL"
    assert integrity.classify(exc) is None


def test_unique_on_another_table_is_not_a_duplicate(fixed_db):
    conn = connect(fixed_db)
    try:
        conn.execute("CREATE TABLE other (id TEXT UNIQUE)")
        conn.execute("INSERT INTO other (id) VALUES ('x')")
    finally:
        conn.close()

    exc = _integrity_error(fixed_db, "INSERT INTO other (id) VALUES ('x')")

    assert exc.sqlite_errorname == "SQLITE_CONSTRAINT_UNIQUE"
    assert integrity.classify(exc) is None


def test_handler_returns_500_integrity_error_for_unexpected_conflict(tmp_path, monkeypatch):
    """A non-duplicate integrity error rolls back and surfaces as 500."""
    db_path = tmp_path / "handler.db"
    init_db(db_path, SEED_PATH, "fixed")
    secret = "test-secret"
    baseline = snapshot(db_path)

    def raise_check_violation(*_args, **_kwargs):
        # Produce a real CHECK IntegrityError from a scratch engine, then raise it
        # from inside the handler's transaction.
        scratch = sqlite3.connect(":memory:")
        try:
            scratch.execute("CREATE TABLE t (amount INTEGER CHECK (amount > 0))")
            scratch.execute("INSERT INTO t (amount) VALUES (0)")
        finally:
            scratch.close()

    monkeypatch.setattr(fixed_handler, "_insert_fulfilment", raise_check_violation)

    app = create_app(
        db_path=str(db_path),
        provider_base_url="http://provider.invalid",
        secret=secret,
        instance="trial-unit",
        provider_transport=mock_provider(captured_fixtures()),
    )
    with TestClient(app) as client:
        response = post_webhook(client, secret, payment_captured())

    assert response.status_code == 500
    assert response.json() == {"status": "error", "reason": "integrity_error"}
    assert snapshot(db_path) == baseline
    assert query(db_path, "SELECT status FROM payments WHERE id = 'pay_A1'")[0]["status"] == "authorized"
    assert query(db_path, "SELECT status FROM orders WHERE id = 'order_A'")[0]["status"] == "created"
