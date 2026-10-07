"""Standalone trusted HTTP worker. No repository imports or database handles. Credentials arrive only over stdin."""
import http.client
import socket
import ssl
import ipaddress
import json
import sys
import urllib.request
from urllib.parse import urlsplit

TRUST_BUNDLE = sys.argv[1] if len(sys.argv) == 2 else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('redirect_forbidden')


def cloud_request(parsed, payload, timeout, api_key):
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('cloud_address_forbidden')
    address = addresses[0][4][0]
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            connected = socket.create_connection((address, self.port), self.timeout)
            try:
                self.sock = self._context.wrap_socket(connected, server_hostname=self.host)
            except BaseException:
                connected.close()
                raise
    connection = PinnedHTTPS(parsed.hostname, parsed.port or 443, timeout=timeout,
                             context=ssl.create_default_context(cafile=TRUST_BUNDLE))
    headers = {'Content-Type': 'application/json'}
    if api_key:
        headers['Authorization'] = 'Bearer ' + api_key
    try:
        connection.request('POST', parsed.path or '/', body=json.dumps(payload, allow_nan=False).encode(), headers=headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ValueError('redirect_forbidden')
        if not 200 <= response.status < 300:
            raise ValueError(f'http_status_{response.status}')
        output = response.read(256_001)
        if len(output) > 256_000:
            raise ValueError('output_limit')
        return output
    finally:
        connection.close()


def main():
    raw = sys.stdin.buffer.read(64_001)
    if len(raw) > 64_000:
        raise ValueError('input_limit')
    value = json.loads(raw)
    parsed = urlsplit(value['url'])
    location = value.get('location', 'local')
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('endpoint_forbidden')
    if location == 'local':
        if parsed.scheme != 'http' or not parsed.port or not ipaddress.ip_address(parsed.hostname).is_loopback:
            raise ValueError('endpoint_forbidden')
    elif location == 'cloud':
        if parsed.scheme != 'https':
            raise ValueError('cloud_requires_https')
    else:
        raise ValueError('invalid_location')
    api_key = value.get('api_key')
    if api_key is not None and (not isinstance(api_key, str) or len(api_key.encode()) > 16384 or any(ch in api_key for ch in '\r\n\x00')):
        raise ValueError('invalid_credential')
    timeout_ms = value['timeout_ms']
    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 45_000:
        raise ValueError('invalid_timeout')
    if location == 'cloud':
        sys.stdout.buffer.write(cloud_request(parsed, value['payload'], timeout_ms / 1000, api_key))
        return
    if api_key is not None:
        raise ValueError('local_credentials_not_supported')
    request = urllib.request.Request(value['url'], data=json.dumps(value['payload'], allow_nan=False).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout_ms / 1000) as response:
        output = response.read(256_001)
    if len(output) > 256_000:
        raise ValueError('output_limit')
    sys.stdout.buffer.write(output)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Fixed diagnostic vocabulary only. Never copy exception messages,
        # URLs, response bodies or credentials into the process result.
        if isinstance(error, ssl.SSLCertVerificationError):
            code = 'tls_verification_failed'
        elif isinstance(error, socket.gaierror):
            code = 'dns_resolution_failed'
        elif isinstance(error, TimeoutError):
            code = 'network_timeout'
        elif isinstance(error, urllib.error.HTTPError):
            code = f'http_status_{error.code}'
        elif type(error) is ValueError and str(error) in {
                'redirect_forbidden', 'cloud_address_forbidden', 'output_limit',
                'endpoint_forbidden', 'cloud_requires_https', 'invalid_credential',
                'local_credentials_not_supported', 'invalid_timeout', 'input_limit'}:
            code = str(error)
        elif type(error) is ValueError and str(error).startswith('http_status_') and str(error)[12:].isdigit():
            code = str(error)
        else:
            code = 'transport_error'
        sys.stdout.buffer.write(code.encode('ascii'))
        sys.exit(1)
