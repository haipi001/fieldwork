"""Validate the trusted broker's framed stream before acknowledging responses."""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def validate_call(call,document=None):
    if not isinstance(call,dict) or set(call)!={'request','response','request_sha256','response_sha256','observed_by'}:raise ValueError('invalid broker checkpoint')
    request,response=call['request'],call['response']
    if (not isinstance(request,dict) or set(request)!={'type','id','name','arguments'}
        or request['type']!='tool_call' or not isinstance(request['id'],str) or not 1<=len(request['id'])<=50
        or not isinstance(request['name'],str) or not 1<=len(request['name'])<=100
        or not isinstance(request['arguments'],dict) or set(request['arguments'])!={'resource'}
        or not isinstance(request['arguments']['resource'],str) or len(request['arguments']['resource'])>200
        or not isinstance(response,dict) or response.get('type')!='tool_result' or response.get('id')!=request['id']):raise ValueError('invalid tool checkpoint fields')
    allowed=request['name']=='fixture.read' and request['arguments']['resource']=='fixture://public'
    if response.get('allowed') is not allowed:raise ValueError('broker grant differs from reviewed boundary')
    if allowed:
        result=response.get('result')
        if (set(response)!={'type','id','allowed','result'} or not isinstance(result,dict) or set(result)!={'text','receipt'}
            or any(not isinstance(v,str) or len(v)>512 for v in result.values()) or document is not None and result!=document):raise ValueError('invalid synthetic read response')
    elif set(response)!={'type','id','allowed','error'} or response['error']!=('tool_not_granted' if request['name']!='fixture.read' else 'resource_not_granted'):
        raise ValueError('denied response exposes ungranted data')
    if (call['observed_by']!='fieldwork_synthetic_tool_broker' or digest(request)!=call['request_sha256']
        or digest(response)!=call['response_sha256']):raise ValueError('checkpoint digest mismatch')
    return allowed


class ToolStream:
    def __init__(self,case,input_sha256,document,on_checkpoint=None):
        self.case=case;self.input_sha256=input_sha256;self.document=document;self.on_checkpoint=on_checkpoint
        self.buffer=bytearray();self.calls=[];self.delivered=set();self.result=None
    def event(self,phase,sequence,call):
        return {'case':self.case,'input_sha256':self.input_sha256,'phase':phase,'sequence':sequence,'call':call}
    def feed(self,chunk):
        self.buffer.extend(chunk)
        if len(self.buffer)>65536:raise ValueError('broker frame exceeds limit')
        acknowledgements=0
        while b'\n' in self.buffer:
            line,_,remainder=self.buffer.partition(b'\n');self.buffer=bytearray(remainder)
            if not line.strip():continue
            if self.result is not None:raise ValueError('broker output follows completion')
            frame=json.loads(line)
            if not isinstance(frame,dict):raise ValueError('invalid broker frame')
            kind=frame.get('record')
            if kind=='prepared':
                sequence=frame.get('sequence');call=frame.get('call')
                if set(frame)!={'record','sequence','call'} or type(sequence) is not int or sequence!=len(self.calls)+1 or sequence>4:raise ValueError('broker checkpoint sequence changed')
                validate_call(call,self.document)
                if any(item['request']['id']==call['request']['id'] for item in self.calls):raise ValueError('repeated tool request id')
                self.calls.append(call)
                if self.on_checkpoint is not None and self.on_checkpoint(self.event('prepared',sequence,call)) is False:
                    raise RuntimeError('checkpoint saved but response release denied')
                acknowledgements+=1
            elif kind=='delivered':
                sequence=frame.get('sequence')
                if (set(frame)!={'record','sequence','request_id'} or type(sequence) is not int or not 1<=sequence<=len(self.calls)
                    or sequence in self.delivered or frame['request_id']!=self.calls[sequence-1]['request']['id']):raise ValueError('invalid broker delivery sequence')
                if self.on_checkpoint is not None:self.on_checkpoint(self.event('delivered',sequence,self.calls[sequence-1]))
                self.delivered.add(sequence)
            elif kind=='complete':
                value=frame.get('result')
                if (set(frame)!={'record','result'} or not isinstance(value,dict)
                    or value.get('broker_protocol')!='fieldwork.synthetic-tool-broker.v1' or value.get('calls')!=self.calls
                    or value.get('call_limit')!=4 or value.get('host_effects_allowed') is not False
                    or type(value.get('transport_complete')) is not bool):raise ValueError('invalid broker completion')
                if value['transport_complete'] and (not isinstance(value.get('final_response'),str) or len(self.delivered)!=len(self.calls)):
                    raise ValueError('broker completion missing response delivery')
                self.result=value
            else:raise ValueError('unknown broker frame')
        return acknowledgements
