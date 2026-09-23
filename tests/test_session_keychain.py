import json
import os
import uuid
from types import SimpleNamespace
import pytest
import session_capture as capture
import traditional_runtime as http
from tests.test_final import client,create_ready


def test_secret_only_sent_through_pipe(monkeypatch):
    monkeypatch.setattr(capture.Path,'is_file',lambda _:True)
    calls=[]
    def command(argv,**kwargs):
        calls.append((argv,kwargs))
        return SimpleNamespace(returncode=0,stdout='')
    monkeypatch.setattr(capture.subprocess,'run',command)
    capture.store_keychain('fixture-account',{'Cookie':'fixture-secret'})
    assert len(calls[0][0])==1 and 'fixture-secret' not in str(calls[0][0])
    payload=json.loads(calls[0][1]['input'])
    assert payload['operation']=='store' and payload['headers']['Cookie']=='fixture-secret'
    assert calls[0][1]['capture_output'] and calls[0][1]['timeout']==8


def test_native_errors_do_not_expose_payload(monkeypatch):
    monkeypatch.setattr(capture,'_keychain_command',lambda *args:SimpleNamespace(returncode=1,stdout='private',stderr='private'))
    with pytest.raises(RuntimeError,match='^keychain_store_failed$'):capture.store_keychain('fixture',{'Cookie':'private'})
    with pytest.raises(RuntimeError,match='^keychain_session_unavailable$'):capture.read_keychain('fixture')


@pytest.mark.skipif(os.getenv('FIELDWORK_TEST_KEYCHAIN')!='1',reason='explicit native Keychain fixture test')
def test_native_roundtrip_used_by_http_verifier(client):
    account='fieldwork-qa-'+uuid.uuid4().hex
    headers={'Cookie':'fixture=nonsecret','X-Fixture':'quote-"-slash-\\-中文'}
    try:
        reference=capture.store_keychain(account,headers)
        project=create_ready(client)
        identity=client.post(f"/api/v1/engagements/{project['id']}/identities",json={'label':'Native fixture','role':'user','auth_type':'keychain_reference','session_status':'ready','credential_ref':reference}).json()
        assert http.resolve_identity_headers(identity['id'])==headers
        headers['Cookie']='fixture=updated'
        capture.store_keychain(account,headers)
        assert http.resolve_identity_headers(identity['id'])==headers
    finally:
        assert capture.delete_keychain(account)
        assert not capture.delete_keychain(account)
