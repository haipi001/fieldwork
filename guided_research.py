"""Persisted preparation and opt-in bounded object-read verification."""
from __future__ import annotations

import hashlib
import json
import threading
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from candidate_quality import HTTP_READ_CATEGORIES, INFORMATION_CATEGORIES
from candidate_workflow import annotate as annotate_workflow
from reporting import redact_structure

router = APIRouter(prefix='/api/v1', tags=['Guided research'])
STAGES = [('prepare', '准备'), ('mine', '页面 / 源码挖掘'), ('candidates', '补齐候选'),
          ('verify', '验证准备'), ('summary', '汇总结果')]
ACTIVE = ('queued', 'running')
MAX_ITEMS = 100
MAX_AUTOMATION_ROUNDS = 50


def core():
    import final_core
    return final_core


def init_guided_db():
    with core().connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS guided_research_jobs (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL, candidate_id TEXT,
          fingerprint TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
          steps TEXT NOT NULL, result TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
          error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS guided_one_active_run
          ON guided_research_jobs(run_id) WHERE status IN ('queued','running');
        CREATE INDEX IF NOT EXISTS guided_history ON guided_research_jobs(run_id,created_at);
        ''')
        # Startup only: unfinished jobs have no surviving worker. User can resume.
        for row in db.execute("SELECT id,steps FROM guided_research_jobs WHERE status IN ('queued','running')").fetchall():
            steps = core().load(row['steps'], [])
            for step in steps:
                if step['status'] in ('pending', 'running'):
                    step['status'] = 'interrupted'
            db.execute("UPDATE guided_research_jobs SET status='interrupted',steps=?,phase='应用重启，可继续整理',updated_at=? WHERE id=?", (core().dump(steps),core().utcnow(),row['id']))


class StartInput(BaseModel):
    candidate_id: str | None = Field(default=None, max_length=100)
    execute_ready: bool = False
    read_pages: bool = False
    after_candidate_id: str | None = Field(default=None,max_length=100)
    continue_from: str | None = Field(default=None,max_length=100)
    auto_continue: bool = False
    automation_round: int = Field(default=1, ge=1, le=MAX_AUTOMATION_ROUNDS)


def snapshot(run_id, candidate_id=None, after_candidate_id=None):
    f = core()
    with f.connect() as db:
        run = db.execute('SELECT * FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, '运行不存在')
        if candidate_id and after_candidate_id:
            raise HTTPException(422, '单条候选处理不能同时指定批次位置')
        if after_candidate_id and not db.execute('SELECT 1 FROM candidate_findings WHERE id=? AND run_id=?',(after_candidate_id,run_id)).fetchone():
            raise HTTPException(409, '批次位置不属于当前运行，请从当前列表重新开始')
        current=db.execute('''SELECT e.current_scope_snapshot_id,e.current_policy_id,s.rules,p.policy
            FROM engagements_v2 e JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id
            JOIN execution_policies p ON p.id=e.current_policy_id WHERE e.id=?''',(run['engagement_id'],)).fetchone()
        budget=db.execute('SELECT request_limit,requests_used,tool_call_limit,tool_calls_used,model_budget_micros,model_cost_micros FROM run_budgets_v2 WHERE run_id=?',(run_id,)).fetchone()
        runtime_context={'run_status':run['status'],'run_scope':run['scope_snapshot_id'],'run_policy':run['policy_id'],
                         'scope_id':current['current_scope_snapshot_id'],'policy_id':current['current_policy_id'],
                         'scope_digest':hashlib.sha256(current['rules'].encode()).hexdigest(),
                         'policy_digest':hashlib.sha256(current['policy'].encode()).hexdigest(),
                         'budget':dict(budget) if budget else None}
        params = [run_id]
        clause = ''
        if candidate_id:
            clause = ' AND id=?'
            params.append(candidate_id)
        elif after_candidate_id:
            clause = ' AND id>?'
            params.append(after_candidate_id)
        rows = db.execute("SELECT * FROM candidate_findings WHERE run_id=? AND status NOT IN ('archived','graveyard','verified')" + clause + ' ORDER BY id LIMIT ?', (*params, MAX_ITEMS + 1)).fetchall()
        if candidate_id and not rows:
            raise HTTPException(409, '候选不属于当前运行，或已结束处理')
        total = db.execute("SELECT count(*) FROM candidate_findings WHERE run_id=? AND status NOT IN ('archived','graveyard','verified')"+clause, params).fetchone()[0]
        # Hard bounds; truncation is disclosed, never presented as complete coverage.
        selected_evidence=f.dump(sorted({eid for row in rows[:MAX_ITEMS] for eid in f.load(row['evidence_ids'],[]) if isinstance(eid,str)}))
        observations = [dict(x) for x in db.execute('''SELECT id,subject,observation_type,summary,source_capability,raw_ref FROM observations WHERE run_id=?
            ORDER BY CASE WHEN id IN (SELECT observation_id FROM evidence_v2 WHERE run_id=? AND id IN (SELECT value FROM json_each(?))) THEN 0 ELSE 1 END,id LIMIT 1001''', (run_id,run_id,selected_evidence))]
        evidence = [dict(x) for x in db.execute('''SELECT e.id,e.observation_id,e.summary,e.artifact_id,e.evidence_type
            FROM evidence_v2 e LEFT JOIN observations o ON o.id=e.observation_id
            WHERE e.run_id=? AND (e.observation_id IS NULL OR o.run_id=e.run_id)
            ORDER BY CASE WHEN e.id IN (SELECT value FROM json_each(?)) THEN 0 ELSE 1 END,e.id LIMIT 2001''', (run_id,selected_evidence))]
        exchanges = [dict(x) for x in db.execute('''SELECT id,url,method,identity_id,response_status,response_body_preview,response_sha256
            FROM http_exchanges WHERE run_id=? ORDER BY created_at DESC,id LIMIT 101''', (run_id,))]
        identities = [dict(x) for x in db.execute('''SELECT i.id,i.label,p.session_status,p.expires_at,p.last_validated_at
            FROM identities i JOIN identity_profiles p ON p.identity_id=i.id
            WHERE i.engagement_id=? ORDER BY i.id''', (run['engagement_id'],))]
    return {'run_id': run_id, 'candidate_id': candidate_id, 'mode': run['mode'],
            'after_candidate_id':after_candidate_id, 'runtime_context':runtime_context,
            'next_cursor':rows[MAX_ITEMS-1]['id'] if len(rows)>MAX_ITEMS else None,
            'candidates': [dict(x) for x in rows[:MAX_ITEMS]], 'observations': observations[:1000],
            'evidence': evidence[:2000], 'exchanges': exchanges[:100], 'identities': identities,
            'remaining_candidates': max(0, total - len(rows[:MAX_ITEMS])) if not candidate_id else 0,
            'truncated': len(rows)>MAX_ITEMS or len(observations)>1000 or len(evidence)>2000 or len(exchanges)>100}


def fingerprint(data):
    return hashlib.sha256(core().dump(data).encode()).hexdigest()


def verification_input_digest(candidate, data, binding):
    """Reuse requires the same claim, referenced evidence and authority context."""
    ids = set(core().load(candidate['evidence_ids'], []))
    evidence = [row for row in data['evidence'] if row['id'] in ids]
    observations = {row['observation_id'] for row in evidence}
    context = data.get('runtime_context', {})
    return fingerprint({
        'candidate': candidate, 'binding': binding,
        'evidence': sorted(evidence, key=lambda row: row['id']),
        'observations': sorted((row for row in data['observations'] if row['id'] in observations), key=lambda row: row['id']),
        'authority': {key: context.get(key) for key in ('run_scope', 'run_policy', 'scope_id', 'policy_id', 'scope_digest', 'policy_digest')},
    })


def safe_url(value, base):
    """Discovery only. Drop credentials and query data; never open inferred URLs."""
    try:
        url = urlsplit(urljoin(base, value))
        origin = urlsplit(base)
        if url.scheme not in ('http', 'https') or url.username or url.password:
            return None
        if (url.scheme, url.netloc) != (origin.scheme, origin.netloc):
            return None
        return urlunsplit((url.scheme, url.netloc, url.path or '/', '', ''))
    except ValueError:
        return None


class PageLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links = base, []

    def handle_starttag(self, tag, attrs):
        key = {'a': 'href', 'script': 'src', 'form': 'action'}.get(tag)
        if not key or len(self.links) >= 30:
            return
        value = dict(attrs).get(key)
        url = safe_url(value, self.base) if value else None
        if url and not any(item['url'] == url for item in self.links):
            self.links.append({'url': url, 'kind': tag, 'executed': False, 'readable': not bool(urlsplit(value).query)})


def material_for(candidate, data):
    f = core()
    category = candidate['category'].lower()
    linked = set(f.load(candidate['evidence_ids'], []))
    observations = {x['id']: x for x in data['observations']}
    evidence = [x for x in data['evidence'] if x['id'] in linked]
    # Suggest unlinked exact-target records separately; never silently rewrite user evidence.
    related = [x for x in data['observations'] if x['subject'] == candidate['target']]
    sources = [{'kind': 'evidence', 'id': x['id'], 'observation_id': x['observation_id'],
                'text': (observations.get(x['observation_id'], {}).get('summary') or x['summary'])[:1500]} for x in evidence]
    used = {x['observation_id'] for x in evidence}
    sources.extend({'kind': 'observation', 'id': x['id'], 'text': x['summary'][:1500]} for x in related if x['id'] not in used)
    matching = [x for x in data['exchanges'] if x['url'] == candidate['target'] and x['method'] == 'GET']
    sources.extend({'kind': 'http_exchange', 'id': x['id'], 'text': f"GET 已记录，HTTP {x['response_status']}", 'sha256': x['response_sha256']} for x in matching[:5])
    gaps = []
    missing = sorted(linked - {x['id'] for x in evidence})
    if missing or not linked:
        gaps.append({'code': 'linked_evidence', 'message': '候选引用证据缺失，不能使用相关页面代替来源证明。', 'action': None})
    if not sources:
        gaps.append({'code': 'evidence', 'message': '尚无可关联的请求或源码证据。需要先完成一次授权分析。', 'action': 'run'})
    if category in INFORMATION_CATEGORIES:
        method = 'observation_review'
        gaps.append({'code': 'security_boundary', 'message': '目前属于信息观察，尚无证据证明具体安全边界被违反。', 'action': None})
    elif candidate['status'] == 'reproduced':
        method = 'impact_review'
        gaps.append({'code':'impact_review','message':'已复现观察行为；继续核对业务权限和实际影响，尚不等于正式漏洞。','action':None})
    elif candidate['mode'] == 'traditional' and category in HTTP_READ_CATEGORIES:
        method = 'http_object_read'
        from datetime import datetime, timezone
        def usable(item):
            if item['session_status'] != 'ready':
                return False
            if item['expires_at']:
                try:
                    return datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc)
                except (ValueError, TypeError):
                    return False
            return True
        ready = [x for x in data['identities'] if usable(x)]
        if len(ready) < 2:
            gaps.append({'code': 'identities', 'message': '请在测试账号中登录两个不同的授权账号。无需粘贴请求头。', 'action': 'identities'})
        gaps.append({'code': 'http_binding', 'message': '对象归属与身份接口尚未自动绑定；已整理请求材料，独立复验尚未执行。', 'action': None})
    elif candidate['mode'] == 'web3' and category == 'web3_property_violation':
        method = 'web3_property'
        gaps.append({'code': 'isolated_replay', 'message': '属性线索已整理；自动独立复测仍需接通隔离执行环境。', 'action': None})
    else:
        method = 'specialized'
        gaps.append({'code': 'unsupported_method', 'message': '已保存材料；这类问题的自动专用验证尚未接通。', 'action': None})
    draft = {'title': candidate['title'], 'target': candidate['target'],
             'observed_behavior': '\n'.join(x['text'] for x in sources[:8]) or None,
             'hypothesis': candidate['hypothesis'] or None,
             'hypothesis_origin': 'existing_candidate',
             'verification_method': method,
             'steps': ['核对来源与对象', '检查所需身份或源码', '运行适用的独立正反对照', '根据真实结果解释影响'],
             'impact': None, 'severity': None, 'root_cause': None}
    from guided_http import plan
    binding = plan(candidate, data['exchanges'], data['identities'])
    if binding:
        gaps = [gap for gap in gaps if gap['code'] != 'http_binding']
        draft['http_binding'] = binding
    return redact_structure({'candidate_id': candidate['id'], 'title': candidate['title'],
        'draft': draft, 'sources': sources[:30], 'source_count': len(sources),
        'missing_evidence_ids': sorted(linked - {x['id'] for x in evidence}),
        'status': 'awaiting_verification' if sources else 'needs_material', 'gaps': gaps,
        'automatically_filled': [key for key in ('title','target','observed_behavior','hypothesis','verification_method','steps') if draft[key]],
        'verified': False})


def hydrate(row):
    value = dict(row)
    for name in ('steps', 'result'):
        value[name] = core().load(value[name], {})
    value['cancel_requested'] = bool(value['cancel_requested'])
    for item in value.get('result', {}).get('items', []):
        independent = item.get('auto_verification', {}).get('independent_verification', {})
        if independent.get('receipt_id'):
            try:
                from v5_verification import get_receipt
                receipt = get_receipt(independent['receipt_id'])
                independent['current_inputs_match'] = receipt['integrity']['current_inputs_match']
                independent['promotion_eligible'] = receipt['integrity']['promotion_eligible']
                with core().connect() as db:
                    finding = db.execute('SELECT id FROM canonical_findings WHERE candidate_id=?', (item['candidate_id'],)).fetchone()
                if finding:
                    independent['finding_id'] = finding['id']
                if receipt['result']['classification'] == 'repaired_negative' and independent['promotion_eligible']:
                    try:
                        from v5_http_receipts import fixed_plan
                        plan = fixed_plan(independent['receipt_id'])
                        independent['fixed_finding_id'] = plan['finding_id']
                        with core().connect() as db:
                            lifecycle = db.execute('SELECT status FROM finding_lifecycle WHERE finding_id=?', (plan['finding_id'],)).fetchone()
                        independent['fixed_confirmed'] = bool(lifecycle and lifecycle['status'] == 'verified_fixed')
                    except Exception:
                        independent['fixed_confirmed'] = False
            except Exception:
                independent['current_inputs_match'] = False
                independent['promotion_eligible'] = False
    return redact_structure(value)


@router.get('/runs/{run_id}/candidate-workflow')
def candidate_workflow(run_id: str, after_candidate_id: str | None = None):
    """Preview a bounded batch without requests, workers or candidate mutation."""
    data = snapshot(run_id, after_candidate_id=after_candidate_id)
    items = [material_for(candidate, data) for candidate in data['candidates']]
    counts = annotate_workflow(data['candidates'], items)
    with core().connect() as db:
        latest = db.execute("""SELECT * FROM guided_research_jobs WHERE run_id=?
            AND json_extract(result,'$.reviewed_execution')=1 ORDER BY created_at DESC,id DESC LIMIT 1""", (run_id,)).fetchone()
    return {'run_id': run_id, 'counts': counts, 'items': items,
            'next_cursor': data['next_cursor'], 'truncated': data['truncated'],
            'source_fingerprint': fingerprint(data), 'automatic_execution': False,
            'execution_job': hydrate(latest) if latest else None,
            'boundary': 'Readiness is not authorization or a vulnerability verdict.'}


class WorkflowExecutionInput(BaseModel):
    source_fingerprint: str = Field(min_length=64, max_length=64, pattern=r'^[a-f0-9]{64}$')
    authorized: bool = False


def workflow_execution_plan(run_id, candidate_id):
    """No network traffic or credential resolution while reviewing a plan."""
    from http_authorization_policy import select_rule
    from guided_http import scalar
    data = snapshot(run_id, candidate_id)
    candidate = data['candidates'][0]
    item = material_for(candidate, data)
    annotate_workflow(data['candidates'], [item])
    engagement = core().get_engagement(candidate['engagement_id'])
    rule, rule_reason = select_rule(engagement['scope'], candidate['target'])
    binding = item['draft'].get('http_binding')
    blockers = list(item['triage']['blockers'])
    if not item['triage']['dispatch_eligible']:
        blockers.append('triage_not_ready')
    if not engagement.get('confirmed_at'):
        blockers.append('scope_unconfirmed')
    if not engagement['scope'].get('allow_authentication'):
        blockers.append('authentication_not_authorized')
    context = data['runtime_context']
    if context['run_scope'] != context['scope_id'] or context['run_policy'] != context['policy_id']:
        blockers.append('authority_changed')
    if context['run_status'] not in ('running', 'paused', 'completed'):
        blockers.append('run_not_executable')
    if rule is None:
        blockers.append('business_rule_' + rule_reason)
    elif rule['access'] == 'public':
        blockers.append('business_access_permitted')
    elif binding and rule['access'] == 'allowlist':
        probe = next((row for row in data['exchanges'] if row['id'] == binding['sources'][2]), None)
        principal = scalar(probe['response_body_preview'], binding['principal_field']) if probe else None
        if principal is None:
            blockers.append('business_principal_unknown')
        elif principal in rule.get('allowed_principals', []):
            blockers.append('business_access_permitted')
    budget = context.get('budget') or {}
    remaining = max(0, budget.get('request_limit', 0) - budget.get('requests_used', 0))
    if remaining < 10:
        blockers.append('request_budget')
    requests = []
    if binding:
        import ipaddress
        import sys
        from pathlib import Path
        if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').is_file():
            blockers.append('isolated_transport_unavailable')
        for target in (binding['target'], binding['identity_url']):
            host = urlsplit(target).hostname
            try:
                local = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local = host == 'localhost'
            if not local:
                blockers.append('isolated_transport_local_only')
            if not core().execution_policy_check(core().PolicyCheckInput(
                    engagement_id=engagement['id'], target=target, action='read'))['allowed']:
                blockers.append('out_of_scope')
        auth_hosts = engagement['scope'].get('auth_allowed_hosts', [])
        if any(urlsplit(target).hostname not in auth_hosts for target in (binding['target'], binding['identity_url'])):
            blockers.append('authentication_host')
        requests = [
            {'role': 'owner', 'url': binding['target'], 'identity_id': binding['owner_identity_id']},
            {'role': 'other', 'url': binding['target'], 'identity_id': binding['other_identity_id']},
            {'role': 'anonymous', 'url': binding['target'], 'identity_id': None},
            {'role': 'owner_identity', 'url': binding['identity_url'], 'identity_id': binding['owner_identity_id']},
            {'role': 'other_identity', 'url': binding['identity_url'], 'identity_id': binding['other_identity_id']},
        ]
    with core().connect() as db:
        if db.execute("SELECT 1 FROM guided_research_jobs WHERE run_id=? AND status IN ('queued','running')", (run_id,)).fetchone():
            blockers.append('active_job')
        if db.execute("SELECT 1 FROM verification_jobs WHERE candidate_id=? AND status IN ('queued','running','cancelling')", (candidate_id,)).fetchone():
            blockers.append('active_job')
    return redact_structure({
        'run_id': run_id, 'candidate_id': candidate_id, 'engagement_id': engagement['id'],
        'title': candidate['title'], 'source_fingerprint': fingerprint(data),
        'scope_snapshot_id': engagement['current_scope_snapshot_id'],
        'business_rule': rule, 'binding': binding, 'rounds': 2, 'request_count': 10,
        'remaining_requests': remaining, 'requests': requests,
        'blockers': sorted(set(blockers)), 'can_execute': not blockers,
        'sends_requests': False, 'automatic_promotion': False,
    })


@router.get('/runs/{run_id}/candidate-workflow/{candidate_id}/execution-plan')
def preview_workflow_execution(run_id: str, candidate_id: str):
    return workflow_execution_plan(run_id, candidate_id)


@router.post('/runs/{run_id}/candidate-workflow/{candidate_id}/execute', status_code=202)
def execute_workflow(run_id: str, candidate_id: str, body: WorkflowExecutionInput):
    if not body.authorized:
        raise HTTPException(422, '请先核对复验计划并确认测试授权')
    plan = workflow_execution_plan(run_id, candidate_id)
    if plan['source_fingerprint'] != body.source_fingerprint:
        raise HTTPException(409, '候选材料、身份或授权已变化，请重新检查执行计划')
    if not plan['can_execute']:
        raise HTTPException(409, {'message': '复验前置条件尚未满足', 'blockers': plan['blockers']})
    return create_job(run_id, StartInput(candidate_id=candidate_id, execute_ready=True),
                      expected_fingerprint=body.source_fingerprint)


@router.get('/guided-research/{job_id}')
def get_job(job_id: str):
    with core().connect() as db:
        row = db.execute('SELECT * FROM guided_research_jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, '自动整理任务不存在')
    return hydrate(row)


@router.get('/runs/{run_id}/guided-research')
def latest_job(run_id: str, candidate_id: str | None = None, batch_only: bool = False):
    with core().connect() as db:
        if batch_only and not candidate_id:
            active=db.execute("SELECT * FROM guided_research_jobs WHERE run_id=? AND status IN ('queued','running') LIMIT 1",(run_id,)).fetchone()
            if active:
                return hydrate(active)
        clause = ' AND (candidate_id=? OR candidate_id IS NULL)' if candidate_id else ' AND candidate_id IS NULL' if batch_only else ''
        args = (run_id, candidate_id) if candidate_id else (run_id,)
        row = db.execute('SELECT * FROM guided_research_jobs WHERE run_id=?' + clause + ' ORDER BY created_at DESC,id DESC LIMIT 1', args).fetchone()
    return hydrate(row) if row else None


@router.post('/guided-research/{job_id}/cancel')
def cancel_job(job_id: str):
    with core().connect() as db:
        row = db.execute('SELECT status FROM guided_research_jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, '自动整理任务不存在')
        if row['status'] in ACTIVE:
            db.execute('UPDATE guided_research_jobs SET cancel_requested=1,updated_at=? WHERE id=?', (core().utcnow(),job_id))
    return get_job(job_id)


class Cancelled(Exception):
    pass


def save(job_id, steps, result, phase, status='running'):
    f = core()
    with f.connect() as db:
        row = db.execute('SELECT cancel_requested,status FROM guided_research_jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['cancel_requested'] or row['status'] not in ACTIVE:
            raise Cancelled()
        db.execute('UPDATE guided_research_jobs SET status=?,steps=?,result=?,phase=?,updated_at=? WHERE id=?',
                   (status, f.dump(steps), f.dump(redact_structure(result)), phase, f.utcnow(), job_id))


def run_job(job_id, data):
    steps = [{'id': key, 'label': label, 'status': 'pending', 'count': 0} for key,label in STAGES]
    result = {'items': [], 'discovered': [], 'requests_sent': 0, 'verification_executed': False,
              'reviewed_execution': data.get('reviewed_execution', False),
              'remaining_candidates': data['remaining_candidates'], 'truncated': data['truncated'],
              'after_candidate_id':data.get('after_candidate_id'), 'next_cursor':data.get('next_cursor'),
              'continue_from':data.get('continue_from'), 'pending_verifications':0,
              'auto_continue':data.get('auto_continue',False),
              'automation_round':data.get('automation_round',1),
              'source_fingerprint': fingerprint(data), 'execute_ready': data.get('execute_ready', False), 'read_pages':data.get('read_pages',False), 'page_reads':[]}
    try:
        for index in range(5):
            steps[index]['status'] = 'running'
            save(job_id, steps, result, STAGES[index][1])
            if index == 0:
                steps[index]['count'] = len(data['candidates'])
            elif index == 1:
                seen = set()
                for exchange in data['exchanges']:
                    parser = PageLinks(exchange['url'])
                    parser.feed(exchange['response_body_preview'][:1000])
                    for item in parser.links:
                        if item['url'] in seen or len(result['discovered']) >= 100:
                            continue
                        seen.add(item['url'])
                        result['discovered'].append({**item, 'source_id': exchange['id'], 'status': 'discovered_only'})
                steps[index]['count'] = len(result['discovered'])
                if data.get('read_pages'):
                    from guided_pages import collect
                    with core().connect() as db:
                        histories=db.execute('SELECT result FROM guided_research_jobs WHERE run_id=? AND id!=?',(data['run_id'],job_id)).fetchall()
                    attempted=[read['url'] for row in histories for read in core().load(row['result'],{}).get('page_reads',[]) if read['status'] in ('requesting','recorded') or (read['status']=='not_completed' and read.get('attempted',True))]
                    def page_checkpoint(reads):
                        result['page_reads']=reads
                        result['requests_sent']=sum(bool(r.get('attempted')) for r in reads)
                        save(job_id,steps,result,f"正在读取页面，已记录 {sum(r['status']=='recorded' for r in reads)} 个")
                    collect(data['run_id'],result['discovered'],page_checkpoint,
                            lambda:save(job_id,steps,result,'检查页面读取范围与预算'),attempted)
                    fresh=snapshot(data['run_id'],data.get('candidate_id'),data['after_candidate_id']) if data.get('after_candidate_id') else snapshot(data['run_id'],data.get('candidate_id'))
                    data.update(fresh)

            elif index == 2:
                for candidate in data['candidates']:
                    result['items'].append(material_for(candidate, data))
                    steps[index]['count'] = len(result['items'])
                    save(job_id, steps, result, f"正在整理 {len(result['items'])}/{len(data['candidates'])} 条候选")
            elif index == 3:
                from guided_http import execute
                result['triage_counts'] = annotate_workflow(data['candidates'], result['items'])
                executed = 0
                for item in result['items']:
                    binding = item['draft'].get('http_binding')
                    if not item['triage']['dispatch_eligible'] or not binding or not data.get('execute_ready'):
                        continue
                    candidate=next(c for c in data['candidates'] if c['id']==item['candidate_id'])
                    input_digest = verification_input_digest(candidate, data, binding)
                    previous = None
                    with core().connect() as db:
                        histories = db.execute('SELECT result FROM guided_research_jobs WHERE run_id=? AND id!=? ORDER BY created_at DESC LIMIT 100', (data['run_id'],job_id)).fetchall()
                    for history in histories:
                        for old in core().load(history['result'],{}).get('items',[]):
                            check = old.get('auto_verification',{})
                            if old['candidate_id']==item['candidate_id'] and check.get('binding_hash')==binding['binding_hash'] and check.get('input_digest')==input_digest:
                                previous=check
                                break
                        if previous:
                            break
                    if previous:
                        item['auto_verification']={**previous,'reused':True}
                        continue
                    if data.get('continue_from') and item['candidate_id'] not in data['continuation_pending']:
                        item['gaps']=data.get('deferred_gaps',{}).get(item['candidate_id'],item['gaps'])
                        continue
                    if executed >= 3:
                        item['gaps'].append({'code':'round_limit','message':'本轮验证名额已用完，点击主按钮继续剩余验证。','action':None})
                        continue
                    candidate=next(c for c in data['candidates'] if c['id']==item['candidate_id'])
                    def before_request(round_index, role):
                        save(job_id,steps,result,f'自动复验第 {min(round_index+1,2)} 轮 · {role}')
                        if data.get('reviewed_execution'):
                            current = snapshot(data['run_id'], candidate['id'])
                            if verification_input_digest(current['candidates'][0], current, binding) != input_digest:
                                raise HTTPException(409, '复验材料或授权已变化，任务停止')
                    try:
                        executed += 1
                        def after_response():
                            result['requests_sent'] += 1
                            save(job_id,steps,result,f"已读取 {result['requests_sent']} 个验证响应")
                        execution_options = {'isolated_transport': True} if data.get('reviewed_execution') else {}
                        item['auto_verification']={**execute(candidate,binding,before_request,after_response,**execution_options), 'input_digest': input_digest}
                        result['verification_executed']=True
                        item['status']=item['auto_verification']['status']
                        item['gaps']=[{'code':'impact_review','message':'正反对照已执行；业务权限、影响与严重度尚需证据确认。','action':None}] if item['status']=='reproduced' else []
                    except Cancelled:
                        raise
                    except HTTPException as error:
                        from reporting import redact
                        item['gaps'].append({'code':'verification_failed','message':redact(str(error.detail)),'action':'identities' if getattr(error,'identity_id',None) else None})
                    except Exception:
                        item['gaps'].append({'code':'verification_failed','message':'自动复验未完成，请检查账号、授权范围与剩余预算。','action':None})
                    steps[index]['count'] += 1
                    save(job_id,steps,result,'保存自动复验结果')
            elif index == 4:
                if data.get('reviewed_execution'):
                    from v5_http_receipts import request_for_job, canonical_for_receipt
                    from v5_verification import local_verifier_tick, get_receipt
                    for item in result['items']:
                        outcome = item.get('auto_verification', {})
                        if not outcome.get('artifact_id'):
                            continue
                        try:
                            request = request_for_job(job_id, item['candidate_id'], outcome['artifact_id'])
                            outcome['independent_verification'] = {**request, 'status': 'queued'}
                            tick = local_verifier_tick(1, request['request_id'])
                            completed = next((entry for entry in tick['completed'] if entry.get('receipt_id')), None)
                            if completed:
                                receipt = get_receipt(completed['receipt_id'])
                                outcome['independent_verification'].update(status=receipt['result']['status'], receipt_id=receipt['id'])
                                canonical_id = canonical_for_receipt(receipt['id'])
                                if canonical_id:
                                    outcome['independent_verification']['canonical_result_id'] = canonical_id
                            elif tick['completed']:
                                outcome['independent_verification']['status'] = 'failed_attempt'
                        except Exception:
                            outcome['independent_verification'] = {'status': 'not_completed'}
                            item['gaps'].append({'code': 'independent_verification_failed', 'message': '独立判定尚未完成，已保留重放材料。', 'action': None})
                result['pending_verifications']=sum(any(gap['code']=='round_limit' for gap in item['gaps']) for item in result['items'])
                result['new_verifications']=sum('auto_verification' in item and not item['auto_verification'].get('reused') for item in result['items'])
                result['reused_verifications']=sum(bool(item.get('auto_verification',{}).get('reused')) for item in result['items'])
                result['summary'] = f"新增读取 {sum(r['status']=='recorded' for r in result['page_reads'])} 个页面。已整理 {len(result['items'])} 条候选材料，发现 {len(result['discovered'])} 个页面入口。" + (f"自动复验已完成 {result['new_verifications']} 条；正式影响仍需确认。" if result['verification_executed'] else '本轮未执行新的独立复验。')
            steps[index]['status'] = 'needs_input' if index == 3 and any(item['gaps'] or ('http_binding' in item['draft'] and not item.get('auto_verification')) for item in result['items']) else 'completed'
            if index == 1 and any(r['status']=='not_completed' for r in result['page_reads']):
                steps[index]['status']='needs_input'
            save(job_id, steps, result, STAGES[index][1])
        save(job_id, steps, result, '材料与验证结果已保存',
             'awaiting_input' if any(s['status']=='needs_input' for s in steps) else 'completed')
        followup = next_automation_input(job_id, data, result)
        if followup:
            create_job(data['run_id'], followup)
    except Cancelled:
        with core().connect() as db:
            for step in steps:
                if step['status'] in ('pending', 'running'):
                    step['status'] = 'cancelled'
            db.execute("UPDATE guided_research_jobs SET status='cancelled',steps=?,phase='已停止，保留已整理材料',updated_at=? WHERE id=? AND status IN ('queued','running')",
                       (core().dump(steps), core().utcnow(), job_id))
    except Exception:
        # No raw exceptions or source content in job errors.
        with core().connect() as db:
            for step in steps:
                if step['status'] == 'running':
                    step['status'] = 'failed'
            db.execute("UPDATE guided_research_jobs SET status='failed',steps=?,phase='材料整理失败，可继续重试',error='材料整理失败，可继续重试',updated_at=? WHERE id=? AND status IN ('queued','running')", (core().dump(steps),core().utcnow(),job_id))


@router.post('/runs/{run_id}/guided-research', status_code=202)
def start_job(run_id: str, body: StartInput):
    return create_job(run_id, body)


def next_automation_input(job_id, data, result):
    """Return the next bounded batch. Blockers remain visible and are never bypassed."""
    if not data.get('auto_continue'):
        return None
    current_round = int(data.get('automation_round', 1))
    if current_round >= MAX_AUTOMATION_ROUNDS:
        return None
    common = {'execute_ready': True, 'read_pages': False, 'auto_continue': True,
              'automation_round': current_round + 1}
    if result.get('pending_verifications'):
        return StartInput(**common, continue_from=job_id,
                          after_candidate_id=result.get('after_candidate_id'))
    if result.get('next_cursor'):
        return StartInput(**common, after_candidate_id=result['next_cursor'])
    return None


def create_job(run_id, body, initial_only=False, expected_fingerprint=None):
    f = core()
    data = snapshot(run_id, body.candidate_id, body.after_candidate_id) if body.after_candidate_id else snapshot(run_id, body.candidate_id)
    if expected_fingerprint is not None:
        if fingerprint(data) != expected_fingerprint:
            raise HTTPException(409, '执行计划已变化，请重新核对')
        data['reviewed_execution'] = True
    data['execute_ready'] = body.execute_ready
    data['read_pages'] = body.read_pages
    data['auto_continue'] = body.auto_continue
    data['automation_round'] = body.automation_round
    if body.continue_from:
        parent=get_job(body.continue_from)
        result=parent['result']
        if parent['run_id']!=run_id or parent['candidate_id']!=body.candidate_id or parent['status'] not in ('completed','awaiting_input') or result.get('after_candidate_id')!=body.after_candidate_id or not body.execute_ready:
            raise HTTPException(409,'当前批次不能从这条记录继续')
        pending=[item['candidate_id'] for item in result.get('items',[]) if any(gap['code']=='round_limit' for gap in item['gaps'])]
        if not pending:
            raise HTTPException(409,'这批没有排队中的验证项，请重新检查材料')
        data['continue_from']=body.continue_from
        data['continuation_pending']=pending
        data['deferred_gaps']={item['candidate_id']:item['gaps'] for item in result.get('items',[]) if item['candidate_id'] not in pending}
    digest = fingerprint({'continuation':body.continue_from,'run_id':run_id,'candidate_id':body.candidate_id,'after_candidate_id':body.after_candidate_id,'execute_ready':body.execute_ready,'read_pages':body.read_pages,'auto_continue':body.auto_continue,'automation_round':body.automation_round}) if body.continue_from else fingerprint(data)
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if initial_only:
            existing = db.execute('SELECT * FROM guided_research_jobs WHERE run_id=? ORDER BY created_at DESC,id DESC LIMIT 1', (run_id,)).fetchone()
            if existing:
                return {**hydrate(existing), 'reused': True}
            run = db.execute('SELECT status,synthetic FROM analysis_runs WHERE id=?', (run_id,)).fetchone()
            if not run or run['status'] != 'completed' or run['synthetic']:
                raise HTTPException(409, '分析尚未完成，自动衔接等待中')
        active = db.execute("SELECT * FROM guided_research_jobs WHERE run_id=? AND status IN ('queued','running')", (run_id,)).fetchone()
        if active:
            if expected_fingerprint is not None:
                raise HTTPException(409, '已有任务正在执行，请刷新当前任务状态')
            return {**hydrate(active), 'reused': True}
        existing = db.execute("SELECT * FROM guided_research_jobs WHERE run_id=? AND fingerprint=? AND status IN ('completed','awaiting_input') ORDER BY created_at DESC LIMIT 1", (run_id,digest)).fetchone()
        if existing:
            if not body.continue_from:
                # A plain recheck must not resurrect an old batch's queue after
                # its explicit continuation chain has already drained it.
                while True:
                    child=db.execute("SELECT * FROM guided_research_jobs WHERE run_id=? AND status IN ('completed','awaiting_input') AND json_extract(result,'$.continue_from')=? ORDER BY created_at DESC,id DESC LIMIT 1",(run_id,existing['id'])).fetchone()
                    if not child:
                        break
                    existing=child
            return {**hydrate(existing), 'reused': True}
        if db.execute("SELECT count(*) FROM guided_research_jobs WHERE status IN ('queued','running')").fetchone()[0] >= 4:
            raise HTTPException(409, '已有四项整理任务，请等待其中一项完成')
        job_id, now = f.uid('guided'), f.utcnow()
        steps = [{'id': key, 'label': label, 'status': 'pending', 'count': 0} for key,label in STAGES]
        db.execute('INSERT INTO guided_research_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (job_id,run_id,body.candidate_id,digest,'queued','等待整理',f.dump(steps),
                    f.dump({'reviewed_execution': True}) if expected_fingerprint is not None else '{}',0,None,now,now))
    try:
        dispatch(job_id, data)
    except Exception:
        with f.connect() as db:
            db.execute("UPDATE guided_research_jobs SET status='failed',phase='无法启动整理任务，可重试',error='worker_start_failed',updated_at=? WHERE id=?", (f.utcnow(),job_id))
    return get_job(job_id)


def dispatch(job_id, data):
    threading.Thread(target=run_job, args=(job_id,data), daemon=True, name=job_id).start()


@router.post('/engagements/{engagement_id}/quick-identities')
def quick_identities(engagement_id: str):
    """Create only missing account slots; never grant login permission or mark ready."""
    f = core()
    engagement = f.get_engagement(engagement_id)
    if engagement['mode'] != 'traditional' or not engagement.get('confirmed_at') or not engagement['scope'].get('allow_authentication'):
        raise HTTPException(409, '当前项目尚未授权测试账号登录，请先核对项目授权范围')
    with f.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        ids = [row['id'] for row in db.execute("SELECT i.id FROM identities i JOIN identity_profiles p ON p.identity_id=i.id WHERE i.engagement_id=? AND p.session_status!='disabled' ORDER BY i.created_at,i.id LIMIT 2", (engagement_id,))]
        while len(ids) < 2:
            identity_id, now = f.uid('identity'), f.utcnow()
            db.execute('INSERT INTO identities VALUES(?,?,?,?,?,?,?)',
                       (identity_id,engagement_id,f'测试账号 {len(ids)+1}','user',None,None,now))
            db.execute('INSERT INTO identity_profiles VALUES(?,?,?,?,?,?,?)',
                       (identity_id,'none','needs_login',None,None,'guided_account',now))
            ids.append(identity_id)
    return {'identities':[f.get_identity(identity_id) for identity_id in ids]}


def resume_after_identity(identity_id):
    """Resume only latest, explicitly enabled jobs awaiting this project's identities."""
    f = core()
    identity = f.get_identity(identity_id)
    with f.connect() as db:
        rows = db.execute("""SELECT j.* FROM guided_research_jobs j JOIN analysis_runs r ON r.id=j.run_id
            WHERE r.engagement_id=? AND r.status IN ('completed','paused','running')
            AND j.status='awaiting_input' AND j.cancel_requested=0
            AND NOT EXISTS (SELECT 1 FROM guided_research_jobs newer WHERE newer.run_id=j.run_id
                AND (newer.created_at>j.created_at OR (newer.created_at=j.created_at AND newer.id>j.id)))
            ORDER BY j.created_at DESC LIMIT 20""", (identity['engagement_id'],)).fetchall()
    resumed = []
    for row in rows:
        result = f.load(row['result'], {})
        if not result.get('execute_ready'):
            continue
        if not any(gap.get('code') in ('identities','http_binding','verification_failed') for item in result.get('items',[]) for gap in item.get('gaps',[])):
            continue
        try:
            job = start_job(row['run_id'], StartInput(candidate_id=row['candidate_id'],execute_ready=True,read_pages=result.get('read_pages',False),after_candidate_id=result.get('after_candidate_id')))
            resumed.append(job['id'])
        except HTTPException:
            # Login remains successful; budget/active-job conflicts stay in the task UI.
            continue
    return resumed


def start_completed_followups(limit=4):
    """Consume persisted launch intent independently of open pages, once per run.

    Existing interrupted/failed/cancelled jobs require an explicit retry. A restart
    may pick up a completed analysis with no job, never replay an uncertain job.
    """
    f = core()
    with f.connect() as db:
        rows = db.execute("""SELECT r.id FROM analysis_runs r JOIN run_configs_v2 c ON c.run_id=r.id
            WHERE r.status='completed' AND r.synthetic=0
            AND json_valid(c.config) AND json_extract(c.config,'$.guided_followup')=1
            AND NOT EXISTS (SELECT 1 FROM guided_research_jobs j WHERE j.run_id=r.id)
            ORDER BY r.created_at,r.id LIMIT ?""", (max(1,min(limit,4)),)).fetchall()
    results = []
    for row in rows:
        try:
            results.append(create_job(row['id'], StartInput(execute_ready=True,read_pages=True), initial_only=True)['id'])
        except HTTPException:
            # Capacity conflicts retain launch intent and retry on the next tick.
            continue
    return results
