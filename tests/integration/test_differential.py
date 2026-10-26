"""M4 differential service and a real negative verify-fix control."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from duress import services
from duress.differential import DEFAULT_SPECS, DEFAULT_TARGETS
from duress.limits import DEFAULT_LIMITS
from duress.store import ensure_dirs, iter_pidfiles


def workspace(tmp_path, registry):
    home = tmp_path / ".duress"
    ensure_dirs(home)
    return services.Workspace(
        root=Path(__file__).resolve().parents[2], home=home,
        registry=registry, limits=DEFAULT_LIMITS,
    )


@pytest.mark.asyncio
async def test_real_differential_matrix_persists_all_nine_cells(tmp_path, registry):
    store = workspace(tmp_path, registry)
    record = await services.differential(store)
    assert record["status"] == "completed"
    assert record["targets"] == list(DEFAULT_TARGETS)
    assert set(record["scenarios"]) == {spec.scenario_id for spec in DEFAULT_SPECS}
    assert record["summary"] == {
        "cells": 9, "compared": 9, "agree": 9, "disagree": 0,
        "inconclusive": 0, "unexpected_cells": [],
    }
    assert all(cell["valid"] == 5 and cell["trials"] >= 5 for cell in record["cells"])
    assert services.load_differential(store, record["differential_id"]) == record
    assert list(iter_pidfiles(store.home)) == []


@pytest.mark.asyncio
async def test_s5_dna_is_not_verified_by_event_id_only_repair(tmp_path, registry):
    store = workspace(tmp_path, registry)
    document, _ = await services.build_dna(
        store, target_id="razorpay-vulnerable", template_id="S5",
        params={"fault": "rendezvous", "latency_ms": 200},
    )
    assert document.signature().check == "INV-1@1"
    run = await services.verify_fix(
        store, dna_id=document.dna_id, vulnerable_target_id="razorpay-vulnerable",
        candidate_target_id="razorpay-naive-fix",
    )
    assert run.outcome.outcome == "NOT_VERIFIED", run.outcome
    assert run.outcome.reason == "still_reproduces"
    assert run.outcome.candidate["same_signature_failures"] >= 1
    assert list(iter_pidfiles(store.home)) == []
