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