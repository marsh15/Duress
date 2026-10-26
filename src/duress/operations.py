"""Scenario execution: trial loop, aggregation and evidence retention."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import limits as limits_module
from .aggregation import aggregate
from .expectations import response_semantics_mismatches
from .models import (
    BuiltScenario,
    ScenarioResult,
    TableSnapshot,
    TrialObservation,
)
from .oracle import evaluate_trial
from .profiles import TargetProfile, load_seed
from .provider_stub import StubServer
from .store import copy_evidence, duress_home, sweep
from .target import TrialConfig, run_trial


@dataclass
class OperationConfig:
    profile: TargetProfile
    scenario: BuiltScenario
    stub: StubServer
    workdir_root: Path
    policy: limits_module.Policy
    limits: limits_module.Limits = field(default_factory=lambda: limits_module.DEFAULT_LIMITS)
    home: Path | None = None
    keep_evidence: bool = True
    drain_verified: bool = True
    instance_override: str | None = None
    cleanup_passed: bool = True
    # verify-fix stage 3 stops at the first failure with the original signature.
    stop_on_signature: str | None = None


async def run_scenario(config: OperationConfig, *, wall_deadline: float | None = None) -> ScenarioResult:
    seed = load_seed(config.profile)
    baseline = TableSnapshot(tables=seed.tables())
    seed_order_ids = {row["id"] for row in seed.orders}
    home = config.home or duress_home(config.profile.cwd)

    verdicts = []
    observations: list[TrialObservation] = []
    budget_exhausted = False

    while True:
        valid = sum(1 for verdict in verdicts if verdict.status in {"PASS", "FAIL"})
        if valid >= config.policy.required_valid:
            break
        if len(verdicts) >= config.policy.max_attempts:
            break
        if wall_deadline is not None:
            if wall_deadline - time.monotonic() < config.limits.trial_start_margin_s:
                budget_exhausted = True
                break

        observation = await run_trial(
            TrialConfig(
                profile=config.profile,
                scenario=config.scenario,
                stub=config.stub,
                workdir_root=config.workdir_root,
                limits=config.limits,
                home=home,
                drain_verified=config.drain_verified,
                instance_override=config.instance_override,
                keep_evidence=config.keep_evidence,
            )
        )
        observation.response_semantics_mismatches = response_semantics_mismatches(
            observation.attempts,
            {
                delivery.id: delivery.effect_intent
                for delivery in config.scenario.schedule.deliveries
            },
        )
        verdict = evaluate_trial(
            observation,
            schedule=config.scenario.schedule,
            expectation=config.scenario.expectation,
            roles=config.scenario.roles,
            baseline=baseline,
            seed_order_ids=seed_order_ids,
        )
        observations.append(observation)
        verdicts.append(verdict)

        # An unconfirmed kill means the group may still be writing. The plan
        # aborts the operation until a sweep has reaped it.
        aborted = observation.settlement == "unconfirmed"
        if aborted:
            sweep(home)

        workdir = Path(observation.workdir) if observation.workdir else None
        if workdir is not None:
            if verdict.status == "PASS":
                if config.cleanup_passed:
                    shutil.rmtree(workdir, ignore_errors=True)
            elif config.keep_evidence:
                try:
                    copy_evidence(workdir, home, observation.trial_id)
                except OSError:
                    pass
                shutil.rmtree(workdir, ignore_errors=True)

        if config.stop_on_signature and verdict.status == "FAIL":
            if verdict.signature and verdict.signature.key == config.stop_on_signature:
                break

        if aborted:
            break

    result = aggregate(verdicts, config.policy, budget_exhausted=budget_exhausted)
    return ScenarioResult(
        template_id=config.scenario.template_id,
        scenario_id=config.scenario.scenario_id,
        params=config.scenario.params,
        result=result,
        verdicts=verdicts,
        observations=observations,
    )