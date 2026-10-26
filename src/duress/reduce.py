"""Bounded reduction of a Failure DNA schedule (plan C.11).

Greedy single-delivery removal in reverse delivery order, restarting the pass
after every accepted removal. A candidate is first checked statically (it may
be *inadmissible* and then runs no trials), then executed under the ``reduce``
policy (r = 3) and accepted only when every valid trial fails with the original
signature *and* records the original release condition.

What this establishes is narrow: a schedule that reproduced under the same
fault, expectation and invariant pack with fewer deliveries, and — only when
every admissible candidate was tested and none was unknown — 1-minimality with
respect to admissible single-delivery removal. It never claims global
minimality, and a reduced document is only primary after a separate ``replay``
re-validation.

Deviation from the plan's claim table: the "otherwise" row assumes something was
removed ("reduced; 1-minimality not established"). When nothing was removed and
the search was incomplete (budget exhausted, or an unknown candidate), that
wording would misreport the outcome, so ``CLAIM_NOT_REDUCED`` ("not reduced;
1-minimality not established") covers it.
"""

from __future__ import annotations

import copy
import dataclasses
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from pydantic import Field

from . import dna as dna_module
from . import limits as limits_module
from .dna import DnaError
from .limits import Limits
from .models import (
    BuiltScenario,
    DnaDocument,
    Expectation,
    FailureSignature,
    RendezvousFault,
    ScenarioResult,
    Schedule,
    Step,
    StrictModel,
    TrialObservation,
    canonical_dict,
    canonical_sha256,
)
from .operations import OperationConfig, run_scenario
from .preflight import preflight_async
from .profiles import TargetProfile
from .provider_stub import StubServer
from .schedule import _matching_deliveries, validate_schedule

CandidateExecutor = Callable[[BuiltScenario], Awaitable[ScenarioResult]]

C_MAX = 10  # 10 admissible candidates x 4 trials = 40 (limits.REDUCTION_TRIALS)

CLAIM_MINIMAL = (
    "1-minimal w.r.t. admissible single-delivery removal under P "
    "(r=3, all-fail, release-preserving)"
)
CLAIM_NONE = "no admissible reductions"
CLAIM_NOT_ESTABLISHED = "reduced; 1-minimality not established"
CLAIM_NOT_REDUCED = "not reduced; 1-minimality not established"

RELEASE_CONDITIONS = {"parties", "timeout", "none"}
MIN_P0 = 0.9


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
class CandidateRecord(StrictModel):
    index: int
    removed: str
    remaining: list[str]
    admissible: bool
    inadmissible_reasons: list[str] = Field(default_factory=list)
    verdict: Literal["accept", "reject", "unknown", "inadmissible"]
    reasons: list[str] = Field(default_factory=list)
    trials: int = 0
    budget_exhausted: bool = False
    valid: int = 0
    fail: int = 0
    passed: int = 0
    signatures: dict[str, int] = Field(default_factory=dict)
    release: dict[str, int] = Field(default_factory=dict)


class ReductionResult(StrictModel):
    status: Literal["reduced", "not_reduced", "no_admissible_reductions", "skipped"]
    skip_reason: str | None = None
    claim: str | None = None
    original_deliveries: list[str]
    reduced_deliveries: list[str]
    candidates: list[CandidateRecord] = Field(default_factory=list)
    admissible_tested: int = 0
    budget_exhausted: bool = False
    trials: int = 0
    reduced_schedule: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Preconditions and schedule surgery
# --------------------------------------------------------------------------- #
def preconditions(document: DnaDocument) -> str | None:
    """``None`` when reduction may run, otherwise the skip reason."""
    characterization = document.characterization or {}
    try:
        p0 = float(characterization.get("p0") or 0.0)
    except (TypeError, ValueError):
        p0 = 0.0
    if not math.isfinite(p0) or p0 < MIN_P0:
        return "p0<0.9"
    conditions = characterization.get("release_conditions") or {}
    observed = [key for key, count in conditions.items() if count]
    release_condition = document.reproducer.get("release_condition")
    if (
        len(observed) != 1
        or observed[0] == "mixed"
        or observed[0] != release_condition
    ):
        return "mixed_release"
    if release_condition not in RELEASE_CONDITIONS:
        return "mixed_release"
    return None


