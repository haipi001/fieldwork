/* Creating a container may project reviewed recorded inputs, never execute them. */
(() => {
  'use strict';
  const t=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let project='',runs=[],selected='',preview=null,error='',loading=false,generation=0,controller=null;
  const host=()=>document.querySelector('#campaignSourceInputs');
  function describeError(message){
    const labels={
      'Run materials changed; review the preview again':['运行资料已变化，请重新核对资料后创建。','Run materials changed; recheck the materials before creating.'],
      'Run materials changed during import; Campaign was not created':['关联期间资料发生变化，Campaign 未创建，请重新核对。','Materials changed during linking; no Campaign was created. Recheck inputs.'],
      'Recorded inputs require a real Run under the current confirmed authority':['只能关联当前已确认授权下的真实 Run 资料。','Recorded inputs require a real Run under current confirmed authority.'],
      'Registered artifact location, size or bytes cannot be verified':['登记产物的位置、大小或文件无法校验，请核对原运行资料。','Registered artifact location, size or bytes cannot be verified.'],
      'Registered artifact bytes changed; no records imported':['登记产物文件已变化，没有关联任何资料。','Registered artifact bytes changed; no records were linked.'],
      'Run materials exceed the bounded Campaign import; no records imported':['资料超过本次关联上限，未关联任何记录；仍可选择不关联来创建容器。','Run materials exceed this import limit; no records were linked. You may create an empty container.'],
      'Material preview unavailable':['资料预览不可用，请稍后重新核对。','Material preview unavailable; recheck later.'],
      'Material preview lacks a matching source':['资料预览缺少匹配来源，请重新核对。','Material preview lacks a matching source; recheck inputs.'],
      'Material preview request timed out or was cancelled':['资料读取超时或已取消，请重新核对。','Material preview request timed out or was cancelled; recheck inputs.'],
      'Run not found in this project':['当前项目中找不到所选运行。','Selected Run was not found in this project.'],
      'Observation refers outside the selected Run artifacts':['观察引用了所选运行之外的产物，无法关联。','Observation refers outside the selected Run artifacts.'],
      'Evidence artifact belongs outside the selected Run':['证据产物不属于所选运行，无法关联。','Evidence artifact is outside the selected Run.'],
      'Evidence observation belongs outside the selected Run':['证据观察不属于所选运行，无法关联。','Evidence observation is outside the selected Run.'],
      'Recorded material storage is unavailable or under maintenance':['资料存储不可用或正在维护，请稍后重新核对。','Material storage is unavailable or under maintenance; recheck later.']
    };return labels[message]?t(...labels[message]):message;
  }

  function render(){
    if(!host())return;
    host().innerHTML=`<label>${t('关联已有 Run 资料（可选）','Link recorded Run materials (optional)')}<select data-campaign-source-run><option value="">${t('不关联，建立空容器','Do not link; create an empty container')}</option>${runs.map(r=>`<option value="${esc(r.id)}" ${selected===r.id?'selected':''}>${esc(r.id)} · ${esc(r.status||'')}</option>`).join('')}</select></label><p>${t('只关联所选 Run 的可校验登记资料。保持私有，仅核对登记关系和文件摘要，不代表独立复验。无登记来源的记录会列为未关联，不启动工具或模型。','Links verifiable registered inputs from the selected Run only. Inputs remain private. Registry and file hash checks do not establish independent verification. Records without registered sources are listed as excluded; no tools or models run.')}</p><div data-campaign-source-status role="status">${loading?t('正在核对已有资料…','Checking recorded materials…'):error?esc(describeError(error)):preview?esc(t(`产物 ${preview.counts.artifacts} · 观察 ${preview.counts.observations} · 证据 ${preview.counts.evidence} · 反证 ${preview.counts.counterevidence} · 未关联 ${preview.counts.excluded}`,`Artifacts ${preview.counts.artifacts} · observations ${preview.counts.observations} · evidence ${preview.counts.evidence} · counterevidence ${preview.counts.counterevidence} · excluded ${preview.counts.excluded}`)):t('未选择资料；创建不会启动任务。','No materials selected; creation starts no tasks.')}</div>${preview?.excluded.length?`<details><summary>${t('未关联记录与原因','Excluded records and reasons')}</summary><ul>${preview.excluded.map(r=>`<li>${esc(r.kind)} ${esc(r.id)} · ${esc(({no_registered_artifact:t('无登记产物，保留在原运行','No registered artifact; retained in original Run'),observation_not_importable:t('关联观察无法校验','Associated observation cannot be verified'),no_registered_source:t('无可校验登记来源','No verifiable registered source')})[r.reason]||t('来源待核对','Source requires review'))}</li>`).join('')}</ul></details>`:''}`;
    if(selected){const button=document.createElement('button');button.type='button';button.dataset.campaignSourceRefresh='';button.textContent=t('重新核对资料','Recheck materials');button.disabled=loading;button.onclick=load;host().append(button)}
    host().querySelector('[data-campaign-source-run]').onchange=event=>{selected=event.target.value;load()};
  }
  async function load(){
    const token=++generation,engagement=project,run=selected;controller?.abort();preview=null;error='';loading=!!run;render();if(!run)return;
    controller=new AbortController();const activeController=controller,timeout=setTimeout(()=>activeController.abort(),15000);
    try{
      const response=await fetch(`/api/v1/research/engagements/${encodeURIComponent(engagement)}/runs/${encodeURIComponent(run)}/campaign-source-plan`,{headers:{Accept:'application/json'},signal:activeController.signal});
      const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Material preview unavailable');
      if(token!==generation||project!==engagement||selected!==run)return;
      if(result.run_id!==run||!/^[0-9a-f]{64}$/.test(result.preview_hash||'')||!result.counts||!Array.isArray(result.excluded))throw new Error('Material preview lacks a matching source');
      preview=result;
    }catch(failure){if(token===generation)error=failure.name==='AbortError'?'Material preview request timed out or was cancelled':failure.message}
    finally{clearTimeout(timeout);if(token===generation){loading=false;render()}}
  }
  window.FieldworkCampaignSources={
    describeError,
    cancel(){generation++;controller?.abort();loading=false},
    prepare(id,preferred,available){project=id;runs=(available||[]).filter(r=>r.engagement_id===id);selected=runs.some(r=>r.id===preferred)?preferred:'';load()},
    payload(id){if(id!==project)throw new Error(t('项目已变化，请重新打开创建窗口','Project changed; reopen the creation dialog'));if(!selected)return {};if(loading||!preview||error)throw new Error(t('请等待资料校验完成，或选择不关联后创建','Wait for material validation, or choose not to link materials'));return {source_run_id:selected,source_plan_hash:preview.preview_hash}}
  };
  document.addEventListener('fieldwork:languagechange',render);
})();
