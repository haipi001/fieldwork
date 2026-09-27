import json
import subprocess
import sys
import time

import pytest
from native_ai_reader import NativeProcessReader,NativeProtocolError,MAX_LINE
from test_native_ai_forwarder import record


class Pipeline:
    pending=None
    def __init__(self):self.records=[];self.full=False
    def accept(self,value):
        import native_ai_events
        native_ai_events.normalize(value,'fixture')
        if self.full:raise BufferError()
        self.records.append(value)


def child(code):
    # Explicit fixture producer, never the Endpoint Security collector.
    return subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)


def drain(reader):
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        state=reader.poll(.05)
        if state['stdout_eof']:return state
    pytest.fail('fixture process failed to reach EOF')


def cleanup(process,reader):
    reader.close()
    if process.poll() is None:process.kill()
    process.wait(timeout=5);process.stdout.close();process.stderr.close()


def test_fragmented_records_and_private_diagnostic_are_separated():
    value=json.dumps(record())+'\n'
    code=f'import os,time;os.write(1,{value[:20].encode()!r});time.sleep(.05);os.write(2,b"private fixture");os.write(1,{value[20:].encode()!r})'
    process=child(code);pipeline=Pipeline();reader=NativeProcessReader(process,pipeline)
    try:
        state=drain(reader)
        assert state['events_read']==1 and pipeline.records[0]['actor']=='Cursor'
        assert 'private fixture' not in repr(state)
    finally:cleanup(process,reader)


def test_backpressure_keeps_original_line_until_pipeline_can_accept():
    value=(json.dumps(record())+'\n').encode()
    process=child(f'import os;os.write(1,{value!r})');pipeline=Pipeline();pipeline.full=True
    reader=NativeProcessReader(process,pipeline)
    try:
        deadline=time.monotonic()+5
        while not reader.buffer and time.monotonic()<deadline:reader.poll(.05)
        assert reader.poll()['backpressure'] and reader.events_read==0
        assert bytes(reader.buffer)==value
        pipeline.full=False
        assert drain(reader)['events_read']==1
    finally:cleanup(process,reader)


@pytest.mark.parametrize('output',[b'{"unfinished":',b'not json\n',
    (json.dumps({**record(),'synthetic':True})+'\n').encode()])
def test_incomplete_invalid_or_simulated_output_is_rejected(output):
    process=child(f'import os;os.write(1,{output!r})');reader=NativeProcessReader(process,Pipeline())
    try:
        with pytest.raises(NativeProtocolError):drain(reader)
    finally:cleanup(process,reader)


def test_unbounded_line_is_rejected_without_unbounded_memory():
    process=child(f'import os;os.write(1,b"x"*{MAX_LINE+16384})');reader=NativeProcessReader(process,Pipeline())
    try:
        with pytest.raises(NativeProtocolError):drain(reader)
        assert len(reader.buffer)<=MAX_LINE+16384
    finally:cleanup(process,reader)
