"""Investigation modes, fallbacks and limits (plan C.12, M3 acceptance).

Most cases use a zero wall cap: the model path is fully exercised, every
scenario is selected, and the runner refuses to start any trial because less
than the 65 s start margin remains. That isolates the mode mapping from trial
execution and also proves the short-wall-cap rule (``budget_exhausted``, no
process left alive).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from duress import investigator
from duress import limits as limits_module
from duress.llm import LlmCache, LlmConfig, cache_key, render_prompt
from duress.orchestrator import FIXTURE_MODEL
from duress.profiles import ProfileRegistry
from duress.services import Workspace, investigate
from duress.store import ensure_dirs, iter_pidfiles

ROOT = Path(__file__).resolve().parents[2]
CONFIG = LlmConfig(base_url="http://llm.invalid/v1", model="fake-model", api_key="secret-key")


def workspace_at(tmp_path: Path) -> Workspace:
    home = tmp_path / ".duress"
    ensure_dirs(home)
    return Workspace(
        root=ROOT,
        home=home,
        registry=ProfileRegistry.discover(ROOT),
        limits=limits_module.DEFAULT_LIMITS,
    )


def handler_line(text: str) -> int:
    lines = (ROOT / "targets/razorpay_ref/vulnerable/handler.py").read_text().splitlines()
    return next(index for index, line in enumerate(lines, start=1) if text in line)


def good_output() -> dict:
    read = handler_line('"SELECT status, amount, currency FROM payments')
    insert = handler_line('"INSERT INTO fulfilments')
    return {
        "hypotheses": [
            {
                "id": "H1",
                "title": "Status check and fulfilment insert are not atomic",
                "mechanism": "Two deliveries read 'not captured' before either commits.",
                "mechanism_kind": "check_then_act_race",
                "citations": [
                    {
                        "file": "razorpay_ref/vulnerable/handler.py",
                        "line_start": read,
                        "line_end": insert,
                        "quote": "INSERT INTO fulfilments",
                    }
                ],
                "predicted_failure": {"check": "INV-1@1", "kind": "duplicate_fulfilment", "role": "order:A"},
                "scenarios": [{"template_id": "S3", "params": {"copies": 3, "fault": "rendezvous"}}],
            }
        ]
    }


def completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 900}},
    )


async def run(tmp_path, mode, *, transport=None, cache=None, config=CONFIG, wall_cap_s=0.0):
    workspace = workspace_at(tmp_path)
    record = await investigate(
        workspace,
        target_id="razorpay-vulnerable",
        mode=mode,
        llm_config=config,
        llm_transport=transport,
        cache=cache or LlmCache([tmp_path / "cache"], write_dir=tmp_path / "cache"),
        wall_cap_s=wall_cap_s,
    )
    return workspace, record


def assert_no_live_groups(workspace: Workspace) -> None:
    assert list(iter_pidfiles(workspace.home)) == []


@pytest.mark.asyncio
async def test_transport_error_falls_back(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    workspace, record = await run(tmp_path, "live", transport=httpx.MockTransport(handler))
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "transport_error"
    assert "secret-key" not in json.dumps(record)
    assert_no_live_groups(workspace)


@pytest.mark.asyncio
async def test_provider_http_error_falls_back(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(503, text="overloaded"))
    _, record = await run(tmp_path, "live", transport=transport)
    assert record["fallback_reason"] == "transport_error"
    assert "503" in record["fallback_detail"]


@pytest.mark.asyncio
async def test_unconfigured_live_mode_falls_back(tmp_path):
    _, record = await run(tmp_path, "live", config=None)
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "transport_error"
    assert "not_configured" in record["fallback_detail"]


@pytest.mark.asyncio
async def test_cache_miss_falls_back_without_network(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("cached mode must not create an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    _, record = await run(tmp_path, "cached", config=None)
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "cache_miss"


@pytest.mark.asyncio
async def test_cached_hit_makes_no_network_call(tmp_path, monkeypatch):
    workspace = workspace_at(tmp_path)
    profile = workspace.registry.get("razorpay-vulnerable")
    bundle = investigator.build_source_bundle(profile)
    messages = investigator.build_messages(profile, bundle, profile.roles)
    key = cache_key(FIXTURE_MODEL, render_prompt(messages))
    cache_dir = tmp_path / "fixtures"
    cache = LlmCache([cache_dir], write_dir=None)
    cache_dir.mkdir()
    (cache_dir / f"{key}.json").write_text(
        json.dumps(
            {
                "key": key,
                "model": FIXTURE_MODEL,
                "prompt_sha256": "test",
                "response": {"content": json.dumps(good_output()), "usage": {}},
                "provenance": {"source": "test-authored"},
            }
        )
    )

    def explode(*args, **kwargs):
        raise AssertionError("cached mode must not create an HTTP client")

    monkeypatch.setattr(httpx, "AsyncClient", explode)
    record = await investigate(
        workspace,
        target_id="razorpay-vulnerable",
        mode="cached",
        llm_config=None,
        cache=cache,
        wall_cap_s=0.0,
    )
    assert record["mode_effective"] == "cached"
    assert record["fallback_reason"] is None
    assert record["model"]["response_source"] == "cache"
    assert record["model"]["provenance"] == {"source": "test-authored"}
    [hypothesis] = record["hypotheses"]
    assert hypothesis["usable"] is True
    assert hypothesis["status"]["structure"] == "pass"
    # Controls first, then the hypothesis' scenario.
    assert [item.split("[")[0] for item in record["selected_scenarios"]] == ["S1", "S2", "S3"]


@pytest.mark.asyncio
async def test_malformed_output_is_repaired_once(tmp_path):
    replies = ["this is not json", json.dumps(good_output())]
    seen: list[dict] = []

    def handler(request):
        seen.append(json.loads(request.content))
        return completion(replies[len(seen) - 1])

    _, record = await run(tmp_path, "live", transport=httpx.MockTransport(handler))
    assert len(seen) == 2
    assert record["mode_effective"] == "live"
    assert record["fallback_reason"] is None
    assert len(record["model"]["calls"]) == 2
    # The repair request carries the rejected output and the validation errors.
    assert seen[1]["messages"][-2]["content"] == "this is not json"
    assert record["usable_hypotheses"] == 1


@pytest.mark.asyncio
async def test_invalid_output_after_repair_falls_back(tmp_path):
    def handler(request):
        return completion(json.dumps({"hypotheses": [{"id": "H1"}]}))

    _, record = await run(tmp_path, "live", transport=httpx.MockTransport(handler))
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "invalid_output"
    assert len(record["model"]["calls"]) == 2  # one call plus one repair, never more


@pytest.mark.asyncio
async def test_input_limit_refuses_before_network(tmp_path, monkeypatch):
    from duress import llm

    monkeypatch.setattr(llm, "estimate_tokens", lambda text: limits_module.MODEL_INPUT_TOKENS + 1)
    calls = []

    def handler(request):
        calls.append(request)
        return completion(json.dumps(good_output()))

    _, record = await run(tmp_path, "live", transport=httpx.MockTransport(handler))
    assert calls == []
    assert record["fallback_reason"] == "limit_exceeded"


@pytest.mark.asyncio
async def test_short_wall_cap_is_budget_exhausted_and_leaves_nothing_running(tmp_path):
    workspace, record = await run(tmp_path, "exhaustive", wall_cap_s=1.0)
    assert record["status"] == "completed"
    assert record["budget_exhausted"] is True
    assert record["trials_used"] == 0
    assert record["verdict"] == "INCONCLUSIVE"
    for entry in record["scenarios"]:
        assert "budget_exhausted" in entry["result"]["reasons"]
    assert_no_live_groups(workspace)


@pytest.mark.asyncio
async def test_live_response_is_recorded_without_the_api_key(tmp_path):
    transport = httpx.MockTransport(lambda request: completion(json.dumps(good_output())))
    _, record = await run(tmp_path, "live", transport=transport)
    assert record["mode_effective"] == "live"
    files = list((tmp_path / "cache").glob("*.json"))
    assert len(files) == 1
    assert "secret-key" not in files[0].read_text()
    assert "secret-key" not in json.dumps(record)


@pytest.mark.asyncio
async def test_total_model_timeout_maps_to_transport_fallback(tmp_path):
    import asyncio

    async def handler(request):
        await asyncio.sleep(1)
        return completion(json.dumps(good_output()))

    _, record = await run(
        tmp_path, "live", transport=httpx.MockTransport(handler),
        config=LlmConfig(base_url=CONFIG.base_url, model=CONFIG.model, timeout_s=0.01),
    )
    assert record["status"] == "completed", record["errors"]
    assert record["fallback_reason"] == "transport_error"
    assert "request_timeout" in record["fallback_detail"]
    assert record["mode_effective"] == "exhaustive"


@pytest.mark.asyncio
async def test_reported_token_overrun_maps_to_bounded_fallback(tmp_path):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": json.dumps(good_output())}}],
            "usage": {"prompt_tokens": limits_module.MODEL_INPUT_TOKENS + 1},
        },
    ))
    _, record = await run(tmp_path, "live", transport=transport)
    assert record["status"] == "completed", record["errors"]
    assert record["mode_effective"] == "exhaustive"
    assert record["fallback_reason"] == "limit_exceeded"
    assert len(record["model"]["calls"]) == 1
    assert list((tmp_path / "cache").glob("*.json")) == []
