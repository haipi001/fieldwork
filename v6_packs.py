"""First-party domain manifests over existing adapters. Manifests grant no authority."""
import copy
import hashlib
import json


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
