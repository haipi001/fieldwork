"""Reviewed, bounded projection of one authorized Run into a new Campaign.

Reads registered bytes, never executes tools or promotes conclusions. Private
inputs remain private. Other Runs and Campaigns are never modified.
"""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from fastapi import HTTPException
import final_core as core
from proof_materials import read_proof_file

MAX_ROWS=500
MAX_ARTIFACTS=100
MAX_BYTES=8_000_000


@contextmanager
def access():
    from storage_access import storage_access
    try:
        with storage_access(core.LOCAL_DATA_ROOT):
            yield
    except (OSError,ValueError,RuntimeError):
        raise HTTPException(409,'Recorded material storage is unavailable or under maintenance') from None


def plan(db, engagement_id, run_id):
    project=db.execute('SELECT e.*,s.confirmed_at,s.rules AS scope_rules,p.policy AS policy_rules FROM engagements_v2 e JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id WHERE e.id=?',(engagement_id,)).fetchone()
    run=db.execute('SELECT * FROM analysis_runs WHERE id=? AND engagement_id=?',(run_id,engagement_id)).fetchone()
    if not project or not run:raise HTTPException(404,'Run not found in this project')
    if (run['mode']!=project['mode'] or project['status']!='ready' or not project['confirmed_at'] or run['synthetic']
        or run['scope_snapshot_id']!=project['current_scope_snapshot_id'] or run['policy_id']!=project['current_policy_id']
        or run['status'] not in {'queued','running','paused','completed'}):
        raise HTTPException(409,'Recorded inputs require a real Run under the current confirmed authority')
    observations=[dict(r) for r in db.execute('SELECT * FROM observations WHERE run_id=? AND engagement_id=? ORDER BY id LIMIT ?', (run_id,engagement_id,MAX_ROWS+1))]
    evidence=[dict(r) for r in db.execute('SELECT * FROM evidence_v2 WHERE run_id=? ORDER BY id LIMIT ?',(run_id,MAX_ROWS+1))]
    artifacts=[dict(r) for r in db.execute('SELECT * FROM artifacts WHERE run_id=? ORDER BY id LIMIT ?',(run_id,MAX_ARTIFACTS+1))]
    if len(observations)>MAX_ROWS or len(evidence)>MAX_ROWS or len(artifacts)>MAX_ARTIFACTS:
        raise HTTPException(409,'Run materials exceed the bounded Campaign import; no records imported')
    by_artifact={r['id']:r for r in artifacts}
    total=0
    for artifact in artifacts:
        try:
            path=Path(artifact['uri']);path.relative_to(core.LOCAL_DATA_ROOT)
            raw=read_proof_file(path,min(2_000_000,MAX_BYTES-total))
        except (OSError,ValueError,TypeError):raise HTTPException(409,'Registered artifact location, size or bytes cannot be verified') from None
        total+=len(raw)
        if not artifact['sha256'] or hashlib.sha256(raw).hexdigest()!=artifact['sha256']:
            raise HTTPException(409,'Registered artifact bytes changed; no records imported')
        if artifact['kind']=='http.ownership_projection':
            from guided_capture import projection_current
            try:current=projection_current(db,run_id,json.loads(raw))
            except (ValueError,TypeError):current=False
            if not current:raise HTTPException(409,'Registered HTTP projection changed; no records imported')
    selected=[];excluded=[]
    for row in observations:
        if row['mode']!=run['mode']:raise HTTPException(409,'Observation mode does not match this Run')
        if not row['raw_ref']:
            excluded.append({'kind':'observation','id':row['id'],'reason':'no_registered_artifact'});continue
        if row['raw_ref'] not in by_artifact:raise HTTPException(409,'Observation refers outside the selected Run artifacts')
        selected.append(row)
    by_observation={r['id']:r for r in selected}
    all_observations={r['id'] for r in observations}
    selected_evidence=[]
    for row in evidence:
        if row['artifact_id'] and row['artifact_id'] not in by_artifact:raise HTTPException(409,'Evidence artifact belongs outside the selected Run')
        if row['observation_id'] and row['observation_id'] not in all_observations:
            raise HTTPException(409,'Evidence observation belongs outside the selected Run')
        if row['observation_id'] and row['observation_id'] not in by_observation and not row['artifact_id']:
            excluded.append({'kind':'evidence','id':row['id'],'reason':'observation_not_importable'});continue
        if not row['artifact_id'] and not row['observation_id']:
            excluded.append({'kind':'evidence','id':row['id'],'reason':'no_registered_source'});continue
        selected_evidence.append(row)
    sources={'run_id':run_id,'engagement_id':engagement_id,'scope_snapshot_id':run['scope_snapshot_id'],'policy_id':run['policy_id'],
             'scope_sha256':hashlib.sha256(project['scope_rules'].encode()).hexdigest(),
             'policy_sha256':hashlib.sha256(project['policy_rules'].encode()).hexdigest(),
             'artifacts':artifacts,'observations':selected,'evidence':selected_evidence,'excluded':excluded}
    digest=hashlib.sha256(core.dump(sources).encode()).hexdigest()
    counters=sum(str(r['polarity']).lower() in {'negative','contradicts','counterevidence'} for r in selected_evidence)
    return {'preview_hash':digest,'run_id':run_id,'counts':{'artifacts':len(artifacts),'observations':len(selected),
            'evidence':len(selected_evidence)-counters,'counterevidence':counters,'excluded':len(excluded)},
            'excluded':excluded,'verified_bytes':total,'sensitivity':'private','executes_tools':False,'sources':sources}


