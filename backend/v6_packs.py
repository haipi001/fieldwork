"""First-party domain manifests over existing adapters. Manifests grant no authority."""
import copy
import hashlib
import json
import ast
from pathlib import Path


PACKS = {
    'src': dict(id='src', version='0.1', roles=['researcher', 'verifier'], skills=[],
        capability_templates=['network.request', 'browser.navigate', 'model.call'],
        tool_adapters=['traditional_runtime:create_http_exchange', 'traditional_runtime:execute_http_replay'],
        verifiers=['v5_verification:local_verifier_tick'],
        evidence_mappings=['http.exchange_metadata', 'http.replay'], report_profiles=['hackerone'],
        eval_suites=[], unsupported=['effectful_http_v6_gate', 'unbounded_external_scanning']),
    'web3': dict(id='web3', version='0.1', roles=['researcher', 'verifier'], skills=[],
        capability_templates=['model.call'],
        tool_adapters=['web3_analysis:run_forge_build', 'web3_analysis:run_forge_tests', 'web3_lab:start_rpc_fork'],
        verifiers=['web3_analysis:execute_property_replay'],
        evidence_mappings=['web3.property_replay'], report_profiles=['immunefi'],
        eval_suites=[], unsupported=['production_wallet_signing', 'remote_runner', 'complete_v6_tool_gateway']),
    'agent': dict(id='agent', version='0.1', roles=['verifier'], skills=[],
        capability_templates=[], tool_adapters=['agent_audit:import_events', 'agent_audit:analyze'],
        verifiers=['agent_audit:verify_incident'],
        evidence_mappings=['agent_audit_snapshot', 'agent_event', 'agent_verification'],
        report_profiles=['agent_audit_self_report'], eval_suites=['v6-policy-safety:1'],
        unsupported=['safe_active_lab', 'complete_process_monitoring', 'complete_v6_collector_gateway']),
}


def list_packs():
    result = []
    for manifest in PACKS.values():
        value = copy.deepcopy(manifest)
        value['manifest_sha256'] = hashlib.sha256(json.dumps(manifest, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        value['integration_status'] = 'partial'
        result.append(value)
    return result


def preflight(pack_id, manifest_sha256):
    manifest=next((p for p in list_packs() if p['id']==pack_id),None)
    if manifest is None:raise KeyError(pack_id)
    if manifest['manifest_sha256']!=manifest_sha256:
        raise ValueError('Manifest changed; refresh before checking')
    checks=[]
    for category in ('tool_adapters','verifiers'):
        for reference in manifest[category]:
            module,symbol=reference.split(':',1)
            source=Path(__file__).resolve().parent/(module+'.py')
            try:
                contents=source.read_bytes()
                tree=ast.parse(contents)
                present=any(isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name==symbol for node in tree.body)
                digest=hashlib.sha256(contents).hexdigest()
            except (OSError,SyntaxError,ValueError):
                present=False;digest=None
            checks.append({'category':category,'reference':reference,'implementation_present':present,'source_sha256':digest})
    return {'pack':manifest,'checks':checks,'implementations_present':all(c['implementation_present'] for c in checks),
            'execution_authorized':False,'installed_external_code':False,
            'scope':'static_source_presence_only','unsupported':manifest['unsupported']}
