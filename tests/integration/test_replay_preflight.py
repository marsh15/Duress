"""Preflight rejects tampered or incompatible artifacts before any target starts.

Each case mutates one thing. Where the plan says "with digests recomputed" the
helper genuinely recomputes them, so the rejection is caused by the semantic
check and not by the digest.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from duress import dna as dna_module
from duress.models import DnaDocument, canonical_sha256
from duress.preflight import preflight_async, preflight_payload
from duress.services import Workspace

ROOT = Path(__file__).resolve().parents[2]


def payload_of(document: DnaDocument) -> dict:
    return json.loads(json.dumps(document.model_dump(mode="json")))


def rehash(document: dict) -> dict:
    document["reproducer_sha256"] = canonical_sha256(document["reproducer"])
    document["dna_id"] = document["reproducer_sha256"][:16]
    return document


def profile(target_id: str = "razorpay-vulnerable"):
    return Workspace.discover(ROOT).registry.get(target_id)


def reason(document: dict, target_id: str = "razorpay-vulnerable") -> str:
    outcome = preflight_payload(document, profile=profile(target_id))
    assert outcome.rejected is not None
    return outcome.rejected.reason


def test_event_body_change_is_a_digest_tamper(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["schedule"]["deliveries"][0]["event_id"] = "evt_changed"
    assert reason(mutated) == "tampered:digest"
    # A wrong dna_id prefix is also a tamper.
    mutated = payload_of(document)
    mutated["dna_id"] = "0000000000000000"
    assert reason(mutated) == "tampered:digest"


def test_expectation_edited_with_stale_hash(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["expectation"]["definition"]["footprint"] = ["order:B"]
    # The outer digest is honest; only the embedded definition hash is stale.
    rehash(mutated)
    assert reason(mutated) == "tampered:expectation"


def test_expectation_edited_with_recomputed_hashes(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    definition = mutated["reproducer"]["expectation"]["definition"]
    definition["footprint"] = ["order:B"]
    mutated["reproducer"]["expectation"]["definition_sha256"] = canonical_sha256(definition)
    rehash(mutated)
    assert reason(mutated) == "incompatible:expectation"


def test_pack_member_definition_edit_is_tampered(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["invariant_pack"]["members"][0]["definition"]["sql"] = "SELECT 1"
    rehash(mutated)
    assert reason(mutated) == "tampered:invariant_pack"


def test_pack_member_removed_with_recomputed_digests(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    pack = mutated["reproducer"]["invariant_pack"]
    pack["members"].pop()
    identities = sorted(
        f"{member['id']}@{member['version']}#{member['definition_sha256']}"
        for member in pack["members"]
    )
    pack["digest"] = canonical_sha256(identities)
    rehash(mutated)
    assert reason(mutated) == "incompatible:invariant_pack"


def test_local_member_version_missing_from_the_dna(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    pack = mutated["reproducer"]["invariant_pack"]
    pack["members"][0]["version"] = 0
    identities = sorted(
        f"{member['id']}@{member['version']}#{member['definition_sha256']}"
        for member in pack["members"]
    )
    pack["digest"] = canonical_sha256(identities)
    rehash(mutated)
    assert reason(mutated) == "incompatible:invariant_pack"


def test_execution_semantics_mismatch(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["execution_semantics"]["version"] = 2
    rehash(mutated)
    assert reason(mutated) == "incompatible:execution_semantics"

    mutated = payload_of(document)
    mutated["reproducer"]["execution_semantics"]["digest"] = "0" * 64
    rehash(mutated)
    assert reason(mutated) == "incompatible:execution_semantics"


def test_unsupported_schema_version(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["schema_version"] = 99
    assert reason(mutated) == "unsupported_version"


def test_party_count_and_worst_case_are_validated(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["schedule"]["faults"][0]["parties"] = 5
    rehash(mutated)
    assert reason(mutated) == "invalid:schedule"

    mutated = payload_of(document)
    delivery = mutated["reproducer"]["schedule"]["deliveries"][0]
    delivery["attempt_timeout_ms"] = 10000
    delivery["max_attempts"] = 3
    mutated["reproducer"]["schedule"]["delivery_policy"]["backoff_ms"] = [0, 0]
    rehash(mutated)
    assert reason(mutated) == "invalid:schedule"


@pytest.mark.asyncio
async def test_contract_mismatch_is_rejected_before_init(dna_artifact):
    document, _ = dna_artifact
    two_workers = Workspace.discover(ROOT).registry.get("razorpay-fixed-2w")
    outcome = await preflight_async(payload_of(document), profile=two_workers)
    assert outcome.rejected.reason == "incompatible:target_contract"


@pytest.mark.asyncio
async def test_contract_version_mismatch(dna_artifact):
    document, _ = dna_artifact
    other = dataclasses.replace(profile(), contract_version=2)
    outcome = await preflight_async(payload_of(document), profile=other)
    assert outcome.rejected.reason == "incompatible:target_contract"


@pytest.mark.asyncio
async def test_schema_probe_failure(dna_artifact):
    document, _ = dna_artifact
    broken = dataclasses.replace(
        profile(), init_command=("python", "-c", "raise SystemExit(3)")
    )
    outcome = await preflight_async(payload_of(document), profile=broken)
    assert outcome.rejected.reason == "schema_mismatch"


def test_no_trial_runs_for_a_rejected_artifact(dna_artifact, monkeypatch):
    document, _ = dna_artifact
    spawned = {"count": 0}

    def fail(*_args, **_kwargs):
        spawned["count"] += 1
        raise AssertionError("a trial started during preflight")

    monkeypatch.setattr("duress.target.run_trial", fail)
    monkeypatch.setattr("duress.operations.run_scenario", fail)
    mutated = payload_of(document)
    mutated["reproducer"]["expectation"]["definition"]["footprint"] = []
    outcome = preflight_payload(mutated, profile=profile())
    assert outcome.rejected.reason == "tampered:digest"
    assert spawned["count"] == 0


def test_unknown_check_in_signature(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["failure_signature"]["check"] = "INV-99@1"
    rehash(mutated)
    assert reason(mutated) == "incompatible:invariant_pack"


def test_unknown_domain_is_rejected(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"]["domain"] = "orders-webhook"
    rehash(mutated)
    assert reason(mutated) == "incompatible:target_contract"