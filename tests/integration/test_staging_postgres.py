"""Staging trials against a real PostgreSQL observer.

The unit tests patch observation, so they never exercise the DSN, the repeatable-read
snapshot or the bracketed reads against a live database. These do. They create and drop
a throwaway database, and skip cleanly when psycopg or a reachable server is missing.

``DURESS_TEST_PG_ADMIN_DSN`` selects the server (it needs CREATEDB); otherwise libpq
defaults (``PG*`` variables, local socket) are tried with a short connect timeout.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

from duress.external.services import trial
from tests.unit.staging_support import FakeStaging, load_staging

psycopg = pytest.importorskip("psycopg", reason="the postgresql extra (psycopg) is not installed")


# Real database reads cost milliseconds each (and the first pays connection setup), so the
# stable window needs far more headroom than the in-memory unit-test table allows.
QUIESCENCE_TABLE = """\
[target.quiescence]
path = "/duress/quiescence"
stable_reads = 3
interval_ms = 50
timeout_ms = 8000
"""


def _admin_dsn() -> str:
    return os.environ.get("DURESS_TEST_PG_ADMIN_DSN") or "dbname=postgres"


@pytest.fixture
def pg_dsn(monkeypatch):
    """A fresh database with the business table, exposed to the manifest as DURESS_TEST_DB."""
    name = f"duress_it_{uuid.uuid4().hex[:12]}"
    try:
        admin = psycopg.connect(_admin_dsn(), autocommit=True, connect_timeout=3)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL is not reachable ({type(exc).__name__})")
    try:
        try:
            admin.execute(f'CREATE DATABASE "{name}"')
        except psycopg.Error as exc:
            pytest.skip(f"cannot create a throwaway database ({type(exc).__name__})")
        dsn = psycopg.conninfo.make_conninfo(_admin_dsn(), dbname=name)
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("CREATE TABLE effects (id serial PRIMARY KEY, entity text NOT NULL)")
        monkeypatch.setenv("DURESS_TEST_DB", dsn)
        yield dsn
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


class PgEffects(list):
    """The fake application's effect log, written through to the real database."""

    def __init__(self, dsn: str, per_write: int = 1):
        super().__init__()
        self.dsn = dsn
        self.per_write = per_write

    def append(self, row):
        super().append(row)
        with psycopg.connect(self.dsn, autocommit=True) as connection:
            for _ in range(self.per_write):
                connection.execute("INSERT INTO effects (entity) VALUES (%s)", (row["entity"],))


def row_count(dsn: str) -> int:
    with psycopg.connect(dsn) as connection:
        return connection.execute("SELECT count(*) FROM effects").fetchone()[0]


async def run_trial(tmp_path, app, **kwargs):
    project = load_staging(tmp_path / "project", app.origin, **kwargs)
    return await trial(project, project.manifest.scenarios[0], tmp_path / "home")


@pytest.mark.asyncio
async def test_attested_quiescence_over_real_postgres_reaches_pass(tmp_path, pg_dsn):
    with FakeStaging("honest") as app:
        app.effects = PgEffects(pg_dsn)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    verdict = record["verdict"]
    assert verdict["status"] == "PASS", verdict
    assert "remote_quiescence_attested" in verdict["reasons"]
    assert record["quiescence"]["before"]["outcome"] == "established"
    assert record["quiescence"]["after"]["outcome"] == "established"
    assert row_count(pg_dsn) == 1
    assert pg_dsn not in json.dumps(record), "the DSN must never be copied into a record"


@pytest.mark.asyncio
async def test_duplicate_effects_in_real_postgres_fail_the_business_invariant(tmp_path, pg_dsn):
    with FakeStaging("honest") as app:
        app.effects = PgEffects(pg_dsn, per_write=2)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    verdict = record["verdict"]
    assert verdict["status"] == "FAIL", verdict
    assert [item["id"] for item in record["invariants"] if item["status"] == "violated"] == ["unique-effect"]
    assert "remote_quiescence_attested" in verdict["reasons"]
    assert row_count(pg_dsn) == 2


@pytest.mark.asyncio
async def test_writer_that_keeps_writing_to_real_postgres_stays_inconclusive(tmp_path, pg_dsn):
    with FakeStaging("drift") as app:
        app.effects = PgEffects(pg_dsn)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    assert record["verdict"]["status"] == "INCONCLUSIVE"
    assert "quiescence_state_unstable" in record["verdict"]["reasons"]
    assert app.posts == [], "an unstable baseline must not receive business events"


@pytest.mark.asyncio
async def test_unreachable_real_database_is_inconclusive_not_a_pass(tmp_path, pg_dsn, monkeypatch):
    monkeypatch.setenv("DURESS_TEST_DB", psycopg.conninfo.make_conninfo(pg_dsn, dbname="duress_absent_db"))
    with FakeStaging("honest") as app:
        app.effects = PgEffects(pg_dsn)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    assert record["verdict"]["status"] == "INCONCLUSIVE"
    assert app.posts == []
