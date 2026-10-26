"""Replay a DNA against a managed target (plan C.10).

Replay is the same trusted execution path as an investigation: fresh process
group, fresh database, fresh port and secret, whole invariant pack evaluated.
The only thing the artifact supplies is the schedule, fixtures and expectation,
and preflight has already checked all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import limits as limits_module
from .dna import DnaError, control_level
from .models import DnaDocument, ReplayOutcome, ScenarioResult
from .operations import OperationConfig, run_scenario
from .preflight import PreflightOptions, preflight_async
from .profiles import ProfileRegistry
from .provider_stub import StubServer


@dataclass
class ReplayRun:
    outcome: ReplayOutcome
    result: ScenarioResult | None = None
    preflight: dict[str, Any] | None = None


def _counts(result: ScenarioResult | None) -> dict[str, int]:
    if result is None:
        return {"valid": 0, "fail": 0, "inconclusive": 0, "trials": 0}
    return {
        "valid": result.result.valid,
        "fail": result.result.fail,
        "inconclusive": result.result.inconclusive,
        "trials": result.result.trials,
    }


async def replay_document(
    payload: Any,
    *,
    dna_id: str,
    profile,
    registry: ProfileRegistry,
    stub: StubServer,
    workdir_root: Path,
    home: Path | None = None,
    limits: limits_module.Limits | None = None,
    policy: str = "replay",
    options: PreflightOptions | None = None,
    public_key: str | None = None,
) -> ReplayRun:
    preflight = await preflight_async(
        payload, profile=profile, limits=limits, options=options
    )
    if preflight.rejected:
        return ReplayRun(
            outcome=ReplayOutcome(
                outcome="REJECTED",
                reason=preflight.rejected.reason,
                dna_id=dna_id,
                target_id=profile.target_id,
            ),
            preflight=preflight.rejected.model_dump(mode="json"),
        )

    document: DnaDocument = preflight.document  # type: ignore[assignment]
    scenario = preflight.scenario
    assert scenario is not None
    original = document.signature()
    try:
        result = await run_scenario(
            OperationConfig(
                profile=profile,
                scenario=scenario,
                stub=stub,
                workdir_root=workdir_root,
                policy=limits_module.POLICIES[policy],
                limits=limits or limits_module.DEFAULT_LIMITS,
                home=home,
                keep_evidence=True,
            )
        )
    except DnaError as exc:
        return ReplayRun(
            outcome=ReplayOutcome(
                outcome="INCONCLUSIVE",
                reason=str(exc),
                dna_id=dna_id,
                target_id=profile.target_id,
            ),
            preflight={"ok": True},
        )

    counts = _counts(result)
    same = result.result.signature_counts.get(original.key, 0)
    base = dict(
        dna_id=dna_id,
        target_id=profile.target_id,
        signature_matched=bool(same),
        **counts,
        reasons=list(result.result.reasons),
    )
    if result.result.status == "PASS":
        outcome = ReplayOutcome(outcome="NOT_REPRODUCED", **base)
    elif result.result.status == "FAIL" and same:
        outcome = ReplayOutcome(outcome="REPRODUCED", **base)
    elif result.result.status == "FAIL":
        outcome = ReplayOutcome(
            outcome="DIFFERENT_FAILURE",
            reason=(
                result.result.signature.key
                if result.result.signature
                else "failure_without_signature"
            ),
            **base,
        )
    else:
        outcome = ReplayOutcome(
            outcome="INCONCLUSIVE",
            reason=",".join(result.result.reasons) or "inconclusive",
            **base,
        )
    return ReplayRun(
        outcome=outcome,
        result=result,
        preflight={"ok": True, "control_level": control_level(scenario.schedule)},
    )


def replay_exit_code(outcome: ReplayOutcome) -> int:
    return {
        "REPRODUCED": 1,
        "DIFFERENT_FAILURE": 1,
        "NOT_REPRODUCED": 0,
        "INCONCLUSIVE": 2,
        "REJECTED": 4,
    }[outcome.outcome]