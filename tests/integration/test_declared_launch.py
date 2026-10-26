"""F1: the supervisor runs the profile's declared serve command, not uvicorn."""

from __future__ import annotations

import dataclasses
import json
import sys

import pytest

from duress.models import TableSnapshot
from duress.oracle import evaluate_trial
from duress.profiles import load_seed
from duress.target import TrialConfig, run_trial

from ..engine import built

WRAPPER = """
import json, os, sys
host, port, db, seed, record = sys.argv[1:6]
json.dump(sys.argv[1:], open(record, "w"))
os.execv(sys.executable, [
    sys.executable, "-m", "razorpay_ref.serve", "--variant", "fixed",
    "--host", host, "--port", port,
])
"""


@pytest.mark.asyncio
async def test_supervisor_runs_the_declared_command(registry, stub, tmp_path):
    profile = registry.get("razorpay-fixed")
    record = tmp_path / "launch.json"
    serve_command = (
        sys.executable,
        "-c",
        WRAPPER,
        "{host}",
        "{port}",
        "{db}",
        "{seed}",
        str(record),
    )
    profile = dataclasses.replace(profile, serve_command=serve_command)
    scenario = built(registry, "razorpay-fixed", "S1")

    observation = await run_trial(
        TrialConfig(
            profile=profile,
            scenario=scenario,
            stub=stub,
            workdir_root=tmp_path,
            home=tmp_path,
        )
    )

    assert record.exists(), "the declared command did not run"
    recorded = json.loads(record.read_text())
    assert recorded[0] == "127.0.0.1"
    assert recorded[1].isdigit()
    assert recorded[2].endswith("app.db")
    assert recorded[3].endswith("seed.json")
    assert observation.exit_code == 0
    assert observation.settlement == "clean"

    seed = load_seed(registry.get("razorpay-fixed"))
    verdict = evaluate_trial(
        observation,
        schedule=scenario.schedule,
        expectation=scenario.expectation,
        roles=profile.roles,
        baseline=TableSnapshot(tables=seed.tables()),
        seed_order_ids={row["id"] for row in seed.orders},
    )
    assert verdict.status == "PASS"