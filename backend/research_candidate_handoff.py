"""Reviewed draft -> unverified candidate; source polarity is never rewritten."""
import hashlib
from fastapi import HTTPException
import final_core as core
from candidate_quality import security_category

COUNTERS={'negative','contradicts','counterevidence'}


def sha(value):
    return hashlib.sha256(core.dump(value).encode()).hexdigest()


def plan(db,task_id,claim_id,target=None):
    from v5_research_worker import read_research_draft
    review=read_research_draft(task_id)
    if not review['inputs_current'] or not review['registered_sources_verified']:
        raise HTTPException(409,'Draft graph or registered sources are not current and verified')
    claim=next((node for node in review['drafts'] if node['id']==claim_id and node['node_type']=='claim'),None)
    if not claim:raise HTTPException(404,'Saved claim not found in this task')
    task=db.execute('SELECT * FROM agent_tasks WHERE id=?',(task_id,)).fetchone()
    if task['status']!='succeeded':raise HTTPException(409,'Research task did not complete successfully')
    run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(task['run_id'],)).fetchone()
    campaign=db.execute('SELECT engagement_id,status FROM research_campaigns WHERE id=?',(task['campaign_id'],)).fetchone()
    if not run or not campaign or campaign['engagement_id']!=run['engagement_id'] or campaign['status']!='active':
        raise HTTPException(409,'Research campaign is archived or does not own this Run')
    project=db.execute('''SELECT e.*,s.confirmed_at,s.rules AS scope_rules,p.policy AS policy_rules
        FROM engagements_v2 e JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
        JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id WHERE e.id=?''',(run['engagement_id'],)).fetchone()
    if (not project or project['status']!='ready' or not project['confirmed_at'] or run['synthetic']
        or run['mode']!='traditional' or run['mode']!=project['mode'] or run['status'] not in {'queued','running','paused','completed'}
        or run['scope_snapshot_id']!=project['current_scope_snapshot_id'] or run['policy_id']!=project['current_policy_id']):
        raise HTTPException(409,'Candidate handoff requires current confirmed Traditional authority')
    attrs=claim['attributes'];inputs={node['id']:node for node in review['inputs']}
    support=attrs.get('evidence_ids');counters=attrs.get('counterevidence_ids',[])
    if (not isinstance(support,list) or not support or not isinstance(counters,list)
        or any(not isinstance(x,str) for x in support+counters) or len(set(support+counters))!=len(support+counters)
        or set(counters)!={node['id'] for node in review['inputs'] if node['node_type']=='counterevidence'}):
        raise HTTPException(409,'Claim support or counterevidence references changed')
    observations={};evidence={};targets=set();categories=set()
    for node_id in support+counters:
        node=inputs.get(node_id)
        if not node or node.get('run_id')!=run['id']:
            raise HTTPException(409,'Candidate sources must belong to the bound Run')
        is_counter=node_id in counters
        if is_counter and node['node_type']!='counterevidence':raise HTTPException(409,'Counterevidence type changed')
        if not is_counter and node['node_type'] not in {'observation','evidence'}:raise HTTPException(409,'Support type changed')
        if node['source_type']=='observation' and not is_counter:
            row=db.execute('SELECT * FROM observations WHERE id=? AND run_id=?',(node['source_ref'],run['id'])).fetchone()
        elif node['source_type']=='evidence_v2':
            original=db.execute('SELECT * FROM evidence_v2 WHERE id=? AND run_id=?',(node['source_ref'],run['id'])).fetchone()
            if not original or (str(original['polarity']).lower() in COUNTERS)!=is_counter:
                raise HTTPException(409,'Registered evidence polarity changed')
            evidence[original['id']]=dict(original)
            row=db.execute('SELECT * FROM observations WHERE id=? AND run_id=?',(original['observation_id'],run['id'])).fetchone()
        else:raise HTTPException(409,'Claim source has no supported registered mapping')
        if row and (row['engagement_id']!=project['id'] or row['mode']!=run['mode']):
            raise HTTPException(409,'Observation does not belong to this research project')
        if row and not is_counter and row['observation_type']!='http.ownership_denial':
            observations[row['id']]=dict(row)
            try:allowed=core.execution_policy_check(core.PolicyCheckInput(engagement_id=project['id'],target=row['subject'],action='read'))['allowed']
            except (HTTPException,ValueError):allowed=False
            if allowed:targets.add(row['subject'])
            category=security_category(row)
            if category:categories.add(category)
    targets=sorted(targets)
    chosen=target or (targets[0] if len(targets)==1 else None)
    if chosen is not None and chosen not in targets:raise HTTPException(409,'Target is not a scoped supporting observation')
    category=next(iter(categories)) if len(categories)==1 else 'research_hypothesis'
    if category=='research_hypothesis' and chosen and any(row['observation_type']=='http.ownership_projection' and row['source_capability']=='guided_login_projection' for row in observations.values()):
        from http_authorization_policy import select_rule
        rule,_=select_rule(core.load(project['scope_rules']),chosen)
        if rule is not None:category='authorization'
    hypothesis=core.dump({'statement':claim['body'],'scope':attrs.get('scope'),
                          'limitations':attrs.get('limitations',[]),'counterevidence_graph_ids':counters,
                          'source_task_id':task_id,'source_claim_id':claim_id,'independently_verified':False})
    proposal={'title':claim['title'],'category':category,'target':chosen,'hypothesis':hypothesis}
    frozen={'review':review,'observations':observations,'evidence':evidence,'proposal':proposal,
            'scope':project['scope_rules'],'policy':project['policy_rules']}
    candidate_id='candidate-'+sha({'task':task_id,'claim':claim_id,'target':chosen})[:24]
    return {'preview_hash':sha(frozen),'task_id':task_id,'claim_id':claim_id,'run_id':run['id'],
            'candidate_id':candidate_id,'proposal':proposal,'targets':targets,'can_create':bool(chosen),
            'support_observation_ids':sorted(observations),'evidence_ids':sorted(evidence),
            'counterevidence_ids':sorted(key for key,row in evidence.items() if str(row['polarity']).lower() in COUNTERS),
            'scope_snapshot_id':run['scope_snapshot_id'],'policy_id':run['policy_id'],
            'executes_tools':False,'independently_verified':False,'_observations':observations}


