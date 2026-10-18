import asyncio
import copy
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web3_contract_import as service

ADDR = '0x' + '12' * 20
OTHER = '0x' + '34' * 20
URL = 'https://etherscan.io/address/' + ADDR + '?utm_source=immunefi'


def bundle():
    return {'provider': 'fixture', 'provider_verified': True, 'raw_sha256': 'ab' * 32,
            'sources': {'src/Counter.sol': {'content': 'pragma solidity 0.8.24; contract Counter { uint256 public count; function inc() external { count++; } }'}},
            'settings': {'optimizer': {'enabled': True, 'runs': 200}, 'evmVersion': 'paris'},
            'compiler_version': '0.8.24+commit.e11b9ed9', 'contract_name': 'Counter', 'abi': []}


@pytest.mark.parametrize('value,chain,expected', [(URL,None,1),(ADDR,1,1),('https://basescan.org/address/'+ADDR,None,8453),(ADDR,31337,31337)])
def test_parser(value,chain,expected):
    parsed=service.parse_target(value,chain)
    assert parsed['chain_id']==expected and parsed['address']==ADDR
    assert 'utm_' not in (parsed['explorer_url'] or '')


@pytest.mark.parametrize('value,chain,code', [(ADDR,None,'chain_required'),(URL,56,'chain_id_mismatch'),('https://evil.test/address/'+ADDR,1,'invalid_target'),('https://etherscan.io/tx/'+ADDR,1,'invalid_target'),('0x'+'zz'*20,1,'invalid_target'),('https://etherscan.io.evil.test/address/'+ADDR,1,'invalid_target'),('https://user:password@etherscan.io/address/'+ADDR,1,'invalid_target')])
def test_parser_rejects(value,chain,code):
    with pytest.raises(service.ImportFailure) as exc:service.parse_target(value,chain)
    assert exc.value.code==code


@pytest.mark.parametrize('url', [URL,'http://remote.test','https://key:secret@rpc.test'])
def test_rpc_validation(url):
    with pytest.raises(service.ImportFailure):service.validate_rpc(url)


@pytest.mark.parametrize('name', ['../x.sol','/x.sol','a/../../x.sol','a\\x.sol','a//x.sol','a/./x.sol','C:x.sol','x\n.sol'])
def test_source_paths(name):
    with pytest.raises(service.ImportFailure):service.safe_sources({name:{'content':'x'}})


def test_source_limits_and_collisions(monkeypatch):
    for sources in [{'X.sol':'x','x.sol':'y'},{'a':'x','a/b.sol':'y'},{'x.sol':{'urls':['https://evil']}}]:
        with pytest.raises(service.ImportFailure):service.safe_sources(sources)
    monkeypatch.setattr(service,'MAX_BYTES',2)
    with pytest.raises(service.ImportFailure):service.safe_sources({'x.sol':'abcd'})
    monkeypatch.setattr(service,'MAX_FILES',1)
    with pytest.raises(service.ImportFailure):service.safe_sources({'x':'','y':''})


def test_materialize_preserves_input_and_rejects_links(tmp_path):
    target=service.parse_target(ADDR,1);b=bundle()
    root,manifest=service.materialize(tmp_path/'imports',target,b)
    assert json.loads((root/'standard-input.json').read_text())['sources']==b['sources']
    assert (root/'sources/src/Counter.sol').read_text()==b['sources']['src/Counter.sol']['content']
    assert manifest['files']['src/Counter.sol']['sha256']
    (root/'sources/src/Counter.sol').unlink();(root/'sources/src/Counter.sol').symlink_to(tmp_path/'sentinel')
    with pytest.raises((service.ImportFailure,OSError)):service.materialize(tmp_path/'imports',target,b)
    assert not (tmp_path/'sentinel').exists()


def test_unsafe_remapping(tmp_path):
    b=bundle();b['settings']['remappings']=['x/=../../secrets/']
    with pytest.raises(service.ImportFailure):service.materialize(tmp_path,service.parse_target(ADDR,1),b)


