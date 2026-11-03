"""Staging trial verdicts end to end: quiescence may upgrade INCONCLUSIVE only when it
is positively established. An HTTP 2xx never does. Observation is patched because
staging requires PostgreSQL, which these unit tests do not start."""
from __future__ import annotations

import pytest

from duress.external import observers
from duress.external.contract import Snapshot
from duress.external.services import trial
from tests.unit.staging_support import QUIESCENCE_TABLE, FakeStaging, ForwardProxy, load_staging


@pytest.fixture
def observed(monkeypatch):
    """Route the staging observation to the fake application's in-memory state."""
    monkeypatch.setenv("DURESS_TEST_DB", "postgresql://observer.invalid/duress")

    def use(app: FakeStaging) -> None:
        def fake_observe(contract, *, kind, database, schema, settled):
            return Snapshot(entities={"effects": app.snapshot_rows()}, available=True,
                            valid=True, settled=settled)

        monkeypatch.setattr(observers, "observe", fake_observe)

    return use


async def run_trial(tmp_path, app, **kwargs):
    project = load_staging(tmp_path / "project", app.origin, **kwargs)
    return await trial(project, project.manifest.scenarios[0], tmp_path / "home")


@pytest.mark.asyncio
async def test_attested_quiescence_allows_a_pass(tmp_path, observed):
    with FakeStaging("honest") as app:
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    verdict = record["verdict"]
    assert verdict["status"] == "PASS", verdict
    assert "remote_quiescence_attested" in verdict["reasons"]
    assert not any(r.startswith("settlement:") for r in verdict["reasons"])
    assert record["quiescence"]["before"]["outcome"] == "established"
    assert record["quiescence"]["after"]["outcome"] == "established"
    assert app.posts == ["/webhooks"]


@pytest.mark.asyncio
async def test_http_2xx_alone_stays_inconclusive_and_sends_no_event(tmp_path, observed):
    with FakeStaging("always_200") as app:
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    verdict = record["verdict"]
    assert verdict["status"] == "INCONCLUSIVE"
    assert "quiescence_not_attested" in verdict["reasons"]
    assert app.posts == [], "an unsettled baseline must not receive business events"


@pytest.mark.asyncio
async def test_unconfigured_staging_is_always_inconclusive(tmp_path, observed):
    with FakeStaging("honest") as app:
        observed(app)
        record = await run_trial(tmp_path, app)
    verdict = record["verdict"]
    assert verdict["status"] == "INCONCLUSIVE"
    assert "settlement:drain_unverified" in verdict["reasons"]
    assert "quiescence" not in record
    assert app.posts == ["/webhooks"], "legacy behaviour: delivery still happens, verdict does not"


@pytest.mark.asyncio
async def test_missing_probe_endpoint_is_inconclusive(tmp_path, observed):
    with FakeStaging("missing") as app:
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    assert record["verdict"]["status"] == "INCONCLUSIVE"
    assert "quiescence_probe_unavailable" in record["verdict"]["reasons"]
    assert app.posts == []


@pytest.mark.asyncio
async def test_writer_that_drifts_is_inconclusive(tmp_path, observed):
    with FakeStaging("drift") as app:
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    assert record["verdict"]["status"] == "INCONCLUSIVE"
    assert "quiescence_state_unstable" in record["verdict"]["reasons"]
    assert app.posts == []


@pytest.mark.asyncio
async def test_writer_going_busy_after_delivery_is_not_settled(tmp_path, observed):
    # The delivery's own 2xx must not stand in for the post-delivery attestation.
    with FakeStaging("honest") as app:
        app.post_attest = "not_quiescent"
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE)
    verdict = record["verdict"]
    assert verdict["status"] == "INCONCLUSIVE"
    assert "quiescence_not_attested" in verdict["reasons"]
    assert app.posts == ["/webhooks"]
    assert record["quiescence"]["before"]["outcome"] == "established"
    assert record["quiescence"]["after"]["outcome"] == "quiescence_not_attested"


@pytest.mark.asyncio
async def test_bad_proxy_reference_fails_closed_before_any_delivery(tmp_path, observed, monkeypatch):
    monkeypatch.setenv("DURESS_TEST_PROXY", "not a url")
    with FakeStaging("honest") as app:
        observed(app)
        with pytest.raises(ValueError):
            await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE,
                            target_extra='proxy_ref = "DURESS_TEST_PROXY"')
    assert app.posts == []


@pytest.mark.asyncio
async def test_declared_proxy_carries_probe_and_delivery(tmp_path, observed):
    with ForwardProxy() as proxy, FakeStaging("honest") as app:
        observed(app)
        record = await run_trial(tmp_path, app, quiescence=QUIESCENCE_TABLE,
                                 target_extra=f'proxy = "{proxy.origin}"')
    assert record["verdict"]["status"] == "PASS", record["verdict"]
    assert app.posts == ["/webhooks"]
    assert any(item == app.origin + "/webhooks" for item in proxy.requests)
    assert any(item.startswith(app.origin + "/duress/quiescence?nonce=") for item in proxy.requests)
