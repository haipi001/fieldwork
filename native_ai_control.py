"""Bounded service control state machine; IPC must supply an OS-authenticated UID."""
import base64
import json
import re
import threading

from native_ai_forwarder import LoopbackTransport
from native_ai_runtime import prepare

MAX_REQUEST=8192
PUBLIC_FIELDS={'status','error_code','exit_code','producer_alive','source_error_code','events_read',
    'pending_batch','buffered_records','forced_termination','stdout_drained',
    'unpersisted_buffer_bytes','unsaved_records','protocol_incomplete'}


def decode_request(raw):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_REQUEST:
        raise ValueError('控制请求大小无效')
    def pairs(items):
        value={}
        for key,item in items:
            if key in value:raise ValueError('控制请求包含重复字段')
            value[key]=item
        return value
    def invalid_constant(value):raise ValueError('控制请求包含无效数值')
    try:value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=invalid_constant)
    except (UnicodeError,RecursionError):raise ValueError('控制请求格式无效') from None
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1:
        raise ValueError('控制请求版本无效')
    command=value.get('command')
    if not isinstance(command,str) or command not in {'start','stop','status','drain'}:
        raise ValueError('未知控制命令')
    fields={'version','command'}
    if command=='start':fields|={'audit_id','collector_id','session_id','public_key'}
    elif command=='drain' or set(value)&{'audit_id','collector_id','session_id'}:
        fields|={'audit_id','collector_id','session_id'}
    if set(value)!=fields:raise ValueError('控制请求字段无效')
    if 'audit_id' in fields:
        for key in ('audit_id','collector_id'):
            if not isinstance(value[key],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value[key]):
                raise ValueError('配对标识无效')
        session=value['session_id']
        if not isinstance(session,str) or not 1<=len(session)<=256 or any(ord(char)<32 for char in session):
            raise ValueError('配对会话无效')
    if command=='start':
        public=value['public_key']
        if not isinstance(public,str):raise ValueError('配对公钥无效')
        decoded=base64.b64decode(public,validate=True)
        if len(decoded)!=32 or base64.b64encode(decoded).decode()!=public:
            raise ValueError('配对公钥无效')
    return value


class NativeControl:
    def __init__(self,*,app_uid,team_id,base_url='http://127.0.0.1:8000'):
        # These settings belong to protected installation configuration, never a request.
        if type(app_uid) is not int or not 0<app_uid<2**32:raise ValueError('服务账户配置无效')
        if not isinstance(team_id,str) or not re.fullmatch(r'[A-Z0-9]{10}',team_id):
            raise ValueError('服务签名团队配置无效')
        LoopbackTransport(base_url)
        self.app_uid=app_uid;self.team_id=team_id;self.base_url=base_url
        self.runtime=None;self.pairing=None;self.lock=threading.RLock()
        self.control_error=None
        self.last_report={'status':'inactive','producer_alive':False}

    def _health(self):
        state=self.runtime.session.health() if self.runtime else self.last_report
        report={key:value for key,value in state.items() if key in PUBLIC_FIELDS}
        if self.control_error:report['control_error_code']=self.control_error
        return report

    def handle(self,raw,*,peer_uid):
        # Caller gets peer_uid from the socket, not from JSON or a supplied header.
        if type(peer_uid) is not int or peer_uid!=self.app_uid:
            return {'ok':False,'error_code':'account_not_authorized'}
        try:request=decode_request(raw)
        except (ValueError,TypeError):return {'ok':False,'error_code':'invalid_request'}
        with self.lock:
            command=request['command']
            if command in {'status','stop','drain'} and 'audit_id' in request:
                scope={key:request[key] for key in ('audit_id','collector_id','session_id')}
                if self.pairing is None:
                    return {'ok':True,'status':'inactive','producer_alive':False,'idempotent':True}
                if any(scope[key]!=self.pairing[key] for key in scope):
                    return {'ok':False,'error_code':'another_pairing_active'}
            if command=='status':return {'ok':True,**self._health()}
            if command=='drain':
                try:report=self.runtime.drain()
                except Exception:
                    self.control_error='delivery_pending'
                    return {'ok':False,**self._health(),'error_code':self.control_error}
                self.last_report={key:value for key,value in report.items() if key in PUBLIC_FIELDS}
                self.runtime=None;self.pairing=None;self.control_error=None
                return {'ok':True,**self.last_report}
            if command=='stop':
                return self.shutdown()
            pairing={key:request[key] for key in ('audit_id','collector_id','session_id','public_key')}
            if self.runtime is not None:
                if pairing!=self.pairing:return {'ok':False,'error_code':'another_pairing_active'}
                state=self._health()
                return {'ok':not self.control_error and state.get('status') not in {'failed','unavailable'},**state,'idempotent':True}
            self.control_error=None
            try:service=prepare(**pairing,team_id=self.team_id,base_url=self.base_url)
            except Exception:
                self.last_report={'status':'unavailable','producer_alive':False,'error_code':'preparation_failed'}
                return {'ok':False,**self.last_report}
            self.runtime=service;self.pairing=pairing
            try:state=service.start()
            except Exception:
                try:
                    service.close();self.runtime=None;self.pairing=None
                    self.last_report={'status':'failed','producer_alive':False,'error_code':'start_failed'}
                except Exception:
                    self.control_error='start_cleanup_pending'
                    return {'ok':False,**self._health(),'error_code':self.control_error}
                return {'ok':False,**self.last_report}
            self.last_report={key:value for key,value in state.items() if key in PUBLIC_FIELDS}
            return {'ok':state.get('status') not in {'failed','unavailable'},**self.last_report}

    def tick(self):
        with self.lock:
            if self.runtime is None:return self._health()
            state=self.runtime.tick()
            report={key:value for key,value in state.items() if key in PUBLIC_FIELDS}
            if self.control_error:report['control_error_code']=self.control_error
            return report

    def shutdown(self):
        """Service-owned cleanup, separate from external peer authentication."""
        with self.lock:
            if self.runtime is None:return {'ok':True,**self._health(),'idempotent':True}
            try:report=self.runtime.close()
            except Exception:
                self.control_error='stop_persistence_pending'
                return {'ok':False,**self._health(),'error_code':self.control_error}
            self.last_report={key:value for key,value in report.items() if key in PUBLIC_FIELDS}
            self.runtime=None;self.pairing=None;self.control_error=None
            return {'ok':True,**self.last_report}
