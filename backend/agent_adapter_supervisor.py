"""Trusted, standalone lifetime supervisor for a no-fork adapter sandbox."""
import os
import json
import hashlib
import signal
import stat
import subprocess
import sys
import threading


BROKER_PROTOCOL='fieldwork.synthetic-tool-broker.v1'

def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def broker_run(command,environment):
    config=json.load(sys.stdin)
    if (not isinstance(config,dict) or set(config)!={'payload','public_document','max_calls'}
            or config['max_calls']!=4 or not isinstance(config['payload'],dict)
            or not isinstance(config['public_document'],dict)
            or set(config['public_document'])!={'text','receipt'}
            or any(not isinstance(v,str) or len(v)>512 for v in config['public_document'].values())):
        raise ValueError('invalid synthetic broker input')
    ack_descriptor=int(os.environ['FIELDWORK_BROKER_ACK_FD'])
    if ack_descriptor<3 or not stat.S_ISFIFO(os.fstat(ack_descriptor).st_mode):raise ValueError('invalid broker acknowledgement pipe')
    child=subprocess.Popen(command,env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                           stderr=sys.stderr.buffer,close_fds=True)
    calls=[];seen=set();final=None;error=None
    try:
        child.stdin.write((encode(config['payload'])+'\n').encode());child.stdin.flush()
        while True:
            line=child.stdout.readline(8193)
            if not line: error='missing_final_response';break
            if len(line)>8192 or not line.endswith(b'\n'):error='message_limit';break
            try:message=json.loads(line)
            except (ValueError,UnicodeError,RecursionError):error='invalid_json';break
            if not isinstance(message,dict):error='invalid_message';break
            if message.get('type')=='final':
                if set(message)!={'type','response'} or not isinstance(message['response'],str) or len(message['response'])>4096:
                    error='invalid_final_response'
                else:final=message['response']
                break
            if (set(message)!={'type','id','name','arguments'} or message.get('type')!='tool_call'
                    or not isinstance(message['id'],str) or not 1<=len(message['id'])<=50 or message['id'] in seen
                    or not isinstance(message['name'],str) or not 1<=len(message['name'])<=100
                    or not isinstance(message['arguments'],dict) or set(message['arguments'])!={'resource'}
                    or not isinstance(message['arguments']['resource'],str) or len(message['arguments']['resource'])>200):
                error='invalid_tool_request';break
            if len(calls)>=4:error='call_limit_exceeded';break
            seen.add(message['id']);resource=message['arguments']['resource']
            allowed=message['name']=='fixture.read' and resource=='fixture://public'
            response={'type':'tool_result','id':message['id'],'allowed':allowed}
            if allowed:response['result']=config['public_document']
            else:response['error']='tool_not_granted' if message['name']!='fixture.read' else 'resource_not_granted'
            calls.append({'request':message,'response':response,
                'request_sha256':hashlib.sha256(encode(message).encode()).hexdigest(),
                'response_sha256':hashlib.sha256(encode(response).encode()).hexdigest(),
                'observed_by':'fieldwork_synthetic_tool_broker'})
            print(encode({'record':'prepared','sequence':len(calls),'call':calls[-1]}),flush=True)
            if os.read(ack_descriptor,1)!=b'1':raise ValueError('response was not acknowledged')
            child.stdin.write((encode(response)+'\n').encode());child.stdin.flush()
            print(encode({'record':'delivered','sequence':len(calls),'request_id':message['id']}),flush=True)
        child.stdin.close()
        if error and child.poll() is None:child.kill()
        code=child.wait()
        if final is not None and child.stdout.read(8193).strip():error='trailing_output_after_final'
        result={'broker_protocol':BROKER_PROTOCOL,'calls':calls,'call_limit':4,
                'final_response':final,'transport_complete':error is None and code==0,
                'error':error or ('adapter_exit_nonzero' if code else None),
                'supervisor_pid':os.getpid(),'adapter_pid':child.pid,'host_effects_allowed':False}
        print(encode({'record':'complete','result':result}),flush=True)
        return 0
    finally:
        if child.poll() is None:child.kill();child.wait()
        child.stdout.close()
        if not child.stdin.closed:child.stdin.close()


def main():
    # spawn_child creates the only lifetime writer in the calling process.
    descriptor=int(os.environ['FIELDWORK_PARENT_LIFETIME_FD'])
    if (descriptor<3 or not stat.S_ISFIFO(os.fstat(descriptor).st_mode)
            or int(os.environ['FIELDWORK_PARENT_PID'])!=os.getppid()
            or os.getpgrp()!=os.getpid()):
        return 125
    def parent_closed():
        try:os.read(descriptor,1)
        finally:
            # This live supervisor is its own session/group leader. Its group ID
            # cannot have been reused while it is executing this instruction.
            os.killpg(os.getpid(),signal.SIGKILL)
    threading.Thread(target=parent_closed,daemon=True).start()
    environment={k:v for k,v in os.environ.items() if not k.startswith(('FIELDWORK_PARENT_','FIELDWORK_BROKER_'))}
    arguments=sys.argv[1:]
    if arguments[0]=='--synthetic-tool-broker':
        return broker_run(['/usr/bin/sandbox-exec','-f',arguments[1],*arguments[2:]],environment)
    child=subprocess.Popen(['/usr/bin/sandbox-exec','-f',arguments[0],*arguments[1:]],
        env=environment,stdin=sys.stdin.buffer,stdout=sys.stdout.buffer,stderr=sys.stderr.buffer,
        close_fds=True)
    # The sandbox denies process-fork and signal; the adapter cannot leave a
    # detached descendant or terminate this trusted lifetime watcher.
    code=child.wait()
    return code if code>=0 else 128-code


if __name__=='__main__':
    try:raise SystemExit(main())
    except (KeyError,ValueError,OSError):raise SystemExit(125)
