"""Quiescence probe against a local fake staging server: only a fresh, nonce-bound
attestation plus a stable, bracketed state can establish quiescence."""
from __future__ import annotations

import socket
import time

import pytest

from duress.external.contract import Snapshot
from duress.external.quiescence import await_quiescence
from tests.unit.staging_support import QUIESCENCE_TABLE, FakeStaging, load_staging

ROWS = [{"id": 1, "entity": "owned"}]


def settled_snapshot(rows=ROWS, *, available=True, valid=True) -> Snapshot:
    return Snapshot(entities={"effects": list(rows)}, available=available, valid=valid, settled=True)


def counting_reader(rows=ROWS):
    calls = {"reads": 0}

    def read():
        calls["reads"] += 1
        return settled_snapshot(rows)

    return read, calls


@pytest.mark.asyncio
async def test_fresh_attestation_and_stable_bracketed_state_establish_quiescence(tmp_path):
    with FakeStaging("honest") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        read, calls = counting_reader()
        outcome = await await_quiescence(project, read, {})
    assert outcome.established and outcome.reason is None
    assert outcome.snapshot.entities == {"effects": ROWS}
    # Three stable reads, each bracketed by two attestations.
    assert calls["reads"] == 3 and app.probes == 6
    assert outcome.evidence["outcome"] == "established"
    assert "nonce" not in str(outcome.evidence)


@pytest.mark.asyncio
async def test_http_2xx_without_attestation_never_establishes(tmp_path):
    with FakeStaging("always_200") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        read, calls = counting_reader()
        outcome = await await_quiescence(project, read, {})
    assert not outcome.established
    assert outcome.reason == "quiescence_not_attested"
    assert calls["reads"] == 0, "state must not be trusted without an attestation"


@pytest.mark.asyncio
async def test_missing_probe_endpoint_is_reported_as_unavailable_immediately(tmp_path):
    # A generous 5s budget: returning well inside it shows the probe did not wait it out.
    slow_budget = QUIESCENCE_TABLE.replace("timeout_ms = 600", "timeout_ms = 5000")
    with FakeStaging("missing") as app:
        project = load_staging(tmp_path, app.origin, quiescence=slow_budget)
        read, calls = counting_reader()
        started = time.monotonic()
        outcome = await await_quiescence(project, read, {})
    assert outcome.reason == "quiescence_probe_unavailable" and not outcome.established
    assert calls["reads"] == 0 and time.monotonic() - started < 2.5


@pytest.mark.asyncio
async def test_replayed_nonce_is_rejected(tmp_path):
    with FakeStaging("replay") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        outcome = await await_quiescence(project, counting_reader()[0], {})
    assert not outcome.established and outcome.reason == "quiescence_not_attested"


@pytest.mark.asyncio
async def test_busy_writer_reporting_not_quiescent_is_not_established(tmp_path):
    with FakeStaging("not_quiescent") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        read, calls = counting_reader()
        outcome = await await_quiescence(project, read, {})
    assert not outcome.established and outcome.reason == "quiescence_not_attested"
    assert calls["reads"] == 0


@pytest.mark.asyncio
async def test_state_that_keeps_changing_is_unstable_and_bounded(tmp_path):
    with FakeStaging("honest") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        counter = {"n": 0}

        def moving_reader():
            counter["n"] += 1
            return settled_snapshot([{"id": counter["n"], "entity": "owned"}])

        started = time.monotonic()
        outcome = await await_quiescence(project, moving_reader, {})
        elapsed = time.monotonic() - started
    assert not outcome.established
    assert outcome.reason == "quiescence_state_unstable"
    assert elapsed < 3.0, "the probe must respect its timeout_ms bound"


@pytest.mark.asyncio
async def test_writer_that_keeps_writing_while_attesting_never_settles(tmp_path):
    with FakeStaging("drift") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        outcome = await await_quiescence(project, lambda: settled_snapshot(app.snapshot_rows()), {})
    assert not outcome.established and outcome.reason == "quiescence_state_unstable"


@pytest.mark.asyncio
async def test_unavailable_state_read_is_not_quiescent(tmp_path):
    with FakeStaging("honest") as app:
        project = load_staging(tmp_path, app.origin, quiescence=QUIESCENCE_TABLE)
        outcome = await await_quiescence(project, lambda: settled_snapshot(available=False, valid=False), {})
    assert not outcome.established and outcome.reason == "quiescence_state_unavailable"


@pytest.mark.asyncio
async def test_unreachable_probe_target_is_bounded_and_not_established(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        closed_port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{closed_port}"
    project = load_staging(tmp_path, origin, quiescence=QUIESCENCE_TABLE)
    started = time.monotonic()
    outcome = await await_quiescence(project, counting_reader()[0], {})
    assert not outcome.established and outcome.reason == "quiescence_unreachable"
    assert time.monotonic() - started < 3.0


@pytest.mark.asyncio
async def test_without_configuration_no_probe_is_attempted(tmp_path):
    project = load_staging(tmp_path, "http://127.0.0.1:9")
    outcome = await await_quiescence(project, counting_reader()[0], {})
    assert not outcome.established and outcome.reason == "staging_quiescence_unverified"
