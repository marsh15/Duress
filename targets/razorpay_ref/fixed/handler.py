"""Corrected webhook handler (plan C.1, fixed variant).

The pre-check and every mutation run inside one ``BEGIN IMMEDIATE`` transaction,
so the write lock is held from the check to the commit. The UNIQUE constraints on
``fulfilments.order_id`` and ``fulfilments.payment_id`` are the guarantee for
every code path, including a second worker.

Only the two named UNIQUE conflicts are treated as idempotent duplicates. Any
other integrity error rolls back and returns ``500 integrity_error``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from ..common import integrity, responses
from ..common.config import Config
from ..common.db import connect
from ..common.events import EventError, parse_event
from ..common.provider import ProviderError, fetch_payment
from ..common.signing import verify

log = logging.getLogger("razorpay_ref.fixed")


def _insert_fulfilment(conn: sqlite3.Connection, order_id: str, payment_id: str, amount: int) -> None:
    conn.execute(
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES (?, ?, ?, ?)",
        (order_id, payment_id, amount, datetime.now(timezone.utc).isoformat()),
    )


async def handle(
    request: Request,
    config: Config,
    provider_transport: httpx.AsyncBaseTransport | None = None,
):
    raw = await request.body()
    if not verify(config.secret, raw, request.headers.get("X-Razorpay-Signature")):
        return responses.rejected(401, "signature")

    try:
        payload = json.loads(raw)
    except ValueError:
        return responses.rejected(400, "invalid_json")

    try:
        delivery = parse_event(payload)
    except EventError as exc:
        return responses.rejected(400, exc.reason)

    conn = connect(config.db_path)
    try:
        # Validation reads run in autocommit; only the state transition is
        # transactional. The provider await must not sit inside a transaction.
        payment = conn.execute(
            "SELECT status, amount, currency FROM payments WHERE id = ? AND order_id = ?",
            (delivery.payment_id, delivery.order_id),
        ).fetchone()
        if payment is None:
            return responses.rejected(404, "unknown_payment")
        order = conn.execute(
            "SELECT status, amount, currency FROM orders WHERE id = ?",
            (delivery.order_id,),
        ).fetchone()
        if order is None:
            return responses.rejected(404, "unknown_order")

        try:
            provider_payment = await fetch_payment(
                config.provider_base_url, delivery.payment_id, provider_transport
            )
        except ProviderError:
            return responses.error(502, "provider_error")
        if provider_payment is None:
            return responses.rejected(404, "unknown_payment")
        if provider_payment.order_id != delivery.order_id:
            return responses.rejected(400, "entity_mismatch")
        if (
            provider_payment.amount != payment[1]
            or provider_payment.currency != payment[2]
            or payment[1] != order[1]
            or payment[2] != order[2]
        ):
            return responses.rejected(400, "amount_mismatch")
        if provider_payment.status != "captured":
            return responses.rejected(409, "invalid_state")

        # ---- transactional state transition: no await until resolved --------
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing = conn.execute(
                "SELECT payment_id FROM fulfilments WHERE order_id = ?",
                (delivery.order_id,),
            ).fetchone()
            if existing is not None:
                conn.execute("ROLLBACK")
                return responses.duplicate(
                    "payment" if existing[0] == delivery.payment_id else "order"
                )

            updated = conn.execute(
                "UPDATE payments SET status = 'captured'"
                " WHERE id = ? AND order_id = ? AND status IN ('created', 'authorized')",
                (delivery.payment_id, delivery.order_id),
            )
            if updated.rowcount == 0:
                conn.execute("ROLLBACK")
                known = conn.execute(
                    "SELECT 1 FROM payments WHERE id = ? AND order_id = ?",
                    (delivery.payment_id, delivery.order_id),
                ).fetchone()
                if known is None:
                    return responses.rejected(404, "unknown_payment")
                return responses.rejected(409, "invalid_state")

            _insert_fulfilment(conn, delivery.order_id, delivery.payment_id, order[1])
            conn.execute(
                "UPDATE orders SET status = 'fulfilled' WHERE id = ? AND status = 'created'",
                (delivery.order_id,),
            )
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK")
            scope = integrity.classify(exc)
            if scope is None:
                log.error("unexpected integrity error: %s", exc)
                return responses.error(500, "integrity_error")
            existing = conn.execute(
                "SELECT payment_id FROM fulfilments WHERE order_id = ?",
                (delivery.order_id,),
            ).fetchone()
            if existing is not None:
                scope = "payment" if existing[0] == delivery.payment_id else "order"
            return responses.duplicate(scope)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # ---- end transaction ------------------------------------------------
        return responses.processed()
    except Exception:
        log.exception("unhandled webhook error")
        return responses.error(500, "internal_error")
    finally:
        conn.close()


def create_app(
    *,
    db_path: str | None = None,
    provider_base_url: str | None = None,
    secret: str | None = None,
    instance: str | None = None,
    provider_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = Config.build(
        db_path=db_path,
        provider_base_url=provider_base_url,
        secret=secret,
        instance=instance,
    )
    app = FastAPI(title="razorpay-ref (fixed)")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"instance": config.instance}

    @app.post("/webhooks/razorpay")
    async def webhook(request: Request):
        return await handle(request, config, provider_transport)

    return app