"""Fast verdict matrix (plan M1 test_matrix_fast)."""

from __future__ import annotations

import pytest

from ..engine import built, run_built

CASES = [
    ("razorpay-vulnerable", "S1", {}, "PASS", None),
    ("razorpay-vulnerable", "S2", {}, "PASS", None),
    ("razorpay-vulnerable", "S8", {}, "PASS", None),
    ("razorpay-fixed", "S1", {}, "PASS", None),
    ("razorpay-fixed", "S2", {}, "PASS", None),
    ("razorpay-fixed", "S3", {"copies": 3, "fault": "rendezvous"}, "PASS", None),
    ("razorpay-fixed", "S8", {}, "PASS", None),
    ("razorpay-vulnerable", "S3", {"copies": 3, "fault": "rendezvous"}, "FAIL", "INV-1@1"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("target,template,params,status,check", CASES)
async def test_matrix_fast(registry, stub, tmp_path, target, template, params, status, check):
    scenario = built(registry, target, template, params)
    result = await run_built(
        registry,
        stub,
        tmp_path / target,
        target,
        scenario,
        policy="explore",
    )
    assert result.result.status == status
    if check is None:
        assert result.result.fail == 0
    else:
        assert result.result.signature.check == check