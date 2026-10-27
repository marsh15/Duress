from pathlib import Path
import json
import pytest
from duress.external.manifest import load_project
from duress.external.services import trial

APP = '''import json,os,signal,sqlite3,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
path=os.environ['DURESS_DB']
if sys.argv[1]=='init':
 c=sqlite3.connect(path); c.execute('create table effects(id integer primary key, entity text)'); c.commit(); sys.exit(0)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({'instance':os.environ['DURESS_INSTANCE']}).encode())
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  c=sqlite3.connect(path); c.execute('insert into effects(entity) values(?)',(body['entity'],)); c.commit(); c.close()
  self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
signal.signal(signal.SIGTERM,lambda *args:sys.exit(0))
HTTPServer(('127.0.0.1',int(os.environ['DURESS_PORT'])),Handler).serve_forever()
'''

@pytest.fixture
def external_project(tmp_path):
    (tmp_path/'app.py').write_text(APP)
    (tmp_path/'fixtures.json').write_text(json.dumps({'events':{'paid':{'raw_json':'{ "entity": "owned" }\\n'.replace('\\n','\n')}}}))
    (tmp_path/'contract.json').write_text(json.dumps({'id':'effects-v1','version':1,'entities':{
        'effects':{'table':'effects','key':'id','columns':['id','entity']}},'invariants':[
        {'id':'unique-effect','kind':'unique_by','entity':'effects','keys':['entity']},
        {'id':'effect-exists','kind':'expected_effect','entity':'effects'}]}))
    (tmp_path/'duress.toml').write_text('''[duress]
project="external-test"
[target]
id="local-test"
init=["python","app.py","init"]
serve=["python","app.py","serve"]
[observation]
adapter="sqlite@1"
[fixtures]
file="fixtures.json"
[contract]
file="contract.json"
[[scenarios]]
id="normal"
deliveries=[{id="one",fixture="paid",event_id="e1"}]
steps=[{deliveries=["one"]}]
[scenarios.context]
expected_effects=[{entity="effects",match={entity="owned"},min_delta=1,max_delta=1}]
''')
    return tmp_path/'duress.toml'

@pytest.mark.asyncio
async def test_external_sqlite_trial_uses_real_process_http_and_settled_state(external_project,tmp_path):
    project=load_project(external_project)
    result=await trial(project,project.scenario('normal'),tmp_path/'runtime')
    assert result['verdict']['status']=='PASS',result
    assert result['after']['entities']['effects']==[{'id':1,'entity':'owned'}]
    assert result['execution']['settlement']=='clean'
    assert not list((tmp_path/'runtime'/'run').glob('*.pid'))
    assert not list((tmp_path/'runtime').glob('duress-*'))

@pytest.mark.asyncio
async def test_missing_business_evidence_is_inconclusive(external_project,tmp_path):
    project=load_project(external_project)
    project.contract.entities['effects'].table='missing'
    result=await trial(project,project.scenario('normal'),tmp_path/'runtime')
    assert result['verdict']['status']=='INCONCLUSIVE'
    assert not result['after']['available']

@pytest.mark.asyncio
async def test_failure_dna_is_exact_portable_and_rejects_changed_rules(external_project,tmp_path,monkeypatch):
    import copy
    from duress.external.artifacts import build_dna,preflight,replay
    from duress.external.manifest import ExternalDelivery
    from duress.external.services import run_series
    from duress.external.storage import ProjectStore
    from duress.limits import Policy
    monkeypatch.setenv('DURESS_HOME',str(tmp_path/'store'))
    project=load_project(external_project)
    scenario=project.scenario('normal')
    scenario.deliveries.append(ExternalDelivery(id='second',fixture='paid',event_id='e1'))
    scenario.steps.append(type(scenario.steps[0])(deliveries=['second']))
    result=await run_series(project,scenario,tmp_path/'runtime',Policy('test',2,3))
    assert result['result']['status']=='FAIL',result
    dna=build_dna(project,scenario,result)
    assert preflight(dna,project)==scenario
    store=ProjectStore(project.manifest.duress.project)
    store.save('dna',dna,dna['dna_id'])
    assert store.load('dna',dna['dna_id'])['payload']==dna
    tampered=copy.deepcopy(dna); tampered['reproducer']['adapters']['dispatcher']='exec@999'
    with pytest.raises(ValueError,match='integrity'):
        preflight(tampered,project)
    changed=copy.deepcopy(dna); changed['characterization']['p0']=0.2
    with pytest.raises(ValueError,match='metadata'):
        preflight(changed,project)
    original_read=Path.read_bytes
    for module in ('target.py','aggregation.py','store.py'):
        with monkeypatch.context() as patch:
            def altered(path, name=module):
                content=original_read(path)
                return content+b"\n# incompatible engine implementation\n" if path.name==name else content
            patch.setattr(Path,'read_bytes',altered)
            with pytest.raises(ValueError,match='implementation_sha256'):
                preflight(dna,project)
    project.contract.invariants[0].max_count=2
    with pytest.raises(ValueError,match='contract'):
        preflight(dna,project)