def remove_delivery(schedule: Schedule, delivery_id: str) -> Schedule:
    """Drop one delivery (and any step it empties); nothing else changes."""
    candidate = schedule.model_copy(deep=True)
    candidate.deliveries = [item for item in candidate.deliveries if item.id != delivery_id]
    steps: list[Step] = []
    for step in candidate.steps:
        remaining = [item for item in step.deliveries if item != delivery_id]
        if remaining:
            steps.append(step.model_copy(update={"deliveries": remaining}))
    candidate.steps = steps
    # Revalidate: an emptied schedule is not a Schedule.
    return Schedule.model_validate(canonical_dict(candidate))


def _targets(delivery, role: str) -> bool:
    return role in {delivery.event.order_role, delivery.event.payment_role}


def static_check(
    original: Schedule,
    candidate: Schedule,
    removed: str,
    *,
    signature: FailureSignature,
    expectation: Expectation,
    limits: Limits | None = None,
) -> list[str]:
    """Reasons a candidate is inadmissible; ``[]`` means it may run."""
    reasons: list[str] = []

    # (a) the signature's roles are still driven by a real-effect delivery.
    for role in signature.roles:
        if not any(
            _targets(delivery, role) and delivery.effect_intent != "none"
            for delivery in candidate.deliveries
        ):
            reasons.append(f"role_untargeted:{role}")

    # (b) preserve concurrency for each affected business role. An unrelated
    # delivery cannot stand in for a second delivery to that role.
    deliveries = {item.id: item for item in original.deliveries}
    for index, step in enumerate(original.steps):
        if removed not in step.deliveries:
            continue
        for role in signature.roles:
            matching = [item for item in step.deliveries if _targets(deliveries[item], role)]
            if len(matching) >= 2 and len([item for item in matching if item != removed]) < 2:
                reasons.append(f"concurrent_group_broken:step{index}")
                break

    # (c) nothing but the removed delivery changed.
    if [canonical_dict(item) for item in candidate.faults] != [
        canonical_dict(item) for item in original.faults
    ]:
        reasons.append("faults_changed")
    if canonical_dict(candidate.delivery_policy) != canonical_dict(original.delivery_policy):
        reasons.append("delivery_policy_changed")
    if [canonical_dict(item) for item in candidate.events] != [
        canonical_dict(item) for item in original.events
    ]:
        reasons.append("events_changed")
    kept = [canonical_dict(item) for item in original.deliveries if item.id != removed]
    if [canonical_dict(item) for item in candidate.deliveries] != kept:
        reasons.append("deliveries_changed")
    expected_steps: list[dict[str, Any]] = []
    for step in original.steps:
        remaining = [item for item in step.deliveries if item != removed]
        if remaining:
            expected_steps.append(
                canonical_dict(step.model_copy(update={"deliveries": remaining}))
            )
    if [canonical_dict(item) for item in candidate.steps] != expected_steps:
        reasons.append("steps_changed")

    # (d) rendezvous matching counts are preserved (>= parties).
    for fault in original.faults:
        if not isinstance(fault, RendezvousFault):
            continue
        for index, step in enumerate(original.steps):
            if not _matching_deliveries(original, step, fault.payments):
                continue
            survivors = [item for item in step.deliveries if item != removed]
            if not survivors:
                reasons.append(f"rendezvous_step_emptied:step{index}")
                continue
            count = len(
                _matching_deliveries(
                    original, Step(deliveries=survivors), fault.payments
                )
            )
            if count < fault.parties:
                reasons.append(
                    f"rendezvous_below_parties:step{index}:{count}<{fault.parties}"
                )

    # (e) the schedule still passes the C.7 validators.
    for error in validate_schedule(candidate, limits):
        reasons.append(f"invalid_schedule:{error}")

    # (f) the unchanged expectation must stay satisfiable.
    pinned: set[str] = set()
    for requirement in expectation.required_evidence:
        pinned.update(item for item in (requirement.delivery, requirement.other) if item)
    pinned.update(condition.delivery for condition in expectation.pass_conditions)
    if removed in pinned:
        reasons.append(f"pinned_by_expectation:{removed}")

    return reasons


# --------------------------------------------------------------------------- #
# Predicate
# --------------------------------------------------------------------------- #
def _release_problem(
    observation: TrialObservation, release_condition: str, parties: int | None
) -> str | None:
    released = observation.stub.released_by
    if release_condition == "parties":
        if released != "parties":
            return f"release_changed:{released or 'none'}"
        if parties is not None and observation.stub.arrivals < parties:
            return f"release_changed:arrivals={observation.stub.arrivals}<{parties}"
    elif release_condition == "timeout":
        if released != "timeout":
            return f"release_changed:{released or 'none'}"
    return None


