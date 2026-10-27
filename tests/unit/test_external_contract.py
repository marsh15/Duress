from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from duress.external.contract import Contract, Snapshot, evaluate, load_contract


def make_contract() -> Contract:
    return Contract.model_validate(
        {
            "schema_version": 1,
            "id": "ticketing-v1",
            "version": 1,
            "entities": {
                "bookings": {
                    "table": "booking",
                    "key": "id",
                    "columns": ["id", "status", "amount", "capacity", "tenant"],
                },
                "tickets": {
                    "table": "ticket",
                    "key": "id",
                    "columns": ["id", "booking_id", "amount", "tenant"],
                },
            },
            "invariants": [
                {
                    "id": "one-ticket-per-booking",
                    "kind": "unique_by",
                    "entity": "tickets",
                    "keys": ["booking_id"],
                },
                {
                    "id": "ticket-has-booking",
                    "kind": "reference_exists",
                    "entity": "tickets",
                    "reference": "booking_id",
                    "target_entity": "bookings",
                    "target_key": "id",
                },
                {"id": "ticket-created", "kind": "expected_effect", "entity": "tickets"},
                {
                    "id": "booking-transition",
                    "kind": "state_transition",
                    "entity": "bookings",
                    "field": "status",
                    "allowed": [{"from_state": "pending", "to_state": "confirmed"}],
                },
                {
                    "id": "ticket-amount-matches-booking",
                    "kind": "value_consistency",
                    "mode": "fields_equal",
                    "entity": "tickets",
                    "field": "amount",
                    "related_entity": "bookings",
                    "reference": "booking_id",
                    "related_key": "id",
                    "related_field": "amount",
                },
                {
                    "id": "booking-capacity",
                    "kind": "value_consistency",
                    "mode": "count_at_most",
                    "entity": "bookings",
                    "field": "capacity",
                    "related_entity": "tickets",
                    "reference": "booking_id",
                    "related_key": "id",
                },
                {"id": "booking-scope", "kind": "scope_ownership", "entity": "bookings"},
                {"id": "ack-matches-effect", "kind": "acknowledgement_consistency"},
            ],
        }
    )


def before_snapshot() -> Snapshot:
    return Snapshot(
        entities={
            "bookings": [
                {"id": "b1", "status": "pending", "amount": Decimal("12.50"), "capacity": 1, "tenant": "north"},
                {"id": "b2", "status": "pending", "amount": Decimal("25.00"), "capacity": 1, "tenant": "south"},
            ],
            "tickets": [],
        },
        available=True,
        valid=True,
        settled=True,
        provenance={"adapter": "test"},
    )


def after_snapshot(**changes) -> Snapshot:
    bookings = [
        {"id": "b1", "status": "confirmed", "amount": Decimal("12.50"), "capacity": 1, "tenant": "north"},
        {"id": "b2", "status": "pending", "amount": Decimal("25.00"), "capacity": 1, "tenant": "south"},
    ]
    tickets = [{"id": "t1", "booking_id": "b1", "amount": Decimal("12.50"), "tenant": "north"}]
    bookings = changes.pop("bookings", bookings)
    tickets = changes.pop("tickets", tickets)
    return Snapshot(
        entities={"bookings": bookings, "tickets": tickets},
        available=True,
        valid=True,
        settled=changes.pop("settled", True),
        provenance={"adapter": "test"},
        **changes,
    )


def valid_context() -> dict:
    return {
        "expected_effects": [
            {"entity": "tickets", "match": {"booking_id": "b1"}, "min_delta": 1, "max_delta": 1}
        ],
        "required_transitions": [
            {"entity": "bookings", "identity": "b1", "from_state": "pending", "to_state": "confirmed"}
        ],
        "scopes": [{"entity": "bookings", "identities": ["b1"]}],
        "acknowledgement": {
            "successful": True,
            "status_code": 200,
            "intent": "create",
            "entity": "tickets",
            "match": {"booking_id": "b1"},
        },
    }


def result_by_id(results):
    return {item.id: item for item in results}


def test_all_seven_invariant_families_and_capacity_have_a_positive_control():
    results = evaluate(make_contract(), before_snapshot(), after_snapshot(), context=valid_context())

    assert len(results) == 8
    assert {item.status for item in results} == {"satisfied"}
    assert all(item.version == 1 and item.reason and item.explanation for item in results)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        ("duplicate", {"one-ticket-per-booking": "violated", "booking-capacity": "violated"}),
        ("orphan", {"ticket-has-booking": "violated"}),
        ("missing_effect", {"ticket-created": "violated", "ack-matches-effect": "violated"}),
        ("bad_transition", {"booking-transition": "violated"}),
        ("bad_value", {"ticket-amount-matches-booking": "violated"}),
        ("out_of_scope", {"booking-scope": "violated"}),
    ],
)
def test_negative_controls_detect_each_invariant_family(mutate, expected):
    snapshot = after_snapshot()
    if mutate == "duplicate":
        snapshot.entities["tickets"].append(
            {"id": "t2", "booking_id": "b1", "amount": Decimal("12.50"), "tenant": "north"}
        )
    elif mutate == "orphan":
        snapshot.entities["tickets"].append(
            {"id": "t3", "booking_id": "missing", "amount": Decimal("12.50"), "tenant": "north"}
        )
    elif mutate == "missing_effect":
        snapshot.entities["tickets"] = []
    elif mutate == "bad_transition":
        snapshot.entities["bookings"][0]["status"] = "cancelled"
    elif mutate == "bad_value":
        snapshot.entities["tickets"][0]["amount"] = Decimal("9.00")
    elif mutate == "out_of_scope":
        snapshot.entities["bookings"][1]["status"] = "confirmed"

    results = result_by_id(evaluate(make_contract(), before_snapshot(), snapshot, context=valid_context()))
    for invariant_id, status in expected.items():
        assert results[invariant_id].status == status
        assert results[invariant_id].identities or invariant_id == "ack-matches-effect"


