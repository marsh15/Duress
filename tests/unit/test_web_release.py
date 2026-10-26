"""Presentation boundaries for retained evidence and malformed artifacts."""
from pathlib import Path
import json
import sqlite3

import pytest

from tests.unit.test_web import _client, _dna, _m3_investigation


def test_evidence_reads_only_the_retained_snapshot_and_escapes_values(tmp_path):
    client = _client(tmp_path)
    directory = tmp_path / ".duress/evidence/trial-1/snapshot"
    directory.mkdir(parents=True)
    path = directory / "app.db"
    with sqlite3.connect(path) as connection:
        for table in ("orders", "payments", "fulfilments"):
            connection.execute(f"CREATE TABLE {table} (id TEXT, status TEXT)")
        connection.execute("INSERT INTO orders VALUES (?, ?)", ("order:A", "<script>alert(1)</script>"))
    before = path.read_bytes()
    response = client.get("/evidence/trial-1")
    assert response.status_code == 200
    assert "order:A" in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text
    assert path.read_bytes() == before
    assert client.get("/evidence/missing").status_code == 404
    # A live working database alone must never be presented as settled state.
    other = tmp_path / ".duress/evidence/live"
    other.mkdir()
    (other / "app.db").write_bytes(before)
    assert client.get("/evidence/live").status_code == 404


def test_evidence_refuses_symlink_and_corrupt_database(tmp_path):
    client = _client(tmp_path)
    directory = tmp_path / ".duress/evidence/corrupt/snapshot"
    directory.mkdir(parents=True)
    (directory / "app.db").write_text("not sqlite")
    assert client.get("/evidence/corrupt").status_code == 404
    (tmp_path / ".duress/evidence/link").symlink_to(directory.parent, target_is_directory=True)
    assert client.get("/evidence/link").status_code == 404


def test_rejected_hypothesis_cannot_be_presented_as_confirmed(tmp_path):
    client = _client(tmp_path)
    record = _m3_investigation()
    record["hypotheses"][0]["usable"] = False
    record["hypotheses"][0]["status"]["citation"] = "invalid"
    (tmp_path / ".duress/investigations/run-01.json").write_text(json.dumps(record))
    response = client.get("/investigations/run-01")
    assert response.status_code == 200
    assert "rejected from execution" in response.text
    assert "Confirmed finding" not in response.text
    assert "0 confirmed" in response.text


def test_malformed_source_bundle_is_unavailable_instead_of_crashing(tmp_path):
    client = _client(tmp_path)
    record = _m3_investigation()
    record["bundle"] = {"files": 3}
    (tmp_path / ".duress/investigations/run-01.json").write_text(json.dumps(record))
    assert client.get("/investigations/run-01").status_code == 404


@pytest.mark.parametrize("schedule", [None, [], {"deliveries": None}, {"steps": [None]},
                                        {"faults": "bad"}, {"delivery_policy": []}])
def test_malformed_nested_dna_is_unavailable_instead_of_crashing(tmp_path, schedule):
    client = _client(tmp_path)
    document = _dna()
    document["reproducer"]["schedule"] = schedule
    (tmp_path / ".duress/dna/dna-001.json").write_text(json.dumps(document))
    assert client.get("/dna/dna-001").status_code == 404
    assert client.get("/dna").status_code == 200
