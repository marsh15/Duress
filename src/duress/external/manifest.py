"""Trusted local configuration for external@1. Never load manifests from HTTP bodies."""
from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from ..models import AttemptPolicy, Step, StrictModel

IDENTIFIER = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"
ENVIRONMENT = r"^[A-Z][A-Z0-9_]{0,79}$"


class Project(StrictModel):
    schema_version: Literal[1] = 1
    project: str = Field(pattern=IDENTIFIER)


class Target(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    mode: Literal["managed", "staging"] = "managed"
    cwd: str = "."
    init: list[str] = Field(default_factory=list)
    serve: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    env_refs: dict[str, str] = Field(default_factory=dict)
    readiness_path: str = "/healthz"
    base_url: str | None = None
    allowed_origins: list[str] = Field(default_factory=list)
    staging_authorized: bool = False
    isolated_test_database: bool = False
    max_processes: int = Field(default=8, ge=1, le=32)
    memory_mb: int = Field(default=1024, ge=128, le=4096)

    @model_validator(mode="after")
    def boundary(self):
        if self.mode == "managed":
            if not self.init or not self.serve or self.base_url or self.allowed_origins:
                raise ValueError("managed targets require init/serve argv; URLs are assigned at runtime")
        else:
            if self.init or self.serve or not self.staging_authorized:
                raise ValueError("staging requires explicit authorization and cannot launch commands")
            parsed = urlsplit(self.base_url or "")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or parsed.path not in {"", "/"} or origin not in self.allowed_origins):
                raise ValueError("staging base_url must be an exact authorized origin without credentials")
        if not self.readiness_path.startswith("/") or self.readiness_path.startswith("//"):
            raise ValueError("readiness_path must be an absolute local path")
        for name in [*self.env, *self.env_refs, *self.env_refs.values()]:
            if not re.fullmatch(ENVIRONMENT, name):
                raise ValueError("environment names must be uppercase identifiers")
        reserved = {"DURESS_DB", "DURESS_SCHEMA", "DURESS_HOST", "DURESS_PORT", "DURESS_INSTANCE",
                    "DURESS_SECRET", "DURESS_FIXTURES", "DURESS_HOSTED", "DURESS_HOME"}
        if any(k in reserved for k in [*self.env, *self.env_refs]):
            raise ValueError("DURESS runtime variables are reserved")
        if any(re.search(r"SECRET|PASSWORD|TOKEN|API_KEY|CREDENTIAL", k) for k in self.env):
            raise ValueError("sensitive environment values must use env_refs")
        for command in (self.init, self.serve):
            if len(command) > 64 or any(not part or len(part) > 4096 for part in command):
                raise ValueError("command argv exceeds bounds or contains empty arguments")
        return self


class Delivery(StrictModel):
    adapter: Literal["http-json@1"] = "http-json@1"
    endpoint: str = "/webhooks"
    method: Literal["POST", "PUT", "PATCH"] = "POST"
    signature: Literal["hmac-sha256", "none"] = "hmac-sha256"
    signature_header: str = "X-Signature"
    event_id_header: str = "X-Event-Id"
    secret_ref: str | None = Field(default=None, pattern=ENVIRONMENT)
    headers: dict[str, str] = Field(default_factory=dict)
    header_refs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_headers(self):
        if not self.endpoint.startswith("/") or self.endpoint.startswith("//") or "#" in self.endpoint:
            raise ValueError("delivery endpoint must be a local absolute path")
        for name, value in [*self.headers.items(), *self.header_refs.items(),
                            (self.signature_header, ""), (self.event_id_header, "")]:
            if not re.fullmatch(r"[A-Za-z0-9-]+", name) or "\r" in value or "\n" in value:
                raise ValueError("invalid HTTP header")
            if name.lower() in {"host", "content-length", "transfer-encoding"}:
                raise ValueError("transport framing headers cannot be configured")
        if any(re.search(r"authorization|token|api.key|secret", k, re.I) for k in self.headers):
            raise ValueError("authentication headers must use header_refs")
        if any(not re.fullmatch(ENVIRONMENT, ref) for ref in self.header_refs.values()):
            raise ValueError("header_refs values must name environment variables")
        return self


