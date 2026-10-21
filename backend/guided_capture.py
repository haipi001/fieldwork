"""Keep only observed ownership fields needed by the guided read verifier."""
import hashlib
import json
import re
from urllib.parse import urlsplit


def project_response(url, target, method, status, body):
    parsed,base=urlsplit(url),urlsplit(target)
    if method!='GET' or status not in (200,401,403) or len(body)>65536 or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if (parsed.scheme,parsed.netloc)!=(base.scheme,base.netloc):return None
    if status in (401,403):
        return {'url':url,'status':status,'preview':'{}','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}
    try:value=json.loads(body)
    except (ValueError,UnicodeError):return None
    def keep(item,depth=0):
        if not isinstance(item,dict) or depth>2:return {}
        result={}
        for key,child in item.items():
            if key in ('id','sub','user_id','owner_id') and type(child) in (str,int) and len(str(child))<=160:
                result[key]=child
            elif key in ('data','user','owner'):
                nested=keep(child,depth+1)
                if nested:result[key]=nested
        return result
    projected=keep(value)
    if not projected:return None
    return {'url':url,'status':status,'preview':json.dumps(projected,separators=(',',':')),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}


OWNERSHIP_PATHS=('id','sub','user_id','data.id','user.id','owner_id','data.owner_id','owner.id','data.owner.id')


def ownership_fingerprints(preview):
    """Equality fingerprints only, still private data; never an anonymity claim."""
    from guided_http import scalar
    return {path:hashlib.sha256(value.encode()).hexdigest() for path in OWNERSHIP_PATHS
            if (value:=scalar(preview,path)) is not None}


def projection_metadata(exchange_id,run_id,identity_id,url,result):
    if (type(result['status']) is not int or result['status'] not in (200,401,403) or type(result['body_bytes']) is not int
        or not 0<=result['body_bytes']<=65536 or not isinstance(result['body_sha256'],str)
        or re.fullmatch(r'[a-f0-9]{64}',result['body_sha256']) is None):
        raise ValueError('ownership projection metadata is invalid')
    fields=ownership_fingerprints(result['body_preview'])
    return {'exchange_id':exchange_id,'run_id':run_id,'identity_id':identity_id,'url':url,'method':'GET',
            'response_status':int(result['status']),'response_sha256':result['body_sha256'],
            'response_bytes':int(result['body_bytes']),'ownership_fingerprints':fields}


def projection_current(db,run_id,value):
    if not isinstance(value,dict):return False
    row=db.execute('SELECT * FROM http_exchanges WHERE id=? AND run_id=?',(value.get('exchange_id'),run_id)).fetchone()
    if not row or row['source']!='guided_login_projection' or row['method']!='GET' or row['response_status'] not in (200,401,403):return False
    identity=db.execute('SELECT engagement_id FROM identities WHERE id=?',(row['identity_id'],)).fetchone()
    run=db.execute('SELECT engagement_id FROM analysis_runs WHERE id=?',(run_id,)).fetchone()
    if not run or not identity or run['engagement_id']!=row['engagement_id'] or identity['engagement_id']!=row['engagement_id']:return False
    result={'body_preview':row['response_body_preview'],'status':row['response_status'],
            'body_sha256':row['response_sha256'],'body_bytes':row['response_bytes']}
    try:return value==projection_metadata(row['id'],run_id,row['identity_id'],row['url'],result)
    except (ValueError,TypeError,KeyError):return False


def store_responses(identity_id,run_id,responses):
    if not run_id:return 0
    import final_core as f
    import traditional_runtime as http
    identity=f.get_identity(identity_id);run=f.get_run(run_id)
    engagement=f.get_engagement(identity['engagement_id'])
    if run['engagement_id']!=identity['engagement_id'] or run['status'] not in ('running','completed','paused') or not engagement['scope'].get('allow_authentication'):return 0
    count=0
    for value in responses[:20]:
        # Re-project at the trust boundary; only worker-recorded fields are stored.
        status=value.get('status',200)
        checked=project_response(value['url'],engagement['normalized_target'],'GET',status,value['preview'].encode())
        if not checked:continue
        summary=f.dump({'identity_ref':identity_id,'http_status':status,
            'ownership_fingerprints':ownership_fingerprints(checked['preview']),
            'response_sha256':value['sha256']})
        with f.connect() as db:
            # Old captures had only an exchange. A fresh browser observation must
            # still register research material; never rewrite the historical row.
            exists=db.execute('''SELECT 1 FROM http_exchanges x
                JOIN observations o ON o.run_id=x.run_id AND o.subject=x.url
                JOIN artifacts a ON a.id=o.raw_ref AND a.run_id=x.run_id
                WHERE x.run_id=? AND x.identity_id=? AND x.url=?
                  AND x.response_sha256=? AND x.response_status=?
                  AND x.source='guided_login_projection' AND x.method='GET'
                  AND o.source_capability='guided_login_projection' AND o.summary=?
                  AND o.observation_type=? AND a.kind='http.ownership_projection'
                LIMIT 1''',(run_id,identity_id,value['url'],value['sha256'],status,summary,
                    'http.ownership_denial' if status in (401,403) else 'http.ownership_projection')).fetchone()
        if exists:continue
        result={'status':status,'headers':{'content-type':'application/json'},'body_preview':checked['preview'],'body_sha256':value['sha256'],'body_bytes':value['bytes']}
        from storage_access import storage_access
        with storage_access(f.LOCAL_DATA_ROOT):
            http._record_exchange(run,http.ReplayRequest(url=value['url']),result,identity_id,'guided_login_projection')
        count+=1
    return count
