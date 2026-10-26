"""Naive-fix webhook handler: event-id dedupe in front of the unchanged defect.

The tempting repair for duplicate fulfilment is to remember event ids. This
variant inserts ``webhook_events(event_id PRIMARY KEY)`` first, as its own
autocommit statement, and answers a repeated event with ``duplicate scope=event``.
Everything after that is the vulnerable handler, verbatim: the check-then-act
across the awaited provider call is still there. Distinct events for the same
payment or order (S5, S9) still race. That gap is why this variant exists.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request

from ..common import responses
from ..common.config import Config
from ..common.db import connect
from ..common.signing import verify
from ..vulnerable.handler import handle as handle_vulnerable

log = logging.getLogger("razorpay_ref.naive_fix")

EVENT_ID_HEADER = "X-Razorpay-Event-Id"


async def handle(
    request: Request,
    config: Config,
    provider_transport: httpx.AsyncBaseTransport | None = None,
):
    raw = await request.body()
    if not verify(config.secret, raw, request.headers.get("X-Razorpay-Signature")):
        return responses.rejected(401, "signature")

    event_id = request.headers.get(EVENT_ID_HEADER)
    if not event_id:
        return responses.rejected(400, "missing_event_id")

    # Autocommit: the event is recorded before any processing and stays recorded.
    conn = connect(config.db_path)
    try:
        conn.execute(
            "INSERT INTO webhook_events (event_id, received_at) VALUES (?, ?)",
            (event_id, datetime.now(timezone.utc).isoformat()),
        )
    except sqlite3.IntegrityError:
        return responses.duplicate("event")
    except Exception:
        log.exception("unhandled webhook error")
        return responses.error(500, "internal_error")
    finally:
        conn.close()

    return await handle_vulnerable(request, config, provider_transport)


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
    app = FastAPI(title="razorpay-ref (naive_fix)")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"instance": config.instance}

    @app.post("/webhooks/razorpay")
    async def webhook(request: Request):
        return await handle(request, config, provider_transport)

    return app