def test_missing_entity_column_and_unsettled_state_are_not_evidence_of_success():
    contract = make_contract()
    missing_table = after_snapshot()
    del missing_table.entities["tickets"]
    result = result_by_id(evaluate(contract, before_snapshot(), missing_table, context=valid_context()))
    assert result["ticket-created"].status == "not_evaluable"

    missing_column = after_snapshot()
    del missing_column.entities["tickets"][0]["amount"]
    result = result_by_id(evaluate(contract, before_snapshot(), missing_column, context=valid_context()))
    assert result["ticket-amount-matches-booking"].status == "not_evaluable"

    unsettled = after_snapshot(settled=False)
    result = result_by_id(evaluate(contract, before_snapshot(), unsettled, context=valid_context()))
    assert {item.status for item in result.values()} == {"not_evaluable"}


def test_duplicate_acknowledgement_is_valid_when_the_prior_effect_remains_unchanged():
    before = before_snapshot()
    before.entities["tickets"] = [
        {"id": "t1", "booking_id": "b1", "amount": Decimal("12.50"), "tenant": "north"}
    ]
    after = after_snapshot()
    after.entities["tickets"] = [dict(before.entities["tickets"][0])]
    context = {
        "acknowledgement": {
            "successful": True,
            "status_code": 200,
            "intent": "duplicate",
            "entity": "tickets",
            "match": {"booking_id": "b1"},
        }
    }
    result = result_by_id(evaluate(make_contract(), before, after, context=context))
    assert result["ack-matches-effect"].status == "satisfied"
    assert result["ticket-created"].status == "not_evaluable"


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 1, "id": "bad", "version": 1, "entities": {}, "invariants": [], "surprise": 1},
        {
            "schema_version": 1,
            "id": "bad",
            "version": 1,
            "entities": {"tickets": {"table": "ticket", "key": "missing", "columns": ["id"]}},
            "invariants": [{"id": "x", "kind": "unique_by", "entity": "tickets", "keys": ["unknown"]}],
        },
        {
            "schema_version": 1,
            "id": "bad",
            "version": 1,
            "entities": {"tickets": {"table": "ticket", "key": "id", "columns": ["id"]}},
            "invariants": [{"id": "x", "kind": "does_not_exist", "entity": "tickets"}],
        },
    ],
)
def test_contract_rejects_unknown_fields_kinds_and_references(payload):
    with pytest.raises(ValidationError):
        Contract.model_validate(payload)


def test_evaluator_rejects_malformed_context_as_invalid_not_pass():
    results = evaluate(make_contract(), before_snapshot(), after_snapshot(), context={"python": "arbitrary"})
    assert {item.status for item in results} == {"invalid"}
    assert {item.reason for item in results} == {"invalid_context"}


def test_validation_errors_hide_unknown_secret_values():
    payload = make_contract().model_dump(mode="python")
    payload["database_password"] = "top-secret-value"

    with pytest.raises(ValidationError) as error:
        Contract.model_validate(payload)

    assert "top-secret-value" not in str(error.value)


def test_load_contract_reads_strict_toml(tmp_path):
    contract_path = tmp_path / "contract.toml"
    contract_path.write_text(
        """
schema_version = 1
id = "simple-v1"
version = 1

[entities.widgets]
table = "widget"
key = "id"
columns = ["id", "external_id"]

[[invariants]]
id = "unique-external-id"
kind = "unique_by"
entity = "widgets"
keys = ["external_id"]
max_count = 1
""",
        encoding="utf-8",
    )

    assert load_contract(contract_path).id == "simple-v1"


def test_decimal_datetime_and_result_json_are_deterministic():
    snapshot = Snapshot(
        entities={"x": [{"amount": Decimal("1.2000"), "when": datetime(2026, 1, 2, tzinfo=timezone.utc)}]},
        available=True,
        valid=True,
        settled=True,
    )
    serialized = snapshot.model_dump(mode="json")
    assert serialized["entities"]["x"][0]["amount"] == "1.2"
    assert serialized["entities"]["x"][0]["when"] == "2026-01-02T00:00:00.000000+00:00"
    assert json.dumps(serialized, sort_keys=True)


def test_duplicate_acknowledgement_does_not_accept_replacement_state():
    before = before_snapshot()
    before.entities["tickets"] = [
        {"id": "t1", "booking_id": "b1", "amount": "12.5", "tenant": "north"}
    ]
    after = after_snapshot()
    after.entities["tickets"] = [
        {"id": "t2", "booking_id": "b1", "amount": "12.5", "tenant": "north"}
    ]
    context = {
        "acknowledgement": {
            "successful": True,
            "status_code": 200,
            "intent": "duplicate",
            "entity": "tickets",
            "match": {"booking_id": "b1"},
        }
    }

    results = result_by_id(evaluate(make_contract(), before, after, context=context))

    assert results["ack-matches-effect"].status == "violated"
