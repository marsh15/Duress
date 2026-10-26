"""Reduction (plan C.11) against real managed targets.

No delivery count is asserted anywhere: the claim must follow from the recorded
candidate log, whatever the target happened to do.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from duress import dna as dna_module
from duress.models import DnaDocument, FailureSignature
from duress.preflight import preflight_async
from duress.profiles import load_seed
from duress.provider_stub import StubServer
from duress.reduce import (
    C_MAX,
    CLAIM_MINIMAL,
    CLAIM_NONE,
    CLAIM_NOT_ESTABLISHED,
    CLAIM_NOT_REDUCED,
    ReductionResult,
    ReductionRun,
    classify,
    preconditions,
    reduce_document,
)
from duress.replay import replay_document
from duress.scenarios import build_scenario
from duress.store import ensure_dirs

from ..engine import built, run_built

VULNERABLE = "razorpay-vulnerable"
FIXED = "razorpay-fixed"
S3_RENDEZVOUS = {"copies": 3, "fault": "rendezvous", "latency_ms": 200}
S3_LATENCY = {"copies": 3, "fault": "latency", "latency_ms": 200}


# --------------------------------------------------------------------------- #
# Shared reductions (each one builds a real DNA, then reduces it once)
# --------------------------------------------------------------------------- #
def _reduce_case(registry, tmp_path_factory, params, *, black_box: bool):
    profile = registry.get(VULNERABLE)
    scenario = build_scenario("S3", params, profile.roles, load_seed(profile))
    workdir = tmp_path_factory.mktemp(f"reduce-{params['fault']}")
    home = workdir / ".duress"
    ensure_dirs(home)

    async def go():
        with StubServer() as server:
            document, characterisation = await dna_module.build_dna(
                profile, scenario, server, workdir_root=workdir, home=home
            )
            run = await reduce_document(
                document.model_dump(mode="json"),
                dna_id=document.dna_id,
                profile=profile,
                stub=server,
                workdir_root=workdir,
                home=home,
                black_box=black_box,
            )
            return document, characterisation, run

    return asyncio.run(go())


@pytest.fixture(scope="module")
def rendezvous_case(registry, tmp_path_factory):
    return _reduce_case(registry, tmp_path_factory, S3_RENDEZVOUS, black_box=True)


@pytest.fixture(scope="module")
def latency_case(registry, tmp_path_factory):
    return _reduce_case(registry, tmp_path_factory, S3_LATENCY, black_box=True)


def assert_claim_follows_log(result: ReductionResult, signature: FailureSignature) -> None:
    """C.11's claim table, recomputed from the candidate log alone."""
    log = result.candidates
    admissible = [item for item in log if item.admissible]
    inadmissible = [item for item in log if not item.admissible]

    assert [item.index for item in log] == list(range(len(log)))
    assert result.admissible_tested == len(admissible) <= C_MAX
    assert result.trials == sum(item.trials for item in log)
    assert all(
        item.trials == 0 and item.verdict == "inadmissible" and item.inadmissible_reasons
        for item in inadmissible
    )
    for item in admissible:
        assert 0 < item.trials <= 4
        assert item.removed not in item.remaining
        should_accept = (
            item.valid >= 3
            and item.fail == item.valid
            and item.signatures.get(signature.key) == item.fail
        )
        # accept additionally requires the release condition; it can only narrow.
        if item.verdict == "accept":
            assert should_accept and item.reasons == []
        else:
            assert item.reasons

    accepted = [item for item in admissible if item.verdict == "accept"]
    unknown = [item for item in admissible if item.verdict == "unknown"]
    if not admissible:
        assert result.status == "no_admissible_reductions"
        assert result.claim == CLAIM_NONE
        assert result.trials == 0 and not result.budget_exhausted
        assert result.reduced_deliveries == result.original_deliveries
        return
    if not result.budget_exhausted and not unknown:
        assert result.claim == CLAIM_MINIMAL
    else:
        assert result.claim == (CLAIM_NOT_ESTABLISHED if accepted else CLAIM_NOT_REDUCED)
    assert result.status == ("reduced" if accepted else "not_reduced")
    assert len(result.reduced_deliveries) == len(result.original_deliveries) - len(accepted)
    assert (result.reduced_schedule is not None) == bool(accepted)
    if result.budget_exhausted:
        assert result.admissible_tested == C_MAX


