"""C.4 invariant fixtures, including MP-1, MP-2 and MP-3 at the database level."""

from __future__ import annotations

from typing import Any

from duress.invariants import PackContext, evaluate_pack
from duress.models import AttemptRecord, TableSnapshot

ROLES = {
    "order:A": "order_A",
    "payment:A1": "pay_A1",
    "payment:A2": "pay_A2",
    "order:B": "order_B",
    "payment:B1": "pay_B1",
    "payment:B2": "pay_B2",
}


def order(entity_id: str, status: str = "created") -> dict[str, Any]:
    return {"id": entity_id, "amount": 5000, "currency": "INR", "status": status}


def payment(
    entity_id: str,
    order_id: str,
    status: str = "authorized",
    amount: int = 5000,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "order_id": order_id,
        "amount": amount,
        "currency": "INR",
        "status": status,
    }


def fulfilment(
    row_id: int, order_id: str, payment_id: str, amount: int = 5000
) -> dict[str, Any]:
    return {
        "id": row_id,
        "order_id": order_id,
        "payment_id": payment_id,
        "amount": amount,
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def seed_tables() -> dict[str, list[dict[str, Any]]]:
    return {
        "orders": [order("order_A"), order("order_B")],
        "payments": [
            payment("pay_A1", "order_A"),
            payment("pay_A2", "order_A"),
            payment("pay_B1", "order_B"),
            payment("pay_B2", "order_B"),
        ],
        "fulfilments": [],
    }


def attempt(
    delivery_id: str,
    *,
    status: int = 200,
    body_status: str = "processed",
    body_scope: str | None = None,
) -> AttemptRecord:
    return AttemptRecord(
        delivery_id=delivery_id,
        attempt=1,
        outcome="response",
        status_code=status,
        body_status=body_status,
        body_scope=body_scope,
        started_s=0.0,
        ended_s=0.01,
    )


DEFAULT_INTENTS = {
    "d1": "capture_and_fulfil",
    "d2": "order_duplicate",
    "d3": "capture_and_fulfil",
}
DEFAULT_PAYMENTS = {"d1": "pay_A1", "d2": "pay_A2", "d3": "pay_B1"}
DEFAULT_ORDERS = {"d1": "order_A", "d2": "order_A", "d3": "order_B"}


def context(
    tables: dict[str, list[dict[str, Any]]],
    *,
    attempts: list[AttemptRecord] | None = None,
    baseline: dict[str, list[dict[str, Any]]] | None = None,
    footprint: tuple[str, ...] = ("order:A",),
) -> PackContext:
    return PackContext(
        roles=ROLES,
        baseline=TableSnapshot(tables=baseline or seed_tables()),
        attempts=attempts or [],
        delivery_intents=DEFAULT_INTENTS,
        delivery_payments=DEFAULT_PAYMENTS,
        delivery_orders=DEFAULT_ORDERS,
        seed_order_ids={"order_A", "order_B"},
        footprint=set(footprint),
    )


def violations(
    tables: dict[str, list[dict[str, Any]]],
    *,
    attempts: list[AttemptRecord] | None = None,
    baseline: dict[str, list[dict[str, Any]]] | None = None,
    footprint: tuple[str, ...] = ("order:A",),
) -> list:
    return evaluate_pack(
        context(tables, attempts=attempts, baseline=baseline, footprint=footprint),
        TableSnapshot(tables=tables),
    )


def kinds(found: list) -> list[str]:
    return [violation.kind for violation in found]


def clean_tables() -> dict[str, list[dict[str, Any]]]:
    tables = seed_tables()
    tables["payments"][0]["status"] = "captured"
    tables["orders"][0]["status"] = "fulfilled"
    tables["fulfilments"].append(fulfilment(1, "order_A", "pay_A1"))
    return tables


def test_clean_trial_has_no_violations():
    assert violations(clean_tables(), attempts=[attempt("d1")]) == []


def test_inv1_duplicate_fulfilment():
    tables = clean_tables()
    tables["fulfilments"].append(fulfilment(2, "order_A", "pay_A1"))
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-1@1" and v.kind == "duplicate_fulfilment" for v in found)
    duplicate = next(v for v in found if v.check == "INV-1@1")
    assert duplicate.independent is True
    assert duplicate.roles == ["order:A"]


def test_inv1_clean_but_tricky_second_payment_preserved():
    """MP-1: A2 is acknowledged as an order duplicate and left unchanged."""
    attempts = [
        attempt("d1"),
        attempt("d2", body_status="duplicate", body_scope="order"),
    ]
    assert violations(clean_tables(), attempts=attempts) == []


def test_inv2_missing_reference():
    tables = seed_tables()
    tables["orders"][0]["status"] = "fulfilled"
    tables["payments"][0]["status"] = "captured"
    tables["fulfilments"].append(fulfilment(1, "order_missing", "pay_A1"))
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-2@1" and v.kind == "missing_reference" for v in found)


def test_inv2_ownership_mismatch():
    tables = seed_tables()
    tables["fulfilments"].append(fulfilment(1, "order_A", "pay_B1"))
    tables["orders"][0]["status"] = "fulfilled"
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-2@1" and v.kind == "ownership_mismatch" for v in found)


def test_inv2_fulfilment_linked_to_other_orders_payment_fixture():
    tables = seed_tables()
    tables["fulfilments"].append(fulfilment(1, "order_A", "pay_B1"))
    tables["orders"][0]["status"] = "fulfilled"
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-2@1" for v in found)


def test_inv3_amount_mismatch():
    tables = clean_tables()
    tables["payments"][0]["amount"] = 4000
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-3@1" and v.kind == "amount_mismatch" for v in found)


def test_inv3_amount_equal_order_but_payment_differs_fixture():
    tables = clean_tables()
    tables["payments"][0]["amount"] = 4000
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-3@1" and v.kind == "amount_mismatch" for v in found)


def test_inv4_two_captured_one_fulfilment_is_mp2():
    """MP-2: A2 is locally captured with no linked fulfilment."""
    tables = clean_tables()
    tables["payments"][1]["status"] = "captured"
    found = violations(tables, attempts=[attempt("d1")])
    matches = [
        v
        for v in found
        if v.check == "INV-4@1" and v.kind == "captured_without_fulfilment"
    ]
    assert matches
    assert "payment:A2" in matches[0].roles
    assert "payment:A2" not in matches[0].roles[:1]  # primary role is the order, still linked


def test_inv4_failed_then_captured_is_clean():
    baseline = seed_tables()
    baseline["payments"][0]["status"] = "failed"
    tables = seed_tables()
    tables["payments"][0]["status"] = "captured"
    tables["orders"][0]["status"] = "fulfilled"
    tables["fulfilments"].append(fulfilment(1, "order_A", "pay_A1"))
    assert violations(tables, attempts=[attempt("d1")], baseline=baseline) == []


def test_inv5_first_capture_acked_without_effect_is_mp3():
    found = violations(seed_tables(), attempts=[attempt("d1")])
    assert any(
        v.check == "INV-5@1" and v.kind == "ack_without_effect" for v in found
    )


def test_inv5_duplicate_acknowledgement_clean():
    attempts = [attempt("d1"), attempt("d2", body_status="duplicate", body_scope="order")]
    assert violations(clean_tables(), attempts=attempts) == []


def test_inv5_duplicate_acknowledgement_with_mutated_payment():
    tables = clean_tables()
    tables["payments"][1]["amount"] = 4000
    attempts = [attempt("d1"), attempt("d2", body_status="duplicate", body_scope="order")]
    found = violations(tables, attempts=attempts)
    assert any(
        v.check == "INV-5@1" and v.kind == "duplicate_ack_inconsistent" for v in found
    )


def test_inv6_unauthorized_mutation_outside_footprint():
    tables = clean_tables()
    found = violations(tables, attempts=[attempt("d3")], footprint=("order:B",))
    assert any(v.check == "INV-6@1" and v.kind == "unauthorized_mutation" for v in found)


def test_inv6_clean_when_outside_footprint_is_untouched():
    tables = seed_tables()
    tables["payments"][2]["status"] = "captured"
    tables["orders"][1]["status"] = "fulfilled"
    tables["fulfilments"].append(fulfilment(1, "order_B", "pay_B1"))
    assert violations(tables, attempts=[attempt("d3")], footprint=("order:B",)) == []


def test_inv6_unseeded_order_reference():
    tables = seed_tables()
    tables["payments"].append(payment("pay_X", "order_X", status="captured"))
    found = violations(tables, attempts=[attempt("d1")])
    assert any(v.check == "INV-6@1" for v in found)
    assert any(v.check == "INV-2@1" for v in found)


def test_every_member_has_a_clean_fixture():
    """The clean fixture must not trip any of the six members."""
    found = violations(clean_tables(), attempts=[attempt("d1")])
    assert {v.check for v in found} == set()