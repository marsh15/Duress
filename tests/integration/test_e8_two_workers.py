"""E8 gate: the corrected handler stays sound with two workers."""

from __future__ import annotations

import pytest

from ..engine import built, run_built


@pytest.mark.asyncio
async def test_fixed_two_workers_matrix_pass(registry, stub, tmp_path):
    scenario = built(
        registry,
        "razorpay-fixed-2w",
        "S3",
        {"copies": 5, "fault": "rendezvous"},
    )
    result = await run_built(
        registry,
        stub,
        tmp_path / "e8",
        "razorpay-fixed-2w",
        scenario,
        policy="matrix",
        cleanup_passed=True,
    )
    assert result.result.status == "PASS"
    assert result.result.valid == 20
    assert result.result.fail == 0
    assert result.result.inconclusive == 0