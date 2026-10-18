import ssl
import urllib.error

import certifi
import v5_runtime


def test_health_uses_packaged_ca_and_keeps_verification(monkeypatch):
    contexts = []
    original = ssl.create_default_context

    def context(*args, **kwargs):
        assert kwargs['cafile'] == certifi.where()
        result = original(*args, **kwargs)
        contexts.append(result)
        return result

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass

    class Opener:
        def open(self, request, timeout):
            assert request.full_url == 'https://provider.example/models'
            assert timeout == 3
            return Response()

    def opener(*handlers):
        assert any(isinstance(item, v5_runtime._NoRedirect) for item in handlers)
        assert any(isinstance(item, v5_runtime.urllib.request.ProxyHandler) and item.proxies == {} for item in handlers)
        return Opener()

    monkeypatch.setattr(v5_runtime, '_assert_cloud_resolution', lambda *args: None)
    monkeypatch.setattr(v5_runtime.ssl, 'create_default_context', context)
    monkeypatch.setattr(v5_runtime.urllib.request, 'build_opener', opener)
    result = v5_runtime._health_request({'base_url': 'https://provider.example', 'kind': 'openai_compatible', 'location': 'cloud'})
    assert result == {'status': 'healthy', 'http_status': 200}
    assert contexts[0].check_hostname is True
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED


def test_health_certificate_failure_is_actionable_and_redacted(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError(ssl.SSLCertVerificationError('private diagnostic'))
    monkeypatch.setattr(v5_runtime.urllib.request, 'build_opener', lambda *args: Opener())
    result = v5_runtime._health_request({'base_url': 'http://127.0.0.1:11434', 'kind': 'ollama', 'location': 'local'})
    assert result == {'status': 'unavailable', 'detail': 'tls_certificate_verification_failed'}
