/* Campaign overview: all counts retain their API source and project scope. */
window.createFieldworkHome = function(api) {
  const {state,esc,list,row,empty,optional,setView,openInspector,runAction,toast}=api;
  const $=s=>document.querySelector(s);
  const t=(zh,en)=>document.documentElement.lang.startsWith("en")?en:zh;
  const statusLabel=value=>({running:t("运行中","Running"),queued:t("排队中","Queued"),paused:t("已暂停","Paused"),completed:t("已完成","Completed"),stopped:t("已停止","Stopped"),failed:t("失败","Failed"),active:t("已启用","Active"),confirmed:t("已确认","Confirmed"),draft:t("草稿","Draft"),unknown:t("未知","Unknown")}[value]||value);
  const researchStages=[['target','目标解析','Target'],['scope','授权范围','Scope'],['surface','攻击面','Surface'],['analysis','自动分析','Analysis'],['processing','数据处理','Processing'],['verification','漏洞验证','Verification'],['impact','影响判断','Impact'],['report','报告','Report']];
  function renderStages(details){
    const stages=list(details?.stages),byId=new Map(stages.map(stage=>[stage.id,stage]));
    const labels={partial:t('部分完成','Partial'),not_tested:t('未测试','Not tested'),not_applicable:t('不适用','Not applicable'),human_review:t('待复验 / 补充','Review required')};
    $('#homeStages').innerHTML=researchStages.map(([id,zh,en],index)=>{
      const stage=byId.get(id),value=stage?.status;
      return `<div class="${value==='completed'?'done':value==='running'?'current':''}" title="${esc(stage?.summary||'')}">${index+1}. ${t(zh,en)}<small>${esc(value?(labels[value]||statusLabel(value)):t('等待读取','Awaiting details'))}</small></div>`;
    }).join('');
    if(stages.length===8)$('#homeMetric0').textContent=`${stages.filter(stage=>stage.status==='completed').length} / 8`;
    else $('#homeMetric0').textContent='–';
  }
  const domainLabel=value=>({traditional:t("传统安全","Traditional security"),web3:"Web3",agent_audit:"AI Agent Security"}[value]||value);
  let selected="",generation=0,graphGeneration=0,snapshot=null,graph=null,scale=1;
  let eventStream=null,eventRun="",eventRecords=new Map(),endedEventRun="";
  function stopEvents(){if(eventStream)eventStream.close();eventStream=null;eventRun=""}
  function renderLiveEvents(message){
    const target=$("#homeEvents");
    const events=[...eventRecords.values()].sort((a,b)=>Number(b.id)-Number(a.id));
    target.innerHTML=`<p class="home-note" role="status">${esc(message)}</p>`+(events.length?`<div class="home-list">${events.slice(0,3).map(x=>row(x.message||x.kind,`${x.created_at||""} · ${x.stage||""}`,x.kind,x)).join("")}</div>`:empty(t("尚无事件","No events yet"),t("等待当前运行的真实执行事件。","Waiting for actual events from the current run.")));
    if(snapshot)snapshot.events=events;
  }
  function connectEvents(){
    const run=snapshot?.run;
    if(document.hidden||!$("#campaign").classList.contains("active")||!run||run.id===endedEventRun||!["running","queued","paused"].includes(run.status)){stopEvents();return}
    if(eventStream&&eventRun===run.id)return;
    stopEvents();eventRun=run.id;
    const source=new EventSource(`/api/v1/runs/${encodeURIComponent(run.id)}/events`);eventStream=source;
    source.onopen=()=>{if(source===eventStream)renderLiveEvents(t("实时连接已建立 · 当前运行","Live connection established · Current run"))};
    source.onmessage=event=>{
      if(source!==eventStream)return;
      try{const record=JSON.parse(event.data);if(record.run_id!==eventRun||record.id==null)return;eventRecords.set(String(record.id),record);
        if(eventRecords.size>500){const oldest=[...eventRecords.keys()].sort((a,b)=>Number(a)-Number(b))[0];eventRecords.delete(oldest)}
        renderLiveEvents(t("实时更新 · ","Live update · ")+new Date().toLocaleTimeString(document.documentElement.lang));
        if(["run.completed","run.stopped","run.failed"].includes(record.kind)){endedEventRun=eventRun;stopEvents();renderLiveEvents(t("运行结束，正在更新结果…","Run ended. Updating results…"));api.refresh()}
      }catch{renderLiveEvents(t("收到无法解析的事件 · 保留最近有效记录","Unreadable event received · Keeping the latest valid records"))}
    };
    source.onerror=()=>{if(source===eventStream)renderLiveEvents(t("事件连接中断，正在重连 · 下方为最近有效记录","Event connection lost; reconnecting · Latest valid records shown below"))};
  }
  const host=document.createElement("div");host.id="homeOverview";
  const panel=(cls,title,kicker,id,view)=>`<article class="panel ${cls}"><header><div><small>${kicker}</small><h2>${title}</h2></div>${view?`<button type="button" data-home-view="${view}">${t("查看全部","View all")}</button>`:""}</header><div id="${id}"></div></article>`;
  function renderShell(){
    host.innerHTML=`<header class="page-heading home-header"><div><p class="kicker">${t("研究工作区","RESEARCH WORKSPACE")}</p><h1 id="homeTitle">${t("研究项目","Research project")}</h1><p id="homeSubtitle">${t("以证据为核心的自主分析与独立复验","Evidence-led analysis and independent verification")}</p><select id="homeProject" aria-label="${t("当前研究项目","Current research project")}"></select></div><div class="home-header-actions"><button id="homePause">${t("暂停","Pause")}</button><button id="homeCopy">${t("复制","Copy")}</button><button id="homeStop">${t("停止","Stop")}</button><button id="homeHistory">${t("历史项目","Project history")}</button><button id="homeRefresh">${t("刷新","Refresh")}</button></div></header><nav class="home-tabs" aria-label="${t("研究工作区","Research workspace")}"><button class="active" aria-current="page">${t("概览","Overview")}</button>${[["graph",t("研究图谱","Research graph")],["agents",t("智能体","Agents")],["tasks",t("执行","Execution")],["evidence",t("证据","Evidence")],["verification",t("验证","Verification")],["findings",t("结果","Findings")]].map(([id,label])=>`<button data-home-view="${id}">${label}</button>`).join("")}</nav><div class="home-metrics" id="homeMetrics"></div><div class="home-grid"><article class="panel home-graph"><header><div><small>${t("知识层","KNOWLEDGE LAYER")}</small><h2>${t("研究图谱","Research graph")}</h2></div><div class="home-controls"><select id="homeGraphMode" aria-label="${t("图谱类型","Graph type")}"><option value="assets">${t("已观察资产","Observed assets")}</option><option value="research">${t("研究关系","Research relationships")}</option><option value="evidence">${t("证据引用","Evidence references")}</option><option disabled>${t("攻击路径 · 路径分析接口未接入","Attack paths · Analysis API not connected")}</option></select><button id="homeFit">${t("适配视图","Fit view")}</button><button id="homeZoom" aria-label="${t("放大图谱","Zoom in")}">＋</button></div></header><div class="home-canvas" id="homeGraph"></div><p class="home-note" id="homeGraphNote"></p></article><article class="panel home-focus"><header><div><small>${t("当前焦点","CURRENT FOCUS")}</small><h2>${t("下一步","Next step")}</h2></div><button id="homeScope">${t("核对授权范围","Review scope")}</button></header><div id="homeFocus"></div><div id="homeOutcome" aria-live="polite"></div></article>${panel("home-third",t("智能体执行","Agent execution"),t("执行层","EXECUTION"),"homeAgents","agents")}${panel("home-third",t("证据与独立验证","Evidence & verification"),t("可信层","TRUST LAYER"),"homeEvidence","verification")}${panel("home-third",t("确认发现","Verified findings"),t("确认结果","CANONICAL RESULTS"),"homeFindings","findings")}${panel("home-half",t("本机安全监控","Local security monitoring"),t("持续监控","CONTINUOUS MONITOR"),"homeSentinel","sentinel")}${panel("home-half",t("执行器健康","Runner health"),t("执行基础设施","EXECUTION INFRASTRUCTURE"),"homeRunners","runners")}<article class="panel home-wide"><div class="home-stages" id="homeStages"></div></article>${panel("home-half",t("最新动态","Latest activity"),t("实时事件","LIVE EVENTS"),"homeEvents","events")}<article class="panel home-half home-assistant"><header><div><small>${t("研究助手","FIELDWORK ASSISTANT")}</small><h2>${t("询问当前研究","Ask about this research")}</h2></div><span class="home-note">${t("本地事实查询","Local fact lookup")}</span></header><form id="homeAsk"><label for="homeQuestion" class="home-note">${t("查询进度、证据、发现、阻塞或下一步；不发起扫描","Look up progress, evidence, findings, blockers or next steps; no scan is started")}</label><textarea id="homeQuestion" required maxlength="2000" placeholder="${t("例如：当前项目为什么还没有已验证发现？","For example: Why are there no verified findings yet?")}"></textarea><footer><span class="home-note">${t("仅检索当前 API 快照；非模型推理","Current API snapshot only; not model reasoning")}</span><button class="primary-button">${t("查询","Look up")}</button></footer></form><div id="homeAnswer" class="home-answer" role="status"></div></article></div>`;
  }
  renderShell();
  host.insertBefore(host.querySelector(".home-focus"),host.querySelector(".home-metrics"));
  const campaign=$("#campaign"),legacy=document.createElement("details");legacy.className="home-archive";
  const summary=document.createElement("summary");summary.textContent=t("全部项目与待办总览","All projects and open items");legacy.append(summary);
  while(campaign.firstChild)legacy.append(campaign.firstChild);
  campaign.append(host,legacy);$("#homeFocus").append($("#focusHero"));
  host.addEventListener("click",e=>{const button=e.target.closest("[data-home-view]");if(button){state.activeRun=snapshot?.run?.id||"";if(button.dataset.homeView==="graph")$("#graphCampaign").value=selected;setView(button.dataset.homeView)}});
  function bindControls(){
  $("#homeProject").onchange=e=>{selected=e.target.value;api.onProjectChange?.(selected);state.activeEngagement=selected;update()};
  $("#homeHistory").onclick=()=>{legacy.hidden=!legacy.hidden;legacy.open=false};
  $("#homeRefresh").onclick=api.refresh;
  $("#homePause").onclick=()=>snapshot?.run&&runAction(snapshot.run.status==="paused"?"resume":"pause",snapshot.run.id);
  $("#homeStop").onclick=()=>snapshot?.run&&runAction("stop",snapshot.run.id);
  $("#homeScope").onclick=()=>selected&&api.openScope(selected);
  $("#homeCopy").onclick=async()=>{if(!snapshot?.project)return;try{await navigator.clipboard.writeText([snapshot.project.name,snapshot.project.normalized_target||snapshot.project.target,`Project: ${selected}`,`Domain: ${state.domain}`].filter(Boolean).join("\n"));toast(t("已复制研究信息；未复制授权或启动任务","Research information copied; no authorization copied or task started"))}catch{toast(t("无法访问剪贴板，请从研究详情复制","Clipboard unavailable; copy from the research details"))}};
  $("#homeFit").onclick=()=>{scale=Math.min(1,Math.max(.25,($("#homeGraph").clientWidth-20)/800));drawGraph()};
  $("#homeZoom").onclick=()=>{scale=Math.min(1.5,scale+.15);drawGraph()};
  $("#homeGraphMode").onchange=()=>updateGraph();
  $("#homeAsk").onsubmit=answerQuestion;
  }
  bindControls();
  async function updateGraph(more=false){
    more=more===true;
    const token=++graphGeneration,mode=$("#homeGraphMode").value,id=selected;
    if(more){const button=$("#homeGraphMore");if(button){button.disabled=true;button.textContent=t("正在加载…","Loading…")}}
    else{$("#homeGraph").innerHTML=empty(t("正在读取关系","Loading relationships"),t("从当前项目的持久化记录生成图谱。","Building the graph from this project's saved records."));$("#homeGraphNote").textContent=""}
    try{
      let next;
      if(mode==="assets")next=await api.request(`/api/v1/engagements/${encodeURIComponent(id)}/asset-graph`);
      else{
        const data=more?await graph.loadMore():await window.loadFieldworkResearchGraph({engagementId:id,mode:state.domain,findings:state.remote.findings?.ok?state.findings:null,request:api.request});
        next=mode==="evidence"?{
          ...data,nodes:data.nodes.filter(n=>["target","candidate","evidence_reference","counterevidence_reference","canonical_result"].includes(n.entity_type)),
          edges:[...data.edges.filter(e=>["engagement_candidate","cites_evidence","verified_promotion","verified_result"].includes(e.relation_type)),...data.nodes.filter(n=>n.entity_type==="candidate"&&data.edges.some(e=>e.target_id===n.id&&e.relation_type==="linked_candidate")).map(n=>({source_id:data.root_id,target_id:n.id,relation_type:"engagement_candidate",source:"candidate.engagement_id"}))],
        }:data;
      }
      if(token!==graphGeneration||id!==selected)return;
      graph=next;
      drawGraph();
    }catch(error){if(token===graphGeneration&&id===selected){if(more)drawGraph();else{graph=null;$("#homeGraph").innerHTML=empty(t("研究关系读取失败","Could not load research relationships"),error.message,"unavailable")}$("#homeGraphNote").prepend(document.createTextNode(t(`读取失败：${error.message}；请重试。 `,`Loading failed: ${error.message}. Please retry. `)))}}
  }
  const showList=(id,items,title,detail,status)=>{$(id).innerHTML=items.length?`<div class="home-list">${items.slice(0,3).map(x=>row(title(x),detail(x),status(x),x)).join("")}</div>`:empty(t("暂无记录","No records yet"),t("当前数据源未返回可展示条目。","The current data source returned no items to display."))};
  function drawGraph(){
    const canvas=$("#homeGraph");if(!graph){canvas.innerHTML=empty(t("图谱不可用","Graph unavailable"),t("选择项目后读取真实资产关系。","Select a project to load its recorded asset relationships."));return}
    const nodes=list(graph.nodes),edges=list(graph.edges);if(!nodes.length){canvas.innerHTML=empty(t("尚无节点","No nodes yet"),t("当前项目没有保存资产关系。","This project has no saved asset relationships."));return}
    const depth=new Map([[graph.root_id||nodes[0].id,0]]);for(let i=0;i<3;i++)for(const e of edges)if(depth.has(e.source_id)&&!depth.has(e.target_id))depth.set(e.target_id,Math.min(3,depth.get(e.source_id)+1));
    const cols=[0,0,0,0],positions=new Map();nodes.forEach(n=>{const col=depth.get(n.id)??1;positions.set(n.id,{x:20+col*200,y:30+cols[col]++*90})});
    const height=Math.max(300,...cols.map(n=>n*90+50));
    canvas.innerHTML=`<div class="asset-graph-stage" style="width:800px;height:${height}px;zoom:${scale}"><svg width="800" height="${height}" aria-hidden="true">${edges.map(e=>{const a=positions.get(e.source_id),b=positions.get(e.target_id);return a&&b?`<path d="M${a.x+170} ${a.y+32} L${b.x} ${b.y+32}"/>`:""}).join("")}</svg>${nodes.map(n=>{const p=positions.get(n.id);return `<button class="asset-node" data-node-type="${esc(n.entity_type)}" data-home-node="${esc(n.id)}" style="left:${p.x}px;top:${p.y}px"><strong>${esc(n.label||n.id)}</strong><small>${esc(n.entity_type||"asset")}</small></button>`}).join("")}</div>`;
    canvas.querySelectorAll("[data-home-node]").forEach(b=>b.onclick=()=>{canvas.querySelectorAll(".selected").forEach(n=>n.classList.remove("selected"));b.classList.add("selected");openInspector(nodes.find(n=>n.id===b.dataset.homeNode))});
    const graphCopy=graph.localize?.()||graph;
    $("#homeGraphNote").textContent=t(`${nodes.length} 节点 · ${edges.length} 关系；仅为观察事实，不代表漏洞成立。 `,`${nodes.length} nodes · ${edges.length} relationships; observations do not establish a vulnerability. `)+(graphCopy.notes||[]).join(" ");
    if(graph.page?.has_more){const button=document.createElement("button");button.id="homeGraphMore";button.textContent=t("继续加载图谱","Load more graph records");button.onclick=()=>updateGraph(true);$("#homeGraphNote").append(button)}
  }
  function renderOutcome(){
    const host=$("#homeOutcome"),{run,details,runFindings,runSummary}=snapshot||{};
    const verified=list(runFindings?.verified),candidates=list(runFindings?.candidates);
    if(!run){host.replaceChildren();return}
    const ended=["completed","stopped","failed","timeout","budget_exhausted"].includes(run.status);
    const count=runFindings?verified.length:t("待读取","Loading");
    const pending=runFindings?candidates.filter(x=>!["verified","rejected"].includes(x.status)):null;
    const tests=list(details?.test_items),complete=tests.filter(x=>x.status==="completed"),incomplete=tests.filter(x=>x.status!=="completed");
    host.innerHTML=`<hr><h3>${ended?t("本次实际结果","Results from this run"):t("研究进行中，结果自动更新","Research in progress; results update automatically")}</h3><p>${t("确认发现","Verified findings")} ${esc(count)} · ${t("待验证线索","Unverified leads")} ${pending?pending.length:t("待读取","Loading")} · ${t("证据产物","Evidence artifacts")} ${details?list(details.artifacts).length:t("待读取","Loading")}</p>${verified.slice(0,3).map(x=>`<p><b>${esc(x.title||x.id)}</b> · ${esc(x.severity||"")}</p>`).join("")}${pending?.slice(0,3).map(x=>`<p>${t("待验证：","Unverified: ")}${esc(x.title||x.id)}</p>`).join("")||""}${details?`<p>${t("执行项","Execution items")} ${complete.length} / ${tests.length} ${t("已完成","completed")}${incomplete.length?` · ${incomplete.length} ${t("项未完成、失败或未测试","incomplete, failed or untested")}`:""}</p><details><summary>${t("查看实际覆盖与观察","View recorded coverage and observations")}</summary>${tests.map(x=>`<p><b>${esc(x.label||x.id)}</b> · ${esc(statusLabel(x.status))} · ${esc(x.result||"")}</p>`).join("")}${list(details.observations).slice(0,5).map(x=>`<p>${esc(x.summary||x.subject||x.id)}</p>`).join("")}</details>`:`<p>${t("运行材料待读取；无法据此判断结果为空。","Run material is not available yet; this does not mean there are no results.")}</p>`}${runSummary?`<details><summary>${t("完整运行总结","Full run summary")}</summary><pre class="workspace-json">${esc(runSummary.content||"")}</pre></details>`:""}${ended&&runFindings&&!verified.length?`<p class="home-note">${t("尚无已确认漏洞；候选线索和未测试项均保留，不能据此认定目标安全。","No confirmed vulnerability yet. Leads and untested items remain visible; this does not establish that the target is safe.")}</p>`:""}`;
  }
  function renderNextStep(){
    const {project,run,verified=[],candidates=[]}=snapshot;
    let label,detail,action;
    if(!state.remote.engagements?.ok){label=t("重新读取项目","Reload projects");detail=t("项目状态读取失败，请重试。","Project status could not be read. Retry to continue.");action=api.refresh}
    else if(!project){label=t("新建研究","New research");detail=t("填写目标后，按顺序核对授权和执行计划。","Enter a target, then review scope and the execution plan.");action=()=>$("#newResearch").click()}
    else if(project.status!=="ready"){label=t("继续核对授权","Continue scope review");detail=t("草稿已保存，确认范围后自动进入执行预检。","Draft saved. Confirm scope to continue to preflight.");action=()=>api.openScope(project.id)}
    else if(!state.remote.tasks?.ok){label=t("重新读取运行状态","Reload run status");detail=t("运行状态暂不可用，刷新后继续。","Run status is unavailable. Refresh to continue.");action=api.refresh}
    else if(run&&["queued","running","paused","failed","blocked","interrupted"].includes(run.status)){label=["failed","blocked","interrupted"].includes(run.status)?t("查看阻塞原因","Review blockers"):run.status==="paused"?t("继续处理暂停任务","Review paused run"):t("查看当前运行","View current run");detail=run.next_action||t("直接打开当前任务，查看进度和可用操作。","Open this run to see progress and available actions.");action=()=>api.openRun(run)}
    else if(!state.remote.findings?.ok&&run){label=t("重新读取研究结果","Reload results");detail=t("结果状态暂不可用，刷新后继续。","Results are unavailable. Refresh to continue.");action=api.refresh}
    else if(candidates.some(x=>!["verified","rejected"].includes(x.status))){const candidate=candidates.find(x=>!["verified","rejected"].includes(x.status));label=t("一键继续深挖","Continue research");detail=candidate.title||t("自动整理材料、执行适用复验并汇总本次结果。","Prepare materials, run supported verification and summarize results.");action=()=>api.continueResearch(candidate.run_id||run?.id,candidate.id)}
    else if(verified.length){label=t("准备报告","Prepare report");detail=t("已选好当前项目的确认发现，可直接生成预览。","The project's verified finding will be selected for report preview.");action=()=>api.openReport(verified[0].id)}
    else if(run){label=t("查看本次证据","Review run evidence");const gaps=list(snapshot.details?.test_items).filter(x=>x.status!=="completed");detail=gaps.length?t(`本次运行已结束，但 ${gaps.length} 项执行未完成。查看原因后继续处理。`,`The run ended, but ${gaps.length} execution items are incomplete. Review the reasons before continuing.`):t("本次运行暂无待复验或已确认发现，先查看覆盖与证据。","No pending or verified findings. Review coverage and evidence.");action=()=>{state.activeRun=run.id;state.runData=null;setView("evidence")}}
    else{label=t("检查并开始研究","Review and start research");detail=t("授权已确认，系统将自动检查执行计划。","Scope confirmed. The execution plan will be checked automatically.");action=()=>api.openExecution(project.id)}
    snapshot.nextStep={label,detail};
    const legacyFocus=$("#focusHero");if(legacyFocus){legacyFocus.hidden=true;legacy.append(legacyFocus)}
    $("#homeFocus").innerHTML=`<h3>${esc(project?.normalized_target||project?.target||project?.name||t("开始一项研究","Start a research project"))}</h3><p>${esc(detail)}</p><button class="primary-button" id="homePlan">${esc(label)}</button>`;
    $("#homePlan").onclick=action;
  }
  async function update(cached=false){
    cached=cached===true&&snapshot?.project?.id===(state.activeEngagement||selected);
    const previous=snapshot,previousGraph=graph;
    const token=cached?generation:++generation;
    if(!cached)graphGeneration++;
    if(!cached){stopEvents();eventRecords=new Map()}
    if(state.engagements.some(x=>x.id===state.activeEngagement))selected=state.activeEngagement;
    if(!state.engagements.some(x=>x.id===selected))selected=state.engagements[0]?.id||"";
    api.onProjectChange?.(selected);state.activeEngagement=selected;
    const project=state.engagements.find(x=>x.id===selected),runs=state.tasks.filter(x=>x.engagement_id===selected),run=runs.find(x=>["running","paused","queued"].includes(x.status))||runs[0];
    legacy.hidden=!!project;snapshot={project,run,runs};graph=null;$("#homeAnswer").textContent="";
    $("#homeProject").innerHTML=state.engagements.map(x=>`<option value="${esc(x.id)}">${esc(x.name||x.id)}</option>`).join("")||`<option value="">${t("没有可选项目","No projects available")}</option>`;$("#homeProject").value=selected;
    $("#homeTitle").textContent=project?.name||t("创建你的第一个研究项目","Create your first research project");
    $("#homeSubtitle").textContent=project?`${domainLabel(project.mode||state.domain)} · ${t("授权范围","Scope")} ${statusLabel(project.status||"unknown")} · ${run?statusLabel(run.status):t("尚无运行","No run yet")}`:t("从已授权目标开始，研究结果必须经过独立复验。","Start with an authorized target. Findings require independent verification.");
    $("#homePause").textContent=run?.status==="paused"?t("恢复","Resume"):t("暂停","Pause");$("#homePause").disabled=!run||!(run.status==="running"||(run.status==="paused"&&run.resume_supported===true));
    $("#homeStop").disabled=!run||!["running","paused","queued"].includes(run.status);$("#homeCopy").disabled=!project;$("#homeScope").disabled=!project;
    // Preserve the legacy renderer target without exposing a second next-step action.
    if(!$("#focusHero")){const old=document.createElement("div");old.id="focusHero";old.hidden=true;legacy.append(old)}
    const verified=list(state.findings.verified).filter(x=>x.engagement_id===selected&&x.status==="verified"),candidates=list(state.findings.candidates).filter(x=>x.engagement_id===selected);
    snapshot.verified=verified;snapshot.candidates=candidates;
    renderNextStep();renderOutcome();
    const agents=null,runners=state.remote.orchestration?.ok?list(state.remote.orchestration.data.runners):null;
    const count=run?`${run.completed_stages??0} / 8`:"–";
    $("#homeMetrics").innerHTML=[[t("研究进度","Research progress"),count,t("当前运行检查点","Current run checkpoints")],[t("智能体","Agents"),agents?.length??"–",t("当前项目 · 逻辑任务","Selected project · Logical tasks")],[t("任务","Tasks"),state.remote.tasks?.ok?runs.length:"–",t("当前项目","Selected project")],[t("证据产物","Evidence artifacts"),"–",t("当前运行","Current run")],[t("已验证","Verified"),state.remote.findings?.ok?verified.length:"–",t("当前项目 · 确认结果","Selected project · Confirmed")],[t("候选发现","Candidates"),state.remote.findings?.ok?candidates.length:"–",t("当前项目 · 尚未确认","Selected project · Unverified")]].map(([a,b,c],i)=>`<article><small>${a}</small><strong id="homeMetric${i}">${b}</strong><span>${c}</span></article>`).join("");
    if(agents)showList("#homeAgents",agents,x=>x.name||x.id,x=>x.current_task||x.role||t("未分配任务","No task assigned"),x=>x.status);else $("#homeAgents").innerHTML=empty(t("智能体清单未接入","Agent inventory not connected"),t("不能把运行数量当作智能体数量。","Run counts cannot be used as agent counts."),"unavailable");
    if(project)optional(`/api/v1/workspace/projects/${encodeURIComponent(selected)}/summary`).then(result=>{if(token!==generation||snapshot?.project?.id!==project.id)return;if(result.ok){$("#homeMetric1").textContent=result.data.task_count;showList("#homeAgents",result.data.tasks,x=>x.id,x=>x.role,x=>x.status)}else{$("#homeMetric1").textContent="–";$("#homeAgents").innerHTML=empty(t("项目任务读取失败","Project task read failed"),t("刷新后重试","Refresh to retry"))}});
    if(runners)showList("#homeRunners",runners,x=>x.name||x.id,x=>x.heartbeat||x.last_seen||t("未提供心跳","No heartbeat available"),x=>x.health||x.status);else $("#homeRunners").innerHTML=empty(t("执行器注册表未接入","Runner registry not connected"),t("在线率与健康状态尚无真实数据源。","No live data source for availability or health yet."),"unavailable");
    showList("#homeFindings",verified,x=>x.title,x=>x.id,x=>x.severity||x.status);
    const audits=state.remote.agentAudits;if(audits?.ok)showList("#homeSentinel",list(audits.data).filter(x=>!x.demo),x=>x.name||x.id,x=>x.created_at||t("本机 AI 审计 · 全局","Local AI audit · Global"),x=>x.status);else $("#homeSentinel").innerHTML=empty(t("监测数据不可用","Monitoring data unavailable"),t("本地审计接口未返回有效状态。","The local audit API returned no valid status."),"unavailable");
    renderStages(null);
    $("#homeGraph").innerHTML=empty(t("读取图谱中","Loading graph"),t("等待项目资产关系","Waiting for project asset relationships"));$("#homeGraphNote").textContent="";
    for(const id of ["#homeEvidence","#homeEvents"])$(id).innerHTML=empty(run?t("读取中","Loading"):t("尚无运行记录","No runs yet"),run?t("正在读取当前运行","Loading the current run"):t("确认授权范围并开始受控执行后显示。","Confirm scope and start a controlled run to see records here."));
    const [g,r,d,s,f]=cached?[previousGraph?{ok:true,data:previousGraph}:null,previous?.events?{ok:true,data:{events:previous.events}}:null,previous?.details?{ok:true,data:previous.details}:null,previous?.runSummary?{ok:true,data:previous.runSummary}:null,previous?.runFindings?{ok:true,data:previous.runFindings}:null]:await Promise.all([project?optional(`/api/v1/engagements/${encodeURIComponent(selected)}/asset-graph`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}/details`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}/summary-report`):null,run?optional(`/api/v1/findings?mode=${encodeURIComponent(state.domain)}&run_id=${encodeURIComponent(run.id)}`):null]);
    if(token!==generation)return;
    if(cached||$("#homeGraphMode").value==="assets"){graph=g?.ok?g.data:null;drawGraph()}else updateGraph();snapshot.details=d?.ok?d.data:null;snapshot.events=r?.ok?list(r.data.events):null;snapshot.runSummary=s?.ok&&s.data.run_id===run?.id?s.data:null;snapshot.runFindings=f?.ok?f.data:null;renderOutcome();renderStages(snapshot.details);
    if(d?.ok){const artifacts=list(d.data.artifacts);$("#homeMetric3").textContent=artifacts.length;showList("#homeEvidence",artifacts,x=>x.id,x=>x.sha256||t("摘要未提供","No digest available"),x=>x.kind||"artifact")}else if(run)$("#homeEvidence").innerHTML=empty(t("证据产物读取失败","Could not load evidence artifacts"),t("刷新重试；无法据此判断证据为空。","Refresh to retry; this does not mean there is no evidence."),"unavailable");
    if(r?.ok)showList("#homeEvents",list(r.data.events).slice().reverse(),x=>x.message||x.kind,x=>`${x.created_at||""} · ${x.stage||""}`,x=>x.kind);else if(run)$("#homeEvents").innerHTML=empty(t("事件读取失败","Could not load events"),t("刷新后重试。","Refresh to retry."),"unavailable");
    if(r?.ok){for(const event of list(r.data.events))if(event.id!=null)eventRecords.set(String(event.id),event);connectEvents()}
  }
  function answerQuestion(e){
    e?.preventDefault();const s=snapshot,q=$("#homeQuestion").value.trim();
    if(!s?.project){$("#homeAnswer").textContent=t("请先选择研究项目。","Select a research project first.");return}
    const origin=`${s.project.id}${s.run?` / ${s.run.id}`:""}`;
    const lines=[t(`来源：${origin}（当前读取快照）`,`Source: ${origin} (current loaded snapshot)`)];
    if(/证据|evidence|验证|verified|发现|finding/i.test(q)){
      const verified=state.remote.findings?.ok?s.verified?.length??0:t("数据不可用","Unavailable"),candidates=state.remote.findings?.ok?s.candidates?.length??0:t("数据不可用","Unavailable");
      const artifacts=s.details?list(s.details.artifacts).length:t("不可用","Unavailable");
      lines.push(t(`可信发现：${verified}；待验证候选：${candidates}。`,`Verified findings: ${verified}; candidates awaiting verification: ${candidates}.`),t(`运行产物：${artifacts}。产物存在不代表独立复验通过。`,`Run artifacts: ${artifacts}. Artifacts alone do not establish successful independent verification.`),t("没有已验证结果不能推断目标安全；具体原因须查看独立验证记录。","No verified findings does not mean the target is safe; inspect the verification records for details."));
    }else if(/进度|任务|阻塞|下一步|status|next|progress|task|block/i.test(q)){
      const status=s.run?statusLabel(s.run.status):t("尚无运行","No run yet"),stage=s.run?.current_stage||t("未记录","Not recorded");
      lines.push(t(`任务状态：${status}；阶段：${stage}。`,`Task status: ${status}; stage: ${stage}.`),t("下一步：","Next step: ")+(s.nextStep?`${s.nextStep.label} · ${s.nextStep.detail}`:t("核对授权范围并检查执行计划","Review scope and the execution plan")));
    }else lines.push(t("当前支持查询：研究进度、下一步、证据与发现。模型对话接口尚未接入，不会模拟 AI 回答。","Supported queries: research progress, next steps, evidence and findings. Model chat is not connected; no AI response is simulated."));
    $("#homeAnswer").textContent=lines.join("\n");
  }
  document.addEventListener("fieldwork:languagechange",async()=>{
    const mode=$("#homeGraphMode").value,question=$("#homeQuestion").value,hadAnswer=!!$("#homeAnswer").textContent;
    renderShell();host.insertBefore(host.querySelector(".home-focus"),host.querySelector(".home-metrics"));bindControls();$("#homeGraphMode").value=mode;$("#homeQuestion").value=question;
    summary.textContent=t("全部项目与待办总览","All projects and open items");
    await update(true);if(hadAnswer&&$("#homeQuestion").value===question)answerQuestion();
  });
  document.addEventListener("visibilitychange",connectEvents);
  window.addEventListener("pagehide",stopEvents);
  new MutationObserver(connectEvents).observe($("#campaign"),{attributes:true,attributeFilter:["class"]});
  return {update,select:async id=>{selected=id;api.onProjectChange?.(id);state.activeEngagement=id;await update()}};
};
