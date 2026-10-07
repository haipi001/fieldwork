import io
import json
import socket
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import v5_model_transport_child as child


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '::ffff:127.0.0.1'])
def test_cloud_dns_rejects_private_addresses_before_connect(monkeypatch, address):
    monkeypatch.setattr(child.socket, 'getaddrinfo', lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    monkeypatch.setattr(child.socket, 'create_connection', lambda *_: pytest.fail('must not connect'))
    with pytest.raises(ValueError, match='cloud_address_forbidden'):
        child.cloud_request(urlsplit('https://model.example.test/v1/chat/completions'), {}, 1, 'fixture-only')


@pytest.mark.parametrize('mode', ['success', 'untrusted_certificate', 'wrong_hostname', 'redirect'])
def test_cloud_tls_host_verification_pinned_connect_and_auth(tmp_path, monkeypatch, mode):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'model.example.test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([x509.DNSName('model.example.test')]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True,path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path/'cert.pem', tmp_path/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    calls, connections = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, self.headers.get('Authorization'), self.headers.get('Host'), body))
            self.send_response(307 if mode=='redirect' else 200)
            if mode=='redirect':self.send_header('Location','https://different.example.test/secret')
            self.send_header('Content-Length','2')
            self.end_headers();self.wfile.write(b'{}')
        def log_message(self,*_):pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert_path,key_path)
    server.socket = context.wrap_socket(server.socket,server_side=True)
    serving = threading.Thread(target=server.serve_forever,daemon=True);serving.start()
    monkeypatch.setattr(child.socket,'getaddrinfo',lambda *_args,**_kwargs:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    # Test-only numeric network shim. Production has no private-connection override.
    def numeric_connect(address,timeout):
        connections.append(address)
        connected=socket.socket(socket.AF_INET,socket.SOCK_STREAM);connected.settimeout(timeout)
        connected.connect(('127.0.0.1',server.server_port));return connected
    monkeypatch.setattr(child.socket,'create_connection',numeric_connect)
    monkeypatch.setattr(child, 'TRUST_BUNDLE', str(cert_path) if mode != 'untrusted_certificate' else None)
    host='wrong.example.test' if mode=='wrong_hostname' else 'model.example.test'
    try:
        if mode in {'untrusted_certificate','wrong_hostname'}:
            with pytest.raises(ssl.SSLCertVerificationError):
                child.cloud_request(urlsplit(f'https://{host}/v1/chat/completions'), {'model':'fixture'},2,'fixture-token')
            assert calls==[]
        elif mode=='redirect':
            with pytest.raises(ValueError,match='redirect_forbidden'):
                child.cloud_request(urlsplit(f'https://{host}/v1/chat/completions'),{},2,'fixture-token')
            assert len(calls)==1
        else:
            assert child.cloud_request(urlsplit(f'https://{host}/v1/chat/completions'),{'model':'fixture'},2,'fixture-token')==b'{}'
            assert calls==[('/v1/chat/completions','Bearer fixture-token','model.example.test',{'model':'fixture'})]
        assert connections==[('8.8.8.8',443)]
    finally:
        server.shutdown();server.server_close();serving.join(timeout=2)