@pytest.mark.parametrize('wrapping', ['single','standard','double','string','multi'])
def test_etherscan_formats(wrapping):
    item={'SourceCode':'contract C {}','ContractName':'C','CompilerVersion':'v0.8.24+commit.e11b9ed9','OptimizationUsed':'1','Runs':'100','ABI':'[]'}
    standard={'language':'Solidity','sources':{'src/C.sol':{'content':'contract C {}'}},'settings':{'viaIR':True,'optimizer':{'enabled':True,'runs':1000}}}
    if wrapping=='standard':item['SourceCode']=json.dumps(standard)
    if wrapping=='double':item['SourceCode']='{'+json.dumps(standard)+'}'
    if wrapping=='string':item['SourceCode']=json.dumps(json.dumps(standard))
    if wrapping=='multi':item['SourceCode']=json.dumps(standard['sources'])
    got=service.parse_etherscan({'status':'1','result':[item]})
    assert got['provider']=='etherscan_v2' and got['abi']==[] and len(got['sources'])==1
    if wrapping in {'standard','double','string'}:assert got['settings']['viaIR']


@pytest.mark.parametrize('payload', [{'status':'0','result':'Max rate limit reached'},{'status':'1','result':[]},{'status':'1','result':[{'SourceCode':''}]}])
def test_etherscan_failures(payload):
    with pytest.raises(service.ImportFailure):service.parse_etherscan(payload)


def test_sourcify_v2_selective_and_fallback(monkeypatch):
    calls=[]
    def fetch(url,params=None):
        calls.append((url,params))
        b=bundle()
        return ({'runtimeMatch':'exact_match'} if params is None else {'runtimeMatch':'exact_match','chainId':'1','address':ADDR,'compilation':{'language':'Solidity','compilerVersion':b['compiler_version'],'name':'Counter','compilerSettings':b['settings']},'sources':b['sources'],'abi':[]}), 'ab'*32
    monkeypatch.setattr(service,'fetch_json',fetch)
    assert service.sourcify(service.parse_target(ADDR,1))['provider_verified']
    assert len(calls)==2 and '/v2/contract/' in calls[0][0] and calls[0][1] is None and calls[1][1]['fields']!='all'
    monkeypatch.delenv('ETHERSCAN_API_KEY',raising=False)
    monkeypatch.setattr(service,'sourcify',lambda t: (_ for _ in ()).throw(service.ImportFailure('provider_unavailable','v2 unavailable')))
    with pytest.raises(service.ImportFailure) as exc:service.retrieve(service.parse_target(ADDR,1))
    assert 'ETHERSCAN_API_KEY' in exc.value.message


