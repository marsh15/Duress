"""Isolation, loopback, pidfile sweep and WAL-copy guarantees (M1)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from duress.models import (
    StubObservation,
    TableSnapshot,
)
from duress.oracle import evaluate_trial
from duress.profiles import load_seed
from duress.scenarios import event_payload
from duress.signing import sign
from duress.store import (
    Pidfile,
    group_exists,
    process_start_time,
    sweep,
    write_pidfile,
)
from duress.target import TrialConfig, TrialSupervisor, run_trial

from ..engine import built

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_config(registry, stub, workdir, target_id="razorpay-fixed", scenario=None, **kwargs):
    profile = registry.get(target_id)
    return TrialConfig(
        profile=profile,
        scenario=scenario or built(registry, target_id, "S1"),
        stub=stub,
        workdir_root=workdir,
        home=workdir,
        **kwargs,
    )


def evaluate(registry, target_id, scenario, observation):
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


@pytest.mark.asyncio
async def test_spawn_orphan_leaves_no_process_and_no_write(registry, stub, tmp_path):
    scenario = built(registry, "fixture-spawn_orphan", "S1")
    supervisor = TrialSupervisor(
        make_config(registry, stub, tmp_path, "fixture-spawn_orphan", scenario)
    )
    observation = await supervisor.run()
    assert supervisor.pgid is not None
    assert not group_exists(supervisor.pgid)
    assert observation.settlement == "clean", (
        observation.settlement,
        observation.harness_errors,
        observation.phase_timings,
    )
    assert not any(
        row["id"] == "orphan_marker" for row in observation.snapshot.rows("payments")
    )


def test_sigint_cleanup(tmp_path):
    """SIGINT to the CLI drains the target group and removes the pidfile."""
    home = tmp_path / ".duress"
    env = os.environ.copy()
    env["DURESS_HOME"] = str(home)
    env["DURESS_ROOT"] = str(REPO_ROOT)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT / "src"), str(REPO_ROOT / "targets")]
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "duress.cli",
            "run",
            "fixture-hang_on_shutdown",
            "--scenario",
            "S1",
            "--policy",
            "explore",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    pgid = None
    pidfile = None
    try:
        run_dir = home / "run"
        for _ in range(500):
            files = list(run_dir.glob("*.pid")) if run_dir.exists() else []
            if files:
                pidfile = files[0]
                pgid = json.loads(pidfile.read_text())["pgid"]
                break
            time.sleep(0.02)
        assert pidfile is not None, "the CLI never started a target"
        os.kill(process.pid, signal.SIGINT)
        process.communicate(timeout=20)
        assert process.returncode in {0, 2, 130}
        for _ in range(250):
            if not group_exists(pgid):
                break
            time.sleep(0.02)
        assert not group_exists(pgid)
        assert not pidfile.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if pgid is not None and group_exists(pgid):
            os.killpg(pgid, signal.SIGKILL)


@pytest.mark.asyncio
async def test_cancellation_kills_the_group(registry, stub, tmp_path):
    scenario = built(registry, "fixture-hang_on_shutdown", "S1")
    supervisor = TrialSupervisor(
        make_config(registry, stub, tmp_path, "fixture-hang_on_shutdown", scenario)
    )
    task = asyncio.create_task(supervisor.run())
    await asyncio.sleep(0.6)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if supervisor.pgid is None or not group_exists(supervisor.pgid):
            break
        await asyncio.sleep(0.02)
    assert supervisor.pgid is None or not group_exists(supervisor.pgid)


def test_pidfile_sweep_kills_matching_groups_and_spares_reused_pids(tmp_path):
    home = tmp_path / ".duress"
    (home / "run").mkdir(parents=True)

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    pgid = os.getpgid(process.pid)
    start_time = process_start_time(process.pid)
    write_pidfile(
        home,
        "t-sweep",
        Pidfile(
            pgid=pgid,
            leader=process.pid,
            instance="t-sweep",
            started_at="2026-01-01T00:00:00+00:00",
            start_time=start_time,
        ),
    )
    result = sweep(home)
    assert result.killed
    process.wait(timeout=5)
    assert not group_exists(pgid)

    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        start_new_session=True,
    )
    other_pgid = os.getpgid(other.pid)
    write_pidfile(
        home,
        "t-stale",
        Pidfile(
            pgid=other_pgid,
            leader=other.pid,
            instance="t-stale",
            started_at="2026-01-01T00:00:00+00:00",
            start_time="a-different-start-time",
        ),
    )
    result = sweep(home)
    assert result.stale
    assert group_exists(other_pgid)
    os.killpg(other_pgid, signal.SIGKILL)
    other.wait(timeout=5)


@pytest.mark.asyncio
async def test_stale_call_makes_the_trial_inconclusive(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-fixed", "S1")
    supervisor = TrialSupervisor(make_config(registry, stub, tmp_path, scenario=scenario))
    assert await supervisor._prepare()
    assert await supervisor._start()
    stub.retire(supervisor.trial_id)

    delivery = scenario.schedule.deliveries[0]
    body = json.dumps(event_payload(scenario.schedule, delivery, scenario.roles), separators=(",", ":")).encode()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": delivery.event_id,
        "X-Razorpay-Signature": sign(supervisor.secret, body),
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"http://127.0.0.1:{supervisor.port}/webhooks/razorpay",
            content=body,
            headers=headers,
        )
    assert response.status_code == 502
    await supervisor._settle()
    supervisor.observation.stub = StubObservation(**supervisor._stub_stats())
    verdict = evaluate(registry, "razorpay-fixed", scenario, supervisor.observation)
    assert verdict.status == "INCONCLUSIVE"
    assert verdict.reasons == ["harness:isolation"]


@pytest.mark.asyncio
async def test_wrong_instance_is_refused(registry, stub, tmp_path):
    scenario = built(registry, "razorpay-fixed", "S1")
    observation = await run_trial(
        make_config(registry, stub, tmp_path, scenario=scenario, instance_override="wrong-instance")
    )
    assert observation.instance_ok is False
    assert "instance_mismatch" in observation.harness_errors
    verdict = evaluate(registry, "razorpay-fixed", scenario, observation)
    assert verdict.status == "INCONCLUSIVE"
    assert verdict.reasons == ["harness:instance_mismatch"]


@pytest.mark.asyncio
async def test_target_bind_loopback(registry, stub, tmp_path):
    with pytest.raises(ValueError):
        TrialSupervisor(make_config(registry, stub, tmp_path, host="0.0.0.0"))

    supervisor = TrialSupervisor(make_config(registry, stub, tmp_path))
    assert await supervisor._prepare()
    assert await supervisor._start()
    try:
        local = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            local.connect(("8.8.8.8", 80))
            interface_ip = local.getsockname()[0]
        finally:
            local.close()
        if interface_ip.startswith("127."):
            pytest.skip("no non-loopback interface")
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://{interface_ip}:{supervisor.port}/healthz", timeout=0.4)
        assert httpx.get(
            f"http://127.0.0.1:{supervisor.port}/healthz", timeout=1.0
        ).status_code == 200
    finally:
        await supervisor._settle()


@pytest.mark.asyncio
async def test_wal_copy_after_sigkill_is_readable(registry, stub, tmp_path):
    """A8: the copy of db + wal + shm survives a forced kill."""
    scenario = built(registry, "razorpay-fixed", "S1")
    supervisor = TrialSupervisor(make_config(registry, stub, tmp_path, scenario=scenario))
    assert await supervisor._prepare()
    assert await supervisor._start()
    await supervisor._execute()
    supervisor._signal_group(signal.SIGKILL)
    await supervisor._settle()
    assert supervisor.observation.snapshot is not None
    assert supervisor.observation.schema_ok is True
    assert supervisor.observation.attempts