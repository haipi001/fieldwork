"""Service-side collector lifecycle. No installation or permission escalation."""
import time
from native_ai_launcher import launch_installed_collector,NativeLaunchError
from native_ai_reader import NativeProcessReader,NativeProtocolError
from native_ai_forwarder import ImportConflict


class NativeCollectorSession:
    def __init__(self,pipeline,team_id):
        self.pipeline=pipeline;self.team_id=team_id;self.process=None;self.reader=None
        self.status='inactive';self.error=None;self.next_flush=0.;self.retry_delay=1.
        self.stop_report={}

    def start(self):
        if self.process is not None:raise ValueError('采集会话不能重复启动')
        try:self.process=launch_installed_collector(self.team_id)
        except NativeLaunchError:
            self.status='unavailable';self.error='launch_requirements';return self.health()
        self.reader=NativeProcessReader(self.process,self.pipeline)
        self.status='starting'
        return self.health()

    def health(self):
        code=self.process.poll() if self.process is not None else None
        return {'status':self.status,'error_code':self.error,'exit_code':code,
            'producer_alive':self.process is not None and code is None,
            'source_error_code':{78:'activation_failed',74:'producer_output_incomplete'}.get(code),
            'events_read':self.reader.events_read if self.reader else 0,
            'pending_batch':self.pipeline.pending is not None,'buffered_records':len(self.pipeline.records)}

    def tick(self):
        if self.reader is None or self.status in {'failed','stopped','exited','unavailable'}:return self.health()
        try:
            read=self.reader.poll(0)
            now=time.monotonic()
            if now>=self.next_flush and (self.pipeline.records or self.pipeline.pending is not None):
                self.pipeline.flush();self.retry_delay=1.;self.next_flush=now+1
                self.error=None
            if read['events_read'] and self.error is None:self.status='running'
            if read['stdout_eof'] and read['exit_code'] is not None:
                self.status='exited'
                self.error={78:'activation_failed',74:'producer_output_incomplete'}.get(read['exit_code'],'collector_exited')
        except (ConnectionError,TimeoutError):
            self.status='retrying';self.error='local_delivery_unavailable'
            self.next_flush=time.monotonic()+self.retry_delay;self.retry_delay=min(30.,self.retry_delay*2)
        except (NativeProtocolError,ImportConflict,ValueError,OSError) as error:
            self.status='failed'
            self.error='invalid_metadata' if isinstance(error,NativeProtocolError) else 'batch_rejected' if isinstance(error,ImportConflict) else 'storage_or_contract_failure'
            if self.process.poll() is None:self.process.terminate()
        return self.health()

    def stop(self,grace=3):
        if type(grace) not in {int,float} or not 0<=grace<=5:raise ValueError('停止等待必须在 0–5 秒之间')
        if self.reader is None:self.status='stopped';return self.health()
        if self.status=='stopped':return {**self.health(),**self.stop_report}
        deadline=time.monotonic()+grace
        if self.process.poll() is None:self.process.terminate()
        drain_error=False
        while time.monotonic()<deadline:
            try:
                state=self.reader.poll(min(.05,max(0,deadline-time.monotonic())))
                if state['exit_code'] is not None and (state['stdout_eof'] or state['backpressure']):break
            except NativeProtocolError:drain_error=True;break
        forced=self.process.poll() is None
        if forced:self.process.kill()
        self.process.wait(timeout=2)
        try:self.pipeline.persist()
        except (OSError,ValueError):self.error='stop_persistence_failure'
        report={'forced_termination':forced,'stdout_drained':self.reader.eof and not self.reader.buffer,
            'unpersisted_buffer_bytes':len(self.reader.buffer),'unsaved_records':len(self.pipeline.records),
            'protocol_incomplete':drain_error}
        self.reader.close();self.process.stdout.close();self.process.stderr.close()
        self.status='stopped'
        self.stop_report=report
        return {**self.health(),**report}
