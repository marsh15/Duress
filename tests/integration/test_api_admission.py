"""Admission refusal and scenario validation must precede work."""
from pathlib import Path

from fastapi.testclient import TestClient
from duress import services
from duress.api import Settings, create_app

ROOT = Path(__file__).resolve().parents[2]


def test_busy_refusals_do_not_spend_the_start_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))

    async def investigate(*args, **kwargs):
        return {}

    monkeypatch.setattr(services, "investigate", investigate)
    app = create_app(Settings(root=ROOT, per_ip_per_hour=1))
    app.state.gate.busy = True
    with TestClient(app) as http:
        body = {"target_id": "razorpay-vulnerable"}
        assert http.post("/api/investigations", json=body).status_code == 409
        app.state.gate.release()
        assert http.post("/api/investigations", json=body).status_code == 202


def test_invalid_differential_scenario_is_refused_before_admission(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        response = http.post("/api/differential", json={
            "scenarios": [{"template_id": "S3", "params": {"copies": 1}}],
        })
        assert response.status_code == 422
        assert app.state.gate.busy is False
        assert not list((tmp_path / "differentials").glob("*.json"))


def test_explicit_empty_differential_lists_are_invalid():
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        assert http.post("/api/differential", json={"target_ids": []}).status_code == 422
        assert http.post("/api/differential", json={"scenarios": []}).status_code == 422


def test_reduction_cannot_disable_the_required_characterization():
    with TestClient(create_app(Settings(root=ROOT))) as http:
        assert http.post("/api/dna/abc123/reduce", json={"black_box": False}).status_code == 422
