import sqlite3
from tests.test_final import client
from tests.test_v6_capabilities import _intent,_grant
from tests.test_v6_intents import _setup
import final_core as core
from v6_capabilities import capability_matches
from v6_policy import record_policy_decision


def review(client,grant):
    result=client.get('/api/v1/v6/capability-grants/'+grant['id']+'/approval-preview')
    assert result.status_code==200,result.text
    return result.json()


def decide(client,grant,preview,decision='approve',**changes):
    return client.post('/api/v1/v6/capability-grants/'+grant['id']+'/approval',json={
        'decision':decision,'preview_sha256':preview['preview_sha256'],'confirmed':True,
        'reason':'Operator reviewed exact intent and limits',**changes})


def test_approval_is_immutable_audited_and_does_not_execute(client):
    _,task=_setup(client)
    intent=_intent(client,task['id'],risk_hint=60)
    grant=_grant(client,intent['id'],resource_pattern='https://intent.example.test/*')
    preview=review(client,grant)
    assert preview['can_approve'] and not preview['eligible']
    before=core.DB.read_bytes()
    assert review(client,grant)==preview and core.DB.read_bytes()==before
    assert decide(client,grant,preview,confirmed=False).status_code==409
    assert decide(client,grant,preview,confirmed=1).status_code==422
    assert decide(client,grant,preview,command='shell').status_code==422
    response=decide(client,grant,preview)
    assert response.status_code==200,response.text
    assert not response.json()['executed']
    assert decide(client,grant,preview).status_code==409
    with core.connect() as db:
        assert capability_matches(db,intent['id'],grant['id'],principal_id=intent['principal_id'])==(True,'eligible_only')
        assert db.execute('SELECT approval_state FROM capability_grants_v6 WHERE id=?',(grant['id'],)).fetchone()[0]=='pending_approval'
        assert db.execute('SELECT COUNT(*) FROM runtime_calls').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM capability_uses_v6').fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM v5_events WHERE event_type='capability.reviewed'").fetchone()[0]==1
        try:db.execute("UPDATE capability_approvals_v6 SET decision='reject'")
        except sqlite3.IntegrityError:pass
        else:raise AssertionError('Approval must remain immutable')
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        allowed=record_policy_decision(db,intent_id=intent['id'],grant_id=grant['id'],principal_id=intent['principal_id'],legacy_guard_allowed=True,legacy_rule_id='fixture.safe',budget_ok=True)
        assert allowed['decision']=='allow_with_limit'
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        blocked=record_policy_decision(db,intent_id=intent['id'],grant_id=grant['id'],principal_id=intent['principal_id'],legacy_guard_allowed=True,legacy_rule_id='fixture.safe',budget_ok=False)
        assert blocked['decision']=='deny'
    with core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        quarantine=record_policy_decision(db,intent_id=intent['id'],grant_id=grant['id'],principal_id=intent['principal_id'],legacy_guard_allowed=True,legacy_rule_id='fixture.safe',budget_ok=True,classifier_risk=90)
        assert quarantine['decision']=='quarantine'
    client.post('/api/v1/v6/capability-grants/'+grant['id']+'/revoke',json={'reason_code':'operator_revoked'})
    with core.connect() as db:assert not capability_matches(db,intent['id'],grant['id'],principal_id=intent['principal_id'])[0]


def test_unsupported_writes_quarantine_stale_preview_and_rejection(client):
    _,task=_setup(client)
    intent=_intent(client,task['id'],capability='email.send',operation='send',resource='mail:recipient',side_effect=True)
    grant=_grant(client,intent['id'])
    preview=review(client,grant)
    assert not preview['can_approve'] and preview['can_reject']
    assert decide(client,grant,preview).status_code==409
    assert decide(client,grant,preview,'reject').status_code==200
    safe=_intent(client,task['id'],risk_hint=90)
    high=_grant(client,safe['id'])
    assert not review(client,high)['can_approve']
    assert decide(client,high,review(client,high)).status_code==409
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'],resource_pattern='https://intent.example.test/*')
    preview=review(client,grant)
    with core.connect() as db:db.execute("UPDATE agent_tasks SET status='paused' WHERE id=?",(task['id'],))
    assert decide(client,grant,preview).status_code==409
    assert not review(client,grant)['can_approve']


def test_pending_expired_or_exhausted_grants_cannot_be_approved(client):
    from datetime import datetime,timedelta,timezone
    _,task=_setup(client)
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'],resource_pattern='https://intent.example.test/*')
    with core.connect() as db:
        db.execute('INSERT INTO capability_uses_v6 VALUES(?,?,?,?,?)',('used',grant['id'],intent['id'],'fixture-used',core.utcnow()))
    preview=review(client,grant)
    assert not preview['can_approve'] and preview['reason_code']=='usage_exhausted'
    assert decide(client,grant,preview).status_code==409
    with core.connect() as db:
        future=datetime.now(timezone.utc)+timedelta(hours=1)
        assert capability_matches(db,intent['id'],grant['id'],principal_id=intent['principal_id'],at=future)==(False,'expired')


