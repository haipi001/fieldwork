const sites=document.getElementById('sites'),config=FIELDWORK_BROWSER_CONFIG;
for(const [host,name] of Object.entries(config.hosts)){
  const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.value=host;input.checked=true;
  label.append(input,document.createTextNode(' '+name));sites.append(label);
}
async function refresh(){const state=await chrome.storage.local.get(['status','dropped']);document.getElementById('status').textContent=state.status||'尚未授权';document.getElementById('loss').textContent=`本次丢弃 ${state.dropped||0} 条记录（队列溢出或监控暂停）`;}
document.getElementById('enable').onclick=async()=>{
  const origins=[...sites.querySelectorAll('input:checked')].map(input=>`https://${input.value}/*`);
  if(!origins.length){document.getElementById('status').textContent='至少选择一个 AI 站点';return;}
  if(await chrome.permissions.request({permissions:['webRequest'],origins})){
    await chrome.storage.local.set({enabled:true,enabledHosts:origins.map(origin=>new URL(origin).hostname),queue:[],pending:null,status:'正在连接本机 Fieldwork'});
    // Reload the service worker so optional webRequest listeners are registered.
    chrome.runtime.reload();
  }else{document.getElementById('status').textContent='未授权，采集未启动';}
};
document.getElementById('disable').onclick=async()=>{await chrome.storage.local.set({enabled:false,queue:[],pending:null,status:'已停止采集'});await chrome.permissions.remove({permissions:['webRequest'],origins:Object.keys(config.hosts).map(host=>`https://${host}/*`)});await refresh();};
refresh();
