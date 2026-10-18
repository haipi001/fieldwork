(() => {
  "use strict";

  window.createFieldworkTeam = ({state, esc, list, request, post, toast, newResearch, openScope, openExecution, createCampaign, configureModel}) => {
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

    const option = (id, label) => `<option value="${esc(id)}">${esc(label)}</option>`;
    const stateName = value => ({
      queued:t("排队中","Queued"), leased:t("已领取","Leased"),
      running:t("运行中","Running"), paused:t("已暂停","Paused"),
      succeeded:t("已完成","Succeeded"), failed:t("失败","Failed"),
      cancelled:t("已取消","Cancelled"), active:t("活动中","Active")
    }[value] || value || "–");
    const errorName = code => ({model_output_invalid_usage_settled:t("模型输出未通过校验；已知用量已结算，请检查后重试","Model output failed validation; known usage settled, review before retry"),model_usage_unknown:t("用量未知，预算仍保留","Usage unknown; budget remains reserved"),model_budget_blocked:t("预算不足或调用名额已满","Budget or call capacity exhausted"),research_context_too_large:t("固定研究材料过大","Frozen research input is too large"),research_input_stale:t("材料已变化，请创建新队伍","Inputs changed; create a new team"),research_worker_rejected:t("输出或执行未通过校验","Output or execution failed validation"),research_worker_stopped:t("研究执行已停止","Research execution stopped")}[code]||code||"–");
    const outputReason = code => ({output_truncated:t("生成达到输出上限，结果被截断","Generation reached its output limit; response truncated"),content_filtered:t("模型服务过滤了输出","Model service filtered the output"),non_text_content:t("模型未返回文本内容","Model returned no text content"),non_object_json:t("输出是 JSON，但不是任务要求的对象","Output is JSON but not the required object"),invalid_json:t("输出不是完整有效的 JSON","Output is not complete valid JSON"),invalid_json_constant:t("JSON 含不允许的数值常量","JSON contains a disallowed numeric constant"),json_too_deep:t("JSON 嵌套超过解析限制","JSON exceeds the nesting limit"),missing_output:t("模型响应缺少有效内容字段","Model response is missing a valid content field"),schema_mismatch:t("JSON 字段或类型不符合任务约定","JSON fields or types do not match the task contract")}[code]||"");
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
          <div class="team-group-actions">${item.status === "active" && item.strategy === "bounded_research" ? `<button type="button" data-team-start="${esc(item.id)}" ${busy||!canStart?"disabled":""}>${t("分析已有图谱","Analyze recorded graph")}</button>` : ""}<button type="button" data-team-select="${esc(item.id)}">${t("查看任务","View tasks")}</button>
            ${action ? `<button type="button" data-team-action="${action}" data-team-id="${esc(item.id)}">${action === "pause" ? t("暂停","Pause") : t("恢复","Resume")}</button>` : ""}
            ${item.status !== "cancelled" ? `<button type="button" data-team-action="cancel" data-team-id="${esc(item.id)}">${t("取消组","Cancel team")}</button>` : ""}
          </div></article>`;
      }).join("");
      const taskRows = shown.map(item => `<tr><td><strong>${esc(item.objective)}</strong><small class="cell-subline mono">${esc(item.id)}</small>${item.result?.status === "research_draft" ? `<p>${esc(item.result.summary)}</p><small class="cell-subline">${t("未验证草稿","Unverified draft")} · ${t("假设","Hypotheses")} ${list(item.result.claim_ids).length} · ${t("待研究问题","Open questions")} ${list(item.result.open_question_ids).length}</small><button type="button" data-inspect='${esc(JSON.stringify(item))}'>${t("查看记录与引用","Inspect record and references")}</button><button type="button" data-team-draft="${esc(item.id)}">${t("审阅草稿与原始材料","Review draft and source material")}</button>` : ""}</td>
        <td>${esc(item.role)}</td><td><span class="state ${esc(item.status)}">${esc(stateName(item.status))}</span></td>
        <td>${esc(item.lease_owner || t("未领取","Unleased"))}<small class="cell-subline">${t("到期","Expires")}: ${esc(item.lease_expires_at || "–")}</small><small class="cell-subline">${t("尝试","Attempts")}: ${esc(item.attempt ?? 0)} / ${esc(item.max_attempts ?? 0)}</small></td>
        <td>${esc(item.usage?.cost_micros ?? 0)} / ${esc(item.budget?.max_cost_micros ?? "–")} µ<small class="cell-subline">${t("输入 / 输出词元","Input / output tokens")}: ${esc(item.usage?.input_tokens ?? 0)} / ${esc(item.usage?.output_tokens ?? 0)}</small><small class="cell-subline">${t("已记录累计时长","Recorded total time")}: ${esc(item.usage?.runtime_ms ?? 0)} / ${esc(item.budget?.max_runtime_ms ?? "–")} ms${item.model_runtime?.held_ms ? ` · ${t("未确认时长保留","Unconfirmed time held")} ${esc(item.model_runtime.held_ms)} ms` : ""}</small></td>
        <td>${esc(errorName(item.error?.code || item.error?.error_type))}<small class="cell-subline">${esc(outputReason(item.error?.model_output_reason) || transportName(item.error?.transport_code) || item.error?.message || item.error?.detail || "")}</small>${item.error?.model_output_reason?`<small class="cell-subline">${t("不会自动重试；请核对模型配置和预算后明确重试。","No automatic retry. Review model configuration and budget before explicitly retrying.")}</small>`:""}</td></tr>`).join("");
      const nextStep=!loading&&!error?(!available.length?[t("新建研究项目","Create a research project"),newResearch]:!project?.confirmed_at||project.status!=="ready"?[t("核对当前项目授权","Review this project's scope"),()=>openScope(engagementId)]:!campaigns.length?[t("建立研究 Campaign","Create research campaign"),()=>createCampaign(engagementId)]:!run?[t("检查并启动 Run","Review and start a run"),()=>openExecution(engagementId)]:null):null;
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
          review.innerHTML=`<h3>${t("研究草稿审阅","Research draft review")}</h3><p>${esc(data.summary)}</p><p class="trust-note">${t("尚未独立复验，不能作为确认发现。","Not independently verified; cannot be treated as a confirmed finding.")}</p><p>${t("Run","Run")}: ${esc(data.run_id)} · ${data.inputs_current?t("原始材料当前一致","Source material currently matches"):t("原始材料缺失或已变化，请重新研究","Source material is missing or changed; research again")}</p><h4>${t("假设与待研究问题","Hypotheses and open questions")}</h4>${nodes.map(node=>`<details><summary>${esc(node.title)}</summary><p>${esc(node.body)}</p><pre>${esc(JSON.stringify(node.attributes,null,2))}</pre></details>`).join("")||`<p>${t("没有保存的假设或问题","No saved hypotheses or questions")}</p>`}<h4>${t("原始证据与反证","Source evidence and counterevidence")}</h4>${inputs.map(node=>`<details><summary>${esc(node.title||node.id)} · ${esc(node.node_type||"")} · ${esc(node.material_status)}</summary><p>${esc(node.body||"")}</p><small>${esc(node.id)}</small></details>`).join("")}`;
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
      $("#teamAllTasks") && ($("#teamAllTasks").onclick = () => { groupId = ""; render(); });
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
      host.querySelectorAll("[data-team-select]").forEach(button => button.onclick = () => { groupId = button.dataset.teamSelect; render(); });
      host.querySelectorAll("[data-team-start]").forEach(button => button.onclick = () => startResearch(button.dataset.teamStart));
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
          request(`/api/v1/orchestration/tasks?campaign_id=${encodeURIComponent(campaignId)}&limit=100`),
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
        const response = await request(`/api/v1/orchestration/tasks?campaign_id=${encodeURIComponent(campaignId)}&limit=100&cursor=${encodeURIComponent(cursor)}`);
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
          <p>${t("公开材料云发送许可","Public context cloud permission")}: ${result.cloud_context_approved?t("已确认","Confirmed"):t("未许可，强制本机","Not allowed; local only")}</p><p>${t("每任务词元","Tokens per task")} ${esc(result.max_tokens_per_task)} · ${t("累计模型时长预算","Total model time budget")} ${esc(result.max_runtime_ms_per_task)} ms · ${t("固定图谱输入","Frozen graph inputs")} ${Object.keys(result.research_input_hashes||{}).length}</p><p>${t("本次准备不发送目标请求。分析使用固定图谱材料、所选模型和已核对预算；不会自动确认漏洞。","Preparation sends no target requests. Analysis uses frozen graph inputs, the selected model and the reviewed budget; it does not confirm vulnerabilities automatically.")}</p></details>`);
        const analyze=!!body.runtime_profile_id&&$("#teamAutoAnalyze").checked;
        bindings.text($("#teamCommit"),()=>analyze?t("创建并开始 AI 分析","Create and start AI analysis"):t("创建队伍并排队","Create team and queue tasks"));
        message(analyze?"核对后确认，系统将创建队伍并按所选模型分析已有图谱。":"核对后确认创建。没有可用模型时只排队，不模拟分析。",analyze?"Confirm to create the team and analyze recorded graph with the selected model.":"Confirm to queue the team. Without a usable model, no analysis is simulated.");
        $("#teamCommit").disabled = false;
      } catch (failure) {
        if (token !== previewToken) return;
        invalidatePreview();
        bindings.text($("#teamMessage"), () => failure.message === "cloud context requires a selected profile and explicitly public graph inputs"
          ? t("云端分析需要模型 Profile，且所有固定图谱材料必须明确标记为公开并通过敏感字段检查。","Cloud analysis requires a model Profile and all frozen graph inputs explicitly public and free of sensitive fields.") : failure.message);
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
    async function startResearch(id,consented=false) {
      if(busy)return;
      if(!consented&&!window.confirm(t("按组的 Profile 与已确认公开材料权限调用模型？输出仅为研究草稿，不向目标发请求。","Invoke this team's profile within its confirmed public-context permission? Outputs are research drafts; no target request is sent.")))return;
      busy=true;render();
      try {
        const value=await post(`/api/v1/workers/research/groups/${encodeURIComponent(id)}/start`,{});
        watchingGroup=id;
        toast(t(value.status==="already_running"?"研究组正在运行。":"图谱分析已启动，任务结果将自动更新。",value.status==="already_running"?"Research team is already running.":"Graph analysis started. Task results will update automatically."));
        await refreshCampaign();
      } catch(failure){error=failure.message;render()}
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
