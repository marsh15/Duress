from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

from duress.external.onboarding import initialize
from duress.external.storage import ProjectStore, register_project
from duress.api import OperationGate
from duress.external import web


def _client(
    *,
    home: Path,
    token: str | None = "local-token",
    hosted: bool = False,
    remote: tuple[str, int] = ("127.0.0.1", 43123),
) -> TestClient:
    settings = SimpleNamespace(hosted=hosted)
    gate = OperationGate()

    def authorize(x_duress_token: str | None = Header(default=None)) -> None:
        if token is not None and x_duress_token != token:
            raise HTTPException(status_code=401, detail="token_required")

    def begin_operation(request: Request) -> None:
        if not gate.acquire():
            raise HTTPException(status_code=409, detail="operation_in_progress")

    app = FastAPI()
    web.install_external(app, settings, authorize, begin_operation, gate)
    return TestClient(
        app,
        base_url="http://localhost:8000",
        client=remote,
        headers={"Host": "localhost:8000"},
    )


def _registered_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    config = initialize(tmp_path / "owned-integration", "ticket-service")
    home = tmp_path / "duress-state"
    monkeypatch.setenv("DURESS_HOME", str(home))
    register_project(config)
    return config, home


def _evidence_record() -> dict:
    snapshot = {
        "entities": {"activations": [{"id": 1, "resource": "resource-a"}]},
        "available": True,
        "valid": True,
        "settled": True,
        "provenance": {"adapter": "sqlite@1"},
        "error": None,
    }
    return {
        "schema_version": 1,
        "investigation_id": "run-001",
        "project": "ticket-service",
        "target_id": "ticket-service-local",
        "adapter": "external@1",
        "contract_id": "resource-activation-v1",
        "contract_version": 1,
        "status": "completed",
        "verdict": "PASS",
        "scenarios": [{
            "scenario_id": "duplicate-activation",
            "schedule": {"deliveries": [{"id": "first"}]},
            "result": {"status": "PASS", "valid": 1, "fail": 0, "inconclusive": 0, "trials": 1},
            "trials": [{
                "trial_id": "trial-001",
                "before": {**snapshot, "entities": {"activations": []}},
                "after": snapshot,
                "execution": {"attempts": [{"delivery_id": "first", "attempt": 1,
                                             "outcome": "response", "status_code": 200,
                                             "duration_ms": 8}]},
                "invariants": [{"id": "requested-activation-exists", "version": 1,
                                "status": "satisfied", "reason": "expected_effect_observed",
                                "identities": [{"id": 1}],
                                "explanation": "One activation was observed."}],
                "verdict": {"status": "PASS", "reasons": []},
            }],
        }],
        "dna": [],
    }


def test_registered_project_read_journey_shows_safe_details_and_real_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = _registered_project(tmp_path, monkeypatch)
    ProjectStore("ticket-service").save("investigations", _evidence_record(), "run-001")
    client = _client(home=tmp_path)

    index = client.get("/api/projects")
    assert index.status_code == 200
    assert index.json()["projects"][0]["project"] == "ticket-service"
    assert str(config) not in index.text
    assert "init" not in index.text
    assert "env_refs" not in index.text

    details = client.get("/api/projects/ticket-service")
    assert details.status_code == 200
    project = details.json()
    assert project["contract"] == {"id": "resource-activation-v1", "version": 1}
    assert project["adapters"]["delivery"] == "http-json@1"
    assert project["scenarios"][0]["id"] == "duplicate-activation"
    assert "config_path" not in project

    listing = client.get("/projects")
    page = client.get("/projects/ticket-service")
    evidence = client.get("/projects/ticket-service/investigations/run-001")
    report = client.get("/projects/ticket-service/investigations/run-001/report")
    assert listing.status_code == page.status_code == evidence.status_code == report.status_code == 200
    assert "Local configuration stays in the terminal" in listing.text
    assert "duplicate-activation" in page.text
    assert "Run read-only doctor" in page.text
    assert "requested-activation-exists@1" in evidence.text
    assert "resource-a" in evidence.text
    assert "No result proves universal correctness" in evidence.text
    assert "Recorded business-state investigation" in report.text

    api_record = client.get("/api/projects/ticket-service/investigations/run-001")
    assert api_record.status_code == 200
    assert api_record.json()["payload"]["scenarios"][0]["trials"][0]["after"]["entities"]["activations"][0]["resource"] == "resource-a"
    assert "owner" not in api_record.json()


