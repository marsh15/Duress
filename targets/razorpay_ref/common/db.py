"""SQLite lifecycle helpers.

Contract C.1:
  * one connection per request, ``isolation_level=None`` (explicit transactions),
    ``timeout=5``, always closed in ``finally``;
  * WAL is enabled once, at ``init-db``;
  * every connection enables foreign keys and a 5000 ms busy timeout.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .schema import schema_for


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: str | Path, seed_path: str | Path, variant: str) -> None:
    """Create a fresh database, enable WAL, apply the schema and seed it."""
    db_path = Path(db_path)
    seed_path = Path(seed_path)
    if db_path.exists():
        raise FileExistsError(f"refusing to initialise over existing database: {db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    seed = json.loads(seed_path.read_text())
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(schema_for(variant))
        conn.execute("BEGIN")
        for order in seed.get("orders", []):
            conn.execute(
                "INSERT INTO orders (id, amount, currency, status) VALUES (?, ?, ?, ?)",
                (order["id"], order["amount"], order["currency"], order["status"]),
            )
        for payment in seed.get("payments", []):
            conn.execute(
                "INSERT INTO payments (id, order_id, amount, currency, status)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    payment["id"],
                    payment["order_id"],
                    payment["amount"],
                    payment["currency"],
                    payment["status"],
                ),
            )
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def rows_as_dicts(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]