def test_rejection_blocks_active_grant_and_unsupported_operation_cannot_approve(client):
    _,task=_setup(client)
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'])
    assert decide(client,grant,review(client,grant),'reject').status_code==200
    with core.connect() as db:
        assert capability_matches(db,intent['id'],grant['id'],principal_id=intent['principal_id'])==(False,'approval_rejected')
    wrong=_intent(client,task['id'],operation='delete')
    wrong_grant=_grant(client,wrong['id'])
    preview=review(client,wrong_grant)
    assert not preview['can_approve']
    assert decide(client,wrong_grant,preview).status_code==409


def test_schema_40_upgrade_backs_up_and_preserves_existing_grants(client,tmp_path):
    import lifecycle
    from v6_schema import apply_v6_schema
    _,task=_setup(client)
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'])
    database=tmp_path/'upgrade.db'
    with sqlite3.connect(core.DB) as source,sqlite3.connect(database) as target:source.backup(target)
    with sqlite3.connect(database) as db:
        db.execute('DROP TABLE capability_approvals_v6')
        db.execute('PRAGMA user_version=40')
        db.execute("UPDATE app_metadata SET value='40' WHERE key='schema_version'")
        before=db.execute('SELECT * FROM capability_grants_v6 ORDER BY id').fetchall()
    preparation=lifecycle.prepare_database_upgrade(database,tmp_path/'backups',core.ROOT)
    assert preparation['previous_schema']==40 and preparation['target_schema']==41 and preparation['backup']
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==41
        assert db.execute('SELECT * FROM capability_grants_v6 ORDER BY id').fetchall()==before
        assert db.execute('SELECT COUNT(*) FROM capability_approvals_v6').fetchone()[0]==0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'


def test_operator_approval_binds_worker_grant_without_becoming_worker_identity(client):
    from tests.test_v6_model_gateway import _reserved_research_call
    from v6_intents import LOCAL_SESSION_PRINCIPAL
    call,binding,task=_reserved_research_call(client)
    with core.connect() as db:
        grant=dict(db.execute('SELECT * FROM capability_grants_v6 WHERE id=?',(binding['grant_id'],)).fetchone())
    preview=review(client,grant)
    assert preview['can_approve'] and preview['intent']['principal_id']!=LOCAL_SESSION_PRINCIPAL
    assert decide(client,grant,preview).status_code==200
    with core.connect() as db:
        intent=db.execute('SELECT * FROM action_intents_v6 WHERE id=?',(binding['intent_id'],)).fetchone()
        db.execute('BEGIN IMMEDIATE')
        decision=record_policy_decision(db,intent_id=intent['id'],grant_id=grant['id'],principal_id=intent['principal_id'],legacy_guard_allowed=True,legacy_rule_id='fixture.worker',budget_ok=True,classifier_risk=60)
        assert decision['decision']=='allow_with_limit' and 'approval.operator' in decision['reasons']
        assert db.execute('SELECT principal_id FROM capability_approvals_v6').fetchone()[0]==LOCAL_SESSION_PRINCIPAL
        assert db.execute('SELECT state FROM runtime_calls WHERE id=?',(call['id'],)).fetchone()[0]=='reserved'
        assert db.execute('SELECT COUNT(*) FROM capability_uses_v6').fetchone()[0]==0


def test_approval_event_is_linked_to_its_task_and_run(client):
    _,task=_setup(client)
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'])
    assert decide(client,grant,review(client,grant)).status_code==200
    result=client.get('/api/v1/workspace/records/events',params={'task_id':task['id']})
    assert result.status_code==200,result.text
    matches=[item for item in result.json()['items'] if item['type']=='capability.reviewed']
    assert len(matches)==1 and matches[0]['task_id']==task['id'] and matches[0]['run_id']==task['run_id']


def test_capability_inventory_shows_operator_decision_without_rewriting_original_state(client):
    _,task=_setup(client)
    intent=_intent(client,task['id'])
    grant=_grant(client,intent['id'],resource_pattern='https://intent.example.test/*')
    before=client.get('/api/v1/workspace/agents/capabilities').json()['items'][0]
    assert before['approval_state']=='pending_approval' and before['operator_decision'] is None
    response=decide(client,grant,review(client,grant))
    assert response.status_code==200
    after=client.get('/api/v1/workspace/agents/capabilities').json()['items'][0]
    assert after['approval_state']=='pending_approval' and after['operator_decision']=='approve'
    assert after['approval_id']==response.json()['id'] and after['reviewed_at']
    second=_grant(client,intent['id'])
    assert decide(client,second,review(client,second),'reject').status_code==200
    records=client.get('/api/v1/workspace/agents/capabilities').json()['items']
    assert next(row for row in records if row['id']==second['id'])['operator_decision']=='reject'
