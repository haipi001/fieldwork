"""Internal contract routing and lifecycle tests; no real RPC or analyzer claims."""
import asyncio

import pytest
from fastapi import HTTPException

import capability_registry
import final_core
import traditional_tools
import web3_analysis
import web3_lab
from tests.test_final import client, create_ready


@pytest.fixture
def contract(client, tmp_path):
    engagement = create_ready(client, mode='web3', target='0x' + '12' * 20)
    source = tmp_path / 'contract'
    source.mkdir()
    (source / 'Contract.sol').write_text('pragma solidity ^0.8.0; contract Contract {}')
    run_id = 'contract-routing'
    config = {'execution_mode': 'real', 'fork_rpc_configured': True,
              'source_path': str(source), 'chain_id': 1, 'fork_block_number': 42,
              'deployment_alignment_id': 'alignment-routing'}
    rules = {'kind': 'deployment_alignment', 'status': 'aligned', 'chain_id': 1, 'block_number': 42}
    with final_core.connect() as db:
        db.execute('INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, engagement['id'], 'web3', engagement['current_scope_snapshot_id'],
                    engagement['current_policy_id'], 'queued', 'surface', 0,
                    None, None, None, None, final_core.utcnow()))
        db.execute('INSERT INTO run_configs_v2 VALUES(?,?,?)',
                   (run_id, final_core.dump(config), final_core.utcnow()))
        db.execute('INSERT INTO program_snapshots VALUES(?,?,?,?,?,?,?)',
                   ('alignment-routing', engagement['id'], 100, 'fixture', final_core.dump(rules),
                    str(source), final_core.utcnow()))
    return run_id, source


