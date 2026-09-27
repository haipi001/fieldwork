import base64
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
import native_ai_control as control


def request(command,**changes):
    value={'version':1,'command':command}
    if command=='start':value.update(audit_id='audit',collector_id='collector',session_id='session',public_key=base64.b64encode(b'k'*32).decode())
    value.update(changes)
    return json.dumps(value).encode()


class Service:
    def __init__(self):self.session=self;self.starts=0;self.stops=0;self.fail_stop=False;self.status='inactive'
    def health(self):return {'status':self.status,'producer_alive':self.status=='running','private_key':'never expose','resource':'private fixture'}
    def start(self):self.starts+=1;self.status='running';return self.health()
    def close(self):
        self.stops+=1
        if self.fail_stop:raise OSError('private fixture error')
        self.status='stopped';return self.health()
    def tick(self):return self.health()


@pytest.fixture
def service(monkeypatch):
    prepared=[]
    def prepare(**options):
        item=Service();prepared.append((item,options));return item
    monkeypatch.setattr(control,'prepare',prepare)
    return control.NativeControl(app_uid=501,team_id='ABCDEFGHIJ'),prepared


def test_only_authorized_os_account_can_control(service):
    agent,prepared=service
    for uid in (0,502,True,'501',None):
        assert agent.handle(request('start'),peer_uid=uid)['error_code']=='account_not_authorized'
    assert not prepared


@pytest.mark.parametrize('raw',[b'',b'x'*8193,b'[]',b'{"version":1,"version":1,"command":"status"}',
    request('status',path='/tmp'),request('start',team_id='OTHERTEAM1'),request('start',audit_id='../escape'),
    request('start',public_key='bad'),request('start',session_id='bad\n'),request('shell'),request('status',version=True)])
def test_invalid_commands_never_reach_runtime(service,raw):
    agent,prepared=service
    assert agent.handle(raw,peer_uid=501)=={'ok':False,'error_code':'invalid_request'}
    assert not prepared


def test_start_stop_and_status_are_bounded_and_idempotent(service):
    agent,prepared=service
    result=agent.handle(request('start'),peer_uid=501)
    assert result=={'ok':True,'status':'running','producer_alive':True}
    assert agent.handle(request('start'),peer_uid=501)['idempotent']
    assert len(prepared)==1 and prepared[0][0].starts==1
    assert prepared[0][1]['team_id']=='ABCDEFGHIJ'
    assert agent.handle(request('start',audit_id='other'),peer_uid=501)['error_code']=='another_pairing_active'
    assert 'private fixture' not in json.dumps(agent.handle(request('status'),peer_uid=501))
    assert agent.handle(request('stop'),peer_uid=501)['status']=='stopped'
    assert agent.handle(request('stop'),peer_uid=501)['idempotent']
    assert prepared[0][0].stops==1 and agent.runtime is None


def test_stop_failure_preserves_runtime_for_retry(service):
    agent,prepared=service;agent.handle(request('start'),peer_uid=501)
    instance=prepared[0][0];instance.fail_stop=True
    assert agent.handle(request('stop'),peer_uid=501)['error_code']=='stop_persistence_pending'
    assert agent.runtime is instance
    instance.fail_stop=False
    assert agent.handle(request('stop'),peer_uid=501)['ok'] and agent.runtime is None


def test_concurrent_start_launches_only_once(service):
    agent,prepared=service
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:agent.handle(request('start'),peer_uid=501),range(8)))
    assert all(item['ok'] for item in results) and len(prepared)==1
    assert prepared[0][0].starts==1


def test_preparation_error_is_not_exposed(service,monkeypatch):
    agent,prepared=service
    def failed(**args):raise ValueError('private details')
    monkeypatch.setattr(control,'prepare',failed)
    result=agent.handle(request('start'),peer_uid=501)
    assert result['status']=='unavailable' and result['error_code']=='preparation_failed'
    assert 'private details' not in json.dumps(result) and agent.runtime is None


def test_unavailable_start_remains_unavailable_on_retry(service,monkeypatch):
    agent,prepared=service
    def unavailable(self):self.status='unavailable';return self.health()
    monkeypatch.setattr(Service,'start',unavailable)
    assert not agent.handle(request('start'),peer_uid=501)['ok']
    result=agent.handle(request('start'),peer_uid=501)
    assert not result['ok'] and result['status']=='unavailable' and result['idempotent']
    assert len(prepared)==1


def test_start_exception_closes_prepared_runtime(service,monkeypatch):
    agent,prepared=service
    def failed(self):raise OSError('private start details')
    monkeypatch.setattr(Service,'start',failed)
    result=agent.handle(request('start'),peer_uid=501)
    assert result['error_code']=='start_failed' and agent.runtime is None
    assert prepared[0][0].stops==1


def test_failed_start_cleanup_is_visible_and_retry_does_not_claim_success(service,monkeypatch):
    agent,prepared=service
    def failed(self):self.fail_stop=True;raise OSError('private start details')
    monkeypatch.setattr(Service,'start',failed)
    result=agent.handle(request('start'),peer_uid=501)
    assert result['error_code']=='start_cleanup_pending' and agent.runtime is not None
    assert not agent.handle(request('start'),peer_uid=501)['ok']
    assert agent.handle(request('status'),peer_uid=501)['control_error_code']=='start_cleanup_pending'
    prepared[0][0].fail_stop=False
    agent.handle(request('stop'),peer_uid=501)
    assert 'control_error_code' not in agent.handle(request('status'),peer_uid=501)
