"""HTTP API: health, targets, DNA, replay and verify-fix (plan C.13).

Authorization and resource limits are enforced here, before any operation.
The API never accepts a caller-supplied command, path or executable: it accepts
a target id from the registry and a DNA id from the local store.
"""

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import __version__
from . import limits as limits_module
from .services import Workspace, dna_index, load_dna_document, replay, replay_payload, verify_fix
from .records import (
    ensure_showcase, investigation_index, load_investigation_record,
    load_operation_record, save_operation_record,
)

MAX_BODY_BYTES = 1_000_000


# --------------------------------------------------------------------------- #
# Settings and limits
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Settings:
    token: str | None = None
    hosted: bool = False
    root: Path | None = None
    per_ip_per_hour: int = limits_module.HOSTED_PER_IP_PER_HOUR
    per_day: int = limits_module.HOSTED_PER_DAY
    clock: Callable[[], float] = time.time

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            token=os.environ.get("DURESS_DEMO_TOKEN") or None,
            hosted=os.environ.get("DURESS_HOSTED", "").lower() in {"1", "true", "yes"},
            root=Path(os.environ["DURESS_ROOT"]) if os.environ.get("DURESS_ROOT") else None,
        )


@dataclass
class RateLimiter:
    per_ip_per_hour: int
    per_day: int
    clock: Callable[[], float]
    hour: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    day: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def allow(self, key: str) -> bool:
        now = self.clock()
        for bucket, window in ((self.hour, 3600.0), (self.day, 86400.0)):
            entries = bucket[key]
            while entries and now - entries[0] > window:
                entries.popleft()
        if len(self.hour[key]) >= self.per_ip_per_hour:
            return False
        if len(self.day[key]) >= self.per_day:
            return False
        self.hour[key].append(now)
        self.day[key].append(now)
        return True


class OperationGate:
    """One operation at a time (A.4). Never queues."""

    def __init__(self) -> None:
        self.busy = False

    def acquire(self) -> bool:
        if self.busy:
            return False
        self.busy = True
        return True

    def release(self) -> None:
        self.busy = False


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ReplayRequest(BaseModel):
    target_id: str | None = None
    policy: str = Field(default="replay", pattern="^(replay|explore)$")


class VerifyFixRequest(BaseModel):
    vulnerable_target_id: str = "razorpay-vulnerable"
    candidate_target_id: str = "razorpay-fixed"


