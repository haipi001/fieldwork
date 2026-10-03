/* Campaign overview: all counts retain their API source and project scope. */
window.createFieldworkHome = function(api) {
  const {state,esc,list,row,empty,optional,setView,openInspector,runAction,toast}=api;
  const $=s=>document.querySelector(s);
  const t=(zh,en)=>document.documentElement.lang.startsWith("en")?en:zh;
  const statusLabel=value=>({running:t("运行中","Running"),queued:t("排队中","Queued"),paused:t("已暂停","Paused"),completed:t("已完成","Completed"),stopped:t("已停止","Stopped"),failed:t("失败","Failed"),active:t("已启用","Active"),confirmed:t("已确认","Confirmed"),draft:t("草稿","Draft"),unknown:t("未知","Unknown")}[value]||value);
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
        if(["run.completed","run.stopped","run.failed"].includes(record.kind)){endedEventRun=eventRun;stopEvents();renderLiveEvents(t("收到终态事件 · 刷新可读取最终任务与证据状态","Run ended · Refresh to read the final task and evidence status"))}
      }catch{renderLiveEvents(t("收到无法解析的事件 · 保留最近有效记录","Unreadable event received · Keeping the latest valid records"))}
    };
    source.onerror=()=>{if(source===eventStream)renderLiveEvents(t("事件连接中断，正在重连 · 下方为最近有效记录","Event connection lost; reconnecting · Latest valid records shown below"))};
  }
  const host=document.createElement("div");host.id="homeOverview";
  const panel=(cls,title,kicker,id,view)=>`<article class="panel ${cls}"><header><div><small>${kicker}</small><h2>${title}</h2></div>${view?`<button type="button" data-home-view="${view}">${t("查看全部","View all")}</button>`:""}</header><div id="${id}"></div></article>`;
  function renderShell(){
    host.innerHTML=`<header class="page-heading home-header"><div><p class="kicker">${t("研究工作区","RESEARCH WORKSPACE")}</p><h1 id="homeTitle">${t("研究项目","Research project")}</h1><p id="homeSubtitle">${t("以证据为核心的自主分析与独立复验","Evidence-led analysis and independent verification")}</p><select id="homeProject" aria-label="${t("当前研究项目","Current research project")}"></select></div><div class="home-header-actions"><button id="homePause">${t("暂停","Pause")}</button><button id="homeCopy">${t("复制","Copy")}</button><button id="homeStop">${t("停止","Stop")}</button><button id="homeRefresh">${t("刷新","Refresh")}</button></div></header><nav class="home-tabs" aria-label="${t("研究工作区","Research workspace")}"><button class="active" aria-current="page">${t("概览","Overview")}</button>${[["graph",t("研究图谱","Research graph")],["agents",t("智能体","Agents")],["tasks",t("执行","Execution")],["evidence",t("证据","Evidence")],["verification",t("验证","Verification")],["findings",t("结果","Findings")]].map(([id,label])=>`<button data-home-view="${id}">${label}</button>`).join("")}</nav><div class="home-metrics" id="homeMetrics"></div><div class="home-grid"><article class="panel home-graph"><header><div><small>${t("知识层","KNOWLEDGE LAYER")}</small><h2>${t("研究图谱","Research graph")}</h2></div><div class="home-controls"><select id="homeGraphMode" aria-label="${t("图谱类型","Graph type")}"><option value="assets">${t("已观察资产","Observed assets")}</option><option value="research">${t("研究关系","Research relationships")}</option><option value="evidence">${t("证据引用","Evidence references")}</option><option disabled>${t("攻击路径 · 路径分析接口未接入","Attack paths · Analysis API not connected")}</option></select><button id="homeFit">${t("适配视图","Fit view")}</button><button id="homeZoom" aria-label="${t("放大图谱","Zoom in")}">＋</button></div></header><div class="home-canvas" id="homeGraph"></div><p class="home-note" id="homeGraphNote"></p></article><article class="panel home-focus"><header><div><small>${t("当前焦点","CURRENT FOCUS")}</small><h2>${t("下一步","Next step")}</h2></div><button id="homeScope">${t("核对授权范围","Review scope")}</button></header><div id="homeFocus"></div></article>${panel("home-third",t("智能体执行","Agent execution"),t("执行层","EXECUTION"),"homeAgents","agents")}${panel("home-third",t("证据与独立验证","Evidence & verification"),t("可信层","TRUST LAYER"),"homeEvidence","verification")}${panel("home-third",t("确认发现","Verified findings"),t("确认结果","CANONICAL RESULTS"),"homeFindings","findings")}${panel("home-half",t("本机安全监控","Local security monitoring"),t("持续监控","CONTINUOUS MONITOR"),"homeSentinel","sentinel")}${panel("home-half",t("执行器健康","Runner health"),t("执行基础设施","EXECUTION INFRASTRUCTURE"),"homeRunners","runners")}<article class="panel home-wide"><div class="home-stages" id="homeStages"></div></article>${panel("home-half",t("最新动态","Latest activity"),t("实时事件","LIVE EVENTS"),"homeEvents","events")}<article class="panel home-half home-assistant"><header><div><small>${t("研究助手","FIELDWORK ASSISTANT")}</small><h2>${t("询问当前研究","Ask about this research")}</h2></div><span class="home-note">${t("本地事实查询","Local fact lookup")}</span></header><form id="homeAsk"><label for="homeQuestion" class="home-note">${t("查询进度、证据、发现、阻塞或下一步；不发起扫描","Look up progress, evidence, findings, blockers or next steps; no scan is started")}</label><textarea id="homeQuestion" required maxlength="2000" placeholder="${t("例如：当前项目为什么还没有已验证发现？","For example: Why are there no verified findings yet?")}"></textarea><footer><span class="home-note">${t("仅检索当前 API 快照；非模型推理","Current API snapshot only; not model reasoning")}</span><button class="primary-button">${t("查询","Look up")}</button></footer></form><div id="homeAnswer" class="home-answer" role="status"></div></article></div>`;
  }
  renderShell();
  const campaign=$("#campaign"),legacy=document.createElement("details");legacy.className="home-archive";
  const summary=document.createElement("summary");summary.textContent=t("全部项目与待办总览","All projects and open items");legacy.append(summary);
  while(campaign.firstChild)legacy.append(campaign.firstChild);
  campaign.append(host,legacy);$("#homeFocus").append($("#focusHero"));
  host.addEventListener("click",e=>{const button=e.target.closest("[data-home-view]");if(button){state.activeRun=snapshot?.run?.id||"";if(button.dataset.homeView==="graph")$("#graphCampaign").value=selected;setView(button.dataset.homeView)}});
  function bindControls(){
  $("#homeProject").onchange=e=>{selected=e.target.value;update()};
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
  async function update(cached=false){
    cached=cached===true;
    const previous=snapshot,previousGraph=graph;
    const token=cached?generation:++generation;
    if(!cached)graphGeneration++;
    if(!cached){stopEvents();eventRecords=new Map()}
    if(!state.engagements.some(x=>x.id===selected))selected=state.engagements[0]?.id||"";
    const project=state.engagements.find(x=>x.id===selected),runs=state.tasks.filter(x=>x.engagement_id===selected),run=runs.find(x=>["running","paused","queued"].includes(x.status))||runs[0];
    snapshot={project,run,runs};graph=null;$("#homeAnswer").textContent="";
    $("#homeProject").innerHTML=state.engagements.map(x=>`<option value="${esc(x.id)}">${esc(x.name||x.id)}</option>`).join("")||`<option value="">${t("没有可选项目","No projects available")}</option>`;$("#homeProject").value=selected;
    $("#homeTitle").textContent=project?.name||t("创建你的第一个研究项目","Create your first research project");
    $("#homeSubtitle").textContent=project?`${domainLabel(project.mode||state.domain)} · ${t("授权范围","Scope")} ${statusLabel(project.status||"unknown")} · ${run?statusLabel(run.status):t("尚无运行","No run yet")}`:t("从已授权目标开始，研究结果必须经过独立复验。","Start with an authorized target. Findings require independent verification.");
    $("#homePause").textContent=run?.status==="paused"?t("恢复","Resume"):t("暂停","Pause");$("#homePause").disabled=!run||!(run.status==="running"||(run.status==="paused"&&run.resume_supported===true));
    $("#homeStop").disabled=!run||!["running","paused","queued"].includes(run.status);$("#homeCopy").disabled=!project;$("#homeScope").disabled=!project;
    $("#homeFocus").innerHTML=project?`<h3>${esc(project.normalized_target||project.target||project.name)}</h3><p>${esc(run?.next_action||t("核对授权范围并检查执行计划","Review the authorized scope and execution plan"))}</p><p class="home-note">${esc(run?.current_stage||t("尚未执行","Not started"))} · ${esc(run?.id||project.id)}</p><button id="homePlan">${t("检查执行计划","Review execution plan")}</button>`:empty(t("尚无研究焦点","No research focus yet"),t("新建研究后在此核对下一步。","Create a research project to review the next step here."));
    // Keep the existing overview renderer's target available inside its archive.
    if(!$("#focusHero")){const old=document.createElement("div");old.id="focusHero";old.hidden=true;legacy.append(old)}
    if($("#homePlan"))$("#homePlan").onclick=()=>api.openExecution(selected);
    const verified=list(state.findings.verified).filter(x=>x.engagement_id===selected&&x.status==="verified"),candidates=list(state.findings.candidates).filter(x=>x.engagement_id===selected);
    snapshot.verified=verified;snapshot.candidates=candidates;
    const agents=state.remote.orchestration?.ok?list(state.remote.orchestration.data.agents):null,runners=state.remote.orchestration?.ok?list(state.remote.orchestration.data.runners):null;
    const count=run?`${run.completed_stages??0} / 8`:"–";
    $("#homeMetrics").innerHTML=[[t("研究进度","Research progress"),count,t("当前运行检查点","Current run checkpoints")],[t("智能体","Agents"),agents?.length??"–",t("全局清单 · 非当前项目","Global inventory · All projects")],[t("任务","Tasks"),state.remote.tasks?.ok?runs.length:"–",t("当前项目","Selected project")],[t("证据产物","Evidence artifacts"),"–",t("当前运行","Current run")],[t("已验证","Verified"),state.remote.findings?.ok?verified.length:"–",t("当前项目 · 确认结果","Selected project · Confirmed")],[t("候选发现","Candidates"),state.remote.findings?.ok?candidates.length:"–",t("当前项目 · 尚未确认","Selected project · Unverified")]].map(([a,b,c],i)=>`<article><small>${a}</small><strong id="homeMetric${i}">${b}</strong><span>${c}</span></article>`).join("");
    if(agents)showList("#homeAgents",agents,x=>x.name||x.id,x=>x.current_task||x.role||t("未分配任务","No task assigned"),x=>x.status);else $("#homeAgents").innerHTML=empty(t("智能体清单未接入","Agent inventory not connected"),t("不能把运行数量当作智能体数量。","Run counts cannot be used as agent counts."),"unavailable");
    if(runners)showList("#homeRunners",runners,x=>x.name||x.id,x=>x.heartbeat||x.last_seen||t("未提供心跳","No heartbeat available"),x=>x.health||x.status);else $("#homeRunners").innerHTML=empty(t("执行器注册表未接入","Runner registry not connected"),t("在线率与健康状态尚无真实数据源。","No live data source for availability or health yet."),"unavailable");
    showList("#homeFindings",verified,x=>x.title,x=>x.id,x=>x.severity||x.status);
    const audits=state.remote.agentAudits;if(audits?.ok)showList("#homeSentinel",list(audits.data).filter(x=>!x.demo),x=>x.name||x.id,x=>x.created_at||t("本机 AI 审计 · 全局","Local AI audit · Global"),x=>x.status);else $("#homeSentinel").innerHTML=empty(t("监测数据不可用","Monitoring data unavailable"),t("本地审计接口未返回有效状态。","The local audit API returned no valid status."),"unavailable");
    $("#homeStages").innerHTML=Array.from({length:8},(_,i)=>`<div class="${i<(run?.completed_stages??0)?"done":""}">${t("检查点","Checkpoint")} ${i+1}<small>${i<(run?.completed_stages??0)?t("已完成","Completed"):t("未完成 / 未确认","Incomplete / Unconfirmed")}</small></div>`).join("");
    $("#homeGraph").innerHTML=empty(t("读取图谱中","Loading graph"),t("等待项目资产关系","Waiting for project asset relationships"));$("#homeGraphNote").textContent="";
    for(const id of ["#homeEvidence","#homeEvents"])$(id).innerHTML=empty(run?t("读取中","Loading"):t("尚无运行记录","No runs yet"),run?t("正在读取当前运行","Loading the current run"):t("确认授权范围并开始受控执行后显示。","Confirm scope and start a controlled run to see records here."));
    const [g,r,d]=cached?[previousGraph?{ok:true,data:previousGraph}:null,previous?.events?{ok:true,data:{events:previous.events}}:null,previous?.details?{ok:true,data:previous.details}:null]:await Promise.all([project?optional(`/api/v1/engagements/${encodeURIComponent(selected)}/asset-graph`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}/details`):null]);
    if(token!==generation)return;
    if(cached||$("#homeGraphMode").value==="assets"){graph=g?.ok?g.data:null;drawGraph()}else updateGraph();snapshot.details=d?.ok?d.data:null;snapshot.events=r?.ok?list(r.data.events):null;
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
      lines.push(t(`任务状态：${status}；阶段：${stage}。`,`Task status: ${status}; stage: ${stage}.`),t("下一步：","Next step: ")+(s.run?.next_action||t("核对授权范围并检查执行计划","Review scope and the execution plan")));
    }else lines.push(t("当前支持查询：研究进度、下一步、证据与发现。模型对话接口尚未接入，不会模拟 AI 回答。","Supported queries: research progress, next steps, evidence and findings. Model chat is not connected; no AI response is simulated."));
    $("#homeAnswer").textContent=lines.join("\n");
  }
  document.addEventListener("fieldwork:languagechange",async()=>{
    const mode=$("#homeGraphMode").value,question=$("#homeQuestion").value,hadAnswer=!!$("#homeAnswer").textContent;
    renderShell();bindControls();$("#homeGraphMode").value=mode;$("#homeQuestion").value=question;
    summary.textContent=t("全部项目与待办总览","All projects and open items");
    await update(true);if(hadAnswer&&$("#homeQuestion").value===question)answerQuestion();
  });
  document.addEventListener("visibilitychange",connectEvents);
  window.addEventListener("pagehide",stopEvents);
  new MutationObserver(connectEvents).observe($("#campaign"),{attributes:true,attributeFilter:["class"]});
  return {update};
};
