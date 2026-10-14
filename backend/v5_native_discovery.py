"""Native read-only discovery decisions use the V5 private/local model ledger."""
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException
import final_core as f
import v5_runtime as r
import v5_workers as w
import v5_orchestration as o

RUNNER = 'builtin-research-native-' + uuid.uuid4().hex[:12]
_lock = threading.Lock()


def validate_profile(profile_id):
    if not profile_id:
        raise HTTPException(409, 'native discovery requires a selected V5 Profile')
    with f.connect() as db:
        profile = db.execute('SELECT * FROM runtime_profiles WHERE id=?', (profile_id,)).fetchone()
        if not profile:
            raise HTTPException(409, 'native discovery Profile not found')
        config = r.ProfileConfig.model_validate(r._load(profile['config_json'], {}))
        for provider_id in config.local_provider_ids:
            row = db.execute("SELECT * FROM runtime_providers WHERE id=? AND enabled=1 AND last_health='healthy'", (provider_id,)).fetchone()
            if not row or row['secret_ref'] or r._location(row) != 'local':
                continue
            parsed = urlsplit(row['base_url'])
            try:
                valid = parsed.scheme == 'http' and parsed.port and ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                valid = False
            if valid and (not config.provider_config_hashes or r._provider_config_hash(row) == config.provider_config_hashes.get(provider_id)):
                return profile
    raise HTTPException(409, 'native observations require a healthy local model in the selected Profile')


def configured():
    try:
        with f.connect() as db:
            profiles = [row[0] for row in db.execute('SELECT id FROM runtime_profiles')]
        for profile_id in profiles:
            try:
                validate_profile(profile_id)
                return True
            except HTTPException:
                continue
    except Exception:
        pass
    return False


