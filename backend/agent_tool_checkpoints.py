"""Durable, hash-bound broker request preparation and pipe-delivery facts."""
import re
from fastapi import HTTPException
import final_core as core
from agent_tool_stream import digest,validate_call

BASE=('request_id','run_id','plan_hash','case','sequence','input_sha256','call','release_authorized','prepared_at')


def checked_records(request_id,execution):
    records=execution.get('tool_checkpoints',[])
    if not isinstance(records,list) or len(records)>8:raise ValueError('invalid checkpoint collection')
    counts={};seen=set()
    for record in records:
        if not isinstance(record,dict):raise ValueError('invalid checkpoint')
        validate_call(record['call'])
        case=record['case'];sequence=record['sequence']
        if (case not in ('control','tool') or type(sequence) is not int or sequence!=counts.get(case,0)+1 or sequence>4
            or record['request_id']!=request_id or record['run_id']!=execution['run_id']
            or record['plan_hash']!=execution['plan']['plan_hash'] or type(record['release_authorized']) is not bool
            or re.fullmatch('[a-f0-9]{64}',record['input_sha256']) is None
            or digest({key:record[key] for key in BASE})!=record['checkpoint_sha256']):raise ValueError('checkpoint provenance changed')
        identity=(case,record['call']['request']['id'])
        if identity in seen:raise ValueError('repeated checkpoint request id')
        seen.add(identity);counts[case]=sequence
        state=record['response_delivery']
        if state=='written_to_pipe':
            if not record['release_authorized'] or digest({'checkpoint_sha256':record['checkpoint_sha256'],'delivery':'written_to_pipe','observed_at':record['delivery_observed_at']})!=record['delivery_sha256']:
                raise ValueError('delivery digest changed')
        elif state not in ('unknown','withheld') or (state=='withheld')==record['release_authorized']:
            raise ValueError('invalid response delivery state')
    return records


def persist(request_id,event,release_check):
    if (not isinstance(event,dict) or event.get('case') not in ('control','tool') or event.get('phase') not in ('prepared','delivered')
        or type(event.get('sequence')) is not int or not 1<=event['sequence']<=4
        or not isinstance(event.get('input_sha256'),str) or re.fullmatch('[a-f0-9]{64}',event['input_sha256']) is None):
        raise ValueError('invalid checkpoint event')
    validate_call(event['call'])
    with core.connect() as db:
        db.execute('PRAGMA busy_timeout=250')
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(request_id,)).fetchone()
        if not row or row['scenario_id']!='tool' or row['status'] not in ('executing','cancelling'):raise RuntimeError('execution no longer accepts checkpoints')
        value=core.load(row['result_json']);execution=value['execution'];records=checked_records(request_id,execution)
        match=next((record for record in records if record['case']==event['case'] and record['sequence']==event['sequence']),None)
        now=core.utcnow()
        if event['phase']=='prepared':
            if match or len(records)>=8:raise ValueError('repeated checkpoint preparation')
            previous=[record for record in records if record['case']==event['case']]
            if (event['sequence']!=len(previous)+1 or previous and any(record['input_sha256']!=event['input_sha256'] for record in previous)):
                raise ValueError('checkpoint input or sequence changed')
            authorized=bool(release_check())
            record={'request_id':request_id,'run_id':execution['run_id'],'plan_hash':execution['plan']['plan_hash'],
                'case':event['case'],'sequence':event['sequence'],'input_sha256':event['input_sha256'],'call':event['call'],
                'release_authorized':authorized,'prepared_at':now}
            record['checkpoint_sha256']=digest(record)
            record['response_delivery']='unknown' if authorized else 'withheld'
            records.append(record);match=record
        else:
            if not match or match['call']!=event['call'] or match['input_sha256']!=event['input_sha256'] or match['response_delivery']!='unknown' or not match['release_authorized']:
                raise ValueError('delivery has no acknowledged preparation')
            authorized=True;match['response_delivery']='written_to_pipe';match['delivery_observed_at']=now
            match['delivery_sha256']=digest({'checkpoint_sha256':match['checkpoint_sha256'],'delivery':'written_to_pipe','observed_at':now})
        execution['tool_checkpoints']=records;execution['known_tool_requests']=len(records)
        execution['unknown_response_deliveries']=sum(record['response_delivery']=='unknown' for record in records)
        checked_records(request_id,execution)
        db.execute('UPDATE agent_attack_tests_v5 SET result_json=? WHERE id=?',(core.dump(value),request_id))
        db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
            ('agent_attack',None,request_id,'agent_attack.tool_response_'+event['phase'],core.dump({'run_id':execution['run_id'],
                'checkpoint_sha256':match['checkpoint_sha256'],'case':event['case'],'sequence':event['sequence']}),now))
    return authorized


def read(request_id):
    with core.connect() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT * FROM agent_attack_tests_v5 WHERE id=?',(request_id,)).fetchone()
        if not row:raise HTTPException(404,'Attack request not found')
        execution=core.load(row['result_json'],{}).get('execution',{})
        try:records=checked_records(request_id,execution)
        except (ValueError,KeyError,TypeError):raise HTTPException(409,'Saved checkpoint integrity check failed') from None
        budget=db.execute('SELECT tool_calls_used FROM run_budgets_v2 WHERE run_id=?',(execution.get('run_id'),)).fetchone()
        return {'request_id':request_id,'run_id':execution.get('run_id'),'status':row['status'],'checkpoints':records,
            'adapter_invocations_used':budget[0] if budget else None,
            'known_request_count':len(records),'actual_total_request_count':None,
            'coverage':'saved checkpoint prefix only; missing completion never establishes zero requests',
            'automatic_replay':False,'independently_verified':False,'finding_issued':False}
