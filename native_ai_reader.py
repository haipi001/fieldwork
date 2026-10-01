"""Bounded reader for a collector already started by a verified service launcher."""
import json
import os
import selectors

MAX_LINE = 262144
CHUNK = 16384


class NativeProtocolError(ValueError):
    pass


class NativeProcessReader:
    def __init__(self,process,pipeline):
        if process.stdout is None or process.stderr is None:
            raise ValueError('采集器必须提供独立的 stdout / stderr 管道')
        self.process=process;self.pipeline=pipeline;self.buffer=bytearray()
        self.events_read=0;self.stderr_bytes=0;self.eof=False
        self.selector=selectors.DefaultSelector()
        for stream,kind in [(process.stdout,'events'),(process.stderr,'diagnostic')]:
            os.set_blocking(stream.fileno(),False)
            self.selector.register(stream,selectors.EVENT_READ,kind)

    def close(self):self.selector.close()

    def _consume(self):
        while b'\n' in self.buffer:
            end=self.buffer.index(b'\n')
            if end>MAX_LINE:raise NativeProtocolError('原生输出单行超过上限')
            line=bytes(self.buffer[:end])
            try:record=json.loads(line)
            except (ValueError,UnicodeDecodeError,RecursionError):raise NativeProtocolError('原生输出不是有效 JSON 行') from None
            try:self.pipeline.accept(record)
            except BufferError:return False
            except (ValueError,TypeError):raise NativeProtocolError('原生输出未通过元数据契约') from None
            del self.buffer[:end+1];self.events_read+=1
        if len(self.buffer)>MAX_LINE:raise NativeProtocolError('原生输出单行超过上限')
        return True

    def poll(self,timeout=0):
        if type(timeout) not in {int,float} or not 0<=timeout<=1:
            raise ValueError('读取等待必须在 0–1 秒之间')
        ready=self._consume()
        for key,_ in self.selector.select(timeout):
            if key.data=='events' and (not ready or self.pipeline.pending is not None):continue
            chunk=os.read(key.fileobj.fileno(),CHUNK)
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.data=='events':self.eof=True
                continue
            if key.data=='diagnostic':
                # Count only; never expose or persist arbitrary producer stderr.
                self.stderr_bytes+=len(chunk)
            else:
                self.buffer.extend(chunk);ready=self._consume()
        if self.eof and self.buffer and b'\n' not in self.buffer:
            raise NativeProtocolError('采集器退出时留下未完成记录')
        return {'exit_code':self.process.poll(),'events_read':self.events_read,'stdout_eof':self.eof,
            'backpressure':not ready or self.pipeline.pending is not None,
            'buffered_bytes':len(self.buffer),'diagnostic_bytes':self.stderr_bytes}
