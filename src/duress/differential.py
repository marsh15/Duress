"""Differential evaluation across reference variants (plan E.1, M4).

The same scenarios run against several targets through the trusted runner, and
each cell is compared with the verdict the plan expects. ``naive_fix`` is the
interesting column: it deduplicates by event id, so it passes the same-event
race (S3) but still fails the cross-event race (S5), where two different events
carry the same payment.
"""

from __future__ import annotations

from typing import Any

from .models import ScenarioSpec

DEFAULT_TARGETS: tuple[str, ...] = (
    "razorpay-vulnerable",
    "razorpay-naive-fix",
    "razorpay-fixed",
)

S3_RENDEZVOUS = ScenarioSpec("S3", {"copies": 3, "fault": "rendezvous", "latency_ms": 200})
S5_RENDEZVOUS = ScenarioSpec("S5", {"fault": "rendezvous", "latency_ms": 200})
S9 = ScenarioSpec("S9", {})

DEFAULT_SPECS: tuple[ScenarioSpec, ...] = (S3_RENDEZVOUS, S5_RENDEZVOUS, S9)

# E.1, restricted to the rows with a definite expected verdict per variant.
EXPECTED: dict[tuple[str, str], str] = {
    (S3_RENDEZVOUS.scenario_id, "vulnerable"): "FAIL",
    (S3_RENDEZVOUS.scenario_id, "naive_fix"): "PASS",
    (S3_RENDEZVOUS.scenario_id, "fixed"): "PASS",
    (S5_RENDEZVOUS.scenario_id, "vulnerable"): "FAIL",
    (S5_RENDEZVOUS.scenario_id, "naive_fix"): "FAIL",
    (S5_RENDEZVOUS.scenario_id, "fixed"): "PASS",
    (S9.scenario_id, "vulnerable"): "PASS",
    (S9.scenario_id, "naive_fix"): "PASS",
    (S9.scenario_id, "fixed"): "PASS",
    ("S1", "vulnerable"): "PASS",
    ("S1", "naive_fix"): "PASS",
    ("S1", "fixed"): "PASS",
    ("S2", "vulnerable"): "PASS",
    ("S2", "naive_fix"): "PASS",
    ("S2", "fixed"): "PASS",
    ("S8", "vulnerable"): "PASS",
    ("S8", "naive_fix"): "PASS",
    ("S8", "fixed"): "PASS",
}


def expected_verdict(scenario_id: str, variant: str) -> str | None:
    return EXPECTED.get((scenario_id, variant))


def cell(
    *,
    scenario_id: str,
    target_id: str,
    variant: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """One matrix cell: the observed aggregate beside the plan's expectation."""
    expected = expected_verdict(scenario_id, variant)
    observed = result.get("status")
    return {
        "scenario_id": scenario_id,
        "target_id": target_id,
        "variant": variant,
        "expected": expected,
        "observed": observed,
        # INCONCLUSIVE is never counted as agreement or disagreement.
        "agrees": None if expected is None or observed == "INCONCLUSIVE" else observed == expected,
        "signature": result.get("signature"),
        "valid": result.get("valid"),
        "fail": result.get("fail"),
        "inconclusive": result.get("inconclusive"),
        "trials": result.get("trials"),
        "reasons": result.get("reasons", []),
    }


def summarise(cells: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [item for item in cells if item["agrees"] is not None]
    return {
        "cells": len(cells),
        "compared": len(compared),
        "agree": sum(1 for item in compared if item["agrees"]),
        "disagree": sum(1 for item in compared if not item["agrees"]),
        "inconclusive": sum(1 for item in cells if item["observed"] == "INCONCLUSIVE"),
        "unexpected_cells": [
            f"{item['scenario_id']} @ {item['target_id']}: expected {item['expected']}, observed {item['observed']}"
            for item in compared
            if not item["agrees"]
        ],
    }