def test_cache_and_cancellation(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(service,'retrieve',lambda t: calls.append(t) or bundle())
    a=service.import_contract(ADDR,1,'source',base=tmp_path)
    b=service.import_contract(ADDR,1,'source',base=tmp_path)
    assert a['source_path']==b['source_path'] and b['cache_hit'] and len(calls)==1
    service.import_contract(ADDR,1,'source',base=tmp_path,refresh=True)
    assert len(calls)==2
    c=service.import_contract(ADDR,1,'source',base=tmp_path,cancelled=lambda:True)
    assert c['status']=='cancelled'


def test_failed_build_and_timeout(tmp_path,monkeypatch):
    monkeypatch.setattr(service,'retrieve',lambda t:bundle())
    monkeypatch.setattr(service,'build_project',lambda r:{'status':'failed','reason':'timeout','output':'compiler timeout'})
    got=service.import_contract(ADDR,1,'build',base=tmp_path)
    assert got['status']=='compile_failed' and got['compile']['reason']=='timeout' and Path(got['source_path']).is_dir()
    monkeypatch.setattr(service,'build_project',lambda r:(_ for _ in ()).throw(OSError('disk full')))
    assert service.import_contract(ADDR,1,'build',base=tmp_path)['error']['code']=='local_failure'


def test_matcher_evidence_and_real_mismatch():
    assert service.match_runtime('0x6001','0x6001')['status']=='exact_match'
    assert service.match_runtime('0x6001','0x6002')['status']=='mismatch'
    local='0x60'+'00'*32+'00';chain='0x60'+'12'*32+'00'
    result=service.match_runtime(local,chain,{'42':[{'start':1,'length':32}]})
    assert result['status']=='explained_equivalence' and result['masked_bytes']==32 and result['policy'].startswith('manual_review')
    assert service.match_runtime(local,chain,{'42':[{'start':1,'length':1000}]})['status']=='unknown'
    assert service.match_runtime(local,chain,{}, {'x':{'L':[{'start':1,'length':32}]}})['status']=='mismatch'
    def code(hashbyte):return '0x6000a264697066735822'+hashbyte*34+'64736f6c63430008180033'
    result=service.match_runtime(code('11'),code('22'))
    assert result['status']=='explained_equivalence' and result['masked_ranges'][0]['basis'].startswith('complete_solidity_cbor')
    assert service.match_runtime('0x6000120002','0x6000340002')['status']=='mismatch'


@pytest.mark.parametrize('kind', ['direct','eip1967','beacon','eip1167','cycle','noncontract','wrongchain','unknown'])
def test_proxy_resolution(kind,monkeypatch):
    calls=[]
    def rpc(endpoint,method,params):
        calls.append((method,params))
        if method=='eth_chainId':return '0x2' if kind=='wrongchain' else '0x1'
        if method=='eth_blockNumber':return '0x2a'
        if method=='eth_getCode':
            if kind=='noncontract':return '0x'
            if kind=='unknown':return '0x60fff4'
            if kind=='eip1167' and params[0]==ADDR:return '0x363d3d373d3d3d363d73'+OTHER[2:]+'5af43d82803e903d91602b57fd5bf3'
            return '0x6000'
        if method=='eth_getStorageAt':
            if params[0]==ADDR and ((kind in {'eip1967','cycle'} and params[1]==service.IMPLEMENTATION_SLOT) or (kind=='beacon' and params[1]==service.BEACON_SLOT)):
                return '0x'+'00'*12+(ADDR if kind=='cycle' else OTHER)[2:]
            return '0x'+'00'*32
        if method=='eth_call':return '0x'+'00'*12+OTHER[2:]
        raise AssertionError(method)
    monkeypatch.setattr(service,'rpc_call',rpc)
    if kind in {'cycle','noncontract','wrongchain'}:
        with pytest.raises(service.ImportFailure):service.resolve_proxy(service.parse_target(ADDR,1),'https://rpc.test/secret')
    else:
        result=service.resolve_proxy(service.parse_target(ADDR,1),'https://rpc.test/secret')
        assert result['block_number']==42
        assert result['comparison_address']==(OTHER if kind in {'eip1967','beacon','eip1167'} else ADDR)
        assert 'secret' not in json.dumps(result)
        assert result['proxy_status']==('unknown_proxy_pattern' if kind=='unknown' else 'resolved')
    assert all(m in {'eth_chainId','eth_blockNumber','eth_getCode','eth_getStorageAt','eth_call'} for m,p in calls)
    assert all(p[-1]=='0x2a' for m,p in calls if m in {'eth_getCode','eth_getStorageAt','eth_call'})


def test_real_forge_standard_json_offline(tmp_path,monkeypatch):
    from web3_lab import binary
    if not binary('forge'):pytest.skip('forge unavailable')
    monkeypatch.setattr(service,'retrieve',lambda t:bundle())
    result=service.import_contract(ADDR,31337,'build',base=tmp_path)
    assert result['status']=='chain_unchecked',result
    root=Path(result['source_path'])
    output=json.loads((root/'standard-output.json').read_text())
    runtime=output['contracts']['src/Counter.sol']['Counter']['evm']['deployedBytecode']['object']
    resolution={'runtime':'0x'+runtime,'proxy_status':'resolved','chain_id':31337,'block_number':42,'contract_address':ADDR,'comparison_address':ADDR}
    assert service.align_project(root,resolution)['status']=='aligned'
    resolution['runtime']='0x00'+runtime[2:]
    assert service.align_project(root,resolution)['status']=='alignment_blocked'
    # Compilation cache actually skips both compilers.
    monkeypatch.setattr(service,'build_project',lambda r:pytest.fail('compiled cache was not reused'))
    assert service.import_contract(ADDR,31337,'build',base=tmp_path)['compile']['status']=='compiled'


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app, final_core, web3_import_api
    database=tmp_path/'test.db'
    monkeypatch.setattr(app,'DB',database)
    monkeypatch.setattr(app,'DATA',tmp_path)
    monkeypatch.setattr(final_core,'DB',database)
    monkeypatch.setattr(final_core,'LOCAL_DATA_ROOT',tmp_path)
    monkeypatch.setattr(web3_import_api,'data_directory',lambda:tmp_path)
    monkeypatch.setattr(service,'retrieve',lambda t:bundle())
    with TestClient(app.app,base_url='http://127.0.0.1:8000') as c:yield c


def create_project(client,target=URL):
    response=client.post('/api/v1/engagements',json={'name':'Import offline acceptance','target':target,'mode':'web3','chain_id':1,'scope':{},'policy':{}})
    assert response.status_code==201,response.text
    return response.json()


def wait_job(client,identity):
    for _ in range(200):
        result=client.get('/api/v1/web3/contract-imports/'+identity)
        assert result.status_code==200,result.text
        value=result.json()
        if value.get('finished_at') or value['status']=='interrupted':return value
        time.sleep(.01)
    pytest.fail('job timeout')


def test_api_target_scope_idempotency_attach_restart(client):
    import web3_import_api
    engagement=create_project(client)
    assert engagement['target_type']=='contract' and engagement['normalized_target']==ADDR and engagement['chain_id']==1
    payload={'engagement_id':engagement['id'],'address':URL,'mode':'source'}
    a=client.post('/api/v1/web3/contract-imports',json=payload)
    assert a.status_code==202,a.text
    value=wait_job(client,a.json()['import_id'])
    assert value['status']=='source_ready'
    again=client.post('/api/v1/web3/contract-imports',json=payload).json()
    assert again['import_id']==value['import_id'] and again['finished_at']==value['finished_at']
    wrong=client.post('/api/v1/web3/contract-imports',json={**payload,'address':OTHER})
    assert wrong.status_code==409
    attached=client.post('/api/v1/web3/contract-imports/'+value['import_id']+'/attach',json={'engagement_id':engagement['id']})
    assert attached.status_code==200 and attached.json()['authorization_granted'] is False
    other=create_project(client,OTHER)
    assert client.post('/api/v1/web3/contract-imports/'+value['import_id']+'/attach',json={'engagement_id':other['id']}).status_code==409
    value['status']='compile_running';value.pop('finished_at',None);web3_import_api.save_job(value['import_id'],value)
    restarted=client.get('/api/v1/web3/contract-imports/'+value['import_id']).json()
    assert restarted['status']=='interrupted'
    assert client.post('/api/v1/web3/contract-imports/'+value['import_id']+'/retry',json=payload).status_code==202
    assert wait_job(client,value['import_id'])['status']=='source_ready'


def test_api_session_and_rpc_secrets(client,monkeypatch):
    import session_auth,web3_import_api
    engagement=create_project(client)
    payload={'engagement_id':engagement['id'],'address':ADDR,'mode':'align','rpc_url':'https://rpc.test/VERY_SECRET_KEY'}
    monkeypatch.setattr(service,'resolve_proxy',lambda *a,**k:(_ for _ in ()).throw(service.ImportFailure('rpc_unavailable','JSON-RPC 请求失败')))
    got=client.post('/api/v1/web3/contract-imports',json=payload)
    assert got.status_code==202,got.text
    value=wait_job(client,got.json()['import_id'])
    assert 'VERY_SECRET_KEY' not in json.dumps(value)
    assert 'VERY_SECRET_KEY' not in web3_import_api.job_path(value['import_id']).read_text()
    assert client.post('/api/v1/web3/contract-imports',json={**payload,'rpc_url':URL}).status_code==422
    import app
    middleware=app.app.middleware_stack
    while not isinstance(middleware,session_auth.SessionAuthMiddleware):middleware=middleware.app
    monkeypatch.setattr(middleware,'allow_test_bypass',False)
    token=middleware.token
    assert client.get('/api/v1/web3/contract-imports/'+value['import_id']).status_code==401
    assert client.get('/api/v1/web3/contract-imports/'+value['import_id'],headers={'x-fieldwork-session':token}).status_code==200
    assert client.get('/api/v1/web3/contract-imports/'+value['import_id'],headers={'Origin':'https://evil.test','x-fieldwork-session':token}).status_code==403


def test_read_only_run_uses_budget_and_evidence_without_fork(client,monkeypatch):
    import final_core, web3_lab
    engagement=create_project(client)
    value=client.post('/api/v1/web3/contract-imports',json={'engagement_id':engagement['id'],'address':URL,'mode':'source'}).json()
    value=wait_job(client,value['import_id'])
    payload={'execution_mode':'read_only','contract_import_id':value['import_id']}
    assert not client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan",json=payload).json()['ready']
    assert client.post(f"/api/v1/engagements/{engagement['id']}/confirm").status_code==200
    monkeypatch.setattr(web3_lab,'start_rpc_fork',lambda *a,**kw:pytest.fail('read-only must never fork'))
    plan=client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan",json=payload).json()
    assert plan['ready'] and plan['tools']==[] and plan['actions']==['read','analyze'], plan
    run=client.post(f"/api/v1/engagements/{engagement['id']}/start",json=payload)
    assert run.status_code==202,run.text
    details=client.get(f"/api/v1/runs/{run.json()['id']}/details").json()
    items={item['id']:item for item in details['test_items']}
    assert set(items)=={'contract-materials','forge-build','forge-test'}
    assert items['contract-materials']['status']=='completed'
    assert items['forge-build']['status']==items['forge-test']['status']=='not_tested'
    assert next(item for item in details['stages'] if item['id']=='analysis')['status']=='not_tested'
    with final_core.connect() as db:
        assert db.execute('SELECT status FROM analysis_runs WHERE id=?',(run.json()['id'],)).fetchone()['status']=='completed'
        assert db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(run.json()['id'],)).fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM observations WHERE run_id=?',(run.json()['id'],)).fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM artifacts WHERE run_id=?',(run.json()['id'],)).fetchone()[0]==1
        artifact_path=db.execute('SELECT uri FROM artifacts WHERE run_id=?',(run.json()['id'],)).fetchone()[0]
        assert json.loads(Path(artifact_path).read_text())['source_analysis']['contracts']
        assert db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0]==0
    other=create_project(client,OTHER)
    assert client.post(f"/api/v1/engagements/{other['id']}/confirm").status_code==200
    assert client.post(f"/api/v1/engagements/{other['id']}/start",json=payload).status_code==409


