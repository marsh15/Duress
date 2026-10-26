"""Mock payment-provider API with latency, rendezvous and per-trial isolation.

One stub process (an in-process uvicorn thread) serves every trial under
``/t/{trial_id}/``. Retired namespaces answer 410 and count a ``stale_call``,
which is how a leaked target is detected rather than silently contaminating a
later trial.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .util import free_port, wait_for_http


@dataclass
class NamespaceConfig:
    fixtures: dict[str, dict]
    latency_ms: int = 0
    parties: int | None = None
    rendezvous_timeout_ms: int = 2000


@dataclass
class Namespace:
    config: NamespaceConfig
    calls: int = 0
    arrivals: int = 0
    released_by: str | None = None
    stale_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _event: asyncio.Event | None = None

    def event(self) -> asyncio.Event:
        # Created lazily inside the stub's event loop; all access is from that loop.
        if self._event is None:
            self._event = asyncio.Event()
        return self._event

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "calls": self.calls,
                "arrivals": self.arrivals,
                "released_by": self.released_by,
                "stale_calls": self.stale_calls,
                "latency_ms": self.config.latency_ms,
                "parties": self.config.parties,
            }

    def note_stale(self) -> None:
        with self._lock:
            self.stale_calls += 1


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, Namespace] = {}
        self._retired: dict[str, Namespace] = {}

    def create(self, trial_id: str, config: NamespaceConfig) -> Namespace:
        namespace = Namespace(config=config)
        with self._lock:
            self._active[trial_id] = namespace
        return namespace

    def get(self, trial_id: str) -> Namespace | None:
        with self._lock:
            return self._active.get(trial_id)

    def retire(self, trial_id: str) -> None:
        with self._lock:
            namespace = self._active.pop(trial_id, None)
            if namespace is not None:
                self._retired[trial_id] = namespace

    def note_stale(self, trial_id: str | None) -> None:
        with self._lock:
            if trial_id is None:
                return
            namespace = self._retired.get(trial_id)
            if namespace is not None:
                namespace.note_stale()


async def _serve_payment(registry: Registry, trial_id: str, payment_id: str) -> JSONResponse:
    namespace = registry.get(trial_id)
    if namespace is None:
        registry.note_stale(trial_id)
        return JSONResponse(status_code=410, content={"error": "stale_namespace"})

    config = namespace.config
    with namespace._lock:
        namespace.calls += 1

    if config.latency_ms:
        await asyncio.sleep(config.latency_ms / 1000)

    hold = False
    event = None
    if config.parties is not None:
        with namespace._lock:
            if namespace.released_by is None:
                hold = True
                namespace.arrivals += 1
                if namespace.arrivals >= config.parties:
                    namespace.released_by = "parties"
                    event = namespace.event()
                    event.set()
    if hold and event is None:
        try:
            await asyncio.wait_for(namespace.event().wait(), config.rendezvous_timeout_ms / 1000)
        except asyncio.TimeoutError:
            with namespace._lock:
                if namespace.released_by is None:
                    namespace.released_by = "timeout"
                namespace.event().set()

    fixture = config.fixtures.get(payment_id)
    if fixture is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(status_code=200, content=fixture)


def create_app(registry: Registry) -> FastAPI:
    app = FastAPI(title="duress-provider-stub")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/t/{trial_id}/v1/payments/{payment_id}")
    async def payment(trial_id: str, payment_id: str):
        return await _serve_payment(registry, trial_id, payment_id)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all(path: str, request: Request):
        # Any other prefix is a stale/incorrect call by contract (exec@1 clause 8).
        parts = path.split("/")
        trial_id = parts[1] if len(parts) > 1 and parts[0] == "t" else None
        registry.note_stale(trial_id)
        return JSONResponse(status_code=410, content={"error": "stale_call"})

    return app


class StubServer:
    def __init__(self, registry: Registry | None = None) -> None:
        self.registry = registry or Registry()
        self.port: int | None = None
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self.port is None:
            raise RuntimeError("stub not started")
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "StubServer":
        self.port = free_port()
        config = uvicorn.Config(
            create_app(self.registry),
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, name="provider-stub", daemon=True)
        self._thread.start()
        wait_for_http(f"{self.base_url}/healthz", timeout_s=10)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    def create_namespace(
        self,
        trial_id: str,
        fixtures: dict[str, dict],
        *,
        latency_ms: int = 0,
        parties: int | None = None,
        rendezvous_timeout_ms: int = 2000,
    ) -> Namespace:
        return self.registry.create(
            trial_id,
            NamespaceConfig(
                fixtures=fixtures,
                latency_ms=latency_ms,
                parties=parties,
                rendezvous_timeout_ms=rendezvous_timeout_ms,
            ),
        )

    def stats(self, trial_id: str) -> dict:
        namespace = self.registry.get(trial_id)
        if namespace is None:
            raise KeyError(trial_id)
        return namespace.snapshot()

    def retire(self, trial_id: str) -> None:
        self.registry.retire(trial_id)
