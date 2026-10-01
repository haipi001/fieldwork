importScripts('config.js','metadata.js');
const config=FIELDWORK_BROWSER_CONFIG;
let chain=Promise.resolve();
let waiting=0,waitingDropped=0;
const serialize=fn=>{const task=chain.then(fn);chain=task.catch(()=>{});return task;};
const initialized=chrome.storage.local.get('connectionId').then(state=>{if(state.connectionId!==config.connectionId)return chrome.storage.local.set({connectionId:config.connectionId,enabled:false,queue:[],pending:null,sequence:0,dropped:0,status:'尚未授权'});});
async function send() {
  await initialized;
  const state=await chrome.storage.local.get(['enabled','queue','sequence','dropped','pending']);
  if(!state.enabled)return;
  const batch=state.pending||{events:(state.queue||[]).slice(0,100),sequence:(state.sequence||0)+1};
  const {events,sequence}=batch;
  await chrome.storage.local.set({pending:batch});
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
  try {
    const response=await fetch(config.endpoint,{method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${config.token}`},
      body:JSON.stringify({sequence,events}),signal:controller.signal});
    if(response.status===409){await chrome.storage.local.set({queue:[],pending:null,status:'监控暂停或结束，已丢弃待发送记录',dropped:(state.dropped||0)+(state.queue||[]).length});return;}
    if(response.status===401){await chrome.storage.local.set({enabled:false,queue:[],pending:null,status:'配对已失效，请从 Fieldwork 重新下载采集器'});return;}
    if(response.status===422){await chrome.storage.local.set({queue:(state.queue||[]).slice(events.length),pending:null,dropped:(state.dropped||0)+events.length,status:'本批元数据无法接收，已丢弃'});return;}
    if(!response.ok)throw new Error('receiver unavailable');
    const result=await response.json();
    await chrome.storage.local.set({queue:(state.queue||[]).slice(events.length),pending:null,sequence:result.sequence,dropped:(state.dropped||0)+(result.dropped||0),status:'已连接本机 Fieldwork',lastSent:Date.now()});
  } catch {await chrome.storage.local.set({status:'本机 Fieldwork 暂不可达，将自动重试'});}
  finally {clearTimeout(timer);}
}
async function observe(details,phase) {
  const event=browserMetadata(details,config.hosts,phase);if(!event)return;
  if(waiting>=500){waitingDropped++;return;}waiting++;
  try{await initialized;await serialize(async()=>{
    const state=await chrome.storage.local.get(['enabled','queue','dropped','enabledHosts']);if(!state.enabled||!state.enabledHosts?.includes(event.host))return;
    const queue=state.queue||[];queue.push(event);
    const dropped=Math.max(0,queue.length-500);
    const lost=waitingDropped;waitingDropped=0;
    await chrome.storage.local.set({queue:queue.slice(0,500),dropped:(state.dropped||0)+dropped+lost});
    await send();
  });}finally{waiting--;}
}
function listen() {
  const filter={urls:Object.keys(config.hosts).map(host=>`https://${host}/*`)};
  if(chrome.webRequest){
    chrome.webRequest.onBeforeRequest.addListener(details=>observe(details,'started'),filter);
    chrome.webRequest.onCompleted.addListener(details=>observe(details,'completed'),filter);
    chrome.webRequest.onErrorOccurred.addListener(details=>observe(details,'error'),filter);
  }
}
chrome.permissions.contains({permissions:['webRequest']}).then(granted=>{if(granted)listen();});
chrome.alarms.create('fieldwork-heartbeat',{periodInMinutes:0.5});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='fieldwork-heartbeat')serialize(send);});
chrome.runtime.onMessage.addListener((message,sender,respond)=>{
  if(sender.id!==chrome.runtime.id||message.type!=='flush')return;
  serialize(send).then(()=>respond({ok:true}));return true;
});
chrome.permissions.onRemoved.addListener(()=>serialize(async()=>{await chrome.storage.local.set({enabled:false,queue:[],status:'授权已移除，采集停止'});}));