def classify(
    result: ScenarioResult,
    *,
    signature: FailureSignature,
    release_condition: str,
    parties: int | None,
    required_valid: int = 3,
) -> tuple[Literal["accept", "reject", "unknown"], list[str]]:
    reject: list[str] = []
    unknown: list[str] = _trial_record_consistency_reasons(result)
    valid = 0
    for index, verdict in enumerate(result.verdicts):
        if verdict.status not in {"PASS", "FAIL"}:
            continue
        valid += 1
        if verdict.status == "PASS":
            reject.append("pass_observed")
            continue
        key = verdict.signature.key if verdict.signature else "none"
        if key != signature.key:
            reject.append(f"different_signature:{key}")
        if index >= len(result.observations):
            unknown.append(f"missing_observation:{index}")
            continue
        observation = result.observations[index]
        problem = _release_problem(observation, release_condition, parties)
        if problem:
            reject.append(problem)
    if reject:
        return "reject", list(dict.fromkeys(reject))
    if unknown:
        return "unknown", list(dict.fromkeys(unknown))
    if valid >= required_valid:
        return "accept", []
    return "unknown", [f"insufficient_valid:{valid}"]


def _trial_record_consistency_reasons(result: ScenarioResult) -> list[str]:
    """Reject incomplete or contradictory trial summaries as reduction evidence."""
    verdicts = result.verdicts
    aggregate = result.result
    valid = sum(verdict.status in {"PASS", "FAIL"} for verdict in verdicts)
    failed = sum(verdict.status == "FAIL" for verdict in verdicts)
    signatures = Counter(
        verdict.signature.key
        for verdict in verdicts
        if verdict.status == "FAIL" and verdict.signature is not None
    )
    reasons: list[str] = []
    if len(verdicts) != len(result.observations):
        reasons.append(
            f"trial_observation_mismatch:{len(verdicts)}:{len(result.observations)}"
        )
    if aggregate.trials != len(verdicts):
        reasons.append(f"trial_count_mismatch:{aggregate.trials}:{len(verdicts)}")
    if aggregate.valid != valid:
        reasons.append(f"valid_count_mismatch:{aggregate.valid}:{valid}")
    if aggregate.fail != failed:
        reasons.append(f"fail_count_mismatch:{aggregate.fail}:{failed}")
    if aggregate.signature_counts != dict(signatures):
        reasons.append("signature_count_mismatch")
    return reasons


def _parties_for(schedule: Schedule, signature: FailureSignature) -> int | None:
    faults = [item for item in schedule.faults if isinstance(item, RendezvousFault)]
    if not faults:
        return None
    if len(faults) == 1:
        return faults[0].parties
    roles = set(signature.roles)
    for fault in faults:
        if fault.payments is None or roles & set(fault.payments):
            return fault.parties
    return faults[0].parties


def _release_counts(result: ScenarioResult) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for index, verdict in enumerate(result.verdicts):
        if verdict.status in {"PASS", "FAIL"}:
            if index >= len(result.observations):
                counts["missing_observation"] += 1
            else:
                counts[result.observations[index].stub.released_by or "none"] += 1
    return dict(counts)


def _candidate_scenario(
    scenario: BuiltScenario, candidate: Schedule
) -> BuiltScenario:
    remaining = [item.id for item in candidate.deliveries]
    return dataclasses.replace(
        scenario,
        schedule=candidate,
        scenario_id=f"{scenario.scenario_id}~reduce[{','.join(remaining)}]",
    )


