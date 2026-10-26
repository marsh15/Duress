"""M2 scenario matrix cells, including MP-1 and MP-2 end to end."""

from __future__ import annotations

import pytest

from ..engine import built, run_built


@pytest.mark.asyncio
async def test_s5_two_events_for_one_payment(registry, stub, tmp_path):
    vulnerable = built(registry, "razorpay-vulnerable", "S5")
    result = await run_built(
        registry, stub, tmp_path / "s5v", "razorpay-vulnerable", vulnerable
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-1@1"

    fixed = built(registry, "razorpay-fixed", "S5")
    result = await run_built(registry, stub, tmp_path / "s5f", "razorpay-fixed", fixed)
    assert result.result.status == "PASS"
    assert result.result.valid >= 5


@pytest.mark.asyncio
async def test_s6_timeout_then_retry(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-vulnerable", "S6")
    result = await run_built(
        registry, stub, tmp_path / "s6v", "razorpay-vulnerable", scenario
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-1@1"
    for observation, verdict in zip(result.observations, result.verdicts):
        outcomes = [attempt.outcome for attempt in observation.attempts]
        assert outcomes[0] == "timeout"
        assert len(outcomes) == 2
        # The retry must stay inside the first attempt's provider window.
        assert observation.execution_deadline_exceeded is False

    fixed = built(registry, "razorpay-fixed", "S6")
    result = await run_built(registry, stub, tmp_path / "s6f", "razorpay-fixed", fixed)
    assert result.result.status == "PASS"


@pytest.mark.asyncio
async def test_s9_fixed_second_payment_is_an_order_duplicate(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-fixed", "S9")
    result = await run_built(
        registry, stub, tmp_path / "s9f", "razorpay-fixed", scenario, policy="matrix"
    )
    assert result.result.status == "PASS"
    assert result.result.valid == 20
    for observation in result.observations:
        rows = observation.snapshot.rows("fulfilments")
        assert len(rows) == 1
        assert rows[0]["payment_id"] == "pay_A1"
        assert rows[0]["order_id"] == "order_A"
        payments = {row["id"]: row for row in observation.snapshot.rows("payments")}
        assert payments["pay_A2"]["status"] == "authorized"
        assert payments["pay_A1"]["status"] == "captured"
        second = [
            attempt for attempt in observation.attempts if attempt.delivery_id == "d2"
        ]
        assert second[-1].body_status == "duplicate"
        assert second[-1].body_scope == "order"


@pytest.mark.asyncio
async def test_s9_vulnerable_also_passes(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-vulnerable", "S9")
    result = await run_built(
        registry, stub, tmp_path / "s9v", "razorpay-vulnerable", scenario, policy="matrix"
    )
    assert result.result.status == "PASS"
    assert result.result.valid == 20


@pytest.mark.asyncio
async def test_mp2_mark_duplicate_captured_fails_inv4(registry, stub, tmp_path):
    scenario = built(registry, "fixture-mark_duplicate_captured", "S9")
    result = await run_built(
        registry,
        stub,
        tmp_path / "mp2",
        "fixture-mark_duplicate_captured",
        scenario,
    )
    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-4@1"
    assert result.result.signature.kind == "captured_without_fulfilment"
    assert result.result.signature.roles == ["order:A", "payment:A2"]