class DnaPayloadRequest(BaseModel):
    """Replay an artifact posted by the caller (used by the portability checks)."""

    dna: dict[str, Any]
    target_id: str = "razorpay-vulnerable"


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Duress",
        version=__version__,
        description="Put your integrations under pressure. Keep the proof.",
    )
    rate = RateLimiter(settings.per_ip_per_hour, settings.per_day, settings.clock)
    gate = OperationGate()
    # Tests and operators can inspect admission state; they still cannot bypass
    # authorization, which is a dependency on every write route.
    app.state.settings = settings
    app.state.rate = rate
    app.state.gate = gate

    def workspace() -> Workspace:
        store = Workspace.discover(settings.root)
        ensure_showcase(store)
        return store

    def authorize(
        authorization: str | None = Header(default=None),
        x_duress_token: str | None = Header(default=None),
    ) -> None:
        if settings.token is None:
            return
        provided = x_duress_token
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        if not provided or not hmac.compare_digest(provided, settings.token):
            raise HTTPException(status_code=401, detail="token_required")

    def client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    @app.middleware("http")
    async def limit_body(request: Request, call_next):
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > MAX_BODY_BYTES:
                    from fastapi.responses import JSONResponse

                    return JSONResponse(status_code=413, content={"detail": "body_too_large"})
            except ValueError:
                pass
        return await call_next(request)

    def begin_operation(request: Request) -> None:
        if not rate.allow(client_key(request)):
            raise HTTPException(status_code=429, detail="rate_limited")
        if not gate.acquire():
            # A refused admission must not consume the caller's quota twice.
            raise HTTPException(status_code=409, detail="operation_in_progress")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "hosted": settings.hosted,
            "token_required": settings.token is not None,
            "busy": gate.busy,
        }

    @app.get("/api/targets")
    def targets() -> dict[str, Any]:
        registry = workspace().registry
        return {
            "targets": [
                {
                    "target_id": profile.target_id,
                    "profile": profile.profile_id,
                    "variant": profile.variant,
                    "contract_version": profile.contract_version,
                    "workers": profile.workers,
                }
                for profile in registry.all()
            ]
        }

    @app.get("/api/dna")
    def dna_list() -> dict[str, Any]:
        return {"items": dna_index(workspace())}

    @app.get("/api/investigations")
    def investigations() -> dict[str, Any]:
        return {"items": investigation_index(workspace()), "creation_available": False}

    @app.get("/api/investigations/{investigation_id}")
    def investigation_get(investigation_id: str) -> dict[str, Any]:
        try:
            return load_investigation_record(workspace(), investigation_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown_investigation") from None

    @app.get("/api/operations/{operation_id}")
    def operation_get(operation_id: str) -> dict[str, Any]:
        try:
            return load_operation_record(workspace(), operation_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown_operation") from None

    @app.get("/api/dna/{dna_id}")
    def dna_get(dna_id: str) -> dict[str, Any]:
        try:
            document = load_dna_document(workspace(), dna_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown_dna") from None
        return document.model_dump(mode="json")

    @app.post("/api/dna/{dna_id}/replay", dependencies=[Depends(authorize)])
    async def dna_replay(
        dna_id: str, body: ReplayRequest, request: Request
    ) -> dict[str, Any]:
        begin_operation(request)
        try:
            store = workspace()
            try:
                document = load_dna_document(store, dna_id)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="unknown_dna") from None
            target_id = body.target_id or str(
                document.provenance.get("target_id") or "razorpay-vulnerable"
            )
            if target_id not in {item.target_id for item in store.registry.all()}:
                raise HTTPException(status_code=404, detail="unknown_target")
            run = await replay(
                store, dna_id=dna_id, target_id=target_id, policy=body.policy
            )
            payload = {
                "outcome": run.outcome.model_dump(mode="json"),
                "preflight": run.preflight,
            }
            operation_id = save_operation_record(store, kind="replay", payload=payload,
                                                dna_id=dna_id, target_id=target_id)
            return {**payload, "operation_id": operation_id}
        finally:
            gate.release()

    @app.post("/api/dna/replay", dependencies=[Depends(authorize)])
    async def dna_replay_payload(
        body: DnaPayloadRequest, request: Request
    ) -> dict[str, Any]:
        """Portability: replay a document the caller already holds."""
        begin_operation(request)
        try:
            store = workspace()
            if body.target_id not in {item.target_id for item in store.registry.all()}:
                raise HTTPException(status_code=404, detail="unknown_target")
            dna_id = str(body.dna.get("dna_id") or "submitted")
            run = await replay_payload(
                store, payload=body.dna, dna_id=dna_id, target_id=body.target_id
            )
            payload = {
                "outcome": run.outcome.model_dump(mode="json"),
                "preflight": run.preflight,
            }
            operation_id = save_operation_record(store, kind="import_replay", payload=payload,
                                                dna_id=dna_id, target_id=body.target_id)
            return {**payload, "operation_id": operation_id}
        finally:
            gate.release()

    @app.post("/api/dna/{dna_id}/verify-fix", dependencies=[Depends(authorize)])
    async def dna_verify_fix(
        dna_id: str, body: VerifyFixRequest, request: Request
    ) -> dict[str, Any]:
        begin_operation(request)
        try:
            store = workspace()
            try:
                load_dna_document(store, dna_id)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="unknown_dna") from None
            known = {item.target_id for item in store.registry.all()}
            if body.vulnerable_target_id not in known or body.candidate_target_id not in known:
                raise HTTPException(status_code=404, detail="unknown_target")
            run = await verify_fix(
                store,
                dna_id=dna_id,
                vulnerable_target_id=body.vulnerable_target_id,
                candidate_target_id=body.candidate_target_id,
            )
            payload = run.outcome.model_dump(mode="json")
            operation_id = save_operation_record(store, kind="verify_fix", payload=payload,
                dna_id=dna_id, vulnerable_target_id=body.vulnerable_target_id,
                candidate_target_id=body.candidate_target_id)
            return {**payload, "operation_id": operation_id}
        finally:
            gate.release()

    from .web import install_web

    install_web(app, workspace)
    return app
