"""Versioned, declarative contracts for externally observed application state.

Contracts deliberately describe data and a small set of fixed checks. They do
not contain Python, SQL, or another executable expression language.
"""

from __future__ import annotations

import json
import math
import re
import tomllib
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
EntityName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]
StableId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")]
Scalar = str | int | float | bool | None
MAX_CONTRACT_BYTES = 256 * 1024
MAX_CONTRACT_ENTITIES = 50
MAX_CONTRACT_INVARIANTS = 100
MAX_CONTRACT_COLUMNS = 100


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class EntitySpec(StrictModel):
    """Allowlisted database table and its normalized identity/columns."""

    table: Identifier
    key: Identifier
    columns: list[Identifier] = Field(min_length=1, max_length=MAX_CONTRACT_COLUMNS)

    @model_validator(mode="after")
    def validate_columns(self) -> EntitySpec:
        from .storage import _key_is_sensitive
        if any(_key_is_sensitive(column) for column in self.columns):
            raise ValueError("credential-like observation fields are forbidden; select non-secret business attributes")
        if len(set(self.columns)) != len(self.columns):
            raise ValueError("entity columns must be unique")
        if self.key not in self.columns:
            raise ValueError("entity key must be included in columns")
        return self


class InvariantBase(StrictModel):
    id: StableId
    version: Literal[1] = 1


class UniqueByInvariant(InvariantBase):
    kind: Literal["unique_by"]
    entity: EntityName
    keys: list[Identifier] = Field(min_length=1)
    max_count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def unique_keys(self) -> UniqueByInvariant:
        if len(set(self.keys)) != len(self.keys):
            raise ValueError("unique_by keys must be unique")
        return self


class ReferenceExistsInvariant(InvariantBase):
    kind: Literal["reference_exists"]
    entity: EntityName
    reference: Identifier
    target_entity: EntityName
    target_key: Identifier


class ExpectedEffectInvariant(InvariantBase):
    kind: Literal["expected_effect"]
    entity: EntityName


class StatePair(StrictModel):
    from_state: Scalar
    to_state: Scalar


class StateTransitionInvariant(InvariantBase):
    kind: Literal["state_transition"]
    entity: EntityName
    field: Identifier
    allowed: list[StatePair] = Field(min_length=1)


class ValueConsistencyInvariant(InvariantBase):
    """Compare related fields, or bound related-row counts by a capacity field.

    ``fields_equal`` compares ``entity.field`` with ``related_entity.related_field``
    through ``entity.reference == related_entity.related_key``.

    ``count_at_most`` treats ``entity`` as the parent, ``field`` as its numeric
    capacity, and counts ``related_entity`` rows whose ``reference`` equals the
    parent's ``related_key``.
    """

    kind: Literal["value_consistency"]
    mode: Literal["fields_equal", "count_at_most"]
    entity: EntityName
    field: Identifier
    related_entity: EntityName
    reference: Identifier
    related_key: Identifier
    related_field: Identifier | None = None

    @model_validator(mode="after")
    def validate_mode_fields(self) -> ValueConsistencyInvariant:
        if self.mode == "fields_equal" and self.related_field is None:
            raise ValueError("fields_equal requires related_field")
        if self.mode == "count_at_most" and self.related_field is not None:
            raise ValueError("count_at_most does not accept related_field")
        return self


class ScopeOwnershipInvariant(InvariantBase):
    kind: Literal["scope_ownership"]
    entity: EntityName


class AcknowledgementInvariant(InvariantBase):
    kind: Literal["acknowledgement_consistency"]


Invariant = Annotated[
    Union[
        UniqueByInvariant,
        ReferenceExistsInvariant,
        ExpectedEffectInvariant,
        StateTransitionInvariant,
        ValueConsistencyInvariant,
        ScopeOwnershipInvariant,
        AcknowledgementInvariant,
    ],
    Field(discriminator="kind"),
]


