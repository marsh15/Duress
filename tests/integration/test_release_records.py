"""All asynchronous record readers share the same corruption/path boundary."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from duress.api import create_app, Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("kind,api,page", [
    ("reductions", "/api/reductions/broken", "/reductions/broken"),
    ("differentials", "/api/differential/broken", "/differential/broken"),
    ("investigations", "/api/investigations/broken", "/investigations/broken"),
    ("dna", "/api/dna/broken", "/dna/broken"),
])
@pytest.mark.parametrize("payload", [b"{", b"[]", b"null", b"\xff"])
def test_corrupt_records_are_unavailable_to_both_api_and_pages(tmp_path, monkeypatch, kind, api, page, payload):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    directory = tmp_path / kind
    directory.mkdir()
    (directory / "broken.json").write_bytes(payload)
    with TestClient(create_app(Settings(root=ROOT))) as client:
        assert client.get(api).status_code == 404
        assert client.get(page).status_code == 404
        if kind == "dna":
            assert client.get("/api/dna").status_code == 200
            assert client.get("/dna").status_code == 200
            for operation in ("replay", "verify-fix", "reduce"):
                response = client.post(f"/api/dna/broken/{operation}", json={})
                assert response.status_code == 404
            assert not list((tmp_path / "run").glob("*.pid"))


@pytest.mark.parametrize("kind,api", [
    ("reductions", "/api/reductions/linked"),
    ("differentials", "/api/differential/linked"),
    ("dna", "/api/dna/linked"),
])
def test_analysis_readers_refuse_symlink_records(tmp_path, monkeypatch, kind, api):
    monkeypatch.setenv("DURESS_HOME", str(tmp_path))
    directory = tmp_path / kind
    directory.mkdir()
    outside = tmp_path / "private.json"
    outside.write_text('{"private":"must not be returned"}')
    (directory / "linked.json").symlink_to(outside)
    with TestClient(create_app(Settings(root=ROOT))) as client:
        assert client.get(api).status_code == 404
