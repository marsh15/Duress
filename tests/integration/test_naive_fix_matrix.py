"""Differential cells for naive_fix: event-id dedupe stops S3 but not S5 (plan M4)."""

from __future__ import annotations

import pytest

from ..engine import built, run_built

TARGET = "razorpay-naive-fix"
RENDEZVOUS = {"fault": "rendezvous", "latency_ms": 200}


@pytest.mark.asyncio
async def test_s3_same_event_replayed_is_deduplicated(registry, stub, tmp_path):
    scenario = built(registry, TARGET, "S3", {"copies": 3, **RENDEZVOUS})
    result = await run_built(registry, stub, tmp_path / "s3", TARGET, scenario)

    assert result.result.status == "PASS"
    scopes = {
        attempt.body_scope
        for observation in result.observations
        for attempt in observation.attempts
        if attempt.body_status == "duplicate"
    }
    assert "event" in scopes


@pytest.mark.asyncio
async def test_s5_distinct_events_for_one_payment_still_double_fulfil(registry, stub, tmp_path):
    scenario = built(registry, TARGET, "S5", dict(RENDEZVOUS))
    result = await run_built(registry, stub, tmp_path / "s5", TARGET, scenario)

    assert result.result.status == "FAIL"
    assert result.result.signature.check == "INV-1@1"
    assert result.result.signature.kind == "duplicate_fulfilment"


@pytest.mark.asyncio
async def test_s9_second_payment_is_an_order_duplicate(registry, stub, tmp_path):
    scenario = built(registry, TARGET, "S9")
    result = await run_built(registry, stub, tmp_path / "s9", TARGET, scenario)

    assert result.result.status == "PASS"
