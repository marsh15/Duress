"""Model client: cache, budget, and the live transport contract (plan C.12/C.14)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from duress import limits
from duress.llm import (
    LlmCache,
    LlmClient,
    LlmConfig,
    LlmError,
    ModelBudget,
    cache_key,
    estimate_tokens,
    render_prompt,
)

SECRET = "sk-super-secret-key-123"
MESSAGES = [{"role": "user", "content": "hi"}]
MODEL = "m1"


def _config(**overrides) -> LlmConfig:
    values = {"base_url": "https://api.example.com/v1/", "model": MODEL, "api_key": SECRET}
    values.update(overrides)
    return LlmConfig(**values)


def _envelope(content="{}", usage=None) -> dict:
    body: dict = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return body


class _Recorder:
    """MockTransport handler that records requests."""

    def __init__(self, response: httpx.Response | Exception):
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _transport(response: httpx.Response | Exception) -> tuple[httpx.MockTransport, _Recorder]:
    recorder = _Recorder(response)
    return httpx.MockTransport(recorder), recorder


class _DelayedTransport(httpx.AsyncBaseTransport):
    def __init__(self, delay_s: float):
        self.delay_s = delay_s
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        await asyncio.sleep(self.delay_s)
        return httpx.Response(200, json=_envelope(), request=request)


def _live(tmp_path: Path, response, **kwargs) -> tuple[LlmClient, _Recorder]:
    transport, recorder = _transport(response)
    client = LlmClient(
        mode="live",
        model=MODEL,
        cache=LlmCache([], write_dir=tmp_path / "cache"),
        config=kwargs.pop("config", _config()),
        transport=transport,
        **kwargs,
    )
    return client, recorder


def _stored_entry(key: str, content: str = '{"ok":true}') -> dict:
    return {
        "key": key,
        "model": MODEL,
        "prompt_sha256": "x",
        "response": {"content": content, "usage": {"prompt_tokens": 7}},
        "provenance": {"source": "live", "model": MODEL},
        "created_at": "2026-01-01T00:00:00+00:00",
    }


# --------------------------------------------------------------------------- #
# config / keys
# --------------------------------------------------------------------------- #


def test_from_env_present_and_absent():
    config = LlmConfig.from_env(
        {
            "DURESS_LLM_BASE_URL": "https://api.example.com:8443/v1",
            "DURESS_LLM_MODEL": "m1",
            "DURESS_LLM_API_KEY": SECRET,
        }
    )
    assert config is not None
    assert (config.base_url, config.model, config.api_key) == (
        "https://api.example.com:8443/v1",
        "m1",
        SECRET,
    )
    assert config.timeout_s == limits.MODEL_TIMEOUT_S

    keyless = LlmConfig.from_env({"DURESS_LLM_BASE_URL": "http://h/v1", "DURESS_LLM_MODEL": "m"})
    assert keyless is not None and keyless.api_key is None

    assert LlmConfig.from_env({}) is None
    assert LlmConfig.from_env({"DURESS_LLM_BASE_URL": "http://h/v1"}) is None
    assert LlmConfig.from_env({"DURESS_LLM_MODEL": "m"}) is None
    assert LlmConfig.from_env({"DURESS_LLM_BASE_URL": "", "DURESS_LLM_MODEL": "m"}) is None


def test_redacted_hides_key_and_path():
    redacted = _config().redacted()
    assert redacted == {"base_url_host": "api.example.com", "model": MODEL, "api_key": "set"}
    assert _config(api_key=None).redacted()["api_key"] == "unset"
    assert SECRET not in json.dumps(redacted)


def test_cache_key_golden_and_prompt_rendering():
    prompt = render_prompt(MESSAGES)
    assert prompt == '[{"content":"hi","role":"user"}]'
    assert cache_key("m1", prompt) == (
        "912249ecfe5e0454a4f312310e2813951892ed8b65b6c2c827ca1c2c0a26bb36"
    )
    assert cache_key("m1", prompt) == hashlib.sha256(("m1" + prompt).encode()).hexdigest()
    assert cache_key("m2", prompt) != cache_key("m1", prompt)


def test_estimate_tokens_rounds_bytes_up():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("é") == 1  # 2 bytes
    assert estimate_tokens("日本") == 2  # 6 bytes


# --------------------------------------------------------------------------- #
# cached mode
# --------------------------------------------------------------------------- #


def _explode_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        pytest.fail("cached mode touched the network")

    return httpx.MockTransport(handler)


def _forbid_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        pytest.fail("cached mode created an httpx client")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    monkeypatch.setattr(httpx, "Client", boom)


@pytest.mark.asyncio
async def test_cached_hit_makes_no_network_calls(tmp_path, monkeypatch):
    key = cache_key(MODEL, render_prompt(MESSAGES))
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / f"{key}.json").write_text(json.dumps(_stored_entry(key)))
    _forbid_http_client(monkeypatch)
    client = LlmClient(
        mode="cached",
        model=MODEL,
        cache=LlmCache([fixtures]),
        transport=_explode_transport(),
    )

    response = await client.complete(MESSAGES)

    assert response.content == '{"ok":true}'
    assert response.source == "cache"
    assert response.key == key
    assert response.usage == {"prompt_tokens": 7}
    assert response.provenance == {"source": "live", "model": MODEL}
    assert client.budget.calls == 1
    assert client.calls[0]["ok"] is True and client.calls[0]["source"] == "cache"
    assert set(client.calls[0]) == {
        "source",
        "key",
        "ok",
        "reason",
        "latency_s",
        "estimated_tokens",
        "usage",
    }


@pytest.mark.asyncio
async def test_cached_miss_is_cache_miss_without_network(tmp_path, monkeypatch):
    _forbid_http_client(monkeypatch)
    client = LlmClient(
        mode="cached",
        model=MODEL,
        cache=LlmCache([tmp_path]),
        transport=_explode_transport(),
    )
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "cache_miss"
    assert client.calls[0]["ok"] is False and client.calls[0]["reason"] == "cache_miss"


@pytest.mark.asyncio
async def test_cached_mode_ignores_config_and_never_goes_live(tmp_path, monkeypatch):
    _forbid_http_client(monkeypatch)
    client = LlmClient(
        mode="cached",
        model=MODEL,
        cache=LlmCache([tmp_path], write_dir=tmp_path),
        config=_config(),
    )
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "cache_miss"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "not json {",
        "[]",
        json.dumps({"key": "wrong", "response": {"content": "x"}}),
        json.dumps({"key": "KEY", "response": {"content": 5}}),
        json.dumps({"key": "KEY", "response": "x"}),
        json.dumps({"key": "KEY"}),
    ],
)
async def test_corrupt_cache_file_is_a_miss(tmp_path, payload):
    key = cache_key(MODEL, render_prompt(MESSAGES))
    (tmp_path / f"{key}.json").write_text(payload.replace("KEY", key))
    cache = LlmCache([tmp_path])
    assert cache.get(key) is None
    client = LlmClient(mode="cached", model=MODEL, cache=cache)
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "cache_miss"


def test_cache_searches_read_dirs_in_order_and_rejects_bad_keys(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (second / "k.json").write_text(json.dumps(_stored_entry("k", "from-second")))
    cache = LlmCache([first, second])
    assert cache.get("k")["response"]["content"] == "from-second"
    (first / "k.json").write_text("not json")
    assert cache.get("k")["response"]["content"] == "from-second"
    (first / "k.json").write_text(json.dumps(_stored_entry("k", "from-first")))
    assert cache.get("k")["response"]["content"] == "from-first"
    assert cache.get("../k") is None
    with pytest.raises(ValueError):
        cache.put("k", _stored_entry("k"))  # no write_dir


def test_cache_put_round_trips_atomically(tmp_path):
    cache = LlmCache([tmp_path / "w"], write_dir=tmp_path / "w")
    path = cache.put("k", _stored_entry("k"))
    assert path == tmp_path / "w" / "k.json"
    assert cache.get("k")["response"]["content"] == '{"ok":true}'
    assert not list((tmp_path / "w").glob("*.tmp"))


# --------------------------------------------------------------------------- #
# live mode
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_live_success_sends_contract_and_records_without_secret(tmp_path):
    client, recorder = _live(
        tmp_path,
        httpx.Response(
            200,
            json=_envelope('{"a":1}', {"prompt_tokens": 12, "completion_tokens": 5, "extra": 1}),
        ),
    )

    response = await client.complete(MESSAGES)

    assert response.content == '{"a":1}'
    assert response.source == "live"
    assert response.model == MODEL
    assert response.usage == {"prompt_tokens": 12, "completion_tokens": 5}
    assert response.key == cache_key(MODEL, render_prompt(MESSAGES))
    assert response.provenance == {
        "source": "live",
        "base_url_host": "api.example.com",
        "model": MODEL,
        "api_key": "set",
    }

    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.example.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    body = json.loads(request.content)
    assert body == {
        "model": MODEL,
        "messages": MESSAGES,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    cache_file = tmp_path / "cache" / f"{response.key}.json"
    raw = cache_file.read_bytes()
    assert SECRET.encode() not in raw
    entry = json.loads(raw)
    assert entry["key"] == response.key
    assert entry["model"] == MODEL
    assert entry["prompt_sha256"] == hashlib.sha256(render_prompt(MESSAGES).encode()).hexdigest()
    assert entry["response"] == {
        "content": '{"a":1}',
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    }
    assert entry["provenance"]["source"] == "live" and "recorded_at" in entry["provenance"]
    assert "created_at" in entry
    for path in (tmp_path).rglob("*"):
        if path.is_file():
            assert SECRET.encode() not in path.read_bytes()

    assert SECRET not in json.dumps(client.calls)
    assert client.calls[0]["ok"] is True and client.calls[0]["source"] == "live"

    # the recording is a valid cached-mode fixture
    replay = LlmClient(
        mode="cached", model=MODEL, cache=LlmCache([tmp_path / "cache"]), transport=_explode_transport()
    )
    cached = await replay.complete(MESSAGES)
    assert cached.content == '{"a":1}' and cached.source == "cache"


@pytest.mark.asyncio
async def test_live_without_api_key_sends_no_authorization(tmp_path):
    client, recorder = _live(
        tmp_path, httpx.Response(200, json=_envelope()), config=_config(api_key=None)
    )
    await client.complete(MESSAGES)
    assert "authorization" not in recorder.requests[0].headers


@pytest.mark.asyncio
async def test_live_record_false_or_no_write_dir_writes_nothing(tmp_path):
    client, _ = _live(tmp_path, httpx.Response(200, json=_envelope()), record=False)
    await client.complete(MESSAGES)
    assert not (tmp_path / "cache").exists()

    transport, _ = _transport(httpx.Response(200, json=_envelope()))
    readonly = LlmClient(
        mode="live", model=MODEL, cache=LlmCache([tmp_path]), config=_config(), transport=transport
    )
    await readonly.complete(MESSAGES)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_live_http_500_is_transport_error_with_status(tmp_path):
    body = f"boom {SECRET} " + "x" * 1000
    client, _ = _live(tmp_path, httpx.Response(500, text=body))
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "transport_error"
    assert "500" in raised.value.detail
    assert SECRET not in raised.value.detail and SECRET not in str(raised.value)
    assert len(raised.value.detail) < 400
    assert not (tmp_path / "cache").exists()
    assert client.calls[0]["ok"] is False and client.calls[0]["reason"] == "transport_error"


@pytest.mark.asyncio
async def test_live_http_error_redacts_before_truncating_body(tmp_path):
    body = "x" * (300 - len(SECRET) + 1) + SECRET + " tail"
    client, _ = _live(tmp_path, httpx.Response(400, text=body))

    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)

    assert raised.value.reason == "transport_error"
    assert "[redacted]" in raised.value.detail
    assert SECRET not in str(raised.value)


@pytest.mark.asyncio
async def test_live_connect_error_is_transport_error(tmp_path):
    client, _ = _live(tmp_path, httpx.ConnectError(f"refused {SECRET}"))
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "transport_error"
    assert "ConnectError" in raised.value.detail
    assert SECRET not in raised.value.detail


@pytest.mark.asyncio
async def test_live_timeout_is_transport_error(tmp_path):
    client, _ = _live(tmp_path, httpx.ReadTimeout("slow"))
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "transport_error"
    assert "ReadTimeout" in raised.value.detail


@pytest.mark.asyncio
async def test_live_timeout_is_a_total_wall_clock_limit(tmp_path):
    delay = _DelayedTransport(delay_s=0.1)
    client = LlmClient(
        mode="live",
        model=MODEL,
        cache=LlmCache([], write_dir=tmp_path / "cache"),
        config=_config(timeout_s=0.01),
        transport=delay,
        record=False,
    )

    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)

    assert raised.value.reason == "transport_error"
    assert raised.value.detail == "request_timeout"
    assert len(delay.requests) == 1
    assert client.calls[0]["reason"] == "transport_error"


@pytest.mark.asyncio
async def test_live_timeout_cannot_exceed_plan_limit(tmp_path, monkeypatch):
    original_timeout = httpx.Timeout
    seen: list[float] = []

    def record_timeout(value):
        seen.append(value)
        return original_timeout(value)

    monkeypatch.setattr(httpx, "Timeout", record_timeout)
    client, _ = _live(
        tmp_path,
        httpx.Response(200, json=_envelope()),
        config=_config(timeout_s=limits.MODEL_TIMEOUT_S * 2),
        record=False,
    )

    await client.complete(MESSAGES)

    assert seen == [limits.MODEL_TIMEOUT_S]


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_s", [0, -1, float("inf"), float("nan")])
async def test_invalid_live_timeout_is_not_configured_without_network(tmp_path, timeout_s):
    client, recorder = _live(
        tmp_path,
        httpx.Response(200, json=_envelope()),
        config=_config(timeout_s=timeout_s),
    )

    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)

    assert raised.value.reason == "not_configured"
    assert recorder.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": 5}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
        httpx.Response(200, json=["choices"]),
        httpx.Response(200, json={"error": {"message": "unsupported success envelope"}}),
        httpx.Response(
            200,
            json={"choices": [{"message": {"content": [{"type": "text", "text": "{}"}]}}]},
        ),
    ],
)
async def test_live_malformed_envelope_is_transport_error(tmp_path, response):
    client, _ = _live(tmp_path, response)
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "transport_error"
    assert raised.value.detail == "malformed_envelope"
    assert not (tmp_path / "cache").exists()


@pytest.mark.asyncio
async def test_live_without_config_is_not_configured(tmp_path):
    client = LlmClient(
        mode="live",
        model=MODEL,
        cache=LlmCache([], write_dir=tmp_path),
        transport=_explode_transport(),
    )
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "not_configured"


@pytest.mark.asyncio
async def test_live_model_must_match_config(tmp_path):
    client, recorder = _live(tmp_path, httpx.Response(200, json=_envelope()), config=_config(model="other"))
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "not_configured"
    assert recorder.requests == []


# --------------------------------------------------------------------------- #
# budget
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_fourth_call_is_limit_exceeded_without_network(tmp_path):
    client, recorder = _live(tmp_path, httpx.Response(200, json=_envelope()), record=False)
    for _ in range(limits.MODEL_CALLS):
        await client.complete(MESSAGES)
    assert len(recorder.requests) == 3
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "limit_exceeded"
    assert len(recorder.requests) == 3
    assert client.budget.calls == 3


@pytest.mark.asyncio
async def test_fourth_cached_call_is_also_limited(tmp_path):
    key = cache_key(MODEL, render_prompt(MESSAGES))
    (tmp_path / f"{key}.json").write_text(json.dumps(_stored_entry(key)))
    client = LlmClient(mode="cached", model=MODEL, cache=LlmCache([tmp_path]))
    for _ in range(3):
        await client.complete(MESSAGES)
    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)
    assert raised.value.reason == "limit_exceeded"


@pytest.mark.asyncio
async def test_oversized_prompt_is_limit_exceeded_without_network(tmp_path):
    client, recorder = _live(tmp_path, httpx.Response(200, json=_envelope()))
    huge = [{"role": "user", "content": "x" * (limits.MODEL_INPUT_TOKENS * 4 + 8)}]
    with pytest.raises(LlmError) as raised:
        await client.complete(huge)
    assert raised.value.reason == "limit_exceeded"
    assert recorder.requests == []
    assert client.budget.calls == 0 and client.budget.input_tokens == 0
    assert client.calls[0]["reason"] == "limit_exceeded"


@pytest.mark.asyncio
async def test_oversized_prompt_in_cached_mode_never_reads_cache(tmp_path):
    client = LlmClient(mode="cached", model=MODEL, cache=LlmCache([tmp_path]))
    huge = [{"role": "user", "content": "x" * (limits.MODEL_INPUT_TOKENS * 4 + 8)}]
    with pytest.raises(LlmError) as raised:
        await client.complete(huge)
    assert raised.value.reason == "limit_exceeded"


def test_budget_reserve_and_snapshot_boundaries():
    budget = ModelBudget()
    budget.reserve(limits.MODEL_INPUT_TOKENS)  # exactly at the cap is allowed
    assert budget.snapshot() == {
        "calls": 1,
        "max_calls": 3,
        "input_tokens": 30_000,
        "max_input_tokens": 30_000,
    }
    with pytest.raises(LlmError):
        budget.reserve(1)
    assert budget.calls == 1 and budget.input_tokens == 30_000


def test_settle_adds_only_underestimates():
    budget = ModelBudget()
    budget.reserve(100)
    budget.settle(None, 100)
    assert budget.input_tokens == 100
    budget.settle(80, 100)
    assert budget.input_tokens == 100
    budget.settle(250, 100)
    assert budget.input_tokens == 250


@pytest.mark.asyncio
async def test_live_usage_underestimate_is_charged_to_the_budget(tmp_path):
    budget = ModelBudget()
    client, _ = _live(
        tmp_path,
        httpx.Response(200, json=_envelope(usage={"prompt_tokens": 29_000})),
        budget=budget,
        record=False,
    )
    await client.complete(MESSAGES)
    estimated = estimate_tokens(render_prompt(MESSAGES))
    assert budget.input_tokens == 29_000
    assert 29_000 > estimated
    with pytest.raises(LlmError) as raised:
        await client.complete([{"role": "user", "content": "x" * 8_000}])
    assert raised.value.reason == "limit_exceeded"


@pytest.mark.asyncio
async def test_live_usage_over_budget_rejects_response_and_does_not_cache(tmp_path):
    client, _ = _live(
        tmp_path,
        httpx.Response(
            200,
            json=_envelope(usage={"prompt_tokens": limits.MODEL_INPUT_TOKENS + 1}),
        ),
    )

    with pytest.raises(LlmError) as raised:
        await client.complete(MESSAGES)

    assert raised.value.reason == "limit_exceeded"
    assert client.calls[0]["reason"] == "limit_exceeded"
    assert client.budget.input_tokens == limits.MODEL_INPUT_TOKENS + 1
    assert not (tmp_path / "cache").exists()


@pytest.mark.asyncio
async def test_cached_usage_is_reconciled_and_over_budget_hit_is_rejected(tmp_path):
    key = cache_key(MODEL, render_prompt(MESSAGES))
    entry = _stored_entry(key)
    entry["response"]["usage"] = {"prompt_tokens": 100}
    (tmp_path / f"{key}.json").write_text(json.dumps(entry))
    budget = ModelBudget()
    client = LlmClient(mode="cached", model=MODEL, cache=LlmCache([tmp_path]), budget=budget)

    response = await client.complete(MESSAGES)

    assert response.source == "cache"
    assert budget.input_tokens == 100

    entry["response"]["usage"] = {"prompt_tokens": limits.MODEL_INPUT_TOKENS + 1}
    (tmp_path / f"{key}.json").write_text(json.dumps(entry))
    over_budget = LlmClient(mode="cached", model=MODEL, cache=LlmCache([tmp_path]))
    with pytest.raises(LlmError) as raised:
        await over_budget.complete(MESSAGES)
    assert raised.value.reason == "limit_exceeded"
    assert over_budget.calls[0]["reason"] == "limit_exceeded"
    assert over_budget.budget.input_tokens == limits.MODEL_INPUT_TOKENS + 1


@pytest.mark.asyncio
async def test_live_ignores_invalid_reported_usage_fields(tmp_path):
    client, _ = _live(
        tmp_path,
        httpx.Response(
            200,
            json=_envelope(
                usage={"prompt_tokens": -3, "completion_tokens": True, "extra": 20}
            ),
        ),
        record=False,
    )

    response = await client.complete(MESSAGES)

    assert response.usage == {}