def test_provider_network_failure_and_no_v1(monkeypatch):
    import httpx
    class Client:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def stream(self,*args,**kw):raise httpx.ConnectTimeout('URL contains SECRET')
    monkeypatch.setattr(httpx,'Client',Client)
    with pytest.raises(service.ImportFailure) as exc:service.fetch_json('https://sourcify.dev/server/v2/contract/1/'+ADDR)
    assert 'SECRET' not in exc.value.message and exc.value.code=='provider_unavailable'


def test_compiler_download_checksum_fails_closed(tmp_path,monkeypatch):
    import httpx
    monkeypatch.setattr(service.Path,'home',classmethod(lambda cls:tmp_path))
    name='solc-macosx-amd64-v0.8.24+commit.e11b9ed9'
    monkeypatch.setattr(service,'fetch_json',lambda *a,**k:({'releases':{'0.8.24':name},'builds':[{'path':name,'longVersion':'0.8.24+commit.e11b9ed9','sha256':'00'*32}]},''))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_bytes(self):yield b'untrusted executable'
    class Client(Response):
        def __init__(self,**kw):pass
        def stream(self,*a):return Response()
    monkeypatch.setattr(httpx,'Client',Client)
    with pytest.raises(service.ImportFailure) as exc:service.ensure_compiler('0.8.24+commit.e11b9ed9')
    assert exc.value.code=='compiler_checksum_mismatch'
    assert not (tmp_path/'.solc-select/artifacts/solc-0.8.24/solc-0.8.24').exists()


