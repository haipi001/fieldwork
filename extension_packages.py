"""Bounded local extension packages, installed disabled; never execute package code."""
import base64
import fcntl
import hashlib
import io
import json
import os
import re
import stat
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from pathlib import PurePosixPath
import final_core as core

MAX_BYTES=4*1024*1024
ID=re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
SHA=re.compile(r'^[0-9a-f]{64}$')


@contextmanager
def snapshot_files(data_root):
    """Keep package writers out while the database and version files are copied."""
    directory=Path(data_root)/'extension-packages'
    if not directory.exists() and not directory.is_symlink():
        yield [];return
    status=directory.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid!=os.getuid() or status.st_mode&0o077:
        raise ValueError('Unsafe package directory')
    descriptor=os.open(directory/'control.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    with os.fdopen(descriptor,'r+') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):raise ValueError('Invalid package lock')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pointers=sorted(directory.glob('package-*.json'));archives=sorted(directory.glob('archive-*.zip'))
        if len(pointers)>128 or len(archives)>500:raise ValueError('Package backup file limit')
        files=pointers+archives
        yield files


def strict_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate manifest key')
        result[key]=value
    return result


def inspect(encoded,expected):
    if not SHA.fullmatch(expected):raise ValueError('Invalid expected digest')
    payload=base64.b64decode(encoded,validate=True)
    if not payload or len(payload)>MAX_BYTES:raise ValueError('Package exceeds size limit')
    digest=hashlib.sha256(payload).hexdigest()
    if digest!=expected:raise ValueError('Package digest mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries=archive.infolist()
        if not 1<=len(entries)<=64:raise ValueError('Package file count limit')
        names=set();total=0
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if (entry.is_dir() or path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                or str(path)!=entry.filename or len(entry.filename)>200 or entry.filename in names
                or any(ord(c)<32 for c in entry.filename) or ':' in entry.filename
                or entry.compress_type not in {zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED}
                or entry.flag_bits&1 or stat.S_IFMT(entry.external_attr>>16) not in {0,stat.S_IFREG}):
                raise ValueError('Unsafe package member')
            names.add(entry.filename);total+=entry.file_size
            if entry.file_size>MAX_BYTES or total>MAX_BYTES:raise ValueError('Expanded package exceeds size limit')
            with archive.open(entry) as stream:
                if len(stream.read(MAX_BYTES+1))!=entry.file_size:raise ValueError('Package member size mismatch')
        if 'fieldwork-extension.json' not in names:raise ValueError('Manifest missing')
        manifest=json.loads(archive.read('fieldwork-extension.json'),object_pairs_hook=strict_object)
        if not isinstance(manifest,dict) or set(manifest)!={'id','name','version','type','entrypoint','capabilities'}:
            raise ValueError('Unsupported manifest schema')
        if not isinstance(manifest['id'],str) or not ID.fullmatch(manifest['id']):raise ValueError('Invalid package ID')
        for key,limit in [('name',200),('version',100)]:
            value=manifest[key]
            if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):raise ValueError('Invalid manifest text')
        if manifest['type'] not in {'pack','mcp','adapter'} or manifest['entrypoint'] not in names:raise ValueError('Invalid entry point')
        capabilities=manifest['capabilities']
        if not isinstance(capabilities,list) or len(capabilities)>32 or any(not isinstance(c,str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,79}',c) for c in capabilities):raise ValueError('Invalid capabilities')
    return payload,manifest,digest


def root():
    directory=core.LOCAL_DATA_ROOT/'extension-packages'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    status=directory.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid!=os.getuid() or status.st_mode&0o077:raise ValueError('Unsafe package directory')
    return directory


def read(name):
    try:descriptor=os.open(root()/name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return None
    with os.fdopen(descriptor) as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid package record')
        encoded=stream.read(32769)
        if len(encoded)>32768:raise ValueError('Package record too large')
        value=json.loads(encoded)
        if not isinstance(value,dict):raise ValueError('Invalid package record')
        return value


def write(name,value):
    temporary=root()/('temporary-'+uuid.uuid4().hex)
    descriptor=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(descriptor,'w') as stream:json.dump(value,stream)
        os.replace(temporary,root()/name)
    finally:temporary.unlink(missing_ok=True)


def inventory():
    paths=sorted(root().glob('package-*.json'))
    if len(paths)>128:raise ValueError('Package count limit')
    result=[]
    for path in paths:
        value=read(path.name)
        if not value or not ID.fullmatch(value.get('id','')) or not SHA.fullmatch(value.get('sha256','')):raise ValueError('Invalid package identity')
        if path.name!='package-'+value['id']+'.json':raise ValueError('Package identity mismatch')
        try:
            payload=archive_bytes(value['sha256'])
            _,manifest,_=inspect(base64.b64encode(payload).decode(),value['sha256'])
            intact=manifest==value['manifest']
        except (OSError,ValueError,TypeError,zipfile.BadZipFile):intact=False
        with core.connect() as db:
            event=db.execute("SELECT payload_json FROM v5_events WHERE id=? AND entity_id=? AND event_type='extension.package_stored'",(value.get('event_id'),value['id'])).fetchone()
        audited=bool(event and json.loads(event['payload_json']).get('sha256')==value['sha256'])
        result.append({**value,'state':value['state'] if intact and audited else 'storage_unconfirmed',
                       'version':value['manifest'].get('version'),'package_type':value['manifest'].get('type'),
                       'integrity':'verified' if intact else 'failed','audit_confirmed':audited,
                       'enabled':False,'execution_supported':False,'trust':'unverified'})
    return result


def install(encoded,expected,expected_current):
    payload,manifest,digest=inspect(encoded,expected)
    directory=root()
    descriptor=os.open(directory/'control.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    with os.fdopen(descriptor,'r+') as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):raise ValueError('Invalid control lock')
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        name='package-'+manifest['id']+'.json';before=read(name)
        if (before or {}).get('sha256')!=expected_current:raise ValueError('Installed version changed; refresh')
        if before and before['manifest']['type']!=manifest['type']:raise ValueError('Package type cannot change')
        if not before and len(inventory())>=128:raise ValueError('Package count limit')
        archive_path=directory/('archive-'+digest+'.zip')
        if not archive_path.exists() and len(list(directory.glob('archive-*.zip')))>=500:raise ValueError('Package version storage limit')
        try:archive_descriptor=os.open(archive_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        except FileExistsError:
            with os.fdopen(os.open(archive_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or stream.read(MAX_BYTES+1)!=payload:raise ValueError('Stored package integrity mismatch')
        else:
            try:
                with os.fdopen(archive_descriptor,'wb') as stream:stream.write(payload)
            except Exception:
                archive_path.unlink(missing_ok=True);raise
        value={'id':manifest['id'],'sha256':digest,'manifest':manifest,'state':'stored_disabled','updated_at':core.utcnow()}
        try:
            with core.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                event=db.execute('INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                           ('control',None,manifest['id'],'extension.package_stored',json.dumps({'sha256':digest,'previous_sha256':expected_current,'enabled':False}),value['updated_at']))
                value['event_id']=event.lastrowid
                write(name,value)
        except Exception:
            if before:write(name,before)
            else:(directory/name).unlink(missing_ok=True)
            raise
        return {**value,'enabled':False,'execution_supported':False,'trust':'unverified'}


def archive_bytes(digest):
    if not SHA.fullmatch(digest):raise ValueError('Invalid archive digest')
    path=root()/('archive-'+digest+'.zip')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):raise ValueError('Invalid stored package')
        payload=stream.read(MAX_BYTES+1)
    if len(payload)>MAX_BYTES or hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('Stored package digest mismatch')
    return payload


def restore(package_id,digest,expected_current):
    if not ID.fullmatch(package_id) or not SHA.fullmatch(digest):raise ValueError('Invalid stored version')
    payload=archive_bytes(digest)
    encoded=base64.b64encode(payload).decode()
    _,manifest,_=inspect(encoded,digest)
    if manifest['id']!=package_id:raise ValueError('Stored version belongs to another package')
    return install(encoded,digest,expected_current)
