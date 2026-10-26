"""Reduction (plan C.11) against a deterministic fake executor: no processes."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from duress import reduce as reduce_module
from duress import limits as limits_module
from duress.aggregation import aggregate
from duress.dna import build_reproducer, environment_ref
from duress.models import (
    BuiltScenario,
    DeliverySpec,
    DnaDocument,
    EvidenceRequirement,
    Expectation,
    EventSpec,
    FailureSignature,
    LatencyFault,
    PassCondition,
    RendezvousFault,
    ScenarioResult,
    Schedule,
    Step,
    StubObservation,
    TrialObservation,
    TrialVerdict,
    AttemptPolicy,
    canonical_dict,
    canonical_sha256,
)
from duress.preflight import preflight_payload
from duress.profiles import load_seed
from duress.reduce import (
    C_MAX,
    CLAIM_MINIMAL,
    CLAIM_NONE,
    CLAIM_NOT_ESTABLISHED,
    CLAIM_NOT_REDUCED,
    ReductionResult,
    _revalidation_summary,
    build_reduced_document,
    classify,
    preconditions,
    reduce_document,
    reduce_schedule,
    remove_delivery,
    static_check,
)
from duress.scenarios import build_scenario

SIGNATURE = FailureSignature(
    check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"]
)
OTHER = FailureSignature(
    check="INV-4@1", kind="captured_without_fulfilment", roles=["payment:A1"]
)
EVENT = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A1")


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def make_schedule(
    groups: list[list[str]],
    *,
    faults=None,
    intents: dict[str, str] | None = None,
) -> Schedule:
    intents = intents or {}
    ids = [item for group in groups for item in group]
    return Schedule(
        deliveries=[
            DeliverySpec(
                id=item,
                event=EVENT,
                effect_intent=intents.get(item, "capture_and_fulfil"),
                event_id="evt_1",
            )
            for item in ids
        ],
        steps=[Step(deliveries=list(group)) for group in groups],
        faults=list(faults or []),
        delivery_policy=AttemptPolicy(attempt_timeout_ms=1000),
    )


def make_expectation(*, pinned: tuple[str, ...] = (), other: str | None = None) -> Expectation:
    return Expectation(
        id="S3.exactly_once",
        required_evidence=[
            EvidenceRequirement(kind="group_overlap"),
            *[EvidenceRequirement(kind="answered", delivery=item) for item in pinned],
            *(
                [EvidenceRequirement(kind="send_after_end", delivery=pinned[0], other=other)]
                if other and pinned
                else []
            ),
        ],
    )


def make_scenario(schedule: Schedule, expectation: Expectation) -> BuiltScenario:
    return BuiltScenario(
        template_id="S3",
        template_version=1,
        scenario_id="S3[test]",
        params={},
        schedule=schedule,
        expectation=expectation,
        fixtures={},
        roles={"order:A": "order_001", "payment:A1": "pay_001"},
    )


def make_document(
    scenario: BuiltScenario,
    *,
    release_condition: str = "none",
    p0: float = 1.0,
    release_conditions: dict[str, int] | None = None,
) -> DnaDocument:
    reproducer = {
        "schedule": canonical_dict(scenario.schedule),
        "expectation": {"definition": scenario.expectation.definition()},
        "failure_signature": canonical_dict(SIGNATURE),
        "release_condition": release_condition,
    }
    digest = canonical_sha256(reproducer)
    return DnaDocument(
        dna_id=digest[:16],
        reproducer_sha256=digest,
        reproducer=reproducer,
        provenance_scenario={},
        environment={},
        characterization={
            "p0": p0,
            "valid": 10,
            "release_conditions": release_conditions or {release_condition: 10},
        },
    )


def trial(
    status: str,
    signature: FailureSignature | None = SIGNATURE,
    *,
    released_by: str | None = None,
    arrivals: int = 0,
) -> tuple[TrialVerdict, TrialObservation]:
    verdict = TrialVerdict(
        status=status,
        signature=signature if status == "FAIL" else None,
        reasons=[] if status != "INCONCLUSIVE" else ["harness:test"],
    )
    observation = TrialObservation(
        trial_id="t",
        target_id="fake",
        scenario_id="S3[test]",
        stub=StubObservation(arrivals=arrivals, released_by=released_by),
    )
    return verdict, observation


def make_result(trials: list[tuple[TrialVerdict, TrialObservation]]) -> ScenarioResult:
    verdicts = [item[0] for item in trials]
    return ScenarioResult(
        template_id="S3",
        scenario_id="S3[test]",
        result=aggregate(verdicts, limits_module.POLICIES["reduce"]),
        verdicts=verdicts,
        observations=[item[1] for item in trials],
    )


def accepted_result(
    *, signature=SIGNATURE, released_by=None, arrivals=0, count: int = 3
) -> ScenarioResult:
    return make_result(
        [trial("FAIL", signature, released_by=released_by, arrivals=arrivals)] * count
    )


def rejected_result() -> ScenarioResult:
    return make_result(
        [trial("FAIL")] * 2 + [trial("PASS")]
    )


def unknown_result() -> ScenarioResult:
    return make_result([trial("FAIL"), trial("INCONCLUSIVE"), trial("INCONCLUSIVE")])


class FakeExecutor:
    """Maps a candidate's remaining delivery set to a canned ScenarioResult."""

    def __init__(self, decide: Callable[[frozenset[str]], ScenarioResult]) -> None:
        self.decide = decide
        self.calls: list[tuple[str, ...]] = []

    async def __call__(self, scenario: BuiltScenario) -> ScenarioResult:
        remaining = tuple(item.id for item in scenario.schedule.deliveries)
        self.calls.append(remaining)
        return self.decide(frozenset(remaining))