def test_open_project_restricts_directory(client,monkeypatch,tmp_path):
    import web3_import_api
    engagement=create_project(client)
    result=client.post('/api/v1/web3/contract-imports',json={'engagement_id':engagement['id'],'address':ADDR,'mode':'source'}).json()
    value=wait_job(client,result['import_id'])
    commands=[]
    import subprocess
    monkeypatch.setattr(subprocess,'run',lambda argv,**kw:commands.append(argv))
    opened=client.post('/api/v1/web3/contract-imports/'+value['import_id']+'/open',json={})
    assert opened.status_code==200 and commands[0][0]=='/usr/bin/open'
    value['source_path']=str(tmp_path)
    web3_import_api.save_job(value['import_id'],value)
    assert client.post('/api/v1/web3/contract-imports/'+value['import_id']+'/open',json={}).status_code==409
    assert len(commands)==1


def test_actual_sandbox_disk_limit(tmp_path,monkeypatch):
    import isolated_execution,sys
    (tmp_path/'writer.py').write_text("from pathlib import Path\nimport time\nPath('out').mkdir()\nPath('out/too-large').write_bytes(b'x'*2000000)\ntime.sleep(2)\n")
    monkeypatch.setattr(isolated_execution,'MAX_SNAPSHOT_BYTES',1000000)
    result=isolated_execution.run_isolated([sys.executable,'writer.py'],tmp_path,timeout=10,export_dirs=('out',))
    assert result.status=='resource_limit' and result.returncode is None
    assert not (tmp_path/'out').exists()


