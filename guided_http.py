"""Build object-read checks only from recorded, identity-bound JSON exchanges."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit


def scalar(body, path):
    try:
        value = json.loads(body)
        for part in path.split('.'):
            value = value[part]
        return str(value) if type(value) in (str, int) and str(value) and value != '[REDACTED]' else None
    except (ValueError, KeyError, TypeError):
        return None


def ready(identity):
    if identity['session_status'] != 'ready':
        return False
    try:
        expiry = identity.get('expires_at')
        return not expiry or datetime.fromisoformat(expiry.replace('Z','+00:00')) > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


def plan(candidate, exchanges, identities):
    if candidate['mode'] != 'traditional' or candidate['category'].lower() not in {'authorization','authentication','access_control','cwe-639','idor'}:
        return None
    if candidate['status'] == 'reproduced':
        return None
    target = urlsplit(candidate['target'])
    if target.scheme not in ('http','https') or target.username or target.password:
        return None
    usable = {x['id'] for x in identities if ready(x)}
    objects = [x for x in exchanges if x['url']==candidate['target'] and x['method']=='GET' and x['response_status']==200 and x['identity_id'] in usable]
    # Paths must exist in captured traffic. These names are hints, not findings.
    probes = [x for x in exchanges if x['method']=='GET' and x['response_status']==200 and x['identity_id'] in usable
              and (urlsplit(x['url']).scheme,urlsplit(x['url']).netloc)==(target.scheme,target.netloc)
              and urlsplit(x['url']).path.rstrip('/').split('/')[-1].lower() in {'me','whoami','userinfo'}]
    choices = {}
    for obj in objects:
        for own in probes:
            if own['identity_id'] != obj['identity_id']:
                continue
            for other in probes:
                if own['url']!=other['url'] or own['identity_id']==other['identity_id']:
                    continue
                for principal in ('id','sub','user_id','data.id','user.id'):
                    a,b=scalar(own['response_body_preview'],principal),scalar(other['response_body_preview'],principal)
                    if a is None or b is None or a==b:
                        continue
                    for owner in ('owner_id','data.owner_id','owner.id','data.owner.id'):
                        if scalar(obj['response_body_preview'],owner)!=a:
                            continue
                        value={'target':candidate['target'],'identity_url':own['url'],
                               'owner_identity_id':own['identity_id'],'other_identity_id':other['identity_id'],
                               'principal_field':principal,'owner_field':owner,
                               'sources':[obj['id'],own['id'],other['id']],
                               'semantic_status':'inferred_from_recorded_json', 'requests':10}
                        key=(own['url'],own['identity_id'],other['identity_id'],principal,owner)
                        choices.setdefault(key,value)
    # Never arbitrarily choose among ambiguous identities/field interpretations.
    if len(choices)!=1:
        return None
    result=next(iter(choices.values()))
    result['binding_hash']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def execute(candidate, binding, before_request, after_response=None, *, isolated_transport=False, checkpoint_callback=None):
    import traditional_runtime as http
    import final_core as f
    from fastapi import HTTPException
    engagement=f.get_engagement(candidate['engagement_id'])
    if not engagement.get('confirmed_at') or not engagement['scope'].get('allow_authentication'):
        raise HTTPException(409,'当前授权未允许测试账号登录与身份验证')
    for identity_id in (binding['owner_identity_id'],binding['other_identity_id']):
        identity=f.get_identity(identity_id)
        if identity['engagement_id']!=candidate['engagement_id'] or not ready(identity):
            raise HTTPException(409,'测试账号不属于当前项目或已失效')
    owner=http.resolve_identity_headers(binding['owner_identity_id'])
    other=http.resolve_identity_headers(binding['other_identity_id'])
    body=http.HttpReplayInput(candidate_id=candidate['id'],
        baseline=http.ReplayRequest(url=binding['target'],headers=owner),
        attack=http.ReplayRequest(url=binding['target'],headers=other),
        negative_control=http.ReplayRequest(url=binding['target']),
        authorization=http.AuthorizationAssertion(
            baseline_identity=http.ReplayRequest(url=binding['identity_url'],headers=owner),
            attack_identity=http.ReplayRequest(url=binding['identity_url'],headers=other),
            principal_field=binding['principal_field'],owner_field=binding['owner_field']),
        severity='unknown',impact_description='',root_cause='',weakness='CWE-639',location=binding['target'])
    with f.connect() as db:
        budget=db.execute('SELECT request_limit,requests_used FROM run_budgets_v2 WHERE run_id=?',(candidate['run_id'],)).fetchone()
        if not budget or budget['request_limit']-budget['requests_used'] < 10:
            raise HTTPException(409,'剩余请求预算不足两轮验证（需要10次），本次未开始')
        active=db.execute("SELECT id FROM verification_jobs WHERE candidate_id=? AND status IN ('queued','running','cancelling')",(candidate['id'],)).fetchone()
        if active:
            raise HTTPException(409,'这条候选已有正在执行的独立复验')
    def guarded_before(round_index,role):
        before_request(round_index,role)
        current=f.get_engagement(candidate['engagement_id'])
        if current.get('current_scope_snapshot_id')!=engagement.get('current_scope_snapshot_id') or not current['scope'].get('allow_authentication'):
            raise HTTPException(409,'项目授权已变化，自动复验停止')
        with f.connect() as db:
            run=db.execute('SELECT status FROM analysis_runs WHERE id=?',(candidate['run_id'],)).fetchone()
            record=db.execute('SELECT status FROM candidate_findings WHERE id=?',(candidate['id'],)).fetchone()
        if not run or run['status'] not in ('running','paused','completed') or not record or record['status'] in ('archived','graveyard','verified'):
            raise HTTPException(409,'运行或候选状态已变化，自动复验停止')
        if any(not ready(f.get_identity(identity_id)) for identity_id in (binding['owner_identity_id'],binding['other_identity_id'])):
            raise HTTPException(409,'测试会话已失效，自动复验停止')
    source_snapshot = None
    if isolated_transport:
        from v5_http_receipts import capture_sources
        with f.connect() as db:
            source_snapshot = capture_sources(db, candidate['id'], binding)
    result=http.execute_http_replay(candidate['run_id'],body,finalize=False,before_request=guarded_before,after_response=after_response,isolated_transport=isolated_transport,source_snapshot=source_snapshot,checkpoint_callback=checkpoint_callback)
    return {'binding_hash':binding['binding_hash'],'sources':binding['sources'],
            'artifact_id':result['artifact_id'],'status':result['verification']['status'],
            'checks':result['replay']['semantic_checks'], 'request_count':10,
            'business_boundary':result['replay']['business_boundary'],
            'limitation':'已检验所选身份与对象字段；业务共享权限、实际影响及严重度仍待证据确认。'}