def test_mutation_requires_existing_authorization_and_same_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registered_project(tmp_path, monkeypatch)
    client = _client(home=tmp_path)
    endpoint = "/api/projects/ticket-service/investigations"

    unauthorized = client.post(endpoint, json={"scenario_id": "duplicate-activation"},
                               headers={"Origin": "http://localhost:8000"})
    cross_origin = client.post(endpoint, json={"scenario_id": "duplicate-activation"},
                               headers={"X-Duress-Token": "local-token", "Origin": "http://attacker.test"})
    assert unauthorized.status_code == 401
    assert cross_origin.status_code == 403
    assert cross_origin.json()["detail"] == "same_origin_required"


@pytest.mark.parametrize("hosted,environment", [(True, None), (False, "1")])
def test_execution_is_rejected_when_hosted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hosted: bool, environment: str | None,
) -> None:
    _registered_project(tmp_path, monkeypatch)
    if environment is None:
        monkeypatch.delenv("DURESS_HOSTED", raising=False)
    else:
        monkeypatch.setenv("DURESS_HOSTED", environment)
    client = _client(home=tmp_path, hosted=hosted)
    response = client.post(
        "/api/projects/ticket-service/investigations",
        json={"scenario_id": "duplicate-activation"},
        headers={"X-Duress-Token": "local-token", "Origin": "http://localhost:8000"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "external_execution_disabled"
    assert ProjectStore("ticket-service").list("operations") == []


def test_external_routes_reject_non_loopback_client_and_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registered_project(tmp_path, monkeypatch)
    remote_client = _client(home=tmp_path, remote=("192.0.2.4", 43123))
    bad_host_client = _client(home=tmp_path)

    remote = remote_client.get("/api/projects")
    bad_host = bad_host_client.get("/api/projects", headers={"Host": "duress.attacker.test"})
    assert remote.status_code == 403
    assert bad_host.status_code == 403
    assert remote.json()["detail"] == "local_only"


def test_investigation_input_accepts_only_registered_scenario_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _registered_project(tmp_path, monkeypatch)
    client = _client(home=tmp_path)
    headers = {"X-Duress-Token": "local-token", "Origin": "http://localhost:8000"}

    unknown_scenario = client.post(
        "/api/projects/ticket-service/investigations",
        json={"scenario_id": "caller-chosen"}, headers=headers,
    )
    path_input = client.post(
        "/api/projects/ticket-service/investigations",
        json={"scenario_id": "duplicate-activation", "config_path": "/tmp/other.toml"}, headers=headers,
    )
    doctor_path_input = client.post(
        "/api/projects/ticket-service/doctor",
        json={"command": ["/bin/sh", "-c", "echo unsafe"]}, headers=headers,
    )
    assert unknown_scenario.status_code == 404
    assert unknown_scenario.json()["detail"] == "unknown_scenario"
    assert path_input.status_code == 422
    assert doctor_path_input.status_code == 422
    assert ProjectStore("ticket-service").list("operations") == []


def test_ui_investigation_submission_persists_and_polls_with_mock_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UI contract test: service execution is mocked; no target is launched."""
    _registered_project(tmp_path, monkeypatch)

    async def fake_investigate(config: Path, *, scenario_id: str | None = None):
        return {
            "investigation_id": "i1234567890abcdef",
            "project": "ticket-service",
            "target_id": "ticket-service-local",
            "status": "completed",
            "verdict": "PASS",
            "scenarios": [{"scenario_id": scenario_id or "duplicate-activation"}],
            "dna": [],
        }

    monkeypatch.setattr(web, "investigate", fake_investigate)
    client = _client(home=tmp_path)
    response = client.post(
        "/api/projects/ticket-service/investigations",
        json={"scenario_id": "duplicate-activation"},
        headers={"X-Duress-Token": "local-token", "Origin": "http://localhost:8000"},
    )
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "queued"
    assert ProjectStore("ticket-service").load("operations", accepted["operation_id"])["status"] == "completed"

    poll = client.get(accepted["poll_url"])
    assert poll.status_code == 200
    assert poll.json()["payload"]["status"] == "completed"
    assert poll.json()["payload"]["investigation_id"] == "i1234567890abcdef"
    assert "config_path" not in json.dumps(poll.json())
    assert client.get(accepted["operation_url"]).status_code == 200
