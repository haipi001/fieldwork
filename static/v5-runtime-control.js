(() => {
  "use strict";
  window.createFieldworkRuntimeControl = ({state, esc, list, request, onConfig=()=>{}}) => {
    const $ = selector => document.querySelector(selector);
    const t = (zh, en) => document.documentElement.lang.startsWith("en") ? en : zh;
    const copy = (zh, en) => `<span data-runtime-zh="${esc(zh)}" data-runtime-en="${esc(en)}">${esc(t(zh,en))}</span>`;
    const label = (zh, en, field) => `<label>${copy(zh,en)}${field}</label>`;
    const input = (id, type="text", attrs="") => `<input id="${id}" type="${type}" ${attrs}>`;
    const number = (id, value, min, max, step=1) => input(id,"number",`required value="${value}" min="${min}" max="${max}" step="${step}"`);
    const check = (id, zh, en, checked=false) => `<label class="check-line">${input(id,"checkbox",checked ? "checked" : "")}${copy(zh,en)}</label>`;
    const button = (id, zh, en, type="button") => `<button id="${id}" type="${type}">${copy(zh,en)}</button>`;
    const bindings = window.FieldworkLocale.bindings();
    let calls=[], providers=[], profiles=[], campaigns=[], campaignId="", engagementId="", policy=null;
    let generation=0, busy=false, offset=0, routes=[], routePage={}, failure="";
    let summary=null, summaryError="";
    let importedReview=null,reviewGeneration=0;
    let reconciliations=null,reconciliationError="";
    let evalRuns=null, evalError="";
    let policyDecisions=null, policyError="";
    let incidentResponse=null, incidentError="", incidentGeneration=0;
    let evidencePage=null, evidenceError="", evidenceGeneration=0;
    const panel = document.createElement("article");
    panel.id="runtimeControl"; panel.className="panel runtime-control";
    panel.innerHTML=`<header><h2>${copy("V5 模型配置与研究策略","V5 model configuration and research policy")}</h2>${button("rcRefresh","刷新配置","Refresh configuration")}</header>
      <p>${copy("Profile 是不可变配置版本。研究组引用指定版本；创建或浏览配置不会调用模型。","Profiles are immutable configuration versions referenced by research teams. Creating or viewing configuration does not invoke a model.")}</p>
      <p id="rcMessage" role="status"></p>
      <section aria-labelledby="rcSummaryTitle"><h3 id="rcSummaryTitle">${copy("运行状态快照","Runtime state snapshot")}</h3><div id="rcSummary" aria-live="polite"></div></section>
      <details><summary>${copy("模型用量核销审计","Model usage reconciliation audit")}</summary><p>${copy("审核来源由操作员声明，当前未验证 Provider 签名。核销不会自动恢复或重放任务。","Review source is declared by the operator; provider signatures are not verified. Reconciliation does not resume or replay tasks.")}</p><form id="rcReviewForm" class="form-stack">${label("调用 ID","Call ID",input("rcReviewCall","text","required maxlength=200"))}${label("路由判定 ID","Route decision ID",input("rcReviewDecision","text","required maxlength=200"))}${label("Provider ID","Provider ID",input("rcReviewProvider","text","required maxlength=200"))}${label("审核来源声明","Review source statement",`<select id="rcReviewKind"><option value="operator_review">Operator review</option><option value="provider_record">Provider record statement</option></select>`)}<div class="form-pair">${label("输入词元","Input tokens",number("rcReviewInput",0,0,1000000000))}${label("输出词元","Output tokens",number("rcReviewOutput",0,0,1000000000))}</div><div class="form-pair">${label("实际费用（µ）","Actual cost (µ)",number("rcReviewCost",0,0,1000000000000))}${label("实际耗时（ms）","Actual duration (ms)",number("rcReviewRuntime",0,0,1000000000000))}</div>${button("rcReviewImport","保存审核材料","Save review material","submit")}</form><p id="rcReviewStatus" role="status"></p>${check("rcReviewConfirmed","确认上述材料与实际用量，执行核销","Confirm the material and actual usage to reconcile")}${button("rcReviewSettle","确认核销","Confirm reconciliation")}<div id="rcReconciliations" aria-live="polite"></div></details>
      <details><summary>${copy("回归评估记录","Regression Eval records")}</summary><p>${copy("状态仅适用于记录的场景。材料完整性由后端在读取时核对。","Status applies to the recorded scenario. The backend checks material integrity on read.")}</p><div id="rcEvals" aria-live="polite"></div></details>
      <details><summary>${copy("策略判定记录","Policy decision records")}</summary><p>${copy("判定记录不代表操作已执行。","A decision record does not establish execution.")}</p><div id="rcDecisions" aria-live="polite"></div></details>
      <details><summary>${copy("证据血缘","Evidence lineage")}</summary><form id="rcEvidenceForm" class="form-stack">${label("Run ID","Run ID",input("rcEvidenceRun","text","required maxlength=200 autocomplete=off"))}${button("rcEvidenceRead","读取血缘","Read lineage","submit")}</form><p>${copy("读取时核对材料哈希。确认发现需独立复验。","Material hashes are checked on read. Confirmed findings require independent verification.")}</p><div id="rcEvidence" aria-live="polite"></div><div class="form-pair">${button("rcEvidencePrevious","上一页","Previous page")}${button("rcEvidenceNext","下一页","Next page")}</div></details>
      <details><summary>${copy("事件响应历史","Incident response history")}</summary><form id="rcIncidentForm" class="form-stack">${label("事件 Candidate ID","Incident candidate ID",input("rcIncidentId","text","required maxlength=200 autocomplete=off"))}${button("rcIncidentRead","读取响应历史","Read response history","submit")}</form><p>${copy("状态来自操作员响应记录。证据完整性不代表修复已验证；Run 隔离仅约束后续 V6 授权操作。","States come from operator response records. Evidence integrity does not establish verified remediation; Run containment covers subsequent V6 authorized operations.")}</p><div id="rcIncident" aria-live="polite"></div></details>
      <details><summary>${copy("新增 V5 Provider","Add V5 provider")}</summary><form id="rcProviderForm" class="form-stack">
        <div class="form-pair">${label("名称","Name",input("rcProviderName","text","required maxlength=120 autocomplete=off"))}
          ${label("位置","Location",`<select id="rcLocation"><option value="local">Local</option><option value="cloud">Cloud</option></select>`)}</div>
        <div class="form-pair">${label("接口类型","API kind",`<select id="rcKind"><option value="openai_compatible">OpenAI compatible</option><option value="ollama">Ollama</option><option value="llama_cpp">llama.cpp</option></select>`)}
          ${label("模型 ID","Model ID",input("rcModel","text","required maxlength=200 autocomplete=off"))}</div>
        ${label("API Base URL","API Base URL",input("rcBase","url","required autocomplete=off placeholder=http://127.0.0.1:11434"))}
        ${label("API Key（可选）","API key (optional)",input("rcKey","password","minlength=8 maxlength=16384 autocomplete=new-password spellcheck=false"))}
        <div class="form-pair">${label("每百万词元费用（µ）","Cost per million tokens (µ)",number("rcRate",0,0,10000000000))}
          ${label("最大上下文词元","Maximum context tokens",number("rcContext",32768,1,10000000))}</div>
        ${label("输入词元计数","Input token counting",`<select id="rcInputCounting"><option value="utf8_estimate" data-runtime-zh="保守字节预估" data-runtime-en="Conservative byte estimate">${t("保守字节预估","Conservative byte estimate")}</option><option value="llama_cpp_server" data-runtime-zh="本机 llama.cpp 服务端计数" data-runtime-en="Local llama.cpp server count">${t("本机 llama.cpp 服务端计数","Local llama.cpp server count")}</option></select>`)}
        <p>${copy("输入先计入总额度，输出只使用剩余额度。字节预估不是精确计数；服务端计数需本机 llama.cpp 支持计数接口。","Input uses the total allowance first; generation uses the remainder. Byte estimates are not exact. Server counting requires the local llama.cpp counting endpoint.")}</p>
        ${check("rcIndependent","声明支持独立模型路线；独立验证仍需隔离执行与证据","Declare independent model routing support; verification still requires isolation and evidence")}
        ${check("rcProviderAccepted","确认将配置保存至本机后端","Confirm saving configuration to the local backend")}
        ${button("rcProviderSave","保存 Provider","Save provider","submit")}</form></details>
      <div id="rcProviderActions"></div>
      <details><summary>${copy("创建新的 Profile 版本","Create a new profile version")}</summary><form id="rcProfileForm" class="form-stack">
        <div class="form-pair">${label("Profile 名称","Profile name",input("rcProfileName","text","required maxlength=120 autocomplete=off"))}
          ${label("模式","Mode",`<select id="rcMode"><option value="local">Local</option><option value="cloud">Cloud</option><option value="hybrid">Hybrid</option><option value="offline">Offline</option></select>`)}</div>
        <p>${copy("Offline 可使用本机模型；所有敏感上下文强制本地。独立路线的本机限制同样适用。","Offline may use local models. Sensitive context always stays local, including independent routes.")}</p>
        <fieldset><legend>${copy("本机 Provider","Local providers")}</legend><div id="rcLocalProviders"></div></fieldset>
        <fieldset><legend>${copy("云端 Provider","Cloud providers")}</legend><div id="rcCloudProviders"></div></fieldset>
        <fieldset><legend>${copy("独立模型路线","Independent model route")}</legend><div id="rcIndependentProviders"></div></fieldset>
        <div class="form-pair">${label("单次词元上限","Tokens per call",number("rcMaxTokens",32768,1,2000000))}
          ${label("累计费用上限（µ；0 表示未设置）","Cumulative cost cap (µ; 0 means unset)",number("rcMaxCost",1000000,0,1000000000))}</div>
        <div class="form-pair">${label("模型调用并发上限（同一 Profile）","Concurrent model calls per profile",number("rcCallConcurrency",1,1,32))}
          ${label("每小时词元上限（同一 Profile）","Hourly tokens per profile",number("rcHourlyTokens",1000000,1,100000000))}</div>
        ${label("单次调用时限（毫秒，包含进程启动）","Call deadline in milliseconds, including process startup",number("rcCallRuntime",45000,1,45000))}
        ${label("云升级复杂度阈值","Cloud escalation complexity threshold",number("rcThreshold",0.65,0,1,0.01))}
        ${check("rcFallback","允许健康与预算约束下的回退","Allow fallback within health and budget limits",true)}
        ${check("rcProfileAccepted","确认创建新版本，现有队伍继续引用原版本","Confirm a new version; existing teams keep their original version")}
        ${button("rcProfileSave","创建 Profile 版本","Create profile version","submit")}</form></details>
      <details><summary>${copy("检查模型路线","Inspect model routing")}</summary><form id="rcRouteForm" class="form-stack">
        ${label("Profile","Profile",`<select id="rcRouteProfile" required></select>`)}
        <div class="form-pair">${label("任务类型","Task type",input("rcTaskType","text","required value=hypothesis_exploration maxlength=120"))}
          ${label("上下文敏感度","Context sensitivity",`<select id="rcSensitivity"><option value="internal">Internal</option><option value="public">Public</option><option value="private">Private</option><option value="secret">Secret</option></select>`)}</div>
        <div class="form-pair">${label("复杂度","Complexity",number("rcComplexity",0.5,0,1,0.01))}${label("估算词元","Estimated tokens",number("rcEstimate",4000,1,2000000))}</div>
        ${label("本次剩余费用预算（µ）","Remaining cost budget for this decision (µ)",number("rcRouteBudget",1000000,0,1000000000))}
        ${check("rcRouteIndependent","要求独立模型路线","Require an independent model route")}
        <p>${copy("检查只保存路由决策，不发送模型提示词，也不增加调用次数。","Inspection records a routing decision, sends no model prompt, and does not count as a call.")}</p>
        ${button("rcRouteCheck","检查并记录路由决策","Inspect and record route decision","submit")}<pre id="rcRouteResult"></pre></form></details>
      <h3>${copy("路由决策账本","Route decision ledger")}</h3><div id="rcRoutes"></div>
      <h3>${copy("模型调用与预算预留","Model calls and budget reservations")}</h3>
      <p>${copy("未知用量保留额度并阻止同一任务自动重发；关闭连接不代表模型服务已停止推理。这里只显示最近 50 条。","Unknown usage retains budget and blocks automatic task redispatch. Disconnecting does not prove server inference stopped. Showing the latest 50 calls.")}</p>
      <div id="rcCalls"></div>${button("rcMoreRoutes","加载更多路由","Load more routes")}
      <details><summary>${copy("持续研究策略","Continuous research policy")}</summary>
        <div class="form-pair">${label("授权项目","Authorized project",`<select id="rcEngagement"></select>`)}${label("Research Campaign","Research campaign",`<select id="rcCampaign"></select>`)}</div>
        <p id="rcPolicyState" role="status"></p><form id="rcPolicyForm" class="form-stack">
          ${check("rcEnabled","启用定期增量研究入队","Enable scheduled incremental research queueing")}
          ${check("rcIdle","仅在队列空闲时触发","Trigger only while the queue is idle",true)}
          <div class="form-pair">${label("最小间隔（分钟）","Minimum interval (minutes)",number("rcInterval",60,1,10080))}
            ${label("每日费用上限（µ；0 表示未设置）","Daily cost cap (µ; 0 means unset)",number("rcDaily",1000000,0,1000000000))}</div>
          ${label("每次最多新增任务","Maximum new tasks per tick",number("rcTickTasks",4,1,50))}
          <p>${copy("当前持续研究任务强制本地。云升级尚未接通；定期入队需要 App 后端在线，任务执行需要兼容 Worker。","Continuous research tasks currently stay local. Cloud escalation is not connected. Scheduled queueing requires the app backend online, and execution requires a compatible worker.")}</p>
          ${check("rcPolicyAccepted","确认当前授权与策略变更；旧配置任务将取消","Confirm current authority and policy change; old configuration tasks will be cancelled")}
          ${button("rcPolicySave","保存策略版本","Save policy version","submit")}
        </form>${button("rcTick","执行一次增量入队检查","Run one incremental queue check")}<pre id="rcTickResult"></pre><div id="rcHistory"></div></details>`;
    $("#runtime .page-heading").after(panel);
    const showMessage = (zh,en=zh) => bindings.text($("#rcMessage"),()=>t(zh,en));
    function localize() {
      panel.querySelectorAll("[data-runtime-zh]").forEach(node=>node.textContent=t(node.dataset.runtimeZh,node.dataset.runtimeEn));
      bindings.apply(); renderProviders(); renderRoutes(); renderCalls(); renderSummary();renderEvals();renderDecisions();renderEvidence();renderIncident();renderReconciliations();
    }
    async function send(url, body, method="POST") {
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
      try {
        const result=await fetch(url,{method,headers:{Accept:"application/json","Content-Type":"application/json"},body:JSON.stringify(body),signal:controller.signal});
        const data=await result.json();
        if(!result.ok) throw new Error(typeof data.detail==="string"?data.detail:`${result.status} ${result.statusText}`);
        return data;
      } catch(error) {
        if(error.name==="AbortError")throw window.FieldworkLocale.error("请求超时，请刷新核对保存状态后重试。","Request timed out. Refresh to check saved state before retrying.");
        throw error;
      } finally {clearTimeout(timer)}
    }
    async function mutate(action, control) {
      if(busy)return;
      busy=true; control.disabled=true; showMessage("正在处理…","Working…");
      try{await action()}catch(error){bindings.text($("#rcMessage"),()=>error.message)}
      finally{busy=false;control.disabled=false}
    }
    const option=(id,name)=>`<option value="${esc(id)}">${esc(name)}</option>`;
    function providerChecks(selector, values) {
      const prior=new Set([...$(selector).querySelectorAll("input:checked")].map(node=>node.value));
      $(selector).innerHTML=values.map(item=>`<label class="check-line"><input type="checkbox" value="${esc(item.id)}" ${prior.has(item.id)?"checked":""}>${esc(item.name)} · ${esc(item.model)} · ${esc(item.last_health)}</label>`).join("") || `<p>${t("尚无对应 Provider","No matching providers")}</p>`;
    }
    function renderProviders() {
      $("#rcProviderActions").innerHTML=providers.map(item=>`<div class="runtime-provider-action"><span><b>${esc(item.name)}</b> · ${esc(item.model)} · ${esc(item.location)} · ${esc(item.last_health)}<small class="cell-subline">${esc(item.base_url)} · ${esc(item.last_health_at||"–")}</small></span><button type="button" data-rc-health="${esc(item.id)}">${t("检查连接","Check connection")}</button><button type="button" data-rc-toggle="${esc(item.id)}">${item.enabled?t("禁用","Disable"):t("启用","Enable")}</button></div>`).join("");
      panel.querySelectorAll("[data-rc-health]").forEach(node=>node.onclick=()=>{
        const provider=providers.find(item=>item.id===node.dataset.rcHealth);
        if(!provider||!window.confirm(t(`连接 ${provider.base_url} 检查健康？不会调用模型。`,`Check health at ${provider.base_url}? No model prompt is sent.`)))return;
        mutate(async()=>{const value=await send("/api/v1/runtime/providers/test",{provider_id:provider.id});await refresh();showMessage(`${provider.name}: ${value.status}`)},node);
      });
      panel.querySelectorAll("[data-rc-toggle]").forEach(node=>node.onclick=()=>{
        const provider=providers.find(item=>item.id===node.dataset.rcToggle);
        if(!provider)return;
        mutate(async()=>{await send(`/api/v1/runtime/providers/${encodeURIComponent(provider.id)}`,{enabled:!provider.enabled},"PATCH");await refresh();showMessage("Provider 状态已更新。","Provider state updated.")},node);
      });
      providerChecks("#rcLocalProviders",providers.filter(item=>item.location==="local"));
      providerChecks("#rcCloudProviders",providers.filter(item=>item.location==="cloud"));
      providerChecks("#rcIndependentProviders",providers.filter(item=>item.metadata?.supports_independence));
      const selected=$("#rcRouteProfile").value;
      $("#rcRouteProfile").innerHTML=option("",t("选择 Profile","Select profile"))+profiles.map(item=>option(item.id,`${item.name} · ${item.mode} · ${item.id}${item.provider_configuration_bound===false?t(" · 旧版：未绑定 Provider 快照"," · Legacy: provider snapshot unbound"):""}`)).join("");
      if(profiles.some(item=>item.id===selected))$("#rcRouteProfile").value=selected;
    }
    function renderRoutes() {
      $("#rcRoutes").innerHTML=routes.length?`<div class="table-wrap"><table><thead><tr><th>Profile / Task</th><th>${t("路线 / 状态","Route / status")}</th><th>Provider / Model</th><th>${t("原因","Reason")}</th></tr></thead><tbody>${routes.map(item=>`<tr><td>${esc(item.profile_id||"–")}<small class="cell-subline">${esc(item.task_id||t("配置检查","Configuration inspection"))}</small></td><td>${esc(item.route)} / ${esc(item.status)}</td><td>${esc(item.provider_id||"–")}<small class="cell-subline">${esc(item.model||"–")}</small></td><td>${esc(list(item.reason).join(" · "))}<small class="cell-subline">${esc(item.created_at)}</small></td></tr>`).join("")}</tbody></table></div>`:`<p>${esc(failure||t("尚无路由决策。","No route decisions yet."))}</p>`;
      $("#rcMoreRoutes").hidden=!routePage.has_more;
    }
    async function loadRoutes(append=false) {
      const token=generation;
      const value=await request(`/api/v1/runtime/routes?limit=50&offset=${append?offset:0}`);
      if(token!==generation)return;
      routes=append?[...routes,...list(value)]:list(value);offset=routes.length;routePage=value.page||{};renderRoutes();
    }
    async function loadCalls(token=generation) {
      const value=await request("/api/v1/runtime/calls?limit=50");
      if(token!==generation)return;
      calls=list(value);renderCalls();
    }
    function renderIncident() {
      const host=$("#rcIncident");
      if(incidentError){host.textContent=incidentError;return}
      if(!incidentResponse){host.textContent=t("输入事件 Candidate ID 后读取历史。","Enter an incident candidate ID to read history.");return}
      const data=incidentResponse,states={DETECTED:t("已检测","Detected"),TRIAGED:t("已分诊","Triaged"),CONTAINED:t("已隔离","Contained"),INVESTIGATING:t("调查中","Investigating"),REMEDIATING:t("修复中","Remediating"),RECOVERED:t("已恢复","Recovered"),CLOSED:t("已关闭","Closed")};
      const integrity=value=>value==="intact"?t("完整","Intact"):value==="not_recorded"?t("未记录","Not recorded"):t("缺失或已变化","Missing or changed");
      host.innerHTML=`<p data-incident-state="${esc(data.state)}">${esc(states[data.state]||data.state)} · Run: ${esc(data.run_id)} · ${data.run_containment_active?t("当前隔离有效","Containment active"):t("当前未隔离","Containment inactive")} · ${integrity(data.response_evidence_integrity)}</p>${data.history.length?`<div class="table-wrap"><table><thead><tr><th>${t("响应状态","Response state")}</th><th>Artifact</th><th>${t("证据完整性","Evidence integrity")}</th><th>${t("记录时间","Recorded at")}</th></tr></thead><tbody>${data.history.map(row=>`<tr data-incident-integrity="${esc(row.evidence_integrity)}"><td>${esc(states[row.state]||row.state)}</td><td>${esc(row.artifact_id)}<small class="cell-subline">SHA256: ${esc(row.artifact_sha256)}</small></td><td>${integrity(row.evidence_integrity)}</td><td>${esc(row.created_at)}</td></tr>`).join("")}</tbody></table></div>`:`<p>${t("没有操作员响应记录。","No operator response records.")}</p>`}`;
    }
    $("#rcIncidentForm").onsubmit=async event=>{
      event.preventDefault();const token=++incidentGeneration,id=$("#rcIncidentId").value.trim();
      incidentResponse=null;incidentError=t("正在读取…","Reading…");renderIncident();$("#rcIncidentRead").disabled=true;
      try{const value=await request(`/api/v1/v6/incidents/${encodeURIComponent(id)}/response`);if(token!==incidentGeneration)return;incidentResponse=value;incidentError=""}
      catch(error){if(token!==incidentGeneration)return;incidentError=error.message}
      finally{if(token===incidentGeneration){$("#rcIncidentRead").disabled=false;renderIncident()}}
    };
    function renderEvidence() {
      const host=$("#rcEvidence");
      $("#rcEvidencePrevious").disabled=!evidencePage||evidencePage.offset===0;
      $("#rcEvidenceNext").disabled=!evidencePage||!evidencePage.has_more;
      if(evidenceError){host.textContent=evidenceError;return}
      if(!evidencePage){host.textContent=t("输入 Run ID 后读取材料。","Enter a Run ID to read materials.");return}
      if(!evidencePage.items.length){host.textContent=t("该 Run 没有已记录的 Artifact。","This Run has no recorded Artifacts.");return}
      host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>Artifact</th><th>${t("材料完整性","Material integrity")}</th><th>${t("事件 / Observation / Evidence","Events / observations / evidence")}</th></tr></thead><tbody>${evidencePage.items.map(item=>`<tr data-evidence-integrity="${esc(item.integrity)}"><td>${esc(item.kind)}<small class="cell-subline">${esc(item.artifact_id)}</small><small class="cell-subline">SHA256: ${esc(item.sha256||t("未记录","Not recorded"))}</small></td><td>${item.integrity==="intact"?t("完整","Intact"):t("缺失或已变化","Missing or changed")}</td><td>${item.runtime_event_ids.length} / ${item.observations.length} / ${item.evidence.length}<details><summary>${t("关联记录","Linked records")}</summary><pre>${esc(JSON.stringify({events:item.runtime_event_ids,observations:item.observations,evidence:item.evidence},null,2))}</pre></details></td></tr>`).join("")}</tbody></table></div><p>${t("当前显示","Showing")} ${evidencePage.offset+1}–${evidencePage.offset+evidencePage.items.length} · Run: ${esc(evidencePage.run_id)}</p>`;
    }
    async function readEvidence(runId,offset=0) {
      const token=++evidenceGeneration;
      evidencePage=null;evidenceError=t("正在读取…","Reading…");renderEvidence();$("#rcEvidenceRead").disabled=true;
      try{const value=await request(`/api/v1/v6/runs/${encodeURIComponent(runId)}/evidence-lineage?limit=50&offset=${offset}`);if(token!==evidenceGeneration)return;evidencePage=value;evidenceError=""}
      catch(error){if(token!==evidenceGeneration)return;evidenceError=error.message}
      finally{if(token===evidenceGeneration){$("#rcEvidenceRead").disabled=false;renderEvidence()}}
    }
    $("#rcEvidenceForm").onsubmit=event=>{event.preventDefault();readEvidence($("#rcEvidenceRun").value.trim())};
    $("#rcEvidencePrevious").onclick=()=>{if(evidencePage)readEvidence(evidencePage.run_id,Math.max(0,evidencePage.offset-50))};
    $("#rcEvidenceNext").onclick=()=>{if(evidencePage?.has_more)readEvidence(evidencePage.run_id,evidencePage.offset+50)};
    function renderDecisions() {
      const host=$("#rcDecisions");
      if(policyError){host.textContent=policyError;return}
      if(policyDecisions===null){host.textContent=t("等待读取判定。","Awaiting decisions.");return}
      if(!policyDecisions.length){host.textContent=t("没有已记录的判定。","No recorded decisions.");return}
      const labels={allow:t("允许","Allow"),allow_with_limit:t("受限允许","Allow with limits"),deny:t("拒绝","Deny"),require_approval:t("待批准","Approval required"),quarantine:t("隔离","Quarantine")};
      host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>${t("任务 / Run","Task / run")}</th><th>${t("动作 / 资源","Action / resource")}</th><th>${t("判定 / 理由","Decision / reasons")}</th></tr></thead><tbody>${policyDecisions.map(row=>`<tr data-policy-decision="${esc(row.decision)}"><td>${esc(row.task_id)}<small class="cell-subline">${esc(row.run_id)}</small></td><td>${esc(row.capability)} / ${esc(row.operation)}<small class="cell-subline">${esc(row.resource)}</small></td><td>${esc(labels[row.decision]||row.decision)}<small class="cell-subline">${(row.reasons||[]).map(esc).join(" · ")}</small></td></tr>`).join("")}</tbody></table></div>`;
    }
    function invalidateReview() {reviewGeneration++;importedReview=null;$("#rcReviewConfirmed").checked=false;$("#rcReviewSettle").disabled=true;$("#rcReviewStatus").textContent=""}
    $("#rcReviewSettle").disabled=true;
    $("#rcReviewForm").oninput=invalidateReview;
    $("#rcReviewForm").onsubmit=async event=>{
      event.preventDefault();invalidateReview();const token=reviewGeneration;
      $("#rcReviewImport").disabled=true;
      const material={call_id:$("#rcReviewCall").value.trim(),decision_id:$("#rcReviewDecision").value.trim(),provider_id:$("#rcReviewProvider").value.trim(),review_kind:$("#rcReviewKind").value,input_tokens:Number($("#rcReviewInput").value),output_tokens:Number($("#rcReviewOutput").value),cost_micros:Number($("#rcReviewCost").value),runtime_ms:Number($("#rcReviewRuntime").value)};
      try{const result=await send(`/api/v1/v6/model-calls/${encodeURIComponent(material.call_id)}/usage-review`,material);if(token!==reviewGeneration)return;importedReview=result;$("#rcReviewStatus").textContent=`Artifact: ${result.artifact_id} · SHA256: ${result.sha256}`;$("#rcReviewSettle").disabled=false}
      catch(error){if(token===reviewGeneration)$("#rcReviewStatus").textContent=error.message}
      finally{$("#rcReviewImport").disabled=false}
    };
    $("#rcReviewSettle").onclick=async()=>{
      if(!importedReview||!$("#rcReviewConfirmed").checked){$("#rcReviewStatus").textContent=t("请明确确认审核材料和实际用量。","Explicitly confirm the material and actual usage.");return}
      const review=importedReview,token=reviewGeneration;$("#rcReviewSettle").disabled=true;
      try{await send(`/api/v1/v6/model-calls/${encodeURIComponent(review.call_id)}/reconcile`,{artifact_id:review.artifact_id,confirmed:true});if(token!==reviewGeneration)return;invalidateReview();$("#rcReviewStatus").textContent=t("核销已记录，任务状态保持不变。","Reconciliation recorded; task state is unchanged.");await refresh()}
      catch(error){if(token===reviewGeneration){$("#rcReviewStatus").textContent=error.message;$("#rcReviewSettle").disabled=false}}
    };
    function renderReconciliations() {
      const host=$("#rcReconciliations");
      if(reconciliationError){host.textContent=reconciliationError;return}
      if(reconciliations===null){host.textContent=t("等待读取核销记录。","Awaiting reconciliation records.");return}
      if(!reconciliations.length){host.textContent=t("没有已记录的用量核销。","No recorded usage reconciliations.");return}
      host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>${t("调用 / Run","Call / run")}</th><th>${t("实际用量","Recorded usage")}</th><th>${t("审核 / 完整性","Review / integrity")}</th></tr></thead><tbody>${reconciliations.map(row=>`<tr data-reconciliation-integrity="${esc(row.integrity)}"><td>${esc(row.call_id)}<small class="cell-subline">${esc(row.run_id)}</small></td><td>${row.usage?`${esc(row.usage.input_tokens)} / ${esc(row.usage.output_tokens)} tokens · ${esc(row.usage.cost_micros)} µ`:t("用量记录缺失","Usage missing")}<small class="cell-subline">${esc(row.runtime_ms??"—")} ms</small></td><td>${row.review_kind==="provider_record"?t("Provider 记录声明","Provider record statement"):t("操作员审核","Operator review")} · ${row.integrity==="intact"?t("完整","Intact"):t("缺失或已变化","Missing or changed")}<small class="cell-subline">Artifact: ${esc(row.artifact_id)}</small><small class="cell-subline">${esc(row.created_at)}</small></td></tr>`).join("")}</tbody></table></div><p>${t("当前显示最近 50 条核销记录。","Showing the latest 50 reconciliation records.")}</p>`;
    }
    function renderEvals() {
      const host=$("#rcEvals");
      if(evalError){host.textContent=evalError;return}
      if(evalRuns===null){host.textContent=t("等待读取评估记录。","Awaiting Eval records.");return}
      if(!evalRuns.length){host.textContent=t("没有已记录的 Eval。","No recorded Evals.");return}
      const labels={passed:t("通过","Passed"),failed:t("失败","Failed"),invalid:t("材料失效","Materials invalid")};
      host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>${t("场景 / 版本","Scenario / version")}</th><th>${t("模型 / Profile","Model / profile")}</th><th>${t("状态","Status")}</th><th>${t("失败项 / 材料","Failures / materials")}</th></tr></thead><tbody>${evalRuns.map(row=>`<tr data-eval-id="${esc(row.id)}" data-eval-status="${esc(row.status)}"><td>${esc(row.scenario_id)} / ${esc(row.scenario_version)}<small class="cell-subline">${esc(row.id)}</small></td><td>${esc(row.subject?.model??t("未记录","Not recorded"))}<small class="cell-subline">${esc(row.subject?.profile??"—")}</small></td><td>${esc(labels[row.status]||row.status)}</td><td>${(row.result?.failures||[]).map(esc).join(" · ")||"—"}<small class="cell-subline">Artifact: ${esc(row.artifact_id)} · ${row.integrity?.artifact?t("完整","Intact"):t("失效","Invalid")}</small></td></tr>`).join("")}</tbody></table></div>`;
    }
    function renderSummary() {
      const host=$("#rcSummary");
      if(!summary){host.textContent=summaryError||t("等待状态快照。","Awaiting state snapshot.");return}
      const sections=[["groups",t("研究组","Groups")],["tasks",t("任务","Tasks")],["runners",t("执行节点","Runners")],["model_calls",t("模型调用","Model calls")],["http_receipts",t("HTTP 回执","HTTP receipts")]];
      const names={running:t("运行中","Running"),pending:t("待执行","Pending"),queued:t("排队中","Queued"),succeeded:t("已完成","Succeeded"),completed:t("已完成","Completed"),failed:t("失败","Failed"),paused:t("已暂停","Paused"),cancelled:t("已取消","Cancelled"),online:t("在线","Online"),offline:t("离线","Offline"),reserved:t("已预留","Reserved"),calling:t("调用中","Calling"),unknown:t("用量未知","Usage unknown"),settled:t("已结算","Settled"),released:t("已释放","Released")};
      host.innerHTML=`<div class="table-wrap"><table><thead><tr><th>${t("账本","Ledger")}</th><th>${t("总数","Total")}</th><th>${t("当前状态计数","Current state counts")}</th></tr></thead><tbody>${sections.map(([key,label])=>`<tr><td>${esc(label)}</td><td data-summary-total="${key}">${esc(summary[key]?.total??"—")}</td><td>${Object.entries(summary[key]?.states||{}).map(([state,count])=>`${esc(names[state]||state)}: ${esc(count)}`).join(" · ")||t("无记录","No records")}</td></tr>`).join("")}</tbody></table></div>
        <p>${t("未结读取","Unsettled reads")}: ${esc(summary.http_unsettled??"—")} · ${t("隔离 Run","Contained runs")}: ${esc(summary.contained_runs??"—")} · ${t("事件","Events")}: ${esc(summary.event_count??"—")}</p>
        <p>Grant ${t("签发 / 撤销 / 使用","issued / revoked / used")}: ${esc(summary.grants?.issued??"—")} / ${esc(summary.grants?.revoked??"—")} / ${esc(summary.grants?.uses??"—")}</p>
        <small>${t("快照时间","Snapshot time")}: ${esc(summary.snapshot_at||"—")}</small>`;
    }
    function renderCalls() {
      const states={reserved:t("已预留","Reserved"),calling:t("调用中","Calling"),unknown:t("用量未知","Usage unknown"),settled:t("已结算","Settled"),released:t("发送前释放","Released before dispatch")};
      $("#rcCalls").innerHTML=calls.length?`<div class="table-wrap"><table><thead><tr><th>Task / Profile</th><th>${t("状态","State")}</th><th>${t("预留词元 / 费用 µ","Reserved tokens / cost µ")}</th><th>${t("调用截止时间 / 用量记录","Deadline / usage record")}</th></tr></thead><tbody>${calls.map(item=>`<tr><td>${esc(item.task_id)}<small class="cell-subline">${esc(item.profile_id||"–")}</small></td><td>${esc(states[item.state]||item.state)}</td><td>${esc(item.reserved_tokens)} / ${esc(item.reserved_cost_micros)}<small class="cell-subline">${item.input_counting ? `${esc(item.input_counting === "llama_cpp_server" ? t("服务端报告计数","Server-reported count") : t("字节预估（非精确）","Byte estimate (not exact)"))}: ${esc(item.preflight_input_tokens)} · ${t("生成上限","Generation limit")} ${esc(item.output_max_tokens)}` : esc(t("无输入预检记录","No input preflight record"))}</small></td><td>${esc(item.deadline_at)}<small class="cell-subline">${esc(item.usage_id||"–")}</small></td></tr>`).join("")}</tbody></table></div>`:`<p>${esc(t("尚无模型调用预留。","No model call reservations yet."))}</p>`;
    }
    async function refresh() {
      const token=++generation;failure="";
      try {
        const [config,ledger,snapshot,evals,decisions,reviews]=await Promise.all([request("/api/v1/runtime/config"),request("/api/v1/runtime/routes?limit=50&offset=0"),request("/api/v1/v6/runtime-summary").then(data=>({data}),error=>({error:error.message})),request("/api/v1/v6/eval-runs?limit=50").then(data=>({data}),error=>({error:error.message})),request("/api/v1/v6/policy-decisions?limit=50").then(data=>({data}),error=>({error:error.message})),request("/api/v1/v6/model-usage-reconciliations?limit=50").then(data=>({data}),error=>({error:error.message}))]);
        if(token!==generation)return;
        summary=snapshot.data||null;summaryError=snapshot.error||"";renderSummary();
        reconciliations=reviews.data?.items||[];reconciliationError=reviews.error||"";renderReconciliations();
        evalRuns=evals.data?.runs||[];evalError=evals.error||"";renderEvals();
        policyDecisions=decisions.data?.decisions||[];policyError=decisions.error||"";renderDecisions();
        providers=list(config.providers);profiles=list(config.profiles);state.remote.runtimeConfig={ok:true,data:config};
        onConfig();
        routes=list(ledger);offset=routes.length;routePage=ledger.page||{};renderProviders();renderRoutes();await loadCalls(token);
        await loadCampaigns(token);
      } catch(error){if(token===generation){failure=error.message;showMessage(error.message);renderRoutes();$("#rcCalls").textContent=error.message}}
    }
    async function loadCampaigns(token=++generation) {
      const projects=list(state.engagements);if(!projects.some(item=>item.id===engagementId))engagementId=projects[0]?.id||"";
      $("#rcEngagement").innerHTML=option("",t("选择项目","Select project"))+projects.map(item=>option(item.id,item.name||item.id)).join("");$("#rcEngagement").value=engagementId;
      const result=engagementId?list(await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`)):[];
      if(token!==generation)return;
      campaigns=result;
      if(!campaigns.some(item=>item.id===campaignId))campaignId=campaigns[0]?.id||"";
      $("#rcCampaign").innerHTML=option("",t("选择 Campaign","Select campaign"))+campaigns.map(item=>option(item.id,item.name||item.id)).join("");$("#rcCampaign").value=campaignId;
      await loadPolicy(token);
    }
    async function loadPolicy(token=++generation) {
      policy=null;$("#rcPolicySave").disabled=$("#rcTick").disabled=true;
      if(!campaignId){$("#rcTickResult").textContent="";bindings.forget($("#rcHistory"));$("#rcHistory").textContent="";bindings.text($("#rcPolicyState"),()=>t("选择已授权项目与 Campaign 后配置。","Select an authorized project and campaign to configure."));return}
      try{
        const [value,history]=await Promise.all([request(`/api/v1/continuous-research/campaigns/${encodeURIComponent(campaignId)}`),request(`/api/v1/continuous-research/campaigns/${encodeURIComponent(campaignId)}/history`)]);
        if(token!==generation)return;
        policy=value.policy;$("#rcEnabled").checked=!!policy.enabled;$("#rcIdle").checked=!!policy.idle_only;
        $("#rcInterval").value=policy.min_interval_minutes;$("#rcDaily").value=policy.daily_budget_micros;$("#rcTickTasks").value=policy.max_tasks_per_tick;$("#rcPolicyAccepted").checked=false;
        $("#rcPolicySave").disabled=false;$("#rcTick").disabled=!policy.enabled;
        bindings.text($("#rcPolicyState"),()=>`${t("配置版本","Configuration revision")}: ${value.configuration_revision} · ${value.configuration_sha256||t("未配置","Not configured")} · ${t("下次检查","Next tick")}: ${value.next_tick_at||"–"}`);
        bindings.html($("#rcHistory"),()=>list(history).map(item=>`<p>${t("版本","Revision")} ${esc(item.revision)} · ${esc(item.created_at)}<small class="cell-subline">Scope ${esc(item.scope_snapshot_id)} · Policy ${esc(item.policy_id)}</small><small class="cell-subline mono">${esc(item.sha256)}</small></p>`).join(""));
      }catch(error){if(token===generation)bindings.text($("#rcPolicyState"),()=>error.message)}
    }
    $("#rcProviderForm").onsubmit=event=>{
      event.preventDefault();if(!$("#rcProviderAccepted").checked)return;
      const body={name:$("#rcProviderName").value.trim(),location:$("#rcLocation").value,kind:$("#rcKind").value,base_url:$("#rcBase").value.trim(),model:$("#rcModel").value.trim(),api_key:$("#rcKey").value||null,
        metadata:{supports_independence:$("#rcIndependent").checked,cost_micros_per_million_tokens:Number($("#rcRate").value),max_context_tokens:Number($("#rcContext").value),input_token_counting:$("#rcInputCounting").value}};
      mutate(async()=>{try{await send("/api/v1/runtime/providers",body);event.target.reset();await refresh();showMessage("Provider 已保存；请显式检查连接。","Provider saved. Explicitly check its connection.")}finally{$("#rcKey").value="";body.api_key=null}},$("#rcProviderSave"));
    };
    $("#rcProfileForm").onsubmit=event=>{
      event.preventDefault();if(!$("#rcProfileAccepted").checked)return;
      const ids=selector=>[...$(selector).querySelectorAll("input:checked")].map(node=>node.value);
      const body={name:$("#rcProfileName").value.trim(),mode:$("#rcMode").value,config:{local_provider_ids:ids("#rcLocalProviders"),cloud_provider_ids:ids("#rcCloudProviders"),independent_provider_ids:ids("#rcIndependentProviders"),allow_fallback:$("#rcFallback").checked,cloud_complexity_threshold:Number($("#rcThreshold").value),max_tokens_per_call:Number($("#rcMaxTokens").value),max_cost_micros:Number($("#rcMaxCost").value),max_concurrent_calls:Number($("#rcCallConcurrency").value),max_tokens_per_hour:Number($("#rcHourlyTokens").value),max_runtime_ms_per_call:Number($("#rcCallRuntime").value)}};
      const selectedIds=new Set([...body.config.local_provider_ids,...body.config.cloud_provider_ids,...body.config.independent_provider_ids]);
      body.config.provider_config_hashes=Object.fromEntries(providers.filter(item=>selectedIds.has(item.id)).map(item=>[item.id,item.configuration_sha256]));
      mutate(async()=>{const value=await send("/api/v1/runtime/config",body,"PUT");await refresh();$("#rcRouteProfile").value=value.id;$("#rcProfileAccepted").checked=false;showMessage(`Profile ${value.id} ${t("已保存。","saved.")}`)},$("#rcProfileSave"));
    };
    $("#rcRouteForm").onsubmit=event=>{
      event.preventDefault();mutate(async()=>{const value=await send("/api/v1/runtime/routes",{profile_id:$("#rcRouteProfile").value,task_type:$("#rcTaskType").value,sensitivity:$("#rcSensitivity").value,complexity:Number($("#rcComplexity").value),estimated_tokens:Number($("#rcEstimate").value),budget_remaining_micros:Number($("#rcRouteBudget").value),requires_independence:$("#rcRouteIndependent").checked});$("#rcRouteResult").textContent=JSON.stringify(value,null,2);await loadRoutes();showMessage("路由决策已记录；模型调用次数未增加。","Route decision recorded; model call count did not increase.")},$("#rcRouteCheck"));
    };
    $("#rcPolicyForm").onsubmit=event=>{
      event.preventDefault();if(!campaignId||!policy||!$("#rcPolicyAccepted").checked)return;
      const body={...policy,enabled:$("#rcEnabled").checked,idle_only:$("#rcIdle").checked,min_interval_minutes:Number($("#rcInterval").value),daily_budget_micros:Number($("#rcDaily").value),max_tasks_per_tick:Number($("#rcTickTasks").value),local_first:true,sensitive_local_only:true,cloud_escalation:false,max_cloud_tasks_per_tick:0};
      mutate(async()=>{await send(`/api/v1/continuous-research/campaigns/${encodeURIComponent(campaignId)}`,body,"PUT");await loadPolicy();showMessage("策略版本已保存。","Policy version saved.")},$("#rcPolicySave"));
    };
    $("#rcTick").onclick=()=>{if(!campaignId||!window.confirm(t("检查当前图谱变化并按预算新增持久任务？","Check graph changes and queue tasks within the budget?")))return;mutate(async()=>{const value=await send(`/api/v1/continuous-research/campaigns/${encodeURIComponent(campaignId)}/tick`,{});$("#rcTickResult").textContent=JSON.stringify(value,null,2);await loadPolicy()},$("#rcTick"))};
    $("#rcEngagement").onchange=event=>{engagementId=event.target.value;campaignId="";loadCampaigns().catch(error=>showMessage(error.message))};
    $("#rcCampaign").onchange=event=>{campaignId=event.target.value;loadPolicy()};
    $("#rcRefresh").onclick=refresh;$("#rcMoreRoutes").onclick=()=>loadRoutes(true).catch(error=>showMessage(error.message));
    for(const [formId,acceptedId] of [["rcProviderForm","rcProviderAccepted"],["rcProfileForm","rcProfileAccepted"],["rcPolicyForm","rcPolicyAccepted"]]){
      $(`#${acceptedId}`).required=true;
      $(`#${formId}`).addEventListener("input",event=>{if(event.target.id!==acceptedId)$(`#${acceptedId}`).checked=false});
    }
    document.addEventListener("fieldwork:languagechange",localize);
    localize();
    return {refresh,reset(){generation++;invalidateReview();incidentGeneration++;incidentResponse=null;incidentError="";$("#rcIncidentId").value="";$("#rcIncidentRead").disabled=false;renderIncident();evidenceGeneration++;evidencePage=null;evidenceError="";$("#rcEvidenceRun").value="";$("#rcEvidenceRead").disabled=false;renderEvidence();engagementId=campaignId="";policy=null;$("#rcPolicySave").disabled=$("#rcTick").disabled=true}};
  };
})();
