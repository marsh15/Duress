"""DNA identity, portability and secret hygiene (plan M2 acceptance)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import pytest

from duress import dna as dna_module
from duress.models import DnaDocument, canonical_sha256
from duress.preflight import preflight_payload, PreflightOptions
from duress.provider_stub import StubServer
from duress.replay import replay_document
from duress.services import Workspace
from duress.store import save_dna


def payload_of(document: DnaDocument) -> dict:
    return document.model_dump(mode="json")


def test_dna_is_built_from_real_failing_trials(dna_artifact):
    document, characterisation = dna_artifact
    assert characterisation.valid == 10
    assert characterisation.fail == 10
    assert characterisation.p0 == 1.0
    assert document.reproducer["failure_signature"]["check"] == "INV-1@1"
    assert document.reproducer["release_condition"] == "parties"
    assert document.reproducer["control_level"] == "environment_controlled"
    assert len(document.reproducer["invariant_pack"]["members"]) == 6
    assert document.observations["violation_rows"]
    assert document.observations["attempts"]


def test_dna_embedding_is_complete_and_canonical(dna_artifact):
    document, _ = dna_artifact
    reproducer = document.reproducer
    assert reproducer["execution_semantics"]["id"] == "exec"
    assert reproducer["execution_semantics"]["version"] == 1
    assert reproducer["target_contract"]["profile"] == "razorpay-ref"
    assert reproducer["target_contract"]["process"]["workers"] == 1
    assert {member["id"] for member in reproducer["invariant_pack"]["members"]} == {
        "INV-1",
        "INV-2",
        "INV-3",
        "INV-4",
        "INV-5",
        "INV-6",
    }
    assert reproducer["expectation"]["definition_sha256"]
    assert reproducer["seed"]["roles"]["order:A"] == "order_A"
    assert reproducer["provider_fixtures"]
    assert canonical_sha256(reproducer) == document.reproducer_sha256
    assert document.dna_id == document.reproducer_sha256[:16]


def test_canonical_json_is_order_independent():
    left = {"b": [1, 2], "a": {"y": None, "x": "v"}}
    right = {"a": {"x": "v", "y": None}, "b": [1, 2]}
    assert canonical_sha256(left) == canonical_sha256(right)
    # Golden value for the canonical form itself (not for a model shape).
    assert canonical_sha256({"b": 1, "a": [2, 3]}) == (
        "2aae3bfa906be39530e7d7a4aa90a7e6d4f29c33e3ac80e1c3b05598c5953c04"
    )


def test_round_trip_preserves_the_digest(dna_artifact, tmp_path):
    document, _ = dna_artifact
    text = dna_module.document_to_json(document)
    reloaded = DnaDocument.model_validate(json.loads(text))
    assert canonical_sha256(reloaded.reproducer) == reloaded.reproducer_sha256
    assert reloaded.dna_id == document.dna_id


def test_store_uses_the_digest_as_the_filename(dna_artifact, tmp_path):
    document, _ = dna_artifact
    from duress.store import list_dna, load_dna_payload

    home = tmp_path / ".duress"
    path = save_dna(home, document)
    assert path.name == f"{document.dna_id}.json"
    assert load_dna_payload(home, document.dna_id)["dna_id"] == document.dna_id
    index = list_dna(home)
    assert index[0]["signature"]["check"] == "INV-1@1"


def test_no_secret_or_signature_is_stored(dna_artifact):
    document, _ = dna_artifact
    text = dna_module.document_to_json(document)
    assert re.search(r"\b[0-9a-f]{64}\b", text) is not None  # digests are hex
    # Secret material would be a 32-byte token-format string; only the reference
    # to its environment variable is present.
    assert "DURESS_WEBHOOK_SECRET" in text
    signatures = re.findall(r'"signature"\s*:\s*"([0-9a-f]{64})"', text)
    assert signatures == []


@pytest.mark.asyncio
async def test_replay_reproduces_on_a_fresh_process(dna_artifact):
    document, _ = dna_artifact
    from tests.engine import built  # noqa: F401

    root = Path(__file__).resolve().parents[2]
    workspace = Workspace.discover(root)
    with StubServer() as stub:
        run = await replay_document(
            payload_of(document),
            dna_id=document.dna_id,
            profile=workspace.registry.get("razorpay-vulnerable"),
            registry=workspace.registry,
            stub=stub,
            workdir_root=Path(workspace.home) / "tmp",
            home=workspace.home,
            policy="explore",
        )
    assert run.outcome.outcome == "REPRODUCED"
    assert run.outcome.signature_matched is True
    assert run.outcome.valid >= 5
    # Every trial is a fresh process, database and secret.
    trials = {observation.trial_id for observation in run.result.observations}
    assert len(trials) == len(run.result.observations)


@pytest.mark.asyncio
async def test_a_different_valid_secret_still_reproduces(dna_artifact, monkeypatch):
    document, _ = dna_artifact
    root = Path(__file__).resolve().parents[2]
    workspace = Workspace.discover(root)
    monkeypatch.setenv("DURESS_WEBHOOK_SECRET", "a-completely-different-secret")
    with StubServer() as stub:
        run = await replay_document(
            payload_of(document),
            dna_id=document.dna_id,
            profile=workspace.registry.get("razorpay-vulnerable"),
            registry=workspace.registry,
            stub=stub,
            workdir_root=Path(workspace.home) / "tmp",
            home=workspace.home,
            policy="explore",
        )
    assert run.outcome.outcome == "REPRODUCED"


def test_preflight_accepts_the_real_artifact(dna_artifact):
    document, _ = dna_artifact
    root = Path(__file__).resolve().parents[2]
    profile = Workspace.discover(root).registry.get("razorpay-vulnerable")
    outcome = preflight_payload(payload_of(document), profile=profile)
    assert outcome.ok, getattr(outcome.rejected, "detail", "")
    assert outcome.scenario is not None


def test_preflight_rejects_an_unresolvable_secret(dna_artifact):
    document, _ = dna_artifact
    mutated = payload_of(document)
    mutated["reproducer"] = json.loads(json.dumps(document.reproducer))
    mutated["reproducer"]["signing"]["secret_ref"] = "vault:payments/webhook"
    mutated = _rehash(mutated)
    root = Path(__file__).resolve().parents[2]
    profile = Workspace.discover(root).registry.get("razorpay-vulnerable")
    outcome = preflight_payload(mutated, profile=profile)
    assert outcome.rejected.reason == "secret_unresolved"

    real = payload_of(document)
    real["reproducer"] = json.loads(json.dumps(document.reproducer))
    real["reproducer"]["signing"]["secret_ref"] = "env:DURESS_NOT_SET_ANYWHERE"
    real = _rehash(real)
    outcome = preflight_payload(
        real, profile=profile, options=PreflightOptions(env={}, secret_required=True)
    )
    assert outcome.rejected.reason == "secret_unresolved"


def _rehash(document: dict) -> dict:
    document["reproducer_sha256"] = canonical_sha256(document["reproducer"])
    document["dna_id"] = document["reproducer_sha256"][:16]
    return document