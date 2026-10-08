"""Bounded incremental reads of observed same-origin page links."""
from urllib.parse import urlsplit, unquote
import re
import time

from fastapi import HTTPException


def eligible(item):
    url=urlsplit(item['url'])
    # Query-bearing links are observations only: dropping their query changes meaning.
    return (item.get('kind') in ('a','script','seed') and item.get('readable',True)
            and not url.query and not url.fragment and not url.username and not url.password
            and url.scheme in ('http','https')
            and not re.search(r'(?:^|[/_.-])(logout|signout|delete|remove|unsubscribe|purchase|checkout|reset)(?:$|[/_.-])',unquote(url.path),re.I))


def collect(run_id, links, checkpoint, check_cancel, previous_attempts=(), limit=5):
    import final_core as f
    import traditional_runtime as http
    run=f.get_run(run_id)
    engagement=f.get_engagement(run['engagement_id'])
    if run['mode']!='traditional' or engagement.get('target_type')=='repository':
        return []
    origin=urlsplit(engagement['normalized_target'])
    candidates=links or [{'url':engagement['normalized_target'],'kind':'seed','source_id':None}]
    attempted=set(previous_attempts)
    reads=[]
    for item in candidates:
        if len(reads)>=limit:break
        if not eligible(item):continue
        url=urlsplit(item['url'])
        if (url.scheme,url.netloc)!=(origin.scheme,origin.netloc) or item['url'] in attempted:continue
        with f.connect() as db:
            if db.execute("SELECT 1 FROM http_exchanges WHERE run_id=? AND url=? AND method='GET' LIMIT 1",(run_id,item['url'])).fetchone():continue
        check_cancel()
        current=f.get_run(run_id)
        if current['status'] not in ('running','completed'):break
        live=f.get_engagement(run['engagement_id'])
        if live['current_scope_snapshot_id']!=engagement['current_scope_snapshot_id']:break
        record={'url':item['url'],'source_id':item.get('source_id'),'status':'checking','attempted':False}
        spec=http.ReplayRequest(url=item['url'])
        try:
            http.network_guard(live,spec)
            deadline=time.monotonic()+30
            while True:
                check_cancel()
                if time.monotonic()>deadline or f.get_run(run_id)['status'] not in ('running','completed') or f.get_engagement(run['engagement_id'])['current_scope_snapshot_id']!=engagement['current_scope_snapshot_id']:
                    raise HTTPException(409,'page_wait_interrupted')
                slot=f.authorize_request(run_id,f.RequestAuthorizationInput(target=item['url'],action='read'))
                if slot['allowed']:break
                if slot.get('reason')!='rate_limit':raise HTTPException(409,'page_budget_or_scope')
                time.sleep(min(.2,max(.01,slot.get('retry_after_seconds',.1))))
            check_cancel()
            if f.get_run(run_id)['status'] not in ('running','completed'):
                raise HTTPException(409,'page_run_stopped')
            fresh_engagement=f.get_engagement(run['engagement_id'])
            if fresh_engagement['current_scope_snapshot_id']!=engagement['current_scope_snapshot_id']:
                raise HTTPException(409,'page_scope_changed')
            http.network_guard(fresh_engagement,spec)
            # Persist intent before transport. Uncertain attempts are not automatically repeated.
            record['status']='requesting';record['attempted']=True;reads.append(record);attempted.add(item['url']);checkpoint(reads)
            from v6_http_gateway import authorize_run_http_read, finish_http_read
            action_id=authorize_run_http_read(run_id,run['engagement_id'],spec.url,
                                              headers=spec.headers,source='guided_page_read')
            try:
                response=http.request_once(spec)
                finish_http_read(action_id,response=response)
            except Exception as error:
                try:finish_http_read(action_id,error_type=type(error).__name__)
                except ValueError:pass
                raise
            exchange=http._record_exchange(current,spec,response,None,'guided_page_read',item.get('source_id'))
            record.update(status='recorded',exchange_id=exchange['id'],response_status=exchange['response_status'])
            checkpoint(reads)
        except (HTTPException,ValueError):
            record['status']='not_completed'
            if record not in reads:reads.append(record)
            checkpoint(reads)
            # Do not burn remaining capacity on repeated policy or transport failures.
            break
    return reads
