"""Phased supervisor timeouts (P1-P6) and the watchdog."""

from __future__ import annotations

import dataclasses
import os
import sys
import time
from pathlib import Path

import pytest

from duress.limits import Limits
from duress.models import LatencyFault, TableSnapshot
from duress.oracle import evaluate_trial
from duress.profiles import load_seed
from duress.target import TrialConfig, TrialSupervisor, run_trial

from ..engine import built


def config(registry, stub, tmp_path, target_id="razorpay-fixed", **kwargs):
    profile = registry.get(target_id)
    limits = kwargs.pop("limits", Limits())
    return TrialConfig(
        profile=profile,
        scenario=kwargs.pop("scenario", built(registry, target_id, "S1")),
        stub=stub,
        workdir_root=tmp_path,
        limits=limits,
        home=tmp_path,
        **kwargs,
    )


def verdict(registry, target_id, scenario, observation):
    profile = registry.get(target_id)
    seed = load_seed(profile)
    return evaluate_trial(
        observation,
        schedule=scenario.schedule,
        expectation=scenario.expectation,
        roles=profile.roles,
        baseline=TableSnapshot(tables=seed.tables()),
        seed_order_ids={row["id"] for row in seed.orders},
    )


def test_phase_timeouts_sum_to_the_watchdog():
    assert sum(Limits().phase_timeouts.values()) == Limits().watchdog_s == 65.0


@pytest.mark.asyncio
async def test_prepare_timeout(registry, stub, tmp_path):
    profile = dataclasses.replace(
        registry.get("razorpay-fixed"),
        init_command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    cfg = config(registry, stub, tmp_path, limits=Limits().with_phase("prepare", 0.1))
    cfg.profile = profile
    supervisor = TrialSupervisor(cfg)
    observation = await supervisor.run()
    assert "prepare_timeout" in observation.harness_errors
    assert supervisor.proc is None
    assert observation.settlement == "clean"


@pytest.mark.asyncio
async def test_readiness_timeout_then_kill(registry, stub, tmp_path):
    profile = dataclasses.replace(
        registry.get("razorpay-fixed"),
        serve_command=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    cfg = config(registry, stub, tmp_path, limits=Limits().with_phase("readiness", 0.2))
    cfg.profile = profile
    supervisor = TrialSupervisor(cfg)
    observation = await supervisor.run()
    assert observation.target_ready is False
    assert "target_unavailable" in observation.harness_errors
    assert supervisor.pgid is None or not _group_alive(supervisor.pgid)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_drain_timeout_is_forced(registry, stub, tmp_path):
    scenario = built(registry, "fixture-hang_on_shutdown", "S1")
    cfg = config(
        registry,
        stub,
        tmp_path,
        "fixture-hang_on_shutdown",
        scenario=scenario,
        limits=Limits().with_phase("drain", 0.3),
    )
    observation = await run_trial(cfg)
    assert observation.settlement == "forced"
    result = verdict(registry, "fixture-hang_on_shutdown", scenario, observation)
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["settlement:forced"]


@pytest.mark.asyncio
async def test_watchdog_fires(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-fixed", "S1")
    scenario.schedule.faults = [LatencyFault(ms=3000)]
    cfg = config(registry, stub, tmp_path, scenario=scenario, limits=Limits(watchdog_s=0.5))
    observation = await run_trial(cfg)
    assert observation.watchdog_fired is True
    assert "watchdog" in observation.harness_errors
    result = verdict(registry, "razorpay-fixed", scenario, observation)
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["harness:watchdog"]


@pytest.mark.asyncio
async def test_evaluation_timeout(registry, stub, tmp_path, monkeypatch):
    supervisor = TrialSupervisor(config(registry, stub, tmp_path))
    supervisor.limits = supervisor.limits.with_phase("evaluate", 0.05)

    def slow_read(path):
        time.sleep(0.3)
        return {}

    monkeypatch.setattr("duress.target.read_tables", slow_read)
    await supervisor._evaluate()
    assert "evaluation_timeout" in supervisor.observation.harness_errors
    assert supervisor.observation.snapshot is None


@pytest.mark.asyncio
async def test_kill_confirm_timeout(registry, stub, tmp_path):
    supervisor = TrialSupervisor(config(registry, stub, tmp_path))
    supervisor.limits = supervisor.limits.with_phase("kill_confirm", 0.05)
    supervisor.pgid = os.getpgid(0)
    assert await supervisor._confirm_group_gone() is False