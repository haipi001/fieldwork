(() => {
  window.settingsEnhancerReady = true;
  const optionalIds = new Set(['strix', 'shannon']);
  const defaultIds = new Set(['native-agent', 'pentest-ai', 'nuclei', 'katana', 'httpx', 'subfinder', 'semgrep', 'gitleaks', 'trivy', 'forge', 'anvil', 'cast', 'slither', 'aderyn', 'echidna', 'medusa', 'halmos']);
  const labels = {
    'native-agent': 'Native Agent', strix: 'Strix', shannon: 'Shannon', 'pentest-ai': 'pentest-ai',
    nuclei: 'Nuclei', katana: 'Katana', httpx: 'httpx', subfinder: 'Subfinder', semgrep: 'Semgrep',
    gitleaks: 'Gitleaks', trivy: 'Trivy', forge: 'Forge', anvil: 'Anvil', cast: 'Cast',
    slither: 'Slither', aderyn: 'Aderyn', echidna: 'Echidna', medusa: 'Medusa', halmos: 'Halmos'
  };
  const zh = {
    'Autonomous security agent': '自主安全研究 Agent',
    'Source-aware proof-by-exploitation Web/API agent': '源码感知的 Web/API 漏洞证明引擎',
    'Deterministic HTTP oracle and proof capsules': '确定性 HTTP 复验与证据胶囊',
    'Template scanner': '模板化漏洞与错误配置检测', 'Web crawler': 'Web 路径与接口爬取',
    'HTTP probe': 'HTTP 服务与技术识别', 'Subdomain discovery': '授权子域发现',
    'Static analysis': '源码静态规则分析', 'Secret scanning': '源码密钥与凭据检查',
    'Dependency and image analysis': '依赖、配置与供应链分析', 'Foundry build and fuzz': '合约编译、测试与 Fuzz',
    'Local EVM fork': '本地 EVM Fork', 'EVM RPC utility': 'EVM RPC 读取与调用',
    'Solidity static analyzer': 'Solidity 静态分析', 'Rust Solidity analyzer': 'Rust Solidity 分析器',
    'Property fuzzer': '合约属性 Fuzz', 'Parallel Solidity fuzzer': '并行覆盖引导 Fuzz',
    'Symbolic testing': '符号执行测试'
  };

  const sheet = document.createElement('dialog');
  sheet.id = 'toolConditionSheet';
  sheet.className = 'condition-sheet';
  sheet.innerHTML = '<form method="dialog"><button class="sheet-close" value="cancel" aria-label="关闭">×</button><p class="eyebrow">ACTIVATION CHECK</p><h2 id="conditionTitle">启用条件</h2><p id="conditionIntro"></p><div id="conditionList" class="condition-list"></div><div id="conditionActions" class="sheet-actions"></div></form>';
  document.body.append(sheet);

  const maintenanceSheet = document.createElement('dialog');
  maintenanceSheet.id = 'maintenanceSheet';
  maintenanceSheet.className = 'condition-sheet maintenance-sheet';
  maintenanceSheet.innerHTML = '<form method="dialog"><button class="sheet-close" value="cancel" aria-label="关闭">×</button><p class="eyebrow">LOCAL DATA</p><h2 id="maintenanceTitle"></h2><p id="maintenanceIntro"></p><label id="maintenanceConfirmLabel" hidden>输入“清空”继续<input id="maintenanceConfirmInput" autocomplete="off"></label><div class="sheet-actions"><button value="cancel" class="quiet-button">取消</button><button id="maintenanceCommit" type="button" class="danger-button">执行</button></div></form>';
  document.body.append(maintenanceSheet);

  let snapshot = null;
  const enabledOptional = () => new Set(JSON.parse(localStorage.getItem('fieldwork-optional-tools') || '[]'));
  const saveOptional = set => localStorage.setItem('fieldwork-optional-tools', JSON.stringify([...set]));
  const bytes = n => n > 1073741824 ? `${(n / 1073741824).toFixed(1)} GB` : `${(n / 1048576).toFixed(1)} MB`;
  const health = (ok, waiting = false) => `<i class="health-dot ${ok ? 'ok' : waiting ? 'wait' : 'bad'}"></i>`;

  function renderStatus(status, readiness) {
    const provider = status.provider;
    const agent = status.native_agent;
    $('.settings-grid').innerHTML = `
      <section class="status-cell lead"><small>本机运行时</small><strong>${health(readiness.ready)}${readiness.available_core}/${readiness.required_core}</strong><p>Traditional 与 Web3 原生核心工具</p></section>
      <section class="status-cell"><small>Native Agent</small><strong>${health(agent.ready, agent.available)}${agent.ready ? 'Ready' : agent.available ? '等待模型' : '不可用'}</strong><p>系统 Chrome · ${agent.safety === 'read_only_navigation_scope_budget_guarded' ? '全请求受控' : '状态未知'}</p></section>
      <section class="status-cell"><small>模型连接</small><strong>${health(provider.connected, provider.configured)}${provider.connected ? '已连接' : provider.configured ? '连接失败' : '未配置'}</strong><p>${esc(provider.model || '填写模型 ID')}${provider.endpoint ? ` · ${esc(provider.endpoint)}` : ''}</p></section>
      <section class="status-cell"><small>外部网络</small><strong>${health(status.network.connected)}${status.network.connected ? 'Online' : 'Offline'}</strong><p>${status.network.latency_ms == null ? '连接不可用' : `${status.network.latency_ms} ms · 仅授权目标`}</p></section>
      <section class="status-cell"><small>本地存储</small><strong>${health(status.storage.free_bytes >= 10737418240)}${bytes(status.storage.free_bytes)}</strong><p>${status.storage.free_percent}% 可用 · Docker ${status.docker.running ? '运行中' : status.docker.installed ? '未启动' : '未安装'}</p></section>`;
  }

  function conditionsFor(tool) {
    const provider = snapshot.status.provider;
    const docker = snapshot.status.docker;
    const common = [{label: '已确认目标授权范围', ok: true, note: '每次任务仍需人工确认 Scope'}];
    if (tool.id === 'native-agent') return [
      {label: '系统 Chrome 与 Playwright', ok: tool.available, note: tool.browser || ''},
      {label: '模型 API 已配置并连通', ok: provider.connected, note: provider.configured ? (provider.connected ? provider.model : '检查 API Base、模型 ID 与密钥') : '前往上方配置模型 API'},
      {label: '外部网络可用', ok: snapshot.status.network.connected, note: '只访问 Scope 内目标'}, ...common
    ];
    if (tool.id === 'strix') return [
      {label: 'Strix CLI 已安装', ok: tool.available, note: tool.version || '未安装'},
      {label: '模型 API 已连接', ok: provider.connected, note: provider.model || '未配置'},
      {label: 'Docker Desktop 正在运行', ok: docker.running, note: docker.installed ? '已安装但未启动' : '未安装 Docker'},
      {label: 'Strix Sandbox 镜像可用', ok: tool.sandbox_ready, note: 'ghcr.io/usestrix/strix-sandbox:1.3.0'}, ...common
    ];
    if (tool.id === 'shannon') return [
      {label: 'Shannon CLI 已安装', ok: tool.available, note: tool.version || '未安装'},
      {label: '模型 API 已连接', ok: provider.connected, note: provider.model || '未配置'},
      {label: 'Docker Desktop 正在运行', ok: docker.running, note: docker.installed ? '已安装但未启动' : '未安装 Docker'},
      {label: '同时提供运行 URL 与本地源码', ok: false, later: true, note: '启用后仍需在新建任务的高级设置中填写源码目录'}, ...common
    ];
    return [{label: `${labels[tool.id] || tool.id} 可执行文件已安装`, ok: tool.available, note: tool.version || '需要安装后重新探测'}, ...common];
  }

  function openConditions(id) {
    const tool = snapshot.tools.find(item => item.id === id);
    if (!tool) return;
    const conditions = conditionsFor(tool), allReady = conditions.every(item => item.ok || item.later);
    $('#conditionTitle').textContent = labels[id] || id;
    $('#conditionIntro').textContent = defaultIds.has(id) ? '该能力属于默认运行计划；条件不足时会明确降级。' : '该能力不会默认启动，满足条件后可加入下一次任务。';
    $('#conditionList').innerHTML = conditions.map(item => `<article><span>${health(item.ok, item.later)}</span><div><b>${esc(item.label)}</b><p>${esc(item.note)}</p></div></article>`).join('');
    const actions = $('#conditionActions');
    actions.innerHTML = '<button value="cancel" class="quiet-button">关闭</button>';
    if (['strix', 'shannon'].includes(id)) {
      const enabled = enabledOptional(), active = enabled.has(id);
      actions.innerHTML += `<button type="button" class="primary-action compact" id="toggleOptional" ${allReady ? '' : 'disabled'}><span>${active ? '停用下次任务' : '启用下次任务'}</span><b>→</b></button>`;
      const toggle = $('#toggleOptional');
      if (toggle) toggle.onclick = () => { const set = enabledOptional(); set.has(id) ? set.delete(id) : set.add(id); saveOptional(set); sheet.close(); renderTools(snapshot.tools); };
    }
    if (!providerConfigured() && ['native-agent', 'strix', 'shannon'].includes(id)) actions.innerHTML += '<button type="button" class="text-action" id="jumpProvider">前往模型配置</button>';
    if ($('#jumpProvider')) $('#jumpProvider').onclick = () => { sheet.close(); $('#providerBase').focus(); $('#providerBase').scrollIntoView({behavior:'smooth', block:'center'}); };
    sheet.showModal();
  }

  function providerConfigured() { return Boolean(snapshot?.status?.provider?.configured); }

  function toolCard(tool) {
    const enabled = enabledOptional().has(tool.id);
    const state = tool.ready ? 'ready' : tool.available ? 'waiting' : 'missing';
    const stateText = defaultIds.has(tool.id) ? (tool.ready ? '默认运行' : tool.available ? '等待配置' : '缺少组件') : enabled ? '已加入下次任务' : tool.available ? '可选能力' : '尚未安装';
    const action = optionalIds.has(tool.id) || tool.id === 'native-agent' ? `<button class="tool-action" data-tool-condition="${esc(tool.id)}">${['strix','shannon'].includes(tool.id) ? (enabled ? '已启用' : '启动') : '查看条件'} <span>↗</span></button>` : '';
    return `<article class="tool-card ${state}"><header><div><small>${esc(tool.domain || 'agent')}</small><h3>${esc(labels[tool.id] || tool.id)}</h3></div>${health(tool.ready, tool.available)}</header><p>${esc(zh[tool.detail] || tool.detail || '本机安全研究能力')}</p><div class="tool-version">${esc(tool.version || '未探测到版本')}</div><footer><span>${esc(stateText)}</span>${action}</footer></article>`;
  }

  function renderTools(tools) {
    const primary = tools.filter(item => defaultIds.has(item.id));
    const optional = tools.filter(item => !defaultIds.has(item.id));
    $('#toolGrid').innerHTML = `<div class="tool-group-title"><span>默认运行</span><small>任务创建后自动编排；缺失条件会写入 Coverage Ledger</small></div>${primary.map(toolCard).join('')}<div class="tool-group-title optional"><span>可选增强</span><small>不会自动启动；先查看条件，再加入下一次任务</small></div>${optional.map(toolCard).join('')}`;
    $$('[data-tool-condition]').forEach(button => button.onclick = () => openConditions(button.dataset.toolCondition));
  }

  function renderOracles(oracles) {
    let section = $('#oracleCatalog');
    if (!section) {
      section = document.createElement('section');
      section.id = 'oracleCatalog';
      section.className = 'oracle-catalog';
      $('.tool-section').after(section);
    }
    const level = {automatic_proof:'自动证明', bounded_adapter:'受限适配器', human_review_only:'仅人工复核'};
    section.innerHTML = `<div class="section-heading"><div><p class="eyebrow">PROOF BOUNDARY</p><h2>验证方法目录</h2><p>每种方法公开适用范围、成立条件与反例；目录外类型不会由按钮或文字升级为漏洞。</p></div><strong>${oracles.filter(item=>item.promotes_finding).length} MACHINE ORACLES</strong></div><div class="oracle-grid">${oracles.map(item=>`<article class="${item.promotes_finding?'supported':'review-only'}"><header><span>${esc(level[item.level]||item.level)}</span><b>${esc(item.mode)}</b></header><h3>${esc(item.id)}</h3><p>${esc(item.supports.join(' · ')||'尚无机器支持类别')}</p><dl><dt>成立条件</dt><dd>${esc(item.requires.join('；'))}</dd><dt>正样本</dt><dd>${esc(item.positive)}</dd><dt>负样本 / 失败关闭</dt><dd>${esc(item.negative)}</dd></dl><footer>${esc(item.portable)}</footer></article>`).join('')}</div>`;
  }

  async function refreshSettings() {
    try {
      const [capabilities, provider, readiness, status, oracles] = await Promise.all([
        api('/api/v1/capabilities'), api('/api/v1/traditional/provider'), api('/api/v1/runtime/readiness'), api('/api/v1/runtime/status'), api('/api/v1/verification-oracles')
      ]);
      const agent = {...readiness.native_agent, domain:'agent', detail:'Scope 与预算约束的 Chrome 只读研究 Agent', version:readiness.native_agent.browser};
      snapshot = {tools:[agent, ...capabilities], provider, readiness, status};
      renderStatus(status, readiness); renderTools(snapshot.tools); renderOracles(oracles);
      if (provider.base_url && !$('#providerBase').value) $('#providerBase').value = provider.base_url;
      if (provider.model && !$('#providerModel').value) $('#providerModel').value = provider.model.replace(/^openai\//, '');
      $('#providerBadge').textContent = status.provider.connected ? 'CONNECTED' : provider.configured ? 'CHECK CONNECTION' : 'OPTIONAL';
      $('#providerBadge').classList.toggle('ready', status.provider.connected);
    } catch (error) { toast(error.message); }
  }

  loadTools = refreshSettings;
  $('#refreshTools').onclick = refreshSettings;

  function openMaintenance(kind) {
    const recent = kind === 'recent';
    maintenanceSheet.dataset.kind = kind;
    $('#maintenanceTitle').textContent = recent ? '清空近期记录' : '清理运行缓存';
    $('#maintenanceIntro').textContent = recent ? '项目、任务、候选、证据和报告会被清空。系统先创建可恢复数据库备份；运行中的任务会阻止此操作。' : '只删除 Agent 临时工作目录和仓库检出。漏洞证据与报告保持不变。';
    $('#maintenanceConfirmLabel').hidden = !recent;
    $('#maintenanceConfirmInput').value = '';
    $('#maintenanceCommit').textContent = recent ? '备份并清空' : '清理缓存';
    $('#maintenanceCommit').className = recent ? 'danger-button' : 'primary-action compact';
    maintenanceSheet.showModal();
  }

  $('#clearCache').onclick = () => openMaintenance('cache');
  $('#clearRecent').onclick = () => openMaintenance('recent');
  $('#maintenanceCommit').onclick = async () => {
    const kind = maintenanceSheet.dataset.kind, recent = kind === 'recent';
    if (recent && $('#maintenanceConfirmInput').value !== '清空') { $('#maintenanceConfirmInput').setCustomValidity('请输入“清空”'); $('#maintenanceConfirmInput').reportValidity(); return; }
    const button = $('#maintenanceCommit'); button.disabled = true;
    try {
      const result = await api(recent ? '/api/v1/maintenance/recent-records' : '/api/v1/maintenance/cache/clear', {method: recent ? 'DELETE' : 'POST', body: JSON.stringify({confirmation: recent ? 'CLEAR_RECENT_RECORDS' : 'CLEAR_CACHE'})});
      maintenanceSheet.close();
      $('#maintenanceMessage').textContent = recent ? `记录已清空；恢复备份：${result.backup}` : `已清理 ${result.removed_files} 个文件，释放 ${bytes(result.removed_bytes)}。`;
      if (recent) { state.activeRun = null; state.runs = []; state.engagements = []; state.findings = {verified:[], candidates:[]}; await refresh(); }
      toast(recent ? '近期记录已备份并清空' : '缓存清理完成');
    } catch (error) { $('#maintenanceMessage').textContent = error.message; maintenanceSheet.close(); }
    finally { button.disabled = false; }
  };

  refreshSettings();
})();
