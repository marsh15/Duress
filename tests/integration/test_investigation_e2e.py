"""Cached investigations against real managed targets (M3 acceptance).

These use the committed fixtures in ``fixtures/llm/`` through the default
cache path, so they also fail if a fixture goes stale (source or prompt
changed without re-running ``scripts/build_llm_fixture.py``): staleness shows
up as ``fallback_reason == "cache_miss"``.

The fixtures are authored, not model output; the record says so.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from duress import investigator
from duress.llm import LlmClient
from duress import limits as limits_module
from duress.profiles import ProfileRegistry
from duress.services import Workspace, investigate
from duress.store import ensure_dirs, iter_pidfiles, load_dna_payload
from duress.preflight import preflight_async

ROOT = Path(__file__).resolve().parents[2]


def workspace_at(tmp_path: Path) -> Workspace:
    home = tmp_path / ".duress"
    ensure_dirs(home)
    return Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )


@pytest.fixture
def no_network(monkeypatch):
    async def explode(*args, **kwargs):
        raise AssertionError("cached investigations must not call the model provider")

    # Managed targets use HTTP for readiness and webhook delivery. Forbidding
    # every AsyncClient also breaks those trusted execution paths before a
    # scenario can run. Block model inference and all non-loopback requests.
    monkeypatch.setattr(LlmClient, "_live", explode)
    original_send = httpx.AsyncClient.send

    async def loopback_only(client, request, *args, **kwargs):
        assert request.url.host in {"127.0.0.1", "localhost", "::1"}, request.url.host
        return await original_send(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "send", loopback_only)


def by_id(record: dict) -> dict[str, dict]:
    return {item["draft"]["id"]: item for item in record["hypotheses"]}


def test_naive_fix_bundle_contains_the_code_it_delegates_to():
    registry = ProfileRegistry.discover(ROOT)
    bundle = investigator.build_source_bundle(registry.get("razorpay-naive-fix"))
    assert "razorpay_ref/naive_fix/handler.py" in bundle.files
    assert "razorpay_ref/vulnerable/handler.py" in bundle.files
    assert "razorpay_ref/fixed/handler.py" not in bundle.files


@pytest.mark.asyncio
async def test_cached_investigation_on_vulnerable_confirms_and_writes_dna(tmp_path, no_network):
    workspace = workspace_at(tmp_path)
    record = await investigate(
        workspace, target_id="razorpay-vulnerable", mode="cached", llm_config=None, reduce=True
    )
    assert record["status"] == "completed", record["errors"]
    assert record["fallback_reason"] is None, record["fallback_detail"]
    assert record["mode_effective"] == "cached"
    assert record["model"]["response_source"] == "cache"
    assert record["model"]["provenance"]["source"] == "authored_fixture"

    hypotheses = by_id(record)
    assert all(item["usable"] for item in hypotheses.values())
    assert hypotheses["H1"]["status"]["structure"] == "pass"
    confirmed = [item for item in hypotheses.values() if item["status"]["outcome"] == "confirmed"]
    assert confirmed, [item["status"] for item in hypotheses.values()]

    # Confirmation comes from execution: the confirmed hypothesis' scenario failed
    # with the predicted check on the predicted role.
    results = {entry["scenario_id"]: entry["result"] for entry in record["scenarios"]}
    for item in confirmed:
        failing = [results[sid] for sid in item["scenario_ids"] if results[sid]["status"] == "FAIL"]
        assert failing
        assert failing[0]["signature"]["check"] == item["draft"]["predicted_failure"]["check"]

    assert record["verdict"] == "FAIL"
    assert record["dna_ids"], record["findings"]
    [reduction] = record["reductions"]
    assert reduction["outcome"] == "NOT_REDUCED"
    assert 10 <= reduction["trials"] <= 11  # fault-free characterization; candidates are pinned
    assert reduction["black_box_rate"] is not None
    assert reduction["primary_dna_id"] == record["dna_ids"][0]
    persisted_reduction = workspace.home / "reductions" / f"{reduction['reduction_id']}.json"
    assert persisted_reduction.is_file()
    assert record["trials_used"] <= limits_module.INVESTIGATION_TRIAL_CAP
    assert record["duration_s"] <= record["wall_cap_s"]
    # The DNA it wrote is a valid, replayable artifact.
    payload = load_dna_payload(workspace.home, record["dna_ids"][0])
    assert payload["provenance"]["investigation_id"] == record["investigation_id"]
    assert payload["characterization"]["black_box_rate"] == reduction["black_box_rate"]
    check = await preflight_async(payload, profile=workspace.registry.get("razorpay-vulnerable"))
    assert check.ok, check.rejected
    assert list(iter_pidfiles(workspace.home)) == []


@pytest.mark.asyncio
async def test_cached_investigation_on_fixed_finds_no_failure(tmp_path, no_network):
    workspace = workspace_at(tmp_path)
    record = await investigate(workspace, target_id="razorpay-fixed", mode="cached", llm_config=None)
    assert record["status"] == "completed", record["errors"]
    assert record["fallback_reason"] is None, record["fallback_detail"]
    for entry in record["scenarios"]:
        assert entry["result"]["status"] == "PASS", entry
    assert record["dna_ids"] == []
    hypotheses = by_id(record)
    # The fixed handler re-checks under BEGIN IMMEDIATE: the structure check says so,
    # and execution refutes the predicted race.
    assert hypotheses["H1"]["status"]["structure"] == "fail"
    assert {item["status"]["outcome"] for item in hypotheses.values()} <= {"refuted", "inconclusive"}


@pytest.mark.asyncio
async def test_cached_investigation_on_naive_fix_separates_same_event_and_cross_event(tmp_path, no_network):
    workspace = workspace_at(tmp_path)
    record = await investigate(workspace, target_id="razorpay-naive-fix", mode="cached", llm_config=None)
    assert record["status"] == "completed", record["errors"]
    assert record["fallback_reason"] is None, record["fallback_detail"]
    hypotheses = by_id(record)
    # Same-event duplicates are absorbed by the event-id table; the cross-event race is not.
    assert hypotheses["H1"]["status"]["outcome"] == "refuted"
    assert hypotheses["H2"]["status"]["outcome"] == "confirmed"
    assert record["verdict"] == "FAIL"
    assert len(record["dna_ids"]) == 1
    [finding] = record["findings"]
    assert finding["scenario_id"].startswith("S5")
    assert finding["hypothesis_ids"] == ["H2"]
