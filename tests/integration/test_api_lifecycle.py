"""Accepted background operations terminate on cancellation and restart."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from duress import services
from duress.api import Settings, create_app
from duress.store import atomic_write_json

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("operation", ["investigation", "reduction", "differential"])
def test_shutdown_cancellation_leaves_a_terminal_record(operation, tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))

    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(services, "investigate", blocked)
    monkeypatch.setattr(services, "reduce_dna", blocked)
    monkeypatch.setattr(services, "differential", blocked)
    monkeypatch.setattr(services, "load_dna_document", lambda *_: SimpleNamespace(provenance={}))
    endpoint = {
        "investigation": "/api/investigations",
        "reduction": "/api/dna/abc123/reduce",
        "differential": "/api/differential",
    }[operation]
    body = {"target_id": "razorpay-vulnerable"} if operation == "investigation" else {}
    app = create_app(Settings(root=ROOT))
    with TestClient(app) as http:
        response = http.post(endpoint, json=body)
        assert response.status_code == 202
        accepted = response.json()
        assert http.get(accepted["status_url"]).status_code == 200
        assert app.state.gate.busy
    directory = {"investigation": "investigations", "reduction": "reductions", "differential": "differentials"}[operation]
    record = json.loads(next((tmp_path / directory).glob("*.json")).read_text())
    assert record["status"] == "failed"
    assert "CancelledError" in record["error"]
    assert app.state.gate.busy is False
    assert app.state.backend_tasks == set()


def test_startup_marks_stale_operations_failed_without_resuming_them(tmp_path, monkeypatch):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    for directory, record_type in [("investigations", "investigation"), ("reductions", "reduction"), ("differentials", "differential")]:
        for status in ("queued", "running", "completed"):
            atomic_write_json(tmp_path / directory / f"old-{status}.json", {
                "record_type": record_type, "status": status,
            })
    with TestClient(create_app(Settings(root=ROOT))):
        pass
    for path in tmp_path.glob("*/*.json"):
        if path.parent.name not in {"investigations", "reductions", "differentials"}:
            continue
        record = json.loads(path.read_text())
        if path.stem == "old-completed":
            assert record["status"] == "completed"
        else:
            assert record["status"] == "failed"
            assert record["error"] == "interrupted_by_restart"
