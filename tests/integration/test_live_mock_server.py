"""Live investigator path over a real local HTTP socket (MOCK PROVIDER ONLY).

A local OpenAI-compatible mock (``scripts/mock_openai_server.py``) stands in for
the provider. Nothing here contacts a hosted model, and no result from these
tests may be described as genuine hosted inference.

These tests run through ``services.investigate`` with a zero wall cap, so the
model path is exercised end to end and no trial starts. They assert:

* structured output is accepted and every proposal is bounded by the closed
  scenario library;
* malformed and truncated output gets exactly one repair call;
* schema-invalid proposals fall back without ever reaching a verdict;
* the model never controls verdicts;
* HTTP 429/500, timeouts and malformed envelopes map to bounded fallbacks;
* the API key is sent only in the Authorization header and never persisted.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx  # noqa: F401  (documents that the live path rides httpx)
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from mock_openai_server import (  # noqa: E402
    MockOpenAIServer,
    Reply,
    proposal_text,
    vulnerable_proposal,
)

from duress import limits as limits_module  # noqa: E402
from duress.investigator import SCENARIO_LIBRARY, validate_params  # noqa: E402
from duress.llm import LlmConfig  # noqa: E402
from duress.profiles import ProfileRegistry  # noqa: E402
from duress.services import Workspace, investigate  # noqa: E402
from duress.store import ensure_dirs  # noqa: E402

API_KEY = "mock-secret-7f3a91"
MODEL = "mock-model"
LIBRARY_IDS = {entry["id"] for entry in SCENARIO_LIBRARY}


def workspace_at(tmp_path: Path) -> Workspace:
    home = tmp_path / ".duress"
    ensure_dirs(home)
    return Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )


async def run_live(tmp_path, base_url: str, *, timeout_s: float = 10.0):
    config = LlmConfig(base_url=base_url, model=MODEL, api_key=API_KEY, timeout_s=timeout_s)
    return await investigate(
        workspace_at(tmp_path),
        target_id="razorpay-vulnerable",
        mode="live",
        llm_config=config,
        wall_cap_s=0.0,
    )


def assert_bounded_by_library(hypothesis: dict) -> None:
    """Every scenario a proposal selected is a library template with in-bounds params."""
    for spec in hypothesis["scenario_specs"]:
        assert spec["template_id"] in LIBRARY_IDS
        assert validate_params(spec["template_id"], spec["params"]) == []


# --------------------------------------------------------------------------- #
# Normal output
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_structured_output_is_accepted_bounded_and_sent_correctly(tmp_path):
    with MockOpenAIServer([Reply.ok(proposal_text(vulnerable_proposal()))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["status"] == "completed", record["errors"]
    assert record["mode_effective"] == "live"
    assert record["fallback_reason"] is None

    [request] = server.requests
    assert request.path == "/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.body["model"] == MODEL
    assert request.body["temperature"] == 0
    assert request.body["response_format"] == {"type": "json_object"}

    [hypothesis] = record["hypotheses"]
    assert hypothesis["usable"] is True
    assert hypothesis["status"]["citation"] == "valid"
    assert hypothesis["status"]["linkage"] == "valid"
    assert hypothesis["status"]["structure"] == "pass"
    assert_bounded_by_library(hypothesis)
    assert record["selected_scenarios"]

    # The model proposes; the outcome is computed from trial results. With no
    # trials run (zero wall cap) it can only be not_run or inconclusive.
    assert hypothesis["status"]["outcome"] in {"not_run", "inconclusive"}
    assert record["verdict"] == "INCONCLUSIVE"

    # The secret reached the wire in the header only.
    assert API_KEY not in json.dumps(record)


# --------------------------------------------------------------------------- #
# Repair: exactly one retry for unparseable output
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_malformed_output_gets_exactly_one_repair_call(tmp_path):
    replies = [Reply.ok("this is not json"), Reply.ok(proposal_text(vulnerable_proposal()))]
    with MockOpenAIServer(replies) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 2
    assert record["mode_effective"] == "live"
    assert record["fallback_reason"] is None
    assert len(record["model"]["calls"]) == 2
    repair = server.requests[1].body["messages"]
    assert repair[-2] == {"role": "assistant", "content": "this is not json"}
    assert "Your previous reply was rejected" in repair[-1]["content"]


@pytest.mark.asyncio
async def test_truncated_json_is_repaired_once(tmp_path):
    truncated = proposal_text(vulnerable_proposal())[:120]
    replies = [Reply.ok(truncated), Reply.ok(proposal_text(vulnerable_proposal()))]
    with MockOpenAIServer(replies) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 2
    assert record["mode_effective"] == "live"
    assert record["usable_hypotheses"] == 1


@pytest.mark.asyncio
async def test_truncated_json_twice_falls_back_after_one_repair(tmp_path):
    truncated = proposal_text(vulnerable_proposal())[:120]
    with MockOpenAIServer([Reply.ok(truncated)]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 2  # one call, one repair, never a third
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "invalid_output"


# --------------------------------------------------------------------------- #
# Schema-invalid and linkage-invalid proposals
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unknown_template_is_not_run_and_is_not_repaired(tmp_path):
    proposal = vulnerable_proposal(scenarios=[{"template_id": "S99", "params": {}}])
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    # Linkage failures are not syntax failures: no repair call is spent on them.
    assert len(server.requests) == 1
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "no_usable_hypotheses"
    [hypothesis] = record["hypotheses"]
    assert hypothesis["usable"] is False
    assert hypothesis["status"]["linkage"] == "invalid"


@pytest.mark.asyncio
async def test_out_of_bounds_param_is_rejected_before_any_trial(tmp_path):
    proposal = vulnerable_proposal(
        scenarios=[{"template_id": "S3", "params": {"copies": 99, "fault": "rendezvous"}}]
    )
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "no_usable_hypotheses"
    [hypothesis] = record["hypotheses"]
    assert hypothesis["usable"] is False
    # The rejected parameter never reaches a scenario that would run. Exhaustive
    # fallback runs the closed library at its own bounds instead.
    assert record["mode_effective"] == "exhaustive"
    assert not any("copies=99" in item for item in record["selected_scenarios"])


@pytest.mark.asyncio
async def test_unknown_check_or_citation_outside_bundle_is_unusable(tmp_path):
    proposal = vulnerable_proposal(
        predicted_failure={"check": "INV-99@1", "kind": "duplicate_fulfilment", "role": "order:A"},
        citations=[{"file": "../../etc/passwd", "line_start": 1, "line_end": 2}],
    )
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "no_usable_hypotheses"
    [hypothesis] = record["hypotheses"]
    assert hypothesis["status"]["citation"] == "invalid"
    assert hypothesis["status"]["linkage"] == "invalid"


@pytest.mark.asyncio
async def test_schema_violating_proposal_is_repaired_then_falls_back(tmp_path):
    # Extra fields are forbidden by the closed schema; the repair reply is the same.
    proposal = vulnerable_proposal(verdict="confirmed")
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 2
    assert record["fallback_reason"] == "invalid_output"
    assert record["hypotheses"] == []


# --------------------------------------------------------------------------- #
# The model never controls verdicts
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_model_declared_outcome_is_rejected_not_adopted(tmp_path):
    proposal = vulnerable_proposal()
    proposal["hypotheses"][0]["status"] = {"outcome": "confirmed"}
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "invalid_output"
    assert record["verdict"] == "INCONCLUSIVE"
    assert all(item["status"]["outcome"] != "confirmed" for item in record["hypotheses"])


@pytest.mark.asyncio
async def test_top_level_verdict_in_reply_is_rejected(tmp_path):
    proposal = {**vulnerable_proposal(), "verdict": "PASS"}
    with MockOpenAIServer([Reply.ok(proposal_text(proposal))]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "invalid_output"
    assert record["verdict"] == "INCONCLUSIVE"


# --------------------------------------------------------------------------- #
# Provider failures
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_rate_limit_and_server_error_fall_back_without_retry(tmp_path, status):
    with MockOpenAIServer([Reply.error(status, "provider said no")]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 1  # the client does not retry
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "transport_error"
    assert f"http {status}" in record["fallback_detail"]


@pytest.mark.asyncio
async def test_error_body_echoing_the_key_is_redacted(tmp_path):
    with MockOpenAIServer([Reply.error(500, f"auth failed for {API_KEY}")]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "transport_error"
    assert "[redacted]" in record["fallback_detail"]
    assert API_KEY not in json.dumps(record)


@pytest.mark.asyncio
async def test_slow_provider_times_out_promptly(tmp_path):
    slow = Reply.ok(proposal_text(vulnerable_proposal()), delay_s=3.0)
    started = time.monotonic()
    with MockOpenAIServer([slow]) as server:
        record = await run_live(tmp_path, server.base_url, timeout_s=0.3)
    elapsed = time.monotonic() - started

    assert record["fallback_reason"] == "transport_error"
    assert "request_timeout" in record["fallback_detail"]
    assert elapsed < 2.5, f"timeout did not bound the call: {elapsed:.2f}s"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=200, raw_body="<html>login required</html>", content_type="text/html"),
        Reply(status=200, content=None),  # provider returned null content
    ],
    ids=["html_body", "null_content"],
)
async def test_malformed_envelope_is_a_transport_fallback(tmp_path, reply):
    with MockOpenAIServer([reply]) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "transport_error"
    assert "malformed_envelope" in record["fallback_detail"]
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_repair_call_failure_still_falls_back_to_exhaustive(tmp_path):
    replies = [Reply.ok("not json"), Reply.error(500, "provider down")]
    with MockOpenAIServer(replies) as server:
        record = await run_live(tmp_path, server.base_url)

    assert len(server.requests) == 2
    assert record["mode_effective"] == "exhaustive"
    assert record["verdict"] == "INCONCLUSIVE"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT (not fixed here): orchestrator._model_phase maps a transport_error "
        "raised by the repair call to 'invalid_output'. The fix is in "
        "src/duress/orchestrator.py, which is frozen and outside this task's owned paths."
    ),
)
@pytest.mark.asyncio
async def test_repair_call_transport_failure_is_reported_as_transport_error(tmp_path):
    replies = [Reply.ok("not json"), Reply.error(500, "provider down")]
    with MockOpenAIServer(replies) as server:
        record = await run_live(tmp_path, server.base_url)

    assert record["fallback_reason"] == "transport_error"
