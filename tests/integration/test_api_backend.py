"""M3/M4 API contracts: admission, status codes and record polling.

The long-running services are replaced here by fast fakes that write the same
records the real services write, so these tests check the HTTP contract only.
The services themselves are exercised against real targets in
``tests/e2e/test_exhaustive_fallback.py``, ``tests/integration/test_investigation_e2e.py``
and ``tests/integration/test_reduce.py``.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from duress import services
from duress.api import Settings, create_app
from duress.orchestrator import initial_record, investigation_path
from duress.store import atomic_write_json, save_dna

ROOT = Path(__file__).resolve().parents[2]
TOKEN = {"X-Duress-Token": "s3cret"}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / ".duress"
    monkeypatch.setenv("DURESS_HOME", str(home))
    return home


@pytest.fixture
def fake_investigate(monkeypatch):
    calls = []

    async def fake(workspace, *, target_id, mode, investigation_id=None, reduce=False, **_):
        calls.append({"target_id": target_id, "mode": mode, "reduce": reduce})
        record = initial_record(investigation_id, target_id, mode, wall_cap_s=1.0, trial_cap=130)
        record.update(
            status="completed",
            mode_effective="exhaustive",
            fallback_reason="cache_miss",
            verdict="FAIL",
            hypotheses=[{"draft": {"id": "H1"}, "usable": False, "status": {"citation": "invalid"}}],
            findings=[{"signature": {"check": "INV-1@1"}, "dna_id": "abc"}],
            dna_ids=["abc"],
        )
        atomic_write_json(investigation_path(workspace.home, investigation_id), record)
        return record

    monkeypatch.setattr(services, "investigate", fake)
    return calls


def wait_for(http, url, *, statuses=("completed", "failed"), timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.get(url).json()
        if body.get("status") in statuses:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{url} did not finish: {body}")


def wait_for_status(http, url, status, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.get(url).json()
        if body.get("status") == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"{url} did not reach {status}: {body}")


def test_anonymous_users_may_only_start_cached_investigations(fake_investigate):
    with TestClient(create_app(Settings(token="s3cret", root=ROOT))) as http:
        for mode in ("live", "exhaustive"):
            response = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable", "mode": mode})
            assert response.status_code == 401
        accepted = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable", "mode": "cached"})
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["status"] == "queued"
        assert body["status_url"] == f"/api/investigations/{body['investigation_id']}"
        wait_for(http, body["status_url"])
        with_token = http.post(
            "/api/investigations",
            json={"target_id": "razorpay-vulnerable", "mode": "live"},
            headers=TOKEN,
        )
        assert with_token.status_code == 202
        wait_for(http, with_token.json()["status_url"])
    assert [item["mode"] for item in fake_investigate] == ["cached", "live"]


def test_investigation_record_hypotheses_and_findings(fake_investigate):
    with TestClient(create_app(Settings(root=ROOT))) as http:
        accepted = http.post(
            "/api/investigations",
            json={"target_id": "razorpay-vulnerable", "mode": "exhaustive", "reduce": True},
        ).json()
        record = wait_for(http, accepted["status_url"])
        assert record["record_type"] == "investigation"
        assert record["mode_effective"] == "exhaustive"
        investigation_id = accepted["investigation_id"]
        hypotheses = http.get(f"/api/investigations/{investigation_id}/hypotheses").json()
        assert hypotheses["rejected"] == 1 and hypotheses["usable"] == 0
        assert hypotheses["fallback_reason"] == "cache_miss"
        findings = http.get(f"/api/investigations/{investigation_id}/findings").json()
        assert findings["dna_ids"] == ["abc"]
        assert findings["verdict"] == "FAIL"
        index = http.get("/api/investigations").json()["items"]
        assert {item["investigation_id"] for item in index} == {investigation_id, "showcase-m2"}
        assert len(index) == 2
        assert index[0]["investigation_id"] == investigation_id
    assert fake_investigate[0]["reduce"] is True


def test_queued_record_is_readable_before_the_operation_finishes(monkeypatch):
    import asyncio

    release = asyncio.Event()

    async def slow(workspace, *, investigation_id=None, **_):
        await release.wait()

    monkeypatch.setattr(services, "investigate", slow)
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        accepted = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable"}).json()
        queued = http.get(accepted["status_url"]).json()
        assert queued["status"] == "queued"
        assert queued["mode_requested"] == "cached"
        # One operation at a time: a second start is refused while the first runs.
        busy = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable"})
        assert busy.status_code == 409
        http.portal.call(release.set)
        deadline = time.monotonic() + 5
        while app.state.gate.busy and time.monotonic() < deadline:
            time.sleep(0.02)
        assert app.state.gate.busy is False


def test_investigation_validation_errors(fake_investigate):
    with TestClient(create_app(Settings(root=ROOT))) as http:
        assert http.post("/api/investigations", json={"target_id": "nope"}).status_code == 404
        assert (
            http.post("/api/investigations", json={"target_id": "razorpay-vulnerable", "mode": "psychic"}).status_code
            == 422
        )
        assert http.get("/api/investigations/inv-missing").status_code == 404
        assert http.get("/api/investigations/bad.id").status_code == 404
        assert http.get("/api/investigations/inv-missing/findings").status_code == 404
    assert fake_investigate == []


def test_rate_limit_applies_to_investigations(fake_investigate):
    with TestClient(create_app(Settings(root=ROOT, per_ip_per_hour=1))) as http:
        first = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable"})
        assert first.status_code == 202
        wait_for(http, first.json()["status_url"])
        second = http.post("/api/investigations", json={"target_id": "razorpay-vulnerable"})
        assert second.status_code == 429


def _store_dna(home: Path, dna_artifact_like: dict) -> None:
    save_dna(home, dna_artifact_like)


def test_reduce_contract(isolated_home, monkeypatch):
    payload = {"dna_id": "dna0000000000001", "provenance": {"target_id": "razorpay-vulnerable"}}
    release = asyncio.Event()

    def fake_load(workspace, dna_id):
        if dna_id != payload["dna_id"]:
            raise FileNotFoundError(dna_id)

        class Document:
            provenance = payload["provenance"]

        return Document()

    async def fake_reduce(workspace, *, dna_id, target_id=None, black_box=False, reduction_id=None):
        await release.wait()
        atomic_write_json(
            workspace.home / "reductions" / f"{reduction_id}.json",
            {
                "record_type": "reduction",
                "reduction_id": reduction_id,
                "status": "completed",
                "outcome": "REDUCED",
                "original_dna_id": dna_id,
                "black_box_requested": black_box,
                "summary": {"claim": "no admissible reductions"},
            },
        )

    monkeypatch.setattr(services, "load_dna_document", fake_load)
    monkeypatch.setattr(services, "reduce_dna", fake_reduce)
    with TestClient(create_app(Settings(token="s3cret", root=ROOT))) as http:
        url = f"/api/dna/{payload['dna_id']}/reduce"
        assert http.post(url, json={}).status_code == 401
        assert http.post("/api/dna/unknown0000000/reduce", json={}, headers=TOKEN).status_code == 404
        assert (
            http.post(url, json={"target_id": "nope"}, headers=TOKEN).status_code == 404
        )
        accepted = http.post(url, json={"black_box": True}, headers=TOKEN)
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["status"] == "queued"
        assert body["target_id"] == "razorpay-vulnerable"
        assert body["status_url"] == f"/api/reductions/{body['reduction_id']}"
        running = wait_for_status(http, body["status_url"], "running")
        assert running["original_dna_id"] == payload["dna_id"]
        http.portal.call(release.set)
        record = wait_for(http, body["status_url"])
        assert record["outcome"] == "REDUCED"
        assert record["black_box_requested"] is True
        assert http.get("/api/reductions/red-missing").status_code == 404


def test_reduce_failure_is_recorded(monkeypatch):
    class Document:
        provenance = {"target_id": "razorpay-vulnerable"}

    async def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(services, "load_dna_document", lambda workspace, dna_id: Document())
    monkeypatch.setattr(services, "reduce_dna", broken)
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        body = http.post("/api/dna/dna0000000000001/reduce", json={}).json()
        record = wait_for(http, body["status_url"])
        assert record["status"] == "failed"
        assert "boom" in record["error"]
        assert app.state.gate.busy is False


def test_investigation_failure_is_recorded(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("investigation failed before its service record")

    monkeypatch.setattr(services, "investigate", broken)
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        accepted = http.post(
            "/api/investigations",
            json={"target_id": "razorpay-vulnerable", "mode": "cached"},
        )
        assert accepted.status_code == 202
        record = wait_for(http, accepted.json()["status_url"])
        assert record["status"] == "failed"
        assert "investigation failed before its service record" in record["error"]
        assert app.state.gate.busy is False


def test_differential_contract(monkeypatch):
    seen = {}

    async def fake_differential(workspace, *, target_ids=None, specs=None, policy="explore", differential_id=None):
        seen.update(target_ids=target_ids, specs=specs, policy=policy)
        atomic_write_json(
            workspace.home / "differentials" / f"{differential_id}.json",
            {"record_type": "differential", "differential_id": differential_id, "status": "completed", "cells": []},
        )

    monkeypatch.setattr(services, "differential", fake_differential)
    with TestClient(create_app(Settings(root=ROOT))) as http:
        assert http.post("/api/differential", json={"target_ids": ["nope"]}).status_code == 404
        assert (
            http.post("/api/differential", json={"scenarios": [{"template_id": "S7"}]}).status_code == 422
        )
        assert http.post("/api/differential", json={"policy": "forever"}).status_code == 422
        accepted = http.post(
            "/api/differential",
            json={"scenarios": [{"template_id": "S5", "params": {"fault": "rendezvous"}}], "policy": "explore"},
        )
        assert accepted.status_code == 202
        body = accepted.json()
        assert body["status"] == "queued"
        assert body["status_url"] == f"/api/differential/{body['differential_id']}"
        record = wait_for(http, body["status_url"])
        assert record["record_type"] == "differential"
        assert http.get("/api/differential/diff-missing").status_code == 404
    assert seen["policy"] == "explore"
    assert [spec.template_id for spec in seen["specs"]] == ["S5"]


def test_differential_failure_updates_the_accepted_record(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("differential failed before its service record")

    monkeypatch.setattr(services, "differential", broken)
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        accepted = http.post("/api/differential", json={})
        assert accepted.status_code == 202
        record = wait_for(http, accepted.json()["status_url"])
        assert record["status"] == "failed"
        assert "differential failed before its service record" in record["error"]
        assert app.state.gate.busy is False


def test_differential_requires_token_when_configured():
    with TestClient(create_app(Settings(token="s3cret", root=ROOT))) as http:
        assert http.post("/api/differential", json={}).status_code == 401
