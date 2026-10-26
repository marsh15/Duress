"""Invariant pack ``payments-ref-v1`` (plan C.4).

Six members judge committed state plus the acknowledgement observations. Every
member returns violation rows; the oracle decides precedence and the verdict.
"""

from __future__ import annotations

import inspect
import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..models import (
    AttemptRecord,
    TableSnapshot,
    Violation,
    canonical_dict,
    canonical_sha256,
)

PACK_ID = "payments-ref-v1"
PACK_VERSION = 1
PACK_FLAGS = ("payments-webhook", "sqlite", "exec@1")

PRECEDENCE: dict[str, int] = {
    "INV-1": 0,
    "INV-6": 1,
    "INV-2": 2,
    "INV-3": 3,
    "INV-4": 4,
    "INV-5": 5,
    "expected-effect": 6,
}

ORDER_STATUSES = ("created", "fulfilled")
PAYMENT_STATUSES = ("created", "authorized", "captured", "failed")

INV1_SQL = (
    "SELECT order_id, COUNT(*) AS n FROM fulfilments "
    "GROUP BY order_id HAVING COUNT(*) > 1"
)
INV2_SQL = (
    "missing order reference | missing payment reference | payment with missing order | "
    "fulfilment linked to a payment of another order"
)
INV3_SQL = (
    "SELECT f.order_id, f.payment_id, f.amount AS f_amount, p.amount AS p_amount, "
    "p.currency AS p_currency, p.status AS p_status, o.amount AS o_amount, "
    "o.currency AS o_currency FROM fulfilments f "
    "LEFT JOIN payments p ON p.id = f.payment_id "
    "LEFT JOIN orders o ON o.id = f.order_id"
)
INV4_SQL = (
    "invalid order/payment status | captured payment without linked fulfilment | "
    "fulfilled order without fulfilment | fulfilment whose order is not fulfilled"
)
INV5_SQL = "acknowledgement rows joined with the delivery's effect intent and the baseline"
INV6_SQL = "rows outside the expectation footprint compared with the baseline"


@dataclass(frozen=True)
class PackContext:
    roles: dict[str, str]
    baseline: TableSnapshot
    attempts: list[AttemptRecord]
    delivery_intents: dict[str, str]
    delivery_payments: dict[str, str]
    delivery_orders: dict[str, str]
    seed_order_ids: set[str]
    footprint: set[str]

    @property
    def allowed_order_ids(self) -> set[str]:
        return {
            self.roles[role]
            for role in self.footprint
            if role.startswith("order:") and role in self.roles
        }


class RoleIndex:
    def __init__(self, roles: dict[str, str]) -> None:
        self.by_entity = {entity: role for role, entity in roles.items()}

    def order(self, entity_id: str) -> str:
        return self.by_entity.get(entity_id, f"order:unseeded:{entity_id}")

    def payment(self, entity_id: str) -> str:
        return self.by_entity.get(entity_id, f"payment:unseeded:{entity_id}")