def status(run_id):
    with final_core.connect() as db:
        return db.execute('SELECT status FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]


def test_internal_contract_route_keeps_public_tool_route_closed(contract):
    run_id, source = contract
    with pytest.raises(HTTPException):
        traditional_tools._load_run(run_id)
    assert traditional_tools._load_run(run_id, contract_pipeline=True)[0]['id'] == run_id
    for capability, path in [('nuclei', source), ('semgrep', source.parent)]:
        with pytest.raises(HTTPException) as error:
            traditional_tools.run_capability(run_id, capability, str(path), 10, contract_pipeline=True)
        assert error.value.status_code == 409


@pytest.mark.parametrize('field,value', [('execution_mode', 'demo'), ('fork_rpc_configured', False),
                                         ('chain_id', 2), ('fork_block_number', 43)])
def test_stale_contract_configuration_fails_without_starting_fork(contract, monkeypatch, field, value):
    run_id, source = contract
    with final_core.connect() as db:
        config = final_core.load(db.execute('SELECT config FROM run_configs_v2 WHERE run_id=?', (run_id,)).fetchone()[0], {})
        config[field] = value
        db.execute('UPDATE run_configs_v2 SET config=? WHERE run_id=?', (final_core.dump(config), run_id))
    calls = []
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', lambda *args: calls.append(args))
    asyncio.run(traditional_tools.execute_web3_source_pipeline(
        run_id, traditional_tools.TraditionalToolchainInput(source_path=str(source)), 'https://rpc.invalid', 42, 1))
    assert status(run_id) == 'failed'
    assert calls == []


@pytest.mark.parametrize('pause_at', [None, 'code', 'analysis'])
def test_contract_chain_routes_code_only_and_preserves_pause(contract, monkeypatch, pause_at):
    run_id, source = contract
    calls = []
    def fork(*args):
        calls.append('fork')
        return {'id': 'routing-fork', 'chain_id': 1, 'fork_block': 42}
    def tool(run, capability, path, timeout, target, profile, *, contract_pipeline=False):
        assert contract_pipeline and capability in traditional_tools.CODE_CAPABILITIES
        assert path == str(source)
        calls.append(capability)
        if pause_at == 'code':
            with final_core.connect() as db:
                db.execute("UPDATE analysis_runs SET status='paused' WHERE id=?", (run,))
        return {'observation_count': 0, 'observation_ids': [], 'subjects': [],
                'tool_result': {'status': 'failed'}}
    def analyze(*args):
        calls.append('analysis')
        if pause_at == 'analysis':
            with final_core.connect() as db:
                db.execute("UPDATE analysis_runs SET status='paused' WHERE id=?", (run_id,))
    def correlate(*args):
        calls.append('correlate')
        return {'count': 0}
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', fork)
    monkeypatch.setattr(web3_lab, 'stop_local_fork', lambda fork_id: calls.append(('stop', fork_id)))
    monkeypatch.setattr(capability_registry, 'inventory', lambda: [
        {'id': item, 'available': True} for item in traditional_tools.CODE_CAPABILITIES])
    monkeypatch.setattr(traditional_tools, 'run_capability', tool)
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', analyze)
    monkeypatch.setattr(final_core, 'correlate_observations', correlate)
    body = traditional_tools.TraditionalToolchainInput(source_path=str(source), include_recon=True,
                                                     include_strix=True, include_shannon=True)
    asyncio.run(traditional_tools.execute_web3_source_pipeline(run_id, body, 'https://rpc.invalid', 42, 1))
    assert calls[0] == 'fork' and calls[-1] == ('stop', 'routing-fork')
    assert status(run_id) == ('paused' if pause_at else 'completed')
    assert ('analysis' in calls) == (pause_at != 'code')
    assert ('correlate' in calls) == (pause_at is None)


@pytest.mark.parametrize('failure', ['source', 'fork', 'analysis'])
def test_pipeline_failure_reaches_terminal_and_releases_created_fork(contract, monkeypatch, failure):
    run_id, source = contract
    calls = []
    def fork(*args):
        calls.append('fork')
        if failure == 'fork':
            raise RuntimeError('RPC fixture unavailable')
        return {'id': 'routing-fork', 'chain_id': 1, 'fork_block': 42}
    def analyze(*args):
        raise RuntimeError('analyzer fixture failure')
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', fork)
    monkeypatch.setattr(web3_lab, 'stop_local_fork', lambda fork_id: calls.append('stop'))
    monkeypatch.setattr(capability_registry, 'inventory', lambda: [])
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', analyze)
    body = traditional_tools.TraditionalToolchainInput(source_path=str(source.parent if failure == 'source' else source))
    asyncio.run(traditional_tools.execute_web3_source_pipeline(run_id, body, 'https://rpc.invalid', 42, 1))
    assert status(run_id) == 'failed'
    assert ('fork' in calls) == (failure != 'source')
    assert ('stop' in calls) == (failure == 'analysis')


@pytest.mark.parametrize('state', ['paused', 'stopped', 'budget_exhausted', 'timeout'])
def test_terminal_or_paused_contract_does_not_restart_fork(contract, monkeypatch, state):
    run_id, source = contract
    with final_core.connect() as db:
        db.execute('UPDATE analysis_runs SET status=? WHERE id=?', (state, run_id))
    calls = []
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', lambda *args: calls.append('fork'))
    asyncio.run(traditional_tools.execute_web3_source_pipeline(
        run_id, traditional_tools.TraditionalToolchainInput(source_path=str(source)), 'https://rpc.invalid', 42, 1))
    assert status(run_id) == state and calls == []


def test_omitted_fork_context_is_previewed_persisted_and_dispatched(client, contract, monkeypatch):
    run_id, source = contract
    with final_core.connect() as db:
        engagement_id = db.execute('SELECT engagement_id FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]
    calls = []
    async def execute(run, body, rpc, block, chain):
        calls.append((run, block, chain))
    monkeypatch.setattr(traditional_tools, 'execute_web3_source_pipeline', execute)
    payload = {'execution_mode': 'real', 'source_path': str(source), 'fork_rpc_url': 'https://rpc.invalid',
               'include_native_agent': False}
    plan = client.post(f'/api/v1/engagements/{engagement_id}/execution-plan', json=payload).json()
    assert plan['contract_context'] == {'deployment_alignment_id': 'alignment-routing', 'chain_id': 1, 'fork_block_number': 42}
    assert any(item['id'] == 'fork_context' for item in plan['warnings'])
    response = client.post(f'/api/v1/engagements/{engagement_id}/start', json=payload)
    assert response.status_code == 202, response.text
    with final_core.connect() as db:
        config = final_core.load(db.execute('SELECT config FROM run_configs_v2 WHERE run_id=?', (response.json()['id'],)).fetchone()[0], {})
    assert config['chain_id'] == 1 and config['fork_block_number'] == 42
    assert config['deployment_alignment_id'] == 'alignment-routing'
    assert calls == [(response.json()['id'], 42, 1)]


@pytest.mark.parametrize('field,value', [('chain_id', None), ('block_number', None), ('block_number', -1), ('chain_id', True)])
def test_incomplete_alignment_blocks_preview_and_start(client, contract, field, value):
    run_id, source = contract
    with final_core.connect() as db:
        rules = final_core.load(db.execute("SELECT rules FROM program_snapshots WHERE id='alignment-routing'").fetchone()[0], {})
        rules[field] = value
        db.execute("UPDATE program_snapshots SET rules=? WHERE id='alignment-routing'", (final_core.dump(rules),))
        engagement_id = db.execute('SELECT engagement_id FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]
    payload = {'execution_mode': 'real', 'source_path': str(source), 'fork_rpc_url': 'https://rpc.invalid'}
    plan = client.post(f'/api/v1/engagements/{engagement_id}/execution-plan', json=payload).json()
    assert any(item['id'] == 'deployment_alignment' for item in plan['blockers'])
    response = client.post(f'/api/v1/engagements/{engagement_id}/start', json=payload)
    assert response.status_code == 409
    with final_core.connect() as db:
        assert db.execute('SELECT count(*) FROM analysis_runs').fetchone()[0] == 1


@pytest.mark.parametrize('mismatch', ['input', 'fork'])
def test_fork_context_mismatch_never_starts_analysis(contract, monkeypatch, mismatch):
    run_id, source = contract
    calls = []
    def fork(*args):
        assert args[2:] == (42, 1)
        calls.append('fork')
        return {'id': 'wrong-fork', 'chain_id': 1, 'fork_block': 43}
    monkeypatch.setattr(web3_lab, 'start_rpc_fork', fork)
    monkeypatch.setattr(web3_lab, 'stop_local_fork', lambda _: calls.append('stop'))
    monkeypatch.setattr(web3_analysis, 'execute_repository_pipeline', lambda *args: calls.append('analysis'))
    asyncio.run(traditional_tools.execute_web3_source_pipeline(
        run_id, traditional_tools.TraditionalToolchainInput(source_path=str(source)), 'https://rpc.invalid',
        43 if mismatch == 'input' else None, None))
    assert status(run_id) == 'failed'
    assert calls == ([] if mismatch == 'input' else ['fork', 'stop'])


def test_new_alignment_invalidates_reviewed_plan_and_queued_run(client, contract):
    from assisted_research import prepare
    run_id, source = contract
    with final_core.connect() as db:
        engagement_id = db.execute('SELECT engagement_id FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]
    payload = {'execution_mode': 'real', 'source_path': str(source), 'fork_rpc_url': 'https://rpc.invalid'}
    before = prepare(engagement_id, overrides=payload)
    with final_core.connect() as db:
        db.execute('INSERT INTO program_snapshots SELECT ?,engagement_id,101,platform,rules,source_uri,created_at FROM program_snapshots WHERE id=?',
                   ('alignment-replaced', 'alignment-routing'))
    after = prepare(engagement_id, overrides=payload)
    assert before['plan_hash'] != after['plan_hash']
    with pytest.raises(HTTPException):
        traditional_tools._load_run(run_id, contract_pipeline=True)


def test_actual_anvil_inherits_upstream_chain_and_rejects_mismatch(client, contract):
    """Real local Anvil processes, no external RPC and no contract analyzer."""
    import subprocess
    executable = web3_lab.binary('anvil')
    if not executable:
        pytest.skip('actual Anvil unavailable')
    run_id, _ = contract
    with final_core.connect() as db:
        engagement_id = db.execute('SELECT engagement_id FROM analysis_runs WHERE id=?', (run_id,)).fetchone()[0]
    port = web3_lab.free_port()
    upstream = subprocess.Popen([executable, '--host', '127.0.0.1', '--port', str(port),
                                 '--chain-id', '23456', '--silent'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fork_id = None
    try:
        web3_lab.wait_ready(port, upstream)
        endpoint = f'http://127.0.0.1:{port}'
        fork = web3_lab.start_rpc_fork(engagement_id, endpoint, 0, 23456)
        fork_id = fork['id']
        assert fork['chain_id'] == 23456 and fork['fork_block'] == 0
        with pytest.raises(HTTPException) as error:
            web3_lab.start_rpc_fork(engagement_id, endpoint, 0, 1)
        assert error.value.status_code == 409
        with final_core.connect() as db:
            assert db.execute('SELECT count(*) FROM web3_forks WHERE engagement_id=?', (engagement_id,)).fetchone()[0] == 1
    finally:
        if fork_id:
            web3_lab.stop_local_fork(fork_id)
        web3_lab.stop_process(upstream)
