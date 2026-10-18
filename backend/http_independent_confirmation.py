"""Independent semantic confirmation for the traditional HTTP entry point."""
from http_authorization_policy import digest, select_rule


def execution_input(artifact, scope, target):
    rule, _ = select_rule(scope, target)
    if not rule or not artifact.get('assertion'):
        return None
    rounds = artifact.get('rounds', [])
    if len(rounds) != 2:
        raise ValueError('independent HTTP confirmation requires two rounds')
    safe = []
    for group in rounds:
        if set(group) != {'baseline', 'attack', 'negative_control', 'baseline_identity', 'attack_identity'}:
            raise ValueError('independent HTTP confirmation requires all replay roles')
        for response in group.values():
            proof = response.get('process_execution', {})
            if (proof.get('observed_by') != 'fieldwork_local_supervisor'
                    or proof.get('network_grant') != 'single_connected_socket'
                    or proof.get('network_connect_denied') is not True
                    or proof.get('file_read_denied') is not True
                    or proof.get('exit_code') != 0 or proof.get('sandbox') != 'macos-seatbelt'
                    or type(proof.get('child_pid')) is not int
                    or proof.get('child_pid') == proof.get('parent_pid')):
                raise ValueError('HTTP transport was not independently observed')
        safe.append({role: {key: response.get(key) for key in
                           ('status', 'body_sha256', 'body_bytes', 'scalar_sha256')}
                     for role, response in group.items()})
    assertion = artifact['assertion']
    source = {'rounds': rounds, 'assertion': assertion, 'business_boundary': artifact['business_boundary']}
    return {'type': 'http_object_read_v1', 'rounds': safe,
            'principal_field': assertion['principal_field'], 'owner_field': assertion['owner_field'],
            'source_artifact_sha256': digest(source), 'scope_sha256': digest(scope), 'rule_sha256': digest(rule),
            'rule': {'access': rule['access'], 'allowed_principal_sha256':
                     [digest(p) for p in rule.get('allowed_principals', [])]}}


def confirm(artifact, scope, target):
    from v5_verification import _run_local_verifier
    contract = execution_input(artifact, scope, target)
    if contract is None:
        return None
    result, process = _run_local_verifier(contract)
    return {'schema': 'http-independent-confirmation/1', 'result': result, 'process_execution': process}


def valid(artifact, scope, target, classification):
    """Check the supervisor's stored result against current execution material."""
    from v5_verification import _dump
    import hashlib
    try:
        contract = execution_input(artifact, scope, target)
        confirmation = artifact['independent_confirmation']
        result, process = confirmation['result'], confirmation['process_execution']
        oracle = result['oracle']
        return bool(contract and confirmation['schema'] == 'http-independent-confirmation/1'
                    and process['observed_by'] == 'fieldwork_local_supervisor'
                    and process['sandbox'] == 'macos-seatbelt' and process['exit_code'] == 0
                    and process['file_read_denied'] is True and process['network_denied'] is True
                    and type(process['child_pid']) is int and process['child_pid'] != process['parent_pid']
                    and process['input_sha256'] == hashlib.sha256(_dump(contract).encode()).hexdigest()
                    and result['classification'] == classification
                    and result['status'] == ('verified' if classification == 'positive' else 'refuted')
                    and result['attempts'] == 2 and result['preconditions_valid'] is True
                    and result['counterevidence_checked'] is True and result['interrupted'] is False
                    and oracle['kind'] == 'http_object_read_v1'
                    and all(oracle[key] == contract[key] for key in
                            ('source_artifact_sha256', 'scope_sha256', 'rule_sha256')))
    except (KeyError, TypeError, ValueError):
        return False