def always(result_factory: Callable[[], ScenarioResult]) -> FakeExecutor:
    return FakeExecutor(lambda _remaining: result_factory())


# --------------------------------------------------------------------------- #
# classify
# --------------------------------------------------------------------------- #
def run_classify(result, *, release="none", parties=None, signature=SIGNATURE):
    return classify(
        result, signature=signature, release_condition=release, parties=parties
    )


def test_all_fail_same_signature_is_accepted():
    assert run_classify(accepted_result()) == ("accept", [])


def test_a_pass_rejects():
    verdict, reasons = run_classify(rejected_result())
    assert verdict == "reject" and reasons == ["pass_observed"]


def test_different_signature_is_rejected():
    verdict, reasons = run_classify(accepted_result(signature=OTHER))
    assert verdict == "reject"
    assert reasons == [f"different_signature:{OTHER.key}"]


def test_different_release_condition_is_rejected():
    result = accepted_result(released_by="timeout")
    verdict, reasons = run_classify(result, release="parties", parties=3)
    assert verdict == "reject" and reasons == ["release_changed:timeout"]
    verdict, reasons = run_classify(accepted_result(released_by="parties", arrivals=3), release="timeout")
    assert verdict == "reject" and reasons == ["release_changed:parties"]
    verdict, reasons = run_classify(accepted_result(released_by=None), release="parties", parties=3)
    assert verdict == "reject" and reasons == ["release_changed:none"]


def test_parties_release_with_too_few_arrivals_is_rejected():
    result = accepted_result(released_by="parties", arrivals=2)
    verdict, reasons = run_classify(result, release="parties", parties=3)
    assert verdict == "reject"
    assert reasons and reasons[0].startswith("release_changed:")
    assert run_classify(
        accepted_result(released_by="parties", arrivals=3), release="parties", parties=3
    ) == ("accept", [])


def test_timeout_release_accepts_timeout_and_none_requires_nothing():
    assert run_classify(accepted_result(released_by="timeout"), release="timeout")[0] == "accept"
    assert run_classify(accepted_result(released_by="timeout"), release="none")[0] == "accept"


def test_fewer_than_three_valid_trials_is_unknown():
    verdict, reasons = run_classify(unknown_result())
    assert verdict == "unknown" and reasons == ["insufficient_valid:1"]
    # A reject reason still wins over a short quota.
    short = make_result([trial("PASS"), trial("INCONCLUSIVE")])
    assert run_classify(short)[0] == "reject"


def test_inconclusive_trials_do_not_count_as_failures_or_passes():
    result = make_result([trial("FAIL")] * 3 + [trial("INCONCLUSIVE")])
    assert run_classify(result) == ("accept", [])


# --------------------------------------------------------------------------- #
# preconditions
# --------------------------------------------------------------------------- #
def simple_document(**kwargs) -> DnaDocument:
    schedule = make_schedule([["d1", "d2", "d3"]])
    return make_document(make_scenario(schedule, make_expectation(pinned=("d1",))), **kwargs)


def test_preconditions_hold_for_a_clean_characterisation():
    assert preconditions(simple_document()) is None
    assert preconditions(simple_document(p0=0.9)) is None


def test_low_p0_is_skipped():
    assert preconditions(simple_document(p0=0.8)) == "p0<0.9"


def test_mixed_release_is_skipped():
    document = simple_document(release_conditions={"parties": 7, "timeout": 3})
    assert preconditions(document) == "mixed_release"
    # A zero count does not make a release condition "observed".
    assert preconditions(
        simple_document(
            release_condition="parties",
            release_conditions={"parties": 10, "timeout": 0},
        )
    ) is None
    assert preconditions(simple_document(release_condition="mixed")) == "mixed_release"
    assert preconditions(simple_document(release_conditions={"mixed": 10})) == "mixed_release"
    assert preconditions(
        simple_document(release_condition="none", release_conditions={"timeout": 10})
    ) == "mixed_release"
    assert preconditions(simple_document(p0=math.nan)) == "p0<0.9"


