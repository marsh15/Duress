"""Read-only, bounded SQLite and PostgreSQL observers for external contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import UUID

from .contract import Contract, EntitySpec, Snapshot, _canonical_value

OBSERVER_VERSION = "2"
MAX_ROWS_PER_ENTITY = 5_000
QUERY_TIMEOUT_MS = 3_000
CONNECT_TIMEOUT_SECONDS = 3
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def observe(
    contract: Contract,
    *,
    kind: str,
    database: str | Path,
    schema: str = "public",
    settled: bool = False,
) -> Snapshot:
    """Read allowlisted rows as a bounded, repeatable, read-only snapshot.

    ``kind`` is ``sqlite`` or ``postgresql``. ``database`` is a SQLite file
    path or a resolved PostgreSQL DSN. DSNs are used only for the connection;
    neither DSNs nor driver exception messages are copied into the snapshot.
    No caller-provided SQL is accepted.
    """

    if not isinstance(contract, Contract):
        raise TypeError("contract must be a validated Contract")
    if not isinstance(settled, bool):
        return _invalid("settled must be a boolean", settled=False)
    if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
        return _invalid("schema must be a simple SQL identifier", settled=settled)
    if not isinstance(database, (str, Path)) or not str(database):
        return _invalid("database must be a file path or resolved PostgreSQL DSN", settled=settled)

    if kind == "sqlite":
        if schema not in {"public", "main"}:
            return _invalid("SQLite observer supports only the main database", settled=settled)
        return _observe_sqlite(contract, Path(database), settled=settled, schema="main")
    if kind == "postgresql":
        return _observe_postgresql(contract, str(database), schema=schema, settled=settled)
    return _invalid("kind must be 'sqlite' or 'postgresql'", settled=settled)


def _observe_sqlite(contract: Contract, path: Path, *, settled: bool, schema: str) -> Snapshot:
    connection: sqlite3.Connection | None = None
    try:
        uri = f"{path.expanduser().resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=CONNECT_TIMEOUT_SECONDS)
        connection.row_factory = sqlite3.Row
        deadline = monotonic() + QUERY_TIMEOUT_MS / 1_000
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1_000)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        entities, missing, truncated, schema_rows = _read_sqlite_entities(connection, contract)
        connection.commit()
        provenance = _provenance("sqlite", schema, schema_rows)
        if missing:
            provenance["missing_entities"] = missing
        if truncated:
            provenance["truncated_entities"] = truncated
        return Snapshot(
            entities=entities,
            available=True,
            valid=True,
            settled=settled,
            provenance=provenance,
            error=("one or more configured entities were unavailable" if missing else None),
        )
    except sqlite3.Error as exc:
        return Snapshot(
            entities={},
            available=False,
            valid=False,
            settled=settled,
            provenance={"adapter": "sqlite", "version": OBSERVER_VERSION, "read_only": True},
            error=f"SQLite observation failed ({type(exc).__name__})",
        )
    except (OSError, ValueError, TypeError) as exc:
        return Snapshot(
            entities={},
            available=True,
            valid=False,
            settled=settled,
            provenance={"adapter": "sqlite", "version": OBSERVER_VERSION, "read_only": True},
            error=f"SQLite snapshot normalization failed ({type(exc).__name__})",
        )
    finally:
        if connection is not None:
            connection.close()


def _read_sqlite_entities(
    connection: sqlite3.Connection, contract: Contract
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    entities: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []
    truncated: list[str] = []
    schema_rows: list[dict[str, Any]] = []

    for name, spec in contract.entities.items():
        quoted_table = _quote(spec.table)
        table_info = connection.execute(f"PRAGMA table_info({quoted_table})").fetchall()
        existing = {row["name"] for row in table_info}
        column_metadata = {
            row["name"]: {
                "type": (row["type"] or "").strip().upper(),
                "nullable": not bool(row["notnull"]),
            }
            for row in table_info
        }
        absent = [column for column in spec.columns if column not in existing]
        schema_rows.append(_schema_description(name, spec, column_metadata))
        if not table_info:
            missing.append({"entity": name, "reason": "missing_table"})
            continue
        if absent:
            missing.append({"entity": name, "reason": "missing_columns", "columns": absent})
            continue
        selection = ", ".join(_quote(column) for column in spec.columns)
        query = f"SELECT {selection} FROM {quoted_table} LIMIT ?"
        rows = connection.execute(query, (MAX_ROWS_PER_ENTITY + 1,)).fetchall()
        if len(rows) > MAX_ROWS_PER_ENTITY:
            truncated.append(name)
            continue
        entities[name] = [
            {
                column: _normalize_sqlite_value(row[column], column_metadata[column]["type"])
                for column in spec.columns
            }
            for row in rows
        ]
    return entities, missing, truncated, schema_rows


def _observe_postgresql(
    contract: Contract, dsn: str, *, schema: str, settled: bool
) -> Snapshot:
    try:
        import psycopg
    except ImportError:
        return Snapshot(
            entities={},
            available=False,
            valid=False,
            settled=settled,
            provenance={"adapter": "postgresql", "version": OBSERVER_VERSION, "read_only": True},
            error="PostgreSQL observation requires the optional psycopg dependency",
        )

    connection = None
    try:
        connection = psycopg.connect(
            dsn,
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            options=(
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={QUERY_TIMEOUT_MS} "
                "-c lock_timeout=1000"
            ),
            autocommit=False,
        )
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            entities, missing, truncated, schema_rows = _read_postgres_entities(
                cursor, contract, schema
            )
        connection.commit()
        provenance = _provenance("postgresql", schema, schema_rows)
        if missing:
            provenance["missing_entities"] = missing
        if truncated:
            provenance["truncated_entities"] = truncated
        return Snapshot(
            entities=entities,
            available=True,
            valid=True,
            settled=settled,
            provenance=provenance,
            error=("one or more configured entities were unavailable" if missing else None),
        )
    except Exception as exc:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass
        return Snapshot(
            entities={},
            available=False,
            valid=False,
            settled=settled,
            provenance={"adapter": "postgresql", "version": OBSERVER_VERSION, "read_only": True},
            error=f"PostgreSQL observation failed ({type(exc).__name__})",
        )
    finally:
        if connection is not None:
            connection.close()


def _read_postgres_entities(
    cursor: Any, contract: Contract, schema: str
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    entities: dict[str, list[dict[str, Any]]] = {}
    missing: list[dict[str, Any]] = []
    truncated: list[str] = []
    schema_rows: list[dict[str, Any]] = []

    for name, spec in contract.entities.items():
        cursor.execute(
            "SELECT column_name, data_type, udt_name, is_nullable, "
            "character_maximum_length, numeric_precision, numeric_scale, datetime_precision "
            "FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (schema, spec.table),
        )
        column_metadata = {
            row[0]: {
                "type": (row[1] or "").strip().lower(),
                "nullable": row[3] == "YES",
                "type_details": {
                    "udt_name": row[2],
                    "character_maximum_length": row[4],
                    "numeric_precision": row[5],
                    "numeric_scale": row[6],
                    "datetime_precision": row[7],
                },
            }
            for row in cursor.fetchall()
        }
        existing = set(column_metadata)
        absent = [column for column in spec.columns if column not in existing]
        schema_rows.append(_schema_description(name, spec, column_metadata))
        if not existing:
            missing.append({"entity": name, "reason": "missing_table"})
            continue
        if absent:
            missing.append({"entity": name, "reason": "missing_columns", "columns": absent})
            continue

        selection = ", ".join(_quote(column) for column in spec.columns)
        query = (
            f"SELECT {selection} FROM {_quote(schema)}.{_quote(spec.table)} "
            f"LIMIT {MAX_ROWS_PER_ENTITY + 1}"
        )
        cursor.execute(query)
        raw_rows = cursor.fetchall()
        if len(raw_rows) > MAX_ROWS_PER_ENTITY:
            truncated.append(name)
            continue
        entities[name] = [
            {
                column: _normalize_db_value(value)
                for column, value in zip(spec.columns, raw_row, strict=True)
            }
            for raw_row in raw_rows
        ]
    return entities, missing, truncated, schema_rows


def _normalize_db_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    normalized = _canonical_value(value)
    if isinstance(normalized, float) and not math.isfinite(normalized):
        raise ValueError("non-finite numeric values are unsupported")
    return normalized


def _normalize_sqlite_value(value: Any, declared_type: str) -> Any:
    """Normalize SQLite's dynamically typed values using its declared affinity."""

    if value is None:
        return None
    if any(part in declared_type for part in ("NUMERIC", "DECIMAL")) and isinstance(value, float):
        return _canonical_value(Decimal(str(value)))
    if "DATE" in declared_type and "TIME" not in declared_type and isinstance(value, str):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return value
    if any(part in declared_type for part in ("DATE", "TIME")) and isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        return _canonical_value(parsed)
    return _normalize_db_value(value)


