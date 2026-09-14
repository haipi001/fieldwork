/* AI Agent Audit entry shell. No audit API, telemetry upload or findings yet. */
(() => {
  const names = ['new','run','findings','reports','settings'];
  const host = name => document.querySelector(`[data-workspace="${name}"]`);
  const hero = host('new').querySelector('.hero-grid');
  const originalHero = hero.innerHTML;
  const architecture = 'https://github.com/haipi001/fieldwork/blob/main/docs/AI_AGENT_AUDIT.md';
  const stages = ['行为采集','自述对账','Policy 检查','独立验证','事件与报告'];
  const details = {
    new: ['AI AGENT AUDIT','第三个工作域，正在构建。','将 Agent 的任务、自述与独立遥测放在一起审查，确认它实际做了什么。'],
    run: ['AGENT INCIDENT TIMELINE','重建 Agent 行为。','后续将在这里展示行为时间线、Policy 检查，以及 Agent Says / Evidence Shows 对账。'],
    findings: ['CANONICAL INCIDENTS','事件结果','确认事件必须经过独立验证。自述、模型建议和候选不能直接成为确认结论。'],
    reports: ['AI INCIDENT REPORT','事件报告','后续支持时间线、Policy 快照、差异、反证与材料清单，并导出 Markdown、JSON、HTML 和证据包。'],
    settings: ['AUDIT CAPABILITIES','审计设置与工具','优先接入结构化 JSON / JSONL、工具调用、进程与网络日志；确定性审计不依赖模型 API。']
  };
  const panels = names.map(name => {
    const [eyebrow,title,description] = details[name];
    const panel = document.createElement('section');panel.className='agent-mode-panel';panel.hidden=true;
    panel.innerHTML=`<div class="agent-mode-card"><p class="eyebrow">${eyebrow}</p><div class="agent-mode-heading"><h2>${title}</h2><span class="state-badge">规划中</span></div><p class="agent-mode-description">${description}</p><ol class="agent-mode-stages">${stages.map((stage,index)=>`<li><span>0${index+1}</span>${stage}</li>`).join('')}</ol><div class="agent-mode-footer"><p>模式入口已就绪，功能尚未接入。当前不会启动审计任务。</p><a class="quiet-button" href="${architecture}" target="_blank" rel="noopener noreferrer">查看完整架构 ↗</a></div></div>`;
    host(name).append(panel);return panel;
  });
  function applyMode() {
    const active=state.mode==='agent_audit';panels.forEach(panel=>panel.hidden=!active);
    if(active&&!hero.dataset.agent){hero.dataset.agent='1';hero.innerHTML='<div class="hero-copy"><p class="eyebrow"><span class="live-dot"></span> AI AGENT AUDIT WORKSPACE</p><h1>审查一次 Agent 行为。<br><em>让每个判断，都有独立证据。</em></h1><p class="lede" id="modeLede">导入 Agent 的任务、行为轨迹与独立遥测。比较自述与实际行为，重建越界、遗漏和异常事件。</p></div><aside class="truth-panel"><div class="truth-index">SELF-REPORT ≠ GROUND TRUTH</div><p>Agent 的解释只是证词，不是事实。只有经过独立遥测、Policy 和证据交叉验证的事件，才能成为确认结论。</p><div class="truth-chain"><span>采集行为</span><i></i><span>对账自述</span><i></i><span>独立验证</span></div></aside>';}
    if(!active&&hero.dataset.agent){hero.innerHTML=originalHero;delete hero.dataset.agent;$('#modeLede').textContent=state.mode==='web3'?'输入已获授权的合约、协议或代码仓库。生产网保持只读，写入只允许 local fork / devnet。':'输入已获授权的网站或代码仓库。系统先冻结 Scope，再安排分析、复验和报告。';}
    document.querySelector('.nav-link[data-go="findings"]').innerHTML=`<span>03</span>${active?'事件结果':'漏洞结果'}`;
    if(active){$('#contextMode').textContent='AI Agent Audit';$('#contextTaskFact').textContent='模式规划中';if(host('findings').classList.contains('active'))$('#contextTitle').textContent='事件结果';}
  }
  function refresh(){applyMode();}
  window.agentAudit={applyMode,refresh};applyMode();
})();