def test_actual_sandbox_timeout(tmp_path):
    import isolated_execution,sys
    (tmp_path/'sleeper.py').write_text('import time\ntime.sleep(5)\n')
    result=isolated_execution.run_isolated([sys.executable,'sleeper.py'],tmp_path,timeout=1)
    assert result.status=='timeout' and result.returncode is None


def test_auto_mode_selects_configured_rpc_and_restores_job(client,monkeypatch):
    engagement=create_project(client)
    assert client.get('/api/v1/web3/contract-imports/engagements/'+engagement['id']+'/latest').json()=={'import':None}
    monkeypatch.setenv('ETHEREUM_RPC_URL','https://rpc.test/SECRET_AUTO')
    monkeypatch.setattr(service,'resolve_proxy',lambda *a,**k:(_ for _ in ()).throw(service.ImportFailure('rpc_unavailable','RPC unavailable')))
    monkeypatch.setattr(service,'build_project',lambda r:{'status':'compiled','output':''})
    value=client.post('/api/v1/web3/contract-imports',json={'engagement_id':engagement['id'],'address':ADDR,'mode':'auto'}).json()
    final=wait_job(client,value['import_id'])
    assert final['request']['mode']=='align' and final['request']['rpc_env']=='ETHEREUM_RPC_URL'
    latest=client.get('/api/v1/web3/contract-imports/engagements/'+engagement['id']+'/latest').json()['import']
    assert latest['import_id']==value['import_id'] and 'SECRET_AUTO' not in json.dumps(latest)


def test_job_read_waits_for_terminal_save_and_task_cleanup(client, monkeypatch):
    import threading
    import web3_import_api as api
    identity='import-'+'a'*32
    initial={'request':{},'status':'source_ready','finished_at':None}
    monkeypatch.setattr(api,'scoped',lambda body:None)
    # A valid request is still parsed, while target access is isolated here.
    engagement=create_project(client)
    initial['request']={'engagement_id':engagement['id'],'address':URL,'mode':'source'}
    api.save_job(identity,initial)
    entered,done=threading.Event(),threading.Event()
    results=[]
    def read():
        entered.set()
        try: results.append(api.read_job(identity))
        finally: done.set()
    worker=threading.Thread(target=read)
    with api.LOCK:
        api.TASKS[identity]=object()
        worker.start()
        assert entered.wait(2)
        try:
            assert not done.wait(.1), 'reader bypassed terminal-state lock'
            api.save_job(identity,{**initial,'finished_at':'fixture-finished'})
            api.TASKS.pop(identity)
        finally:
            api.TASKS.pop(identity,None)
    worker.join(timeout=2)
    assert not worker.is_alive() and len(results)==1
    assert results[0]['status']=='source_ready' and results[0]['finished_at']=='fixture-finished'
    assert api.read_job(identity)['finished_at']=='fixture-finished'


@pytest.mark.parametrize('status',['failed','cancelled','interrupted','unverified_source'])
def test_read_only_rejects_finished_but_unusable_import(client,status):
    import web3_import_api as api
    engagement=create_project(client)
    value=client.post('/api/v1/web3/contract-imports',json={'engagement_id':engagement['id'],'address':URL,'mode':'source'}).json()
    value=wait_job(client,value['import_id'])
    assert value.get('finished_at') and value.get('source_path')
    api.save_job(value['import_id'],{**value,'status':status})
    client.post(f"/api/v1/engagements/{engagement['id']}/confirm")
    body={'execution_mode':'read_only','contract_import_id':value['import_id']}
    plan=client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan",json=body).json()
    assert not plan['ready'] and any(item['id']=='import' for item in plan['blockers'])
    assert client.post(f"/api/v1/engagements/{engagement['id']}/start",json=body).status_code==409


def test_read_only_rechecks_source_directory_before_run_creation(client):
    import web3_import_api as api
    engagement=create_project(client)
    value=client.post('/api/v1/web3/contract-imports',json={'engagement_id':engagement['id'],'address':URL,'mode':'source'}).json()
    value=wait_job(client,value['import_id'])
    api.save_job(value['import_id'],{**value,'source_path':str(Path(value['source_path']).parent/'missing-source')})
    client.post(f"/api/v1/engagements/{engagement['id']}/confirm")
    response=client.post(f"/api/v1/engagements/{engagement['id']}/start",json={'execution_mode':'read_only','contract_import_id':value['import_id']})
    assert response.status_code==409
    import final_core
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM analysis_runs WHERE engagement_id=?',(engagement['id'],)).fetchone()[0]==0
