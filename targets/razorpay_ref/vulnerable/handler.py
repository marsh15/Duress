"""Vulnerable webhook handler: intentional non-atomic check-then-act (defect D-1).

T1 (autocommit) reads the payment and order status. The provider call is awaited
outside any transaction. T2 then inserts the fulfilment and flips both statuses
in one transaction. Two deliveries that both pass T1 before either reaches T2
both insert, because ``fulfilments`` has no UNIQUE constraint.

This defect is the object of study. Do not "fix" it here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from ..common import responses
from ..common.config import Config
from ..common.db import connect
from ..common.events import EventError, parse_event
from ..common.provider import ProviderError, fetch_payment
from ..common.signing import verify

log = logging.getLogger("razorpay_ref.vulnerable")


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
        # ---- T1: autocommit check-then-act ---------------------------------
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

        if payment[0] == "captured":
            return responses.duplicate("payment")
        if order[0] == "fulfilled":
            return responses.duplicate("order")
        # ---- end T1 ---------------------------------------------------------

        # The await below sits outside any transaction. This is the race window.
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

        # ---- T2: no await between BEGIN and COMMIT --------------------------
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    delivery.order_id,
                    delivery.payment_id,
                    order[1],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute(
                "UPDATE payments SET status = 'captured' WHERE id = ?", (delivery.payment_id,)
            )
            conn.execute(
                "UPDATE orders SET status = 'fulfilled' WHERE id = ?", (delivery.order_id,)
            )
            conn.execute("COMMIT")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # ---- end T2 ---------------------------------------------------------
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
    app = FastAPI(title="razorpay-ref (vulnerable)")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"instance": config.instance}

    @app.post("/webhooks/razorpay")
    async def webhook(request: Request):
        return await handle(request, config, provider_transport)

    return app
