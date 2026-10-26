from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from typer.testing import CliRunner

from duress.records import (
    read_record, ensure_showcase, investigation_index, load_investigation_record,
    load_operation_record, save_operation_record,
)
from duress.cli import app


@pytest.fixture
def workspace(tmp_path):
    return SimpleNamespace(root=tmp_path, home=tmp_path / 'home')


def test_record_ids_cannot_escape_directory_and_symlinks_are_rejected(workspace):
    directory = workspace.root / 'records'
    directory.mkdir()
    secret = workspace.root / 'secret.json'
    secret.write_text('{"secret":"private"}')
    (directory / 'linked.json').symlink_to(secret)
    for record_id in ['../secret', 'linked', '', 'x'*81]:
        with pytest.raises(FileNotFoundError):
            read_record(directory, record_id)


def test_showcase_does_not_replace_workspace_artifact(workspace):
    source = workspace.root / 'showcase/dna'
    source.mkdir(parents=True)
    (source / 'artifact.json').write_text('{"dna_id":"artifact","marker":"bundled"}')
    ensure_showcase(workspace)
    destination = workspace.home / 'dna/artifact.json'
    assert json.loads(destination.read_text())['marker'] == 'bundled'
    destination.write_text('{"marker":"workspace"}')
    ensure_showcase(workspace)
    assert json.loads(destination.read_text()) == {'marker': 'workspace'}


def test_history_handles_empty_corrupt_and_recorded_data(workspace):
    assert investigation_index(workspace) == []
    directory = workspace.home / 'investigations'
    directory.mkdir(parents=True)
    (directory / 'broken.json').write_text('{')
    (directory / 'run1.json').write_text('{"investigation_id":"run1","status":"completed"}')
    assert [x['investigation_id'] for x in investigation_index(workspace)] == ['run1']
    assert load_investigation_record(workspace, 'run1')['status'] == 'completed'


def test_saved_operation_is_exact_service_response(workspace):
    payload = {'outcome': {'outcome': 'INCONCLUSIVE', 'reason': 'trial_timeout'}}
    record_id = save_operation_record(workspace, kind='replay', payload=payload, dna_id='abc')
    saved = load_operation_record(workspace, record_id)
    assert saved['payload'] == payload
    assert saved['kind'] == 'replay'
    assert saved['dna_id'] == 'abc'


def test_report_cli_exports_without_executing_backend(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    output = tmp_path / 'report.html'
    result = CliRunner().invoke(app, ['report', 'showcase', '--output', str(output)])
    assert result.exit_code == 0, result.output
    text = output.read_text()
    assert 'showcase-m2' in text
    assert 'Characterization summary' in text
    assert 'not a live or autonomous investigation' in text
    assert '<style>' in text
    assert 'src="/web/' not in text
    assert CliRunner().invoke(app, ['report', 'missing']).exit_code == 3
