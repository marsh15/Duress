"""E4 — measured: is the effect committed after the client gives up?

Attempt timeout 0.3 s, provider latency 1 s, one delivery, then SIGTERM.
This is a measurement, not a gate: A9 (handler behaviour after disconnect) is an
assumption until the numbers say otherwise.
"""

from __future__ import annotations

import pytest

from .harness import run_trial, run_until_valid, valid_trials, write_measurement

TRIALS = 10
MAX_ATTEMPTS = 12


def _make_trial(stub, workdir_root):
    return run_trial(
        variant="fixed",
        stub=stub,
        workdir_root=workdir_root,
        copies=1,
        latency_ms=1000,
        attempt_timeout_s=0.3,
        allow_timeouts=True,
    )


@pytest.mark.slow
def test_effect_commit_after_client_disconnect(stub, tmp_path):
    trials = run_until_valid(lambda: _make_trial(stub, tmp_path), TRIALS, MAX_ATTEMPTS)
    valid = valid_trials(trials)

    committed = sum(1 for trial in valid if trial.fulfilment_count == 1)
    timed_out = sum(
        1 for trial in valid if any(r.outcome == "timeout" for r in trial.responses)
    )

    write_measurement(
        "e4",
        {
            "experiment": "E4",
            "classification": "measured",
            "variant": "fixed",
            "latency_ms": 1000,
            "attempt_timeout_s": 0.3,
            "trials": len(trials),
            "valid": len(valid),
            "invalid": len(trials) - len(valid),
            "client_timed_out": timed_out,
            "effect_committed": committed,
            "rate": f"{committed}/{len(valid)}" if valid else "0/0",
            "trials_detail": [trial.to_dict() for trial in trials],
        },
    )

    assert len(valid) == TRIALS, f"only {len(valid)} valid trials from {len(trials)} attempts"