@pytest.mark.asyncio
async def test_skipped_reductions_execute_nothing():
    for kwargs, reason in (
        ({"p0": 0.5}, "p0<0.9"),
        ({"release_conditions": {"parties": 5, "timeout": 5}}, "mixed_release"),
    ):
        document = simple_document(**kwargs)
        scenario = make_scenario(document.schedule(), document.expectation())

        async def explode(_scenario):
            raise AssertionError("a skipped reduction ran a trial")

        result = await reduce_schedule(document, scenario, explode)
        assert result.status == "skipped" and result.skip_reason == reason
        assert result.reduced_deliveries == result.original_deliveries
        assert result.candidates == [] and result.trials == 0


# --------------------------------------------------------------------------- #
# remove_delivery
# --------------------------------------------------------------------------- #
def test_remove_delivery_drops_the_id_and_empty_steps_only():
    schedule = make_schedule([["d1"], ["d2", "d3"]], faults=[LatencyFault(ms=200)])
    schedule.steps[1] = Step(deliveries=["d2", "d3"], delay_before_ms=40)
    solo = remove_delivery(schedule, "d1")
    assert [item.id for item in solo.deliveries] == ["d2", "d3"]
    assert [step.deliveries for step in solo.steps] == [["d2", "d3"]]
    assert solo.steps[0].delay_before_ms == 40
    assert solo.faults == schedule.faults
    assert solo.delivery_policy == schedule.delivery_policy
    assert schedule.deliveries[0].id == "d1"  # input is untouched
    grouped = remove_delivery(schedule, "d3")
    assert [step.deliveries for step in grouped.steps] == [["d1"], ["d2"]]


def test_removing_the_last_delivery_is_an_error():
    with pytest.raises(ValueError):
        remove_delivery(make_schedule([["d1"]]), "d1")


# --------------------------------------------------------------------------- #
# static_check
# --------------------------------------------------------------------------- #
def check(schedule, removed, expectation=None, signature=SIGNATURE):
    return static_check(
        schedule,
        remove_delivery(schedule, removed),
        removed,
        signature=signature,
        expectation=expectation or make_expectation(),
    )


def test_removal_below_parties_is_inadmissible():
    schedule = make_schedule(
        [["d1", "d2", "d3"]], faults=[RendezvousFault(parties=3, timeout_ms=2000)]
    )
    reasons = check(schedule, "d3")
    assert any(item.startswith("rendezvous_below_parties") for item in reasons)
    assert any(item.startswith("invalid_schedule") for item in reasons)


def test_deliveries_the_rendezvous_does_not_match_may_leave():
    other = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A2")
    schedule = make_schedule(
        [["d1", "d2", "d3"]], faults=[RendezvousFault(parties=2, payments=["payment:A1"])]
    )
    schedule.deliveries[2] = schedule.deliveries[2].model_copy(update={"event": other})
    assert check(schedule, "d3") == []
    assert any(item.startswith("rendezvous_below_parties") for item in check(schedule, "d2"))


def test_breaking_a_concurrent_group_is_inadmissible():
    schedule = make_schedule([["d1"], ["d2", "d3"]])
    assert check(schedule, "d3") == ["concurrent_group_broken:step1"]
    assert check(schedule, "d2") == ["concurrent_group_broken:step1"]
    assert check(schedule, "d1") == []  # a lone step may vanish


def test_a_larger_group_may_shrink_to_two():
    schedule = make_schedule([["d1", "d2", "d3", "d4"]])
    assert check(schedule, "d4") == []


def test_evidence_and_pass_condition_deliveries_are_pinned():
    schedule = make_schedule([["d1"], ["d2"], ["d3"]])
    expectation = make_expectation(pinned=("d1",), other="d2")
    assert check(schedule, "d1", expectation) == ["pinned_by_expectation:d1"]
    assert check(schedule, "d2", expectation) == ["pinned_by_expectation:d2"]
    assert check(schedule, "d3", expectation) == []
    with_condition = expectation.model_copy(
        update={"pass_conditions": [PassCondition(delivery="d3", expected="2xx")]}
    )
    assert check(schedule, "d3", with_condition) == ["pinned_by_expectation:d3"]


def test_signature_roles_must_stay_targeted_by_a_real_effect():
    other = EventSpec(type="payment.captured", order_role="order:B", payment_role="payment:B1")
    schedule = make_schedule([["d1"], ["d2"]])
    schedule.deliveries[1] = schedule.deliveries[1].model_copy(update={"event": other})
    assert check(schedule, "d1") == ["role_untargeted:order:A"]
    assert check(schedule, "d2") == []

    inert = make_schedule([["d1"], ["d2"]], intents={"d1": "none"})
    # d2 is the only delivery with a real effect: removing it strands the role.
    assert check(inert, "d2") == ["role_untargeted:order:A"]
    assert check(inert, "d1") == []


