from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import ModuleType

from duress.external.contract import Contract, evaluate
from duress.external.observers import MAX_ROWS_PER_ENTITY, observe


def contract_for_sqlite() -> Contract:
    return Contract.model_validate(
        {
            "schema_version": 1,
            "id": "inventory-v1",
            "version": 1,
            "entities": {
                "bookings": {
                    "table": "booking",
                    "key": "id",
                    "columns": ["id", "amount", "created_at"],
                },
                "tickets": {
                    "table": "ticket",
                    "key": "id",
                    "columns": ["id", "booking_id", "amount"],
                },
            },
            "invariants": [
                {
                    "id": "ticket-amount",
                    "kind": "value_consistency",
                    "mode": "fields_equal",
                    "entity": "tickets",
                    "field": "amount",
                    "related_entity": "bookings",
                    "reference": "booking_id",
                    "related_key": "id",
                    "related_field": "amount",
                }
            ],
        }
    )


def test_sqlite_observer_is_read_only_allowlisted_and_settled(tmp_path, monkeypatch):
    database = tmp_path / "business.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE booking(id TEXT PRIMARY KEY, amount NUMERIC, created_at TEXT, secret TEXT);"
            "CREATE TABLE ticket(id TEXT PRIMARY KEY, booking_id TEXT, amount NUMERIC);"
            "INSERT INTO booking VALUES ('b1', 12.50, '2026-10-27T00:00:00', 'do-not-read');"
            "INSERT INTO ticket VALUES ('t1', 'b1', 12.50);"
        )

    original_connect = sqlite3.connect
    observed_connections = []

    class ProbeConnection(sqlite3.Connection):
        write_blocked = False

        def close(self):
            try:
                self.execute("UPDATE booking SET amount = amount WHERE id = 'b1'")
            except sqlite3.OperationalError:
                self.write_blocked = True
            finally:
                super().close()

    def tracked_connect(*args, **kwargs):
        kwargs["factory"] = ProbeConnection
        connection = original_connect(*args, **kwargs)
        observed_connections.append(connection)
        return connection

    monkeypatch.setattr("duress.external.observers.sqlite3.connect", tracked_connect)
    snapshot = observe(contract_for_sqlite(), kind="sqlite", database=database, settled=True)

    assert snapshot.available and snapshot.valid and snapshot.settled
    assert set(snapshot.entities) == {"bookings", "tickets"}
    assert set(snapshot.entities["bookings"][0]) == {"id", "amount", "created_at"}
    assert "do-not-read" not in repr(snapshot.model_dump())
    assert snapshot.provenance["adapter"] == "sqlite"
    assert snapshot.provenance["read_only"] is True
    assert str(database) not in repr(snapshot.provenance)
    assert observed_connections[0].write_blocked

    result = evaluate(
        contract_for_sqlite(),
        type(snapshot)(**{**snapshot.model_dump(), "entities": {"bookings": snapshot.entities["bookings"], "tickets": []}}),
        snapshot,
        context={},
    )
    assert result[0].status == "satisfied"


def test_sqlite_missing_table_or_column_is_missing_evidence_not_empty_proof(tmp_path):
    database = tmp_path / "partial.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE booking(id TEXT PRIMARY KEY, amount NUMERIC)")

    snapshot = observe(contract_for_sqlite(), kind="sqlite", database=database, settled=True)

    assert snapshot.available and snapshot.valid
    assert "tickets" not in snapshot.entities
    assert snapshot.provenance["missing_entities"]
    result = evaluate(contract_for_sqlite(), snapshot, snapshot)
    assert result[0].status == "not_evaluable"


def test_sqlite_row_limit_omits_truncated_entity(tmp_path, monkeypatch):
    database = tmp_path / "large.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE booking(id INTEGER PRIMARY KEY, amount NUMERIC, created_at TEXT)")
        connection.execute("CREATE TABLE ticket(id INTEGER PRIMARY KEY, booking_id INTEGER, amount NUMERIC)")
        connection.executemany(
            "INSERT INTO booking(id, amount, created_at) VALUES (?, 1, 'now')",
            ((index,) for index in range(MAX_ROWS_PER_ENTITY + 1)),
        )
    monkeypatch.setattr("duress.external.observers.MAX_ROWS_PER_ENTITY", 20)

    snapshot = observe(contract_for_sqlite(), kind="sqlite", database=database, settled=True)

    assert "bookings" not in snapshot.entities
    assert "bookings" in snapshot.provenance["truncated_entities"]
    assert evaluate(contract_for_sqlite(), snapshot, snapshot)[0].status == "not_evaluable"


def test_unsafe_schema_and_kind_are_rejected_before_database_access(tmp_path):
    contract = contract_for_sqlite()

    unsafe_schema = observe(
        contract, kind="postgresql", database="postgresql://user:secret@host/db", schema='public";DROP', settled=True
    )
    unknown_kind = observe(contract, kind="arbitrary", database=tmp_path / "x.db")

    assert not unsafe_schema.available and not unsafe_schema.valid
    assert "secret" not in repr(unsafe_schema.model_dump())
    assert not unknown_kind.available and not unknown_kind.valid


class FakeCursor:
    def __init__(self):
        self.description = []
        self.queries = []
        self.next_rows = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, parameters=None):
        self.queries.append((query, parameters))
        if "information_schema.columns" in query:
            if parameters[1] == "booking":
                self.next_rows = [("id",), ("amount",), ("created_at",)]
            else:
                self.next_rows = [("id",), ("booking_id",), ("amount",)]
        elif 'FROM "ticketing"."booking"' in query:
            self.next_rows = [
                ("b1", Decimal("12.500"), datetime(2026, 10, 27, 12, tzinfo=timezone.utc))
            ]
        else:
            self.next_rows = [("t1", "b1", Decimal("12.500"))]

    def fetchall(self):
        return self.next_rows


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def test_postgres_observer_uses_repeatable_read_and_never_retains_dsn(monkeypatch):
    connection = FakeConnection()
    fake_driver = ModuleType("psycopg")
    options = {}

    def connect(dsn, **kwargs):
        options["dsn"] = dsn
        options.update(kwargs)
        return connection

    fake_driver.connect = connect
    monkeypatch.setitem(sys.modules, "psycopg", fake_driver)

    snapshot = observe(
        contract_for_sqlite(),
        kind="postgresql",
        database="postgresql://alice:top-secret@localhost/private",
        schema="ticketing",
        settled=True,
    )

    assert snapshot.available and snapshot.valid and snapshot.settled
    assert snapshot.entities["bookings"][0]["amount"] == "12.5"
    assert snapshot.entities["bookings"][0]["created_at"] == "2026-10-27T12:00:00.000000+00:00"
    assert connection.committed
    assert "READ ONLY" in connection.cursor_instance.queries[0][0]
    assert options["connect_timeout"] > 0
    assert "default_transaction_read_only=on" in options["options"]
    assert "top-secret" not in repr(snapshot.model_dump())
    assert "alice" not in repr(snapshot.provenance)
    assert all(";" not in query for query, _ in connection.cursor_instance.queries)