def inputs_current(db, task):
    capsule = r._load(task['context_capsule_json'], {})
    run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (task['run_id'],)).fetchone()
    if not run or run['status'] != 'running' or not o._continuous_scope_current(db, task):
        raise HTTPException(409, 'native discovery run or authority changed')
    for observation_id, expected in capsule['native_input_hashes'].items():
        row = db.execute('SELECT * FROM observations WHERE id=? AND run_id=?', (observation_id, task['run_id'])).fetchone()
        if not row or hashlib.sha256(f.dump(dict(row)).encode()).hexdigest() != expected:
            raise HTTPException(409, 'native discovery observation changed')
        artifact = db.execute('SELECT uri,sha256 FROM artifacts WHERE id=? AND run_id=?', (row['raw_ref'], task['run_id'])).fetchone()
        from native_agent import WORKSPACE_ROOT
        path = Path(artifact['uri']) if artifact else None
        root = (WORKSPACE_ROOT / task['run_id']).resolve()
        if (not path or path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file()
                or path.stat().st_size > 2_000_000 or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']):
            raise HTTPException(409, 'native discovery artifact changed')


def decision_for_run(run, profile_id, turn, context):
    from v5_runtime_calls import reserve_call, fail_call
    from v5_model_inputs import prepare_call
    from v5_model_response import ModelOutputRejected, settle_rejected_output
    validate_profile(profile_id)
    with _lock:
        now = f.utcnow()
        with f.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run['id'],)).fetchone()
            if not current or current['status'] != 'running' or current['scope_snapshot_id'] != run['scope_snapshot_id'] or current['policy_id'] != run['policy_id']:
                raise HTTPException(409, 'native discovery run changed')
            name = 'Native read-only discovery ' + run['id']
            campaign = db.execute('SELECT id FROM research_campaigns WHERE engagement_id=? AND name=?', (run['engagement_id'], name)).fetchone()
            campaign_id = campaign[0] if campaign else f.uid('campaign')
            if not campaign:
                db.execute('INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                    campaign_id, run['engagement_id'], name, 'Read-only model-driven page discovery', 'active',
                    'business_logic', 12, 0, 1, .85, now, now))
            hashes = {}
            for item in context.get('observations', []):
                row = db.execute('SELECT * FROM observations WHERE id=? AND run_id=?', (item['observation_id'], run['id'])).fetchone()
                if not row:
                    raise HTTPException(409, 'native observation missing')
                hashes[row['id']] = hashlib.sha256(f.dump(dict(row)).encode()).hexdigest()
        created = o.create_task(o.TaskCreate(campaign_id=campaign_id, run_id=run['id'], role='explorer',
            objective='Choose a bounded read-only navigation or finish with evidence-bound hypotheses',
            route_requirement={'kind': 'native-discovery'},
            idempotency_key=f"native-discovery:{run['id']}:{turn}", max_attempts=1,
            context_capsule={'native_discovery': True, 'runtime_profile_id': profile_id,
                'scope_snapshot_id': run['scope_snapshot_id'], 'policy_id': run['policy_id'],
                'native_discovery_context': context, 'native_input_hashes': hashes, 'sensitivity': 'secret'},
            budget={'max_tokens': 8000, 'max_runtime_ms': 45000, 'max_cost_micros': 0}))
        with f.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            occupied = db.execute('SELECT metadata_json FROM runner_registry_v5 WHERE id=?', (RUNNER,)).fetchone()
            if occupied and r._load(occupied['metadata_json'], {}).get('builtin') is not True:
                raise HTTPException(409, 'built-in native runner identity is occupied')
            db.execute('INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=\'online\',heartbeat_at=excluded.heartbeat_at,updated_at=excluded.updated_at', (
                RUNNER, 'Native discovery model runner', 'native-discovery', 'online', f.dump(['native_discovery']),
                '{"location":"local"}', 1, 0, now, '{"builtin":true}', now, now))
            task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (created['id'],)).fetchone()
            if task['status'] != 'queued' or task['attempt']:
                raise HTTPException(409, 'native decision already attempted; resume requires checkpoint review')
            inputs_current(db, task)
            db.execute("UPDATE agent_tasks SET status='running',attempt=1,lease_owner=?,lease_expires_at=?,heartbeat_at=?,updated_at=? WHERE id=?",
                (RUNNER, (datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(), now, now, task['id']))
            o._sync_runner_jobs(db, RUNNER)
            task = db.execute('SELECT * FROM agent_tasks WHERE id=?', (task['id'],)).fetchone()
        call = None
        try:
            decision = r.route_task(r.RouteRequest(task_id=task['id'], task_type='native_discovery', profile_id=profile_id, sensitivity='secret', mode='local', estimated_tokens=8000))
            if decision['status'] != 'selected' or decision['route'] != 'local':
                raise HTTPException(409, 'native discovery route blocked')
            with f.connect() as db:
                provider = db.execute('SELECT * FROM runtime_providers WHERE id=?', (decision['provider_ref'],)).fetchone()
            call = reserve_call(decision['id'], task['id'], RUNNER, 1)
            started = time.monotonic()
            execution = prepare_call(provider, task, [], call)
            output, incoming, outgoing, duration = w._local_model_output(provider, execution, [])
            duration = max(duration, int((time.monotonic()-started)*1000))
            r.record_usage(r.UsageReport(decision_id=decision['id'], call_id=call['id'],
                idempotency_key=f"native:{task['id']}:1", input_tokens=incoming, output_tokens=outgoing, runtime_ms=duration))
            if (output.get('action') not in {'navigate','finish'} or not isinstance(output.get('hypotheses'), list)
                    or len(output['hypotheses']) > 20 or incoming > execution['model_preflight']['input_tokens']
                    or outgoing > execution['model_preflight']['output_max_tokens']):
                raise ValueError('native model decision invalid')
            with f.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = o._owned_running(db, task['id'], RUNNER)
                inputs_current(db, current)
                db.execute("UPDATE agent_tasks SET status='succeeded',result_json=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=? WHERE id=?", (f.dump(output), f.utcnow(), task['id']))
                o._sync_runner_jobs(db, RUNNER)
            return output
        except Exception as error:
            if call:
                if isinstance(error, ModelOutputRejected):
                    try:
                        settle_rejected_output(task, call, decision, provider, error, int((time.monotonic()-started)*1000), 'native')
                    except Exception:
                        pass
                fail_call(call['id'])
            with f.connect() as db:
                db.execute("UPDATE agent_tasks SET status='paused',lease_owner=NULL,lease_expires_at=NULL,error_json=?,updated_at=? WHERE id=? AND status='running' AND lease_owner=?", (f.dump({'code':'native_discovery_stopped','error_type':type(error).__name__}), f.utcnow(), task['id'], RUNNER))
                o._sync_runner_jobs(db, RUNNER)
            raise