def test_rendezvous_reduction_claim_is_consistent_with_the_log(rendezvous_case):
    document, characterisation, run = rendezvous_case
    assert preconditions(document) is None
    assert characterisation.release_condition == "parties"
    assert run.black_box is not None
    assert run.black_box["valid"] == 10
    assert 10 <= run.black_box["trials"] <= 11
    result = run.result
    assert result is not None
    assert_claim_follows_log(result, document.signature())

    # Every deletion would drop the matching deliveries below `parties`, or pin
    # d1 by the expectation: the rendezvous DNA is expected to have no admissible
    # candidate, but the assertion is on the log, not on that expectation.
    if result.admissible_tested == 0:
        assert run.outcome == "NOT_REDUCED"
        assert run.reduced_document is None and run.revalidation is None
        assert run.primary_dna_id == document.dna_id
        assert result.claim == CLAIM_NONE
        assert result.trials == 0
        assert any(
            reason.startswith("rendezvous_below_parties")
            for item in result.candidates
            for reason in item.inadmissible_reasons
        )
    json.dumps(run.to_json())  # the run is JSON-serialisable


def test_latency_reduction_runs_real_trials_and_revalidates(latency_case):
    document, _characterisation, run = latency_case
    skip = preconditions(document)
    if skip is not None:
        assert run.outcome == "SKIPPED" and run.reason == skip
        assert run.result.status == "skipped" and run.result.trials == 0
        pytest.fail(f"S3 latency DNA is not reducible: skipped for {skip}")

    result = run.result
    assert result is not None and result.status != "skipped"
    assert_claim_follows_log(result, document.signature())
    assert result.admissible_tested > 0 and result.trials > 0
    assert run.preflight == {"ok": True}
    assert run.black_box is not None and run.black_box["policy"] == "characterize"
    k, _, n = run.black_box["rate"].partition("/")
    assert 0 <= int(k) <= int(n) == run.black_box["valid"]

    if run.outcome == "NOT_REDUCED":
        assert run.reduced_document is None and run.revalidation is None
        assert run.primary_dna_id == document.dna_id
        return

    assert run.outcome == "REDUCED" and result.status == "reduced"
    reduced = run.reduced_document
    assert isinstance(reduced, DnaDocument)
    assert reduced.dna_id != document.dna_id
    assert reduced.provenance["reduced_from"] == document.dna_id
    assert reduced.reduction["claim"] == result.claim
    assert reduced.characterization["revalidated"] is True
    assert reduced.characterization["black_box_rate"] == run.black_box["rate"]

    # Only the schedule differs.
    before = {key: value for key, value in document.reproducer.items() if key != "schedule"}
    after = {key: value for key, value in reduced.reproducer.items() if key != "schedule"}
    assert before == after
    assert [item["id"] for item in reduced.reproducer["schedule"]["deliveries"]] == (
        result.reduced_deliveries
    )

    revalidation = run.revalidation
    assert revalidation is not None and revalidation["policy"] == "replay"
    assert revalidation["valid"] >= 10 and revalidation["uniform_release"]
    assert revalidation["p"] >= 0.9 and revalidation["accepted"] is True
    assert run.primary_dna_id == reduced.dna_id

    from duress.services import reduction_summary as service_reduction_summary

    summary = service_reduction_summary(run)
    assert summary["trials"] == (
        result.trials + revalidation["trials"] + run.black_box["trials"]
    )


@pytest.mark.asyncio
async def test_reduced_dna_is_independently_replayable(latency_case, registry, stub, tmp_path):
    document, _, run = latency_case
    if run.outcome != "REDUCED":
        pytest.skip(f"nothing was reduced: {run.outcome} ({run.reason})")
    payload = json.loads(json.dumps(run.reduced_document.model_dump(mode="json")))
    # The reduced artifact is a stand-alone DNA: preflight accepts it unchanged...
    outcome = await preflight_async(payload, profile=registry.get(VULNERABLE))
    assert outcome.rejected is None, outcome.rejected
    # ...and an independent replay (fresh home, only the reduced payload) reproduces.
    fresh_home = tmp_path / "fresh-home"
    ensure_dirs(fresh_home)
    (tmp_path / "replay").mkdir()
    replayed = await replay_document(
        payload,
        dna_id=payload["dna_id"],
        profile=registry.get(VULNERABLE),
        registry=registry,
        stub=stub,
        workdir_root=tmp_path / "replay",
        home=fresh_home,
    )
    assert replayed.outcome.outcome == "REPRODUCED", replayed.outcome
    assert replayed.outcome.signature_matched
    assert replayed.outcome.valid >= 10


