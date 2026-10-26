"""M3/M4 HTTP routes: investigations, reductions and differential evaluation.

Kept apart from ``api.py`` so the web layer can evolve independently. Every
route goes through the same admission control as the M2 routes (token,
per-IP rate limit, one operation at a time) and calls the shared services; no
route runs a trial itself.

Long operations return ``202 Accepted`` with a record id. The record is written
before the response and updated as the operation progresses, so clients poll
the matching ``GET`` route.
"""

from __future__ import annotations

import asyncio
import hmac
import re
import uuid
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import limits as limits_module
from . import services
from .orchestrator import MODES, initial_record, investigation_path, investigation_wall_s
from .store import atomic_write_json

RECORD_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


# --------------------------------------------------------------------------- #
# Request and response contracts
# --------------------------------------------------------------------------- #
class InvestigationRequest(BaseModel):
    target_id: str
    mode: Literal["live", "cached", "exhaustive"] = "cached"
    reduce: bool = False


class InvestigationAccepted(BaseModel):
    investigation_id: str
    status: Literal["queued"] = "queued"
    mode_requested: str
    target_id: str
    status_url: str


class ReduceRequest(BaseModel):
    target_id: str | None = None
    black_box: Literal[True] = True


class ReductionAccepted(BaseModel):
    reduction_id: str
    status: Literal["queued"] = "queued"
    dna_id: str
    target_id: str
    status_url: str


class DifferentialScenario(BaseModel):
    template_id: str = Field(pattern="^S[0-9]$")
    params: dict[str, Any] = Field(default_factory=dict)


class DifferentialRequest(BaseModel):
    target_ids: list[str] | None = Field(default=None, min_length=1, max_length=5)
    scenarios: list[DifferentialScenario] | None = Field(default=None, min_length=1, max_length=7)
    policy: Literal["explore", "matrix"] = "explore"


class DifferentialAccepted(BaseModel):
    differential_id: str
    status: Literal["queued"] = "queued"
    status_url: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _check_id(value: str, detail: str) -> None:
    if not RECORD_ID.fullmatch(value):
        raise HTTPException(status_code=404, detail=detail)


def _record_failure(path, initial: dict[str, Any], exc: BaseException) -> None:
    """Leave a pollable terminal record even if a service fails before saving one."""
    from .store import read_json

    try:
        current = read_json(path)
    except (OSError, ValueError):
        current = dict(initial)
    if not isinstance(current, dict):
        current = dict(initial)
    if current.get("status") not in {"completed", "failed"}:
        current.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            updated_at=_now(),
        )
    atomic_write_json(path, current)


def _record_running(path) -> None:
    from .store import read_json

    current = read_json(path)
    if isinstance(current, dict) and current.get("status") == "queued":
        current.update(status="running", updated_at=_now())
        atomic_write_json(path, current)


