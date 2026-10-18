"""Only explicit public CA certificates for authorized origins are accepted."""
import pytest
from http_tls_trust import validate, issuer_for
from network_broker_tls import broker_authority
from tests.test_final import client


def test_scoped_tls_issuer_is_origin_bound_and_default_port_is_canonical():
    with broker_authority() as (pem, _):
        scope = {'allowed_targets': ['https://example.test/allowed'], 'http_tls_trust': {'https://example.test:443': pem.decode()}}
        assert validate(scope) == {'https://example.test': pem.decode()}
        assert issuer_for(scope, 'https://example.test/allowed/resource') == pem.decode()
        assert issuer_for(scope, 'https://other.test/allowed') is None
        assert issuer_for(scope, 'http://example.test/allowed') is None


@pytest.mark.parametrize('change', ['outside', 'query', 'path', 'credentials', 'private_key', 'malformed', 'map_type'])
def test_invalid_tls_issuer_cannot_be_persisted_by_project_creation(client, change):
    with broker_authority() as (pem, _):
        target = 'https://example.test'
        issuer = pem.decode()
        if change == 'outside': target = 'https://other.test'
        if change == 'query': target += '?secret=fixture'
        if change == 'path': target += '/issuer'
        if change == 'credentials': target = 'https://fixture:fixture@example.test'
        if change == 'private_key': issuer += '\n-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----'
        if change == 'malformed': issuer = 'invalid certificate'
        mapping = [] if change == 'map_type' else {target: issuer}
        result = client.post('/api/v1/engagements', json={'name': 'Invalid issuer fixture', 'target': 'https://example.test',
            'mode': 'traditional', 'scope': {'http_tls_trust': mapping}, 'policy': {}})
        assert result.status_code == 422
        assert 'BEGIN' not in result.text