# --------------------------------------------------------------------------- #
# reduce_document guards (fast: no candidate ever runs)
# --------------------------------------------------------------------------- #
async def _forbidden(_scenario):
    raise AssertionError("a candidate ran")


@pytest.mark.asyncio
async def test_tampered_input_is_rejected_before_anything_runs(latency_case, registry, stub, tmp_path):
    document, _, _ = latency_case
    payload = json.loads(json.dumps(document.model_dump(mode="json")))
    payload["reproducer"]["schedule"]["deliveries"][0]["event_id"] = "evt_changed"
    run = await reduce_document(
        payload,
        dna_id=document.dna_id,
        profile=registry.get(VULNERABLE),
        stub=stub,
        workdir_root=tmp_path,
        executor=_forbidden,
    )
    assert isinstance(run, ReductionRun)
    assert run.outcome == "REJECTED" and run.reason == "tampered:digest"
    assert run.result is None and run.primary_dna_id is None


@pytest.mark.asyncio
async def test_low_p0_is_skipped_without_running_candidates(latency_case, registry, stub, tmp_path):
    document, _, _ = latency_case
    payload = json.loads(json.dumps(document.model_dump(mode="json")))
    payload["characterization"]["p0"] = 0.5  # characterisation is not part of the digest
    run = await reduce_document(
        payload,
        dna_id=document.dna_id,
        profile=registry.get(VULNERABLE),
        stub=stub,
        workdir_root=tmp_path,
        executor=_forbidden,
        black_box=False,
    )
    assert run.outcome == "SKIPPED" and run.reason == "p0<0.9"
    assert run.result.status == "skipped" and run.primary_dna_id == document.dna_id


# --------------------------------------------------------------------------- #
# Negative controls: the predicate against real executions
# --------------------------------------------------------------------------- #
DUPLICATE = FailureSignature(
    check="INV-1@1", kind="duplicate_fulfilment", roles=["order:A"]
)


async def real_run(registry, stub, tmp_path, target: str, params: dict):
    scenario = built(registry, target, "S3", params)
    return await run_built(
        registry, stub, tmp_path / target, target, scenario, policy="reduce", cleanup_passed=True
    )


@pytest.mark.asyncio
async def test_a_fixed_target_is_never_accepted(registry, stub, tmp_path):
    # The candidate (one copy fewer) against the corrected target: it passes.
    result = await real_run(
        registry, stub, tmp_path, FIXED, {**S3_LATENCY, "copies": 2}
    )
    assert result.result.status == "PASS"
    verdict, reasons = classify(
        result, signature=DUPLICATE, release_condition="none", parties=None
    )
    assert verdict == "reject" and "pass_observed" in reasons


@pytest.mark.asyncio
async def test_a_different_signature_is_never_accepted(registry, stub, tmp_path):
    result = await real_run(registry, stub, tmp_path, VULNERABLE, S3_LATENCY)
    assert result.result.status == "FAIL"
    other = FailureSignature(
        check="INV-4@1", kind="captured_without_fulfilment", roles=["payment:A1"]
    )
    assert result.result.signature.key != other.key
    verdict, reasons = classify(
        result, signature=other, release_condition="none", parties=None
    )
    assert verdict == "reject"
    assert reasons and all(item.startswith("different_signature:") for item in reasons)
    # Sanity: against its own signature the same run is accepted.
    assert classify(
        result, signature=result.result.signature, release_condition="none", parties=None
    ) == ("accept", [])


@pytest.mark.asyncio
async def test_a_changed_release_condition_is_never_accepted(registry, stub, tmp_path):
    result = await real_run(registry, stub, tmp_path, VULNERABLE, S3_RENDEZVOUS)
    assert result.result.status == "FAIL"
    released = {item.stub.released_by for item in result.observations}
    assert released == {"parties"}
    signature = result.result.signature

    wrong = classify(result, signature=signature, release_condition="timeout", parties=3)
    assert wrong[0] == "reject" and wrong[1] == ["release_changed:parties"]

    # Needing more arrivals than the stub ever saw also rejects.
    too_many = classify(result, signature=signature, release_condition="parties", parties=4)
    assert too_many[0] == "reject"
    assert too_many[1][0].startswith("release_changed:arrivals=")

    right = classify(result, signature=signature, release_condition="parties", parties=3)
    assert right == ("accept", [])
