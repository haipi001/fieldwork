"""Local database and registered artifact archives; drills never replace live data."""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lifecycle import database_integrity, sha256
from version import APP_VERSION

MAX_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARTIFACTS = 5000
ID_RE = re.compile(r'evidence-[0-9a-f]{32}\Z')


def _name(artifact_id):
    return 'artifacts/' + hashlib.sha256(artifact_id.encode()).hexdigest() + '.bin'


def _open_artifact(root, uri):
    root_alias = Path(root).absolute()
    root = root_alias.resolve()
    path = Path(uri)
    if not path.is_absolute():
        raise ValueError('artifact_outside_data_directory')
    if path.is_relative_to(root_alias):
        parts = path.relative_to(root_alias).parts
    elif path.is_relative_to(root):
        parts = path.relative_to(root).parts
    else:
        raise ValueError('artifact_outside_data_directory')
    if not parts or any(p in {'.','..'} for p in parts):
        raise ValueError('invalid_artifact_path')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = following
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        info = os.fstat(file_descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
            os.close(file_descriptor)
            raise ValueError('artifact_not_regular_or_too_large')
        return os.fdopen(file_descriptor, 'rb')
    finally:
        os.close(descriptor)


def create_archive(database, data_root, backup_root):
    from extension_packages import snapshot_files
    with snapshot_files(data_root) as extension_files:
        return _create_archive(database,data_root,backup_root,extension_files)


def _create_archive(database, data_root, backup_root,extension_files):
    backup_root = Path(backup_root); backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup_id = 'evidence-' + uuid.uuid4().hex
    archive_path = backup_root / (backup_id + '.zip')
    manifest_path = backup_root / (backup_id + '.evidence.json')
    try:
        with tempfile.TemporaryDirectory(prefix='evidence-backup-',dir=backup_root) as directory:
            snapshot = Path(directory)/'database.sqlite3'
            with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as source, sqlite3.connect(snapshot) as target:
                source.backup(target)
            snapshot.chmod(0o600)
            if not database_integrity(snapshot):
                raise ValueError('database_integrity_failed')
            total = snapshot.stat().st_size
            if total > MAX_BYTES:
                raise ValueError('backup_size_limit')
            with sqlite3.connect(snapshot) as db:
                artifacts = db.execute('SELECT id,uri,sha256 FROM artifacts ORDER BY id LIMIT ?', (MAX_ARTIFACTS+1,)).fetchall()
                schema = db.execute('PRAGMA user_version').fetchone()[0]
            if len(artifacts)>MAX_ARTIFACTS:
                raise ValueError('artifact_count_limit')
            captured = []
            staging = Path(directory)/'archive.zip'
            with zipfile.ZipFile(staging,'w',compression=zipfile.ZIP_STORED) as archive:
                archive.write(snapshot,'database.sqlite3')
                for artifact_id,uri,expected in artifacts:
                    try:
                        with _open_artifact(data_root,uri) as source, archive.open(_name(artifact_id),'w') as target:
                            digest=hashlib.sha256();size=0
                            while chunk:=source.read(1024*1024):
                                size+=len(chunk);total+=len(chunk)
                                if size>MAX_FILE_BYTES or total>MAX_BYTES:
                                    raise ValueError('backup_size_limit')
                                digest.update(chunk);target.write(chunk)
                    except (OSError, ValueError) as error:
                        raise ValueError('artifact_unavailable_or_unbounded:'+artifact_id) from error
                    if digest.hexdigest()!=expected:
                        raise ValueError('artifact_digest_mismatch:'+artifact_id)
                    captured.append({'id':artifact_id,'entry':_name(artifact_id),'sha256':expected,'bytes':size})
                packages=[];package_values=[]
                for path in extension_files:
                    with _open_artifact(data_root,path.absolute()) as source:
                        payload=source.read(4*1024*1024+1)
                    total+=len(payload)
                    if len(payload)>4*1024*1024 or total>MAX_BYTES:raise ValueError('package_backup_size_limit')
                    package_values.append(_validate_package_file(path.name,payload,snapshot))
                    entry='extension-packages/'+path.name
                    archive.writestr(entry,payload)
                    packages.append({'name':path.name,'entry':entry,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()})
                _validate_package_links(package_values)
                archive.writestr('manifest.json',json.dumps({'format':2,'database_sha256':sha256(snapshot),'artifacts':captured,'extension_files':packages},sort_keys=True))
            staging.chmod(0o600)
            metadata={'id':backup_id,'created_at':datetime.now(timezone.utc).isoformat(),'app_version':APP_VERSION,
                      'schema':schema,'artifact_count':len(captured),'bytes':staging.stat().st_size,
                      'sha256':sha256(staging),'includes':'database_registered_artifacts_and_extension_packages','extension_file_count':len(packages),
                      'excludes':['keychain','unregistered_files','application_source_code']}
            staging.replace(archive_path)
            manifest_path.write_text(json.dumps(metadata,sort_keys=True));manifest_path.chmod(0o600)
        return metadata
    except Exception:
        archive_path.unlink(missing_ok=True);manifest_path.unlink(missing_ok=True)
        raise


def _validate_package_file(name,payload,database):
    import base64
    from extension_packages import inspect,ID,SHA
    if name.startswith('archive-') and name.endswith('.zip'):
        digest=name[8:-4]
        if not SHA.fullmatch(digest):raise ValueError('invalid_package_archive_name')
        _,manifest,_=inspect(base64.b64encode(payload).decode(),digest)
        return ('archive',digest,manifest)
    elif name.startswith('package-') and name.endswith('.json'):
        identifier=name[8:-5]
        if not ID.fullmatch(identifier) or len(payload)>32768:raise ValueError('invalid_package_pointer')
        value=json.loads(payload)
        if not isinstance(value,dict) or value.get('id')!=identifier or not SHA.fullmatch(value.get('sha256','')):
            raise ValueError('invalid_package_pointer')
        with sqlite3.connect(database) as db:
            record=db.execute("SELECT payload_json FROM v5_events WHERE id=? AND entity_id=? AND event_type='extension.package_stored'",(value.get('event_id'),identifier)).fetchone()
        if not record or json.loads(record[0]).get('sha256')!=value['sha256']:raise ValueError('package_audit_mismatch')
        return ('pointer',value['sha256'],value)
    else:raise ValueError('unexpected_package_file')


def _validate_package_links(values):
    if sum(kind=='pointer' for kind,_,_ in values)>128 or sum(kind=='archive' for kind,_,_ in values)>500:
        raise ValueError('package_inventory_limit')
    archives={digest:manifest for kind,digest,manifest in values if kind=='archive'}
    for kind,digest,value in values:
        if kind=='pointer' and value.get('manifest')!=archives.get(digest):
            raise ValueError('package_pointer_archive_mismatch')


def _compare_records(db,table,columns='*'):
    current_columns=db.execute('PRAGMA main.table_info('+table+')').fetchall()
    backup_columns=db.execute('PRAGMA restored.table_info('+table+')').fetchall()
    if not current_columns or current_columns!=backup_columns:return {'verified':False}
    current=db.execute('SELECT COUNT(*) FROM (SELECT '+columns+' FROM main.'+table+' EXCEPT SELECT '+columns+' FROM restored.'+table+')').fetchone()[0]
    backup=db.execute('SELECT COUNT(*) FROM (SELECT '+columns+' FROM restored.'+table+' EXCEPT SELECT '+columns+' FROM main.'+table+')').fetchone()[0]
    return {'verified':True,'current_records_missing_or_changed':current,'backup_records_missing_or_changed':backup}


def _metadata(backup_root,backup_id):
    if not ID_RE.fullmatch(backup_id):
        raise ValueError('invalid_backup_id')
    path=Path(backup_root)/(backup_id+'.evidence.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>65536:
        raise ValueError('backup_metadata_unavailable')
    value=json.loads(path.read_text())
    if not isinstance(value,dict) or value.get('id')!=backup_id:
        raise ValueError('invalid_backup_metadata')
    return value


def list_archives(backup_root):
    values=[]
    for path in sorted(Path(backup_root).glob('*.evidence.json'),reverse=True):
        try:
            value=_metadata(backup_root,path.name.removesuffix('.evidence.json'))
            archive=Path(backup_root)/(value['id']+'.zip')
            value['available']=archive.is_file() and not archive.is_symlink()
            value['integrity']='not_checked'
            values.append(value)
        except (OSError,ValueError,TypeError):
            continue
    return values


def restore_drill(backup_root,backup_id):
    metadata=_metadata(backup_root,backup_id)
    path=Path(backup_root)/(backup_id+'.zip')
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES+8*1024*1024 or sha256(path)!=metadata['sha256']:
        raise ValueError('archive_digest_mismatch')
    with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix='evidence-restore-') as directory:
        entries=archive.infolist()
        if len(entries)>MAX_ARTIFACTS+630 or len({i.filename for i in entries})!=len(entries) or sum(i.file_size for i in entries)>MAX_BYTES+8*1024*1024:
            raise ValueError('invalid_archive_entries')
        if archive.getinfo('manifest.json').file_size>8*1024*1024:
            raise ValueError('manifest_size_limit')
        manifest=json.loads(archive.read('manifest.json'))
        records=manifest['artifacts']
        if manifest.get('format') not in {1,2} or not isinstance(records,list) or len(records)>MAX_ARTIFACTS:
            raise ValueError('invalid_archive_manifest')
        if any(not isinstance(record,dict) or not isinstance(record.get('id'),str)
               or not isinstance(record.get('sha256'),str) or type(record.get('bytes')) is not int
               or not 0<=record['bytes']<=MAX_FILE_BYTES for record in records):
            raise ValueError('invalid_artifact_manifest')
        packages=manifest.get('extension_files',[]) if manifest.get('format')==2 else []
        if not isinstance(packages,list) or len(packages)>628:
            raise ValueError('invalid_package_manifest')
        if any(not isinstance(p,dict) or not isinstance(p.get('name'),str) or '/' in p['name'] or '\\' in p['name']
               or p.get('entry')!='extension-packages/'+p['name'] or type(p.get('bytes')) is not int
               or not 0<=p['bytes']<=4*1024*1024 or not isinstance(p.get('sha256'),str) for p in packages):
            raise ValueError('invalid_package_manifest')
        if len({p['name'] for p in packages})!=len(packages):raise ValueError('invalid_package_manifest')
        expected={'database.sqlite3','manifest.json'}|{_name(record['id']) for record in records}|{p['entry'] for p in packages}
        if {i.filename for i in entries}!=expected or any(record['entry']!=_name(record['id']) for record in records):
            raise ValueError('unexpected_archive_entries')
        db_path=Path(directory)/'database.sqlite3'
        with archive.open('database.sqlite3') as source, db_path.open('wb') as target:shutil.copyfileobj(source,target)
        if sha256(db_path)!=manifest['database_sha256'] or not database_integrity(db_path):
            raise ValueError('database_integrity_failed')
        with sqlite3.connect(db_path) as db:
            registered=dict(db.execute('SELECT id,sha256 FROM artifacts'))
        if len(records)!=len(registered) or len(records)!=metadata['artifact_count'] or len({r['id'] for r in records})!=len(records):
            raise ValueError('artifact_inventory_mismatch')
        for record in records:
            target=Path(directory)/Path(record['entry']).name
            with archive.open(record['entry']) as source,target.open('wb') as dest:shutil.copyfileobj(source,dest)
            if target.stat().st_size!=record['bytes'] or sha256(target)!=record['sha256'] or registered.get(record['id'])!=record['sha256']:
                raise ValueError('artifact_digest_mismatch')
        package_values=[]
        for record in packages:
            payload=archive.read(record['entry'])
            if len(payload)!=record['bytes'] or hashlib.sha256(payload).hexdigest()!=record['sha256']:
                raise ValueError('package_digest_mismatch')
            package_values.append(_validate_package_file(record['name'],payload,db_path))
            target=Path(directory)/'extension-packages';target.mkdir(mode=0o700,exist_ok=True)
            (target/record['name']).write_bytes(payload)
            (target/record['name']).chmod(0o600)
        _validate_package_links(package_values)
        if len(packages)!=metadata.get('extension_file_count',0):raise ValueError('package_inventory_mismatch')
    return {'backup_id':backup_id,'integrity_ok':True,'artifact_count':len(records),
            'restored_to_isolated_copy':True,'live_database_replaced':False,'live_artifacts_replaced':False,
            'database_sha256':manifest['database_sha256'],'extension_file_count':len(packages),'live_extensions_replaced':False}


def restore_plan(database,backup_root,backup_id):
    """Validate offline restore prerequisites and quantify records absent from the snapshot."""
    from version import SCHEMA_VERSION
    checked=restore_drill(backup_root,backup_id)
    metadata=_metadata(backup_root,backup_id)
    blockers=[]
    import monitor_process_control
    try:
        monitor=monitor_process_control.status()
        if monitor['state']=='unresolved':
            blockers.append('monitor_process_state_unknown')
        elif monitor['running'] or monitor['state']=='starting':
            blockers.append('active_monitor_process')
    except (OSError,ValueError,RuntimeError):
        blockers.append('monitor_process_state_unknown')
    import monitor_startup
    try:
        if monitor_startup.configured():blockers.append('monitor_login_configured')
    except OSError:blockers.append('monitor_startup_state_unknown')
    with tempfile.TemporaryDirectory(prefix='restore-plan-') as directory:
        restored=Path(directory)/'restored.db'
        with zipfile.ZipFile(Path(backup_root)/(backup_id+'.zip')) as archive:
            with archive.open('database.sqlite3') as source,restored.open('wb') as target:
                shutil.copyfileobj(source,target)
        if sha256(restored)!=checked['database_sha256']:
            raise ValueError('backup_changed_during_review')
        with sqlite3.connect(f'file:{database}?mode=ro',uri=True) as db:
            db.execute('ATTACH DATABASE ? AS restored',(str(restored),))
            db.execute('BEGIN')
            current_schema=db.execute('PRAGMA main.user_version').fetchone()[0]
            restored_schema=db.execute('PRAGMA restored.user_version').fetchone()[0]
            if current_schema!=SCHEMA_VERSION or restored_schema!=SCHEMA_VERSION:
                blockers.append('schema_version_mismatch')
            counts={}
            for table in ('analysis_runs','agent_tasks','runtime_calls','candidate_findings','canonical_findings','artifacts'):
                counts[table]={'current':db.execute('SELECT COUNT(*) FROM main.'+table).fetchone()[0],
                               'backup':db.execute('SELECT COUNT(*) FROM restored.'+table).fetchone()[0],
                               'absent_from_backup':db.execute('SELECT COUNT(*) FROM main.'+table+' c LEFT JOIN restored.'+table+' b ON b.id=c.id WHERE b.id IS NULL').fetchone()[0]}
            active_runs=db.execute("SELECT COUNT(*) FROM main.analysis_runs WHERE status IN ('running','queued')").fetchone()[0]
            active_tasks=db.execute("SELECT COUNT(*) FROM main.agent_tasks WHERE status IN ('leased','running')").fetchone()[0]
            active_monitors=db.execute("SELECT COUNT(*) FROM main.agent_monitors WHERE status='active'").fetchone()[0]
            unresolved_calls=db.execute("SELECT COUNT(*) FROM main.runtime_calls WHERE state IN ('reserved','calling','unknown')").fetchone()[0]
            if active_runs or active_tasks:blockers.append('active_research')
            if active_monitors:blockers.append('active_monitor_collection')
            if unresolved_calls:blockers.append('unresolved_model_usage')
            if counts['runtime_calls']['absent_from_backup']:blockers.append('model_history_missing_from_backup')
            security_counts={}
            for table in ('scope_snapshots','execution_policies','action_intents_v6','capability_grants_v6','capability_approvals_v6',
                          'capability_revocations_v6','capability_uses_v6','policy_decisions_v6',
                          'run_containment_v6','run_containment_events_v6','incident_response_events_v6',
                          'model_usage_reconciliations_v6','verification_receipt_bindings_v6',
                          'http_gateway_receipts_v6','http_gateway_executions_v6',
                          'model_gateway_bindings_v6','model_gateway_starts_v6'):
                current_columns=db.execute('PRAGMA main.table_info('+table+')').fetchall()
                backup_columns=db.execute('PRAGMA restored.table_info('+table+')').fetchall()
                if not current_columns or current_columns!=backup_columns:
                    security_counts[table]={'verified':False}
                    if 'security_history_unverified' not in blockers:blockers.append('security_history_unverified')
                    continue
                current_difference=db.execute('SELECT COUNT(*) FROM (SELECT * FROM main.'+table+' EXCEPT SELECT * FROM restored.'+table+')').fetchone()[0]
                backup_difference=db.execute('SELECT COUNT(*) FROM (SELECT * FROM restored.'+table+' EXCEPT SELECT * FROM main.'+table+')').fetchone()[0]
                security_counts[table]={'verified':True,'current_records_missing_or_changed':current_difference,
                                        'backup_records_missing_or_changed':backup_difference}
                if current_difference or backup_difference:
                    if 'security_history_differs' not in blockers:blockers.append('security_history_differs')
            binding_columns='id,status,current_scope_snapshot_id,current_policy_id'
            current_bindings=db.execute('SELECT COUNT(*) FROM (SELECT '+binding_columns+' FROM main.engagements_v2 EXCEPT SELECT '+binding_columns+' FROM restored.engagements_v2)').fetchone()[0]
            backup_bindings=db.execute('SELECT COUNT(*) FROM (SELECT '+binding_columns+' FROM restored.engagements_v2 EXCEPT SELECT '+binding_columns+' FROM main.engagements_v2)').fetchone()[0]
            authority_context={'current_bindings_missing_or_changed':current_bindings,'backup_bindings_missing_or_changed':backup_bindings}
            if current_bindings or backup_bindings:blockers.append('authorization_context_differs')
            model_counts={table:_compare_records(db,table) for table in
                          ('runtime_calls','runtime_usage','runtime_usage_reports','agent_task_usage',
                           'runtime_call_inputs','runtime_call_timings')}
            configuration_counts={table:_compare_records(db,table,columns) for table,columns in (
                ('runtime_profiles','*'),('runtime_route_decisions','*'),
                ('runtime_providers','id,kind,name,base_url,model,secret_ref,enabled,metadata_json'),
                ('research_groups','id,campaign_id,run_id,runtime_profile_id,budget_json'),
                ('agent_tasks','id,campaign_id,run_id,group_id,context_capsule_json,tool_grants_json,route_requirement_json,budget_json'))}
            for comparison,prefix in ((model_counts,'model_ledger'),(configuration_counts,'execution_configuration')):
                if any(not record['verified'] for record in comparison.values()):blockers.append(prefix+'_unverified')
                if any(record.get('current_records_missing_or_changed') or record.get('backup_records_missing_or_changed') for record in comparison.values()):
                    blockers.append(prefix+'_differs')
    return {'backup_id':backup_id,'archive_sha256':metadata['sha256'],'artifact_count':checked['artifact_count'],
            'counts':counts,'security_ledger_comparison':security_counts,'authorization_context_comparison':authority_context,
            'model_ledger_comparison':model_counts,'execution_configuration_comparison':configuration_counts,'blockers':blockers,'preflight_ready':not blockers,
            'execution_mode':'offline','confirmation_required':True,'live_data_changed':False,
            'restored_authorization_requires_review':True,'automatic_resume':False,
            'excludes':metadata['excludes']}
