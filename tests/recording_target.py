"""A tiny HTTP target that records every webhook request for exec@1 tests."""

from __future__ import annotations

import asyncio
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from duress.provider_stub import free_port, wait_for_http


class RecordingTarget:
    def __init__(
        self,
        *,
        delays: list[float] | None = None,
        statuses: list[int] | None = None,
        body: dict | None = None,
    ) -> None:
        self.delays = list(delays or [])
        self.statuses = list(statuses or [])
        self.body = body or {"status": "processed"}
        self.records: list[dict] = []
        self.port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "RecordingTarget":
        app = FastAPI()

        @app.get("/healthz")
        def healthz() -> dict:
            return {"ok": True}

        @app.post("/webhooks/razorpay")
        async def webhook(request: Request):
            raw = await request.body()
            index = len(self.records)
            record = {
                "index": index,
                "started": time.monotonic(),
                "ended": None,
                "body": raw,
                "event_id": request.headers.get("X-Razorpay-Event-Id"),
                "signature": request.headers.get("X-Razorpay-Signature"),
            }
            self.records.append(record)
            delay = self.delays[min(index, len(self.delays) - 1)] if self.delays else 0.0
            if delay:
                await asyncio.sleep(delay)
            record["ended"] = time.monotonic()
            status = self.statuses[min(index, len(self.statuses) - 1)] if self.statuses else 200
            return JSONResponse(status_code=status, content=self.body)

        self.port = free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False)
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        wait_for_http(f"{self.base_url}/healthz", timeout_s=10)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    def reset(self) -> None:
        self.records.clear()

    def __enter__(self) -> "RecordingTarget":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()