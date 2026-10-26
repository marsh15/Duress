"""ASGI app for the fixture-mode controls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from fastapi import FastAPI, Request

from razorpay_ref.common import responses
from razorpay_ref.common.config import Config
from razorpay_ref.common.db import connect
from razorpay_ref.common.events import EventError, parse_event
from razorpay_ref.common.signing import verify

from . import MODES


def _mode() -> str:
    value = os.environ.get("FIXTURE_MODE", "ack_without_effect")
    if value not in MODES:
        raise RuntimeError(f"unknown FIXTURE_MODE {value!r}")
    return value


def _write_fulfilment(conn, order_id: str, payment_id: str, amount: int) -> None:
    conn.execute(
        "INSERT INTO fulfilments (order_id, payment_id, amount, created_at)"
        " VALUES (?, ?, ?, ?)",
        (order_id, payment_id, amount, datetime.now(timezone.utc).isoformat()),
    )
    conn.execute("UPDATE payments SET status = 'captured' WHERE id = ?", (payment_id,))
    conn.execute("UPDATE orders SET status = 'fulfilled' WHERE id = ?", (order_id,))


def _spawn_orphan(db_path: str) -> None:
    """A child that writes only after its parent dies.

    It inherits the trial's process group, so SIGTERM/SIGKILL to the group must
    take it down. If it ever escaped, the write lands in the snapshot and the
    isolation test fails.
    """
    script = f"""
import os, sqlite3, time
parent = {os.getpid()}
while True:
    try:
        os.kill(parent, 0)
    except OSError:
        break
    time.sleep(0.01)
conn = sqlite3.connect({db_path!r})
conn.execute(
    "INSERT INTO payments (id, order_id, amount, currency, status)"
    " VALUES ('orphan_marker', 'order_A', 5000, 'INR', 'authorized')"
)
conn.commit()
conn.close()
"""
    subprocess.Popen([sys.executable, "-c", script])


async def _handle(request: Request, config: Config, mode: str):
    raw = await request.body()
    signature_ok = verify(config.secret, raw, request.headers.get("X-Razorpay-Signature"))

    if mode == "reject_all":
        return responses.rejected(400, "reject_all")
    if not signature_ok and mode != "no_sig_check":
        return responses.rejected(401, "signature")

    try:
        payload = json.loads(raw)
    except ValueError:
        return responses.rejected(400, "invalid_json")
    try:
        delivery = parse_event(payload)
    except EventError as exc:
        return responses.rejected(400, exc.reason)

    if mode == "ack_without_effect":
        return responses.processed()

    conn = connect(config.db_path)
    try:
        payment = conn.execute(
            "SELECT status, amount FROM payments WHERE id = ? AND order_id = ?",
            (delivery.payment_id, delivery.order_id),
        ).fetchone()
        if payment is None:
            return responses.rejected(404, "unknown_payment")
        order = conn.execute(
            "SELECT status, amount FROM orders WHERE id = ?", (delivery.order_id,)
        ).fetchone()
        if order is None:
            return responses.rejected(404, "unknown_order")

        if order[0] == "fulfilled":
            if mode == "mark_duplicate_captured":
                # MP-2: mutates the second payment while acknowledging an order
                # duplicate, without a linked fulfilment.
                conn.execute(
                    "UPDATE payments SET status = 'captured' WHERE id = ?", (delivery.payment_id,)
                )
            return responses.duplicate("order")
        if payment[0] == "captured":
            return responses.duplicate("payment")

        conn.execute("BEGIN")
        try:
            if mode == "dup_then_hang":
                _write_fulfilment(conn, delivery.order_id, delivery.payment_id, order[1])
                _write_fulfilment(conn, delivery.order_id, delivery.payment_id, order[1])
            else:
                _write_fulfilment(conn, delivery.order_id, delivery.payment_id, order[1])
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        return responses.processed()
    finally:
        conn.close()


def create_app(
    *,
    mode: str | None = None,
    db_path: str | None = None,
    provider_base_url: str | None = None,
    secret: str | None = None,
    instance: str | None = None,
) -> FastAPI:
    active_mode = mode or _mode()
    config = Config.build(
        db_path=db_path,
        provider_base_url=provider_base_url,
        secret=secret,
        instance=instance,
    )
    app = FastAPI(title=f"fixture-modes ({active_mode})")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"instance": config.instance}

    @app.post("/webhooks/razorpay")
    async def webhook(request: Request):
        if active_mode == "spawn_orphan":
            try:
                _spawn_orphan_once(app)
            except OSError:
                pass
        return await _handle(request, config, active_mode)

    @app.on_event("startup")
    async def startup() -> None:
        if active_mode == "spawn_orphan":
            _spawn_orphan_once(app)

    return app


def _spawn_orphan_once(app: FastAPI) -> None:
    if getattr(app.state, "orphan_spawned", False):
        return
    app.state.orphan_spawned = True
    config = Config.from_env()
    _spawn_orphan(config.db_path)


app = create_app()