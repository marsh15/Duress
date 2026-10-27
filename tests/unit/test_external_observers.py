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
    assert snapshot.provenance["version"] == "2"
    assert len(snapshot.provenance["schema_fingerprint"]) == 64
    assert str(database) not in repr(snapshot.provenance)
    assert observed_connections[0].write_blocked

    result = evaluate(
        contract_for_sqlite(),
        type(snapshot)(**{**snapshot.model_dump(), "entities": {"bookings": snapshot.entities["bookings"], "tickets": []}}),
        snapshot,
        context={},
    )
    assert result[0].status == "satisfied"


def test_sqlite_schema_fingerprint_tracks_selected_fields_types_and_nullability(tmp_path):
    def make_database(path, amount_declaration="NUMERIC"):
        with sqlite3.connect(path) as connection:
            connection.executescript(
                "CREATE TABLE booking(id TEXT PRIMARY KEY, "
                f"amount {amount_declaration}, created_at TEXT);"
                "CREATE TABLE ticket(id TEXT PRIMARY KEY, booking_id TEXT, amount NUMERIC);"
            )

    baseline_path = tmp_path / "baseline.db"
    type_drift_path = tmp_path / "type-drift.db"
    nullability_drift_path = tmp_path / "nullability-drift.db"
    make_database(baseline_path)
    make_database(type_drift_path, "TEXT")
    make_database(nullability_drift_path, "NUMERIC NOT NULL")

    baseline = observe(contract_for_sqlite(), kind="sqlite", database=baseline_path)
    repeated = observe(contract_for_sqlite(), kind="sqlite", database=baseline_path)
    type_drift = observe(contract_for_sqlite(), kind="sqlite", database=type_drift_path)
    nullability_drift = observe(
        contract_for_sqlite(), kind="sqlite", database=nullability_drift_path
    )

    fingerprint = baseline.provenance["schema_fingerprint"]
    assert fingerprint == repeated.provenance["schema_fingerprint"]
    assert fingerprint != type_drift.provenance["schema_fingerprint"]
    assert fingerprint != nullability_drift.provenance["schema_fingerprint"]


def test_sqlite_schema_fingerprint_tracks_the_contract_selected_field_set(tmp_path):
    database = tmp_path / "selected.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE booking(id TEXT PRIMARY KEY, amount NUMERIC, created_at TEXT);"
            "CREATE TABLE ticket(id TEXT PRIMARY KEY, booking_id TEXT, amount NUMERIC);"
        )
    original = contract_for_sqlite()
    reordered_payload = original.model_dump(mode="python")
    reordered_payload["entities"]["bookings"]["columns"] = ["created_at", "amount", "id"]
    reordered_payload["entities"]["tickets"]["columns"] = ["amount", "id", "booking_id"]
    reordered = Contract.model_validate(reordered_payload)
    subset = Contract.model_validate(
        {
            "schema_version": 1,
            "id": "inventory-subset-v1",
            "version": 1,
            "entities": {
                "bookings": {
                    "table": "booking",
                    "key": "id",
                    "columns": ["id", "created_at"],
                },
                "tickets": {
                    "table": "ticket",
                    "key": "id",
                    "columns": ["id", "booking_id"],
                },
            },
            "invariants": [
                {"id": "ticket-seen", "kind": "expected_effect", "entity": "tickets"}
            ],
        }
    )

    original_snapshot = observe(original, kind="sqlite", database=database)
    reordered_snapshot = observe(reordered, kind="sqlite", database=database)
    subset_snapshot = observe(subset, kind="sqlite", database=database)

    assert (
        original_snapshot.provenance["schema_fingerprint"]
        == reordered_snapshot.provenance["schema_fingerprint"]
    )
    assert (
        original_snapshot.provenance["schema_fingerprint"]
        != subset_snapshot.provenance["schema_fingerprint"]
    )


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
                self.next_rows = [
                    ("id", "text", "text", "NO", None, None, None, None),
                    ("amount", "numeric", "numeric", "YES", None, 10, 2, None),
                    (
                        "created_at",
                        "timestamp with time zone",
                        "timestamptz",
                        "YES",
                        None,
                        None,
                        None,
                        6,
                    ),
                ]
            else:
                self.next_rows = [
                    ("id", "text", "text", "NO", None, None, None, None),
                    ("booking_id", "text", "text", "YES", None, None, None, None),
                    ("amount", "numeric", "numeric", "YES", None, 10, 2, None),
                ]
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


def test_schema_fingerprint_is_portable_across_fresh_trial_namespaces():
    from duress.external.observers import _provenance
    fields=[{'entity':'things','table':'things','key':'id','columns':[{'name':'id','present':True,'type':'integer','nullable':False}]}]
    first=_provenance('postgresql','duress_trial_a',fields)
    second=_provenance('postgresql','duress_trial_b',fields)
    assert first['schema']!=second['schema']
    assert first['schema_fingerprint']==second['schema_fingerprint']
