"""E6 — measured: phase timings P1-P6 and trial p50 / p95."""

from __future__ import annotations

import pytest

from .harness import percentile, run_trial, run_until_valid, valid_trials, write_measurement

TRIALS = 10
MAX_ATTEMPTS = 12
PHASES = ("prepare", "readiness", "execute", "drain", "kill_confirm", "evaluate")


def _make_trial(stub, workdir_root):
    return run_trial(
        variant="fixed",
        stub=stub,
        workdir_root=workdir_root,
        copies=2,
        parties=2,
        rendezvous_timeout_ms=2000,
        attempt_timeout_s=15.0,
    )


@pytest.mark.slow
def test_phase_timings(stub, tmp_path):
    trials = run_until_valid(lambda: _make_trial(stub, tmp_path), TRIALS, MAX_ATTEMPTS)
    valid = valid_trials(trials)

    totals = [trial.total_s for trial in valid]
    phase_stats = {}
    for phase in PHASES:
        values = [trial.phase_timings.get(phase, 0.0) for trial in valid]
        phase_stats[phase] = {
            "p50": round(percentile(values, 0.50), 4),
            "p95": round(percentile(values, 0.95), 4),
            "max": round(max(values), 4) if values else 0.0,
        }

    write_measurement(
        "e6",
        {
            "experiment": "E6",
            "classification": "measured",
            "variant": "fixed",
            "copies": 2,
            "trials": len(trials),
            "valid": len(valid),
            "invalid": len(trials) - len(valid),
            "total_p50_s": round(percentile(totals, 0.50), 4),
            "total_p95_s": round(percentile(totals, 0.95), 4),
            "total_max_s": round(max(totals), 4) if totals else 0.0,
            "phase_stats": phase_stats,
            "trials_detail": [trial.to_dict() for trial in trials],
        },
    )

    assert len(valid) == TRIALS, f"only {len(valid)} valid trials from {len(trials)} attempts"
