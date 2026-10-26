"""Model client for the investigator (plan C.12, C.14).

Two modes share one interface:

* ``cached``: a fixture keyed by ``sha256(model + prompt)``. No client is ever
  built and the network is never touched.
* ``live``: one OpenAI-compatible ``/chat/completions`` POST in JSON mode.

Both modes spend the same :class:`ModelBudget` (a cached hit is still a model
call), and the budget is checked before any I/O. The API key is only ever put
in the ``Authorization`` header; it is not logged, cached, or echoed in errors.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from urllib.parse import urlparse

import httpx

from duress import limits
from duress.models import canonical_json
from duress.store import atomic_write_json

LlmFailure = Literal["transport_error", "cache_miss", "limit_exceeded", "not_configured"]

_DETAIL_BODY_CHARS = 300


class LlmError(Exception):
    def __init__(self, reason: LlmFailure, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason: LlmFailure = reason
        self.detail = detail


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    model: str
    api_key: str | None = None
    timeout_s: float = limits.MODEL_TIMEOUT_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmConfig | None":
        source = os.environ if env is None else env
        base_url = (source.get("DURESS_LLM_BASE_URL") or "").strip()
        model = (source.get("DURESS_LLM_MODEL") or "").strip()
        if not base_url or not model:
            return None
        api_key = (source.get("DURESS_LLM_API_KEY") or "").strip() or None
        return cls(base_url=base_url, model=model, api_key=api_key)

    def redacted(self) -> dict[str, Any]:
        return {
            "base_url_host": urlparse(self.base_url).hostname or "",
            "model": self.model,
            "api_key": "set" if self.api_key else "unset",
        }


@dataclass(frozen=True)
class LlmResponse:
    content: str
    model: str
    key: str
    source: Literal["live", "cache"]
    usage: dict[str, Any]
    latency_s: float
    provenance: dict[str, Any]


def render_prompt(messages: list[dict[str, str]]) -> str:
    return canonical_json(messages)


def cache_key(model: str, prompt: str) -> str:
    return hashlib.sha256((model + prompt).encode()).hexdigest()


def estimate_tokens(text: str) -> int:
    """Conservative token estimate: one token per 4 UTF-8 bytes, rounded up.

    Bytes (not characters) so non-ASCII text, which tokenizes worse, is not
    under-counted. Provider-reported usage corrects any shortfall via
    :meth:`ModelBudget.settle`.
    """
    return ceil(len(text.encode("utf-8")) / 4)


class LlmCache:
    """Fixture store: ``<dir>/<key>.json``, searched in order across ``read_dirs``."""

    def __init__(self, read_dirs: Sequence[Path], write_dir: Path | None = None) -> None:
        self.read_dirs = [Path(d) for d in read_dirs]
        self.write_dir = Path(write_dir) if write_dir is not None else None

    @staticmethod
    def _filename(key: str) -> str:
        if not key or "/" in key or "\\" in key or key.startswith("."):
            raise ValueError(f"invalid cache key {key!r}")
        return f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            name = self._filename(key)
        except ValueError:
            return None
        for directory in self.read_dirs:
            path = directory / name
            if not path.is_file():
                continue
            try:
                entry = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if _valid_entry(entry, key):
                return entry
        return None

    def put(self, key: str, entry: dict[str, Any]) -> Path:
        if self.write_dir is None:
            raise ValueError("cache has no write_dir")
        path = self.write_dir / self._filename(key)
        atomic_write_json(path, entry)
        return path


def _valid_entry(entry: Any, key: str) -> bool:
    if not isinstance(entry, dict) or entry.get("key") != key:
        return False
    response = entry.get("response")
    return isinstance(response, dict) and isinstance(response.get("content"), str)


@dataclass
class ModelBudget:
    max_calls: int = limits.MODEL_CALLS
    max_input_tokens: int = limits.MODEL_INPUT_TOKENS
    calls: int = 0
    input_tokens: int = 0

    def reserve(self, estimated_tokens: int) -> None:
        if self.calls + 1 > self.max_calls:
            raise LlmError("limit_exceeded", f"model calls > {self.max_calls}")
        if self.input_tokens + estimated_tokens > self.max_input_tokens:
            raise LlmError(
                "limit_exceeded",
                f"input tokens {self.input_tokens + estimated_tokens} > {self.max_input_tokens}",
            )
        self.calls += 1
        self.input_tokens += estimated_tokens

    def settle(self, reported_prompt_tokens: int | None, estimated: int) -> None:
        """Charge a valid provider count and reject results that exceed the cap."""
        if (
            not isinstance(reported_prompt_tokens, int)
            or isinstance(reported_prompt_tokens, bool)
            or reported_prompt_tokens < 0
            or reported_prompt_tokens <= estimated
        ):
            return
        settled = self.input_tokens + reported_prompt_tokens - estimated
        self.input_tokens = settled
        if settled > self.max_input_tokens:
            raise LlmError(
                "limit_exceeded",
                f"input tokens {settled} > {self.max_input_tokens}",
            )

    def snapshot(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "max_calls": self.max_calls,
            "input_tokens": self.input_tokens,
            "max_input_tokens": self.max_input_tokens,
        }


class LlmClient:
    """In live mode the request model is ``config.model``; ``model`` must equal it."""

    def __init__(
        self,
        *,
        mode: Literal["live", "cached"],
        model: str,
        cache: LlmCache,
        budget: ModelBudget | None = None,
        config: LlmConfig | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        record: bool = True,
    ) -> None:
        self.mode = mode
        self.model = model
        self.cache = cache
        self.budget = budget if budget is not None else ModelBudget()
        self.config = config
        self.transport = transport
        self.record = record
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: list[dict[str, str]]) -> LlmResponse:
        prompt = render_prompt(messages)
        key = cache_key(self.model, prompt)
        estimated = estimate_tokens(prompt)
        source: Literal["live", "cache"] = "cache" if self.mode == "cached" else "live"
        started = time.monotonic()
        try:
            self.budget.reserve(estimated)
            if self.mode == "cached":
                response = self._from_cache(key, started)
                self.budget.settle(response.usage.get("prompt_tokens"), estimated)
            else:
                response = await self._live(messages, prompt, key, estimated, started)
        except LlmError as exc:
            self._audit(source, key, False, exc.reason, started, estimated, {})
            raise
        self._audit(response.source, key, True, None, started, estimated, response.usage)
        return response

    def _audit(
        self,
        source: str,
        key: str,
        ok: bool,
        reason: str | None,
        started: float,
        estimated: int,
        usage: dict[str, Any],
    ) -> None:
        self.calls.append(
            {
                "source": source,
                "key": key,
                "ok": ok,
                "reason": reason,
                "latency_s": time.monotonic() - started,
                "estimated_tokens": estimated,
                "usage": usage,
            }
        )

    def _from_cache(self, key: str, started: float) -> LlmResponse:
        entry = self.cache.get(key)
        if entry is None:
            raise LlmError("cache_miss", key)
        response = entry["response"]
        usage = _clean_usage(response.get("usage"))
        provenance = entry.get("provenance")
        return LlmResponse(
            content=response["content"],
            model=str(entry.get("model", self.model)),
            key=key,
            source="cache",
            usage=dict(usage) if isinstance(usage, dict) else {},
            latency_s=time.monotonic() - started,
            provenance=dict(provenance) if isinstance(provenance, dict) else {},
        )

    async def _live(
        self,
        messages: list[dict[str, str]],
        prompt: str,
        key: str,
        estimated: int,
        started: float,
    ) -> LlmResponse:
        config = self.config
        if config is None:
            raise LlmError("not_configured", "no LLM configuration")
        if config.model != self.model:
            raise LlmError("not_configured", "client model does not match config model")

        timeout_s = _bounded_timeout(config.timeout_s)

        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        body = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        try:
            async with asyncio.timeout(timeout_s):
                async with httpx.AsyncClient(
                    transport=self.transport, timeout=httpx.Timeout(timeout_s)
                ) as client:
                    http_response = await client.post(url, json=body, headers=headers)
        except TimeoutError:
            raise LlmError("transport_error", "request_timeout") from None
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise LlmError(
                "transport_error", _scrub(f"{type(exc).__name__}: {exc}", config)
            ) from None

        if not http_response.is_success:
            snippet = _scrub(http_response.text, config)[:_DETAIL_BODY_CHARS]
            raise LlmError("transport_error", f"http {http_response.status_code}: {snippet}")

        content, usage = _parse_envelope(http_response)
        self.budget.settle(usage.get("prompt_tokens"), estimated)
        provenance = {"source": "live", **config.redacted()}
        response = LlmResponse(
            content=content,
            model=config.model,
            key=key,
            source="live",
            usage=usage,
            latency_s=time.monotonic() - started,
            provenance=provenance,
        )
        if self.record and self.cache.write_dir is not None:
            entry = {
                "key": key,
                "model": config.model,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "response": {"content": content, "usage": usage},
                "provenance": {**provenance, "recorded_at": _now()},
                "created_at": _now(),
            }
            try:
                self.cache.put(key, entry)
            except OSError:
                pass  # recording is best effort; the live answer is still valid
        return response


def _scrub(text: str, config: LlmConfig) -> str:
    return text.replace(config.api_key, "[redacted]") if config.api_key else text


def _bounded_timeout(timeout_s: float) -> float:
    try:
        requested = float(timeout_s)
    except (TypeError, ValueError, OverflowError):
        raise LlmError("not_configured", "invalid model timeout") from None
    if not isfinite(requested) or requested <= 0:
        raise LlmError("not_configured", "invalid model timeout")
    return min(requested, limits.MODEL_TIMEOUT_S)


def _clean_usage(raw_usage: Any) -> dict[str, int]:
    usage: dict[str, int] = {}
    if isinstance(raw_usage, dict):
        for field in ("prompt_tokens", "completion_tokens"):
            value = raw_usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[field] = value
    return usage


def _parse_envelope(http_response: httpx.Response) -> tuple[str, dict[str, Any]]:
    try:
        data = http_response.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LlmError("transport_error", "malformed_envelope") from None
    if not isinstance(content, str):
        raise LlmError("transport_error", "malformed_envelope")
    return content, _clean_usage(data.get("usage"))