def install_backend_routes(
    app: FastAPI,
    *,
    settings: Any,
    workspace: Callable[[], services.Workspace],
    authorize: Callable[..., None],
    begin_operation: Callable[[Request], None],
    gate: Any,
) -> None:
    tasks: set[asyncio.Task] = set()
    app.state.backend_tasks = tasks

    def has_token(authorization: str | None, x_duress_token: str | None) -> bool:
        if settings.token is None:
            return True
        provided = x_duress_token
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        return bool(provided) and hmac.compare_digest(provided, settings.token)

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def backend_lifespan(application):
        async with previous_lifespan(application) as state:
            store = workspace()
            store.prepare()
            from .store import read_json

            for directory in ("investigations", "reductions", "differentials"):
                for path in (store.home / directory).glob("*.json"):
                    try:
                        record = read_json(path)
                    except (OSError, ValueError):
                        continue
                    if isinstance(record, dict) and record.get("status") in {"queued", "running"}:
                        record.update(status="failed", error="interrupted_by_restart", updated_at=_now())
                        atomic_write_json(path, record)
            try:
                yield state
            finally:
                pending = list(tasks)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

    app.router.lifespan_context = backend_lifespan

    def spawn(coro, *, path, initial: dict[str, Any]) -> None:
        """Own admission until completion, including shutdown cancellation."""
        started = False

        async def runner() -> None:
            nonlocal started
            started = True
            try:
                await coro
            except asyncio.CancelledError as exc:
                _record_failure(path, initial, exc)
                raise
            finally:
                gate.release()

        def finished(task) -> None:
            tasks.discard(task)
            if task.cancelled() and not started:
                # Cancellation before the runner's first instruction skips its
                # finally block and would otherwise leave admission acquired.
                coro.close()
                _record_failure(path, initial, asyncio.CancelledError())
                gate.release()

        task = asyncio.create_task(runner())
        tasks.add(task)
        task.add_done_callback(finished)

    def known_target(store: services.Workspace, target_id: str) -> None:
        if target_id not in {item.target_id for item in store.registry.all()}:
            raise HTTPException(status_code=404, detail="unknown_target")

    # ----------------------------------------------------------------- M3 --
    @app.post("/api/investigations", status_code=202, response_model=InvestigationAccepted)
    async def investigation_start(
        body: InvestigationRequest,
        request: Request,
        authorization: str | None = Header(default=None),
        x_duress_token: str | None = Header(default=None),
    ) -> InvestigationAccepted:
        # A.4: anonymous visitors may start cached investigations only.
        if body.mode != "cached" and not has_token(authorization, x_duress_token):
            raise HTTPException(status_code=401, detail="token_required")
        store = workspace()
        known_target(store, body.target_id)
        begin_operation(request)
        try:
            from .orchestrator import new_investigation_id

            investigation_id = new_investigation_id()
            store.prepare()
            path = investigation_path(store.home, investigation_id)
            record = initial_record(
                investigation_id,
                body.target_id,
                body.mode,
                wall_cap_s=investigation_wall_s(),
                trial_cap=limits_module.INVESTIGATION_TRIAL_CAP,
            )
            atomic_write_json(path, record)
        except BaseException:
            gate.release()
            raise

        async def run() -> None:
            try:
                await services.investigate(
                    store,
                    target_id=body.target_id,
                    mode=body.mode,
                    investigation_id=investigation_id,
                    reduce=body.reduce,
                )
            except Exception as exc:
                _record_failure(path, record, exc)

        spawn(run(), path=path, initial=record)
        return InvestigationAccepted(
            investigation_id=investigation_id,
            mode_requested=body.mode,
            target_id=body.target_id,
            status_url=f"/api/investigations/{investigation_id}",
        )

    @app.get("/api/investigations")
    def investigation_index() -> dict[str, Any]:
        return {"items": services.investigation_list(workspace()), "modes": list(MODES)}

    def _investigation(investigation_id: str) -> dict[str, Any]:
        _check_id(investigation_id, "unknown_investigation")
        try:
            return services.load_investigation(workspace(), investigation_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="unknown_investigation") from None

    @app.get("/api/investigations/{investigation_id}")
    def investigation_get(investigation_id: str) -> dict[str, Any]:
        return _investigation(investigation_id)

    @app.get("/api/investigations/{investigation_id}/hypotheses")
    def investigation_hypotheses(investigation_id: str) -> dict[str, Any]:
        record = _investigation(investigation_id)
        hypotheses = record.get("hypotheses") or []
        return {
            "investigation_id": investigation_id,
            "status": record.get("status"),
            "mode_effective": record.get("mode_effective"),
            "fallback_reason": record.get("fallback_reason"),
            "usable": sum(1 for item in hypotheses if item.get("usable")),
            "rejected": sum(1 for item in hypotheses if not item.get("usable")),
            "hypotheses": hypotheses,
        }

    @app.get("/api/investigations/{investigation_id}/findings")
    def investigation_findings(investigation_id: str) -> dict[str, Any]:
        record = _investigation(investigation_id)
        return {
            "investigation_id": investigation_id,
            "status": record.get("status"),
            "verdict": record.get("verdict"),
            "findings": record.get("findings") or [],
            "dna_ids": record.get("dna_ids") or [],
            "reductions": record.get("reductions") or [],
            "explanation": record.get("explanation"),
            "explanation_source": record.get("explanation_source"),
        }

    # ----------------------------------------------------------------- M4 --
    @app.post(
        "/api/dna/{dna_id}/reduce",
        status_code=202,
        response_model=ReductionAccepted,
        dependencies=[Depends(authorize)],
    )
    async def dna_reduce(dna_id: str, body: ReduceRequest, request: Request) -> ReductionAccepted:
        _check_id(dna_id, "unknown_dna")
        store = workspace()
        try:
            document = services.load_dna_document(store, dna_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="unknown_dna") from None
        target_id = body.target_id or str(
            document.provenance.get("target_id") or "razorpay-vulnerable"
        )
        known_target(store, target_id)
        begin_operation(request)
        reduction_id = f"red-{uuid.uuid4().hex[:12]}"
        path = store.home / "reductions" / f"{reduction_id}.json"
        try:
            store.prepare()
            atomic_write_json(
                path,
                {
                    "record_type": "reduction",
                    "reduction_id": reduction_id,
                    "status": "queued",
                    "original_dna_id": dna_id,
                    "target_id": target_id,
                    "created_at": _now(),
                },
            )
        except BaseException:
            gate.release()
            raise

        async def run() -> None:
            try:
                _record_running(path)
                await services.reduce_dna(
                    store,
                    dna_id=dna_id,
                    target_id=target_id,
                    black_box=body.black_box,
                    reduction_id=reduction_id,
                )
            except Exception as exc:
                _record_failure(
                    path,
                    {
                        "record_type": "reduction",
                        "reduction_id": reduction_id,
                        "original_dna_id": dna_id,
                        "target_id": target_id,
                        "created_at": _now(),
                    },
                    exc,
                )

        spawn(run(), path=path, initial={
            "record_type": "reduction", "reduction_id": reduction_id,
            "original_dna_id": dna_id, "target_id": target_id,
        })
        return ReductionAccepted(
            reduction_id=reduction_id,
            dna_id=dna_id,
            target_id=target_id,
            status_url=f"/api/reductions/{reduction_id}",
        )

    @app.get("/api/reductions/{reduction_id}")
    def reduction_get(reduction_id: str) -> dict[str, Any]:
        _check_id(reduction_id, "unknown_reduction")
        try:
            return services.load_reduction(workspace(), reduction_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="unknown_reduction") from None

    @app.post(
        "/api/differential",
        status_code=202,
        response_model=DifferentialAccepted,
        dependencies=[Depends(authorize)],
    )
    async def differential_start(body: DifferentialRequest, request: Request) -> DifferentialAccepted:
        from .models import ScenarioSpec
        from .scenarios import TEMPLATES
        from .investigator import validate_params

        store = workspace()
        for target_id in body.target_ids or []:
            known_target(store, target_id)
        specs = None
        if body.scenarios:
            specs = []
            for item in body.scenarios:
                if item.template_id not in TEMPLATES:
                    raise HTTPException(status_code=422, detail=f"unknown_template:{item.template_id}")
                problems = validate_params(item.template_id, item.params)
                if problems:
                    raise HTTPException(status_code=422, detail="invalid_scenario:" + "; ".join(problems))
                specs.append(ScenarioSpec(item.template_id, dict(item.params)))
        begin_operation(request)
        differential_id = f"diff-{uuid.uuid4().hex[:12]}"
        path = store.home / "differentials" / f"{differential_id}.json"
        initial = {
            "record_type": "differential",
            "differential_id": differential_id,
            "status": "queued",
            "created_at": _now(),
        }
        try:
            store.prepare()
            atomic_write_json(path, initial)
        except BaseException:
            gate.release()
            raise

        async def run() -> None:
            try:
                await services.differential(
                    store,
                    target_ids=body.target_ids,
                    specs=specs,
                    policy=body.policy,
                    differential_id=differential_id,
                )
            except Exception as exc:
                # The service normally persists its own error. Keep the
                # accepted record pollable if it fails before that write.
                _record_failure(path, initial, exc)

        spawn(run(), path=path, initial=initial)
        return DifferentialAccepted(
            differential_id=differential_id,
            status_url=f"/api/differential/{differential_id}",
        )

    @app.get("/api/differential/{differential_id}")
    def differential_get(differential_id: str) -> dict[str, Any]:
        _check_id(differential_id, "unknown_differential")
        try:
            return services.load_differential(workspace(), differential_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="unknown_differential") from None
