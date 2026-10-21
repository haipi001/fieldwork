"""Bounded source revisions inside the existing private component record."""
import hashlib
import json
from agent_attack_executor import MAX_SOURCE_BYTES

LIMIT=20
FIELDS={'source_sha256','source_code','recorded_at','origin','record_sha256'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load(metadata):
    if 'source_history' not in metadata:
        if 'history_sha256' in metadata:raise ValueError('history list is missing')
        return []
    entries=metadata['source_history']
    if not isinstance(entries,list) or len(entries)>LIMIT or digest(entries)!=metadata.get('history_sha256'):
        raise ValueError('history index differs')
    seen=set();result=[]
    for row in entries:
        if not isinstance(row,dict) or set(row)!=FIELDS:raise ValueError('invalid history fields')
        code=row['source_code']
        if (not isinstance(code,str) or not 0<len(code.encode())<=MAX_SOURCE_BYTES
            or hashlib.sha256(code.encode()).hexdigest()!=row['source_sha256']
            or row['source_sha256'] in seen or row['origin'] not in {'registered_version','captured_existing_current'}
            or not isinstance(row['recorded_at'],str) or not 1<=len(row['recorded_at'])<=64
            or digest({k:v for k,v in row.items() if k!='record_sha256'})!=row['record_sha256']):
            raise ValueError('history source or record digest differs')
        seen.add(row['source_sha256']);result.append(dict(row))
    return result


def capacity(metadata,new_source):
    ids={row['source_sha256'] for row in load(metadata)}
    if metadata.get('source_code') is not None:ids.add(hashlib.sha256(metadata['source_code'].encode()).hexdigest())
    ids.add(hashlib.sha256(new_source.encode()).hexdigest())
    if len(ids)>LIMIT:raise ValueError('Adapter source history limit reached (20 distinct versions)')
    return len(ids)


def capture(metadata,new_source,now):
    capacity(metadata,new_source);history=load(metadata)
    def add(code,origin):
        sha=hashlib.sha256(code.encode()).hexdigest()
        if any(row['source_sha256']==sha for row in history):return
        row={'source_code':code,'source_sha256':sha,'recorded_at':now,'origin':origin}
        row['record_sha256']=digest(row);history.append(row)
    if metadata.get('source_code') is not None:add(metadata['source_code'],'captured_existing_current')
    add(new_source,'registered_version')
    return {'source_history':history,'history_sha256':digest(history)}


def summaries(metadata):
    return [{key:value for key,value in row.items() if key!='source_code'}|{'source_bytes':len(row['source_code'].encode()),'archived':True}
            for row in load(metadata)]