def _schema_description(
    name: str, spec: EntitySpec, column_metadata: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Describe contract-selected fields with their observed SQL metadata."""

    return {
        "entity": name,
        "table": spec.table,
        "key": spec.key,
        "columns": [
            {
                "name": column,
                "present": column in column_metadata,
                **column_metadata.get(column, {"type": None, "nullable": None}),
            }
            for column in sorted(spec.columns)
        ],
    }


def _provenance(kind: str, schema: str, schema_rows: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = json.dumps(
        {
            "adapter": kind,
            "entities": sorted(schema_rows, key=lambda item: item["entity"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "adapter": kind,
        "version": OBSERVER_VERSION,
        "read_only": True,
        "schema": schema,
        "schema_fingerprint": hashlib.sha256(canonical).hexdigest(),
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "row_limit_per_entity": MAX_ROWS_PER_ENTITY,
    }


def _quote(identifier: str) -> str:
    """Quote an identifier already restricted by the contract validator."""

    if not isinstance(identifier, str) or not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("unsafe SQL identifier")
    return f'"{identifier}"'


def _invalid(message: str, *, settled: bool) -> Snapshot:
    return Snapshot(
        entities={},
        available=False,
        valid=False,
        settled=settled,
        provenance={"adapter": "external", "version": OBSERVER_VERSION, "read_only": True},
        error=message,
    )


__all__ = ["MAX_ROWS_PER_ENTITY", "observe"]
