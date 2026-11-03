"""Readiness budget under a slow target start (INVALID trial regression).

The retained record for inv-20261027T134459-5dffbe holds one attempt with
reason ``harness:target_unavailable`` (S1, trial t7fff6ab0295f). The only code
path that appends ``target_unavailable`` is the readiness-phase timeout in
``TrialSupervisor._phases`` (src/duress/target.py). These tests reproduce that
path with the real razorpay-fixed target and a serve command that starts
listening only after a delay: when the delay exceeds the readiness budget the
attempt must be INCONCLUSIVE (never FAIL or PASS), the process group must be
reaped, and the same delay inside the budget must still run the schedule.
"""

from __future__ import annotations

import dataclasses
import os
import sys

import pytest

from duress.limits import Limits
from duress.target import TrialSupervisor

from .test_trial_phases import config, verdict


def slow_serve(delay_s: float) -> tuple[str, ...]:
    """Serve command that waits ``delay_s`` seconds before the real launcher.

    Placeholders are filled by ``ProfileSpec.format_command``. The delay models a
    slow cold start (interpreter, imports, bind) without relying on machine speed.
    """
    code = (
        "import runpy, sys, time;"
        f"time.sleep({delay_s!r});"
        "sys.argv = ['razorpay_ref.serve', '--variant', 'fixed', '--host', '{host}',"
        " '--port', '{port}', '--workers', '1'];"
        "runpy.run_module('razorpay_ref.serve', run_name='__main__')"
    )
    return (sys.executable, "-c", code)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_slow_start_past_readiness_budget_is_inconclusive(registry, stub, tmp_path):
    profile = dataclasses.replace(registry.get("razorpay-fixed"), serve_command=slow_serve(2.0))
    cfg = config(registry, stub, tmp_path, limits=Limits().with_phase("readiness", 0.5))
    cfg.profile = profile
    supervisor = TrialSupervisor(cfg)

    observation = await supervisor.run()

    assert observation.target_ready is False
    assert observation.harness_errors == ["target_unavailable"]
    assert supervisor.pgid is None or not _group_alive(supervisor.pgid)

    result = verdict(registry, "razorpay-fixed", cfg.scenario, observation)
    assert result.status == "INCONCLUSIVE"
    assert result.reasons == ["harness:target_unavailable"]


@pytest.mark.asyncio
async def test_same_slow_start_inside_budget_runs_the_schedule(registry, stub, tmp_path):
    profile = dataclasses.replace(registry.get("razorpay-fixed"), serve_command=slow_serve(0.3))
    cfg = config(registry, stub, tmp_path)
    cfg.profile = profile
    supervisor = TrialSupervisor(cfg)

    observation = await supervisor.run()

    assert observation.target_ready is True
    assert "target_unavailable" not in observation.harness_errors
    assert observation.attempts, "the schedule must reach the target once ready"