class Contract(StrictModel):
    """A complete external domain contract, with a stable schema version."""

    schema_version: Literal[1] = 1
    id: StableId
    version: Literal[1] = 1
    entities: dict[EntityName, EntitySpec] = Field(
        min_length=1, max_length=MAX_CONTRACT_ENTITIES
    )
    invariants: list[Invariant] = Field(min_length=1, max_length=MAX_CONTRACT_INVARIANTS)

    @model_validator(mode="after")
    def validate_references(self) -> Contract:
        if sum(len(spec.columns) for spec in self.entities.values()) > MAX_CONTRACT_COLUMNS:
            raise ValueError(
                f"contract may select at most {MAX_CONTRACT_COLUMNS} columns across all entities"
            )

        tables = [spec.table for spec in self.entities.values()]
        if len(set(tables)) != len(tables):
            raise ValueError("each entity must map to a distinct table")

        for invariant in self.invariants:
            if isinstance(invariant, UniqueByInvariant):
                spec = self._entity(invariant.entity)
                self._columns(spec, invariant.keys, invariant.id)
            elif isinstance(invariant, ReferenceExistsInvariant):
                source = self._entity(invariant.entity)
                target = self._entity(invariant.target_entity)
                self._columns(source, [invariant.reference], invariant.id)
                self._columns(target, [invariant.target_key], invariant.id)
                if target.key != invariant.target_key:
                    raise ValueError(
                        f"invariant {invariant.id!r} target_key must match "
                        f"{invariant.target_entity!r} key {target.key!r}"
                    )
            elif isinstance(invariant, ExpectedEffectInvariant):
                self._entity(invariant.entity)
            elif isinstance(invariant, StateTransitionInvariant):
                self._columns(self._entity(invariant.entity), [invariant.field], invariant.id)
            elif isinstance(invariant, ValueConsistencyInvariant):
                primary = self._entity(invariant.entity)
                related = self._entity(invariant.related_entity)
                if invariant.mode == "fields_equal":
                    self._columns(primary, [invariant.field, invariant.reference], invariant.id)
                    self._columns(related, [invariant.related_key, invariant.related_field], invariant.id)
                else:
                    self._columns(primary, [invariant.field, invariant.related_key], invariant.id)
                    self._columns(related, [invariant.reference], invariant.id)
                    if primary.key != invariant.related_key:
                        raise ValueError(
                            f"invariant {invariant.id!r} related_key must match "
                            f"{invariant.entity!r} key {primary.key!r}"
                        )
            elif isinstance(invariant, ScopeOwnershipInvariant):
                self._entity(invariant.entity)
            elif isinstance(invariant, AcknowledgementInvariant):
                pass

        ids = [item.id for item in self.invariants]
        if len(ids) != len(set(ids)):
            raise ValueError("invariant ids must be unique within a contract")
        return self

    def _entity(self, name: str) -> EntitySpec:
        try:
            return self.entities[name]
        except KeyError as exc:
            raise ValueError(f"invariant references unknown entity {name!r}") from exc

    @staticmethod
    def _columns(spec: EntitySpec, columns: list[str | None], invariant_id: str) -> None:
        missing = [column for column in columns if column is not None and column not in spec.columns]
        if missing:
            raise ValueError(
                f"invariant {invariant_id!r} references undeclared column(s): "
                f"{', '.join(missing)}"
            )


