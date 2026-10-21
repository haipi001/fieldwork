(() => {
  "use strict";

  window.createFieldworkTeam = ({state, esc, list, request, post, toast, newResearch, openScope, openExecution, createCampaign, configureModel, reviewRun}) => {
    const $ = selector => document.querySelector(selector);
    const t = (zh, en) => document.documentElement.lang.startsWith("en") ? en : zh;
    const bindings = window.FieldworkLocale.bindings();
    const message = (zh, en) => bindings.text($("#teamMessage"), () => t(zh, en));
    const host = $("#agentsContent");
    let engagementId = "", campaignId = "", runId = "", groupId = "";
    let campaigns = [], runs = [], groups = [], tasks = [], nextCursor = "";
    let error = "", loading = false, busy = false, requestToken = 0;
    let preview = null, previewPayload = null, requestId = "";
    let previewToken = 0, previewRequest = 0, previewTimer = null, preparingTeam=false,dialogToken=0,watchingGroup="";

    const sourceName = value => ({verified:t("文件摘要已核对","File hash checked"),changed:t("来源已变化","Source changed"),missing:t("来源缺失","Source missing"),unavailable:t("文件不可核验或超限","File unavailable or over limit"),unregistered:t("无可核验登记来源","No verifiable registered source")})[value]||t("尚未核验","Not checked");
    let resumeDialog=null;
    let executionDialog=null;
    let handoffOpen=false;
    async function reviewCandidateHandoff(taskId,claimId){
      if(handoffOpen)return;handoffOpen=true;
      const project=engagementId,campaign=campaignId;let proposal=null,epoch=0,submitting=false;
      const current=()=>engagementId===project&&campaignId===campaign;
      const modal=document.createElement('dialog');modal.className='workflow-dialog';modal.setAttribute('aria-labelledby','draftCandidateTitle');
      const base=`/api/v1/workers/research/tasks/${encodeURIComponent(taskId)}/drafts/${encodeURIComponent(claimId)}`;
      const failure=message=>({'Draft graph or registered sources are not current and verified':t('图谱或登记来源未完整核验，请先审阅材料。','Draft graph or registered sources are not current and verified'),'Candidate proposal or sources changed; review again':t('候选方案或来源已变化，请重新核对。','Candidate proposal or sources changed; review again')})[message]||message;
      function close(){if(!submitting)modal.close()}
      modal.addEventListener('cancel',event=>{if(submitting)event.preventDefault()});modal.addEventListener('close',()=>{epoch++;handoffOpen=false;modal.remove()},{once:true});
      async function load(target){
        const token=++epoch;proposal=null;
        modal.innerHTML=`<h2 id="draftCandidateTitle">${t('审阅未验证候选','Review unverified candidate')}</h2><p>${t('正在核对原始来源…','Checking registered sources…')}</p><button type="button" data-close>${t('关闭','Close')}</button>`;
        modal.querySelector('[data-close]').onclick=close;
        try{
          const plan=await request(base+'/candidate-plan'+(target?'?target='+encodeURIComponent(target):''));
          if(token!==epoch||!modal.open)return;if(!current()){modal.close();return}proposal=plan;
          modal.innerHTML=`<form class="form-stack"><h2 id="draftCandidateTitle">${t('审阅未验证候选','Review unverified candidate')}</h2><p>${esc(plan.proposal.title)}</p><p>${t('仅保存同Run来源支持的待复核候选；保留原始反证，不启动模型、目标请求或独立复验，也不确认漏洞。','Saves an unverified candidate supported by the same run; preserves original counterevidence. No model, target request, replay or finding confirmation starts.')}</p><p>${t('分类','Category')}: ${esc(plan.proposal.category)} · ${t('授权快照','Scope snapshot')}: ${esc(plan.scope_snapshot_id)}</p><label>${t('来源中的授权目标','Scoped target from source')}<select data-target><option value="">${t('选择目标','Select target')}</option>${plan.targets.map(value=>`<option value="${esc(value)}" ${value===plan.proposal.target?'selected':''}>${esc(value)}</option>`).join('')}</select></label><p>${t('支持观察','Supporting observations')}: ${plan.support_observation_ids.length} · ${t('原始反证','Original counterevidence')}: ${plan.counterevidence_ids.length}</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(plan.proposal.hypothesis)}</pre><label class="check-line"><input type="checkbox" data-authorized ${plan.can_create?'':'disabled'}> ${t('已审阅目标、来源和反证，仅保存为未验证候选','I reviewed target, sources and counterevidence; save only as an unverified candidate')}</label><p role="alert">${plan.can_create?'':t('请先选择一个来源内且在授权范围内的目标。','Select a scoped target from the registered sources first.')}</p><button type="submit" disabled>${t('保存未验证候选','Save unverified candidate')}</button><button type="button" data-close>${t('取消','Cancel')}</button></form>`;
          const submit=modal.querySelector('[type=submit]');modal.querySelector('[data-close]').onclick=close;modal.querySelector('[data-close]').focus();
          modal.querySelector('[data-target]').onchange=event=>load(event.target.value);
          modal.querySelector('[data-authorized]').onchange=event=>{submit.disabled=!proposal.can_create||!event.target.checked};
          modal.querySelector('form').onsubmit=async event=>{
            event.preventDefault();if(submitting||!current()||!proposal.can_create||!modal.querySelector('[data-authorized]').checked)return;
            submitting=true;submit.disabled=true;modal.querySelector('[data-close]').disabled=true;modal.querySelector('[data-target]').disabled=true;
            try{
              const result=await post(base+'/candidate',{authorized:true,target:proposal.proposal.target,preview_hash:proposal.preview_hash});
              if(!current()){modal.close();return}
              modal.innerHTML=`<h2 id="draftCandidateTitle">${t('未验证候选已保存','Unverified candidate saved')}</h2><p>${esc(result.id)}</p><p>${t('仍需分诊、当前授权核对和适用的独立复验。','Triage, current authority checks and applicable independent replay are still required.')}</p><button type="button" data-follow>${t('查看同Run复验候选','Review same-run candidates')}</button><button type="button" data-close>${t('关闭','Close')}</button>`;
              modal.querySelector('[data-close]').onclick=()=>modal.close();modal.querySelector('[data-close]').focus();
              modal.querySelector('[data-follow]').onclick=async()=>{try{modal.close();await reviewRun(result.run_id,[result.id])}catch(error){toast(error.message)}};
            }catch(error){if(modal.open){modal.querySelector('[role=alert]').textContent=failure(error.message);proposal.can_create=false;modal.querySelector('[data-authorized]').checked=false}}
            finally{submitting=false;if(modal.open){modal.querySelector('[data-close]').disabled=false;modal.querySelector('[data-target]')?.removeAttribute('disabled')}}
          };
        }catch(error){if(modal.open&&token===epoch)modal.querySelector('p').textContent=failure(error.message)}
      }
      modal.innerHTML='<p></p>';document.body.append(modal);modal.showModal();load();
    }

    async function reviewGroupHandoff(group){
      if(handoffOpen)return;handoffOpen=true;
      const project=engagementId,campaign=campaignId,selection=runId,base=`/api/v1/workers/research/groups/${encodeURIComponent(group)}`;
      const modal=document.createElement('dialog');modal.className='workflow-dialog';modal.setAttribute('aria-labelledby','groupHandoffTitle');
      let page=null,offset=0,chosen=new Map(),proposal=null,epoch=0,submitting=false,problem='',preparedNote='';
      const current=()=>engagementId===project&&campaignId===campaign&&runId===selection;
      const items=()=>Array.from(chosen.values()).map(({task_id,claim_id,target})=>({task_id,claim_id,target:target||null}));
      const close=()=>{if(!submitting)modal.close()};
      modal.addEventListener('cancel',e=>{if(submitting)e.preventDefault()});modal.addEventListener('close',()=>{epoch++;handoffOpen=false;modal.remove()},{once:true});
      function invalidate(){epoch++;proposal=null;problem='';preparedNote=''}
      function draw(){
        modal.innerHTML=`<h2 id="groupHandoffTitle">${t('批量审阅组内草稿','Review team drafts as a batch')}</h2><p>${t('最多选择3条；仅原子保存同组同Run的未验证候选。不会调用模型、访问目标、启动复验或生成正式发现。切页会清空选中和旧预览。','Select up to 3. Atomically saves unverified candidates from this group and Run only. No model, target request, replay or finding. Changing pages clears selection and old preview.')}</p><p>Run: ${esc(page?.run_id||'—')} · ${t('已选','Selected')}: ${chosen.size}/3</p><div data-batch-options>${(page?.items||[]).map((row,i)=>`<label class="check-line"><input type="checkbox" data-batch-pick="${i}" ${chosen.has(row.claim_id)?'checked':''}>${esc(row.title)} <small>${esc(row.task_id)}</small></label>`).join('')||`<p>${t('本页没有成功任务的草稿；可继续翻页，或先完成已有图谱分析。','No drafts from successful tasks on this page. Continue paging or analyze recorded graph first.')}</p>`}</div><button data-batch-prev ${offset===0?'disabled':''}>${t('上一页','Previous')}</button><button data-batch-next ${page?.next_offset==null?'disabled':''}>${t('下一页','Next')}</button><button data-batch-prepare ${page?.items?.length?'':'disabled'}>${t('自动准备本页可核验草稿','Prepare verifiable drafts on this page')}</button><p data-prepared-note role="status">${esc(preparedNote)}</p><button data-batch-preview ${chosen.size?'':'disabled'}>${t('预览所选候选','Preview selected candidates')}</button><div data-batch-plan>${proposal?proposal.items.map((item,i)=>`<section><h3>${esc(item.proposal.title)}</h3><p>${esc(item.proposal.category)} · Scope: ${esc(item.scope_snapshot_id)} · ${t('支持观察','Supporting observations')}: ${item.support_observation_ids.length} · ${t('反证','Counterevidence')}: ${item.counterevidence_ids.length}</p><label>${t('来源中的授权目标','Scoped target from source')}<select data-batch-target="${i}"><option value="">${t('选择目标','Select target')}</option>${item.targets.map(target=>`<option value="${esc(target)}" ${target===item.proposal.target?'selected':''}>${esc(target)}</option>`).join('')}</select></label><details><summary>${t('假设、范围与限制','Hypothesis, scope and limitations')}</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(item.proposal.hypothesis)}</pre></details></section>`).join(''):''}</div><label class="check-line"><input type="checkbox" data-batch-authorized ${proposal?.can_create?'':'disabled'}>${t('已审阅全部目标、来源与反证，仅批量保存未验证候选','I reviewed all targets, sources and counterevidence; save unverified candidates only')}</label><button data-batch-save disabled>${t('确认原子保存本批候选','Confirm atomic batch save')}</button><p role="alert">${esc(problem)}</p><button data-batch-close>${t('关闭','Close')}</button>`;
        modal.querySelector('[data-batch-close]').onclick=close;
        modal.querySelector('[data-batch-prev]').onclick=()=>load(Math.max(0,offset-5));
        modal.querySelector('[data-batch-next]').onclick=()=>load(page.next_offset);
        modal.querySelectorAll('[data-batch-pick]').forEach(box=>box.onchange=()=>{const row=page.items[Number(box.dataset.batchPick)];invalidate();if(box.checked){if(chosen.size>=3){problem=t('每批最多3条。','At most 3 claims per batch.')}else chosen.set(row.claim_id,{...row,target:null})}else chosen.delete(row.claim_id);draw()});
        modal.querySelector('[data-batch-preview]').onclick=previewBatch;
        modal.querySelector('[data-batch-prepare]').onclick=prepareBatch;
        const previewedItems=proposal?.items||[];
        modal.querySelectorAll('[data-batch-target]').forEach(select=>select.onchange=()=>{const row=previewedItems[Number(select.dataset.batchTarget)];chosen.get(row.claim_id).target=select.value||null;invalidate();modal.querySelector('[data-batch-authorized]').checked=false;modal.querySelector('[data-batch-authorized]').disabled=true;modal.querySelector('[data-batch-save]').disabled=true;modal.querySelector('[role=alert]').textContent=t('目标已变更，请重新预览。','Target changed; preview again.')});
        modal.querySelector('[data-batch-authorized]').onchange=e=>{modal.querySelector('[data-batch-save]').disabled=!e.target.checked||!proposal?.can_create};
        modal.querySelector('[data-batch-save]').onclick=saveBatch;
      }
      async function load(next){
        invalidate();const token=epoch;chosen.clear();offset=next;page=null;draw();
        try{const value=await request(base+'/candidate-handoff-options?offset='+offset);if(token!==epoch||!modal.open)return;if(!current()){modal.close();return}page=value;draw()}catch(error){if(token===epoch&&modal.open){problem=error.message;draw()}}
      }
      async function prepareBatch(){
        invalidate();const token=epoch;chosen.clear();draw();modal.querySelector('[data-batch-prepare]').disabled=true;
        try{
          const value=await request(base+'/candidate-handoff-prepare?offset='+offset);
          if(token!==epoch||!modal.open)return;if(!current()){modal.close();return}
          for(const row of value.selection){const shown=page.items.find(item=>item.task_id===row.task_id&&item.claim_id===row.claim_id);if(shown)chosen.set(row.claim_id,{...shown,target:row.target})}
          if(chosen.size!==value.selection.length)throw new Error(t('草稿清单已变化，请重新打开审阅。','Draft inventory changed; reopen review.'));
          proposal=value.plan;
          const reasons={sources_or_authority_require_review:t('来源或授权待核对','Sources or authority need review'),target_requires_manual_selection:t('目标需手动选择','Target needs manual selection'),candidate_already_saved:t('候选已保存','Candidate already saved')};
          preparedNote=t(`已准备 ${chosen.size} 条；跳过 ${value.skipped.length} 条；未检查 ${value.not_examined} 条。按任务ID和草稿记录顺序准备，不代表质量排序；仍需审阅确认。`,`Prepared ${chosen.size}; skipped ${value.skipped.length}; not examined ${value.not_examined}. Uses task ID and saved claim order, not quality ranking; review and confirmation are still required.`)+(value.skipped.length?' '+value.skipped.map(row=>`${row.claim_id}: ${reasons[row.reason]||row.reason}`).join(' · '):'');draw();
        }catch(error){if(token===epoch&&modal.open){problem=error.message;draw()}}
      }
      async function previewBatch(){
        invalidate();const token=epoch,payload={items:items()};draw();
        try{const value=await post(base+'/candidate-handoff-plan',payload);if(token!==epoch||!modal.open)return;if(!current()){modal.close();return}proposal=value;if(!value.can_create)problem=t('请为每条候选选择来源中的授权目标，再重新预览。','Select a scoped target for every candidate, then preview again.');draw()}catch(error){if(token===epoch&&modal.open){problem=error.message;draw()}}
      }
      async function saveBatch(){
        if(submitting||!proposal?.can_create||!current()||!modal.querySelector('[data-batch-authorized]').checked)return;
        const approved=proposal,payload={items:items(),preview_hash:proposal.preview_hash,authorized:true};submitting=true;
        modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=true);
        try{
          const result=await post(base+'/candidate-handoff',payload);if(!modal.open)return;if(!current()){modal.close();return}
          modal.innerHTML=`<h2 id="groupHandoffTitle">${t('本批未验证候选已保存','Unverified candidate batch saved')}</h2><p>Run: ${esc(result.run_id)}</p><ul>${result.items.map(row=>`<li>${esc(row.id)} · ${row.created?t('新增','Created'):t('已有记录','Existing')} · ${esc(row.status)}</li>`).join('')}</ul><p>${t('仍需核对本Run复验计划。此操作没有启动目标请求或确认漏洞。','Review this Run’s replay plan next. No target request or finding confirmation started.')}</p><button data-batch-follow>${t('查看同Run复验候选','Review same-run candidates')}</button><button data-batch-close>${t('关闭','Close')}</button>`;
          modal.querySelector('[data-batch-close]').onclick=()=>modal.close();modal.querySelector('[data-batch-follow]').onclick=async()=>{try{modal.close();await reviewRun(approved.run_id,result.items.map(row=>row.id))}catch(error){toast(error.message)}};
        }catch(error){if(modal.open){invalidate();problem=error.message;draw()}}finally{submitting=false}
      }
      document.body.append(modal);modal.showModal();load(0);
    }

    const option = (id, label) => `<option value="${esc(id)}">${esc(label)}</option>`;
    const stateName = value => ({
      queued:t("排队中","Queued"), leased:t("已领取","Leased"),
      running:t("运行中","Running"), paused:t("已暂停","Paused"),
      succeeded:t("已完成","Succeeded"), failed:t("失败","Failed"),
      cancelled:t("已取消","Cancelled"), active:t("活动中","Active")
    }[value] || value || "–");
    const errorName = code => ({model_call_interrupted_review:t("模型调用中断；请核对账本与已保存结果，不会自动重试","Model call interrupted; review ledger and saved results; no automatic retry"),model_output_invalid_usage_settled:t("模型输出未通过校验；已知用量已结算，请检查后重试","Model output failed validation; known usage settled, review before retry"),model_usage_unknown:t("用量未知，预算仍保留","Usage unknown; budget remains reserved"),model_budget_blocked:t("预算不足或调用名额已满","Budget or call capacity exhausted"),research_context_too_large:t("固定研究材料过大","Frozen research input is too large"),research_input_stale:t("材料已变化，请创建新队伍","Inputs changed; create a new team"),research_worker_rejected:t("输出或执行未通过校验","Output or execution failed validation"),research_worker_stopped:t("研究执行已停止","Research execution stopped")}[code]||code||"–");
    const teamFailureMessage = message => ({
      'Run counterevidence exceeds the current research input limit; no team was created':t('所选 Run 的反证超过单组输入上限（20）；组未创建，反证未省略。','Run counterevidence exceeds the per-team input limit (20); no team was created and no counterevidence was omitted.'),
      'Run counterevidence exceeds the current research input limit':t('反证超过单组输入上限（20）；分析未启动，反证未省略。','Counterevidence exceeds the per-team input limit (20); analysis was not started and no counterevidence was omitted.'),
      'research input graph changed':t('固定研究材料不一致，请核对绑定 Run 和来源后重新准备。','Frozen research inputs do not match; review the bound Run and sources before preparing again.')
    }[message]||message);
    const outputReason = code => ({output_truncated:t("生成达到输出上限，结果被截断","Generation reached its output limit; response truncated"),content_filtered:t("模型服务过滤了输出","Model service filtered the output"),non_text_content:t("模型未返回文本内容","Model returned no text content"),non_object_json:t("输出是 JSON，但不是任务要求的对象","Output is JSON but not the required object"),invalid_json:t("输出不是完整有效的 JSON","Output is not complete valid JSON"),invalid_json_constant:t("JSON 含不允许的数值常量","JSON contains a disallowed numeric constant"),json_too_deep:t("JSON 嵌套超过解析限制","JSON exceeds the nesting limit"),missing_output:t("模型响应缺少有效内容字段","Model response is missing a valid content field"),schema_mismatch:t("JSON 字段或类型不符合任务约定","JSON fields or types do not match the task contract")}[code]||"");
    const contractReason = code => ({duplicate_support_ids:t('模型重复引用支持证据','Model repeated supporting evidence IDs'),duplicate_counterevidence_ids:t('模型重复引用反证','Model repeated counterevidence IDs'),unapproved_support_ids:t('模型引用了本次输入以外的支持证据','Model cited supporting evidence outside this input'),counterevidence_set_mismatch:t('模型遗漏或添加了反证引用','Model omitted or invented counterevidence references'),invalid_limitation:t('模型限制说明不符合约定','Model limitations violate the contract'),invalid_open_question:t('模型待解决问题不符合约定','Model open questions violate the contract'),call_budget_exceeded:t('模型结果超过本次调用预算','Model result exceeds this call budget')}[code]||'');
    const outputDiagnostics = value => {
      if(!value || !Number.isSafeInteger(value.content_bytes))return '';
      const labels={trailing_content:t('JSON 后有额外内容','Content follows JSON'),unterminated_string:t('字符串未结束','Unterminated string'),invalid_escape:t('无效转义','Invalid escape'),invalid_control_character:t('无效控制字符','Invalid control character'),expected_property_name:t('缺少合法字段名','Expected property name'),expected_delimiter:t('缺少分隔符','Expected delimiter'),expected_value:t('缺少 JSON 值','Expected JSON value'),other_json_syntax:t('JSON 语法错误','JSON syntax error')};
      const location=Number.isSafeInteger(value.json_error_line)&&Number.isSafeInteger(value.json_error_column)?t('；位置 ','; position ')+value.json_error_line+':'+value.json_error_column:'';
      return t('输出字节：','Output bytes: ')+value.content_bytes+(labels[value.json_error_category]?' · '+labels[value.json_error_category]:'')+location;
    };
    const transportName = code => ({tls_verification_failed:t("模型服务证书校验失败","Model service certificate verification failed"),dns_resolution_failed:t("模型服务地址解析失败","Model service DNS resolution failed"),network_timeout:t("模型服务连接或读取超时","Model service connection or read timed out"),cloud_address_forbidden:t("模型服务解析到了非公开地址","Model service resolved to a nonpublic address"),http_status_401:t("模型服务拒绝凭据（HTTP 401）","Model service rejected credentials (HTTP 401)"),http_status_403:t("模型服务拒绝访问（HTTP 403）","Model service denied access (HTTP 403)"),http_status_429:t("模型服务限流（HTTP 429）","Model service rate limited the request (HTTP 429)")}[code] || (/^http_status_[1-5][0-9]{2}$/.test(code || "") ? t("模型服务返回 HTTP ","Model service returned HTTP ") + code.slice(12) : code ? t("模型传输未通过校验","Model transport failed validation") : ""));
    const empty = (title, detail) => `<div class="empty-state"><b>${esc(title)}</b><p>${esc(detail)}</p></div>`;
    function reset() {
      requestToken++;
      clearTimeout(previewTimer);
      previewToken++;
      $("#teamDialog").close();
      engagementId = campaignId = runId = groupId = "";
      watchingGroup="";
      campaigns = runs = groups = tasks = [];
      nextCursor = error = "";
      preview = previewPayload = null;
      render();
    }
    function eligibleRuns() {
      return runs.filter(item => item.engagement_id === engagementId && !item.synthetic &&
        ["queued", "running", "paused", "completed"].includes(item.status));
    }
    function render() {
      if(resumeDialog&&!resumeDialog.current())resumeDialog.modal.close();
      if(executionDialog&&!executionDialog.current())executionDialog.modal.close();
      bindings.apply();
      if (state.agentTab !== "inventory") return;
      if (!state.remote.engagements?.ok) {
        host.innerHTML = empty(t("项目数据不可用","Project data unavailable"),
          t("请恢复项目接口后再配置研究组。","Restore the project API before configuring research teams."));
        return;
      }
      const available = list(state.engagements);
      if (!available.some(item => item.id === engagementId)) engagementId = available[0]?.id || "";
      const project = available.find(item => item.id === engagementId);
      const selected = campaigns.find(item => item.id === campaignId);
      const run = eligibleRuns().find(item => item.id === runId);
      const canCreate = !!(!loading && !error && project?.status === "ready" && project.confirmed_at && selected?.status === "active" && run);
      const selectors = `<div class="team-toolbar">
        <label>${t("研究项目","Research project")}<select id="teamEngagement">${option("",t("选择项目","Select project"))}${available.map(item => option(item.id,item.name || item.normalized_target || item.id)).join("")}</select></label>
        <label>${t("研究 Campaign","Research campaign")}<select id="teamCampaign">${option("",t("选择 Campaign","Select campaign"))}${campaigns.map(item => option(item.id,item.name || item.id)).join("")}</select></label>
        <label>${t("绑定 Run","Bound run")}<select id="teamRun">${option("",t("选择真实 Run","Select a real run"))}${eligibleRuns().map(item => option(item.id,`${item.id} · ${stateName(item.status)}`)).join("")}</select></label>
        <button id="teamConfigureModel" type="button">${t("配置研究模型","Configure research model")}</button>
        <button id="teamRefresh" type="button" ${loading ? "disabled" : ""}>${t("刷新","Refresh")}</button>
        <button id="teamCreate" type="button" class="primary-button" ${canCreate ? "" : "disabled"}>${t("创建研究组","Create research team")}</button>
      </div>`;
      const precondition = !available.length
        ? t("先新建并确认授权研究项目。","Create and confirm a research project first.")
        : !project?.confirmed_at || project?.status !== "ready"
          ? t("当前项目的 Scope 尚未确认。","The selected project's scope is not confirmed.")
          : !campaigns.length
            ? t("请先在研究假设页建立 Research Campaign。","Create a research campaign in Hypotheses first.")
            : !selected
              ? t("选择一个研究 Campaign。","Select a research campaign.")
              : !run
                ? t("请先启动一个当前授权下的真实 Run；演示 Run 不可组队。","Start a real run under current authority; demo runs are not eligible.")
                : t("创建只排队。点击分析已有图谱后才按 Profile 调用模型，生成未验证草稿；未批准公开材料外发时强制本机。","Creation queues tasks. Analyze recorded graph explicitly invokes the profile for drafts. Unapproved context stays local.");
      const selectedGroups = groups.filter(item => item.campaign_id === campaignId);
      const shown = tasks.filter(item => !groupId || item.group_id === groupId);
      const groupCards = selectedGroups.map(item => {
        const count = item.usage?.task_count;
        const loadedTasks = tasks.filter(task => task.group_id === item.id);
        const canStart = loadedTasks.length < (count||0) || loadedTasks.some(task => task.status === "queued" && task.attempt < task.max_attempts);
        const action = item.status === "active" ? "pause" : item.status === "paused" ? "resume" : "";
        return `<article class="panel team-group ${groupId === item.id ? "selected" : ""}">
          <header><div><small class="mono">${esc(item.id)}</small><h3>${esc(item.objective)}</h3></div><span class="state ${esc(item.status)}">${esc(stateName(item.status))}</span></header>
          <p>${t("任务","Tasks")}: ${count ?? t("待读取","Loading")} · ${t("同时领取上限","Concurrent lease limit")}: ${esc(item.budget?.max_concurrency ?? "–")} · ${t("成本预算","Cost budget")}: ${esc(item.budget?.max_cost_micros ?? "–")} µ</p>
          <p>${t("模型 Profile","Model profile")}: ${esc(item.runtime_profile_id || t("未指定","Not selected"))} · Run ${esc(item.run_id || "–")}</p>
          ${item.status !== "cancelled" ? `<form data-team-limit="${esc(item.id)}" class="workspace-toolbar"><label>${t("调整组内并发","Change team concurrency")}<input name="concurrency" type="number" min="1" max="32" required value="${item.budget?.max_concurrency||1}"></label><button type="submit">${t("保存并发上限","Save concurrency limit")}</button><span role="status"></span></form>` : ""}
          ${item.strategy === "bounded_research" && item.status !== "cancelled" ? `<details><summary>${t("调整成员数量","Adjust team size")}</summary><p>${t("可在执行期间调整尚未开始的成员。已执行成员及其预算保持不变；总费用预算不增加，缩减保留取消记录。","Adjust unstarted members while the team runs. Preserves started members, their allowances and the total cost budget; removed tasks retain cancellation records.")}</p><button type="button" data-team-size-load="${esc(item.id)}">${t("读取当前规模","Load current size")}</button><div data-team-size-editor="${esc(item.id)}" role="region"></div></details>` : ""}
          <div class="team-group-actions">${item.status === "active" && item.strategy === "bounded_research" ? `<button type="button" data-team-start="${esc(item.id)}" ${busy||!canStart?"disabled":""}>${t("分析已有图谱","Analyze recorded graph")}</button>` : ""}${item.status === "active" && item.strategy === "bounded_research" ? `<button type="button" data-team-handoff="${esc(item.id)}">${t("批量审阅组内草稿","Review team drafts as a batch")}</button>` : ""}${item.strategy === "bounded_research" ? `<button type="button" data-team-resume-policy="${esc(item.id)}">${t("自动续跑设置","Automatic resume settings")}</button>` : ""}<button type="button" data-team-execution="${esc(item.id)}">${t("执行状态与恢复提示","Execution and recovery status")}</button><button type="button" data-team-select="${esc(item.id)}">${t("查看任务","View tasks")}</button>
            ${action ? `<button type="button" data-team-action="${action}" data-team-id="${esc(item.id)}">${action === "pause" ? t("暂停","Pause") : t("恢复","Resume")}</button>` : ""}
            ${item.status !== "cancelled" ? `<button type="button" data-team-action="cancel" data-team-id="${esc(item.id)}">${t("取消组","Cancel team")}</button>` : ""}
          </div></article>`;
      }).join("");
      const taskRows = shown.map(item => `<tr><td><strong>${esc(item.objective)}</strong><small class="cell-subline mono">${esc(item.id)}</small>${item.result?.status === "research_draft" ? `<p>${esc(item.result.summary)}</p><small class="cell-subline">${t("未验证草稿","Unverified draft")} · ${t("假设","Hypotheses")} ${list(item.result.claim_ids).length} · ${t("待研究问题","Open questions")} ${list(item.result.open_question_ids).length}</small><button type="button" data-inspect='${esc(JSON.stringify(item))}'>${t("查看记录与引用","Inspect record and references")}</button><button type="button" data-team-draft="${esc(item.id)}">${t("审阅草稿与原始材料","Review draft and source material")}</button>` : ""}</td>
        <td>${esc(item.role)}</td><td><span class="state ${esc(item.status)}">${esc(stateName(item.status))}</span></td>
        <td>${esc(item.lease_owner || t("未领取","Unleased"))}<small class="cell-subline">${t("到期","Expires")}: ${esc(item.lease_expires_at || "–")}</small><small class="cell-subline">${t("尝试","Attempts")}: ${esc(item.attempt ?? 0)} / ${esc(item.max_attempts ?? 0)}</small></td>
        <td>${esc(item.usage?.cost_micros ?? 0)} / ${esc(item.budget?.max_cost_micros ?? "–")} µ<small class="cell-subline">${t("输入 / 输出词元","Input / output tokens")}: ${esc(item.usage?.input_tokens ?? 0)} / ${esc(item.usage?.output_tokens ?? 0)}</small><small class="cell-subline">${t("已记录累计时长","Recorded total time")}: ${esc(item.usage?.runtime_ms ?? 0)} / ${esc(item.budget?.max_runtime_ms ?? "–")} ms${item.model_runtime?.held_ms ? ` · ${t("未确认时长保留","Unconfirmed time held")} ${esc(item.model_runtime.held_ms)} ms` : ""}</small></td>
        <td>${esc(errorName(item.error?.code || item.error?.error_type))}<small class="cell-subline">${esc(outputReason(item.error?.model_output_reason) || contractReason(item.error?.model_contract_reason) || transportName(item.error?.transport_code) || item.error?.message || item.error?.detail || "")}</small>${item.error?.model_output_diagnostics?`<small class="cell-subline">${esc(outputDiagnostics(item.error.model_output_diagnostics))}</small>`:""}${(item.error?.model_output_reason||item.error?.model_contract_reason)?`<small class="cell-subline">${t("不会自动重试；请核对模型配置和预算后明确重试。","No automatic retry. Review model configuration and budget before explicitly retrying.")}</small>`:""}</td></tr>`).join("");
      const nextStep=!loading&&!error?(!available.length?[t("新建研究项目","Create a research project"),newResearch]:!project?.confirmed_at||project.status!=="ready"?[t("核对当前项目授权","Review this project's scope"),()=>openScope(engagementId)]:!campaigns.length?[t("建立研究 Campaign","Create research campaign"),()=>createCampaign(engagementId,runId,runs)]:!run?[t("检查并启动 Run","Review and start a run"),()=>openExecution(engagementId)]:null):null;
      host.innerHTML = `${selectors}<p class="trust-note">${esc(precondition)}</p>${nextStep?`<button type="button" id="teamNextStep" class="primary-button">${esc(nextStep[0])}</button>`:""}
        ${error ? `<p class="team-error" role="alert">${esc(error)}</p>` : ""}
        ${loading ? `<p role="status">${t("正在读取研究组与任务…","Loading research teams and tasks…")}</p>` : ""}
        ${selected ? `<div class="team-summary"><span>${t("研究组","Research groups")}: ${selectedGroups.length}${selectedGroups.length === 500 ? "+" : ""}</span>
          <span>${t("已加载逻辑任务","Loaded logical tasks")}: ${tasks.length}${nextCursor ? "+" : ""}</span>
          <span>${t("已登记在线研究节点","Registered online research runners")}: ${state.remote.orchestration?.ok ? list(state.remote.orchestration.data?.runners).filter(item => item.kind === "research-worker" && item.status === "online").length : t("读取失败","Read failed")}</span></div>
          <div class="team-groups">${groupCards || (error ? empty(t("研究组读取失败","Research teams unavailable"),t("请刷新重试。","Refresh to retry.")) : loading ? "" : empty(t("尚无研究组","No research teams"),t("确认授权与 Run 后可创建队伍。","Create a team after confirming scope and selecting a run.")))}</div>
          <article class="panel"><header><h3>${t("逻辑 AgentTask","Logical agent tasks")}</h3><button type="button" id="teamAllTasks">${t("全部组","All groups")}</button></header>
            ${taskRows ? `<div class="table-wrap"><table><thead><tr><th>${t("任务","Task")}</th><th>${t("角色","Role")}</th><th>${t("状态","Status")}</th><th>${t("租约持有者","Lease owner")}</th><th>${t("费用","Cost")}</th><th>${t("错误","Error")}</th></tr></thead><tbody>${taskRows}</tbody></table></div>`
              : error ? empty(t("任务读取失败","Tasks unavailable"),t("请刷新重试。","Refresh to retry.")) : loading ? "" : nextCursor ? empty(t("已加载页中没有该组任务","No matching tasks in loaded pages"),t("继续加载后续任务页。","Load subsequent task pages.")) : empty(t("没有已记录的逻辑任务","No recorded logical tasks"),t("这里不使用 Run 或 Runner 数量填充 Agent 清单。","Run and runner counts are not used as agent inventory."))}
            ${nextCursor ? `<button type="button" id="teamMoreTasks">${t("继续加载任务","Load more tasks")}</button>` : ""}</article>` : ""}
      `;
      host.querySelectorAll("[data-team-handoff]").forEach(button=>button.onclick=()=>reviewGroupHandoff(button.dataset.teamHandoff));
      host.querySelectorAll("[data-team-draft]").forEach(button=>button.onclick=async()=>{
        const taskId=button.dataset.teamDraft, token=requestToken, campaign=campaignId;
        button.disabled=true;
        let review=host.querySelector('#teamDraftReview');
        if(!review){review=document.createElement('article');review.id='teamDraftReview';review.className='panel';host.append(review)}
        review.textContent=t("正在读取草稿材料…","Loading draft material…");
        review.dataset.task=taskId;
        try{
          const data=await request(`/api/v1/workers/research/tasks/${encodeURIComponent(taskId)}/draft`);
          if(token!==requestToken||campaign!==campaignId||!review.isConnected||review.dataset.task!==taskId)return;
          if(data.campaign_id!==campaign)throw new Error(t("研究项目上下文不一致","Research campaign context mismatch"));
          const nodes=list(data.drafts),inputs=list(data.inputs);
          review.innerHTML=`<h3>${t("研究草稿审阅","Research draft review")}</h3><p>${esc(data.summary)}</p><p class="trust-note">${t("尚未独立复验，不能作为确认发现。","Not independently verified; cannot be treated as a confirmed finding.")}</p><p>${t("Run","Run")}: ${esc(data.run_id)} · ${data.inputs_current?t("图谱输入当前一致","Graph inputs currently match"):t("图谱输入缺失或已变化，请重新研究","Graph inputs are missing or changed; research again")}</p><p>${data.registered_sources_verified?t("登记来源与产物文件摘要已核对；仍未独立复验。","Registered sources and file hashes checked; still not independently verified."):t("登记来源尚未全部核验，不能据此确认原始证据完整。","Registered sources are not fully verified; original evidence completeness is unproven.")}</p><h4>${t("假设与待研究问题","Hypotheses and open questions")}</h4>${nodes.map(node=>`<details><summary>${esc(node.title)}</summary><p>${esc(node.body)}</p><pre>${esc(JSON.stringify(node.attributes,null,2))}</pre>${node.node_type==="claim"?`<button type="button" data-draft-candidate="${esc(node.id)}">${t("审阅为未验证候选","Review as an unverified candidate")}</button>`:""}</details>`).join("")||`<p>${t("没有保存的假设或问题","No saved hypotheses or questions")}</p>`}<h4>${t("原始证据与反证","Source evidence and counterevidence")}</h4>${inputs.map(node=>`<details><summary>${esc(node.title||node.id)} · ${esc(node.node_type||"")} · ${esc(node.material_status)} · ${esc(sourceName(node.source_status))}</summary><p>${esc(node.body||"")}</p><small>${esc(node.id)}</small></details>`).join("")}`;
          review.querySelectorAll("[data-draft-candidate]").forEach(button=>button.onclick=()=>reviewCandidateHandoff(taskId,button.dataset.draftCandidate));
          if(reviewRun){
            const next=document.createElement('button');next.type='button';
            next.textContent=t("查看此 Run 的复验候选与阻塞项","Review this run's replay candidates and blockers");
            const note=document.createElement('p');note.textContent=t("仅查看同 Run 已有候选；草稿不自动转为漏洞，不启动目标请求。","Reads existing candidates from the same run; drafts are not promoted and no target requests start.");
            review.append(note,next);
            next.onclick=async()=>{next.disabled=true;try{await reviewRun(data.run_id)}catch(error){toast(error.message)}finally{if(next.isConnected)next.disabled=false}};
          }
          review.scrollIntoView({block:'start',behavior:'smooth'});
        }catch(error){if(review.isConnected&&review.dataset.task===taskId)review.textContent=error.message}
        finally{if(button.isConnected)button.disabled=false}
      });
      if($("#teamNextStep"))$("#teamNextStep").onclick=nextStep[1];
      $("#teamEngagement").value = engagementId;
      $("#teamCampaign").value = campaignId;
      $("#teamRun").value = runId;
      $("#teamEngagement").onchange = event => { engagementId = event.target.value; state.activeEngagement=engagementId; campaignId = runId = groupId = ""; refresh(); };
      $("#teamCampaign").onchange = event => { campaignId = event.target.value; groupId = ""; refreshCampaign(); };
      $("#teamRun").onchange = event => { runId = event.target.value; render(); };
      $("#teamRefresh").onclick = refresh;
      $("#teamCreate").onclick = openDialog;
      $("#teamConfigureModel").onclick=configureModel;
      $("#teamAllTasks") && ($("#teamAllTasks").onclick = () => { groupId = ""; refreshCampaign(); });
      $("#teamMoreTasks") && ($("#teamMoreTasks").onclick = loadMore);
      host.querySelectorAll("[data-team-limit]").forEach(form=>form.onsubmit=async event=>{event.preventDefault();const group=groups.find(item=>item.id===form.dataset.teamLimit),button=form.querySelector('button'),status=form.querySelector('[role=status]');button.disabled=true;try{const response=await fetch(`/api/v1/workspace/groups/${encodeURIComponent(group.id)}/concurrency`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({max_concurrency:Number(form.elements.concurrency.value),expected_updated_at:group.updated_at})});const data=await response.json();if(!response.ok)throw new Error(data.detail||response.status);await refreshCampaign();toast(t("并发上限已更新","Concurrency limit updated"))}catch(error){status.textContent=error.message}finally{button.disabled=false}});
      host.querySelectorAll("[data-team-size-load]").forEach(button => button.onclick = async () => {
        const id = button.dataset.teamSizeLoad;
        const region = [...host.querySelectorAll('[data-team-size-editor]')].find(el => el.dataset.teamSizeEditor === id);
        button.disabled = true;
        try {
          const response = await fetch(`/api/v1/workspace/groups/${encodeURIComponent(id)}/size`);
          const size = await response.json();
          if (!response.ok) throw new Error(size.detail || response.status);
          region.innerHTML = `<form class="workspace-toolbar">${['researcher','explorer','specialist'].map(role => `<label>${esc(role)}<input name="${role}" type="number" min="${Number(size.minimum_roles[role] || 0)}" max="100" required value="${Number(size.roles[role] || 0)}" ${size.editable?'':'disabled'}></label>`).join('')}<button ${size.editable?'':'disabled'}>${t("保存成员数量","Save team size")}</button><span role="status">${size.editable?'':t("当前队伍不可调整成员。","This team cannot be resized.")}</span></form>`;
          const form = region.querySelector('form');
          form.onsubmit = async event => {
            event.preventDefault(); const submit = form.querySelector('button'); submit.disabled = true;
            try {
              const body = {expected_updated_at:size.updated_at};
              for (const role of ['researcher','explorer','specialist']) body[role] = Number(form.elements[role].value);
              const result = await fetch(`/api/v1/workspace/groups/${encodeURIComponent(id)}/size`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
              const data = await result.json(); if (!result.ok) throw new Error(data.detail || result.status);
              await refreshCampaign(); toast(t("队伍规模已更新，费用总预算保持不变","Team resized; total cost budget preserved"));
            } catch(error) { form.querySelector('[role=status]').textContent = error.message; }
            finally { submit.disabled = false; }
          };
        } catch(error) { region.textContent = error.message; }
        finally { button.disabled = false; }
      });
      host.querySelectorAll("[data-team-select]").forEach(button => button.onclick = () => { groupId = button.dataset.teamSelect; refreshCampaign(); });
      host.querySelectorAll("[data-team-start]").forEach(button => button.onclick = () => startResearch(button.dataset.teamStart));
      host.querySelectorAll("[data-team-execution]").forEach(button => button.onclick = () => inspectExecution(button.dataset.teamExecution));
      host.querySelectorAll("[data-team-resume-policy]").forEach(button=>button.onclick=()=>configureResume(button.dataset.teamResumePolicy));
      host.querySelectorAll("[data-team-action]").forEach(button => button.onclick = () => act(button.dataset.teamId, button.dataset.teamAction));
    }
    async function refresh() {
      if(state.activeEngagement&&list(state.engagements).some(item=>item.id===state.activeEngagement)&&engagementId!==state.activeEngagement){engagementId=state.activeEngagement;campaignId=runId=groupId=""}
      const token = ++requestToken;
      loading = true; error = ""; campaigns = runs = groups = tasks = []; nextCursor = "";
      render();
      try {
        if (!engagementId) return;
        const [campaignResult, allRuns] = await Promise.all([
          request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`),
          request(`/api/v1/runs?mode=${encodeURIComponent(state.domain)}`)
        ]);
        if (token !== requestToken) return;
        campaigns = list(campaignResult); runs = list(allRuns);
        if (!campaigns.some(item => item.id === campaignId)) campaignId = campaigns.find(item=>item.status==="active")?.id || campaigns[0]?.id || "";
        if (!eligibleRuns().some(item => item.id === runId)) runId = eligibleRuns().find(item=>item.id===state.activeRun)?.id || eligibleRuns()[0]?.id || "";
        await refreshCampaign(token);
      } catch (failure) { if (token === requestToken) error = failure.message; }
      finally { if (token === requestToken) { loading = false; render(); } }
    }
    async function refreshCampaign(token = ++requestToken) {
      loading = true; error = ""; groups = tasks = []; nextCursor = "";
      render();
      try {
        if (!campaignId) return;
        const [groupResult, taskResult, runnerResult] = await Promise.all([
          request(`/api/v1/orchestration/groups?campaign_id=${encodeURIComponent(campaignId)}&limit=500`),
          request(`/api/v1/orchestration/tasks?campaign_id=${encodeURIComponent(campaignId)}&limit=100${groupId?"&group_id="+encodeURIComponent(groupId):""}`),
          request("/api/v1/orchestration/status")
        ]);
        if (token !== requestToken) return;
        groups = list(groupResult); tasks = list(taskResult);state.remote.orchestration={ok:true,data:runnerResult};
        nextCursor = taskResult.page?.next_cursor || "";
        if(watchingGroup){const watched=groups.find(item=>item.id===watchingGroup),loaded=tasks.filter(item=>item.group_id===watchingGroup);if(!watched||watched.status!=="active"||loaded.length>=Number(watched.usage?.task_count||0)&&!loaded.some(item=>["queued","leased","running"].includes(item.status)))watchingGroup=""}
        if (!groups.some(item => item.id === groupId)) groupId = "";
      } catch (failure) { if (token === requestToken) error = failure.message; }
      finally { if (token === requestToken) { loading = false; render(); } }
    }
    async function loadMore() {
      if (!nextCursor || loading) return;
      const cursor = nextCursor, token = requestToken;
      loading = true; render();
      try {
        const response = await request(`/api/v1/orchestration/tasks?campaign_id=${encodeURIComponent(campaignId)}&limit=100&cursor=${encodeURIComponent(cursor)}${groupId?"&group_id="+encodeURIComponent(groupId):""}`);
        if (token !== requestToken) return;
        tasks.push(...list(response)); nextCursor = response.page?.next_cursor || "";
      } catch (failure) { if (token === requestToken) error = failure.message; }
      finally { if (token === requestToken) { loading = false; render(); } }
    }
    function invalidatePreview() {
      clearTimeout(previewTimer);
      previewToken++;
      preview = previewPayload = null;
      $("#teamCommit").disabled = true;
      $("#teamAccepted").checked = false;
      bindings.forget($("#teamPreviewResult"));
      $("#teamPreviewResult").innerHTML = "";
      message("配置已变化，正在更新预检。","Configuration changed. Updating preflight.");
    }
    function payload() {
      const roles = [["researcher","#teamResearchers"],["explorer","#teamExplorers"],["specialist","#teamSpecialists"]]
        .map(([role,id]) => ({role,count:Number($(id).value)})).filter(item => item.count > 0);
      return {
        campaign_id:campaignId, run_id:runId, objective:$("#teamObjective").value.trim(),
        roles, max_concurrency:Number($("#teamConcurrency").value),
        max_cost_micros:Number($("#teamCost").value),allow_cloud_context:$("#teamCloudContext").checked,
        max_tokens_per_task:Number($("#teamTaskTokens").value),max_runtime_ms_per_task:Number($("#teamTaskRuntime").value),
        runtime_profile_id:$("#teamProfile").value || null
      };
    }
    async function openDialog() {
      const opening=++dialogToken;preparingTeam=true;
      const project = list(state.engagements).find(item => item.id === engagementId);
      if (!project?.confirmed_at || project.status !== "ready" || !campaignId || !runId){preparingTeam=false;return}
      $("#teamForm").reset();$("#teamAdvanced").open=false;
      $("#teamObjective").value = campaigns.find(item => item.id === campaignId)?.objective || "";
      const profiles = list(state.remote.runtimeConfig?.data?.profiles);
      $("#teamProfile").innerHTML = option("",t("不指定","Not selected")) +
        profiles.map(item => `<option value="${esc(item.id)}" ${item.provider_configuration_current===false?"disabled":""}>${esc(`${item.name} · ${item.mode}${item.provider_configuration_current===false?t(" · 已过期，请新建版本"," · Stale; create a new version"):""}`)}</option>`).join("");
      if(profiles.some(item=>item.id===state.activeProfile&&item.provider_configuration_current!==false))$("#teamProfile").value=state.activeProfile;
      requestId = crypto.randomUUID(); preview = previewPayload = null;
      previewToken++;
      $("#teamPreviewResult").innerHTML = "";
      bindings.forget($("#teamPreviewResult"));
      message("", "");
      $("#teamCommit").disabled = true;
      $("#teamDialog").showModal();
      message("正在匹配已有 AI 与预算…","Matching existing AI and budget…");
      try{const prepared=await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/assisted-plan${state.activeProfile?`?preferred_profile_id=${encodeURIComponent(state.activeProfile)}`:""}`);if(opening!==dialogToken||!$("#teamDialog").open)return;if(prepared.ai.enabled&&!$("#teamProfile").value){if(![...$("#teamProfile").options].some(item=>item.value===prepared.ai.profile_id))$("#teamProfile").insertAdjacentHTML("beforeend",option(prepared.ai.profile_id,prepared.ai.name));$("#teamProfile").value=prepared.ai.profile_id;if(prepared.team_defaults){const defaults=prepared.team_defaults;$("#teamConcurrency").value=defaults.max_concurrency;$("#teamCost").value=defaults.max_cost_micros;$("#teamTaskTokens").value=defaults.max_tokens_per_task;$("#teamTaskRuntime").value=defaults.max_runtime_ms_per_task}}}
      catch{message("模型自动匹配不可用，队伍可先排队；可在高级设置中选择已配置模型。","Automatic model matching unavailable. Queue a team or select a configured model in advanced settings.")}
      finally{if(opening===dialogToken){preparingTeam=false;if($("#teamDialog").open)previewTeam(true)}}
    }
    async function previewTeam(automatic=false) {
      clearTimeout(previewTimer);if(preparingTeam)return;
      for (const id of ["#teamObjective","#teamResearchers","#teamExplorers","#teamSpecialists",
                        "#teamConcurrency","#teamCost","#teamProfile","#teamTaskTokens","#teamTaskRuntime"]) {
        if (!(automatic===true?$(id).checkValidity():$(id).reportValidity())) return;
      }
      const body = payload();
      if (body.allow_cloud_context && !body.runtime_profile_id) {
        message("允许云端分析前，请先选择模型 Profile。","Select a model Profile before allowing cloud analysis.");
        return;
      }
      const token = ++previewToken;
      const requestNumber=++previewRequest;
      const count = body.roles.reduce((sum, item) => sum + item.count, 0);
      if (count < 1 || count > 100 || body.max_concurrency > count) {
        message("任务数需为 1–100，且并发不得超过任务数。","Use 1–100 tasks; concurrency cannot exceed task count.");
        return;
      }
      preview=previewPayload=null;$("#teamAccepted").checked=false;$("#teamCommit").disabled=true;
      $("#teamPreview").disabled = true;
      message("正在核对当前授权…","Checking current authority…");
      try {
        const result = await post("/api/v1/orchestration/groups/team/preview",body);
        if (token !== previewToken || !$("#teamDialog").open) return;
        preview = result; previewPayload = body;
        bindings.html($("#teamPreviewResult"), () => `<p><b>${t("当前计划","Current plan")}</b> · ${esc(result.task_count)} ${t("个逻辑任务","logical tasks")}</p>
          <details><summary>${t("系统配置与预算详情","Prepared configuration and budget")}</summary><p>Run ${esc(result.run_id)} · Scope ${esc(result.scope_snapshot_id)} · Policy ${esc(result.policy_id)}</p>
          <p>${t("并发上限","Concurrency limit")} ${esc(result.max_concurrency)} · ${t("成本上限","Cost limit")} ${esc(result.max_cost_micros)} µ</p>
          <p>${t("公开材料云发送许可","Public context cloud permission")}: ${result.cloud_context_approved?t("已确认","Confirmed"):t("未许可，强制本机","Not allowed; local only")}</p><p>${t("每任务词元","Tokens per task")} ${esc(result.max_tokens_per_task)} · ${t("累计模型时长预算","Total model time budget")} ${esc(result.max_runtime_ms_per_task)} ms · ${t("固定图谱输入","Frozen graph inputs")} ${Object.keys(result.research_input_hashes||{}).length}${result.research_input_selection?` / ${esc(result.research_input_selection.eligible_count)} · ${t("未纳入本组","Outside this group")} ${esc(result.research_input_selection.omitted_count)}`:""}</p><p>${t("本次准备不发送目标请求。分析使用固定图谱材料、所选模型和已核对预算；不会自动确认漏洞。","Preparation sends no target requests. Analysis uses frozen graph inputs, the selected model and the reviewed budget; it does not confirm vulnerabilities automatically.")}</p></details>`);
        const analyze=!!body.runtime_profile_id&&$("#teamAutoAnalyze").checked;
        bindings.text($("#teamCommit"),()=>analyze?t("创建并开始 AI 分析","Create and start AI analysis"):t("创建队伍并排队","Create team and queue tasks"));
        message(analyze?"核对后确认，系统将创建队伍并按所选模型分析已有图谱。":"核对后确认创建。没有可用模型时只排队，不模拟分析。",analyze?"Confirm to create the team and analyze recorded graph with the selected model.":"Confirm to queue the team. Without a usable model, no analysis is simulated.");
        $("#teamCommit").disabled = false;
      } catch (failure) {
        if (token !== previewToken) return;
        invalidatePreview();
        bindings.text($("#teamMessage"), () => failure.message === "cloud context requires a selected profile and explicitly public graph inputs"
          ? t("云端分析需要模型 Profile，且所有固定图谱材料必须明确标记为公开并通过敏感字段检查。","Cloud analysis requires a model Profile and all frozen graph inputs explicitly public and free of sensitive fields.") : teamFailureMessage(failure.message));
      } finally { if(requestNumber===previewRequest)$("#teamPreview").disabled = false; }
    }
    async function commitTeam(event) {
      event.preventDefault();
      if (!preview || !previewPayload || JSON.stringify(payload())!==JSON.stringify(previewPayload) || !$("#teamAccepted").checked) return;
      const analyze=!!previewPayload.runtime_profile_id&&$("#teamAutoAnalyze").checked;
      const button = $("#teamCommit"); button.disabled = true;
      message("正在原子创建研究组与任务…","Creating the team and tasks atomically…");
      try {
        const created = await post("/api/v1/orchestration/groups/team",{
          ...previewPayload, preview_hash:preview.preview_hash, idempotency_key:requestId
        });
        $("#teamDialog").close(); groupId = created.group.id;
        await refreshCampaign();
        if(analyze)await startResearch(created.group.id,true);else toast(t("研究组与任务已入队；等待研究执行节点","Team and tasks queued; awaiting a research runner"));
      } catch (failure) {
        bindings.text($("#teamMessage"), () => failure.message);
        button.disabled = false;
      }
    }
    const resumeFailureMessage = message => ({
      'Automatic candidates require current confirmed Traditional authority':t('自动候选需要当前已确认的 Traditional 授权；请核对所选Run。','Automatic candidates require current confirmed Traditional authority'),
      'Automatic candidates require verified registered input sources':t('自动候选需要全部固定来源均已登记并通过核验；请先审阅材料。','Automatic candidates require verified registered input sources'),
      'Automatic candidate authority changed':t('自动候选的授权已变化；请重新核对清单与来源。','Automatic candidate authority changed'),
      "Active bounded research group required":t("需要活动中的有界研究组；请核对研究组状态。","Active bounded research group required"),
      "Automatic resume requires an explicit runtime Profile on this group":t("该研究组尚未绑定明确的模型 Profile，无法启用续跑；请核对模型配置后使用绑定 Profile 的研究组。","Automatic resume requires an explicit runtime Profile on this group"),
      "Configured model providers are unavailable":t("Profile 中的模型服务不可用；请到“模型与路由”核对服务配置。","Configured model providers are unavailable"),
      "Select a group with 1 to 1000 unstarted queued tasks":t("需要 1–1000 个尚未开始的排队任务；请核对组内任务记录。","Select a group with 1 to 1000 unstarted queued tasks"),
      "Automatic resume supports only configured research-team tasks":t("续跑仅支持已配置的研究组任务；请核对成员角色与任务配置。","Automatic resume supports only configured research-team tasks"),
      "Freeze research inputs with the existing explicit start flow before enabling resume":t("任务尚未固定研究材料；请先通过原研究组准备流程核对并固定输入，再预览续跑。","Freeze research inputs with the existing explicit start flow before enabling resume"),
      "Unstarted resume tasks must have no model-call history":t("任务已有模型调用记录，不能作为未开始任务续跑；请核对调用账本与已保存结果。","Unstarted resume tasks must have no model-call history"),
      "Research group not found":t("研究组已不存在；请关闭弹窗并刷新清单。","Research group not found"),
      "Resume proposal changed; review again":t("续跑方案已变化；请重新预览并审阅后批准。","Resume proposal changed; review again"),
      "Resume policy changed; reload":t("续跑策略已变化；请关闭弹窗并重新读取当前策略。","Resume policy changed; reload"),
      "Resume policy is no longer enabled":t("续跑策略已关闭或被替换；请重新读取当前策略。","Resume policy is no longer enabled"),
      "Resume group or model configuration changed":t("研究组或模型配置已变化；请重新预览并审阅。","Resume group or model configuration changed"),
      "Approved task inventory changed":t("批准的任务清单已变化；请核对缺失记录并重新审阅。","Approved task inventory changed"),
      "An approved task requires review":t("批准的任务需要核对；请检查暂停、失败或已开始任务及调用账本。","An approved task requires review"),
      "Resume task changed or already started":t("任务已变化或已经开始，不能自动重放；请核对任务与调用账本。","Resume task changed or already started"),
      "Resume task has model-call history":t("任务已有模型调用记录；请核对用量与已保存结果，不会自动重放。","Resume task has model-call history"),
      "Approved unstarted task changed":t("批准的未开始任务已变化；请重新核对任务与调用账本。","Approved unstarted task changed")
    }[message]||teamFailureMessage(message));
    async function configureResume(id) {
      if(resumeDialog)return;
      const project=engagementId,campaign=campaignId,selection=runId;
      const current=()=>engagementId===project&&campaignId===campaign&&runId===selection;
      const base=`/api/v1/workers/research/groups/${encodeURIComponent(id)}`;
      const modal=document.createElement('dialog');modal.className='workflow-dialog';
      modal.setAttribute('aria-labelledby','resumePolicyTitle');let proposal=null,policy=null,saving=false,previewEpoch=0,statusPending=null,statusTimer=null;
      modal.innerHTML=`<h2 id="resumePolicyTitle">${t('自动续跑设置','Automatic resume settings')}</h2><p>${t('明确批准本组尚未开始的固定任务清单。App运行或重启后会按原Profile与预算调用模型；退出App时停止。不会重放已开始任务、运行目标工具或确认发现。配置或材料变化、任一批准任务暂停或失败时会阻断续跑，需重新审阅。关闭续跑后，本策略不再新领取，已领取任务可继续结束；手动启动的调度仍使用原控制。立即停止请用原暂停/取消组控制。','Approve an exact set of unstarted tasks. While the app runs, including after restart, it may invoke the existing profile within the original budget. It stops on app exit. No started task is replayed, target tool run or finding promoted. Configuration/input changes or any approved task pausing or failing block resumption until reviewed again. Disabling stops new claims under this policy; inflight work may finish. Manually started dispatchers continue under their existing controls. Use existing pause/cancel controls to interrupt work.')}</p><p data-message role="status"></p><div data-status></div><button type="button" data-refresh-policy>${t('刷新状态','Refresh status')}</button><p data-refresh-error role="status"></p><label>${t('草稿完成后自动保存未验证候选上限','Automatic unverified candidate limit after drafts')}<select data-candidate-limit><option value="0">${t('关闭（默认）','Off (default)')}</option><option value="1">1</option><option value="2">2</option><option value="3">3</option></select></label><p>${t('仅限本次批准任务及其固定来源、唯一授权目标；多目标留待手动审阅。按任务ID和保存顺序处理，不作质量排序。最多每轮检查5个任务，达到上限即停止；不启动复验或确认漏洞。','Only this approved task list, frozen sources and a unique scoped target. Ambiguous targets stay for manual review. Uses task ID and saved claim order, not quality ranking. Checks at most 5 tasks per pass, stopping at the limit; no replay or finding confirmation.')}</p><button type="button" data-preview>${t('预览未开始任务与当前配置','Preview unstarted tasks and current configuration')}</button><div data-plan></div><button type="button" data-disable hidden>${t('关闭自动续跑','Disable automatic resume')}</button><button type="button" data-close>${t('关闭','Close')}</button>`;
      const valid=()=>modal.open&&current();const msg=text=>{if(valid())modal.querySelector('[data-message]').textContent=text};
      const close=()=>{if(!saving)modal.close()};modal.querySelector('[data-close]').onclick=close;
      modal.addEventListener('cancel',event=>{if(saving)event.preventDefault()});
      resumeDialog={modal,current};modal.addEventListener('close',()=>{clearInterval(statusTimer);resumeDialog=null;modal.remove()},{once:true});document.body.append(modal);modal.showModal();
      modal.querySelector('[data-candidate-limit]').onchange=()=>{if(saving)return;previewEpoch++;proposal=null;modal.querySelector('[data-plan]').innerHTML='';msg('')};
      async function load(fresh=false){
        if(fresh&&statusPending)await statusPending;
        if(statusPending)return statusPending;
        statusPending=readStatus(fresh).finally(()=>{statusPending=null});
        return statusPending;
      }
      async function readStatus(duringSave){const value=await request(base+'/resume-policy');if(!valid()||(saving&&!duringSave))return;const changed=policy&&(policy.revision!==value.revision||policy.state!==value.state);policy=value;if(changed&&proposal){previewEpoch++;proposal=null;modal.querySelector('[data-plan]').innerHTML='';msg(t('策略已变化，旧批准已清空；请重新预览审阅。','Policy changed; old approval cleared. Preview and review again.'))}modal.querySelector('[data-refresh-error]').textContent='';const states={disabled:t('已关闭','Disabled'),enabled:t('已启用','Enabled'),blocked:t('待核对','Needs review'),completed:policy.task_progress?.drafts_saved===policy.task_count&&policy.task_progress?.unstarted===0&&policy.task_progress?.running===0&&policy.task_progress?.review_required===0&&policy.task_progress?.missing===0?t('本清单草稿已保存','Drafts saved for this list'):t('策略已结束；请核对任务记录','Policy ended; review task records')},schedulerStates={unobserved:t('尚未观察到','Not observed'),running:t('运行中','Running'),stopped:t('已停止','Stopped'),stale:t('心跳已陈旧','Heartbeat stale'),error:t('检查失败','Check failed')};modal.querySelector('[data-status]').dataset.state=policy.state;modal.querySelector('[data-status]').textContent=`${t('状态','State')}: ${states[policy.state]||policy.state} · ${t('批准任务数','Approved tasks')}: ${policy.task_count} · ${t('后台调度','Scheduler')}: ${schedulerStates[policy.scheduler?.state]||t('尚未观察到','Not observed')}${policy.reason?' · '+t('配置、材料或执行条件需要核对；请重新预览审阅。','Configuration, inputs or execution need review; preview and approve again.'):''}`;const counts=policy.task_progress||{};let detail=modal.querySelector('[data-progress]');if(!detail){detail=document.createElement('p');detail.dataset.progress='';modal.querySelector('[data-status]').after(detail)}detail.textContent=`${t('未开始','Unstarted')}: ${counts.unstarted??t('未提供','Unavailable')} · ${t('在途','In flight')}: ${counts.running??t('未提供','Unavailable')} · ${t('已保存草稿','Drafts saved')}: ${counts.drafts_saved??t('未提供','Unavailable')} · ${t('待核对','Needs review')}: ${counts.review_required??t('未提供','Unavailable')} · ${t('缺失记录','Missing records')}: ${counts.missing??t('未提供','Unavailable')}`;modal.querySelector('[data-disable]').hidden=policy.state!=='enabled';let review=modal.querySelector('[data-resume-review]');if(!review){review=document.createElement('section');review.dataset.resumeReview='';modal.querySelector('[data-progress]').after(review)}const reviewItems=policy.review_tasks?.items||[];review.innerHTML=reviewItems.length?`<h3>${t('本批准清单中待核对的任务','Approved tasks requiring review')}</h3>${reviewItems.map(row=>`<article><p class="mono" style="overflow-wrap:anywhere">${esc(row.id)} · ${esc(stateName(row.status))} · ${t('尝试','Attempts')}: ${esc(row.attempt)}</p><p>${esc(errorName(row.error_code))}${row.model_output_reason?' · '+esc(outputReason(row.model_output_reason)):''}</p><p>${t('模型调用账本','Model call ledger')}: ${esc(JSON.stringify(row.call_counts))}</p></article>`).join('')}<p>${t('其余待核对任务','Other tasks requiring review')}: ${esc(policy.review_tasks.remaining)} · ${t('不会自动重试；请核对账本和材料后使用原研究组控制。','No automatic retry. Review ledger and inputs before using the existing team controls.')}</p><button type="button" data-resume-review-group>${t('查看该组全部任务','View all tasks in this group')}</button>`:'';review.querySelector('[data-resume-review-group]')?.addEventListener('click',async()=>{if(!valid()||saving)return;modal.close();groupId=id;await refreshCampaign()});let saved=modal.querySelector('[data-candidate-status]');if(!saved){saved=document.createElement('p');saved.dataset.candidateStatus='';modal.querySelector('[data-status]').after(saved)}const handoff=policy.candidate_handoff;const labels={observations_only:t('仅资产观察，未自动保存安全候选','Asset observations only; no automatic security candidates'),preparing:t('准备中','Preparing'),limit_reached:t('已达到批准上限；其余请手动审阅','Approved limit reached; review remaining drafts manually'),completed:t('已检查批准清单','Approved list checked'),needs_review:t('存在需手动选择目标的草稿','Drafts require manual target selection')};saved.textContent=`${t('自动候选上限','Automatic candidate limit')}: ${policy.candidate_limit||0} · ${t('已保存未验证候选','Unverified candidates saved')}: ${handoff?.candidate_ids?.length||0}${handoff?' · '+(labels[handoff.state]||handoff.state)+' · '+t('保留资产草稿','Asset drafts retained')+': '+(handoff.non_security_drafts||0):''}`;modal.querySelector('[data-candidate-follow]')?.remove();if(handoff?.candidate_ids?.length){const follow=document.createElement('button');follow.type='button';follow.dataset.candidateFollow='';follow.textContent=t('查看本次自动保存的候选','Review candidates saved by this policy');follow.onclick=async()=>{if(!valid())return;modal.close();try{await reviewRun(handoff.run_id,handoff.candidate_ids)}catch(error){toast(error.message)}};saved.after(follow)}}
      modal.querySelector('[data-preview]').onclick=async()=>{
        if(saving)return;const epoch=++previewEpoch;modal.querySelector('[data-preview]').disabled=true;modal.querySelector('[data-candidate-limit]').disabled=true;
        proposal=null;msg('');modal.querySelector('[data-plan]').innerHTML='';
        try{const value=await post(base+'/resume-plan?candidate_limit='+encodeURIComponent(modal.querySelector('[data-candidate-limit]').value),{});if(!valid()||epoch!==previewEpoch)return;proposal=value;
          modal.querySelector('[data-plan]').innerHTML=`<p>${t('待批准任务数','Tasks to approve')}: ${esc(value.task_count)}</p><p style="overflow-wrap:anywhere">Run: ${esc(value.run_id)}</p><details><summary>${t('确切任务ID与配置摘要','Exact task IDs and configuration digests')}</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify({tasks:Object.keys(value.task_hashes),binding:value.binding},null,2))}</pre></details><label><input type="checkbox" data-approved>${t('我批准按上述清单自动调用模型，接受原预算与材料权限约束','I approve automatic model calls for this exact list within the existing budget and material permissions')}</label>${value.candidate_limit?`<label><input type="checkbox" data-candidate-approved>${t('我另外批准按上述上限自动保存未验证候选，仍需独立复验','I separately approve automatic unverified candidate creation up to this limit; independent verification is still required')}</label>`:''}<button type="button" data-enable disabled>${t('启用自动续跑','Enable automatic resume')}</button>`;
          const ready=()=>modal.querySelector('[data-approved]').checked&&(!value.candidate_limit||modal.querySelector('[data-candidate-approved]').checked);const check=()=>{modal.querySelector('[data-enable]').disabled=!ready()};modal.querySelector('[data-approved]').onchange=check;if(value.candidate_limit)modal.querySelector('[data-candidate-approved]').onchange=check;
          modal.querySelector('[data-enable]').onclick=async()=>{if(!valid()||!proposal||!ready()||saving)return;saving=true;const approved=proposal;modal.querySelector('[data-candidate-limit]').disabled=true;modal.querySelector('[data-approved]').disabled=true;if(approved.candidate_limit)modal.querySelector('[data-candidate-approved]').disabled=true;modal.querySelector('[data-enable]').disabled=true;modal.querySelector('[data-preview]').disabled=true;
            try{await post(base+'/resume-policy',{confirmed:true,preview_hash:approved.preview_hash,candidate_limit:approved.candidate_limit||0,candidate_creation_confirmed:!!approved.candidate_limit&&modal.querySelector('[data-candidate-approved]').checked});if(!valid())return;proposal=null;modal.querySelector('[data-plan]').innerHTML='';await load(true);msg(t('已启用；后台会检查原授权与预算后领取未开始任务。','Enabled; the scheduler will check current authority and budgets before claiming unstarted tasks.'))}
            catch(error){if(valid()){proposal=null;modal.querySelector('[data-plan]').innerHTML='';msg(resumeFailureMessage(error.message))}}
            finally{saving=false;if(valid()){modal.querySelector('[data-preview]').disabled=false;modal.querySelector('[data-candidate-limit]').disabled=false}}}
        }catch(error){msg(resumeFailureMessage(error.message))}finally{if(valid()){modal.querySelector('[data-preview]').disabled=false;modal.querySelector('[data-candidate-limit]').disabled=false}}
      };
      modal.querySelector('[data-disable]').onclick=async()=>{if(!policy||saving||!valid())return;previewEpoch++;saving=true;try{await post(base+'/resume-policy/disable',{revision:policy.revision});if(valid()){proposal=null;modal.querySelector('[data-plan]').innerHTML='';await load(true);msg(t('已停止新领取；在途任务可完成。','New claims stopped; inflight tasks may finish.'))}}catch(error){msg(resumeFailureMessage(error.message))}finally{saving=false}};
      const refreshStatus=async()=>{if(!valid()||saving)return;const button=modal.querySelector('[data-refresh-policy]');button.disabled=true;try{await load()}catch(error){if(valid())modal.querySelector('[data-refresh-error]').textContent=t('状态刷新失败：','Status refresh failed: ')+resumeFailureMessage(error.message)}finally{if(valid())button.disabled=false}};
      modal.querySelector('[data-refresh-policy]').onclick=refreshStatus;
      statusTimer=setInterval(()=>{if(valid()&&!saving&&!document.hidden)refreshStatus()},5000);
      await refreshStatus();
    }

    async function inspectExecution(id) {
      if(executionDialog)return;
      const project=engagementId,campaign=campaignId,selection=runId;
      const current=()=>engagementId===project&&campaignId===campaign&&runId===selection;
      const modal=document.createElement('dialog');modal.className='workflow-dialog';
      modal.setAttribute('aria-labelledby','teamExecutionTitle');
      modal.innerHTML=`<h2 id="teamExecutionTitle">${t('执行状态与恢复提示','Execution and recovery status')}</h2><p data-state role="status">${t('正在读取','Loading')}</p><div data-detail></div><button type="button" data-close>${t('关闭','Close')}</button>`;
      modal.querySelector('[data-close]').onclick=()=>modal.close();
      executionDialog={modal,current};modal.addEventListener('close',()=>{executionDialog=null;modal.remove()},{once:true});document.body.append(modal);modal.showModal();
      try {
        const value=await request(`/api/v1/workers/research/groups/${encodeURIComponent(id)}/execution`);
        if(!modal.open)return;if(!current()){modal.close();return}
        const names={not_started:t('尚未启动','Not started'),active_marker:t('执行标记心跳有效','Execution marker heartbeat is current'),stale_marker:t('执行标记已陈旧，需要核对租约与用量','Execution marker is stale; review leases and usage'),stopped:t('调度已停止','Dispatcher stopped')};
        modal.querySelector('[data-state]').textContent=names[value.state]||value.state;
        modal.querySelector('[data-detail]').innerHTML=`<p style="overflow-wrap:anywhere">${esc(id)}</p><p>${t('最后心跳','Last heartbeat')}: ${esc(value.last_heartbeat_at||'–')}</p><p>${t('未到期租约','Unexpired leases')}: ${esc(value.unexpired_leases)}</p><p>${t('中断后待核对的模型任务','Interrupted model tasks requiring review')}: ${esc(value.interrupted_model_tasks||0)}</p><h3>${t('任务状态计数','Task status counts')}</h3><pre>${esc(JSON.stringify(value.task_counts,null,2))}</pre><h3>${t('模型调用账本','Model call ledger')}</h3><pre>${esc(JSON.stringify(value.call_counts,null,2))}</pre><p>${t('心跳仅表示调度标记仍在更新，不证明研究完成。此读取不恢复任务、不调用模型。未启用自动续跑时，App重启不会自行启动任务；已明确批准的续跑策略仅可继续原清单中尚未开始的任务。已开始或用量未知的任务不会自动重放。请核对当前材料、预算、租约和用量，再使用原研究组控制继续。','A heartbeat only shows that the dispatcher marker is updating; it does not prove research completion. Reading does not resume work or call a model. Without an enabled resume policy, app restart does not start tasks. An explicitly approved policy may continue only unstarted tasks in its original list. Started or unknown-usage tasks are never automatically replayed. Review current materials, budgets, leases and usage before using existing team controls.')}</p>${value.unknown_usage_requires_review?`<p role="alert">${t('仍有进行中或用量未知的调用；须先核对用量，不能当作未调用重新执行。','Calling or unknown-usage entries remain; reconcile usage before any new execution.')}</p>`:''}`;
      } catch(error){if(modal.open&&current())modal.querySelector('[data-state]').textContent=error.message;else if(modal.open)modal.close()}
    }

    async function startResearch(id,consented=false) {
      if(busy)return;
      if(!consented&&!window.confirm(t("按组的 Profile 与已确认公开材料权限调用模型？输出仅为研究草稿，不向目标发请求。","Invoke this team's profile within its confirmed public-context permission? Outputs are research drafts; no target request is sent.")))return;
      busy=true;render();
      try {
        const value=await post(`/api/v1/workers/research/groups/${encodeURIComponent(id)}/start`,{});
        watchingGroup=id;
        toast(t(value.status==="already_running"?"研究组正在运行。":"图谱分析已启动，任务结果将自动更新。",value.status==="already_running"?"Research team is already running.":"Graph analysis started. Task results will update automatically."));
        await refreshCampaign();
      } catch(failure){error=teamFailureMessage(failure.message);render()}
      finally{busy=false;render()}
    }
    async function act(id, action) {
      const group = groups.find(item => item.id === id);
      if (!group || busy || !["pause","resume","cancel"].includes(action)) return;
      if (!window.confirm(t(`确认${action === "pause" ? "暂停" : action === "resume" ? "恢复" : "取消"}研究组 ${group.objective}？`,
        `Confirm ${action} for research team ${group.objective}?`))) return;
      busy = true;
      try {
        await post(`/api/v1/orchestration/groups/${encodeURIComponent(id)}/${action}`,{});
        await refreshCampaign();
      } catch (failure) { error = failure.message; render(); }
      finally { busy = false; }
    }
    $("#teamClose").onclick = $("#teamCancel").onclick = () => $("#teamDialog").close();
    $("#teamForm").addEventListener("input", event => { if (event.target.id !== "teamAccepted"){invalidatePreview();previewTimer=setTimeout(()=>{if($("#teamDialog").open)previewTeam(true)},500)} });
    $("#teamDialog").addEventListener("close",()=>{clearTimeout(previewTimer);previewToken++;dialogToken++;preparingTeam=false});
    $("#teamPreview").onclick = previewTeam;
    $("#teamForm").onsubmit = commitTeam;
    const poll=setInterval(()=>{if(watchingGroup&&!document.hidden&&$("#agents").classList.contains("active")&&state.agentTab==="inventory"&&!busy&&!loading&&!$("#teamDialog").open&&!document.activeElement?.matches("input,textarea,select"))refreshCampaign()},5000);
    window.addEventListener("pagehide",()=>clearInterval(poll));
    return {render, refresh, reset};
  };
})();