def test_a_tampered_candidate_is_caught_by_the_unchanged_checks():
    schedule = make_schedule([["d1"], ["d2"]], faults=[LatencyFault(ms=200)])
    candidate = remove_delivery(schedule, "d2")
    candidate.faults = [LatencyFault(ms=999)]
    candidate.delivery_policy = AttemptPolicy(attempt_timeout_ms=7)
    reasons = static_check(
        schedule, candidate, "d2", signature=SIGNATURE, expectation=make_expectation()
    )
    assert "faults_changed" in reasons and "delivery_policy_changed" in reasons
    candidate = remove_delivery(schedule, "d2")
    candidate.deliveries[0] = candidate.deliveries[0].model_copy(update={"event_id": "x"})
    assert "deliveries_changed" in static_check(
        schedule, candidate, "d2", signature=SIGNATURE, expectation=make_expectation()
    )


def test_static_check_rejects_changed_step_order_and_concurrency():
    schedule = make_schedule([["d1"], ["d2"], ["d3"]])
    candidate = remove_delivery(schedule, "d3")
    candidate.steps = list(reversed(candidate.steps))
    assert "steps_changed" in static_check(
        schedule, candidate, "d3", signature=SIGNATURE, expectation=make_expectation()
    )

    grouped = make_schedule([["d1", "d2", "d3"]])
    candidate = remove_delivery(grouped, "d3")
    candidate.steps = [Step(deliveries=["d1"]), Step(deliveries=["d2"])]
    assert "steps_changed" in static_check(
        grouped, candidate, "d3", signature=SIGNATURE, expectation=make_expectation()
    )

    candidate = remove_delivery(grouped, "d3")
    candidate.steps[0] = candidate.steps[0].model_copy(
        update={"deliveries": ["d2", "d1"]}
    )
    assert "steps_changed" in static_check(
        grouped, candidate, "d3", signature=SIGNATURE, expectation=make_expectation()
    )


# --------------------------------------------------------------------------- #
# reduce_schedule
# --------------------------------------------------------------------------- #
async def run_reduction(groups, executor, *, pinned=("d1",), c_max=C_MAX, **doc_kwargs):
    schedule = make_schedule(groups)
    scenario = make_scenario(schedule, make_expectation(pinned=pinned))
    document = make_document(scenario, **doc_kwargs)
    return await reduce_schedule(document, scenario, executor, c_max=c_max)


def test_c_max_matches_the_budget_arithmetic():
    assert C_MAX == 10
    assert C_MAX * limits_module.POLICIES["reduce"].max_attempts == limits_module.REDUCTION_TRIALS


