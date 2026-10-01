/* Campaign overview: all counts retain their API source and project scope. */
window.createFieldworkHome = function(api) {
  const {state,esc,list,row,empty,optional,setView,openInspector,runAction,toast}=api;
  const $=s=>document.querySelector(s);
  let selected="",generation=0,graphGeneration=0,snapshot=null,graph=null,scale=1;
  let eventStream=null,eventRun="",eventRecords=new Map(),endedEventRun="";
  function stopEvents(){if(eventStream)eventStream.close();eventStream=null;eventRun=""}
  function renderLiveEvents(message){
    const target=$("#homeEvents");
    const events=[...eventRecords.values()].sort((a,b)=>Number(b.id)-Number(a.id));
    target.innerHTML=`<p class="home-note" role="status">${esc(message)}</p>`+(events.length?`<div class="home-list">${events.slice(0,3).map(x=>row(x.message||x.kind,`${x.created_at||""} · ${x.stage||""}`,x.kind,x)).join("")}</div>`:empty("尚无事件","等待当前 Run 的真实执行事件。"));
    if(snapshot)snapshot.events=events;
  }
  function connectEvents(){
    const run=snapshot?.run;
    if(document.hidden||!$("#campaign").classList.contains("active")||!run||run.id===endedEventRun||!["running","queued","paused"].includes(run.status)){stopEvents();return}
    if(eventStream&&eventRun===run.id)return;
    stopEvents();eventRun=run.id;
    const source=new EventSource(`/api/v1/runs/${encodeURIComponent(run.id)}/events`);eventStream=source;
    source.onopen=()=>{if(source===eventStream)renderLiveEvents("实时连接已建立 · 当前 Run")};
    source.onmessage=event=>{
      if(source!==eventStream)return;
      try{const record=JSON.parse(event.data);if(record.run_id!==eventRun||record.id==null)return;eventRecords.set(String(record.id),record);
        if(eventRecords.size>500){const oldest=[...eventRecords.keys()].sort((a,b)=>Number(a)-Number(b))[0];eventRecords.delete(oldest)}
        renderLiveEvents("实时更新 · "+new Date().toLocaleTimeString());
        if(["run.completed","run.stopped","run.failed"].includes(record.kind)){endedEventRun=eventRun;stopEvents();renderLiveEvents("收到终态事件 · 刷新可读取最终任务与证据状态")}
      }catch{renderLiveEvents("收到无法解析的事件 · 保留最近有效记录")}
    };
    source.onerror=()=>{if(source===eventStream)renderLiveEvents("事件连接中断，正在重连 · 下方为最近有效记录")};
  }
  const host=document.createElement("div");host.id="homeOverview";
  const panel=(cls,title,kicker,id,view)=>`<article class="panel ${cls}"><header><div><small>${kicker}</small><h2>${title}</h2></div>${view?`<button type="button" data-home-view="${view}">查看全部</button>`:""}</header><div id="${id}"></div></article>`;
  host.innerHTML=`<header class="page-heading home-header"><div><p class="kicker">RESEARCH CAMPAIGN</p><h1 id="homeTitle">研究项目</h1><p id="homeSubtitle">以证据为核心的自主分析与独立复验</p><select id="homeProject" aria-label="当前研究项目"></select></div><div class="home-header-actions"><button id="homePause">暂停</button><button id="homeCopy">复制</button><button id="homeStop">停止</button><button id="homeRefresh">刷新</button></div></header><nav class="home-tabs" aria-label="研究工作区"><button class="active" aria-current="page">概览</button>${[["graph","研究图谱"],["agents","Agents"],["tasks","执行"],["evidence","证据"],["verification","验证"],["findings","结果"]].map(([id,label])=>`<button data-home-view="${id}">${label}</button>`).join("")}</nav><div class="home-metrics" id="homeMetrics"></div><div class="home-grid"><article class="panel home-graph"><header><div><small>KNOWLEDGE LAYER</small><h2>研究图谱</h2></div><div class="home-controls"><select id="homeGraphMode" aria-label="图谱类型"><option value="assets">已观察资产</option><option value="research">研究关系</option><option value="evidence">证据引用</option><option disabled>攻击路径 · 路径分析接口未接入</option></select><button id="homeFit">适配视图</button><button id="homeZoom" aria-label="放大图谱">＋</button></div></header><div class="home-canvas" id="homeGraph"></div><p class="home-note" id="homeGraphNote"></p></article><article class="panel home-focus"><header><div><small>CURRENT FOCUS</small><h2>当前焦点</h2></div><button id="homeScope">核对 Scope</button></header><div id="homeFocus"></div></article>${panel("home-third","Agent 执行","EXECUTION","homeAgents","agents")}${panel("home-third","证据与独立验证","TRUST LAYER","homeEvidence","verification")}${panel("home-third","确认发现","CANONICAL RESULTS","homeFindings","findings")}${panel("home-half","本地 Sentinel","CONTINUOUS MONITOR","homeSentinel","sentinel")}${panel("home-half","Runner 健康","EXECUTION INFRASTRUCTURE","homeRunners","runners")}<article class="panel home-wide"><div class="home-stages" id="homeStages"></div></article>${panel("home-half","正在发生什么","LIVE EVENTS","homeEvents","events")}<article class="panel home-half home-assistant"><header><div><small>FIELDWORK ASSISTANT</small><h2>询问当前研究</h2></div><span class="home-note">本地事实查询</span></header><form id="homeAsk"><label for="homeQuestion" class="home-note">查询进度、证据、发现、阻塞或下一步；不发起扫描</label><textarea id="homeQuestion" required maxlength="2000" placeholder="例如：当前项目为什么还没有 Verified Finding？"></textarea><footer><span class="home-note">仅检索当前 API 快照；非模型推理</span><button class="primary-button">查询</button></footer></form><div id="homeAnswer" class="home-answer" role="status"></div></article></div>`;
  const campaign=$("#campaign"),legacy=document.createElement("details");legacy.className="home-archive";
  const summary=document.createElement("summary");summary.textContent="全部项目与待办总览";legacy.append(summary);
  while(campaign.firstChild)legacy.append(campaign.firstChild);
  campaign.append(host,legacy);$("#homeFocus").append($("#focusHero"));
  host.addEventListener("click",e=>{const button=e.target.closest("[data-home-view]");if(button){state.activeRun=snapshot?.run?.id||"";if(button.dataset.homeView==="graph")$("#graphCampaign").value=selected;setView(button.dataset.homeView)}});
  $("#homeProject").onchange=e=>{selected=e.target.value;update()};
  $("#homeRefresh").onclick=api.refresh;
  $("#homePause").onclick=()=>snapshot?.run&&runAction(snapshot.run.status==="paused"?"resume":"pause",snapshot.run.id);
  $("#homeStop").onclick=()=>snapshot?.run&&runAction("stop",snapshot.run.id);
  $("#homeScope").onclick=()=>selected&&api.openScope(selected);
  $("#homeCopy").onclick=async()=>{if(!snapshot?.project)return;try{await navigator.clipboard.writeText([snapshot.project.name,snapshot.project.normalized_target||snapshot.project.target,`Project: ${selected}`,`Domain: ${state.domain}`].filter(Boolean).join("\n"));toast("已复制研究信息；未复制授权或启动任务")}catch{toast("无法访问剪贴板，请从研究详情复制")}};
  $("#homeFit").onclick=()=>{scale=Math.min(1,Math.max(.25,($("#homeGraph").clientWidth-20)/800));drawGraph()};
  $("#homeZoom").onclick=()=>{scale=Math.min(1.5,scale+.15);drawGraph()};
  $("#homeGraphMode").onchange=()=>updateGraph();
  async function updateGraph(){
    const token=++graphGeneration,mode=$("#homeGraphMode").value,id=selected;
    $("#homeGraph").innerHTML=empty("正在读取关系","从当前项目的持久化记录生成图谱。");
    try{
      if(mode==="assets")graph=await api.request(`/api/v1/engagements/${encodeURIComponent(id)}/asset-graph`);
      else{
        const data=await window.loadFieldworkResearchGraph({engagementId:id,mode:state.domain,findings:state.remote.findings?.ok?state.findings:null,request:api.request});
        graph=mode==="evidence"?{
          ...data,nodes:data.nodes.filter(n=>["target","candidate","evidence_reference","counterevidence_reference","canonical_result"].includes(n.entity_type)),
          edges:[...data.edges.filter(e=>["engagement_candidate","cites_evidence","verified_promotion","verified_result"].includes(e.relation_type)),...data.nodes.filter(n=>n.entity_type==="candidate"&&data.edges.some(e=>e.target_id===n.id&&e.relation_type==="linked_candidate")).map(n=>({source_id:data.root_id,target_id:n.id,relation_type:"engagement_candidate",source:"candidate.engagement_id"}))],
        }:data;
      }
      if(token!==graphGeneration||id!==selected)return;
      drawGraph();
    }catch(error){if(token===graphGeneration&&id===selected){graph=null;$("#homeGraph").innerHTML=empty("研究关系读取失败",error.message,"unavailable");$("#homeGraphNote").textContent="请检查数据连接后重试。"}}
  }
  const showList=(id,items,title,detail,status)=>{$(id).innerHTML=items.length?`<div class="home-list">${items.slice(0,3).map(x=>row(title(x),detail(x),status(x),x)).join("")}</div>`:empty("暂无记录","当前数据源未返回可展示条目。")};
  function drawGraph(){
    const canvas=$("#homeGraph");if(!graph){canvas.innerHTML=empty("图谱不可用","选择项目后读取真实资产关系。");return}
    const nodes=list(graph.nodes),edges=list(graph.edges);if(!nodes.length){canvas.innerHTML=empty("尚无节点","当前项目没有保存资产关系。");return}
    const depth=new Map([[graph.root_id||nodes[0].id,0]]);for(let i=0;i<3;i++)for(const e of edges)if(depth.has(e.source_id)&&!depth.has(e.target_id))depth.set(e.target_id,Math.min(3,depth.get(e.source_id)+1));
    const cols=[0,0,0,0],positions=new Map();nodes.forEach(n=>{const col=depth.get(n.id)??1;positions.set(n.id,{x:20+col*200,y:30+cols[col]++*90})});
    const height=Math.max(300,...cols.map(n=>n*90+50));
    canvas.innerHTML=`<div class="asset-graph-stage" style="width:800px;height:${height}px;zoom:${scale}"><svg width="800" height="${height}" aria-hidden="true">${edges.map(e=>{const a=positions.get(e.source_id),b=positions.get(e.target_id);return a&&b?`<path d="M${a.x+170} ${a.y+32} L${b.x} ${b.y+32}"/>`:""}).join("")}</svg>${nodes.map(n=>{const p=positions.get(n.id);return `<button class="asset-node" data-node-type="${esc(n.entity_type)}" data-home-node="${esc(n.id)}" style="left:${p.x}px;top:${p.y}px"><strong>${esc(n.label||n.id)}</strong><small>${esc(n.entity_type||"asset")}</small></button>`}).join("")}</div>`;
    canvas.querySelectorAll("[data-home-node]").forEach(b=>b.onclick=()=>{canvas.querySelectorAll(".selected").forEach(n=>n.classList.remove("selected"));b.classList.add("selected");openInspector(nodes.find(n=>n.id===b.dataset.homeNode))});
    $("#homeGraphNote").textContent=`${nodes.length} 节点 · ${edges.length} 关系；仅为观察事实，不代表漏洞成立。`;
  }
  async function update(){
    const token=++generation;
    graphGeneration++;
    stopEvents();eventRecords=new Map();
    if(!state.engagements.some(x=>x.id===selected))selected=state.engagements[0]?.id||"";
    const project=state.engagements.find(x=>x.id===selected),runs=state.tasks.filter(x=>x.engagement_id===selected),run=runs.find(x=>["running","paused","queued"].includes(x.status))||runs[0];
    snapshot={project,run,runs};graph=null;$("#homeAnswer").textContent="";
    $("#homeProject").innerHTML=state.engagements.map(x=>`<option value="${esc(x.id)}">${esc(x.name||x.id)}</option>`).join("")||'<option value="">没有可选项目</option>';$("#homeProject").value=selected;
    $("#homeTitle").textContent=project?.name||"创建你的第一个研究项目";
    $("#homeSubtitle").textContent=project?`${project.mode||state.domain} · Scope ${project.status||"unknown"} · ${run?.status||"尚无 Run"}`:"从已授权目标开始，研究结果必须经过独立复验。";
    $("#homePause").textContent=run?.status==="paused"?"恢复":"暂停";$("#homePause").disabled=!run||!(run.status==="running"||(run.status==="paused"&&run.resume_supported===true));
    $("#homeStop").disabled=!run||!["running","paused","queued"].includes(run.status);$("#homeCopy").disabled=!project;$("#homeScope").disabled=!project;
    $("#homeFocus").innerHTML=project?`<h3>${esc(project.normalized_target||project.target||project.name)}</h3><p>${esc(run?.next_action||"核对授权范围并检查执行计划")}</p><p class="home-note">${esc(run?.current_stage||"尚未执行")} · ${esc(run?.id||project.id)}</p><button id="homePlan">检查执行计划</button>`:empty("尚无研究焦点","新建研究后在此核对下一步。");
    // Keep the existing overview renderer's target available inside its archive.
    if(!$("#focusHero")){const old=document.createElement("div");old.id="focusHero";old.hidden=true;legacy.append(old)}
    if($("#homePlan"))$("#homePlan").onclick=()=>api.openExecution(selected);
    const verified=list(state.findings.verified).filter(x=>x.engagement_id===selected&&x.status==="verified"),candidates=list(state.findings.candidates).filter(x=>x.engagement_id===selected);
    snapshot.verified=verified;snapshot.candidates=candidates;
    const agents=state.remote.orchestration?.ok?list(state.remote.orchestration.data.agents):null,runners=state.remote.orchestration?.ok?list(state.remote.orchestration.data.runners):null;
    const count=run?`${run.completed_stages??0} / 8`:"–";
    $("#homeMetrics").innerHTML=[["研究进度",count,"Run checkpoints"],["Agents",agents?.length??"–","全局 Inventory"],["任务",state.remote.tasks?.ok?runs.length:"–","当前项目"],["证据产物","–","当前 Run"],["已验证",state.remote.findings?.ok?verified.length:"–","Canonical Results"],["候选发现",state.remote.findings?.ok?candidates.length:"–","不等于 Verified"]].map(([a,b,c],i)=>`<article><small>${a}</small><strong id="homeMetric${i}">${b}</strong><span>${c}</span></article>`).join("");
    if(agents)showList("#homeAgents",agents,x=>x.name||x.id,x=>x.current_task||x.role||"未分配任务",x=>x.status);else $("#homeAgents").innerHTML=empty("Agent Inventory 未接入","不能把 Run 数量当作 Agent 数量。","unavailable");
    if(runners)showList("#homeRunners",runners,x=>x.name||x.id,x=>x.heartbeat||x.last_seen||"Heartbeat 未提供",x=>x.health||x.status);else $("#homeRunners").innerHTML=empty("Runner Registry 未接入","在线率与健康状态尚无真实数据源。","unavailable");
    showList("#homeFindings",verified,x=>x.title,x=>x.id,x=>x.severity||x.status);
const audits=state.remote.agentAudits; if(audits?.ok)showList("#homeSentinel",list(audits.data).filter(x=>!x.demo),x=>x.name||x.id,x=>x.created_at||"本机 AI Audit · 全局",x=>x.status);else $("#homeSentinel").innerHTML=empty("监测数据不可用","本地 Audit 接口未返回有效状态。","unavailable");
    $("#homeStages").innerHTML=Array.from({length:8},(_,i)=>`<div class="${i<(run?.completed_stages??0)?"done":""}">Checkpoint ${i+1}<small>${i<(run?.completed_stages??0)?"已完成":"未完成 / 未确认"}</small></div>`).join("");
    $("#homeGraph").innerHTML=empty("读取图谱中","等待项目资产关系");$("#homeGraphNote").textContent="";
    for(const id of ["#homeEvidence","#homeEvents"])$(id).innerHTML=empty(run?"读取中":"尚无运行记录",run?"正在读取当前 Run":"确认 Scope 并开始受控执行后显示。");
    const [g,r,d]=await Promise.all([project?optional(`/api/v1/engagements/${encodeURIComponent(selected)}/asset-graph`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}`):null,run?optional(`/api/v1/runs/${encodeURIComponent(run.id)}/details`):null]);
    if(token!==generation)return;
    if($("#homeGraphMode").value==="assets"){graph=g?.ok?g.data:null;drawGraph()}else updateGraph();snapshot.details=d?.ok?d.data:null;snapshot.events=r?.ok?list(r.data.events):null;
    if(d?.ok){const artifacts=list(d.data.artifacts);$("#homeMetric3").textContent=artifacts.length;showList("#homeEvidence",artifacts,x=>x.id,x=>x.sha256||"摘要未提供",x=>x.kind||"artifact")}else if(run)$("#homeEvidence").innerHTML=empty("证据产物读取失败","刷新重试；无法据此判断证据为空。","unavailable");
    if(r?.ok)showList("#homeEvents",list(r.data.events).slice().reverse(),x=>x.message||x.kind,x=>`${x.created_at||""} · ${x.stage||""}`,x=>x.kind);else if(run)$("#homeEvents").innerHTML=empty("事件读取失败","刷新后重试。","unavailable");
    if(r?.ok){for(const event of list(r.data.events))if(event.id!=null)eventRecords.set(String(event.id),event);connectEvents()}
  }
  $("#homeAsk").onsubmit=e=>{e.preventDefault();const s=snapshot,q=$("#homeQuestion").value.trim();if(!s?.project){$("#homeAnswer").textContent="请先选择研究项目。";return}const lines=[`来源：${s.project.id}${s.run?` / ${s.run.id}`:""}（当前读取快照）`];if(/证据|evidence|验证|verified|发现|finding/i.test(q)){lines.push(`可信发现：${state.remote.findings?.ok?s.verified.length:"数据不可用"}；待验证候选：${state.remote.findings?.ok?s.candidates.length:"数据不可用"}。`,`运行产物：${s.details?list(s.details.artifacts).length:"不可用"}。产物存在不代表独立复验通过。`,`没有 Verified 结果不能推断目标安全；具体原因须查看 Verification 记录。`)}else if(/进度|任务|阻塞|下一步|status|next|progress/i.test(q)){lines.push(`任务状态：${s.run?.status||"尚无 Run"}；阶段：${s.run?.current_stage||"未记录"}。`,`下一步：${s.run?.next_action||"核对 Scope 并检查执行计划"}`)}else{lines.push("当前支持查询：研究进度、下一步、证据与发现。模型对话接口尚未接入，不会模拟 AI 回答。") }$("#homeAnswer").textContent=lines.join("\n")};
  document.addEventListener("visibilitychange",connectEvents);
  window.addEventListener("pagehide",stopEvents);
  new MutationObserver(connectEvents).observe($("#campaign"),{attributes:true,attributeFilter:["class"]});
  return {update};
};
