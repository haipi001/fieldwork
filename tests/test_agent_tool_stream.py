import hashlib,json
import pytest
from agent_tool_stream import ToolStream

DOCUMENT={'text':'Owned synthetic fixture','receipt':'random-fixture-receipt'}


def frame(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()

def call(resource='fixture://public',allowed=True):
    request={'type':'tool_call','id':'owned','name':'fixture.read','arguments':{'resource':resource}}
    response={'type':'tool_result','id':'owned','allowed':allowed}
    if allowed:response['result']=DOCUMENT
    else:response['error']='resource_not_granted'
    sha=lambda value:hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    return {'request':request,'response':response,'request_sha256':sha(request),'response_sha256':sha(response),'observed_by':'fieldwork_synthetic_tool_broker'}


def test_split_frame_does_not_acknowledge_before_complete_validated_checkpoint():
    persisted=[];stream=ToolStream('control','0'*64,DOCUMENT,lambda event:persisted.append(event))
    encoded=frame({'record':'prepared','sequence':1,'call':call()})
    assert stream.feed(encoded[:11])==0 and persisted==[]
    assert stream.feed(encoded[11:])==1 and len(persisted)==1
    assert persisted[0]['phase']=='prepared'
    assert stream.feed(frame({'record':'delivered','sequence':1,'request_id':'owned'}))==0
    assert persisted[-1]['phase']=='delivered'


def test_invalid_grant_and_duplicate_checkpoint_never_receive_acknowledgement():
    persisted=[];stream=ToolStream('control','0'*64,DOCUMENT,lambda event:persisted.append(event))
    with pytest.raises(ValueError):stream.feed(frame({'record':'prepared','sequence':1,'call':call('fixture://private',True)}))
    assert persisted==[]
    stream=ToolStream('control','0'*64,DOCUMENT,lambda event:persisted.append(event))
    encoded=frame({'record':'prepared','sequence':1,'call':call()})
    assert stream.feed(encoded)==1
    with pytest.raises(ValueError):stream.feed(encoded)
    assert len(persisted)==1


def test_failed_persistence_cannot_acknowledge_broker_response():
    def fail(_):raise RuntimeError('owned persistence failure')
    stream=ToolStream('control','0'*64,DOCUMENT,fail)
    with pytest.raises(RuntimeError,match='owned persistence failure'):
        stream.feed(frame({'record':'prepared','sequence':1,'call':call()}))
    assert stream.delivered==set() and stream.result is None
