/* Translate only nodes/attributes from the original static template.
   Dynamic modules own their rendered text; user data is never observed here. */
(() => {
  "use strict";
  // Preserve in-progress controls when a module redraws authored UI copy.
  window.FieldworkLocale = {
    error(zh, en) {
      const error = new Error();
      Object.defineProperty(error, "message", {get: () => document.documentElement.lang.startsWith("en") ? en : zh});
      return error;
    },
    bindings() {
      const bindings = new Map();
      const set = (node, render, property) => {
        if (bindings.size > 200) for (const element of bindings.keys()) if (!element.isConnected) bindings.delete(element);
        bindings.set(node, {render, property});
        node[property] = render();
      };
      return {
        forget: node => bindings.delete(node),
        text: (node, render) => set(node, render, "textContent"),
        html: (node, render) => set(node, render, "innerHTML"),
        title: (node, render) => set(node, render, "title"),
        apply() {
          for (const [node, binding] of bindings) {
            if (!node.isConnected) { bindings.delete(node); continue; }
            node[binding.property] = binding.render();
          }
        }
      };
    },
    preserve(root, render) {
      const controls = [...root.querySelectorAll("input,textarea,select")].map((node, index) => ({
        id: node.id, name: node.name, index, type: node.type, value: node.value,
        checked: node.checked, focused: node === document.activeElement,
        start: node.selectionStart, end: node.selectionEnd
      }));
      const details = [...root.querySelectorAll("details")].map(node => node.open);
      const scrolls = [...root.querySelectorAll("[id]")].filter(node => node.scrollTop || node.scrollLeft).map(node => [node.id, node.scrollTop, node.scrollLeft]);
      render();
      const next = [...root.querySelectorAll("input,textarea,select")];
      for (const saved of controls) {
        const node = saved.id ? next.find(item => item.id === saved.id) : next[saved.index];
        if (!node || node.name !== saved.name || node.type !== saved.type) continue;
        if (node.type !== "file") node.value = saved.value;
        if (["checkbox", "radio"].includes(node.type)) node.checked = saved.checked;
        if (saved.focused) {
          node.focus({preventScroll: true});
          if (saved.start !== null && ["text", "search", "url", "tel", "password", "textarea"].includes(node.type)) node.setSelectionRange(saved.start, saved.end);
        }
      }
      [...root.querySelectorAll("details")].forEach((node, index) => { if (index < details.length) node.open = details[index]; });
      for (const [id, top, left] of scrolls) { const node = document.getElementById(id); if (node) { node.scrollTop = top; node.scrollLeft = left; } }
    }
  };
  const pairs = {
    "跳到主要内容":"Skip to main content", "安全研究空间":"Security workspace",
    "主导航":"Main navigation", "收起导航":"Collapse navigation", "关闭导航":"Close navigation", "打开导航":"Open navigation",
    "研究":"RESEARCH", "监控":"MONITOR", "执行":"EXECUTION", "信任层":"TRUST LAYER", "结果":"RESULTS", "系统":"SYSTEM",
    "研究项目":"Campaigns", "研究图谱":"Research Graph", "研究假设":"Hypotheses", "安全监控":"AI Agent Security",
    "运行事件":"Events", "调查线索":"Incidents", "智能体":"Agents", "研究演化":"Evolution", "运行队列":"Tasks", "执行节点":"Runners", "工具能力":"Tools",
    "证据":"Evidence", "独立复验":"Verification", "确认发现":"Findings", "报告":"Reports", "模型与路由":"AI Runtime", "扩展":"Extensions", "设置":"Settings",
    "连接本机服务":"Connecting to local service", "等待能力状态":"Waiting for capability status", "菜单":"Menu", "搜索":"Search", "研究域":"Research domain",
    "搜索页面与命令":"Search pages and commands", "主题":"Theme", "新建研究":"New research", "部分数据暂时不可用":"Some data is unavailable", "重试":"Retry",
    "从授权边界到可信结果":"From authorized scope to trusted results", "查看研究对象、执行状态、证据变化、可信结论与下一步。":"Review targets, execution, evidence, trusted results, and next steps.",
    "尚未同步":"Not synced yet", "读取中":"Loading", "活动任务":"Active tasks", "候选发现":"Candidate findings", "仍需独立复验":"Independent verification required", "可进入报告":"Eligible for reports",
    "查看图谱":"View graph", "需要处理":"Needs attention", "任务队列":"Task queue", "复核队列":"Review queue", "任务中心":"Task center", "查看队列":"View queue", "查看复验":"View verification", "待复核":"Pending review",
    "Candidate 与模型输出不会自动成为可信 Finding。":"Candidates and model output do not automatically become trusted findings.",
    "资产、研究假设、候选、证据引用与可信结果来自持久化记录。攻击路径分析仍待专用接口。":"Assets, hypotheses, candidates, evidence references, and trusted results come from persisted records. Attack-path analysis is not connected yet.",
    "选择项目":"Select project", "已观察资产":"Observed assets", "研究关系":"Research relationships", "证据引用":"Evidence references", "攻击路径":"Attack paths", "查找节点":"Find nodes", "等待项目数据":"Waiting for project data",
    "这里仅显示真实图谱数据。":"Only recorded graph data appears here.", "尚未选择节点":"No node selected", "选择节点查看来源与 Scope 状态；这不是漏洞验证结论。":"Select a node to inspect its source and scope status. This is not a vulnerability verdict.",
    "研究假设属于具体 Research Campaign；优先级用于安排工作，不代表成立概率。":"Hypotheses belong to a research campaign. Priority schedules work; it is not a probability of validity.",
    "项目":"Project", "研究 Campaign":"Research campaign", "选择 Campaign":"Select campaign", "刷新":"Refresh", "新建研究 Campaign":"New research campaign", "新建假设":"New hypothesis",
    "Agent 负责推理，Runner 负责受控执行。":"Agents reason; runners execute under controls.", "清单":"Inventory", "安全":"Security", "能力":"Capabilities", "记忆":"Memory", "MCP / 供应链":"MCP / Supply Chain", "攻击测试":"Attack Tests",
    "跨组传递精简胶囊，查看候选群体的评估、选择与谱系。优先分数不是漏洞成立概率。":"Transfer compact context between groups and inspect population evaluation, selection, and lineage. Priority scores are not vulnerability probabilities.",
    "等待读取":"Waiting to load", "研究组":"Research group", "选择 Group":"Select group",
    "仅展示当前授权 Campaign 的持久研究记录。Evolver 输出是未验证 draft，不能自动成为 Canonical Result 或 Finding；所有执行操作都需显式确认。":"Only persisted records for the authorized campaign are shown. Evolver output is an unverified draft, not a canonical result or finding. Execution actions require explicit confirmation.",
    "选择研究项目":"Select research project", "读取真实 Population、Transfer 与 AgentTask；不会填充示例排名。":"Load recorded populations, transfers, and agent tasks. No example rankings are inserted.",
    "查看真实 Run 队列、阶段与事件；按当前状态受控暂停、恢复或停止。Priority、Lease 与 Retry 尚待后端接入。":"Review recorded runs, stages, and events. Pause, resume, or stop eligible runs. Run-level priority, leases, and retries are not exposed here.",
    "执行节点受 Capability、Scope、Policy 与 Budget 约束。":"Runners are constrained by capabilities, scope, policy, and budget.",
    "Installed、Executable、Applicable、Executed 与 Valid Result 是不同状态。":"Installed, executable, applicable, executed, and valid result are different states.",
    "本机安全监控":"Local AI security monitor", "持续观察 AI 活动，保留证据，识别需要调查的行为。":"Observe AI activity, retain evidence, and identify behavior that needs investigation.", "本机 · 被动监测":"Local · Passive monitoring", "当前监控":"Current monitor",
    "启用监控":"Enable monitoring", "立即采集":"Collect now", "从观察到调查":"From observation to investigation", "启用采集":"Enable collection", "观察本机 AI 进程及已接入的活动来源。":"Observe local AI processes and connected activity sources.", "配置策略":"Configure policy",
    "明确允许的行为；新策略只影响新入库事件。":"Define allowed behavior. New policies apply only to subsequently ingested events.", "查看分析线索":"Review analysis signals", "策略异常需要独立复验，不能直接当成漏洞。":"Policy anomalies require independent verification; they are not confirmed vulnerabilities.",
    "当前能力边界":"Current limitations", "退出 App 后常驻与自动独立复验尚未完成。采集在线不等于已受到完整保护。":"Operation after app exit and automatic independent verification are not complete. An online collector does not mean full protection.",
    "历史审计与来源覆盖":"Audit history and source coverage", "查看过去的记录，不改变当前监控":"Review past records without changing the current monitor", "审计记录（不改变当前监控）":"Audit record (does not change monitoring)", "选择审计":"Select audit", "刷新记录":"Refresh records",
    "查看所选 Run 的事件；跨系统统一事件流尚未接入。":"Review events for the selected run. A unified cross-system event stream is not connected yet.",
    "Anomaly 与 Candidate 都不自动等于 Incident；当前只呈现本机 AI Audit 的调查线索与可信结果。":"Anomalies and candidates are not automatically incidents. This view shows local AI audit signals and trusted results.", "本机审计":"Local audit",
    "来源、签名、Scope、Hash 与独立性决定证据权重。":"Source, signature, scope, hash, and independence determine evidence weight.", "Failed 与 Not Reproduced 都不等于 Not Vulnerable。":"Failed and not reproduced do not mean not vulnerable.", "候选复验轨迹":"Candidate verification history", "选择 Candidate":"Select candidate", "刷新详情":"Refresh details",
    "这里只展示满足证据和独立复验门槛的结果。":"Only results meeting the evidence and independent verification requirements appear here.", "Verified only · Candidate 不在此页":"Verified only · Candidates are not shown here",
    "仅展示后端 Verified 集合。证据引用数量不代表证据质量；Receipt ID 的存在也不代表当前有效。":"Only the backend verified set is shown. Reference counts do not establish evidence quality; a receipt ID does not establish current validity.",
    "选择已验证结果，检查材料完整度，再生成报告预览。":"Choose a verified result, inspect completeness, then generate a report preview.", "报告来源":"Report source", "选择已验证结果":"Select verified result", "目标平台":"Target platform",
    "当前后端支持平台适配器预览与材料包导出；模板、报告语言和脱敏级别尚不可配置。导出不会自动提交。":"Platform-specific previews and package export are available. Templates, report language, and redaction levels are not configurable yet. Export does not submit a report.", "生成预览":"Generate preview", "报告内容":"Report content", "导出材料包":"Export package", "选择 Verified Finding 后生成预览。":"Choose a verified finding to generate a preview.",
    "模型与计算路由":"Models and compute routing", "查看模型配置、计算路由与能力就绪度；全局路由控制尚未接入。":"Inspect routing profiles and usage, and configure the Traditional tool provider.", "检查中":"Checking", "Traditional 工具模型":"Traditional tool model", "读取 Provider 配置中…":"Loading provider configuration…", "云端":"Cloud", "本机":"Local", "策略升级":"Policy-based escalation", "无外部模型":"No external model",
    "Cloud / Local / Hybrid / Offline 全局路由尚未接入。":"Global routing controls are not available in this interface.", "能力就绪度":"Capability readiness", "配置 Traditional 工具 Provider":"Configure Traditional tool provider",
    "仅影响使用此 Provider 的 Traditional 工具；不会改变全局路由。密钥只提交给本机后端配置接口，不保存在浏览器存储。":"Affects only Traditional tools using this provider, not global routing. Keys are sent only to the local backend and are not saved in browser storage.",
    "我确认将配置写入本机 Provider，可能影响后续授权任务使用的模型":"I confirm saving this local provider configuration, which may affect models used by subsequent authorized tasks", "保存 Provider 配置":"Save provider configuration", "打开 Tools":"Open tools",
    "核对已知工具适配器；Research Packs、MCP、Runner Adapters 与 Physical Runner 的管理能力待接入。":"Inspect known tool adapters. Management for research packs, MCP, runner adapters, and physical runners is not connected yet.",
    "界面偏好与系统边界分开管理。未接入的配置明确标记，不会在浏览器中模拟生效。":"Interface preferences are separate from system controls. Unavailable settings are clearly marked and are not simulated in the browser.", "常规":"General", "默认工作台":"Default workspace", "Fieldwork V5 已作为本机首页":"Fieldwork V5 is the local home page", "外观":"Appearance", "此浏览器的深色 / 浅色偏好":"Dark / light preference for this browser", "切换":"Switch", "语言偏好":"Language preference",
    "切换界面语言；项目内容和原始证据保持原文":"Switch interface language; project content and original evidence remain unchanged", "工作区":"Workspace", "Campaign 与任务":"Campaigns and tasks", "由本机后端提供，页面仅显示真实数据":"Provided by the local backend; only recorded data is shown", "查看":"View",
    "隐私与数据":"Privacy and data", "任务、候选和证据由本机服务读取；页面偏好仅保存在此浏览器。数据导出和保留策略尚无统一配置接口。":"Tasks, candidates, and evidence are read from the local service. Preferences stay in this browser. Unified export and retention controls are not available yet.",
    "安全边界":"Security boundary", "Scope 确认、执行预检和任务控制由后端校验；前端不会把 Candidate 自动提升为 Verified。":"The backend validates scope confirmation, execution preflight, and task controls. The interface never promotes candidates to verified results automatically.", "查看复核边界":"View verification boundary", "密钥":"Secrets",
    "Traditional Provider 密钥只提交至本机后端；页面不回显，也不写入浏览器存储。":"Traditional provider keys are sent only to the local backend. They are not displayed or saved in browser storage.", "查看 Provider":"View provider", "集成接口":"Integrations",
    "统一 MCP/API 管理界面尚未接入。当前可用能力以 Tools Registry 实际返回为准。":"Unified MCP/API management is not connected. Available capabilities are determined by the actual tool registry response.", "查看能力":"View capabilities", "存储":"Storage",
    "研究数据由本机服务管理。V5 当前只在浏览器保存主题、语言偏好与上次页面；没有数据目录迁移控件。":"Research data is managed by the local service. V5 stores only theme, language, and last-page preferences in the browser. Data-directory migration controls are not available.", "备份":"Backup",
    "尚无可验证的 V5 备份状态与恢复接口；此处不显示虚假的“已备份”状态。":"Verified backup status and restore controls are not available in V5. No unverified backup status is displayed.", "更新":"Updates", "尚无 V5 更新检查接口；版本与安装更新需由实际发布流程确认。":"V5 has no update-check interface. Versions and installation updates must be verified through the release process.", "Advanced · 高风险配置":"Advanced · High-risk settings",
    "全局 RuntimeProfile、存储迁移、恢复和 MCP 权限写入尚未提供后端门禁。接入前不开放伪操作。":"Global runtime-profile editing, storage migration, restore, and MCP permission changes are unavailable here until their backend controls are integrated.",
    "对象详情":"Object details", "关闭":"Close", "搜索页面和命令":"Search pages and commands", "移动":"Move", "打开 ·":"Open ·", "创建长期研究 Campaign":"Create long-running research campaign", "仅为当前项目建立研究容器，不会启动扫描或任务。":"Create a research container for this project without starting scans or tasks.",
    "名称":"Name", "研究目标":"Research objective", "策略":"Strategy", "业务逻辑":"Business logic", "风险加权":"Risk weighted", "广度优先":"Breadth first", "深度优先":"Depth first", "取消":"Cancel", "创建 Campaign":"Create campaign", "记录研究假设":"Record research hypothesis", "记录待验证判断；创建不等于证实，也不会执行任务。":"Record an unverified hypothesis. Creation does not confirm it or execute any task.",
    "类别":"Category", "假设陈述":"Hypothesis statement", "下一步":"Next step", "研究优先级":"Research priority", "记录假设":"Record hypothesis", "创建草稿后，先核对 Scope，再由你确认授权范围。":"After creating a draft, review the scope and confirm the authorized boundary.", "工作域":"Domain", "研究名称":"Research name", "目标":"Target", "授权说明":"Authorization notes", "请求预算":"Request budget", "最长运行（分钟）":"Maximum runtime (minutes)",
    "我确认已获得对该目标的测试授权":"I confirm that I am authorized to test this target", "创建草稿":"Create draft", "核对授权边界":"Review authorized scope", "我已核对目标、允许与禁止动作，并确认拥有测试授权":"I have reviewed targets, allowed and prohibited actions, and confirm testing authorization", "确认并冻结 Scope":"Confirm and freeze scope",
    "确认实际执行":"Confirm execution", "扫描档位":"Scan profile", "本地源码目录":"Local source directory", "Web3 Fork RPC":"Web3 fork RPC", "检查执行计划":"Check execution plan", "我已核对实际工具、预算、降级项和禁止动作":"I have reviewed the tools, budget, fallbacks, and prohibited actions", "返回":"Back", "开始受控执行":"Start controlled execution", "确认任务操作":"Confirm task action", "确认":"Confirm",
    "建立定向复测计划":"Create targeted retest plan", "选择同一研究项目的新 Run；不能复用最近一次发现所依赖的 Run。此操作会建立新的待验证 Candidate，不会自动判定修复成功。":"Choose a new run from the same project, not the run of the latest finding. This creates an unverified candidate and does not automatically confirm a fix.", "复测 Run":"Retest run", "选择新的 Run":"Select a new run", "复测说明":"Retest notes", "创建复测计划":"Create retest plan", "确认演化操作":"Confirm evolution action",
    "可能创建持久任务或调用已配置的本地模型；不会自动验证漏洞或触碰外部目标。":"May create persistent tasks or call a configured local model. This does not automatically verify vulnerabilities or contact external targets.", "确认执行":"Confirm execution",
    "当前研究域":"Current research domain", "筛选研究项目、任务、发现及其关联视图；系统能力和本机审计保持全局":"Filter projects, tasks, findings, and related views; system capabilities and local audits remain global", "图谱类型":"Graph type", "等待路径分析 API":"Path-analysis API not connected", "名称或类型":"Name or type", "Agent 信息":"Agent information", "当前本机监控":"Current local monitor", "监控操作说明":"Monitoring workflow",
    "保存时重新输入；现有密钥不会回显":"Re-enter when saving; existing keys are not shown", "输入页面名称":"Enter a page name", "匹配页面":"Matching pages", "例如：授权站点访问控制研究":"For example: authorized site access-control research", "写明允许测试的目标、时间窗口和禁止动作":"Describe authorized targets, time windows, and prohibited actions", "需要源码时填写绝对路径":"Enter an absolute path when source code is required", "Web3 链上目标需要 HTTPS RPC":"Web3 on-chain targets require HTTPS RPC", "写明修复版本、原始根因与要重复的安全断言":"Describe the fixed version, original root cause, and security assertion to repeat"
  };
  const map = new Map();
  const headings = {"研究控制":"RESEARCH CONTROL","研究项目":"RESEARCH CAMPAIGNS","当前研究":"CURRENT RESEARCH","需要关注":"HUMAN ATTENTION","智能体执行":"AGENT EXECUTION","信任边界":"TRUST BOUNDARY","证据 → 复验":"Evidence → Verification","已验证":"Verified","候选":"Candidate","知识层":"KNOWLEDGE LAYER","适配视图":"Fit","节点详情":"NODE INSPECTOR","研究推理":"RESEARCH REASONING","智能体运行":"AGENT RUNTIME","研究演化":"RESEARCH EVOLUTION","执行队列":"EXECUTION QUEUE","执行基础设施":"EXECUTION INFRASTRUCTURE","能力注册表":"CAPABILITY REGISTRY","AI 智能体安全":"AI AGENT SECURITY","监控状态":"MONITOR STATUS","操作流程":"WORKFLOW","运行事件":"RUN EVENTS","调查工作区":"INVESTIGATION WORKSPACE","证据台账":"EVIDENCE LEDGER","独立信任边界":"INDEPENDENT TRUST BOUNDARY","确认结果":"CANONICAL RESULTS","报告生成":"REPORT COMPILER","已验证发现":"Verified Finding","预览":"LIVE PREVIEW","模型运行环境":"AI RUNTIME","提供方状态":"PROVIDER STATUS","云端":"Cloud","本机":"Local","混合":"Hybrid","离线":"Offline","运行健康":"RUNTIME HEALTH","执行边界":"EXECUTION BOUNDARY","扩展管理":"EXTENSION MANAGEMENT","工作区管理":"WORKSPACE CONTROL","常规":"GENERAL","外观":"APPEARANCE","工作区":"WORKSPACE","安全":"SECURITY","密钥":"SECRETS","存储":"STORAGE","备份":"BACKUP","更新":"UPDATES","对象详情":"INSPECTOR","研究项目容器":"RESEARCH CAMPAIGN","研究假设":"RESEARCH HYPOTHESIS","新建研究":"NEW RESEARCH","授权范围确认":"SCOPE GATE","执行计划":"EXECUTION PLAN","快速":"Quick","标准":"Standard","深入":"Deep","运行控制":"RUN CONTROL","定向复测":"DIRECTED RETEST","确认研究操作":"EXPLICIT RESEARCH ACTION","下一步":"NEXT STEP","工具适配器":"Tool Adapter","工具注册领域":"DOMAINS IN TOOL REGISTRY","工具适配器清单":"TOOL ADAPTERS","扩展注册表":"EXTENSION REGISTRY","可用注册数据":"AVAILABLE REGISTRY DATA","规划中的扩展类型":"PLANNED EXTENSION TYPES","回执 / 证明":"Receipt / Proof","候选队列":"CANDIDATE QUEUE","回执注册表":"RECEIPT REGISTRY","不可变回执":"IMMUTABLE RECEIPTS","回执清单":"Receipt Registry","证据引用":"EVIDENCE REFERENCES","尝试次数":"ATTEMPTS","机器回执记录":"MACHINE RECEIPT RECORDS","否定回执":"NEGATIVE RECEIPTS","复验作业":"Verification Jobs","尝试 / 回执":"Attempts / Receipts","路由控制面":"ROUTING CONTROL PLANE","不可变用量台账":"IMMUTABLE USAGE LEDGER","传感器覆盖":"SENSOR COVERAGE","异常信号":"ANOMALY SIGNALS","调查候选":"INVESTIGATION CANDIDATES","确认发现":"VERIFIED FINDINGS","统一事件":"UNIFIED INCIDENTS","调查队列":"INVESTIGATION QUEUE","复验结果":"VERIFICATION RESULT","研究观察":"Observations","运行产物":"Artifacts","覆盖记录":"Coverage","选中对象":"SELECTED OBJECT","研究操作流程":"CAMPAIGN WORKFLOW","启动 / 重新播种":"START / RESEED","跨组传递":"CROSS-POLLINATION","代际历史":"GENERATION HISTORY"};
  for (const [zh,en] of Object.entries(headings)) map.set(en,{zh,en});
  for (const [zh,en] of Object.entries(pairs)) { map.set(zh,{zh,en}); if (!map.has(en)) map.set(en,{zh,en}); }
  const bindings = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode, original = node.textContent;
    const pair = map.get(original.trim());
    if (pair && !node.parentElement.closest("script,style")) bindings.push(() => {
      if (node.isConnected) node.textContent = original.replace(original.trim(), pair[document.documentElement.lang.startsWith("en") ? "en" : "zh"]);
    });
  }
  for (const node of document.querySelectorAll("[aria-label],[placeholder],[title]")) for (const attr of ["aria-label","placeholder","title"]) {
    const pair = map.get(node.getAttribute(attr));
    if (pair) bindings.push(() => { if (node.isConnected) node.setAttribute(attr,pair[document.documentElement.lang.startsWith("en") ? "en" : "zh"]); });
  }
  document.addEventListener("fieldwork:languagechange", () => bindings.forEach(apply => apply()));
  window.FieldworkStaticLocale = {apply: () => bindings.forEach(apply => apply())};
})();
