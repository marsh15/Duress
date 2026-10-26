"""E3 — process settlement: SIGTERM during an in-flight request.

Gate: 10 of 10 valid trials exit 0 within 10 s, the process group is confirmed
gone, and the fulfilment was committed before exit.
"""

from __future__ import annotations

from .harness import run_trial, run_until_valid, valid_trials, write_measurement

REQUIRED_VALID = 10
MAX_ATTEMPTS = 12
LATENCY_MS = 2000
SIGTERM_AFTER_S = 0.5


def _make_trial(stub, workdir_root):
    return run_trial(
        variant="fixed",
        stub=stub,
        workdir_root=workdir_root,
        copies=1,
        latency_ms=LATENCY_MS,
        sigterm_after_s=SIGTERM_AFTER_S,
        attempt_timeout_s=10.0,
    )


def test_sigterm_drains_in_flight_request(stub, tmp_path):
    trials = run_until_valid(lambda: _make_trial(stub, tmp_path), REQUIRED_VALID, MAX_ATTEMPTS)
    valid = valid_trials(trials)

    problems = {}
    for trial in valid:
        issues = []
        if trial.exit_code != 0:
            issues.append(f"exit_code={trial.exit_code}")
        if not trial.group_gone:
            issues.append("group_not_gone")
        if trial.fulfilment_count != 1:
            issues.append(f"fulfilments={trial.fulfilment_count}")
        if len(trial.responses) != 1 or trial.responses[0].status_code != 200:
            issues.append(f"response={trial.responses[0].to_dict() if trial.responses else None}")
        if issues:
            problems[trial.trial_id] = issues

    write_measurement(
        "e3",
        {
            "experiment": "E3",
            "classification": "gate",
            "variant": "fixed",
            "latency_ms": LATENCY_MS,
            "sigterm_after_s": SIGTERM_AFTER_S,
            "required_valid": REQUIRED_VALID,
            "attempts": len(trials),
            "valid": len(valid),
            "invalid": len(trials) - len(valid),
            "clean_settlement": len(valid) - len(problems),
            "problems": problems,
            "drain_seconds": [trial.phase_timings.get("drain") for trial in valid],
            "trials": [trial.to_dict() for trial in trials],
        },
    )

    assert len(valid) == REQUIRED_VALID, f"only {len(valid)} valid trials from {len(trials)} attempts"
    assert not problems, f"settlement violations: {problems}"
