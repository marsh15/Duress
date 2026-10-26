"""Engine and the independent M0 harness must agree on the same 20 trials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ..engine import built, run_built

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_engine_agrees_with_m0_e1(registry, stub, tmp_path):
    measurement_path = REPO_ROOT / "docs" / "measurements" / "e1.json"
    assert measurement_path.exists(), "run scripts/m0_gate.sh before this test"
    measurement = json.loads(measurement_path.read_text())

    scenario = built(
        registry,
        "razorpay-vulnerable",
        "S3",
        {"copies": 2, "fault": "rendezvous"},
    )
    result = await run_built(
        registry,
        stub,
        tmp_path / "engine",
        "razorpay-vulnerable",
        scenario,
        policy="matrix",
        cleanup_passed=True,
    )

    assert result.result.status == "FAIL"
    assert result.result.valid == measurement["valid"] == 20
    assert result.result.fail == measurement["double_fulfilment_with_party_release"] == 20
    assert all(verdict.signature.check == "INV-1@1" for verdict in result.verdicts)
    # The M0 direct count and the oracle agree row for row.
    assert all(
        observation.snapshot.rows("fulfilments")
        and len(observation.snapshot.rows("fulfilments")) == 2
        for observation in result.observations
    )