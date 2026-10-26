"""Operation loop behaviour: an unconfirmed kill aborts before another trial."""

from __future__ import annotations

import pytest

from duress import operations
from duress.limits import POLICIES
from duress.models import TrialObservation
from duress.operations import OperationConfig, run_scenario
from duress.provider_stub import StubServer

from ..engine import built


@pytest.mark.asyncio
async def test_unconfirmed_aborts_and_sweeps(registry, tmp_path, monkeypatch):
    profile = registry.get("razorpay-fixed")
    scenario = built(registry, "razorpay-fixed", "S1")
    calls = {"trials": 0, "sweeps": 0}

    async def fake_trial(config):
        calls["trials"] += 1
        return TrialObservation(
            trial_id="t-unconfirmed",
            target_id=profile.target_id,
            scenario_id=scenario.scenario_id,
            settlement="unconfirmed",
            harness_errors=["unconfirmed"],
        )

    def fake_sweep(home, **kwargs):
        calls["sweeps"] += 1

    monkeypatch.setattr(operations, "run_trial", fake_trial)
    monkeypatch.setattr(operations, "sweep", fake_sweep)

    result = await run_scenario(
        OperationConfig(
            profile=profile,
            scenario=scenario,
            stub=StubServer(),
            workdir_root=tmp_path,
            policy=POLICIES["explore"],
            home=tmp_path,
            keep_evidence=False,
        )
    )

    assert calls["trials"] == 1
    assert calls["sweeps"] == 1
    assert len(result.verdicts) == 1
    assert result.verdicts[0].status == "INCONCLUSIVE"
    assert result.verdicts[0].reasons == ["harness:unconfirmed"]
    assert result.result.status == "INCONCLUSIVE"