def public_plan(value):
    return {key:val for key,val in value.items() if not key.startswith('_')}


def create(task_id,claim_id,target,preview_hash):
    from campaign_materials import access
    with access(),core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        return create_in_transaction(db,task_id,claim_id,target,preview_hash)


def create_in_transaction(db,task_id,claim_id,target,preview_hash,*,policy_revision=None):
    reviewed=plan(db,task_id,claim_id,target)
    if not reviewed['can_create'] or reviewed['preview_hash']!=preview_hash:
        raise HTTPException(409,'Candidate proposal or sources changed; review again')
    identifiers=set(reviewed['evidence_ids']);now=core.utcnow()
    for identifier,row in reviewed['_observations'].items():
        # Existing references keep their original polarity; only raw support observations need a registration.
        if db.execute("SELECT 1 FROM evidence_v2 WHERE run_id=? AND id IN (SELECT value FROM json_each(?)) AND observation_id=? AND lower(polarity) NOT IN ('negative','contradicts','counterevidence')",
                      (reviewed['run_id'],core.dump(sorted(identifiers)),identifier)).fetchone():continue
        evidence_id='evidence-'+sha({'claim':claim_id,'observation':identifier})[:24]
        db.execute('INSERT OR IGNORE INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)',
                   (evidence_id,identifier,reviewed['run_id'],row['observation_type'],row['summary'],row['raw_ref'],'supporting',now))
        registered=db.execute('SELECT * FROM evidence_v2 WHERE id=?',(evidence_id,)).fetchone()
        if registered['observation_id']!=identifier or registered['run_id']!=reviewed['run_id'] or registered['polarity']!='supporting' or registered['artifact_id']!=row['raw_ref'] or registered['summary']!=row['summary']:
            raise HTTPException(409,'Candidate evidence registration changed')
        identifiers.add(evidence_id)
    proposal=reviewed['proposal'];evidence_ids=sorted(identifiers)
    existing=db.execute('SELECT * FROM candidate_findings WHERE id=?',(reviewed['candidate_id'],)).fetchone()
    if existing:
        if any(existing[k]!=proposal[k] for k in ('title','category','target','hypothesis')) or core.load(existing['evidence_ids'])!=evidence_ids:
            raise HTTPException(409,'Candidate already exists with changed material')
    else:
        run=db.execute('SELECT * FROM analysis_runs WHERE id=?',(reviewed['run_id'],)).fetchone()
        db.execute('INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (reviewed['candidate_id'],run['id'],run['engagement_id'],run['mode'],proposal['title'],proposal['category'],proposal['target'],proposal['hypothesis'],'candidate',core.dump(evidence_ids),now,now))
        db.execute('INSERT INTO run_events_v2(run_id,stage,kind,message,payload,created_at) VALUES(?,?,?,?,?,?)',
            (run['id'],'verification','research.candidate_reviewed','Policy-approved draft saved as an unverified candidate' if policy_revision else 'Reviewed draft saved as an unverified candidate',
             core.dump({'task_id':task_id,'claim_id':claim_id,'candidate_id':reviewed['candidate_id'],'preview_hash':preview_hash,'approval_source':'resume_policy' if policy_revision else 'manual_review','policy_revision':policy_revision}),now))
    if plan(db,task_id,claim_id,target)['preview_hash']!=preview_hash:
        raise HTTPException(409,'Candidate proposal changed during creation; no candidate saved')
    return {'id':reviewed['candidate_id'],'run_id':reviewed['run_id'],'evidence_ids':evidence_ids,
            'status':existing['status'] if existing else 'candidate','independently_verified':False,'created':not bool(existing)}
