from pathlib import Path
from typer.testing import CliRunner
from duress.cli import app
from duress.external.onboarding import initialize
from duress.external.manifest import load_project


def test_init_doctor_and_targets_are_usable_outside_checkout(tmp_path):
    runner=CliRunner()
    result=runner.invoke(app,['init',str(tmp_path/'owned'),'--project','owned-example'])
    assert result.exit_code==0,result.output
    config=tmp_path/'owned'/'duress.toml'
    assert load_project(config).manifest.duress.project=='owned-example'
    assert runner.invoke(app,['doctor','--config',str(config),'--json']).exit_code==0
    assert runner.invoke(app,['targets','--config',str(config),'--json']).exit_code==0
    refused=runner.invoke(app,['init',str(tmp_path/'owned'),'--project','owned-example'])
    assert refused.exit_code==3 and 'overwrite' in refused.output


def test_hosted_external_execution_and_registration_are_disabled(tmp_path,monkeypatch):
    config=initialize(tmp_path/'owned','owned-example')
    monkeypatch.setenv('DURESS_HOSTED','true')
    runner=CliRunner()
    for args in (['check','--config',str(config)],['register','--config',str(config)]):
        result=runner.invoke(app,args)
        assert result.exit_code==3,result.output
        assert 'hosted' in result.output
