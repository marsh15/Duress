"""E1 — vulnerable, two simultaneous duplicate deliveries, two-party rendezvous.

Gate: in >= 19 of 20 valid trials the order has two fulfilments, both linked to
the expected payment, and the barrier was released by party count (not by
timeout) with both parties having arrived.
"""

from __future__ import annotations

from .harness import run_trial, run_until_valid, valid_trials, write_measurement

REQUIRED_VALID = 20
MAX_ATTEMPTS = 22


def _make_trial(stub, workdir_root):
    return run_trial(
        variant="vulnerable",
        stub=stub,
        workdir_root=workdir_root,
        copies=2,
        parties=2,
        rendezvous_timeout_ms=2000,
        attempt_timeout_s=15.0,
    )


def test_vulnerable_two_party_race(stub, tmp_path):
    trials = run_until_valid(lambda: _make_trial(stub, tmp_path), REQUIRED_VALID, MAX_ATTEMPTS)
    valid = valid_trials(trials)
    expected_payment = "pay_A1"
    hits = [
        trial
        for trial in valid
        if trial.fulfilment_count == 2
        and trial.released_by == "parties"
        and trial.arrivals >= 2
        # F2: both duplicate rows must reference the delivery's payment, not just
        # any payment of the order (pay_A2 exists in the seed).
        and all(row["payment_id"] == expected_payment for row in trial.fulfilments)
    ]

    write_measurement(
        "e1",
        {
            "experiment": "E1",
            "classification": "gate",
            "variant": "vulnerable",
            "copies": 2,
            "parties": 2,
            "expected_payment": expected_payment,
            "duplicate_rows_linked_to_expected_payment": all(
                row["payment_id"] == expected_payment
                for trial in valid
                for row in trial.fulfilments
            ),
            "required_valid": REQUIRED_VALID,
            "attempts": len(trials),
            "valid": len(valid),
            "invalid": len(trials) - len(valid),
            "double_fulfilment_with_party_release": len(hits),
            "release_conditions": {
                str(condition): sum(1 for trial in valid if trial.released_by == condition)
                for condition in {trial.released_by for trial in valid}
            },
            "fulfilment_counts": [trial.fulfilment_count for trial in valid],
            "trials": [trial.to_dict() for trial in trials],
        },
    )

    assert len(valid) == REQUIRED_VALID, f"only {len(valid)} valid trials from {len(trials)} attempts"
    assert len(hits) >= 19, f"double fulfilment with party release in {len(hits)}/{len(valid)} valid trials"
    assert all(trial.arrivals >= 2 for trial in hits)
