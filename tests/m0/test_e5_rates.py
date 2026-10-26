"""E5 — measured: double-fulfilment rates without and with latency faults.

Black-box means no provider fault: concurrency comes only from the delivery
group. Latency-fault runs are environment_controlled by definition (C.3). These
numbers are recorded, never asserted as a gate.
"""

from __future__ import annotations

import pytest

from .harness import run_trial, run_until_valid, valid_trials, write_measurement

TRIALS_PER_CONDITION = 20
MAX_ATTEMPTS = 22
CONDITIONS = (
    ("black_box", 0),
    ("latency_50", 50),
    ("latency_200", 200),
)


def _make_trial(stub, workdir_root, latency_ms):
    return run_trial(
        variant="vulnerable",
        stub=stub,
        workdir_root=workdir_root,
        copies=2,
        parties=None,
        latency_ms=latency_ms,
        attempt_timeout_s=15.0,
    )


@pytest.mark.slow
def test_double_fulfilment_rates(stub, tmp_path):
    results = []
    for name, latency_ms in CONDITIONS:
        trials = run_until_valid(
            lambda latency_ms=latency_ms: _make_trial(stub, tmp_path, latency_ms),
            TRIALS_PER_CONDITION,
            MAX_ATTEMPTS,
        )
        valid = valid_trials(trials)
        double = sum(1 for trial in valid if trial.fulfilment_count == 2)
        results.append(
            {
                "condition": name,
                "latency_ms": latency_ms,
                "control_level": "black_box" if latency_ms == 0 else "environment_controlled",
                "trials": len(trials),
                "valid": len(valid),
                "invalid": len(trials) - len(valid),
                "double_fulfilment": double,
                "rate": f"{double}/{len(valid)}" if valid else "0/0",
                "fulfilment_counts": [trial.fulfilment_count for trial in valid],
                "trials_detail": [trial.to_dict() for trial in trials],
            }
        )

    write_measurement(
        "e5",
        {
            "experiment": "E5",
            "classification": "measured",
            "variant": "vulnerable",
            "copies": 2,
            "trials_per_condition": TRIALS_PER_CONDITION,
            "conditions": results,
        },
    )

    assert all(condition["valid"] == TRIALS_PER_CONDITION for condition in results)
