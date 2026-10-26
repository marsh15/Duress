"""Conventional record browsing; execution stays behind the existing API."""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from duress.api import Settings, create_app
from duress.records import save_operation_record

ROOT = Path(__file__).resolve().parents[2]


def test_bundled_record_and_dna_are_available_from_empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv('DURESS_HOME', str(tmp_path))
    with TestClient(create_app(Settings(root=ROOT))) as http:
        history = http.get('/api/investigations').json()
        assert history['creation_available'] is False
        assert any(x['investigation_id'] == 'showcase-m2' and x['showcase'] for x in history['items'])
        record = http.get('/api/investigations/showcase-m2').json()
        assert record['hypotheses'] == []
        assert record['recorded_summary']['valid'] == 10
        dna_id = record['dna_ids'][0]
        assert http.get('/api/dna/' + dna_id).json()['dna_id'] == dna_id
        # No autonomous feature is advertised as an executable route.
        assert http.post('/api/investigations', json={'target_id':'razorpay-vulnerable','mode':'cached'}).status_code == 405
        assert http.post('/api/dna/' + dna_id + '/reduce', json={}).status_code == 404


def test_operation_result_is_readable_and_unknown_ids_are_404(tmp_path, monkeypatch):
    monkeypatch.setenv('DURESS_HOME', str(tmp_path))
    record_id = save_operation_record(SimpleNamespace(home=tmp_path), kind='replay',
                                     payload={'outcome':{'outcome':'REJECTED','reason':'tampered:digest'}})
    with TestClient(create_app(Settings(root=ROOT))) as http:
        result = http.get('/api/operations/' + record_id)
        assert result.status_code == 200
        assert result.json()['payload']['outcome']['reason'] == 'tampered:digest'
        assert http.get('/api/operations/missing').status_code == 404
        assert http.get('/api/investigations/missing').status_code == 404


def test_readonly_pages_do_not_acquire_operation_gate(tmp_path, monkeypatch):
    monkeypatch.setenv('DURESS_HOME', str(tmp_path))
    app = create_app(Settings(root=ROOT, token='test-token'))
    app.state.gate.busy = True
    with TestClient(app) as http:
        assert http.get('/').status_code == 200
        assert http.get('/dna/e8007dd56adae8ae').status_code == 200
        assert http.get('/api/investigations').status_code == 200
        assert app.state.gate.busy is True
