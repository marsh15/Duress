"""verify-fix: an empirical, control-calibrated criterion (plan C.10).

Not a proof. Stage 1 rejects untrusted artifacts, stage 2 re-establishes the
failure on the vulnerable target, and stage 3 sizes an adequate candidate run
from the observed reproduction rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import limits as limits_module
from .models import (
    DnaDocument,
    FailureSignature,
    ScenarioResult,
    VerifyOutcome,
)
from .operations import OperationConfig, run_scenario
from .preflight import PreflightOptions, preflight_async
from .provider_stub import StubServer
from .replay import replay_exit_code  # noqa: F401  (kept for callers)

MAX_CANDIDATE_TRIALS = 44


@dataclass
class VerifyRun:
    outcome: VerifyOutcome
    control_result: ScenarioResult | None = None
    candidate_result: ScenarioResult | None = None
    preflight: dict[str, Any] = field(default_factory=dict)


def candidate_size(p_hat: float) -> int | None:
    """n_c = max(20, ceil(ln 0.01 / ln(1 - p_a))); None when > 44."""
    p_a = min(p_hat, 0.99)
    if p_a <= 0:
        return None
    required = math.ceil(math.log(0.01) / math.log(1 - p_a))
    n_c = max(20, required)
    return n_c if n_c <= MAX_CANDIDATE_TRIALS else None


def control_decision(
    result: ScenarioResult,
    *,
    p0: float,
    required_valid: int,
) -> dict[str, Any]:
    original = result.result.signature
    same = 0
    if original is not None:
        same = result.result.signature_counts.get(original.key, 0)
    valid = result.result.valid
    p_hat = (same / valid) if valid else 0.0
    reason: str | None = None
    if valid < required_valid:
        reason = "control_insufficient_valid"
    elif same == 0:
        reason = "control_not_reproduced"
    elif p_hat < p0 / 2:
        reason = "control_degraded"
    return {
        "valid": valid,
        "fail": result.result.fail,
        "inconclusive": result.result.inconclusive,
        "trials": result.result.trials,
        "same_signature_failures": same,
        "p_hat": round(p_hat, 4),
        "p0": p0,
        "required_valid": required_valid,
        "reason": reason,
    }


def candidate_decision(
    result: ScenarioResult,
    *,
    original: FailureSignature,
    n_c: int,
    allowance: int,
) -> dict[str, Any]:
    verdicts = result.verdicts
    same = [
        verdict
        for verdict in verdicts
        if verdict.status == "FAIL"
        and verdict.signature is not None
        and verdict.signature.key == original.key
    ]
    other = [
        verdict
        for verdict in verdicts
        if verdict.status == "FAIL"
        and (verdict.signature is None or verdict.signature.key != original.key)
    ]
    payload = {
        "valid": result.result.valid,
        "fail": result.result.fail,
        "inconclusive": result.result.inconclusive,
        "trials": result.result.trials,
        "same_signature_failures": len(same),
        "other_failures": len(other),
        "required_valid": n_c,
        "budget_exhausted": result.result.budget_exhausted,
        "reason": None,
        "outcome": None,
    }
    if same:
        payload["outcome"] = "NOT_VERIFIED"
        payload["reason"] = "still_reproduces"
    elif other:
        payload["outcome"] = "NOT_VERIFIED"
        payload["reason"] = "other_violation"
    elif result.result.valid < n_c:
        payload["outcome"] = "INCONCLUSIVE"
        payload["reason"] = "candidate_insufficient_valid"
    elif result.result.inconclusive > allowance:
        payload["outcome"] = "INCONCLUSIVE"
        payload["reason"] = "budget_exhausted"
    elif result.result.budget_exhausted:
        payload["outcome"] = "INCONCLUSIVE"
        payload["reason"] = "budget_exhausted"
    else:
        payload["outcome"] = "VERIFIED"
    return payload


def statement(
    *, k: int, v: int, n_c: int, p_a: float, vulnerable: str, candidate: str
) -> str:
    tail = (1 - p_a) ** n_c
    return (
        f"The failure reproduced in {k}/{v} valid control trials on {vulnerable}. "
        f"It was not observed in {n_c} valid trials on {candidate} under the same "
        "DNA conditions. "
        f"If {candidate} still failed independently with per-trial probability "
        f"{p_a:.4f}, the chance of seeing no failures in {n_c} valid trials would be "
        f"(1-{p_a:.4f})^{n_c} = {tail:.2e} <= 1%. "
        f"Under the same independence assumption, about {3 / n_c:.3f} is an "
        f"approximate 95% upper bound on {candidate}'s per-trial failure "
        "probability for this reproducer. This does not establish correctness for "
        "other schedules, loads or environments."
    )


def _inconclusive(
    dna_id: str, vulnerable: str, candidate: str, reason: str, **extra: Any
) -> VerifyOutcome:
    return VerifyOutcome(
        outcome="INCONCLUSIVE",
        reason=reason,
        dna_id=dna_id,
        vulnerable_target_id=vulnerable,
        candidate_target_id=candidate,
        **extra,
    )


async def verify_document(
    payload: Any,
    *,
    dna_id: str,
    vulnerable_profile,
    candidate_profile,
    stub: StubServer,
    workdir_root: Path,
    home: Path | None = None,
    limits: limits_module.Limits | None = None,
    options: PreflightOptions | None = None,
) -> VerifyRun:
    limits = limits or limits_module.DEFAULT_LIMITS
    vulnerable = vulnerable_profile.target_id
    candidate = candidate_profile.target_id

    # Stage 1: both sides must pass preflight before a single target starts.
    for profile in (vulnerable_profile, candidate_profile):
        check = await preflight_async(payload, profile=profile, limits=limits, options=options)
        if check.rejected:
            return VerifyRun(
                outcome=VerifyOutcome(
                    outcome="REJECTED",
                    reason=check.rejected.reason,
                    dna_id=dna_id,
                    vulnerable_target_id=vulnerable,
                    candidate_target_id=candidate,
                ),
                preflight={"ok": False, "reason": check.rejected.reason},
            )

    document: DnaDocument = check.document  # type: ignore[assignment]
    scenario = check.scenario
    assert scenario is not None
    original = document.signature()
    p0 = float(document.characterization.get("p0") or 0.0)
    control_policy = limits_module.verify_control_policy(p0)

    # Stage 2: control on the vulnerable target.
    control = await run_scenario(
        OperationConfig(
            profile=vulnerable_profile,
            scenario=scenario,
            stub=stub,
            workdir_root=workdir_root,
            policy=control_policy,
            limits=limits,
            home=home,
            keep_evidence=True,
        )
    )
    control_stats = control_decision(
        control, p0=p0, required_valid=control_policy.required_valid
    )
    if control_stats["reason"]:
        return VerifyRun(
            outcome=_inconclusive(
                dna_id,
                vulnerable,
                candidate,
                control_stats["reason"],
                control=control_stats,
            ),
            control_result=control,
            preflight={"ok": True},
        )

    # Stage 3: size and run the candidate.
    p_hat = float(control_stats["p_hat"])
    n_c = candidate_size(p_hat)
    if n_c is None:
        return VerifyRun(
            outcome=_inconclusive(
                dna_id,
                vulnerable,
                candidate,
                "reproduction_too_unreliable",
                control=control_stats,
            ),
            control_result=control,
            preflight={"ok": True},
        )
    policy = limits_module.verify_candidate_policy(n_c)
    candidate_result = await run_scenario(
        OperationConfig(
            profile=candidate_profile,
            scenario=scenario,
            stub=stub,
            workdir_root=workdir_root,
            policy=policy,
            limits=limits,
            home=home,
            keep_evidence=True,
            stop_on_signature=original.key,
        )
    )
    stats = candidate_decision(
        candidate_result,
        original=original,
        n_c=n_c,
        allowance=policy.inconclusive_allowance,
    )
    text = statement(
        k=control_stats["same_signature_failures"],
        v=control_stats["valid"],
        n_c=n_c,
        p_a=min(p_hat, 0.99),
        vulnerable=vulnerable,
        candidate=candidate,
    )
    return VerifyRun(
        outcome=VerifyOutcome(
            outcome=stats.pop("outcome"),
            reason=stats.get("reason"),
            dna_id=dna_id,
            vulnerable_target_id=vulnerable,
            candidate_target_id=candidate,
            statement=text,
            control=control_stats,
            candidate=stats,
        ),
        control_result=control,
        candidate_result=candidate_result,
        preflight={"ok": True},
    )