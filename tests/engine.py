"""Helpers for driving the M1 engine in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from duress import limits as limits_module
from duress.models import BuiltScenario, ScenarioResult
from duress.operations import OperationConfig, run_scenario
from duress.profiles import ProfileRegistry, load_seed
from duress.provider_stub import StubServer
from duress.scenarios import build_scenario


def built(
    registry: ProfileRegistry,
    target_id: str,
    template_id: str,
    params: dict[str, Any] | None = None,
    *,
    limits: limits_module.Limits | None = None,
) -> BuiltScenario:
    profile = registry.get(target_id)
    seed = load_seed(profile)
    return build_scenario(
        template_id,
        params or {},
        profile.roles,
        seed,
        limits=limits,
    )


async def run_built(
    registry: ProfileRegistry,
    stub: StubServer,
    workdir: Path,
    target_id: str,
    scenario: BuiltScenario,
    *,
    policy: str = "explore",
    limits: limits_module.Limits | None = None,
    home: Path | None = None,
    keep_evidence: bool = True,
    cleanup_passed: bool = False,
    instance_override: str | None = None,
    drain_verified: bool = True,
) -> ScenarioResult:
    profile = registry.get(target_id)
    workdir.mkdir(parents=True, exist_ok=True)
    return await run_scenario(
        OperationConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=workdir,
            policy=limits_module.POLICIES[policy],
            limits=limits or limits_module.DEFAULT_LIMITS,
            home=home or workdir,
            keep_evidence=keep_evidence,
            cleanup_passed=cleanup_passed,
            instance_override=instance_override,
            drain_verified=drain_verified,
        )
    )