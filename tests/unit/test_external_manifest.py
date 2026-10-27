import pytest
from pydantic import ValidationError
from duress.external.manifest import Delivery, Manifest, Scenario, Target


def scenario(**kwargs):
    return Scenario(id='normal', deliveries=[{'id':'one','fixture':'paid','event_id':'e1'}],
                    steps=[{'deliveries':['one']}], **kwargs)


def test_schedule_is_bounded_and_fully_dispatched():
    assert scenario().resolve_attempts(scenario().deliveries[0])[:2] == (5000, 1)
    for policy in ({'max_attempts':4}, {'attempt_timeout_ms':10001},
                   {'max_attempts':3,'attempt_timeout_ms':10000}):
        with pytest.raises(ValidationError):
            scenario(delivery_policy=policy)
    with pytest.raises(ValidationError):
        Scenario(id='bad',deliveries=[{'id':'one','fixture':'paid','event_id':'e'}],
                 steps=[{'deliveries':['missing']}])


def test_staging_requires_explicit_exact_origin():
    with pytest.raises(ValidationError):
        Target(id='staging',mode='staging',base_url='https://example.test')
    valid = dict(id='staging',mode='staging',staging_authorized=True,
                 base_url='https://example.test',allowed_origins=['https://example.test'])
    assert Target(**valid).mode == 'staging'
    for value in ('https://user:pass@example.test','https://example.test.evil',
                  'https://example.test/private','https://example.test?token=secret'):
        with pytest.raises(ValidationError):
            Target(**{**valid,'base_url':value})


def test_no_unsafe_endpoint_or_framing_headers():
    for args in ({'endpoint':'//example.test'}, {'headers':{'Host':'example.test'}},
                 {'headers':{'X-Token':'bad\r\nInjected: true'}}, {'signature':'unknown'}):
        with pytest.raises(ValidationError):
            Delivery(**args)


def test_managed_commands_and_environment_are_explicit():
    assert Target(id='local',init=['python','init.py'],serve=['python','app.py']).mode == 'managed'
    for args in ({'init':[]}, {'env':{'DURESS_SECRET':'oops'}}, {'env_refs':{'TOKEN':'bad-name'}}):
        with pytest.raises(ValidationError):
            Target(**{**dict(id='local',init=['python','init.py'],serve=['python','app.py']),**args})
