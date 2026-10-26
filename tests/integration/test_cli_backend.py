"""CLI contracts for the M3/M4 investigation, reduction and matrix commands."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from duress import cli, services


runner = CliRunner()


class Registry:
    def get(self, target_id: str):
        if target_id != "razorpay-vulnerable":
            raise KeyError(target_id)
        return object()


@pytest.fixture
def workspace(monkeypatch):
    store = SimpleNamespace(registry=Registry())
    monkeypatch.setattr(cli, "_workspace", lambda: store)
    return store


@pytest.mark.parametrize(
    ("status", "verdict", "expected"),
    [
        ("completed", "PASS", 0),
        ("completed", "FAIL", 1),
        ("completed", "INCONCLUSIVE", 2),
        ("failed", "FAIL", 2),
    ],
)
def test_investigate_exit_codes_follow_record_verdict(
    monkeypatch, workspace, status, verdict, expected
):
    async def fake_investigate(*args, **kwargs):
        return {
            "investigation_id": "inv-test",
            "status": status,
            "mode_requested": "cached",
            "mode_effective": "cached",
            "hypotheses": [],
            "scenarios": [],
            "findings": [],
            "reductions": [],
            "trials_used": 0,
            "trial_cap": 130,
            "verdict": verdict,
        }

    monkeypatch.setattr(services, "investigate", fake_investigate)
    result = runner.invoke(cli.app, ["investigate", "razorpay-vulnerable", "--mode", "cached"])
    assert result.exit_code == expected, result.output


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [("REDUCED", 0), ("NOT_REDUCED", 0), ("SKIPPED", 2), ("REJECTED", 4)],
)
@pytest.mark.parametrize("flags", [[], ["--black-box"]])
def test_reduce_exit_codes_follow_operation_outcome(monkeypatch, workspace, outcome, expected, flags):
    class FakeRun:
        original_dna_id = "dna-test"
        reason = None
        result = None
        revalidation = None
        reduced_document = None
        primary_dna_id = "dna-test"

        def __init__(self):
            self.outcome = outcome

        def to_json(self):
            return {"outcome": self.outcome}

    monkeypatch.setattr(cli, "load_dna_document", lambda *_: object())

    async def fake_reduce(*args, **kwargs):
        assert kwargs["black_box"] is True
        return FakeRun()

    monkeypatch.setattr(services, "reduce_dna", fake_reduce)
    monkeypatch.setattr(
        services,
        "reduction_summary",
        lambda run: {
            "reduction_id": "red-test",
            "claim": None,
            "reduced_dna_id": None,
            "primary_dna_id": "dna-test",
        },
    )
    result = runner.invoke(cli.app, ["reduce", "dna-test", *flags])
    assert result.exit_code == expected, result.output


def test_differential_cli_passes_targets_and_exits_nonzero_on_mismatch(monkeypatch, workspace):
    seen = {}

    async def fake_differential(store, *, target_ids=None, policy="explore"):
        seen.update(store=store, target_ids=target_ids, policy=policy)
        return {
            "differential_id": "diff-test",
            "cells": [
                {
                    "scenario_id": "S5[fault=rendezvous,latency_ms=200]",
                    "target_id": "razorpay-naive-fix",
                    "expected": "FAIL",
                    "observed": "PASS",
                    "agrees": False,
                }
            ],
            "summary": {"disagree": 1},
        }

    monkeypatch.setattr(services, "differential", fake_differential)
    result = runner.invoke(
        cli.app,
        ["differential", "--target", "razorpay-naive-fix", "--policy", "matrix"],
    )
    assert result.exit_code == 1, result.output
    assert seen == {
        "store": workspace,
        "target_ids": ["razorpay-naive-fix"],
        "policy": "matrix",
    }


def test_differential_cli_rejects_invalid_policy_before_starting(monkeypatch, workspace):
    async def should_not_run(*args, **kwargs):
        raise AssertionError("invalid policy reached the differential service")

    monkeypatch.setattr(services, "differential", should_not_run)
    result = runner.invoke(cli.app, ["differential", "--policy", "forever"])
    assert result.exit_code == 3
    assert "policy must be explore or matrix" in result.output
