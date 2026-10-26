"""Versioned data models shared by the runner, oracle, DNA and UI.

The models are the contract boundary. Persisted artifacts are JSON, and every
definition that participates in a hash is a Pydantic model serialised with
``canonical_dict`` so key order never changes the digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

AttemptOutcome = Literal["response", "timeout", "connect_error", "cancelled"]
EffectIntent = Literal["capture_and_fulfil", "order_duplicate", "none"]
Settlement = Literal["clean", "drain_unverified", "forced", "crashed", "unconfirmed"]
VerdictStatus = Literal["PASS", "FAIL", "INCONCLUSIVE"]
EventType = Literal["payment.captured", "order.paid"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def canonical_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude_none=False)


# --------------------------------------------------------------------------- #
# Scenarios and schedules
# --------------------------------------------------------------------------- #
class EventSpec(StrictModel):
    type: EventType
    order_role: str
    payment_role: str


class DeliverySpec(StrictModel):
    id: str
    event: EventSpec
    effect_intent: EffectIntent
    event_id: str
    invalid_signature: bool = False
    attempt_timeout_ms: int | None = None
    max_attempts: int | None = None


class AttemptPolicy(StrictModel):
    attempt_timeout_ms: int = Field(default=5000, ge=1)
    max_attempts: int = Field(default=1, ge=1)
    retry_on: list[AttemptOutcome] = Field(default_factory=list)
    backoff_ms: list[int] = Field(default_factory=list)


class Step(StrictModel):
    deliveries: list[str] = Field(min_length=1)
    delay_before_ms: int = Field(default=0, ge=0)

    @property
    def concurrent(self) -> bool:
        return len(self.deliveries) > 1


class LatencyFault(StrictModel):
    kind: Literal["provider_latency"] = "provider_latency"
    ms: int = Field(ge=0)
    payments: list[str] | None = None


class RendezvousFault(StrictModel):
    kind: Literal["provider_rendezvous"] = "provider_rendezvous"
    parties: int = Field(ge=2)
    timeout_ms: int = Field(default=2000, ge=1)
    payments: list[str] | None = None


Fault = Annotated[Union[LatencyFault, RendezvousFault], Field(discriminator="kind")]


class Schedule(StrictModel):
    events: list[EventSpec] = Field(default_factory=list)
    deliveries: list[DeliverySpec] = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
    faults: list[Fault] = Field(default_factory=list)
    delivery_policy: AttemptPolicy = Field(default_factory=AttemptPolicy)

    def delivery(self, delivery_id: str) -> DeliverySpec:
        for item in self.deliveries:
            if item.id == delivery_id:
                return item
        raise KeyError(delivery_id)

    def resolve_attempts(self, delivery: DeliverySpec) -> tuple[int, int, list[int]]:
        timeout = delivery.attempt_timeout_ms or self.delivery_policy.attempt_timeout_ms
        attempts = delivery.max_attempts or self.delivery_policy.max_attempts
        backoff = self.delivery_policy.backoff_ms[: max(0, attempts - 1)]
        return timeout, attempts, backoff


# --------------------------------------------------------------------------- #
# Expectations
# --------------------------------------------------------------------------- #
class Effect(StrictModel):
    kind: Literal["exactly_once", "payment_unchanged"]
    order_role: str | None = None
    payment_role: str | None = None


class EvidenceRequirement(StrictModel):
    kind: Literal[
        "answered",
        "group_overlap",
        "attempt_timed_out",
        "retried",
        "send_after_end",
    ]
    delivery: str | None = None
    other: str | None = None


class PassCondition(StrictModel):
    kind: Literal["status_class"] = "status_class"
    delivery: str
    expected: Literal["2xx", "4xx"]


class Expectation(StrictModel):
    id: str
    version: int = 1
    effects: list[Effect] = Field(default_factory=list)
    footprint: list[str] = Field(default_factory=list)
    required_evidence: list[EvidenceRequirement] = Field(default_factory=list)
    pass_conditions: list[PassCondition] = Field(default_factory=list)

    def definition(self) -> dict[str, Any]:
        return canonical_dict(self)

    def definition_sha256(self) -> str:
        return canonical_sha256(self.definition())


# --------------------------------------------------------------------------- #
# Observations
# --------------------------------------------------------------------------- #
class AttemptRecord(StrictModel):
    delivery_id: str
    attempt: int = Field(ge=1)
    outcome: AttemptOutcome
    status_code: int | None = None
    body_status: str | None = None
    body_scope: str | None = None
    started_s: float
    ended_s: float
    error: str | None = None

    @property
    def is_2xx(self) -> bool:
        return self.outcome == "response" and self.status_code is not None and 200 <= self.status_code < 300


class TableSnapshot(StrictModel):
    tables: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)

    def rows(self, table: str) -> list[dict[str, Any]]:
        return self.tables.get(table, [])


class StubObservation(StrictModel):
    calls: int = 0
    arrivals: int = 0
    released_by: str | None = None
    release_conditions: dict[str, str | None] = Field(default_factory=dict)
    stale_calls: int = 0
    holding: int = 0


class TrialObservation(StrictModel):
    trial_id: str
    target_id: str
    scenario_id: str
    settlement: Settlement = "clean"
    watchdog_fired: bool = False
    harness_errors: list[str] = Field(default_factory=list)
    instance_ok: bool = True
    baseline_ok: bool = True
    target_ready: bool = True
    snapshot: TableSnapshot | None = None
    schema_ok: bool = True
    schema_error: str | None = None
    execution_deadline_exceeded: bool = False
    cancelled_attempts: int = 0
    undispatched: int = 0
    phase_timings: dict[str, float] = Field(default_factory=dict)
    attempts: list[AttemptRecord] = Field(default_factory=list)
    stub: StubObservation = Field(default_factory=StubObservation)
    response_semantics_mismatches: list[str] = Field(default_factory=list)
    exit_code: int | None = None
    workdir: str = ""
    started_at: str = ""
    duration_s: float = 0.0


class Violation(StrictModel):
    check: str  # e.g. "INV-1@1" or "expected-effect@1"
    kind: str
    roles: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    independent: bool = False


class FailureSignature(StrictModel):
    check: str
    kind: str
    roles: list[str] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.check}:{self.kind}:{','.join(sorted(self.roles))}"

    @classmethod
    def from_violation(cls, violation: Violation) -> "FailureSignature":
        return cls(
            check=violation.check,
            kind=violation.kind,
            roles=sorted(violation.roles),
        )


class TrialVerdict(StrictModel):
    status: VerdictStatus
    reasons: list[str] = Field(default_factory=list)
    signature: FailureSignature | None = None
    violations: list[Violation] = Field(default_factory=list)
    expected_effect_misses: list[Violation] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    pass_condition_failures: list[str] = Field(default_factory=list)

    @property
    def is_valid_for_quota(self) -> bool:
        return self.status in {"PASS", "FAIL"}


class AggregateVerdict(StrictModel):
    status: VerdictStatus
    valid: int = 0
    fail: int = 0
    inconclusive: int = 0
    trials: int = 0
    signature: FailureSignature | None = None
    signature_counts: dict[str, int] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    budget_exhausted: bool = False


class ScenarioResult(StrictModel):
    template_id: str
    scenario_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    result: AggregateVerdict
    verdicts: list[TrialVerdict] = Field(default_factory=list)
    observations: list[TrialObservation] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Built scenarios and runtime helpers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class BuiltScenario:
    template_id: str
    template_version: int
    scenario_id: str
    params: dict[str, Any]
    schedule: Schedule
    expectation: Expectation
    fixtures: dict[str, dict[str, Any]]
    roles: dict[str, str]


@dataclass(frozen=True)
class SeedDocument:
    orders: list[dict[str, Any]]
    payments: list[dict[str, Any]]

    def tables(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "orders": [dict(row) for row in self.orders],
            "payments": [dict(row) for row in self.payments],
            "fulfilments": [],
        }


class HealthStatus(StrictModel):
    status: Literal["ok"] = "ok"
    version: str

# --------------------------------------------------------------------------- #
# Scenario registry specs (C.8)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ScenarioSpec:
    """A registry entry. S3 appears twice with different parameters."""

    template_id: str
    params: dict[str, Any]

    def build(self, roles: dict[str, str], seed: SeedDocument) -> "BuiltScenario":
        from .scenarios import build_scenario

        return build_scenario(self.template_id, self.params, roles, seed)

    @property
    def scenario_id(self) -> str:
        from .scenarios import scenario_id_for

        return scenario_id_for(self.template_id, self.params)


# --------------------------------------------------------------------------- #
# Failure DNA v1 (C.9)
# --------------------------------------------------------------------------- #
class DnaDocument(StrictModel):
    """A portable failure reproducer.

    ``reproducer`` is kept as the exact mapping that was loaded so the digest
    check hashes the stored bytes' canonical form, not a re-serialised model.
    """

    schema_version: Literal[1] = 1
    dna_id: str
    reproducer_sha256: str
    reproducer: dict[str, Any]
    provenance_scenario: dict[str, Any]
    environment: dict[str, Any]
    characterization: dict[str, Any]
    observations: dict[str, Any] = Field(default_factory=dict)
    reduction: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)

    def expectation(self) -> "Expectation":
        return Expectation.model_validate(self.reproducer["expectation"]["definition"])

    def schedule(self) -> "Schedule":
        return Schedule.model_validate(self.reproducer["schedule"])

    def signature(self) -> FailureSignature:
        return FailureSignature.model_validate(self.reproducer["failure_signature"])


class PreflightOk(StrictModel):
    ok: Literal[True] = True
    reason: None = None
    detail: str = ""


class PreflightRejected(StrictModel):
    ok: Literal[False] = False
    reason: str
    detail: str = ""


PreflightResult = PreflightOk | PreflightRejected


class ReplayOutcome(StrictModel):
    outcome: Literal[
        "REPRODUCED",
        "NOT_REPRODUCED",
        "DIFFERENT_FAILURE",
        "INCONCLUSIVE",
        "REJECTED",
    ]
    reason: str | None = None
    dna_id: str
    target_id: str
    signature_matched: bool = False
    valid: int = 0
    fail: int = 0
    inconclusive: int = 0
    trials: int = 0
    reasons: list[str] = Field(default_factory=list)


class VerifyOutcome(StrictModel):
    outcome: Literal["VERIFIED", "NOT_VERIFIED", "INCONCLUSIVE", "REJECTED"]
    reason: str | None = None
    dna_id: str
    vulnerable_target_id: str
    candidate_target_id: str
    statement: str = ""
    control: dict[str, Any] = Field(default_factory=dict)
    candidate: dict[str, Any] = Field(default_factory=dict)