@pytest.mark.asyncio
async def test_every_delivery_pinned_means_no_admissible_reductions():
    # One sequential step per delivery; every delivery is named by the expectation.
    executor = always(accepted_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"]], executor, pinned=("d1", "d2", "d3"))
    assert result.status == "no_admissible_reductions"
    assert result.claim == CLAIM_NONE
    assert executor.calls == [] and result.trials == 0 and result.admissible_tested == 0
    assert result.reduced_deliveries == ["d1", "d2", "d3"] and result.reduced_schedule is None
    assert [item.verdict for item in result.candidates] == ["inadmissible"] * 3
    assert all(not item.admissible and item.trials == 0 for item in result.candidates)


@pytest.mark.asyncio
async def test_a_single_group_pinned_to_its_minimum_has_no_reductions():
    executor = always(accepted_result)
    result = await run_reduction([["d1", "d2"]], executor, pinned=("d1",))
    assert result.status == "no_admissible_reductions" and executor.calls == []


@pytest.mark.asyncio
async def test_greedy_reduces_and_claims_minimality():
    executor = always(accepted_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"]], executor)
    assert result.status == "reduced"
    assert result.reduced_deliveries == ["d1"]
    assert result.claim == CLAIM_MINIMAL
    assert result.budget_exhausted is False
    assert executor.calls == [("d1", "d2"), ("d1",)]
    assert result.reduced_schedule is not None
    assert [item["id"] for item in result.reduced_schedule["deliveries"]] == ["d1"]
    assert result.trials == 6


@pytest.mark.asyncio
async def test_rejections_keep_the_delivery_and_still_claim_minimality():
    executor = always(rejected_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"]], executor)
    assert result.status == "not_reduced"
    assert result.claim == CLAIM_MINIMAL
    assert result.reduced_deliveries == ["d1", "d2", "d3"]
    assert [item.verdict for item in result.candidates] == ["reject", "reject", "inadmissible"]
    assert all(item.reasons == ["pass_observed"] for item in result.candidates[:2])
    assert result.candidates[0].passed == 1 and result.candidates[0].fail == 2


@pytest.mark.asyncio
async def test_different_signature_candidates_are_rejected_and_recorded():
    executor = always(lambda: accepted_result(signature=OTHER))
    result = await run_reduction([["d1"], ["d2"]], executor)
    assert result.status == "not_reduced"
    record = result.candidates[0]
    assert record.verdict == "reject"
    assert record.reasons == [f"different_signature:{OTHER.key}"]
    assert record.signatures == {OTHER.key: 3}


def test_missing_trial_observations_make_a_candidate_unknown():
    result = accepted_result().model_copy(update={"observations": []})
    verdict, reasons = run_classify(result)
    assert verdict == "unknown"
    assert any(reason.startswith("trial_observation_mismatch:") for reason in reasons)


def test_missing_trial_observations_cannot_revalidate_a_reduced_dna():
    result = accepted_result().model_copy(update={"observations": []})
    summary = _revalidation_summary(
        result,
        signature=SIGNATURE,
        release_condition="none",
        parties=None,
        required_valid=3,
    )
    assert summary["accepted"] is False
    assert summary["uniform_release"] is False
    assert summary["reason"].startswith("trial_observation_mismatch:")


@pytest.mark.asyncio
async def test_a_changed_release_condition_is_rejected():
    # The original was released by `parties`; candidates release by timeout.
    executor = always(lambda: accepted_result(released_by="timeout"))
    result = await run_reduction(
        [["d1"], ["d2"]], executor, release_condition="parties",
        release_conditions={"parties": 10},
    )
    assert result.status == "not_reduced"
    assert result.candidates[0].reasons == ["release_changed:timeout"]
    assert result.candidates[0].release == {"timeout": 3}


def _party_search():
    other = EventSpec(type="payment.captured", order_role="order:A", payment_role="payment:A2")
    schedule = make_schedule(
        [["d1", "d2", "d3"]], faults=[RendezvousFault(parties=2, payments=["payment:A1"])]
    )
    schedule.deliveries[2] = schedule.deliveries[2].model_copy(update={"event": other})
    scenario = make_scenario(schedule, make_expectation(pinned=("d1",)))
    return scenario, make_document(scenario, release_condition="parties")


@pytest.mark.asyncio
async def test_parties_release_with_too_few_arrivals_is_rejected_in_the_search():
    scenario, document = _party_search()
    short = always(lambda: accepted_result(released_by="parties", arrivals=1))
    result = await reduce_schedule(document, scenario, short)
    assert result.status == "not_reduced"
    assert result.candidates[0].verdict == "reject"
    assert result.candidates[0].reasons[0].startswith("release_changed:arrivals=1<2")

    full = always(lambda: accepted_result(released_by="parties", arrivals=2))
    result = await reduce_schedule(document, scenario, full)
    assert result.status == "reduced" and result.reduced_deliveries == ["d1", "d2"]
    assert result.claim == CLAIM_MINIMAL


@pytest.mark.asyncio
async def test_unknown_candidates_keep_their_delivery():
    executor = always(unknown_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"]], executor)
    assert result.status == "not_reduced"
    assert result.reduced_deliveries == ["d1", "d2", "d3"]
    assert result.claim == CLAIM_NOT_REDUCED
    assert [item.verdict for item in result.candidates] == ["unknown", "unknown", "inadmissible"]
    assert result.candidates[0].reasons == ["insufficient_valid:1"]


@pytest.mark.asyncio
async def test_an_unknown_blocks_the_minimality_claim_after_a_reduction():
    # d3 is accepted, d2 is unknown: reduced, but not provably 1-minimal.
    def decide(remaining):
        return accepted_result() if remaining == {"d1", "d2"} else unknown_result()

    result = await run_reduction([["d1"], ["d2"], ["d3"]], FakeExecutor(decide))
    assert result.status == "reduced"
    assert result.reduced_deliveries == ["d1", "d2"]
    assert result.claim == CLAIM_NOT_ESTABLISHED


@pytest.mark.asyncio
async def test_budget_exhaustion_is_not_a_minimality_claim():
    executor = always(accepted_result)
    result = await run_reduction(
        [["d1"], ["d2"], ["d3"], ["d4"], ["d5"]], executor, c_max=2
    )
    assert result.budget_exhausted is True
    assert result.admissible_tested == 2 and len(executor.calls) == 2
    assert result.status == "reduced"
    assert result.reduced_deliveries == ["d1", "d2", "d3"]
    assert result.claim == CLAIM_NOT_ESTABLISHED
    assert result.trials == 6


@pytest.mark.asyncio
async def test_budget_exhaustion_without_a_reduction_says_not_reduced():
    executor = always(rejected_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"], ["d4"]], executor, c_max=1)
    assert result.budget_exhausted and result.status == "not_reduced"
    assert result.claim == CLAIM_NOT_REDUCED


@pytest.mark.asyncio
async def test_exactly_c_max_candidates_with_nothing_left_is_not_exhaustion():
    executor = always(rejected_result)
    result = await run_reduction([["d1"], ["d2"], ["d3"]], executor, c_max=2)
    assert result.admissible_tested == 2 and not result.budget_exhausted
    assert result.claim == CLAIM_MINIMAL


@pytest.mark.asyncio
async def test_inadmissible_candidates_do_not_count_against_the_budget():
    # d1, d2 are pinned; only d3..d5 are admissible although there are 5 deliveries.
    executor = always(rejected_result)
    result = await run_reduction(
        [["d1"], ["d2"], ["d3"], ["d4"], ["d5"]], executor, pinned=("d1", "d2"), c_max=3
    )
    assert result.admissible_tested == 3 and not result.budget_exhausted
    inadmissible = [item for item in result.candidates if not item.admissible]
    assert {item.removed for item in inadmissible} == {"d1", "d2"}


@pytest.mark.asyncio
async def test_candidate_scenarios_carry_the_removed_schedule_and_a_distinct_id():
    seen: list[BuiltScenario] = []

    async def executor(scenario):
        seen.append(scenario)
        return rejected_result()

    schedule = make_schedule([["d1"], ["d2"]])
    scenario = make_scenario(schedule, make_expectation(pinned=("d1",)))
    await reduce_schedule(make_document(scenario), scenario, executor)
    assert [item.scenario_id for item in seen] == ["S3[test]~reduce[d1]"]
    assert seen[0].expectation is scenario.expectation
    assert seen[0].roles == scenario.roles and seen[0].fixtures == scenario.fixtures


# --------------------------------------------------------------------------- #
# Fixpoint against an independently written deterministic oracle
# --------------------------------------------------------------------------- #
Verdict = str  # "accept" | "reject" | "unknown"


def oracle(order, groups, pinned, decide, c_max):
    """Greedy reverse-order single removal, written without the production code.

    Admissibility is hand-derived for these schedules (all deliveries share one
    role and one intent): a delivery may leave unless it is pinned, it is the
    last one, or it sits in a group of two or more that would drop below two.
    """
    current = list(order)
    tested: list[tuple[str, ...]] = []
    unknown = 0
    while True:
        found = None
        for victim in reversed(current):
            if victim in pinned or len(current) == 1:
                continue
            group = [item for item in next(g for g in groups if victim in g) if item in current]
            if len(group) >= 2 and len(group) - 1 < 2:
                continue
            if len(tested) == c_max:
                return current, tested, unknown, True
            rest = tuple(item for item in current if item != victim)
            tested.append(rest)
            outcome = decide(frozenset(rest))
            if outcome == "accept":
                found = rest
                break
            if outcome == "unknown":
                unknown += 1
        if found is None:
            return current, tested, unknown, False
        current = list(found)


def result_for(verdict: Verdict) -> ScenarioResult:
    return {"accept": accepted_result, "reject": rejected_result, "unknown": unknown_result}[
        verdict
    ]()


def hashed(remaining: frozenset[str]) -> Verdict:
    digest = hashlib.sha256(",".join(sorted(remaining)).encode()).digest()[0] % 5
    return ["reject", "unknown", "accept", "accept", "accept"][digest]


PREDICATES = {
    "needs_d1_d4": lambda rem: "accept" if {"d1", "d4"} <= rem else "reject",
    "at_least_three": lambda rem: "accept" if len(rem) >= 3 else "reject",
    "needs_d6": lambda rem: "accept" if "d6" in rem else "reject",
    "always": lambda rem: "accept",
    "never": lambda rem: "reject",
    "all_unknown": lambda rem: "unknown",
    "hashed": hashed,
    "unknown_below_four": lambda rem: "accept" if len(rem) >= 5 else ("unknown" if len(rem) == 4 else "reject"),
}

SHAPES = {
    "six_sequential": ([["d1"], ["d2"], ["d3"], ["d4"], ["d5"], ["d6"]], ("d1",)),
    "group_in_the_middle": ([["d1"], ["d2", "d3", "d4"], ["d5"], ["d6"]], ("d1",)),
    "two_groups": ([["d1", "d2", "d3"], ["d4", "d5", "d6"]], ("d1", "d4")),
    "five_with_pair": ([["d1"], ["d2", "d3"], ["d4"], ["d5"]], ("d5",)),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("c_max", [1, 3, 4, 100])
@pytest.mark.parametrize("predicate", list(PREDICATES))
@pytest.mark.parametrize("shape", list(SHAPES))
async def test_greedy_fixpoint_equals_the_deterministic_oracle(shape, predicate, c_max):
    groups, pinned = SHAPES[shape]
    decide = PREDICATES[predicate]
    order = [item for group in groups for item in group]
    expected_final, expected_tested, expected_unknown, expected_exhausted = oracle(
        order, groups, set(pinned), decide, c_max
    )

    executor = FakeExecutor(lambda remaining: result_for(decide(remaining)))
    result = await run_reduction(groups, executor, pinned=pinned, c_max=c_max)

    assert result.reduced_deliveries == expected_final
    assert executor.calls == expected_tested  # same candidates, same order
    assert result.admissible_tested == len(expected_tested)
    assert result.budget_exhausted == expected_exhausted

    accepted = expected_final != order
    if not expected_tested:
        assert (result.status, result.claim) == ("no_admissible_reductions", CLAIM_NONE)
    elif not expected_exhausted and expected_unknown == 0:
        assert result.claim == CLAIM_MINIMAL
        assert result.status == ("reduced" if accepted else "not_reduced")
    else:
        assert result.claim == (CLAIM_NOT_ESTABLISHED if accepted else CLAIM_NOT_REDUCED)
        assert result.status == ("reduced" if accepted else "not_reduced")

    # Inadmissible candidates never reached the executor.
    ran = {item.index for item in result.candidates if item.admissible}
    assert len(ran) == len(executor.calls)
    # The schedule written for the result is exactly the surviving deliveries.
    if accepted:
        assert [d["id"] for d in result.reduced_schedule["deliveries"]] == expected_final
        flattened = [i for s in result.reduced_schedule["steps"] for i in s["deliveries"]]
        assert sorted(flattened) == sorted(expected_final)


# --------------------------------------------------------------------------- #
# build_reduced_document
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_document(registry):
    profile = registry.get("razorpay-vulnerable")
    scenario = build_scenario(
        "S3",
        {"copies": 3, "fault": "latency", "latency_ms": 200},
        profile.roles,
        load_seed(profile),
    )
    signature = FailureSignature(
        check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"]
    )
    reproducer = build_reproducer(profile, scenario, signature, release_condition="none")
    digest = canonical_sha256(reproducer)
    document = DnaDocument(
        dna_id=digest[:16],
        reproducer_sha256=digest,
        reproducer=reproducer,
        provenance_scenario={
            "template_id": scenario.template_id,
            "template_version": scenario.template_version,
            "scenario_id": scenario.scenario_id,
            "params": dict(scenario.params),
        },
        environment=environment_ref(profile),
        characterization={"p0": 1.0, "valid": 10, "fail": 10, "release_conditions": {"none": 10}},
        provenance={"target_id": profile.target_id, "created_at": "then"},
    )
    return profile, scenario, document


def test_reduced_document_changes_only_the_schedule(real_document):
    profile, scenario, original = real_document
    reduced_schedule = remove_delivery(scenario.schedule, "d3")
    reduced = build_reduced_document(
        original,
        reduced_schedule,
        reduction={"claim": CLAIM_MINIMAL},
        characterization={"p0": 1.0, "revalidated": True},
    )

    before = json.loads(json.dumps(original.reproducer))
    after = json.loads(json.dumps(reduced.reproducer))
    assert before["schedule"] != after["schedule"]
    assert after["schedule"] == canonical_dict(reduced_schedule)
    before.pop("schedule")
    after.pop("schedule")
    assert before == after

    assert reduced.reproducer_sha256 == canonical_sha256(reduced.reproducer)
    assert reduced.reproducer_sha256 != original.reproducer_sha256
    assert reduced.dna_id == reduced.reproducer_sha256[:16] != original.dna_id
    assert reduced.reduction == {"claim": CLAIM_MINIMAL}
    assert reduced.characterization == {"p0": 1.0, "revalidated": True}
    assert reduced.provenance["reduced_from"] == original.dna_id
    assert reduced.provenance["target_id"] == "razorpay-vulnerable"
    assert reduced.provenance["created_at"] != "then"
    assert original.provenance["created_at"] == "then"  # original is not mutated
    assert len(original.reproducer["schedule"]["deliveries"]) == 3
    assert reduced.provenance_scenario == original.provenance_scenario
    assert reduced.environment == original.environment


def test_reduced_document_passes_preflight_unchanged(real_document):
    profile, scenario, original = real_document
    assert preflight_payload(original.model_dump(mode="json"), profile=profile).ok
    reduced = build_reduced_document(
        original,
        remove_delivery(scenario.schedule, "d3"),
        reduction={},
        characterization={},
    )
    outcome = preflight_payload(reduced.model_dump(mode="json"), profile=profile)
    assert outcome.rejected is None, outcome.rejected
    assert [item.id for item in outcome.scenario.schedule.deliveries] == ["d1", "d2"]


@pytest.mark.asyncio
async def test_failed_revalidation_is_inconclusive_and_keeps_original_primary(
    real_document, monkeypatch
):
    profile, scenario, original = real_document
    reduced_schedule = remove_delivery(scenario.schedule, "d3")
    reduction = ReductionResult(
        status="reduced",
        claim=CLAIM_MINIMAL,
        original_deliveries=[item.id for item in scenario.schedule.deliveries],
        reduced_deliveries=[item.id for item in reduced_schedule.deliveries],
        admissible_tested=1,
        reduced_schedule=canonical_dict(reduced_schedule),
    )
    calls = 0

    async def fake_preflight(_payload, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(rejected=None, document=original, scenario=scenario)
        return SimpleNamespace(
            rejected=None,
            scenario=replace(scenario, schedule=reduced_schedule),
        )

    async def fake_reduce_schedule(*_args, **_kwargs):
        return reduction

    async def incomplete_replay(_config):
        return make_result(
            [trial("FAIL")] * 8 + [trial("FAIL", OTHER)] * 2
        )

    monkeypatch.setattr(reduce_module, "preflight_async", fake_preflight)
    monkeypatch.setattr(reduce_module, "reduce_schedule", fake_reduce_schedule)
    monkeypatch.setattr(reduce_module, "run_scenario", incomplete_replay)
    run = await reduce_document(
        original.model_dump(mode="json"),
        dna_id=original.dna_id,
        profile=profile,
        stub=None,
        workdir_root=Path("."),
    )

    assert run.outcome == "INCONCLUSIVE"
    assert run.reason == "p<0.9:0.8"
    assert run.reduced_document is not None  # candidate kept for inspection
    assert run.primary_dna_id == original.dna_id
    assert run.to_json()["outcome"] == "INCONCLUSIVE"


def test_tampering_with_the_reduced_schedule_is_a_digest_tamper(real_document):
    profile, scenario, original = real_document
    reduced = build_reduced_document(
        original,
        remove_delivery(scenario.schedule, "d3"),
        reduction={},
        characterization={},
    )
    payload = reduced.model_dump(mode="json")
    payload["reproducer"]["schedule"]["deliveries"][0]["event_id"] = "evt_changed"
    outcome = preflight_payload(payload, profile=profile)
    assert outcome.rejected is not None and outcome.rejected.reason == "tampered:digest"

    payload = reduced.model_dump(mode="json")
    payload["reproducer"]["schedule"]["deliveries"].append(
        scenario.schedule.deliveries[2].model_dump(mode="json")
    )
    assert preflight_payload(payload, profile=profile).rejected.reason == "tampered:digest"


def test_a_schedule_reduced_below_the_rendezvous_is_rejected_by_preflight(registry):
    profile = registry.get("razorpay-vulnerable")
    scenario = build_scenario(
        "S3",
        {"copies": 3, "fault": "rendezvous", "latency_ms": 200},
        profile.roles,
        load_seed(profile),
    )
    reproducer = build_reproducer(
        profile, scenario, SIGNATURE, release_condition="parties"
    )
    digest = canonical_sha256(reproducer)
    document = DnaDocument(
        dna_id=digest[:16],
        reproducer_sha256=digest,
        reproducer=reproducer,
        provenance_scenario={
            "template_id": "S3",
            "template_version": 1,
            "scenario_id": scenario.scenario_id,
            "params": dict(scenario.params),
        },
        environment={},
        characterization={"p0": 1.0, "release_conditions": {"parties": 10}},
    )
    below = build_reduced_document(
        document,
        remove_delivery(scenario.schedule, "d3"),
        reduction={},
        characterization={},
    )
    outcome = preflight_payload(below.model_dump(mode="json"), profile=profile)
    assert outcome.rejected is not None and outcome.rejected.reason == "invalid:schedule"


@pytest.mark.asyncio
async def test_reduction_honors_an_investigation_deadline_without_starting_targets(
    real_document, tmp_path, monkeypatch
):
    from duress import operations
    from duress.provider_stub import StubServer
    import time

    profile, _, document = real_document

    async def forbidden_trial(_config):
        raise AssertionError("no trial may start after the shared deadline")

    monkeypatch.setattr(operations, "run_trial", forbidden_trial)
    run = await reduce_document(
        document.model_dump(mode="json"), dna_id=document.dna_id,
        profile=profile, stub=StubServer(), workdir_root=tmp_path,
        home=tmp_path, wall_deadline=time.monotonic() - 1,
    )
    assert run.outcome == "INCONCLUSIVE"
    assert run.reason == "budget_exhausted"
    assert run.primary_dna_id == document.dna_id
    assert run.result.trials == 0
    assert run.result.budget_exhausted is True
    assert len(run.result.candidates) == 1
    assert run.result.candidates[0].verdict == "unknown"
    assert run.result.claim == CLAIM_NOT_REDUCED


def test_unrelated_concurrent_group_does_not_pin_a_delivery():
    schedule = make_schedule([["d1", "d2"], ["d3", "d4"]])
    other = EventSpec(type="payment.captured", order_role="order:B", payment_role="payment:B1")
    for delivery in schedule.deliveries[2:]:
        delivery.event = other
    assert static_check(
        schedule, remove_delivery(schedule, "d4"), "d4",
        signature=SIGNATURE, expectation=make_expectation(),
    ) == []


def test_unrelated_delivery_cannot_replace_concurrency_on_the_affected_role():
    schedule = make_schedule([["d1", "d2", "d3"]])
    schedule.deliveries[2].event = EventSpec(
        type="payment.captured", order_role="order:B", payment_role="payment:B1"
    )
    reasons = static_check(
        schedule, remove_delivery(schedule, "d2"), "d2",
        signature=SIGNATURE, expectation=make_expectation(),
    )
    assert "concurrent_group_broken:step0" in reasons