# --------------------------------------------------------------------------- #
# Greedy search
# --------------------------------------------------------------------------- #
async def reduce_schedule(
    document: DnaDocument,
    scenario: BuiltScenario,
    executor: CandidateExecutor,
    *,
    c_max: int = C_MAX,
    limits: Limits | None = None,
) -> ReductionResult:
    original = scenario.schedule
    original_ids = [item.id for item in original.deliveries]
    skip = preconditions(document)
    if skip:
        return ReductionResult(
            status="skipped",
            skip_reason=skip,
            original_deliveries=original_ids,
            reduced_deliveries=list(original_ids),
        )

    signature = document.signature()
    release_condition = str(document.reproducer.get("release_condition", "none"))
    parties = (
        _parties_for(original, signature) if release_condition == "parties" else None
    )
    required_valid = limits_module.POLICIES["reduce"].required_valid

    current = original
    records: list[CandidateRecord] = []
    admissible_tested = 0
    accepted = 0
    unknown = 0
    trials = 0
    exhausted = False
    done = False

    def log(**fields: Any) -> None:
        records.append(CandidateRecord(index=len(records), **fields))

    while not done:
        progressed = False
        seen: set[str] = set()
        for delivery_id in [item.id for item in reversed(current.deliveries)]:
            try:
                candidate = remove_delivery(current, delivery_id)
            except ValueError:
                log(
                    removed=delivery_id,
                    remaining=[],
                    admissible=False,
                    inadmissible_reasons=["no_deliveries_remaining"],
                    verdict="inadmissible",
                )
                continue
            key = canonical_sha256(canonical_dict(candidate))
            if key in seen:
                continue
            seen.add(key)
            remaining = [item.id for item in candidate.deliveries]
            blocked = static_check(
                current,
                candidate,
                delivery_id,
                signature=signature,
                expectation=scenario.expectation,
                limits=limits,
            )
            if blocked:
                log(
                    removed=delivery_id,
                    remaining=remaining,
                    admissible=False,
                    inadmissible_reasons=blocked,
                    verdict="inadmissible",
                )
                continue
            if admissible_tested >= c_max:
                exhausted = True
                done = True
                break
            admissible_tested += 1
            result = await executor(_candidate_scenario(scenario, candidate))
            verdict, reasons = classify(
                result,
                signature=signature,
                release_condition=release_condition,
                parties=parties,
                required_valid=required_valid,
            )
            trials += result.result.trials
            log(
                removed=delivery_id,
                remaining=remaining,
                admissible=True,
                verdict=verdict,
                reasons=reasons,
                trials=result.result.trials,
                budget_exhausted=result.result.budget_exhausted,
                valid=result.result.valid,
                fail=result.result.fail,
                passed=result.result.valid - result.result.fail,
                signatures=dict(result.result.signature_counts),
                release=_release_counts(result),
            )
            if result.result.budget_exhausted:
                exhausted = True
                done = True
                break
            if verdict == "accept":
                current = candidate
                accepted += 1
                progressed = True
                break
            if verdict == "unknown":
                unknown += 1
        if not progressed:
            done = True

    reduced_ids = [item.id for item in current.deliveries]
    if admissible_tested == 0:
        status: Literal["reduced", "not_reduced", "no_admissible_reductions", "skipped"]
        status, claim = "no_admissible_reductions", CLAIM_NONE
    elif not exhausted and unknown == 0:
        status = "reduced" if accepted else "not_reduced"
        claim = CLAIM_MINIMAL
    else:
        status = "reduced" if accepted else "not_reduced"
        claim = CLAIM_NOT_ESTABLISHED if accepted else CLAIM_NOT_REDUCED
    return ReductionResult(
        status=status,
        claim=claim,
        original_deliveries=original_ids,
        reduced_deliveries=reduced_ids,
        candidates=records,
        admissible_tested=admissible_tested,
        budget_exhausted=exhausted,
        trials=trials,
        reduced_schedule=canonical_dict(current) if accepted else None,
    )


# --------------------------------------------------------------------------- #
# Reduced document
# --------------------------------------------------------------------------- #
def build_reduced_document(
    original: DnaDocument,
    reduced: Schedule,
    *,
    reduction: dict[str, Any],
    characterization: dict[str, Any],
    observations: dict[str, Any] | None = None,
) -> DnaDocument:
    """A new DNA whose reproducer differs from the original only in ``schedule``."""
    reproducer = copy.deepcopy(original.reproducer)
    reproducer["schedule"] = canonical_dict(reduced)
    digest = canonical_sha256(reproducer)
    return DnaDocument(
        schema_version=1,
        dna_id=digest[:16],
        reproducer_sha256=digest,
        reproducer=reproducer,
        provenance_scenario=copy.deepcopy(original.provenance_scenario),
        environment=copy.deepcopy(original.environment),
        characterization=characterization,
        # The original's attempts name removed deliveries, so they are not carried over.
        observations=observations or {},
        reduction=reduction,
        provenance={
            **copy.deepcopy(original.provenance),
            "reduced_from": original.dna_id,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )


def reduction_summary(result: ReductionResult) -> dict[str, Any]:
    policy = limits_module.POLICIES["reduce"]
    summary = result.model_dump(mode="json", exclude={"reduced_schedule"})
    summary["policy"] = {
        "name": policy.name,
        "r": policy.required_valid,
        "max": policy.max_attempts,
        "c_max": C_MAX,
    }
    return summary


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class ReductionRun:
    outcome: Literal["REDUCED", "NOT_REDUCED", "SKIPPED", "REJECTED", "INCONCLUSIVE"]
    reason: str | None
    original_dna_id: str
    result: ReductionResult | None = None
    reduced_document: DnaDocument | None = None
    revalidation: dict[str, Any] | None = None
    primary_dna_id: str | None = None
    black_box: dict[str, Any] | None = None
    preflight: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "original_dna_id": self.original_dna_id,
            "primary_dna_id": self.primary_dna_id,
            "reduced_dna_id": (
                self.reduced_document.dna_id if self.reduced_document else None
            ),
            "claim": self.result.claim if self.result else None,
            "result": self.result.model_dump(mode="json") if self.result else None,
            "reduced_document": (
                self.reduced_document.model_dump(mode="json")
                if self.reduced_document
                else None
            ),
            "revalidation": self.revalidation,
            "black_box": self.black_box,
            "preflight": self.preflight,
        }