def public_plan(value):
    return {key:value[key] for key in ('preview_hash','run_id','counts','excluded','verified_bytes','sensitivity','executes_tools')}


def project(db,campaign_id,value):
    from v5_graph import _bridge_node,_bridge_edge,_emit
    sources=value['sources'];artifact_refs={};observation_refs={};nodes=edges=0
    for row in sources['artifacts']:
        nid,made=_bridge_node(db,campaign_id,source_type='artifact',source_ref=row['id'],node_type='artifact',
            title='Artifact · '+row['kind'],body='Binary content is not copied into Research Graph.',status='recorded',run_id=value['run_id'],
            attributes={'sha256':row['sha256'],'media_type':row['media_type'],'redacted':bool(row['redacted']),'content_copied':False,'sensitivity':'private',
                        **({'ownership_projection':True} if row['kind']=='http.ownership_projection' else {})})
        artifact_refs[row['id']]=nid;nodes+=int(made)
    for row in sources['observations']:
        ownership=row['observation_type'] in {'http.ownership_projection','http.ownership_denial'}
        nid,made=_bridge_node(db,campaign_id,source_type='observation',source_ref=row['id'],node_type='observation',
            title='HTTP ownership projection · '+row['subject'] if ownership else row['summary'],
            body=row['summary'] if ownership else row['subject'],status='observed',run_id=value['run_id'],confidence=row['confidence'],
            attributes={'mode':row['mode'],'observation_type':row['observation_type'],'source_capability':row['source_capability'],
                        'artifact_ref':row['raw_ref'],'sensitivity':'private',
                        **({'ownership_projection':True,'subject':row['subject']} if ownership else {})})
        observation_refs[row['id']]=nid;nodes+=int(made)
        edges+=int(_bridge_edge(db,campaign_id,nid,artifact_refs[row['raw_ref']],'derived_from'))
    for row in sources['evidence']:
        counter=str(row['polarity']).lower() in {'negative','contradicts','counterevidence'}
        nid,made=_bridge_node(db,campaign_id,source_type='evidence_v2',source_ref=row['id'],node_type='counterevidence' if counter else 'evidence',
            title=row['summary'],body='',status='recorded',run_id=value['run_id'],attributes={'evidence_type':row['evidence_type'],
            'polarity':row['polarity'],'observation_id':row['observation_id'],'artifact_id':row['artifact_id'],
            'observation_linked':row['observation_id'] in observation_refs,'sensitivity':'private'})
        nodes+=int(made)
        for source,lookup in ((row['observation_id'],observation_refs),(row['artifact_id'],artifact_refs)):
            if source in lookup:edges+=int(_bridge_edge(db,campaign_id,nid,lookup[source],'derived_from'))
    _emit(db,campaign_id,value['run_id'],'campaign.recorded_inputs_imported',{'preview_hash':value['preview_hash'],'counts':value['counts']})
    return {'run_id':value['run_id'],'counts':value['counts'],'nodes':nodes,'edges':edges,'sensitivity':'private'}
