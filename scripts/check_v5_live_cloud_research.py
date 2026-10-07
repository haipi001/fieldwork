"""Explicit, single-call cloud acceptance using public synthetic graph material.

Never part of the ordinary pytest suite. Uses an isolated database and secret
store; existing configured credentials are sent only to their configured host.
The configured rate is a conservative test estimate, not an invoice.
"""
from pathlib import Path
import argparse
import hashlib
import json
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not args.execute:
        parser.error('--execute is required for a potentially billable model call')
    if args.output.exists():
        parser.error('Output already exists; preserve prior evidence and review unknown calls before another run')
    import pytest
    import traditional_tools
    import runtime_secrets
    import final_core
    import v5_research_worker as research
    from tests.test_final import client as fixture
    from tests.test_v5_research_worker import team, create
    from urllib.parse import urlsplit

    settings = traditional_tools.strix_provider_settings()
    base = settings.get('LLM_API_BASE') or settings.get('OPENAI_API_BASE') or ''
    credential = settings.get('LLM_API_KEY') or settings.get('OPENAI_API_KEY')
    model = settings.get('STRIX_LLM', '').removeprefix('openai/')
    parsed = urlsplit(base)
    if (not credential or not model or parsed.scheme != 'https' or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('No usable configured HTTPS provider')
    source_db = ROOT / 'data/src_control.db'
    before = hashlib.sha256(source_db.read_bytes()).hexdigest() if source_db.exists() else None
    report = {'provider_host': parsed.hostname, 'model': model,
              'material': 'public synthetic observations; no target requests',
              'configured_rate_micros_per_million_tokens': 20_000_000,
              'cost_basis': 'configured estimate, not vendor invoice', 'status': 'not_started'}
    with tempfile.TemporaryDirectory(prefix='fieldwork-live-cloud-') as directory:
        temporary = Path(directory)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(runtime_secrets, 'secret_root', lambda: temporary / 'secrets')
            session = secrets.token_urlsafe(48)
            patch.setenv('FIELDWORK_SESSION_TOKEN', session)
            generator = fixture.__wrapped__(temporary, patch)
            client = next(generator)
            client.headers['X-Fieldwork-Session'] = session
            try:
                runtime_secrets.put('live-cloud-acceptance', credential)
                with final_core.connect() as db:
                    now = final_core.utcnow()
                    db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                        'live-cloud', 'openai_compatible', 'Isolated live cloud acceptance',
                        base, model, 'live-cloud-acceptance', 1,
                        '{"location":"cloud","cost_micros_per_million_tokens":20000000}',
                        'healthy', now, now, now))
                report['stage'] = 'profile'
                response = client.put('/api/v1/runtime/config', json={
                    'name': 'Isolated live cloud', 'mode': 'cloud', 'config': {
                        'cloud_provider_ids': ['live-cloud'], 'max_concurrent_calls': 1,
                        'max_cost_micros': 100000, 'max_tokens_per_hour': 5000,
                        'max_runtime_ms_per_call': 45000}})
                if response.status_code != 201:
                    report['http_status'] = response.status_code
                    raise ValueError('Profile creation failed')
                report['stage'] = 'team'
                campaign, body = team(client, count=1)
                with final_core.connect() as db:
                    db.execute('UPDATE research_nodes SET attributes_json=? WHERE campaign_id=?',
                               ('{"sensitivity":"public"}', campaign['id']))
                body.update(runtime_profile_id=response.json()['id'], allow_cloud_context=True,
                            max_cost_micros=100000, max_tokens_per_task=4000,
                            objective='Analyze these public synthetic observations. Explain why the legitimate shared resource prevents a confirmed authorization vulnerability. Do not access any target or invent evidence.')
                group_id = create(client, body)
                report['stage'] = 'start'
                response = client.post(f'/api/v1/workers/research/groups/{group_id}/start')
                if response.status_code != 202:
                    report['http_status'] = response.status_code
                    report['blocked_reason'] = response.json().get('detail')
                    raise ValueError('Worker start blocked')
                report['stage'] = 'model'
                research._jobs[group_id].join(timeout=55)
                if research._jobs[group_id].is_alive():
                    raise TimeoutError('Worker did not finish')
                with final_core.connect() as db:
                    task = db.execute('SELECT * FROM agent_tasks WHERE group_id=?', (group_id,)).fetchone()
                    calls = [dict(row) for row in db.execute('SELECT state,reserved_tokens,reserved_cost_micros FROM runtime_calls')]
                    usage = [dict(row) for row in db.execute('SELECT route,input_tokens,output_tokens,cost_micros FROM runtime_usage')]
                    report.update(status=task['status'], result=json.loads(task['result_json'] or '{}'),
                                  error_type=json.loads(task['error_json'] or '{}').get('error_type'), calls=calls, usage=usage,
                                  findings=db.execute('SELECT COUNT(*) FROM canonical_findings').fetchone()[0],
                                  receipts=db.execute('SELECT COUNT(*) FROM verification_receipts_v5').fetchone()[0])
                    report['success'] = (task['status'] == 'succeeded' and len(calls) == 1
                                         and calls[0]['state'] == 'settled' and len(usage) == 1
                                         and usage[0]['route'] == 'cloud'
                                         and report['findings'] == report['receipts'] == 0)
            except Exception as exc:
                report.update(status='failed', error_type=type(exc).__name__, success=False)
            finally:
                research.stop_workers()
                with final_core.connect() as db:
                    report['call_states'] = [row[0] for row in db.execute('SELECT state FROM runtime_calls')]
                    report['task_statuses'] = [row[0] for row in db.execute('SELECT status FROM agent_tasks')]
                    report['task_errors'] = [json.loads(row[0] or '{}') for row in db.execute('SELECT error_json FROM agent_tasks')]
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    import sqlite3
                    destination = args.output.with_suffix('.db')
                    with sqlite3.connect(destination) as snapshot:
                        db.backup(snapshot)
                    destination.chmod(0o600)
                    report['isolated_database_snapshot'] = str(destination)
                runtime_secrets.delete('live-cloud-acceptance')
                generator.close()
    report['source_database_bytes_unchanged'] = (before == hashlib.sha256(source_db.read_bytes()).hexdigest()) if before else None
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if credential in encoded:
        raise ValueError('Credential found in acceptance output')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded)
    print(json.dumps({key: report.get(key) for key in ('status', 'success', 'stage', 'http_status', 'blocked_reason', 'error_type', 'usage', 'calls', 'call_states', 'source_database_bytes_unchanged')}, ensure_ascii=False))
    return 0 if report.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
