"""Backend routes and upstream workbench share one record/admission contract."""
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from duress import api
from duress.models import ReplayOutcome

ROOT = Path(__file__).resolve().parents[2]
DNA_ID = "e8007dd56adae8ae"


def test_only_one_investigation_route_set_is_registered():
    app = api.create_app(api.Settings(root=ROOT))
    pairs = Counter(
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or [])
    )
    for pair in [
        ("GET", "/api/investigations"), ("POST", "/api/investigations"),
        ("GET", "/api/investigations/{investigation_id}"),
        ("GET", "/api/operations/{operation_id}"),
    ]:
        assert pairs[pair] == 1
    assert all(count == 1 for count in pairs.values())


def test_replay_keeps_its_payload_and_adds_a_readable_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))

    async def replay(store, *, dna_id, target_id, policy):
        return SimpleNamespace(
            outcome=ReplayOutcome(outcome="REJECTED", reason="tampered:digest", dna_id=dna_id, target_id=target_id),
            preflight={"ok": False, "reason": "tampered:digest"},
        )

    monkeypatch.setattr(api, "replay", replay)
    with TestClient(api.create_app(api.Settings(root=ROOT))) as http:
        response = http.post(f"/api/dna/{DNA_ID}/replay", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["outcome"]["outcome"] == "REJECTED"
        assert body["preflight"]["reason"] == "tampered:digest"
        saved = http.get(f"/api/operations/{body['operation_id']}")
        assert saved.status_code == 200
        assert saved.json()["payload"] == {"outcome": body["outcome"], "preflight": body["preflight"]}
        assert http.get(f"/operations/{body['operation_id']}").status_code == 200


def test_write_routes_share_one_gate_and_busy_refusals_preserve_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    app = api.create_app(api.Settings(root=ROOT, per_ip_per_hour=1))
    app.state.gate.busy = True
    with TestClient(app) as http:
        for url, body in [
            ("/api/investigations", {"target_id": "razorpay-vulnerable"}),
            (f"/api/dna/{DNA_ID}/replay", {}),
            (f"/api/dna/{DNA_ID}/reduce", {}),
            ("/api/differential", {}),
        ]:
            assert http.post(url, json=body).status_code == 409
        assert app.state.gate.busy
        assert all(not entries for entries in app.state.rate.hour.values())


def test_invalid_local_record_cannot_fall_back_to_the_showcase(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    directory = tmp_path / "investigations"
    directory.mkdir()
    (directory / "showcase-m2.json").symlink_to(ROOT / "showcase" / "investigation.json")
    with TestClient(api.create_app(api.Settings(root=ROOT))) as http:
        assert http.get("/api/investigations/showcase-m2").status_code == 404
        assert http.get("/investigations/showcase-m2").status_code == 404
