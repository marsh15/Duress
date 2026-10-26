"""API authorization, admission control and real replay integration (M2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from duress.api import Settings, create_app
from duress.store import duress_home, save_dna

ROOT = Path(__file__).resolve().parents[2]


def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_health_and_targets_are_public():
    with client(Settings(root=ROOT)) as http:
        health = http.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["token_required"] is False
        targets = http.get("/api/targets").json()["targets"]
        ids = {item["target_id"] for item in targets}
        assert {"razorpay-vulnerable", "razorpay-fixed"} <= ids


def test_token_is_required_for_operations():
    with client(Settings(token="s3cret", root=ROOT)) as http:
        assert http.post("/api/dna/whatever/replay", json={}).status_code == 401
        wrong = http.post(
            "/api/dna/whatever/replay",
            json={},
            headers={"Authorization": "Bearer nope"},
        )
        assert wrong.status_code == 401
        # A correct token gets past authorization and fails on the artifact.
        ok = http.post(
            "/api/dna/whatever/replay",
            json={},
            headers={"X-Duress-Token": "s3cret"},
        )
        assert ok.status_code == 404


def test_unknown_dna_and_target():
    with client(Settings(root=ROOT)) as http:
        assert http.get("/api/dna/nope").status_code == 404
        assert (
            http.post("/api/dna/nope/replay", json={}).status_code == 404
        )


def test_operation_gate_returns_409():
    app = create_app(Settings(root=ROOT))
    app.state.gate.busy = True
    with TestClient(app) as http:
        response = http.post("/api/dna/nope/replay", json={})
    assert response.status_code == 409


def test_rate_limit_returns_429():
    with client(Settings(root=ROOT, per_ip_per_hour=0)) as http:
        response = http.post("/api/dna/nope/replay", json={})
    assert response.status_code == 429


def test_oversized_body_is_refused():
    with client(Settings(root=ROOT)) as http:
        response = http.post(
            "/api/dna/replay",
            content=b"x" * 1_000_001,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413


def test_real_replay_through_the_api(dna_artifact, monkeypatch, tmp_path):
    document, _ = dna_artifact
    monkeypatch.setenv("DURESS_HOME", str(tmp_path / ".duress"))
    save_dna(duress_home(tmp_path), document)
    with client(Settings(root=ROOT, per_ip_per_hour=100)) as http:
        response = http.post(
            f"/api/dna/{document.dna_id}/replay",
            json={"policy": "explore"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"]["outcome"] == "REPRODUCED"
    assert payload["outcome"]["valid"] >= 5


def test_tampered_payload_is_reported_as_rejected(dna_artifact):
    document, _ = dna_artifact
    payload = json.loads(json.dumps(document.model_dump(mode="json")))
    payload["reproducer"]["schedule"]["deliveries"][0]["event_id"] = "changed"
    with client(Settings(root=ROOT, per_ip_per_hour=100)) as http:
        response = http.post(
            "/api/dna/replay",
            json={"dna": payload, "target_id": "razorpay-vulnerable"},
        )
    assert response.status_code == 200
    assert response.json()["outcome"]["outcome"] == "REJECTED"
    assert response.json()["outcome"]["reason"] == "tampered:digest"