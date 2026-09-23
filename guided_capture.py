"""Keep only observed ownership fields needed by the guided read verifier."""
import hashlib
import json
from urllib.parse import urlsplit


def project_response(url, target, method, status, body):
    parsed,base=urlsplit(url),urlsplit(target)
    if method!='GET' or status!=200 or len(body)>65536 or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    if (parsed.scheme,parsed.netloc)!=(base.scheme,base.netloc):return None
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
    return {'url':url,'preview':json.dumps(projected,separators=(',',':')),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}


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
        checked=project_response(value['url'],engagement['normalized_target'],'GET',200,value['preview'].encode())
        if not checked:continue
        with f.connect() as db:
            exists=db.execute('SELECT 1 FROM http_exchanges WHERE run_id=? AND identity_id=? AND url=? AND response_sha256=?',(run_id,identity_id,value['url'],value['sha256'])).fetchone()
        if exists:continue
        result={'status':200,'headers':{'content-type':'application/json'},'body_preview':checked['preview'],'body_sha256':value['sha256'],'body_bytes':value['bytes']}
        http._record_exchange(run,http.ReplayRequest(url=value['url']),result,identity_id,'guided_login_projection')
        count+=1
    return count
