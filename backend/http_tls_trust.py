"""Explicit certificate issuers bound to authorized HTTPS origins."""
import re
from urllib.parse import urlsplit
from cryptography import x509


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid TLS origin')
    host = parsed.hostname
    host = '[' + host + ']' if ':' in host else host
    port = parsed.port
    return 'https://' + host + (':' + str(port) if port and port != 443 else '')


def validate(scope):
    issuers = scope.get('http_tls_trust', {})
    if not isinstance(issuers, dict) or len(issuers) > 32:
        raise ValueError('invalid scoped TLS issuer map')
    allowed = {origin(url) for url in scope.get('allowed_targets', []) if isinstance(url, str) and url.startswith('https://')}
    result = {}
    for target, pem in issuers.items():
        if not isinstance(target, str) or not isinstance(pem, str) or len(pem.encode()) > 16384:
            raise ValueError('invalid scoped TLS issuer')
        parsed = urlsplit(target)
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment or origin(target) not in allowed:
            raise ValueError('TLS issuer origin is outside scope')
        if not re.fullmatch(r'\s*-----BEGIN CERTIFICATE-----[A-Za-z0-9+/=\r\n]+-----END CERTIFICATE-----\s*', pem):
            raise ValueError('TLS issuer must contain one public certificate only')
        try:
            certificate = x509.load_pem_x509_certificate(pem.encode())
            if not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
                raise ValueError('TLS issuer is not a CA')
        except (ValueError, x509.ExtensionNotFound) as error:
            raise ValueError('invalid scoped CA certificate') from error
        key = origin(target)
        if key in result:
            raise ValueError('duplicate scoped TLS origin')
        result[key] = pem
    return result


def issuer_for(scope, url):
    return validate(scope).get(origin(url)) if urlsplit(url).scheme == 'https' else None