@dataclass(frozen=True)
class Member:
    id: str
    kind: str
    sql: str
    independent: bool
    evaluator: Callable[[PackContext, TableSnapshot], list[Violation]]
    version: int = 1
    flags: tuple[str, ...] = PACK_FLAGS

    @property
    def check(self) -> str:
        return f"{self.id}@{self.version}"

    def definition(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "kind": self.kind,
            "sql": self.sql,
            "flags": list(self.flags),
            "evaluator_source_sha256": canonical_sha256(inspect.getsource(self.evaluator)),
        }

    def definition_sha256(self) -> str:
        return canonical_sha256(self.definition())

    def identity(self) -> str:
        return f"{self.id}@{self.version}#{self.definition_sha256()}"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE orders (
            id TEXT PRIMARY KEY, amount INTEGER, currency TEXT, status TEXT
        );
        CREATE TABLE payments (
            id TEXT PRIMARY KEY, order_id TEXT, amount INTEGER,
            currency TEXT, status TEXT
        );
        CREATE TABLE fulfilments (
            id INTEGER PRIMARY KEY, order_id TEXT, payment_id TEXT,
            amount INTEGER, created_at TEXT
        );
        """
    )
    return conn


def _load(conn: sqlite3.Connection, snapshot: TableSnapshot) -> None:
    for row in snapshot.rows("orders"):
        conn.execute(
            "INSERT INTO orders (id, amount, currency, status) VALUES (?, ?, ?, ?)",
            (row.get("id"), row.get("amount"), row.get("currency"), row.get("status")),
        )
    for row in snapshot.rows("payments"):
        conn.execute(
            "INSERT INTO payments (id, order_id, amount, currency, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                row.get("id"),
                row.get("order_id"),
                row.get("amount"),
                row.get("currency"),
                row.get("status"),
            ),
        )
    for row in snapshot.rows("fulfilments"):
        conn.execute(
            "INSERT INTO fulfilments (id, order_id, payment_id, amount, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                row.get("id"),
                row.get("order_id"),
                row.get("payment_id"),
                row.get("amount"),
                row.get("created_at"),
            ),
        )


def _with_sql(
    context: PackContext, snapshot: TableSnapshot, fn: Callable[[sqlite3.Connection, RoleIndex], list[Violation]]
) -> list[Violation]:
    conn = _connect()
    try:
        _load(conn, snapshot)
        return fn(conn, RoleIndex(context.roles))
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Members
# --------------------------------------------------------------------------- #
def _inv1(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    def run(conn: sqlite3.Connection, roles: RoleIndex) -> list[Violation]:
        rows = conn.execute(INV1_SQL).fetchall()
        return [
            Violation(
                check="INV-1@1",
                kind="duplicate_fulfilment",
                roles=[roles.order(row["order_id"])],
                entity_ids=[row["order_id"]],
                independent=True,
            )
            for row in rows
        ]

    return _with_sql(context, snapshot, run)


def _inv2(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    def run(conn: sqlite3.Connection, roles: RoleIndex) -> list[Violation]:
        out: list[Violation] = []
        for row in conn.execute(
            "SELECT f.id, f.order_id, f.payment_id FROM fulfilments f "
            "LEFT JOIN orders o ON o.id = f.order_id WHERE o.id IS NULL"
        ):
            out.append(
                Violation(
                    check="INV-2@1",
                    kind="missing_reference",
                    roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                    entity_ids=[row["order_id"], row["payment_id"]],
                    independent=True,
                )
            )
        for row in conn.execute(
            "SELECT f.id, f.order_id, f.payment_id FROM fulfilments f "
            "LEFT JOIN payments p ON p.id = f.payment_id WHERE p.id IS NULL"
        ):
            out.append(
                Violation(
                    check="INV-2@1",
                    kind="missing_reference",
                    roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                    entity_ids=[row["order_id"], row["payment_id"]],
                    independent=True,
                )
            )
        for row in conn.execute(
            "SELECT p.id, p.order_id FROM payments p "
            "LEFT JOIN orders o ON o.id = p.order_id WHERE o.id IS NULL"
        ):
            out.append(
                Violation(
                    check="INV-2@1",
                    kind="missing_reference",
                    roles=[roles.order(row["order_id"]), roles.payment(row["id"])],
                    entity_ids=[row["order_id"], row["id"]],
                    independent=True,
                )
            )
        for row in conn.execute(
            "SELECT f.order_id, f.payment_id, p.order_id AS payment_order "
            "FROM fulfilments f JOIN payments p ON p.id = f.payment_id "
            "WHERE p.order_id <> f.order_id"
        ):
            out.append(
                Violation(
                    check="INV-2@1",
                    kind="ownership_mismatch",
                    roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                    entity_ids=[row["order_id"], row["payment_id"]],
                    independent=True,
                )
            )
        return out

    return _with_sql(context, snapshot, run)


def _inv3(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    def run(conn: sqlite3.Connection, roles: RoleIndex) -> list[Violation]:
        out: list[Violation] = []
        for row in conn.execute(INV3_SQL):
            if row["p_status"] is None or row["o_amount"] is None:
                continue  # INV-2 owns missing references
            if row["p_status"] != "captured":
                out.append(
                    Violation(
                        check="INV-3@1",
                        kind="uncaptured_fulfilment",
                        roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                        entity_ids=[row["order_id"], row["payment_id"]],
                    )
                )
                continue
            if (
                row["f_amount"] != row["o_amount"]
                or row["p_amount"] != row["o_amount"]
                or row["p_currency"] != row["o_currency"]
            ):
                out.append(
                    Violation(
                        check="INV-3@1",
                        kind="amount_mismatch",
                        roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                        entity_ids=[row["order_id"], row["payment_id"]],
                    )
                )
        return out

    return _with_sql(context, snapshot, run)


def _inv4(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    def run(conn: sqlite3.Connection, roles: RoleIndex) -> list[Violation]:
        out: list[Violation] = []
        for row in conn.execute("SELECT id, status FROM orders"):
            if row["status"] not in ORDER_STATUSES:
                out.append(
                    Violation(
                        check="INV-4@1",
                        kind="invalid_state",
                        roles=[roles.order(row["id"])],
                        entity_ids=[row["id"]],
                    )
                )
        for row in conn.execute("SELECT id, status FROM payments"):
            if row["status"] not in PAYMENT_STATUSES:
                out.append(
                    Violation(
                        check="INV-4@1",
                        kind="invalid_state",
                        roles=[roles.payment(row["id"])],
                        entity_ids=[row["id"]],
                    )
                )
        for row in conn.execute(
            "SELECT p.id, p.order_id FROM payments p WHERE p.status = 'captured' "
            "AND NOT EXISTS (SELECT 1 FROM fulfilments f "
            "WHERE f.payment_id = p.id AND f.order_id = p.order_id)"
        ):
            out.append(
                Violation(
                    check="INV-4@1",
                    kind="captured_without_fulfilment",
                    roles=[roles.order(row["order_id"]), roles.payment(row["id"])],
                    entity_ids=[row["order_id"], row["id"]],
                )
            )
        for row in conn.execute(
            "SELECT o.id FROM orders o WHERE o.status = 'fulfilled' "
            "AND NOT EXISTS (SELECT 1 FROM fulfilments f WHERE f.order_id = o.id)"
        ):
            out.append(
                Violation(
                    check="INV-4@1",
                    kind="order_status_inconsistent",
                    roles=[roles.order(row["id"])],
                    entity_ids=[row["id"]],
                )
            )
        for row in conn.execute(
            "SELECT f.order_id, f.payment_id, o.status FROM fulfilments f "
            "JOIN orders o ON o.id = f.order_id WHERE o.status <> 'fulfilled'"
        ):
            out.append(
                Violation(
                    check="INV-4@1",
                    kind="order_status_inconsistent",
                    roles=[roles.order(row["order_id"]), roles.payment(row["payment_id"])],
                    entity_ids=[row["order_id"], row["payment_id"]],
                )
            )
        return out

    return _with_sql(context, snapshot, run)


def _inv5(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    roles = RoleIndex(context.roles)
    baseline_payments = {row.get("id"): row for row in context.baseline.rows("payments")}
    current_payments = {row.get("id"): row for row in snapshot.rows("payments")}
    fulfilments = snapshot.rows("fulfilments")
    out: list[Violation] = []

    for attempt in context.attempts:
        if not attempt.is_2xx:
            continue
        intent = context.delivery_intents.get(attempt.delivery_id)
        payment_id = context.delivery_payments.get(attempt.delivery_id)
        order_id = context.delivery_orders.get(attempt.delivery_id)
        if intent is None or payment_id is None or order_id is None:
            continue
        if intent == "capture_and_fulfil":
            payment = current_payments.get(payment_id)
            linked = any(
                row.get("payment_id") == payment_id and row.get("order_id") == order_id
                for row in fulfilments
            )
            if not (payment and payment.get("status") == "captured" and linked):
                out.append(
                    Violation(
                        check="INV-5@1",
                        kind="ack_without_effect",
                        roles=[roles.order(order_id), roles.payment(payment_id)],
                        entity_ids=[order_id, payment_id],
                    )
                )
        elif intent == "order_duplicate":
            linked_other = any(
                row.get("order_id") == order_id and row.get("payment_id") != payment_id
                for row in fulfilments
            )
            unchanged = (
                payment_id in baseline_payments
                and current_payments.get(payment_id) == baseline_payments[payment_id]
            )
            if not (linked_other and unchanged):
                out.append(
                    Violation(
                        check="INV-5@1",
                        kind="duplicate_ack_inconsistent",
                        roles=[roles.order(order_id), roles.payment(payment_id)],
                        entity_ids=[order_id, payment_id],
                    )
                )
    return out


def _row_key(table: str, row: dict[str, Any]) -> Any:
    if table == "orders":
        return row.get("id")
    if table == "payments":
        return row.get("id")
    return row.get("id")


def _inv6(context: PackContext, snapshot: TableSnapshot) -> list[Violation]:
    roles = RoleIndex(context.roles)
    allowed = context.allowed_order_ids
    out: list[Violation] = []

    def unauthorised(table: str, role: str, entity_id: str) -> Violation:
        return Violation(
            check="INV-6@1",
            kind="unauthorized_mutation",
            roles=[role],
            entity_ids=[str(entity_id)],
            independent=True,
        )

    baseline = {
        table: {_row_key(table, row): row for row in context.baseline.rows(table)}
        for table in ("orders", "payments", "fulfilments")
    }
    current = {
        table: {_row_key(table, row): row for row in snapshot.rows(table)}
        for table in ("orders", "payments", "fulfilments")
    }

    for row in snapshot.rows("orders"):
        if row.get("id") in allowed:
            continue
        if row.get("id") not in context.seed_order_ids:
            out.append(unauthorised("orders", roles.order(row.get("id", "")), row.get("id", "")))
            continue
        if baseline["orders"].get(row.get("id")) != row:
            out.append(unauthorised("orders", roles.order(row.get("id", "")), row.get("id", "")))

    for row in snapshot.rows("payments"):
        order_id = row.get("order_id")
        if order_id in allowed:
            continue
        if order_id not in context.seed_order_ids:
            out.append(unauthorised("payments", roles.payment(row.get("id", "")), row.get("id", "")))
            continue
        if baseline["payments"].get(row.get("id")) != row:
            out.append(unauthorised("payments", roles.payment(row.get("id", "")), row.get("id", "")))

    for row in snapshot.rows("fulfilments"):
        order_id = row.get("order_id")
        if order_id in allowed:
            continue
        if order_id not in context.seed_order_ids:
            out.append(
                unauthorised(
                    "fulfilments", roles.order(order_id or ""), str(row.get("payment_id", ""))
                )
            )
            continue
        out.append(
            unauthorised("fulfilments", roles.order(order_id), str(row.get("payment_id", "")))
        )

    # Deletions outside the footprint are mutations too.
    for row in context.baseline.rows("orders"):
        if row.get("id") in allowed:
            continue
        if row.get("id") not in current["orders"]:
            out.append(unauthorised("orders", roles.order(row.get("id", "")), row.get("id", "")))
    for row in context.baseline.rows("payments"):
        if row.get("order_id") in allowed:
            continue
        if row.get("id") not in current["payments"]:
            out.append(unauthorised("payments", roles.payment(row.get("id", "")), row.get("id", "")))

    return out


MEMBERS: tuple[Member, ...] = (
    Member("INV-1", "duplicate_fulfilment", INV1_SQL, True, _inv1),
    Member("INV-2", "references_and_ownership", INV2_SQL, True, _inv2),
    Member("INV-3", "capture_and_amounts", INV3_SQL, False, _inv3),
    Member("INV-4", "state_consistency", INV4_SQL, False, _inv4),
    Member("INV-5", "ack_matches_intent", INV5_SQL, False, _inv5),
    Member("INV-6", "authorized_footprint", INV6_SQL, True, _inv6),
)


def member_definition(member_id: str) -> dict[str, Any]:
    for member in MEMBERS:
        if member.id == member_id:
            return member.definition()
    raise KeyError(member_id)


def member_identities() -> list[str]:
    return sorted(member.identity() for member in MEMBERS)


def pack_digest() -> str:
    return canonical_sha256(member_identities())


def pack_identity() -> str:
    return f"{PACK_ID}@{PACK_VERSION}#{pack_digest()}"


def evaluate_pack(
    context: PackContext, snapshot: TableSnapshot, members: Iterable[Member] | None = None
) -> list[Violation]:
    out: list[Violation] = []
    seen: set[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = set()
    for member in members or MEMBERS:
        for violation in member.evaluator(context, snapshot):
            key = (
                violation.check,
                violation.kind,
                tuple(sorted(violation.roles)),
                tuple(sorted(violation.entity_ids)),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(violation)
    return out


def violation_key(violation: Violation) -> tuple[int, str, tuple[str, ...], str]:
    base = violation.check.split("@", 1)[0]
    return (PRECEDENCE.get(base, 99), violation.check, tuple(sorted(violation.roles)), violation.kind)


def primary_violation(violations: Iterable[Violation]) -> Violation | None:
    values = list(violations)
    if not values:
        return None
    return min(values, key=violation_key)


def member_by_id(member_id: str) -> Member:
    for member in MEMBERS:
        if member.id == member_id:
            return member
    raise KeyError(member_id)