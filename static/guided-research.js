(() => {
  const panel=document.createElement('section');
  panel.className='guided-panel';panel.setAttribute('aria-label','自动整理进度');
  (document.querySelector('.candidate-queue-toolbar')||document.querySelector('#candidateFindings')).before(panel);
  const steps=['准备','页面 / 源码挖掘','补齐候选','自动验证','汇总结果'];
  const labels={pending:'等待',running:'进行中',completed:'已完成',needs_input:'待继续',cancelled:'已停止',interrupted:'已中断',failed:'失败'};
  const jobs=new Map();let currentRun=null,epoch=0,timer=null,busy=false;
  function render(job){
    const active=job&&['queued','running'].includes(job.status);
    const automated=job?.result?.auto_continue;
    const exitState=!currentRun?'先选择一次分析运行。':active?'系统正在按批次连续处理；关闭页面后后台任务仍会完成当前批次。':job?.status==='failed'?'本轮失败，已保留已完成材料，可以修复阻塞项后重试。':job?.status==='interrupted'?'应用重启中断了当前轮次，可以从已保存断点继续。':job?.result?.pending_verifications||job?.result?.next_cursor?'后台正在衔接下一轮，请稍候。':job?'当前可自动完成的步骤已结束；缺少账号、证据或影响判断的项目已明确保留。':'自动使用已授权范围与剩余预算，无需填写技术表单。';
    panel.innerHTML=`<header><div><h3>候选自动化流水线</h3><p>自动采集材料、去重整理、分流排序并执行适用的独立复验。只读请求、范围、预算和并发上限始终生效。</p></div><button type="button" class="primary-action compact" data-start ${!currentRun||busy?'disabled':''}>${active?'停止自动流程':job?'重新检查并继续':'一键处理全部'}</button></header><div class="guided-flow-status"><div><small>运行方式</small><strong>${automated?'连续接力':'安全分批'}</strong></div><div><small>当前轮次</small><strong>${job?.result?.automation_round||0} / 50</strong></div><div><small>本轮读取</small><strong>${job?.result?.page_reads?.filter(x=>x.status==='recorded').length||0}</strong></div><div><small>本轮复验</small><strong>${job?.result?.new_verifications||0}</strong></div></div><ol class="guided-steps">${steps.map((label,i)=>{const step=job?.steps?.[i];return `<li data-state="${step?.status||'pending'}"><span>${i+1}</span><div><b>${label}</b><small>${esc(labels[step?.status]||'等待')}${step?.count?` · ${step.count} 项`:''}</small></div></li>`}).join('')}</ol><p class="guided-message" role="status"><strong>${esc(job?.phase||'等待启动')}</strong><span>${esc(job?.result?.summary||job?.error||exitState)}</span></p>${job?.result?.page_reads?.length?`<details><summary>本轮页面读取 · ${job.result.page_reads.length} 项</summary>${job.result.page_reads.map(read=>`<p>${esc(read.url)}<br><small>${esc(({recorded:'响应已保存',requesting:'请求结果尚未确认',not_completed:read.attempted===false?'尚未发出请求：检查范围或预算后可继续':'请求结果未确认：已保留记录，不自动重复发送'}[read.status]||'等待'))}${read.response_status?` · HTTP ${read.response_status}`:''}</small></p>`).join('')}</details>`:''}${job?.result?.pending_verifications?`<p class="guided-notice">还有 ${job.result.pending_verifications} 条复验排队，系统会自动开始下一轮。</p>`:''}${job?.result?.truncated?'<p class="guided-notice">本轮材料达到读取上限，未覆盖项会保留并进入后续批次。</p>':''}${job?.result?.remaining_candidates?`<p class="guided-notice">另有 ${job.result.remaining_candidates} 条候选尚未进入本批，系统会自动接力。</p>`:''}${job?.result?.items?.length?`<details><summary>查看已补齐材料 · ${job.result.items.length} 条</summary>${job.result.items.map(item=>`<article class="guided-material"><h4>${esc(item.title)}</h4><p>${item.sources.length} 项来源 · 已整理 ${item.automatically_filled.length} 项材料</p><p>${esc(item.auto_verification?({reproduced:'两轮对照重现读取行为；影响待确认',not_established:'两轮对照未建立漏洞证明'}[item.auto_verification.status]||'复验已结束'):item.gaps[0]?.message||'等待复验')}</p><button type="button" class="quiet-button" data-candidate="${esc(item.candidate_id)}">查看材料</button></article>`).join('')}</details>`:''}`;
    panel.querySelector('[data-start]').onclick=async()=>{
      if(active){try{await api(`/api/v1/guided-research/${job.id}/cancel`,{method:'POST'});await load(currentRun)}catch(error){toast(error.message)}}
      else await window.prepareGuidedRun(currentRun);
    };
    panel.querySelectorAll('[data-candidate]').forEach(button=>button.onclick=()=>window.openCandidateDetail(button.dataset.candidate));
  }
  async function load(id){
    const stamp=epoch;
    try{
      const job=await api(`/api/v1/runs/${encodeURIComponent(id)}/guided-research?batch_only=true`);
      if(stamp!==epoch||id!==currentRun)return;
      const previous=jobs.get(id);jobs.set(id,job);
      if(previous?.id!==job?.id||previous?.updated_at!==job?.updated_at)render(job);
      clearTimeout(timer);
      timer=setTimeout(()=>load(id),job&&['queued','running'].includes(job.status)?700:3000);
    }catch(error){if(stamp===epoch){panel.querySelector('.guided-message').textContent=error.message;clearTimeout(timer);timer=setTimeout(()=>load(id),5000)}}
  }
  window.syncGuidedResearch=()=>{
    const id=state.activeRun?.id||null;
    if(id!==currentRun){currentRun=id;epoch++;busy=false;clearTimeout(timer);render(jobs.get(id));if(id)load(id)}
  };
  window.prepareGuidedRun=async(id,candidateId=null)=>{
    if(!id||busy)return null;
    busy=true;const stamp=epoch;render(jobs.get(currentRun));
    try{
      const previous=jobs.get(id);const continuing=!candidateId&&previous?.result?.pending_verifications>0;
      const job=await api(`/api/v1/runs/${encodeURIComponent(id)}/guided-research`,{method:'POST',body:JSON.stringify({candidate_id:candidateId,execute_ready:true,read_pages:!continuing,continue_from:continuing?previous.id:null,after_candidate_id:candidateId?null:continuing?(previous.result.after_candidate_id||null):(previous?.result?.next_cursor||null),auto_continue:!candidateId,automation_round:1})});
      if(!candidateId)jobs.set(id,job);
      if(!candidateId&&stamp===epoch&&id===currentRun){render(job);clearTimeout(timer);timer=setTimeout(()=>load(id),350)}
      if(job.reused)toast('已恢复已有整理结果；相同材料不会重复处理。');
      return job;
    }catch(error){toast(error.message);return null}
    finally{if(stamp===epoch){busy=false;render(jobs.get(currentRun))}}
  };
  window.autoPrepareCandidate=async id=>{
    const candidate=state.findings.candidates.find(x=>x.id===id);
    if(!candidate)return;
    const job=await window.prepareGuidedRun(candidate.run_id,id);
    if(job)window.openCandidateDetail(id);
  };
  window.mountGuidedMaterial=async(host,candidate)=>{
    let closed=false,refreshTimer=null;
    const dialog=host.closest('dialog');
    dialog?.addEventListener('close',()=>{closed=true;clearTimeout(refreshTimer)},{once:true});
    async function refresh(){
      try{
        const job=await api(`/api/v1/runs/${candidate.run_id}/guided-research?candidate_id=${encodeURIComponent(candidate.id)}`);
        if(closed||!host.isConnected)return;
        const item=job?.result?.items?.find(x=>x.candidate_id===candidate.id);
        const active=job&&['queued','running'].includes(job.status);
        host.innerHTML=`<h3>自动补齐材料</h3><p>${item?`已从 ${item.sources.length} 项来源整理材料，原候选记录未被覆盖。`:'让系统从已有请求和源码中整理，不用填写判断依据。'}</p><button type="button" class="primary-action compact" data-prepare ${active?'disabled':''}>${active?'正在整理…':item?'继续处理':'自动处理这条候选'}</button>${item?`${item.auto_verification?`<p>${item.auto_verification.status==='reproduced'?'两轮对照已重现，实际业务影响仍待确认。':'两轮对照未建立漏洞证明。'}</p>`:''}<div class="guided-gaps">${item.gaps.map(g=>`<p>${esc(g.message)}${g.action==='identities'?'<button type="button" class="quiet-button" data-identities>打开测试账号</button>':''}</p>`).join('')}</div><details><summary>已填内容与来源</summary><dl><dt>观察到的行为</dt><dd>${esc(item.draft.observed_behavior||'缺少来源')}</dd><dt>原有假设（尚待验证）</dt><dd>${esc(item.draft.hypothesis||'未记录')}</dd></dl>${item.sources.map(source=>`<p>${esc(source.text)}<br><small>${esc(source.kind)} · ${esc(source.id)}</small></p>`).join('')}</details>`:''}`;
        host.querySelector('[data-prepare]').onclick=async()=>{const button=host.querySelector('[data-prepare]');button.disabled=true;await window.prepareGuidedRun(candidate.run_id,candidate.id);await refresh()};
        host.querySelector('[data-identities]')?.addEventListener('click',()=>{dialog?.close();window.openIdentityWorkspace(candidate.engagement_id,candidate.run_id)});
        if(active)refreshTimer=setTimeout(refresh,700);
      }catch(error){if(!closed)host.textContent=error.message}
    }
    await refresh();
  };
  render(null);window.syncGuidedResearch();
})();