def _revalidation_summary(
    result: ScenarioResult,
    *,
    signature: FailureSignature,
    release_condition: str,
    parties: int | None,
    required_valid: int,
) -> dict[str, Any]:
    valid = result.result.valid
    same = result.result.signature_counts.get(signature.key, 0)
    p = round(same / valid, 4) if valid else 0.0
    release_counts = _release_counts(result)
    problems: list[str] = []
    problems.extend(_trial_record_consistency_reasons(result))
    for index, verdict in enumerate(result.verdicts):
        if verdict.status in {"PASS", "FAIL"}:
            if index >= len(result.observations):
                problems.append(f"missing_observation:{index}")
                continue
            observation = result.observations[index]
            problem = _release_problem(observation, release_condition, parties)
            if problem:
                problems.append(problem)
    uniform = len(release_counts) <= 1 and not problems
    reason: str | None = None
    if problems:
        reason = problems[0]
    elif valid < required_valid:
        reason = f"insufficient_valid:{valid}"
    elif p < MIN_P0:
        reason = f"p<0.9:{p}"
    return {
        "policy": "replay",
        "trials": result.result.trials,
        "budget_exhausted": result.result.budget_exhausted,
        "valid": valid,
        "fail": result.result.fail,
        "same_signature": same,
        "p": p,
        "release_conditions": release_counts,
        "uniform_release": uniform,
        "accepted": reason is None,
        "reason": reason,
    }


