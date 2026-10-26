"""Mock payment provider: fixtures, latency, party rendezvous, namespaces.

The stub is the controlled dependency. One process serves every trial under
``/t/{trial_id}/``. A retired namespace answers 410 and increments a global
stale counter; a trial whose window sees a stale call is INCONCLUSIVE, which is
how a leaked target is detected instead of silently contaminating a result.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(url: str, timeout_s: float = 10.0, interval_s: float = 0.02) -> dict:
    import httpx

    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(interval_s)
    raise TimeoutError(f"timed out waiting for {url}: {last_error}")


@dataclass(frozen=True)
class RendezvousRule:
    parties: int
    timeout_ms: int = 2000


@dataclass
class NamespaceConfig:
    fixtures: dict[str, dict[str, Any]]
    latency_by_payment: dict[str, int] = field(default_factory=dict)
    default_latency_ms: int = 0
    rendezvous_by_payment: dict[str, RendezvousRule] = field(default_factory=dict)
    default_rendezvous: RendezvousRule | None = None


@dataclass
class RendezvousState:
    rule: RendezvousRule
    arrivals: int = 0
    released_by: str | None = None
    holding: int = 0
    event: asyncio.Event | None = None

    def get_event(self) -> asyncio.Event:
        if self.event is None:
            self.event = asyncio.Event()
        return self.event


@dataclass
class Namespace:
    config: NamespaceConfig
    calls: int = 0
    arrivals: int = 0
    stale_calls: int = 0
    holding: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _rendezvous: dict[str, RendezvousState] = field(default_factory=dict)

    def rule_for(self, payment_id: str) -> RendezvousRule | None:
        return self.config.rendezvous_by_payment.get(payment_id, self.config.default_rendezvous)

    def latency_for(self, payment_id: str) -> int:
        return self.config.latency_by_payment.get(payment_id, self.config.default_latency_ms)

    def state_for(self, payment_id: str, rule: RendezvousRule) -> RendezvousState:
        key = payment_id if payment_id in self.config.rendezvous_by_payment else "_default"
        state = self._rendezvous.get(key)
        if state is None:
            state = RendezvousState(rule=rule)
            self._rendezvous[key] = state
        return state

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            conditions = {
                key: state.released_by for key, state in self._rendezvous.items()
            }
            values = {value for value in conditions.values() if value is not None}
            released_by = values.pop() if len(values) == 1 else ("mixed" if len(values) > 1 else None)
            return {
                "calls": self.calls,
                "arrivals": self.arrivals,
                "holding": self.holding,
                "stale_calls": self.stale_calls,
                "released_by": released_by,
                "release_conditions": conditions,
            }

    def note_stale(self) -> None:
        with self._lock:
            self.stale_calls += 1


class StubRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, Namespace] = {}
        self._retired: dict[str, Namespace] = {}
        self._stale_total = 0

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

    def stale_total(self) -> int:
        with self._lock:
            return self._stale_total

    def stats(self, trial_id: str) -> dict[str, Any]:
        with self._lock:
            namespace = self._active.get(trial_id) or self._retired.get(trial_id)
        if namespace is None:
            raise KeyError(trial_id)
        return namespace.snapshot()

    def note_stale(self, trial_id: str | None) -> None:
        with self._lock:
            self._stale_total += 1
            namespace = None
            if trial_id is not None:
                namespace = self._retired.get(trial_id) or self._active.get(trial_id)
            if namespace is not None:
                namespace.note_stale()


async def _serve_payment(
    registry: StubRegistry, trial_id: str, payment_id: str
) -> JSONResponse:
    namespace = registry.get(trial_id)
    if namespace is None:
        registry.note_stale(trial_id)
        return JSONResponse(status_code=410, content={"error": "stale_namespace"})

    with namespace._lock:
        namespace.calls += 1

    latency_ms = namespace.latency_for(payment_id)
    if latency_ms:
        await asyncio.sleep(latency_ms / 1000)

    rule = namespace.rule_for(payment_id)
    if rule is not None:
        state = namespace.state_for(payment_id, rule)
        held = False
        with namespace._lock:
            if state.released_by is None:
                held = True
                state.arrivals += 1
                state.holding += 1
                namespace.arrivals += 1
                namespace.holding += 1
                if state.arrivals >= rule.parties:
                    state.released_by = "parties"
                    state.get_event().set()
        if held:
            try:
                if state.released_by is None:
                    try:
                        await asyncio.wait_for(state.get_event().wait(), rule.timeout_ms / 1000)
                    except asyncio.TimeoutError:
                        with namespace._lock:
                            if state.released_by is None:
                                state.released_by = "timeout"
                            state.get_event().set()
            finally:
                with namespace._lock:
                    state.holding -= 1
                    namespace.holding -= 1

    fixture = namespace.config.fixtures.get(payment_id)
    if fixture is None:
        return JSONResponse(status_code=404, content={"error": "not_found"})
    return JSONResponse(status_code=200, content=fixture)


def create_app(registry: StubRegistry) -> FastAPI:
    app = FastAPI(title="duress-provider-stub")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    @app.get("/t/{trial_id}/v1/payments/{payment_id}")
    async def payment(trial_id: str, payment_id: str):
        return await _serve_payment(registry, trial_id, payment_id)

    @app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all(path: str, request: Request):
        parts = path.split("/")
        trial_id = parts[1] if len(parts) > 1 and parts[0] == "t" else None
        registry.note_stale(trial_id)
        return JSONResponse(status_code=410, content={"error": "stale_call"})

    return app


class StubServer:
    def __init__(self, registry: StubRegistry | None = None) -> None:
        self.registry = registry or StubRegistry()
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
        self._thread = threading.Thread(target=self._server.run, name="duress-stub", daemon=True)
        self._thread.start()
        wait_for_http(f"{self.base_url}/healthz", timeout_s=10)
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=10)

    def __enter__(self) -> "StubServer":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def create_namespace(
        self,
        trial_id: str,
        fixtures: dict[str, dict[str, Any]],
        *,
        latency_by_payment: dict[str, int] | None = None,
        default_latency_ms: int = 0,
        rendezvous_by_payment: dict[str, RendezvousRule] | None = None,
        default_rendezvous: RendezvousRule | None = None,
    ) -> Namespace:
        return self.registry.create(
            trial_id,
            NamespaceConfig(
                fixtures=fixtures,
                latency_by_payment=dict(latency_by_payment or {}),
                default_latency_ms=default_latency_ms,
                rendezvous_by_payment=dict(rendezvous_by_payment or {}),
                default_rendezvous=default_rendezvous,
            ),
        )

    def stats(self, trial_id: str) -> dict[str, Any]:
        return self.registry.stats(trial_id)

    def retire(self, trial_id: str) -> None:
        self.registry.retire(trial_id)