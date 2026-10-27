"""Local-only HTTP workbench for registered external integration projects."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from ..web import _render
from .artifacts import replay, reduce, verify
from .manifest import LoadedProject, load_project
from .reporting import html_report
from .services import doctor, investigate
from .storage import ProjectStore, StorageError, registered_projects

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_SCENARIO_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_DNA_ID = re.compile(r"^[a-f0-9]{64}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TERMINAL = {"completed", "failed", "interrupted", "timed_out"}
_OPERATION_TIMEOUT_SECONDS = 300


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InvestigationRequest(_StrictBody):
    scenario_id: str | None = Field(default=None, pattern=_SCENARIO_ID.pattern)


class EmptyRequest(_StrictBody):
    pass


class VerifyRequest(_StrictBody):
    candidate_project_id: str = Field(pattern=_PROJECT_ID.pattern)


def _loopback_address(value: str | None) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_loopback


def _authority(value: str) -> tuple[str, int | None] | None:
    if not value or any(char in value for char in "\\\r\n\t ,"):
        return None
    try:
        parsed = urlsplit("//" + value)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        return None
    return host.rstrip(".").lower(), port


def _host_is_loopback(request: Request) -> bool:
    authority = _authority(request.headers.get("host", ""))
    if authority is None:
        return False
    hostname, _ = authority
    return hostname == "localhost" or _loopback_address(hostname)


def _require_local_request(request: Request, *, mutation: bool = False) -> None:
    client = request.client.host if request.client else None
    if not _loopback_address(client) or not _host_is_loopback(request):
        raise HTTPException(status_code=403, detail="local_only")
    if mutation:
        origin = request.headers.get("origin")
        try:
            origin_parts = urlsplit(origin) if origin else None
        except ValueError:
            origin_parts = None
        host_authority = _authority(request.headers.get("host", ""))
        origin_authority = _authority(origin_parts.netloc) if origin_parts else None
        if (origin_parts is None or origin_parts.scheme not in {"http", "https"}
                or origin_parts.scheme != request.url.scheme
                or origin_parts.path or origin_parts.query or origin_parts.fragment
                or origin_authority is None or host_authority is None
                or origin_authority[0] != host_authority[0]):
            raise HTTPException(status_code=403, detail="same_origin_required")
        default_port = 80 if origin_parts.scheme == "http" else 443
        origin_port = origin_authority[1] if origin_authority[1] is not None else default_port
        host_port = host_authority[1] if host_authority[1] is not None else default_port
        if origin_port != host_port:
            raise HTTPException(status_code=403, detail="same_origin_required")


def _execution_allowed(settings: Any) -> None:
    hosted_env = os.environ.get("DURESS_HOSTED", "").lower() in {"1", "true", "yes"}
    if bool(getattr(settings, "hosted", False)) or hosted_env:
        raise HTTPException(status_code=403, detail="external_execution_disabled")


def _store(project_id: str) -> ProjectStore:
    return ProjectStore(project_id)


def _registry_entry(project_id: str) -> dict[str, Any]:
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="unknown_project")
    try:
        entries = registered_projects()
    except (OSError, ValueError, StorageError):
        raise HTTPException(status_code=503, detail="project_registry_unavailable") from None
    entry = next((item for item in entries if item.get("project") == project_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown_project")
    return entry


def _load_registered_project(project_id: str) -> tuple[dict[str, Any], LoadedProject]:
    entry = _registry_entry(project_id)
    try:
        loaded = load_project(Path(entry["config_path"]))
    except (OSError, ValueError, TypeError):
        raise HTTPException(status_code=409, detail="registered_project_unavailable") from None
    if loaded.manifest.duress.project != project_id:
        raise HTTPException(status_code=409, detail="registered_project_mismatch")
    return entry, loaded


def _public_project(entry: dict[str, Any], loaded: LoadedProject | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "project": entry["project"],
        "registered_at": entry.get("registered_at"),
        "updated_at": entry.get("updated_at"),
        "available": loaded is not None,
    }
    if loaded is None:
        summary["status"] = "configuration_unavailable"
        return summary
    manifest = loaded.manifest
    summary.update({
        "status": "ready",
        "target_id": manifest.target.id,
        "mode": manifest.target.mode,
        "contract": {"id": loaded.contract.id, "version": loaded.contract.version},
        "adapters": {
            "lifecycle": "external@1",
            "delivery": manifest.delivery.adapter,
            "invariants": "declarative@1",
            "dispatcher": "exec@1",
            "observer": manifest.observation.adapter,
        },
        "scenarios": [
            {
                "id": scenario.id,
                "deliveries": len(scenario.deliveries),
                "steps": len(scenario.steps),
                "require_overlap": scenario.require_overlap,
                "require_retry": scenario.require_retry,
            }
            for scenario in manifest.scenarios
        ],
    })
    return summary


def _registered_summaries() -> list[dict[str, Any]]:
    try:
        entries = registered_projects()
    except (OSError, ValueError, StorageError):
        raise HTTPException(status_code=503, detail="project_registry_unavailable") from None
    summaries = []
    for entry in entries:
        try:
            loaded = load_project(Path(entry["config_path"]))
            if loaded.manifest.duress.project != entry["project"]:
                loaded = None
        except (OSError, ValueError, TypeError):
            loaded = None
        summaries.append(_public_project(entry, loaded))
    return summaries


def _record(project_id: str, kind: str, record_id: str) -> dict[str, Any]:
    if not _RECORD_ID.fullmatch(record_id) or record_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="unknown_record")
    try:
        return _store(project_id).load(kind, record_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="unknown_record") from None
    except (OSError, ValueError, StorageError):
        raise HTTPException(status_code=404, detail="unknown_record") from None


def _records(project_id: str, kind: str) -> list[dict[str, Any]]:
    try:
        return _store(project_id).list(kind)
    except (OSError, ValueError, StorageError):
        raise HTTPException(status_code=503, detail="project_store_unavailable") from None


def _public_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """Keep the useful storage envelope fields without local owner metadata."""
    return {
        key: envelope[key]
        for key in ("project", "kind", "record_id", "created_at", "updated_at", "status", "payload")
        if key in envelope
    }


def _save_operation(project_id: str, operation_id: str, payload: dict[str, Any]) -> None:
    _store(project_id).save("operations", payload, operation_id)


async def _run_operation(
    *, app: FastAPI, project_id: str, operation_id: str, operation: str,
    config: Path, scenario_id: str | None = None, dna_payload: dict[str, Any] | None = None,
    candidate_config: Path | None = None,
) -> None:
    payload: dict[str, Any] = {
        "operation_id": operation_id,
        "operation": operation,
        "project": project_id,
        "status": "running",
    }
    try:
        _save_operation(project_id, operation_id, payload)
        async with asyncio.timeout(_OPERATION_TIMEOUT_SECONDS):
            if operation == "investigation":
                result = await investigate(config, scenario_id=scenario_id)
                payload["investigation_id"] = result.get("investigation_id")
            elif operation == "doctor":
                result = await asyncio.to_thread(lambda: asyncio.run(doctor(config)))
            elif operation == "replay":
                result = await replay(config, dna_payload or {})
            elif operation == "verify":
                if candidate_config is None:
                    raise ValueError("candidate project is unavailable")
                result = await verify(config, candidate_config, dna_payload or {})
            elif operation == "reduce":
                result = await reduce(config, dna_payload or {})
            else:
                raise ValueError("unsupported operation")
        payload.update({"status": "completed", "result": result})
        if isinstance(result, dict) and result.get("outcome") == "REJECTED" and result.get("reason"):
            payload["result"] = {**result, "reason": "artifact_rejected"}
        dna_result = result.get("dna") if isinstance(result, dict) else None
        if isinstance(dna_result, dict) and dna_result.get("dna_id"):
            payload["reduced_dna_id"] = dna_result["dna_id"]
    except TimeoutError:
        payload.update({"status": "timed_out", "error": "operation_timeout"})
    except asyncio.CancelledError:
        payload.update({"status": "interrupted", "error": "operation_interrupted"})
        try:
            _save_operation(project_id, operation_id, payload)
        finally:
            app.state.gate.release()
        raise
    except Exception:
        payload.update({"status": "failed", "error": "operation_failed"})
    finally:
        if payload["status"] != "interrupted":
            try:
                _save_operation(project_id, operation_id, payload)
            finally:
                app.state.gate.release()


def _queue_operation(
    *, app: FastAPI, request: Request, background_tasks: BackgroundTasks,
    project_id: str, operation: str, config: Path, scenario_id: str | None = None,
    dna_payload: dict[str, Any] | None = None, candidate_config: Path | None = None,
    candidate_project_id: str | None = None,
) -> dict[str, Any]:
    app.state.begin_operation(request)
    operation_id = "op-" + uuid.uuid4().hex[:20]
    payload: dict[str, Any] = {
        "operation_id": operation_id,
        "operation": operation,
        "project": project_id,
        "status": "queued",
    }
    if scenario_id is not None:
        payload["scenario_id"] = scenario_id
    if operation == "verify" and candidate_project_id is not None:
        payload["candidate_project"] = candidate_project_id
    try:
        _save_operation(project_id, operation_id, payload)
        background_tasks.add_task(
            _run_operation,
            app=app,
            project_id=project_id,
            operation_id=operation_id,
            operation=operation,
            config=config,
            scenario_id=scenario_id,
            dna_payload=dna_payload,
            candidate_config=candidate_config,
        )
    except Exception:
        app.state.gate.release()
        raise HTTPException(status_code=503, detail="operation_unavailable") from None
    base = f"/projects/{project_id}"
    return {
        "project": project_id,
        "operation_id": operation_id,
        "operation": operation,
        "status": "queued",
        "poll_url": f"/api/projects/{project_id}/operations/{operation_id}",
        "operation_url": f"{base}/operations/{operation_id}",
    }


def install_external(
    app: FastAPI,
    settings: Any,
    authorize: Callable[..., Any],
    begin_operation: Callable[[Request], None],
    gate: Any,
) -> None:
    """Install the external workbench using the app's shared auth and gate."""
    app.state.begin_operation = begin_operation
    app.state.gate = gate
    router = APIRouter()

    @router.get("/projects", response_class=HTMLResponse)
    def projects_page(request: Request):
        _require_local_request(request)
        return HTMLResponse(_render("projects.html", projects=_registered_summaries()))

    @router.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_page(project_id: str, request: Request):
        _require_local_request(request)
        entry, loaded = _load_registered_project(project_id)
        summary = _public_project(entry, loaded)
        return HTMLResponse(_render(
            "project.html",
            project=summary,
            projects=_registered_summaries(),
            runs=_records(project_id, "investigations")[:8],
            dna_items=_records(project_id, "dna")[:8],
            operations=[
                item for item in _records(project_id, "operations")
                if item["payload"].get("operation_id") == item["record_id"]
            ][:8],
        ))

    @router.get("/projects/{project_id}/investigations/{investigation_id}", response_class=HTMLResponse)
    def investigation_page(project_id: str, investigation_id: str, request: Request):
        _require_local_request(request)
        entry, loaded = _load_registered_project(project_id)
        record = _record(project_id, "investigations", investigation_id)
        return HTMLResponse(_render(
            "external_run.html",
            project=_public_project(entry, loaded),
            record=record["payload"],
            operation=None,
        ))

    @router.get("/projects/{project_id}/investigations/{investigation_id}/report")
    def investigation_report(project_id: str, investigation_id: str, request: Request):
        _require_local_request(request)
        _load_registered_project(project_id)
        record = _record(project_id, "investigations", investigation_id)
        return HTMLResponse(html_report(record["payload"]), headers={"Cache-Control": "no-store"})

    @router.get("/projects/{project_id}/operations/{operation_id}", response_class=HTMLResponse)
    def operation_page(project_id: str, operation_id: str, request: Request):
        _require_local_request(request)
        entry, loaded = _load_registered_project(project_id)
        operation = _record(project_id, "operations", operation_id)
        if operation["payload"].get("operation_id") != operation_id:
            raise HTTPException(status_code=404, detail="unknown_operation")
        return HTMLResponse(_render(
            "external_run.html",
            project=_public_project(entry, loaded),
            record=None,
            operation=operation,
        ))

    @router.get("/api/projects")
    def project_index_api(request: Request):
        _require_local_request(request)
        return {"projects": _registered_summaries()}

    @router.get("/api/projects/{project_id}")
    def project_detail_api(project_id: str, request: Request):
        _require_local_request(request)
        entry, loaded = _load_registered_project(project_id)
        return _public_project(entry, loaded)

    @router.get("/api/projects/{project_id}/operations/{operation_id}")
    def operation_get(project_id: str, operation_id: str, request: Request):
        _require_local_request(request)
        _registry_entry(project_id)
        operation = _record(project_id, "operations", operation_id)
        if operation["payload"].get("operation_id") != operation_id:
            raise HTTPException(status_code=404, detail="unknown_operation")
        return _public_envelope(operation)

    @router.get("/api/projects/{project_id}/investigations/{investigation_id}")
    def investigation_get(project_id: str, investigation_id: str, request: Request):
        _require_local_request(request)
        _registry_entry(project_id)
        return _public_envelope(_record(project_id, "investigations", investigation_id))

    @router.get("/api/projects/{project_id}/dna/{dna_id}/download")
    def dna_download(project_id: str, dna_id: str, request: Request):
        _require_local_request(request)
        _registry_entry(project_id)
        if not _DNA_ID.fullmatch(dna_id):
            raise HTTPException(status_code=404, detail="unknown_artifact")
        record = _record(project_id, "dna", dna_id)
        return JSONResponse(
            record["payload"],
            headers={
                "Content-Disposition": f'attachment; filename="{dna_id}.json"',
                "Cache-Control": "no-store",
            },
        )

    def _start(
        request: Request,
        background_tasks: BackgroundTasks,
        project_id: str,
        operation: str,
        *,
        scenario_id: str | None = None,
        dna_payload: dict[str, Any] | None = None,
        candidate_config: Path | None = None,
        candidate_project_id: str | None = None,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        _, loaded = _load_registered_project(project_id)
        return _queue_operation(
            app=app,
            request=request,
            background_tasks=background_tasks,
            project_id=project_id,
            operation=operation,
            config=loaded.path,
            scenario_id=scenario_id,
            dna_payload=dna_payload,
            candidate_config=candidate_config,
            candidate_project_id=candidate_project_id,
        )

    @router.post("/api/projects/{project_id}/doctor", status_code=202,
                 dependencies=[Depends(authorize)])
    async def project_doctor(
        project_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        body: EmptyRequest | None = None,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        return _start(request, background_tasks, project_id, "doctor")

    @router.post("/api/projects/{project_id}/investigations", status_code=202,
                 dependencies=[Depends(authorize)])
    async def investigation_start(
        project_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        body: InvestigationRequest | None = None,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        _, loaded = _load_registered_project(project_id)
        scenario_id = body.scenario_id if body else None
        if scenario_id is not None and scenario_id not in {s.id for s in loaded.manifest.scenarios}:
            raise HTTPException(status_code=404, detail="unknown_scenario")
        return _start(request, background_tasks, project_id, "investigation", scenario_id=scenario_id)

    def _dna_for_action(project_id: str, dna_id: str) -> tuple[LoadedProject, dict[str, Any]]:
        _, loaded = _load_registered_project(project_id)
        if not _DNA_ID.fullmatch(dna_id):
            raise HTTPException(status_code=404, detail="unknown_artifact")
        record = _record(project_id, "dna", dna_id)
        return loaded, record["payload"]

    @router.post("/api/projects/{project_id}/dna/{dna_id}/replay", status_code=202,
                 dependencies=[Depends(authorize)])
    async def replay_start(
        project_id: str,
        dna_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        body: EmptyRequest | None = None,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        _, payload = _dna_for_action(project_id, dna_id)
        return _start(request, background_tasks, project_id, "replay", dna_payload=payload)

    @router.post("/api/projects/{project_id}/dna/{dna_id}/reduce", status_code=202,
                 dependencies=[Depends(authorize)])
    async def reduce_start(
        project_id: str,
        dna_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
        body: EmptyRequest | None = None,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        _, payload = _dna_for_action(project_id, dna_id)
        return _start(request, background_tasks, project_id, "reduce", dna_payload=payload)

    @router.post("/api/projects/{project_id}/dna/{dna_id}/verify", status_code=202,
                 dependencies=[Depends(authorize)])
    async def verify_start(
        project_id: str,
        dna_id: str,
        body: VerifyRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        _require_local_request(request, mutation=True)
        _execution_allowed(settings)
        _, payload = _dna_for_action(project_id, dna_id)
        candidate_entry = _registry_entry(body.candidate_project_id)
        try:
            candidate = load_project(Path(candidate_entry["config_path"]))
        except (OSError, ValueError, TypeError):
            raise HTTPException(status_code=409, detail="candidate_project_unavailable") from None
        if candidate.manifest.duress.project != body.candidate_project_id:
            raise HTTPException(status_code=409, detail="candidate_project_mismatch")
        return _start(
            request, background_tasks, project_id, "verify", dna_payload=payload,
            candidate_config=candidate.path, candidate_project_id=body.candidate_project_id,
        )

    app.include_router(router)
