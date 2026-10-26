"""The verify-fix truth table, run against real managed targets (plan M2)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duress.provider_stub import StubServer
from duress.services import Workspace
from duress.verify import verify_document

ROOT = Path(__file__).resolve().parents[2]


def payload_of(document):
    return document.model_dump(mode="json")


async def verify(document, vulnerable: str, candidate: str, workdir: Path):
    workspace = Workspace.discover(ROOT)
    workdir.mkdir(parents=True, exist_ok=True)
    with StubServer() as stub:
        return await verify_document(
            payload_of(document),
            dna_id=document.dna_id,
            vulnerable_profile=workspace.registry.get(vulnerable),
            candidate_profile=workspace.registry.get(candidate),
            stub=stub,
            workdir_root=workdir,
            home=workdir,
        )


@pytest.mark.asyncio
async def test_vulnerable_to_fixed_is_verified(dna_artifact, tmp_path):
    document, _ = dna_artifact
    run = await verify(document, "razorpay-vulnerable", "razorpay-fixed", tmp_path / "vf")
    assert run.outcome.outcome == "VERIFIED"
    assert run.outcome.control["same_signature_failures"] >= 1
    assert run.outcome.candidate["same_signature_failures"] == 0
    assert "<= 1%" in run.outcome.statement


@pytest.mark.asyncio
async def test_vulnerable_to_vulnerable_is_not_verified(dna_artifact, tmp_path):
    document, _ = dna_artifact
    run = await verify(
        document, "razorpay-vulnerable", "razorpay-vulnerable", tmp_path / "vv"
    )
    assert run.outcome.outcome == "NOT_VERIFIED"
    assert run.outcome.reason == "still_reproduces"
    # Stops at the first same-signature failure instead of burning the budget.
    assert run.outcome.candidate["trials"] < run.outcome.candidate["required_valid"]


@pytest.mark.asyncio
async def test_reject_all_is_a_different_violation(dna_artifact, tmp_path):
    document, _ = dna_artifact
    run = await verify(
        document, "razorpay-vulnerable", "fixture-reject_all", tmp_path / "rej"
    )
    assert run.outcome.outcome == "NOT_VERIFIED"
    assert run.outcome.reason == "other_violation"


@pytest.mark.asyncio
async def test_fixed_control_does_not_reproduce(dna_artifact, tmp_path):
    document, _ = dna_artifact
    run = await verify(document, "razorpay-fixed", "razorpay-fixed", tmp_path / "ff")
    assert run.outcome.outcome == "INCONCLUSIVE"
    assert run.outcome.reason == "control_not_reproduced"


@pytest.mark.asyncio
async def test_crash_on_boot_is_inconclusive(dna_artifact, tmp_path):
    document, _ = dna_artifact
    run = await verify(
        document, "razorpay-vulnerable", "fixture-crash_on_boot", tmp_path / "crash"
    )
    assert run.outcome.outcome == "INCONCLUSIVE"
    assert run.outcome.reason == "candidate_insufficient_valid"