class Observation(StrictModel):
    adapter: Literal["sqlite@1", "postgresql@1"] = "sqlite@1"
    database_ref: str | None = Field(default=None, pattern=ENVIRONMENT)
    observer_ref: str | None = Field(default=None, pattern=ENVIRONMENT)
    schema_name: str = Field(default="public", alias="schema", pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class FileReference(StrictModel):
    file: str


class FixturesReference(FileReference):
    events_file: str | None = None


class ExternalDelivery(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    fixture: str = Field(pattern=IDENTIFIER)
    event_id: str
    attempt_timeout_ms: int | None = Field(default=None, ge=1, le=10000)
    max_attempts: int | None = Field(default=None, ge=1, le=3)
    invalid_signature: bool = False


class Scenario(StrictModel):
    id: str = Field(pattern=IDENTIFIER)
    deliveries: list[ExternalDelivery] = Field(min_length=1, max_length=10)
    steps: list[Step] = Field(min_length=1, max_length=10)
    delivery_policy: AttemptPolicy = Field(default_factory=AttemptPolicy)
    context: dict[str, Any] = Field(default_factory=dict)
    expected_status: dict[str, Literal["2xx", "4xx"]] = Field(default_factory=dict)
    require_overlap: bool = False
    require_retry: bool = False
    settle_ms: int = Field(default=0, ge=0, le=2000)

    def delivery(self, delivery_id: str) -> ExternalDelivery:
        return next(item for item in self.deliveries if item.id == delivery_id)

    def resolve_attempts(self, delivery: ExternalDelivery) -> tuple[int, int, list[int]]:
        return (delivery.attempt_timeout_ms or self.delivery_policy.attempt_timeout_ms,
                delivery.max_attempts or self.delivery_policy.max_attempts,
                self.delivery_policy.backoff_ms)

    @model_validator(mode="after")
    def validate_schedule(self):
        ids = [d.id for d in self.deliveries]
        dispatched = [name for step in self.steps for name in step.deliveries]
        if len(set(ids)) != len(ids) or sorted(dispatched) != sorted(ids):
            raise ValueError("deliveries must have unique ids and occur in exactly one step")
        if set(self.expected_status) - set(ids):
            raise ValueError("expected_status references an unknown delivery")
        budget = sum(s.delay_before_ms for s in self.steps) + self.settle_ms
        for step in self.steps:
            if len(step.deliveries) > 10:
                raise ValueError("concurrent group exceeds 10 deliveries")
            costs = []
            for name in step.deliveries:
                timeout, attempts, backoff = self.resolve_attempts(self.delivery(name))
                if timeout > 10000 or attempts > 3 or any(x < 0 for x in backoff):
                    raise ValueError("attempt policy exceeds exec@1 limits")
                costs.append(timeout * attempts + sum(backoff[:attempts-1]))
            budget += max(costs)
        if budget > 25000:
            raise ValueError("planned execution exceeds the 25000ms budget")
        return self


class Manifest(StrictModel):
    duress: Project
    target: Target
    delivery: Delivery = Field(default_factory=Delivery)
    observation: Observation
    contract: FileReference
    fixtures: FixturesReference
    scenarios: list[Scenario] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def compatible(self):
        if len({s.id for s in self.scenarios}) != len(self.scenarios):
            raise ValueError("scenario ids must be unique")
        if self.observation.adapter == "postgresql@1":
            if not self.observation.database_ref or not self.target.isolated_test_database:
                raise ValueError("PostgreSQL requires database_ref and isolated_test_database=true")
        if self.target.mode == "staging" and self.observation.adapter != "postgresql@1":
            raise ValueError("staging currently requires independent PostgreSQL observation")
        if self.target.mode == "staging" and self.delivery.signature != "none" and not self.delivery.secret_ref:
            raise ValueError("staging signatures require a local secret_ref")
        return self


@dataclass
class LoadedProject:
    path: Path
    manifest: Manifest
    cwd: Path
    fixtures_path: Path
    fixtures: dict[str, Any]
    contract: Any
    payloads: dict[str, bytes]

    def scenario(self, name: str) -> Scenario:
        for scenario in self.manifest.scenarios:
            if scenario.id == name:
                return scenario
        raise ValueError(f"unknown scenario {name!r}")

    def resolve(self, ref: str | None) -> str:
        if not ref or not os.environ.get(ref):
            raise ValueError(f"required environment reference is unavailable: {ref or '(not configured)'}")
        return os.environ[ref]


def load_project(path: Path) -> LoadedProject:
    from .contract import EvaluationContext, Snapshot, evaluate, load_contract

    path = path.expanduser().resolve(strict=True)
    if path.stat().st_size > 256_000:
        raise ValueError("manifest exceeds 256KB")
    manifest = Manifest.model_validate(tomllib.loads(path.read_text()))
    cwd = (path.parent / manifest.target.cwd).resolve(strict=True)
    if not cwd.is_dir():
        raise ValueError("target cwd must be an existing approved directory")
    fixtures_path = (path.parent / manifest.fixtures.file).resolve(strict=True)
    if fixtures_path.stat().st_size > 1_000_000:
        raise ValueError("fixtures exceed 1MB")
    fixtures = json.loads(fixtures_path.read_text())
    event_fixtures = fixtures
    if manifest.fixtures.events_file:
        events_path = (path.parent / manifest.fixtures.events_file).resolve(strict=True)
        if events_path.stat().st_size > 1_000_000:
            raise ValueError("event fixtures exceed 1MB")
        event_fixtures = json.loads(events_path.read_text())
    if not isinstance(event_fixtures, dict) or not isinstance(event_fixtures.get("events"), dict):
        raise ValueError("fixtures must contain an events object")
    payloads = {}
    for name, event in event_fixtures["events"].items():
        if not isinstance(event, dict) or set(event) not in ({"payload"}, {"raw_json"}):
            raise ValueError("event fixtures require exactly payload or raw_json")
        raw = event.get("raw_json")
        body = raw.encode() if isinstance(raw, str) else json.dumps(event["payload"], separators=(",", ":")).encode()
        if not isinstance(json.loads(body), dict):
            raise ValueError("event payloads must be JSON objects")
        payloads[name] = body
    for scenario in manifest.scenarios:
        if any(d.fixture not in payloads for d in scenario.deliveries):
            raise ValueError(f"{scenario.id}: unknown event fixture")
    contract = load_contract((path.parent / manifest.contract.file).resolve(strict=True))
    for scenario in manifest.scenarios:
        EvaluationContext.model_validate(scenario.context)
        missing = Snapshot(entities={}, available=False, valid=False, settled=False)
        invalid = [r for r in evaluate(contract, missing, missing, context=scenario.context)
                   if r.status == "invalid"]
        if invalid:
            raise ValueError(f"{scenario.id}: invalid context: {invalid[0].explanation}")
    return LoadedProject(path, manifest, cwd, fixtures_path,
                         {"seed":fixtures,"events":event_fixtures["events"]}, contract, payloads)
