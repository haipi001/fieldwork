/* Native notifications are explicitly enabled, generic, and independent of alert acknowledgement. */
(() => {
  'use strict';
  const t=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
  let host=null,localEnabled=false,current={enabled:false,authorization:'unknown',state:'loading'};
  const bridge=()=>window.webkit?.messageHandlers?.fieldworkNotifications;
  function request(action){const api=bridge();if(api)api.postMessage({action,language:document.documentElement.lang.startsWith('en')?'en':'zh'})}
  function render(){
    if(!host?.isConnected)return;
    host.replaceChildren();
    const heading=document.createElement('h4');heading.textContent=t('macOS 系统通知','macOS system notifications');host.append(heading);
    const boundary=document.createElement('p');boundary.textContent=t('仅 App 运行期间通知新出现的未处理策略线索；启动不补发历史。通知不包含目标、事件正文或凭据，也不代表独立复验。退出 App 后请在本机收件箱查看保留记录。','Notifies new unacknowledged policy signals while the app runs; startup does not backfill history. Notifications contain no target, event body or credentials and do not establish independent verification. After app exit, retained records remain in the local inbox.');host.append(boundary);
    if(!bridge()){const text=document.createElement('p');text.textContent=t('请在安装的 Fieldwork App 中配置系统通知；浏览器仍可使用本机收件箱。','Configure system notifications in the installed Fieldwork app. The local inbox remains available in a browser.');host.append(text);return}
    const statuses={loading:t('正在核对通知状态…','Checking notification state…'),disabled:t('App 通知开关已关闭','App notifications disabled'),enabled:t('App 通知开关已启用','App notifications enabled'),awaiting_permission:t('等待系统授权结果','Waiting for system authorization'),permission_denied:t('系统未授权通知，请在 macOS 系统设置中核对','System notification permission is unavailable; review macOS System Settings'),read_failed:t('告警服务读取失败，尚未确认派发','Alert service read failed; delivery is unconfirmed'),delivery_failed:t('系统通知提交失败，将保留游标重试','System notification submission failed; cursor retained for retry'),local_disabled:t('本机收件箱派发已关闭，不派发系统通知','Local inbox delivery is disabled; system notifications are suppressed')};
    const state=document.createElement('p');state.dataset.nativeAlertStatus='';state.textContent=statuses[current.state]||t('通知状态无法确认','Notification state unavailable');host.append(state);
    const permission=document.createElement('p');permission.textContent=t('系统授权：','System authorization: ')+({authorized:t('已允许','Allowed'),denied:t('未允许','Not allowed'),not_determined:t('尚未授权','Not requested'),unknown:t('尚未确认','Unconfirmed')}[current.authorization]||t('尚未确认','Unconfirmed'));host.append(permission);
    if(!localEnabled){const note=document.createElement('p');note.textContent=t('请先启用上方本机收件箱派发。','Enable local inbox delivery above first.');host.append(note)}
    const form=document.createElement('form');form.dataset.nativeAlertEnable='';
    const label=document.createElement('label');label.className='check-line';const input=document.createElement('input');input.type='checkbox';input.name='confirmed';input.required=true;label.append(input,document.createTextNode(t('确认启用通用系统提示，并由我处理 macOS 授权提示','Enable generic system notices; I will handle the macOS permission prompt')));form.append(label);
    const enable=document.createElement('button');enable.textContent=t('启用并核对系统授权','Enable and review system authorization');enable.disabled=!localEnabled||current.enabled||current.state==='awaiting_permission';form.append(enable);form.onsubmit=event=>{event.preventDefault();if(form.reportValidity()){current={...current,state:'awaiting_permission'};render();request('enable')}};host.append(form);
    const disable=document.createElement('button');disable.type='button';disable.textContent=t('停用 App 系统通知','Disable app system notifications');disable.disabled=!current.enabled&&current.state!=='awaiting_permission';disable.dataset.nativeAlertDisable='';disable.onclick=()=>request('disable');host.append(disable);
    const refresh=document.createElement('button');refresh.type='button';refresh.textContent=t('核对通知状态','Check notification state');refresh.onclick=()=>request('status');host.append(refresh);
  }
  window.addEventListener('fieldwork:native-alert-state',event=>{
    const value=event.detail;
    if(!value||typeof value.enabled!=='boolean'||typeof value.authorization!=='string'||typeof value.state!=='string')return;
    current={enabled:value.enabled,authorization:value.authorization,state:value.state};render();
  });
  document.addEventListener('fieldwork:languagechange',()=>{render();request('status')});
  window.FieldworkNativeAlerts={attach(parent,enabled){localEnabled=enabled;host=document.createElement('section');host.dataset.wNativeAlerts='';parent.append(host);render();request('status')}};
})();