@pytest.mark.parametrize('raw',[False,True])
def test_literal_fixture_secrets_are_refused_before_any_process(external_project,raw):
    value={'entity':'owned','access_token':'literal-unreferenced-credential'}
    fixture={'raw_json':json.dumps(value)} if raw else {'payload':value}
    (external_project.parent/'fixtures.json').write_text(json.dumps({'events':{'paid':fixture}}))
    with pytest.raises(ValueError,match='credential'):
        load_project(external_project)

@pytest.mark.asyncio
async def test_init_timeout_kills_and_confirms_forked_child(external_project,tmp_path):
    import os
    from duress.external.lifecycle import ExternalSupervisor
    from duress.limits import DEFAULT_LIMITS
    from duress.store import group_exists
    app=external_project.parent/'app.py'
    app.write_text('import os,time\nif os.fork()==0: time.sleep(30)\nelse: time.sleep(30)\n')
    project=load_project(external_project)
    supervisor=ExternalSupervisor(project,project.scenario('normal'),tmp_path/'runtime')
    supervisor.limits=DEFAULT_LIMITS.with_phase('prepare',.2)
    observation=await supervisor.run()
    assert 'prepare_timeout' in observation.harness_errors
    assert not group_exists(supervisor.init_pgid)
    assert not list((tmp_path/'runtime'/'run').glob('*.pid'))

@pytest.mark.asyncio
async def test_acknowledgements_use_the_bound_delivery_not_another_success(external_project,tmp_path):
    from duress.external.manifest import Scenario
    source=(external_project.parent/'app.py').read_text()
    source=source.replace('  c=sqlite3.connect(path); c.execute(\'insert', '  if self.headers.get("X-Event-Id")=="e1":\n   self.send_response(409); self.end_headers(); return\n  c=sqlite3.connect(path); c.execute(\'insert')
    (external_project.parent/'app.py').write_text(source)
    contract=json.loads((external_project.parent/'contract.json').read_text())
    contract['invariants'].append({'id':'ack','kind':'acknowledgement_consistency'})
    (external_project.parent/'contract.json').write_text(json.dumps(contract))
    project=load_project(external_project)
    data=project.scenario('normal').model_dump(mode='json')
    data['deliveries'].append({'id':'two','fixture':'paid','event_id':'e2'})
    data['steps']=[{'deliveries':['one','two']}]
    data['expected_status']={'one':'4xx','two':'2xx'}
    data['acknowledgements']={'one':{'intent':'create','entity':'effects','match':{'entity':'owned'}}}
    result=await trial(project,Scenario.model_validate(data),tmp_path/'runtime')
    assert result['verdict']['status']=='PASS',result['verdict']
    assert next(r for r in result['invariants'] if r['id']=='ack')['reason']=='no_success_acknowledgement'

@pytest.mark.asyncio
async def test_expected_status_uses_terminal_response_after_retries(external_project,tmp_path):
    from duress.external.manifest import Scenario
    source=(external_project.parent/'app.py').read_text().replace('class Handler','counter=0\nclass Handler')
    source=source.replace(' def do_POST(self):\n',' def do_POST(self):\n  global counter\n  counter+=1\n')
    source=source.replace('self.send_response(200); self.end_headers(); self.wfile.write(b\'{}\')',
                          'self.send_response(409 if counter==1 else 200); self.end_headers(); self.wfile.write(b\'{}\')')
    (external_project.parent/'app.py').write_text(source)
    project=load_project(external_project)
    data=project.scenario('normal').model_dump(mode='json')
    data['delivery_policy']={'max_attempts':2,'retry_on':['response']}
    data['expected_status']={'one':'4xx'}
    result=await trial(project,Scenario.model_validate(data),tmp_path/'runtime')
    assert [a['status_code'] for a in result['execution']['attempts']]==[409,200]
    assert 'unexpected_response:one' in result['verdict']['reasons']

@pytest.mark.asyncio
async def test_managed_delivery_ignores_ambient_http_proxy(external_project,tmp_path,monkeypatch):
    for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):
        monkeypatch.setenv(name,'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY','');monkeypatch.setenv('no_proxy','')
    project=load_project(external_project)
    result=await trial(project,project.scenario('normal'),tmp_path/'runtime')
    assert result['verdict']['status']=='PASS',result['verdict']

@pytest.mark.asyncio
async def test_init_pidfile_write_failure_still_reaps_process_group(external_project,tmp_path,monkeypatch):
    from duress.external import lifecycle
    from duress.store import group_exists
    app=external_project.parent/'app.py'
    app.write_text('import os,time\nif os.fork()==0: time.sleep(30)\nelse: time.sleep(30)\n')
    project=load_project(external_project)
    supervisor=lifecycle.ExternalSupervisor(project,project.scenario('normal'),tmp_path/'runtime')
    def fail(*args,**kwargs):raise OSError('simulated metadata write failure')
    monkeypatch.setattr(lifecycle,'write_pidfile',fail)
    observation=await supervisor.run()
    assert observation.harness_errors
    assert not group_exists(supervisor.init_pgid)
