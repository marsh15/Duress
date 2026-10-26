"""One execution path shared by the CLI and the HTTP API.

The CLI and the web application must not grow separate copies of the trusted
execution logic: both call these functions, which own the stub lifecycle, the
pidfile sweep and the persistence of artifacts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import dna as dna_module
from . import limits as limits_module
from .models import DnaDocument, ScenarioResult
from .operations import OperationConfig, run_scenario
from .profiles import ProfileRegistry, load_seed, repo_root
from .provider_stub import StubServer
from .replay import ReplayRun, replay_document
from .scenarios import build_scenario
from .store import (
    duress_home,
    ensure_dirs,
    list_dna,
    load_dna_payload,
    save_dna,
    sweep,
)
from .verify import VerifyRun, verify_document


@dataclass(frozen=True)
class Workspace:
    root: Path
    home: Path
    registry: ProfileRegistry
    limits: limits_module.Limits

    @classmethod
    def discover(cls, root: Path | None = None) -> "Workspace":
        resolved = Path(root or repo_root(Path.cwd())).resolve()
        return cls(
            root=resolved,
            home=duress_home(resolved),
            registry=ProfileRegistry.discover(resolved),
            limits=limits_module.DEFAULT_LIMITS,
        )

    def prepare(self) -> None:
        ensure_dirs(self.home)
        (self.home / "tmp").mkdir(parents=True, exist_ok=True)

    @property
    def workdir(self) -> Path:
        return self.home / "tmp"


def _finish(workspace: Workspace) -> None:
    sweep(workspace.home)


async def run_target_scenario(
    workspace: Workspace,
    *,
    target_id: str,
    template_id: str,
    params: dict[str, Any] | None = None,
    policy: str = "explore",
) -> ScenarioResult:
    profile = workspace.registry.get(target_id)
    seed = load_seed(profile)
    scenario = build_scenario(template_id, params or {}, profile.roles, seed)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        return await run_scenario(
            OperationConfig(
                profile=profile,
                scenario=scenario,
                stub=stub,
                workdir_root=workspace.workdir,
                policy=limits_module.POLICIES[policy],
                limits=workspace.limits,
                home=workspace.home,
                keep_evidence=True,
            )
        )


async def build_dna(
    workspace: Workspace,
    *,
    target_id: str,
    template_id: str,
    params: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> tuple[DnaDocument, dna_module.Characterisation]:
    """Characterise a failing scenario and persist the resulting DNA."""
    profile = workspace.registry.get(target_id)
    seed = load_seed(profile)
    scenario = build_scenario(template_id, params or {}, profile.roles, seed)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        document, characterisation = await dna_module.build_dna(
            profile,
            scenario,
            stub,
            workdir_root=workspace.workdir,
            home=workspace.home,
            limits=workspace.limits,
            provenance=provenance,
        )
    save_dna(workspace.home, document)
    return document, characterisation


def load_dna_document(workspace: Workspace, dna_id: str) -> DnaDocument:
    return dna_module.document_from_json(load_dna_payload(workspace.home, dna_id))


def dna_index(workspace: Workspace) -> list[dict[str, Any]]:
    workspace.prepare()
    return list_dna(workspace.home)


async def replay(
    workspace: Workspace,
    *,
    dna_id: str,
    target_id: str,
    policy: str = "replay",
    remote_secret_required: bool = False,
) -> ReplayRun:
    from .preflight import PreflightOptions

    payload = load_dna_payload(workspace.home, dna_id)
    profile = workspace.registry.get(target_id)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        return await replay_document(
            payload,
            dna_id=dna_id,
            profile=profile,
            registry=workspace.registry,
            stub=stub,
            workdir_root=workspace.workdir,
            home=workspace.home,
            limits=workspace.limits,
            policy=policy,
            options=PreflightOptions(secret_required=remote_secret_required),
        )


async def replay_payload(
    workspace: Workspace,
    *,
    payload: Any,
    dna_id: str,
    target_id: str,
    policy: str = "replay",
    remote_secret_required: bool = False,
) -> ReplayRun:
    from .preflight import PreflightOptions

    profile = workspace.registry.get(target_id)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        return await replay_document(
            payload,
            dna_id=dna_id,
            profile=profile,
            registry=workspace.registry,
            stub=stub,
            workdir_root=workspace.workdir,
            home=workspace.home,
            limits=workspace.limits,
            policy=policy,
            options=PreflightOptions(secret_required=remote_secret_required),
        )


async def verify_fix(
    workspace: Workspace,
    *,
    dna_id: str,
    vulnerable_target_id: str,
    candidate_target_id: str,
) -> VerifyRun:
    payload = load_dna_payload(workspace.home, dna_id)
    vulnerable = workspace.registry.get(vulnerable_target_id)
    candidate = workspace.registry.get(candidate_target_id)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        return await verify_document(
            payload,
            dna_id=dna_id,
            vulnerable_profile=vulnerable,
            candidate_profile=candidate,
            stub=stub,
            workdir_root=workspace.workdir,
            home=workspace.home,
            limits=workspace.limits,
        )


def default_workspace() -> Workspace:
    return Workspace.discover()


def hosted_mode() -> bool:
    return os.environ.get("DURESS_HOSTED", "").lower() in {"1", "true", "yes"}

# --------------------------------------------------------------------------- #
# M3: investigations
# --------------------------------------------------------------------------- #
async def investigate(
    workspace: Workspace,
    *,
    target_id: str,
    mode: str,
    investigation_id: str | None = None,
    reduce: bool = False,
    llm_config: Any = "env",
    llm_transport: Any = None,
    cache: Any = None,
    wall_cap_s: float | None = None,
) -> dict[str, Any]:
    """Run one investigation to completion and return its persisted record."""
    from .llm import LlmConfig
    from .orchestrator import InvestigationContext, run_investigation

    profile = workspace.registry.get(target_id)
    config = LlmConfig.from_env() if llm_config == "env" else llm_config
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:

        async def reducer(payload: dict[str, Any], *, wall_deadline: float) -> dict[str, Any]:
            run = await _reduce_payload(
                workspace, payload=payload, profile=profile, stub=stub, black_box=True,
                wall_deadline=wall_deadline,
            )
            return reduction_summary(run)

        context = InvestigationContext(
            profile=profile,
            home=workspace.home,
            workdir_root=workspace.workdir,
            stub=stub,
            root=workspace.root,
            limits=workspace.limits,
            llm_config=config,
            llm_transport=llm_transport,
            cache=cache,
            wall_cap_s=wall_cap_s,
            reducer=reducer if reduce else None,
        )
        try:
            return await run_investigation(
                context, mode=mode, investigation_id=investigation_id, reduce=reduce
            )
        finally:
            _finish(workspace)


def load_investigation(workspace: Workspace, investigation_id: str) -> dict[str, Any]:
    from .orchestrator import load_investigation as load

    return load(workspace.home, investigation_id)


def investigation_list(workspace: Workspace) -> list[dict[str, Any]]:
    from .store import read_json

    workspace.prepare()
    items = []
    for path in sorted((workspace.home / "investigations").glob("*.json")):
        try:
            record = read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("investigation_id") != path.stem:
            continue
        items.append(
            {
                key: record.get(key)
                for key in (
                    "investigation_id",
                    "record_type",
                    "created_at",
                    "updated_at",
                    "status",
                    "phase",
                    "target_id",
                    "mode_requested",
                    "mode_effective",
                    "fallback_reason",
                    "verdict",
                    "dna_ids",
                    "trials_used",
                )
            }
        )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


# --------------------------------------------------------------------------- #
# M4: reduction and differential evaluation
# --------------------------------------------------------------------------- #
def reduction_summary(run: Any) -> dict[str, Any]:
    payload = run.to_json()
    result = payload.get("result") or {}
    return {
        "original_dna_id": run.original_dna_id,
        "outcome": run.outcome,
        "reason": run.reason,
        "claim": result.get("claim"),
        "status": result.get("status"),
        "reduced_dna_id": run.reduced_document.dna_id if run.reduced_document else None,
        "primary_dna_id": run.primary_dna_id,
        "trials": int(result.get("trials") or 0)
        + int((run.revalidation or {}).get("trials") or 0)
        + int((run.black_box or {}).get("trials") or 0),
        "reduction_id": getattr(run, "reduction_id", None),
        "black_box_rate": (run.black_box or {}).get("rate"),
        "budget_exhausted": bool(result.get("budget_exhausted"))
        or bool((run.revalidation or {}).get("budget_exhausted"))
        or bool((run.black_box or {}).get("budget_exhausted")),
    }


async def _reduce_payload(
    workspace: Workspace,
    *,
    payload: Any,
    profile: Any,
    stub: StubServer,
    black_box: bool,
    reduction_id: str | None = None,
    wall_deadline: float | None = None,
) -> Any:
    """Reduce, persist every produced artifact, and write the reduction record."""
    import uuid
    from datetime import datetime, timezone

    from .reduce import reduce_document
    from .store import atomic_write_json

    dna_id = str(payload.get("dna_id") or "submitted")
    run = await reduce_document(
        payload,
        dna_id=dna_id,
        profile=profile,
        stub=stub,
        workdir_root=workspace.workdir,
        home=workspace.home,
        limits=workspace.limits,
        black_box=black_box,
        wall_deadline=wall_deadline,
    )
    reduction_id = reduction_id or f"red-{uuid.uuid4().hex[:12]}"
    run.reduction_id = reduction_id  # type: ignore[attr-defined]
    if run.reduced_document is not None:
        save_dna(workspace.home, run.reduced_document)
    record = {
        "record_type": "reduction",
        "reduction_id": reduction_id,
        "status": "completed",
        "target_id": profile.target_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **run.to_json(),
        "summary": reduction_summary(run),
    }
    atomic_write_json(workspace.home / "reductions" / f"{reduction_id}.json", record)
    # The original's ``reduction`` field is outside the hashed reproducer, so a
    # pointer can be recorded without changing its identity.
    try:
        original = load_dna_payload(workspace.home, dna_id)
    except FileNotFoundError:
        original = None
    if isinstance(original, dict):
        if run.black_box is not None:
            original.setdefault("characterization", {})["black_box_rate"] = run.black_box["rate"]
        original["reduction"] = {
            "role": "original",
            "reduction_id": reduction_id,
            "outcome": run.outcome,
            "claim": record["summary"]["claim"],
            "reduced_dna_id": record["summary"]["reduced_dna_id"],
            "primary_dna_id": run.primary_dna_id,
        }
        save_dna(workspace.home, original)
    return run


async def reduce_dna(
    workspace: Workspace,
    *,
    dna_id: str,
    target_id: str | None = None,
    black_box: bool = True,
    reduction_id: str | None = None,
) -> Any:
    payload = load_dna_payload(workspace.home, dna_id)
    resolved = target_id or str(
        (payload.get("provenance") or {}).get("target_id") or "razorpay-vulnerable"
    )
    profile = workspace.registry.get(resolved)
    workspace.prepare()
    _finish(workspace)
    with StubServer() as stub:
        try:
            return await _reduce_payload(
                workspace,
                payload=payload,
                profile=profile,
                stub=stub,
                black_box=black_box,
                reduction_id=reduction_id,
            )
        finally:
            _finish(workspace)


def load_reduction(workspace: Workspace, reduction_id: str) -> dict[str, Any]:
    from .store import read_json

    path = workspace.home / "reductions" / f"{reduction_id}.json"
    if not path.exists():
        raise FileNotFoundError(reduction_id)
    return read_json(path)


async def differential(
    workspace: Workspace,
    *,
    target_ids: list[str] | None = None,
    specs: list[Any] | None = None,
    policy: str = "explore",
    differential_id: str | None = None,
) -> dict[str, Any]:
    """Run each scenario on each target and compare with the plan's matrix."""
    import uuid
    from datetime import datetime, timezone

    from . import differential as diff
    from .store import atomic_write_json

    targets = list(target_ids or diff.DEFAULT_TARGETS)
    chosen = list(specs or diff.DEFAULT_SPECS)
    profiles = [workspace.registry.get(item) for item in targets]
    differential_id = differential_id or f"diff-{uuid.uuid4().hex[:12]}"
    path = workspace.home / "differentials" / f"{differential_id}.json"
    record: dict[str, Any] = {
        "record_type": "differential",
        "differential_id": differential_id,
        "status": "running",
        "policy": policy,
        "targets": targets,
        "scenarios": [],
        "cells": [],
        "summary": None,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    workspace.prepare()
    _finish(workspace)
    atomic_write_json(path, record)
    try:
        with StubServer() as stub:
            for spec in chosen:
                for profile in profiles:
                    seed = load_seed(profile)
                    scenario = spec.build(profile.roles, seed)
                    if scenario.scenario_id not in record["scenarios"]:
                        record["scenarios"].append(scenario.scenario_id)
                    result = await run_scenario(
                        OperationConfig(
                            profile=profile,
                            scenario=scenario,
                            stub=stub,
                            workdir_root=workspace.workdir,
                            policy=limits_module.POLICIES[policy],
                            limits=workspace.limits,
                            home=workspace.home,
                            keep_evidence=True,
                        )
                    )
                    record["cells"].append(
                        diff.cell(
                            scenario_id=scenario.scenario_id,
                            target_id=profile.target_id,
                            variant=profile.variant,
                            result=result.result.model_dump(mode="json"),
                        )
                    )
                    atomic_write_json(path, record)
        record["status"] = "completed"
    except Exception as exc:
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record["summary"] = diff.summarise(record["cells"])
        atomic_write_json(path, record)
        _finish(workspace)
    return record


def load_differential(workspace: Workspace, differential_id: str) -> dict[str, Any]:
    from .store import read_json

    path = workspace.home / "differentials" / f"{differential_id}.json"
    if not path.exists():
        raise FileNotFoundError(differential_id)
    return read_json(path)
