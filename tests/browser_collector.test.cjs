const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const {browserMetadata}=require('../collectors/browser-ai/metadata.js');
const hosts={'chatgpt.com':'ChatGPT','claude.ai':'Claude'};
const details={url:'https://chatgpt.com/c/private-id?token=private-token',method:'POST',type:'xmlhttprequest',tabId:3,timeStamp:Date.now(),statusCode:200,requestBody:{private:'body'},requestHeaders:[{name:'Cookie',value:'private-cookie'}]};
test('request extraction never includes private URL, headers or body',()=>{
  const result=browserMetadata(details,hosts,'completed');
  assert.equal(result.host,'chatgpt.com');assert.equal(result.status_code,200);
  assert.equal(JSON.stringify(result).includes('private'),false);
  assert.equal(browserMetadata({...details,url:'https://unapproved.example/'},hosts,'started'),null);
  assert.equal(browserMetadata({...details,tabId:-1},hosts,'started'),null);
  assert.equal(browserMetadata({...details,statusCode:0},hosts,'completed').status_code,null);
});
function worker(fetch){
  const state={connectionId:'test-connection',enabled:true,enabledHosts:['chatgpt.com'],queue:[],pending:null,sequence:0,dropped:0};
  const event={addListener(){}};
  const context={FIELDWORK_BROWSER_CONFIG:{endpoint:'http://127.0.0.1:8000/api/v1/agent-audit/browser/events',token:'test-only',connectionId:'test-connection',hosts},
    importScripts(){},browserMetadata,fetch,setTimeout,clearTimeout,AbortController,Promise,
    chrome:{storage:{local:{async get(keys){const selected=typeof keys==='string'?[keys]:keys;return Object.fromEntries(selected.map(key=>[key,structuredClone(state[key])]));},async set(value){Object.assign(state,structuredClone(value));}}},
      webRequest:{onBeforeRequest:event,onCompleted:event,onErrorOccurred:event},permissions:{async contains(){return true;},onRemoved:event},alarms:{create(){},onAlarm:event},runtime:{id:'test',onMessage:event}}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('collectors/browser-ai/worker.js','utf8'),context);
  return{context,state};
}
test('lost acknowledgement freezes the retry batch without dropping later records',async()=>{
  const sent=[];let calls=0;
  const {context,state}=worker(async(url,options)=>{sent.push(JSON.parse(options.body));if(++calls===1)throw Error('lost response');return{ok:true,status:200,async json(){return{sequence:sent.at(-1).sequence};}};});
  const a=browserMetadata(details,hosts,'started'),b=browserMetadata({...details,method:'GET'},hosts,'completed');
  state.queue=[a];await context.send();assert.equal(state.pending.events.length,1);
  state.queue.push(b);await context.send();assert.deepEqual(sent[0],sent[1]);assert.deepEqual(state.queue,[b]);
  await context.send();assert.equal(sent[2].sequence,2);assert.deepEqual(sent[2].events,[b]);assert.equal(state.queue.length,0);
});
test('previously permitted but unchecked sites are not recorded',async()=>{
  const sent=[];const {context}=worker(async(url,options)=>{sent.push(JSON.parse(options.body));return{ok:true,status:200,async json(){return{sequence:1};}};});
  await context.observe({...details,url:'https://claude.ai/private'},'started');assert.equal(sent.length,0);
  await context.observe(details,'started');assert.equal(sent.length,1);assert.equal(sent[0].events[0].host,'chatgpt.com');
});
