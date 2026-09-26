import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from native_ai_forwarder import LoopbackTransport, ImportConflict


@pytest.fixture
def server():
    state={'code':200,'requests':[],'receipt':{'sequence':1}}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):self.handle_request()
        def do_GET(self):self.handle_request()
        def handle_request(self):
            state['requests'].append((self.command,self.path))
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if body:state['body']=body
            self.send_response(state['code'])
            self.send_header('Location','http://127.0.0.1:1/private')
            self.end_headers()
            if state['code']==200:self.wfile.write(json.dumps(state['receipt']).encode())
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=threading.Thread(target=service.serve_forever,daemon=True);worker.start()
    yield LoopbackTransport(f'http://127.0.0.1:{service.server_port}'),state
    service.shutdown();service.server_close();worker.join()


@pytest.mark.parametrize('url',['https://127.0.0.1:8000','http://localhost:8000',
    'http://example.com:8000','http://127.0.0.1:8000/path','http://user@127.0.0.1:8000',
    'http://127.0.0.1:8000?next=remote','http://127.0.0.1:8000#x','http://127.0.0.1'])
def test_remote_or_ambiguous_destination_rejected(url):
    with pytest.raises(ValueError):LoopbackTransport(url)


def test_actual_loopback_post_and_receipt_ignore_proxy_environment(server,monkeypatch):
    transport,state=server
    monkeypatch.setenv('http_proxy','http://127.0.0.1:1')
    transport=LoopbackTransport(transport.base_url)
    assert transport.post('audit',b'{"fixture":true}') == {'acknowledged':True}
    assert state['body']==b'{"fixture":true}'
    assert transport.get_receipt('audit','collector',1)=={'sequence':1}
    assert state['requests'][-1][1].endswith('/collectors/collector/receipts/1')


def test_redirect_is_never_followed_and_error_types_are_explicit(server):
    transport,state=server
    state['code']=302
    with pytest.raises(ValueError,match='重定向'):transport.post('audit',b'{}')
    assert len(state['requests'])==1
    state['code']=409
    with pytest.raises(ImportConflict):transport.post('audit',b'{}')
    state['code']=503
    with pytest.raises(ConnectionError):transport.post('audit',b'{}')
    state['code']=404
    assert transport.get_receipt('audit','collector',1) is None


def test_path_injection_and_invalid_sequence_never_reach_socket(server):
    transport,state=server
    with pytest.raises(ValueError):transport.post('../other',b'{}')
    with pytest.raises(ValueError):transport.get_receipt('audit','collector',True)
    assert not state['requests']


def test_receipt_response_is_bounded(server):
    transport,state=server
    state['receipt']={'padding':'x'*65537}
    with pytest.raises(ValueError,match='大小上限'):transport.get_receipt('audit','collector',1)


@pytest.mark.parametrize('timeout',[0,31,True,float('nan'),float('inf')])
def test_timeout_must_be_finite_and_bounded(timeout):
    with pytest.raises(ValueError):LoopbackTransport(timeout=timeout)