class Snapshot(StrictModel):
    """Normalized rows and the evidence quality of one database observation."""

    entities: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    available: bool
    valid: bool
    settled: bool
    provenance: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @field_validator("entities", mode="before")
    @classmethod
    def normalize_entity_values(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for entity, rows in value.items():
            if not isinstance(rows, list):
                normalized[entity] = rows
                continue
            normalized[entity] = [
                {column: _canonical_value(item) for column, item in row.items()}
                if isinstance(row, dict)
                else row
                for row in rows
            ]
        return normalized


InvariantStatus = Literal["satisfied", "violated", "not_evaluable", "invalid"]


class InvariantResult(StrictModel):
    """Stable machine and human readable result for one invariant."""

    id: StableId
    version: Literal[1]
    status: InvariantStatus
    reason: str
    identities: list[dict[str, Scalar]] = Field(default_factory=list)
    explanation: str


class ExpectedEffect(StrictModel):
    entity: EntityName
    match: dict[Identifier, Scalar] = Field(min_length=1)
    min_delta: int = Field(default=1, ge=0)
    max_delta: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> ExpectedEffect:
        if self.min_delta > self.max_delta:
            raise ValueError("expected effect min_delta must not exceed max_delta")
        return self


class RequiredTransition(StrictModel):
    entity: EntityName
    identity: Scalar
    from_state: Scalar
    to_state: Scalar


class ScopedIdentities(StrictModel):
    entity: EntityName
    identities: list[Scalar]


class AcknowledgementEvidence(StrictModel):
    successful: bool
    status_code: int | None = Field(default=None, ge=100, le=599)
    intent: Literal["create", "update", "delete", "duplicate", "no_effect"]
    entity: EntityName | None = None
    match: dict[Identifier, Scalar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_response(self) -> AcknowledgementEvidence:
        if self.status_code is not None:
            is_success = 200 <= self.status_code < 300
            if self.successful != is_success:
                raise ValueError("successful must agree with the 2xx status_code")
        if self.successful and self.status_code is None:
            raise ValueError("a successful acknowledgement requires status_code")
        if self.successful and (self.entity is None or not self.match):
            raise ValueError("a successful acknowledgement requires entity and match")
        return self


class EvaluationContext(StrictModel):
    """Constrained scenario evidence consumed by dynamic invariant checks.

    ``expected_effects`` and ``required_transitions`` describe this scenario;
    ``scopes`` declares the only identities the operation may change; and
    ``acknowledgement`` records the HTTP result and declared event intent.
    """

    expected_effects: list[ExpectedEffect] = Field(default_factory=list)
    required_transitions: list[RequiredTransition] = Field(default_factory=list)
    scopes: list[ScopedIdentities] = Field(default_factory=list)
    acknowledgement: AcknowledgementEvidence | None = None


def load_contract(path: str | Path) -> Contract:
    """Load a strict TOML or JSON contract from ``path``."""

    contract_path = Path(path)
    try:
        if contract_path.stat().st_size > MAX_CONTRACT_BYTES:
            raise ValueError(
                f"contract file exceeds the {MAX_CONTRACT_BYTES}-byte size limit"
            )
        with contract_path.open("rb") as contract_file:
            raw = contract_file.read(MAX_CONTRACT_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"cannot read contract file {contract_path}: {exc.strerror or 'I/O error'}") from exc
    if len(raw) > MAX_CONTRACT_BYTES:
        raise ValueError(f"contract file exceeds the {MAX_CONTRACT_BYTES}-byte size limit")

    try:
        if contract_path.suffix.lower() == ".toml":
            data = tomllib.loads(raw.decode("utf-8"))
        elif contract_path.suffix.lower() == ".json":
            data = json.loads(raw)
        else:
            raise ValueError("contract file must use .toml or .json")
        return Contract.model_validate(data)
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid contract {contract_path}: {exc}") from exc


def evaluate(
    contract: Contract,
    before: Snapshot,
    after: Snapshot,
    *,
    context: dict[str, Any] | EvaluationContext = {},
) -> list[InvariantResult]:
    """Evaluate every declared invariant against two normalized snapshots.

    Missing tables, columns, rows needed for a comparison, unsettled final
    state, and unavailable observations return ``not_evaluable``. Malformed
    snapshots or scenario context return ``invalid``. No check uses ``eval``.
    """

    if not isinstance(contract, Contract):
        raise TypeError("contract must be a validated Contract")

    try:
        before_snapshot = before if isinstance(before, Snapshot) else Snapshot.model_validate(before)
        after_snapshot = after if isinstance(after, Snapshot) else Snapshot.model_validate(after)
    except (ValidationError, TypeError) as exc:
        return [_result(inv, "invalid", "invalid_snapshot", str(exc)) for inv in contract.invariants]

    try:
        parsed_context = (
            context if isinstance(context, EvaluationContext) else EvaluationContext.model_validate(context)
        )
        _validate_context_references(contract, parsed_context)
    except (ValidationError, ValueError, TypeError) as exc:
        return [_result(inv, "invalid", "invalid_context", str(exc)) for inv in contract.invariants]

    preflight = _snapshot_problem(contract, before_snapshot, is_after=False)
    if preflight is None:
        preflight = _snapshot_problem(contract, after_snapshot, is_after=True)
    if preflight is not None:
        status, reason, explanation = preflight
        return [_result(inv, status, reason, explanation) for inv in contract.invariants]

    results: list[InvariantResult] = []
    for invariant in contract.invariants:
        try:
            status, reason, identities, explanation = _evaluate_one(
                contract, invariant, before_snapshot, after_snapshot, parsed_context
            )
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError) as exc:
            status, reason, identities, explanation = (
                "invalid",
                "incompatible_snapshot",
                [],
                str(exc),
            )
        results.append(_result(invariant, status, reason, explanation, identities))
    return results


def _validate_context_references(contract: Contract, context: EvaluationContext) -> None:
    for effect in context.expected_effects:
        _validate_context_entity(contract, effect.entity, effect.match)
    for transition in context.required_transitions:
        if transition.entity not in contract.entities:
            raise ValueError(f"context references unknown entity {transition.entity!r}")
    for scope in context.scopes:
        if scope.entity not in contract.entities:
            raise ValueError(f"context references unknown entity {scope.entity!r}")
    acknowledgement = context.acknowledgement
    if acknowledgement is not None and acknowledgement.entity is not None:
        _validate_context_entity(contract, acknowledgement.entity, acknowledgement.match)


def _validate_context_entity(contract: Contract, entity: str, match: dict[str, Scalar]) -> None:
    if entity not in contract.entities:
        raise ValueError(f"context references unknown entity {entity!r}")
    spec = contract.entities[entity]
    unknown = set(match) - set(spec.columns)
    if unknown:
        raise ValueError(f"context for {entity!r} references undeclared columns: {', '.join(sorted(unknown))}")


def _snapshot_problem(
    contract: Contract, snapshot: Snapshot, *, is_after: bool
) -> tuple[InvariantStatus, str, str] | None:
    if not snapshot.available:
        return "not_evaluable", "evidence_unavailable", "Database observation is unavailable."
    if not snapshot.valid:
        return "invalid", "invalid_snapshot", "The observer marked the snapshot invalid."
    if is_after and not snapshot.settled:
        return "not_evaluable", "state_unsettled", "Final business state was not confirmed settled."

    unknown_entities = set(snapshot.entities) - set(contract.entities)
    if unknown_entities:
        return (
            "invalid",
            "undeclared_entities",
            f"Snapshot includes undeclared entities: {', '.join(sorted(unknown_entities))}.",
        )
    for name, rows in snapshot.entities.items():
        spec = contract.entities[name]
        seen_keys: set[str] = set()
        for row in rows:
            unknown_columns = set(row) - set(spec.columns)
            if unknown_columns:
                return (
                    "invalid",
                    "undeclared_columns",
                    f"Snapshot entity {name!r} includes undeclared columns: "
                    f"{', '.join(sorted(unknown_columns))}.",
                )
            missing_columns = set(spec.columns) - set(row)
            if missing_columns:
                return (
                    "not_evaluable",
                    "missing_columns",
                    f"Snapshot entity {name!r} is missing declared columns: "
                    f"{', '.join(sorted(missing_columns))}.",
                )
            key_value = row[spec.key]
            if key_value is None or isinstance(key_value, (dict, list)):
                return (
                    "invalid",
                    "invalid_entity_identity",
                    f"Snapshot entity {name!r} has a missing or non-scalar identity key.",
                )
            identity_key = _stable_key({spec.key: key_value})
            if identity_key in seen_keys:
                return (
                    "invalid",
                    "duplicate_entity_identity",
                    f"Snapshot entity {name!r} contains duplicate identity values.",
                )
            seen_keys.add(identity_key)
            for value in row.values():
                try:
                    _canonical_value(value)
                except (TypeError, ValueError):
                    return (
                        "invalid",
                        "unsupported_value",
                        f"Snapshot entity {name!r} contains a non-normalizable value.",
                    )
    return None


def _evaluate_one(
    contract: Contract,
    invariant: Invariant,
    before: Snapshot,
    after: Snapshot,
    context: EvaluationContext,
) -> tuple[InvariantStatus, str, list[dict[str, Scalar]], str]:
    required = _required_entities(invariant)
    if isinstance(invariant, AcknowledgementInvariant):
        acknowledgement = context.acknowledgement
        if acknowledgement is not None and acknowledgement.entity is not None:
            required.add(acknowledgement.entity)
    missing = [name for name in required if name not in before.entities or name not in after.entities]
    if missing:
        return (
            "not_evaluable",
            "missing_entity_evidence",
            [],
            f"Required entity evidence is missing for: {', '.join(sorted(missing))}.",
        )

    if isinstance(invariant, UniqueByInvariant):
        rows = after.entities[invariant.entity]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            key_values = {key: row[key] for key in invariant.keys}
            groups.setdefault(_stable_key(key_values), []).append(key_values)
        failures = [items[0] for items in groups.values() if len(items) > invariant.max_count]
        if failures:
            return "violated", "duplicate_key", _sort_identities(failures), (
                f"Entity {invariant.entity!r} has more than {invariant.max_count} rows "
                f"for {', '.join(invariant.keys)}."
            )
        return "satisfied", "unique", [], f"Entity {invariant.entity!r} satisfies the declared uniqueness bound."

    if isinstance(invariant, ReferenceExistsInvariant):
        targets = after.entities[invariant.target_entity]
        target_values = [_stable_key({invariant.target_key: row[invariant.target_key]}) for row in targets]
        if len(target_values) != len(set(target_values)):
            return "invalid", "ambiguous_target_key", [], (
                f"Target entity {invariant.target_entity!r} contains duplicate keys."
            )
        known = set(target_values)
        missing_rows = [
            {contract.entities[invariant.entity].key: row[contract.entities[invariant.entity].key]}
            for row in after.entities[invariant.entity]
            if row[invariant.reference] is None
            or _stable_key({invariant.target_key: row[invariant.reference]}) not in known
        ]
        if missing_rows:
            return "violated", "missing_reference", _sort_identities(missing_rows), (
                f"Entity {invariant.entity!r} contains a reference that does not resolve "
                f"to {invariant.target_entity!r}.{invariant.target_key}."
            )
        return "satisfied", "references_resolve", [], (
            f"All {invariant.entity!r}.{invariant.reference} references resolve."
        )

    if isinstance(invariant, ExpectedEffectInvariant):
        effects = [item for item in context.expected_effects if item.entity == invariant.entity]
        if not effects:
            return "not_evaluable", "expected_effect_unspecified", [], (
                f"Scenario context contains no expected effect for {invariant.entity!r}."
            )
        failures: list[dict[str, Scalar]] = []
        for effect in effects:
            before_count = _matching_count(before.entities[invariant.entity], effect.match)
            after_count = _matching_count(after.entities[invariant.entity], effect.match)
            delta = after_count - before_count
            if not effect.min_delta <= delta <= effect.max_delta:
                failures.extend(
                    _identity(contract, invariant.entity, row)
                    for row in after.entities[invariant.entity]
                    if _matches(row, effect.match)
                )
                if not any(_matches(row, effect.match) for row in after.entities[invariant.entity]):
                    failures.append(dict(effect.match))
        if failures:
            return "violated", "unexpected_effect_delta", _sort_identities(failures), (
                f"Observed effect count delta for {invariant.entity!r} fell outside the "
                "scenario-declared range."
            )
        return "satisfied", "expected_effect_observed", [], (
            f"Scenario-declared effects for {invariant.entity!r} match their expected count deltas."
        )

    if isinstance(invariant, StateTransitionInvariant):
        spec = contract.entities[invariant.entity]
        before_rows = _rows_by_key(before.entities[invariant.entity], spec.key)
        after_rows = _rows_by_key(after.entities[invariant.entity], spec.key)
        allowed = {(pair.from_state, pair.to_state) for pair in invariant.allowed}
        failures: list[dict[str, Scalar]] = []
        for key, old in before_rows.items():
            new = after_rows.get(key)
            if new is None or old[invariant.field] == new[invariant.field]:
                continue
            if (old[invariant.field], new[invariant.field]) not in allowed:
                failures.append({spec.key: old[spec.key]})
        for required in context.required_transitions:
            if required.entity != invariant.entity:
                continue
            identity = _stable_key({spec.key: required.identity})
            old = before_rows.get(identity)
            new = after_rows.get(identity)
            if (
                old is None
                or new is None
                or old[invariant.field] != required.from_state
                or new[invariant.field] != required.to_state
                or (required.from_state, required.to_state) not in allowed
            ):
                failures.append({spec.key: required.identity})
        if failures:
            return "violated", "forbidden_or_missing_transition", _sort_identities(failures), (
                f"Entity {invariant.entity!r}.{invariant.field} made a forbidden transition "
                "or did not make a scenario-required transition."
            )
        return "satisfied", "transitions_allowed", [], (
            f"Observed transitions for {invariant.entity!r}.{invariant.field} satisfy the contract."
        )

    if isinstance(invariant, ValueConsistencyInvariant):
        if invariant.mode == "fields_equal":
            related = after.entities[invariant.related_entity]
            by_related_key: dict[str, dict[str, Any]] = {}
            for row in related:
                key = _stable_key({invariant.related_key: row[invariant.related_key]})
                if key in by_related_key:
                    return "invalid", "ambiguous_related_key", [], (
                        f"Related entity {invariant.related_entity!r} contains duplicate keys."
                    )
                by_related_key[key] = row
            failures: list[dict[str, Scalar]] = []
            missing_related = False
            for row in after.entities[invariant.entity]:
                related_row = by_related_key.get(
                    _stable_key({invariant.related_key: row[invariant.reference]})
                )
                if related_row is None:
                    missing_related = True
                elif not _values_equal(row[invariant.field], related_row[invariant.related_field]):
                    failures.append(_identity(contract, invariant.entity, row))
            if failures:
                return "violated", "value_mismatch", _sort_identities(failures), (
                    f"Related values for {invariant.entity!r}.{invariant.field} do not match "
                    f"{invariant.related_entity!r}.{invariant.related_field}."
                )
            if missing_related:
                return "not_evaluable", "related_value_unavailable", [], (
                    f"Related {invariant.related_entity!r} rows needed for value comparison are missing."
                )
            return "satisfied", "values_consistent", [], "All declared related values are consistent."

        parents = after.entities[invariant.entity]
        children = after.entities[invariant.related_entity]
        counts: dict[str, int] = {}
        for child in children:
            reference = child[invariant.reference]
            if reference is not None:
                key = _stable_key({invariant.related_key: reference})
                counts[key] = counts.get(key, 0) + 1
        failures = []
        for parent in parents:
            capacity = parent[invariant.field]
            numeric_capacity = _as_decimal(capacity)
            if numeric_capacity is None or numeric_capacity < 0:
                return "invalid", "invalid_capacity_value", [], (
                    f"Capacity field {invariant.entity!r}.{invariant.field} must contain a "
                    "non-negative finite number."
                )
            count = counts.get(_stable_key({invariant.related_key: parent[invariant.related_key]}), 0)
            if Decimal(count) > numeric_capacity:
                failures.append({contract.entities[invariant.entity].key: parent[
                    contract.entities[invariant.entity].key
                ]})
        if failures:
            return "violated", "capacity_exceeded", _sort_identities(failures), (
                f"Related {invariant.related_entity!r} row count exceeds the declared "
                f"capacity in {invariant.entity!r}.{invariant.field}."
            )
        return "satisfied", "within_capacity", [], (
            f"Related {invariant.related_entity!r} row counts fit within declared capacities."
        )

    if isinstance(invariant, ScopeOwnershipInvariant):
        scopes = [scope for scope in context.scopes if scope.entity == invariant.entity]
        if not scopes:
            return "not_evaluable", "scope_unspecified", [], (
                f"Scenario context contains no allowed identity scope for {invariant.entity!r}."
            )
        spec = contract.entities[invariant.entity]
        allowed = {_stable_key({spec.key: identity}) for scope in scopes for identity in scope.identities}
        before_rows = _rows_by_key(before.entities[invariant.entity], spec.key)
        after_rows = _rows_by_key(after.entities[invariant.entity], spec.key)
        changed = []
        for key in before_rows.keys() | after_rows.keys():
            old = before_rows.get(key)
            new = after_rows.get(key)
            if _stable_key({spec.key: (old or new)[spec.key]}) not in allowed and not _rows_equal(old, new):
                changed.append({spec.key: (old or new)[spec.key]})
        if changed:
            return "violated", "out_of_scope_change", _sort_identities(changed), (
                f"Operation changed {invariant.entity!r} identities outside the declared scope."
            )
        return "satisfied", "scope_respected", [], (
            f"All changes to {invariant.entity!r} are within the declared scope."
        )

    if isinstance(invariant, AcknowledgementInvariant):
        ack = context.acknowledgement
        if ack is None:
            return "not_evaluable", "acknowledgement_missing", [], (
                "Scenario context contains no acknowledgement evidence."
            )
        if not ack.successful:
            return "satisfied", "no_success_acknowledgement", [], (
                "No successful acknowledgement was observed. This conditional check makes no "
                "new-write claim; expected effects and response requirements are evaluated separately."
            )
        if ack.entity is None or not ack.match:
            return "not_evaluable", "acknowledgement_intent_missing", [], (
                "Successful acknowledgement has no declared entity effect to compare."
            )
        before_rows = [row for row in before.entities[ack.entity] if _matches(row, ack.match)]
        after_rows = [row for row in after.entities[ack.entity] if _matches(row, ack.match)]
        before_count = len(before_rows)
        after_count = len(after_rows)
        entity_key = contract.entities[ack.entity].key
        before_by_key = _rows_by_key(before.entities[ack.entity], entity_key)
        after_by_key = _rows_by_key(after.entities[ack.entity], entity_key)
        updated_existing_row = False
        for identity in before_by_key.keys() & after_by_key.keys():
            old = before_by_key[identity]
            new = after_by_key[identity]
            if (
                (_matches(old, ack.match) or _matches(new, ack.match))
                and not _rows_equal(old, new)
            ):
                updated_existing_row = True
                break
        valid = {
            "create": after_count > before_count,
            "update": updated_existing_row,
            "delete": after_count < before_count,
            "duplicate": before_count > 0 and _same_row_multiset(before_rows, after_rows),
            "no_effect": _same_row_multiset(before_rows, after_rows),
        }[ack.intent]
        if not valid:
            identities = _sort_identities(
                [_identity(contract, ack.entity, row) for row in after_rows]
                or [dict(ack.match)]
            )
            return "violated", "acknowledgement_effect_mismatch", identities, (
                f"Successful acknowledgement for {ack.intent!r} does not match the "
                "observed business effect."
            )
        return "satisfied", "acknowledgement_matches_effect", [], (
            f"Successful acknowledgement is consistent with the declared {ack.intent!r} intent."
        )

    raise TypeError(f"unsupported invariant type: {type(invariant).__name__}")


def _required_entities(invariant: Invariant) -> set[str]:
    if isinstance(invariant, ReferenceExistsInvariant):
        return {invariant.entity, invariant.target_entity}
    if isinstance(invariant, ValueConsistencyInvariant):
        return {invariant.entity, invariant.related_entity}
    if isinstance(invariant, AcknowledgementInvariant):
        return set()
    return {invariant.entity}


def _identity(contract: Contract, entity: str, row: dict[str, Any]) -> dict[str, Scalar]:
    key = contract.entities[entity].key
    return {key: row[key]}


def _rows_by_key(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        stable = _stable_key({key: row[key]})
        if stable in result:
            raise ValueError(f"snapshot contains duplicate entity identity in key {key!r}")
        result[stable] = row
    return result


def _matching_count(rows: list[dict[str, Any]], match: dict[str, Scalar]) -> int:
    return sum(_matches(row, match) for row in rows)


def _matches(row: dict[str, Any], match: dict[str, Scalar]) -> bool:
    return all(key in row and _values_equal(row[key], value) for key, value in match.items())


def _rows_equal(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    if left is None or right is None:
        return left is right
    return _stable_key(left) == _stable_key(right)


def _same_row_multiset(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return sorted(_stable_key(row) for row in left) == sorted(_stable_key(row) for row in right)


def _values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    left_number = _as_decimal(left)
    right_number = _as_decimal(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return type(left) is type(right) and _canonical_value(left) == _canonical_value(right)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str) and not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _canonical_value(value: Any) -> Any:
    """Convert supported database values to deterministic JSON-compatible data."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are unsupported")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite decimals are unsupported")
        if value == value.to_integral_value():
            return int(value)
        return format(value.normalize(), "f")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("mapping keys must be strings")
        return {key: _canonical_value(value[key]) for key in sorted(value)}
    raise TypeError(f"unsupported observed value type: {type(value).__name__}")


def _stable_key(value: Any) -> str:
    return json.dumps(_canonical_value(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sort_identities(items: list[dict[str, Scalar]]) -> list[dict[str, Scalar]]:
    unique = {_stable_key(item): item for item in items}
    return [unique[key] for key in sorted(unique)]


def _result(
    invariant: Invariant,
    status: InvariantStatus,
    reason: str,
    explanation: str,
    identities: list[dict[str, Scalar]] | None = None,
) -> InvariantResult:
    return InvariantResult(
        id=invariant.id,
        version=invariant.version,
        status=status,
        reason=reason,
        identities=identities or [],
        explanation=explanation,
    )


__all__ = [
    "AcknowledgementEvidence",
    "Contract",
    "EntitySpec",
    "EvaluationContext",
    "ExpectedEffect",
    "Invariant",
    "InvariantResult",
    "Snapshot",
    "evaluate",
    "load_contract",
]
