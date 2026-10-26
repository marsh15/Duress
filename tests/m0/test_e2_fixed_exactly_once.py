"""E2 — fixed, two- and five-party duplicate groups.

Gate: 20 of 20 valid trials per group have exactly one fulfilment linked to the
payment, the payment locally captured, the order fulfilled, a party-based
release, all-2xx responses with exactly one ``processed`` and zero 5xx.
"""

from __future__ import annotations

import pytest

from .harness import run_trial, run_until_valid, valid_trials, write_measurement

REQUIRED_VALID = 20
MAX_ATTEMPTS = 22


def _make_trial(stub, workdir_root, copies):
    return run_trial(
        variant="fixed",
        stub=stub,
        workdir_root=workdir_root,
        copies=copies,
        parties=copies,
        rendezvous_timeout_ms=2000,
        attempt_timeout_s=15.0,
    )


def _problems(trial) -> list[str]:
    problems = []
    if trial.fulfilment_count != 1:
        problems.append(f"fulfilments={trial.fulfilment_count}")
    if trial.linked_count != 1:
        problems.append(f"linked={trial.linked_count}")
    if (trial.payment or {}).get("status") != "captured":
        problems.append(f"payment_status={(trial.payment or {}).get('status')}")
    if (trial.order or {}).get("status") != "fulfilled":
        problems.append(f"order_status={(trial.order or {}).get('status')}")
    if trial.released_by != "parties":
        problems.append(f"released_by={trial.released_by}")
    if trial.arrivals < trial.copies:
        problems.append(f"arrivals={trial.arrivals}")
    statuses = [response.status_code for response in trial.responses]
    if any(not (200 <= (status or 0) < 300) for status in statuses):
        problems.append(f"non_2xx={statuses}")
    if any((status or 0) >= 500 for status in statuses):
        problems.append(f"5xx={statuses}")
    processed = sum(
        1 for response in trial.responses if (response.body or {}).get("status") == "processed"
    )
    duplicates = sum(
        1 for response in trial.responses if (response.body or {}).get("status") == "duplicate"
    )
    if processed != 1:
        problems.append(f"processed={processed}")
    if duplicates != trial.copies - 1:
        problems.append(f"duplicates={duplicates}")
    return problems


@pytest.mark.parametrize("copies", [2, 5])
def test_fixed_exactly_once(copies, stub, tmp_path):
    trials = run_until_valid(
        lambda: _make_trial(stub, tmp_path, copies), REQUIRED_VALID, MAX_ATTEMPTS
    )
    valid = valid_trials(trials)
    failures = {trial.trial_id: _problems(trial) for trial in valid if _problems(trial)}

    write_measurement(
        f"e2_{copies}",
        {
            "experiment": "E2",
            "classification": "gate",
            "variant": "fixed",
            "copies": copies,
            "parties": copies,
            "required_valid": REQUIRED_VALID,
            "attempts": len(trials),
            "valid": len(valid),
            "invalid": len(trials) - len(valid),
            "exactly_once": len(valid) - len(failures),
            "problems": failures,
            "trials": [trial.to_dict() for trial in trials],
        },
    )

    assert len(valid) == REQUIRED_VALID, f"only {len(valid)} valid trials from {len(trials)} attempts"
    assert not failures, f"gate violations: {failures}"