async def reduce_document(
    payload: Any,
    *,
    dna_id: str,
    profile: TargetProfile,
    stub: StubServer,
    workdir_root: Path,
    home: Path | None = None,
    limits: Limits | None = None,
    executor: CandidateExecutor | None = None,
    black_box: bool = True,
    wall_deadline: float | None = None,
) -> ReductionRun:
    limits = limits or limits_module.DEFAULT_LIMITS
    pre = await preflight_async(payload, profile=profile, limits=limits)
    if pre.rejected:
        return ReductionRun(
            outcome="REJECTED",
            reason=pre.rejected.reason,
            original_dna_id=dna_id,
            preflight=pre.rejected.model_dump(mode="json"),
        )
    document = pre.document
    scenario = pre.scenario
    assert document is not None and scenario is not None
    preflight_ok = {"ok": True}

    def configured(candidate: BuiltScenario, policy_name: str) -> OperationConfig:
        return OperationConfig(
            profile=profile,
            scenario=candidate,
            stub=stub,
            workdir_root=workdir_root,
            policy=limits_module.POLICIES[policy_name],
            limits=limits,
            home=home,
            keep_evidence=True,
        )

    async def execute(candidate: BuiltScenario, policy: str) -> ScenarioResult:
        kwargs = {"wall_deadline": wall_deadline} if wall_deadline is not None else {}
        return await run_scenario(configured(candidate, policy), **kwargs)

    async def default_executor(candidate: BuiltScenario) -> ScenarioResult:
        return await execute(candidate, "reduce")

    try:
        result = await reduce_schedule(
            document, scenario, executor or default_executor, limits=limits
        )
        black_box_record: dict[str, Any] | None = None
        if black_box and result.status != "skipped":
            black_box_record = await _black_box(
                document, scenario, configured, wall_deadline=wall_deadline
            )
    except DnaError as exc:
        return ReductionRun(
            outcome="INCONCLUSIVE",
            reason=str(exc),
            original_dna_id=dna_id,
            preflight=preflight_ok,
        )

    run = ReductionRun(
        outcome="NOT_REDUCED",
        reason=result.claim,
        original_dna_id=dna_id,
        result=result,
        primary_dna_id=document.dna_id,
        black_box=black_box_record,
        preflight=preflight_ok,
    )
    if result.status == "skipped":
        run.outcome = "SKIPPED"
        run.reason = result.skip_reason
        return run
    if result.status != "reduced":
        if any(item.budget_exhausted for item in result.candidates):
            run.outcome = "INCONCLUSIVE"
            run.reason = "budget_exhausted"
        return run

    reduced_schedule = Schedule.model_validate(result.reduced_schedule)
    summary = reduction_summary(result)
    provisional = build_reduced_document(
        document,
        reduced_schedule,
        reduction=summary,
        characterization={"revalidated": False},
    )
    reduced_pre = await preflight_async(
        provisional.model_dump(mode="json"), profile=profile, limits=limits
    )
    if reduced_pre.rejected or reduced_pre.scenario is None:
        reason = reduced_pre.rejected.reason if reduced_pre.rejected else "no_scenario"
        run.outcome = "INCONCLUSIVE"
        run.reason = f"reduced_preflight:{reason}"
        return run

    signature = document.signature()
    release_condition = str(document.reproducer.get("release_condition", "none"))
    parties = (
        _parties_for(reduced_schedule, signature)
        if release_condition == "parties"
        else None
    )
    replay_policy = limits_module.POLICIES["replay"]
    try:
        replayed = await execute(reduced_pre.scenario, "replay")
    except DnaError as exc:
        run.outcome = "INCONCLUSIVE"
        run.reason = f"revalidation:{exc}"
        return run
    revalidation = _revalidation_summary(
        replayed,
        signature=signature,
        release_condition=release_condition,
        parties=parties,
        required_valid=replay_policy.required_valid,
    )
    characterization = {
        "p0": revalidation["p"],
        "valid": revalidation["valid"],
        "fail": revalidation["fail"],
        "release_conditions": revalidation["release_conditions"],
        "trial_policy": "replay",
        "revalidated": True,
    }
    if black_box_record is not None:
        characterization["black_box_rate"] = black_box_record["rate"]
    run.reduced_document = build_reduced_document(
        document,
        reduced_schedule,
        reduction=summary,
        characterization=characterization,
        observations=_observations_of(replayed, signature, revalidation),
    )
    run.revalidation = revalidation
    if revalidation["accepted"]:
        run.outcome = "REDUCED"
        run.reason = result.claim
        run.primary_dna_id = run.reduced_document.dna_id
    else:
        run.outcome = "INCONCLUSIVE"
        run.reason = revalidation["reason"] or "revalidation_not_established"
        run.primary_dna_id = document.dna_id
    return run


def _observations_of(
    replayed: ScenarioResult, signature: FailureSignature, revalidation: dict[str, Any]
) -> dict[str, Any]:
    try:
        index = dna_module._representative_index(replayed, signature)
    except DnaError:
        return {}
    characterisation = dna_module.Characterisation(
        scenario_result=replayed,
        signature=signature,
        valid=revalidation["valid"],
        fail=revalidation["fail"],
        p0=revalidation["p"],
        release_conditions=revalidation["release_conditions"],
        representative_trial=index,
    )
    return dna_module._observation_summary(characterisation)


async def _black_box(
    document: DnaDocument,
    scenario: BuiltScenario,
    configured: Callable[[BuiltScenario, str], OperationConfig],
    *,
    wall_deadline: float | None = None,
) -> dict[str, Any]:
    """Characterisation only: the original deliveries with every fault removed."""
    schedule = scenario.schedule.model_copy(deep=True)
    schedule.faults = []
    bare = dataclasses.replace(
        scenario, schedule=schedule, scenario_id=f"{scenario.scenario_id}~black_box"
    )
    kwargs = {"wall_deadline": wall_deadline} if wall_deadline is not None else {}
    result = await run_scenario(configured(bare, "characterize"), **kwargs)
    signature = document.signature()
    valid = result.result.valid
    same = result.result.signature_counts.get(signature.key, 0)
    return {
        "policy": "characterize",
        "trials": result.result.trials,
        "budget_exhausted": result.result.budget_exhausted,
        "valid": valid,
        "fail": result.result.fail,
        "same_signature": same,
        "rate": f"{same}/{valid}",
    }
