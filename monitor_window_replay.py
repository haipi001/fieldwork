"""Reviewed, bounded, durable window reconstruction; never Finding promotion."""
import json
import hashlib
import subprocess
import sys
from pathlib import Path
from fastapi import HTTPException
import agent_audit as audit
import final_core as core
import monitor_alerts as alerts
from storage_access import storage_access
from proof_materials import read_proof_file
from version import APP_VERSION


def implementation():
    root=Path(__file__).resolve().parent
    return {name:hashlib.sha256(read_proof_file(root/name,500_000)).hexdigest()
            for name in ('monitor_window_replay_worker.py','agent_audit.py')}


def _window(db, window_id):
    row=db.execute("SELECT * FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.alert_created' ORDER BY id LIMIT 1",(alerts.TOPIC,window_id)).fetchone()
    if not row:raise HTTPException(404,'Alert not found')
    item=alerts._checked(db,row)
    parent=audit.audit_row(db,item['audit_id'])
    value,_=audit.read_artifact(db,item['artifact_id'],parent['run_id'])
    return item,parent,value


def _receipt(db,window_id,run_id,digest):
    row=db.execute("SELECT payload_json FROM v5_events WHERE topic=? AND entity_id=? AND event_type='monitor.window_reconstructed' ORDER BY id DESC LIMIT 1",(alerts.TOPIC,window_id)).fetchone()
    if not row:return None
    ref=core.load(row['payload_json'])
    result,_=audit.read_artifact(db,ref['artifact_id'],run_id)
    if result['window_id']!=window_id or result['input_digest']!=digest:
        raise HTTPException(409,'Reconstruction receipt binding changed')
    return {'artifact_id':ref['artifact_id'],**result}


def receipt(window_id):
    with core.connect() as db:
        item,parent,window=_window(db,window_id)
        return {'receipt':_receipt(db,window_id,parent['run_id'],item['input_digest'])}


def replay(window_id,review_hash):
    with storage_access(core.LOCAL_DATA_ROOT):
        with core.connect() as db:
            item,parent,window=_window(db,window_id)
            if item['review_hash']!=review_hash:raise HTTPException(409,'Alert changed; review again')
            previous=_receipt(db,window_id,parent['run_id'],item['input_digest'])
            if previous:return previous
        try:implementation_hashes=implementation()
        except (OSError,ValueError):raise HTTPException(409,'Reconstruction implementation cannot be verified') from None
        payload=core.dump(window).encode()
        if len(payload)>2_000_000:raise HTTPException(413,'Window exceeds reconstruction bounds')
        try:
            process=subprocess.run([sys.executable,'-E','-s',str(Path(__file__).with_name('monitor_window_replay_worker.py'))],
                                   input=payload,capture_output=True,timeout=15,check=False,
                                   cwd=Path(__file__).resolve().parent,env={'PATH':'/usr/bin:/bin','LANG':'en_US.UTF-8'})
            if process.returncode or len(process.stdout)>8192:raise ValueError('worker_failed')
            result=json.loads(process.stdout)
            if (not isinstance(result,dict) or type(result.get('executor_pid')) is not int or result['executor_pid']<=0
                    or result.get('window_id')!=window_id or result.get('input_digest')!=item['input_digest']
                    or result.get('status') not in {'reproduced','mismatch'} or result.get('verification_status')!='human_review'
                    or result.get('behavior_authenticity_verified') is not False or result.get('canonical_finding_created') is not False):
                raise ValueError('invalid_worker_result')
        except (subprocess.TimeoutExpired,OSError,ValueError,TypeError):
            raise HTTPException(409,'Window reconstruction failed; no receipt stored') from None
        try:current_implementation=implementation()
        except (OSError,ValueError):raise HTTPException(409,'Reconstruction implementation cannot be verified; no receipt stored') from None
        if current_implementation!=implementation_hashes:raise HTTPException(409,'Reconstruction implementation changed; no receipt stored')
        with core.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current,current_parent,current_window=_window(db,window_id)
            if current['review_hash']!=review_hash or audit.digest(current_window)!=audit.digest(window):
                raise HTTPException(409,'Window changed during reconstruction; no receipt stored')
            previous=_receipt(db,window_id,parent['run_id'],item['input_digest'])
            if previous:return previous
            result['implementation_sha256']=implementation_hashes
            result['app_version']=APP_VERSION
            result['python_version']=sys.version.split()[0]
            result['created_at']=core.utcnow()
            result['audit_id']=item['audit_id']
            result['synthetic']=bool(parent['demo'])
            artifact_id,_=audit.artifact(db,parent['run_id'],'monitor_window_reconstruction',result)
            alerts._write(db,window_id,'monitor.window_reconstructed',{'artifact_id':artifact_id})
            return {'artifact_id':artifact_id,**result}
