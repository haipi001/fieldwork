/* Read-only triage: preparation must never silently start a verification. */
(() => {
  const t=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let runs=[],selected='',items=[],cursor=null,busy=false,error=false,loaded=false,filter='actionable',epoch=0;
  const host=document.createElement('article');host.id='candidateWorkflow';host.className='panel candidate-workflow';
  document.querySelector('#verification .page-heading').after(host);
  const labels=()=>({observation:t('普通观察','Observations'),duplicate:t('重复记录','Duplicates'),needs_evidence:t('待补证据','Needs evidence'),blocked:t('需补配置','Needs configuration'),unsupported_verifier:t('待专用验证器','Verifier unavailable'),verification_ready:t('材料就绪','Materials ready'),impact_review:t('待确认影响','Impact review')});
  const reasons=()=>({linked_evidence:t('补齐缺失的引用证据','Restore missing evidence references'),evidence:t('补充请求或源码证据','Provide request or source evidence'),identities:t('配置两个有效的授权身份','Configure two valid authorized identities'),http_binding:t('补充对象归属与身份接口','Provide object ownership and identity endpoints'),security_boundary:t('先明确被违反的安全边界','Establish the violated security boundary'),isolated_replay:t('尚未接通隔离重放','Isolated replay is not connected'),unsupported_method:t('尚无适用的专用验证器','No applicable specialized verifier'),impact_review:t('核对业务权限、实际影响与反证','Review permissions, impact and counterevidence')});
  function render(){
    const names=labels(),why=reasons(),visible=items.filter(x=>filter==='all'||!['observation','duplicate'].includes(x.triage?.lane));
    const counts={};for(const item of items){const lane=item.triage?.lane||'blocked';counts[lane]=(counts[lane]||0)+1;}
    host.innerHTML=`<header><div><small>${t('处理顺序','PROCESSING ORDER')}</small><h2>${t('先分诊，再复验','Triage before verification')}</h2></div></header><p>${t('选择一次运行 → 查看分诊 → 补齐阻塞项 → 受控复验 → 独立确认。普通观察不等于漏洞。','Select a run → inspect triage → resolve blockers → controlled replay → independent confirmation. Observations are not vulnerabilities.')}</p><div class="task-toolbar"><label>${t('运行记录','Run')}<select id="workflowRun"><option value="">${t('选择运行','Select run')}</option>${runs.map(r=>`<option value="${esc(r.id)}" ${r.id===selected?'selected':''}>${esc(r.id)}</option>`).join('')}</select></label><button id="workflowRead" ${!selected||busy?'disabled':''}>${busy?t('读取中…','Loading…'):t('查看分诊','Inspect triage')}</button><label>${t('显示范围','Show')}<select id="workflowFilter"><option value="actionable" ${filter==='actionable'?'selected':''}>${t('需要处理','Needs attention')}</option><option value="all" ${filter==='all'?'selected':''}>${t('全部（含观察与重复）','All, including observations and duplicates')}</option></select></label></div><p class="trust-note">${t('此入口只读取，不启动扫描、不修改候选。材料就绪不代表获得执行授权，也不代表已验证。','This view only reads data; it does not scan or modify candidates. Ready materials establish neither execution authorization nor verification.')}</p><div role="status">${error?t('分诊读取失败。已有条目保留，请重试。','Triage read failed. Existing entries are retained; retry.'):!loaded?t('选择运行并读取当前材料。','Select a run to inspect its current materials.'):t(`已加载 ${items.length} 条；计数仅代表已加载批次。`,`Loaded ${items.length} records; counts apply only to loaded batches.`)}</div>${loaded?`<div class="workflow-counts">${Object.entries(counts).map(([key,n])=>`<span>${esc(names[key]||key)} <b>${n}</b></span>`).join('')}</div><div class="workflow-items">${visible.map(x=>`<section><h3>${esc(x.title||x.candidate_id)}</h3><p><strong>${esc(names[x.triage?.lane]||names.blocked)}</strong> · <span class="mono">${esc(x.candidate_id)}</span></p><p>${esc((x.triage?.blockers||[]).map(code=>why[code]||t('需要检查材料','Review materials')).join(' · ')||t('查看证据和当前授权，再决定复验。','Review evidence and current authorization before replay.'))}</p>${x.triage?.duplicate_of?`<p>${t('同批次原记录：','Original in this batch: ')}${esc(x.triage.duplicate_of)}</p>`:''}</section>`).join('')||`<p>${t('当前筛选没有条目；可切换“全部”查看观察记录。','No records in this filter. Choose All to inspect observations.')}</p>`}</div>${cursor?`<button id="workflowMore" ${busy?'disabled':''}>${t('继续加载','Load more')}</button>`:''}`:''}`;
    host.querySelector('#workflowRun').onchange=e=>{selected=e.target.value;epoch++;items=[];cursor=null;loaded=false;error=false;busy=false;render();};
    host.querySelector('#workflowFilter').onchange=e=>{filter=e.target.value;render();};
    host.querySelector('#workflowRead').onclick=()=>read(false);
    if(host.querySelector('#workflowMore'))host.querySelector('#workflowMore').onclick=()=>read(true);
  }
  async function read(more){
    if(!selected||busy)return;const token=++epoch,id=selected;busy=true;error=false;render();
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(`/api/v1/runs/${encodeURIComponent(id)}/candidate-workflow${more?'?after_candidate_id='+encodeURIComponent(cursor):''}`,{signal:controller.signal});
      if(!response.ok)throw new Error('read failed');const data=await response.json();
      if(token!==epoch)return;
      if(!Array.isArray(data.items))throw new Error('invalid response');
      const combined=more?[...items,...data.items]:data.items;items=[...new Map(combined.map(x=>[x.candidate_id,x])).values()];cursor=data.next_cursor||null;loaded=true;
    }catch(_){if(token===epoch)error=true;}finally{clearTimeout(timer);if(token===epoch){busy=false;render();}}
  }
  window.FieldworkCandidateWorkflow={update(next){runs=next||[];if(selected&&!runs.some(r=>r.id===selected)){epoch++;selected='';items=[];cursor=null;loaded=false;error=false;busy=false;}render();}};
  document.addEventListener('fieldwork:languagechange',render);render();
})();
