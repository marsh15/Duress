"""Positive controls through real execution (plan M1 test_controls)."""

from __future__ import annotations

import pytest

from ..engine import built, run_built


@pytest.mark.asyncio
async def test_no_sig_check_fails_inv6(registry, stub, tmp_path):
    scenario = built(registry, "fixture-no_sig_check", "S8")
    result = await run_built(
        registry, stub, tmp_path / "no_sig", "fixture-no_sig_check", scenario, policy="explore"
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-6@1"


@pytest.mark.asyncio
async def test_ack_without_effect_fails_inv5(registry, stub, tmp_path):
    scenario = built(registry, "fixture-ack_without_effect", "S1")
    result = await run_built(
        registry,
        stub,
        tmp_path / "ack",
        "fixture-ack_without_effect",
        scenario,
        policy="explore",
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-5@1"
    assert result.result.signature.kind == "ack_without_effect"


@pytest.mark.asyncio
async def test_reject_all_fails_expected_effect(registry, stub, tmp_path):
    scenario = built(registry, "fixture-reject_all", "S1")
    result = await run_built(
        registry, stub, tmp_path / "reject", "fixture-reject_all", scenario, policy="explore"
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "expected-effect@1"
    assert result.result.signature.kind == "expected_effect_missing"


@pytest.mark.asyncio
async def test_crash_on_boot_is_inconclusive(registry, stub, tmp_path):
    scenario = built(registry, "fixture-crash_on_boot", "S1")
    result = await run_built(
        registry, stub, tmp_path / "crash", "fixture-crash_on_boot", scenario, policy="explore"
    )
    assert result.result.status == "INCONCLUSIVE"
    assert result.result.inconclusive == result.result.trials
    assert all(verdict.reasons == ["target_unavailable"] for verdict in result.verdicts)