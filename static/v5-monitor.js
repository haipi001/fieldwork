/* The live control is independent of the selected historical audit. */
(() => {
  "use strict";
  document.documentElement.lang = localStorage.getItem("fieldwork-v5-language") === "en" ? "en" : "zh-CN";
  const t = (zh, en) => document.documentElement.lang.startsWith("en") ? en : zh;
  // Re-evaluate authored copy only; never translate API evidence or replace form nodes.
  const copy = new Map();
  function text(node, value) { copy.set(node, value); node.textContent = value(); }
  const page = document.querySelector("#sentinel");
  const toggle = document.querySelector("#monitorToggle");
  const scan = document.querySelector("#monitorScan");
  const refresh = document.querySelector("#monitorRefresh");
  const status = document.querySelector("#monitorState");
  const message = document.querySelector("#monitorMessage");
  const analysis = document.createElement("p");
  analysis.id = "monitorAnalysis";
  analysis.setAttribute("role", "status");
  document.querySelector("#monitorControl").append(analysis);
  const editor = document.createElement("details");
  editor.innerHTML = '<summary>监控策略</summary><p>只用于判断被动采集记录，不授予执行权限。保存后仅对新入库事件生效（包括迟到记录），历史事件保持原策略。空允许列表表示不允许该类行为。</p><button type="button" id="monitorPolicyRead">读取当前策略</button><form class="form-stack" id="monitorPolicyForm" hidden><div id="monitorPolicyFields"></div><label class="check-line"><input id="monitorPolicyConfirm" type="checkbox" required>我已检查全部规则，确认仅应用到后续新入库事件。</label><button type="submit">保存策略版本</button></form><p id="monitorPolicyMessage" role="status"></p>';
  document.querySelector("#monitorControl").append(editor);
  const policyForm = editor.querySelector("form"), policyRead = editor.querySelector("button"), policyMessage = editor.querySelector("#monitorPolicyMessage");
  const fields = editor.querySelector("#monitorPolicyFields");
  const policyCopy = [
    [editor.querySelector("summary"), "监控策略", "Monitoring policy"],
    [editor.querySelector("p"), "只用于判断被动采集记录，不授予执行权限。保存后仅对新入库事件生效（包括迟到记录），历史事件保持原策略。空允许列表表示不允许该类行为。", "Evaluates passively collected records; it grants no execution permissions. Changes apply only to newly ingested events, including delayed records. Historical events retain their original policy. An empty allowlist permits none of those actions."],
    [policyRead, "读取当前策略", "Read current policy"],
    [policyForm.querySelector('[type="submit"]'), "保存策略版本", "Save policy version"]
  ];
  for (const [node, zh, en] of policyCopy) text(node, () => t(zh, en));
  const confirmation = editor.querySelector('#monitorPolicyConfirm').nextSibling;
  text(confirmation, () => t("我已检查全部规则，确认仅应用到后续新入库事件。", "I have reviewed every rule and confirm application to newly ingested events only."));
  const lists = {allowed_tools:"允许工具", denied_tools:"禁止工具", allowed_network_hosts:"允许网络主机（精确域名/IP）", denied_network_hosts:"禁止网络主机", allowed_filesystem_paths:"允许文件路径（绝对路径）", denied_filesystem_paths:"禁止文件路径", mcp_servers:"允许 MCP 服务", api_hosts:"允许 API 主机"};
  const flags = {internet_access:"允许联网", shell_access:"允许 Shell / 进程执行", browser_access:"允许浏览器", state_change_allowed:"允许状态修改", external_side_effect_allowed:"允许外部副作用", credential_access_allowed:"允许凭据访问", persistence_allowed:"允许持久化行为"};
  const english = {allowed_tools:"Allowed tools", denied_tools:"Denied tools", allowed_network_hosts:"Allowed network hosts (exact domain/IP)", denied_network_hosts:"Denied network hosts", allowed_filesystem_paths:"Allowed file paths (absolute)", denied_filesystem_paths:"Denied file paths", mcp_servers:"Allowed MCP servers", api_hosts:"Allowed API hosts", internet_access:"Allow internet access", shell_access:"Allow shell / process execution", browser_access:"Allow browser access", state_change_allowed:"Allow state changes", external_side_effect_allowed:"Allow external side effects", credential_access_allowed:"Allow credential access", persistence_allowed:"Allow persistence", max_privilege:"Maximum privilege"};
  let policyAuditId = null, policyVersion = null;
  for (const [name, labelText] of Object.entries({...lists, ...flags, max_privilege:"最高权限"})) {
    const label = document.createElement("label"), caption = document.createTextNode("");
    label.append(caption); text(caption, () => t(labelText, english[name]));
    const input = document.createElement(name in lists ? "textarea" : name === "max_privilege" ? "select" : "input");
    input.name = name;
    if (name in lists) { input.rows = 2; input.placeholder = t("每行一项", "One entry per line"); }
    else if (name === "max_privilege") {
      input.innerHTML = '<option value="user"></option><option value="admin"></option>';
      text(input.options[0], () => t("普通用户", "Standard user"));
      text(input.options[1], () => t("管理员", "Administrator"));
    }
    else { input.type = "checkbox"; label.className = "check-line"; }
    label.append(input); fields.append(label);
  }
  let current = null, busy = false, healthy = false;
  async function request(path, method = "GET", body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(`/api/v1/agent-audit/${path}`, {method, signal: controller.signal, headers: {Accept: "application/json", ...(body ? {"Content-Type":"application/json"} : {})}, ...(body ? {body: JSON.stringify(body)} : {})});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  function controls() {
    toggle.textContent = current?.monitor.status === "active" ? t("暂停监控", "Pause monitoring") : current?.monitor.status === "paused" ? t("恢复监控", "Resume monitoring") : t("启用监控", "Enable monitoring");
    document.querySelector("#monitorControl").dataset.status = !healthy ? "error" : current?.monitor.scan_state === "degraded" || current?.monitor.scan_state === "delayed" ? "error" : current?.monitor.status || "inactive";
    toggle.disabled = busy || !healthy;
    scan.disabled = busy || !healthy || current?.monitor.status !== "active";
    refresh.disabled = busy;
    policyRead.disabled = busy || !healthy || !current;
    policyForm.querySelector('[type="submit"]').disabled = busy || !healthy || !current || current.id !== policyAuditId;
  }
  async function read() {
    const audits = await request("audits");
    if (!Array.isArray(audits)) throw window.FieldworkLocale.error("监控列表格式无效","Invalid monitor list");
    const live = audits.filter(a => !a.demo && a.collector_kind === "desktop" && ["active", "paused"].includes(a.monitor_status));
    if (live.length > 1) throw window.FieldworkLocale.error("发现多个活动监控，请检查后台状态","Multiple live monitors found; check backend state");
    current = live.length ? await request(`audits/${encodeURIComponent(live[0].id)}`) : null;
    if (current && (!current.monitor || !["active", "paused"].includes(current.monitor.status))) throw window.FieldworkLocale.error("监控状态已变化，请刷新","Monitor state changed; refresh to continue");
    healthy = true;
    const monitor = current?.monitor;
    text(status, () => {
      const names = {current: t("采集运行中", "Collecting"), delayed: t("采集延迟", "Collection delayed"), degraded: t("采集覆盖降级", "Collection coverage degraded"), paused: t("已暂停", "Paused"), active: t("采集已启用", "Collection enabled")};
      return monitor ? `${names[monitor.scan_state || monitor.status] || t("状态未知", "Unknown state")} · ${t("最近采集：", "Last collection: ")}${monitor.last_scan_at || t("尚无记录", "No records yet")}${monitor.last_error ? ` · ${t("错误：", "Error: ")}${monitor.last_error}` : ""}` : t("尚未启用 · 启用后观察本机 AI 活动，不会主动扫描外部目标", "Not enabled · Observes local AI activity when enabled; does not actively scan external targets");
    });
    text(analysis, () => t("尚无增量分析窗口；未执行独立复验。", "No incremental analysis windows yet; independent verification has not run."));
    if (current) {
      try {
        const data = await request(`monitor/${encodeURIComponent(current.id)}/windows`);
        const latest = data.windows[0];
        text(analysis, () => data.recovery ? t(`增量分析重试中：${data.recovery.last_error}；待分析 ${data.pending_events} 个事件。采集状态单独显示。`, `Incremental analysis retrying: ${data.recovery.last_error}; ${data.pending_events} pending events. Collection status is shown separately.`) : latest ? t(`最近窗口：${latest.event_count} 个事件 · ${latest.status === "policy_required" ? "缺少策略，无法判定违规" : `${latest.signals.length} 条策略线索`} · 待分析 ${data.pending_events} 个事件。尚未独立复验，不是已确认漏洞。`, `Latest window: ${latest.event_count} events · ${latest.status === "policy_required" ? "Policy missing; violations cannot be determined" : `${latest.signals.length} policy signals`} · ${data.pending_events} pending events. Not independently verified; not confirmed vulnerabilities.`) : t(`等待增量分析 · 待分析 ${data.pending_events} 个事件。`, `Awaiting incremental analysis · ${data.pending_events} pending events.`));
      } catch { text(analysis, () => t("增量分析状态不可用；不能据此判断没有异常。", "Analysis status unavailable; this does not establish that activity is safe.")); }
    }
  }
  async function load() {
    if (busy) return;
    busy = true; controls();
    try { await read(); text(message, () => ""); }
    catch (error) { healthy = false; text(status, () => t("监控状态不可用（不能据此判断监控已停止）", "Monitor status unavailable (this does not mean monitoring has stopped)")); text(message, () => t(`读取失败：${error.message}。请刷新重试。`, `Read failed: ${error.message}. Refresh to retry.`)); }
    finally { busy = false; controls(); }
  }
  async function act(action) {
    if (busy || !healthy) return;
    const id = current?.id;
    if (action !== "start" && !id) return;
    busy = true; controls(); text(message, () => t("正在执行…", "Working…"));
    try {
      await request(action === "start" ? "monitor/start" : `monitor/${encodeURIComponent(id)}/${action}`, "POST");
      await read();
      text(message, () => action === "scan" ? t("采集完成；这不是漏洞确认结果。", "Collection complete; this is not a vulnerability confirmation.") : t("监控状态已更新。", "Monitor state updated."));
      document.querySelector("#sentinelRefresh").click();
    } catch (error) {
      healthy = false;
      text(message, () => t(`操作未确认：${error.message}。请先刷新状态，勿重复提交。`, `Action unconfirmed: ${error.message}. Refresh status before submitting again.`));
    } finally { busy = false; controls(); }
  }
  toggle.onclick = () => act(current?.monitor.status === "active" ? "pause" : current?.monitor.status === "paused" ? "resume" : "start");
  scan.onclick = () => act("scan");
  refresh.onclick = load;
  policyRead.onclick = async () => {
    if (busy || !healthy || !current) return;
    busy = true; controls(); policyForm.hidden = true; text(policyMessage, () => t("正在读取…", "Reading…"));
    try {
      const data = await request(`monitor/${encodeURIComponent(current.id)}/policy`);
      policyAuditId = current.id; policyVersion = data.id;
      for (const name of Object.keys(lists)) policyForm.elements[name].value = (data.policy?.[name] || []).join("\n");
      for (const name of Object.keys(flags)) policyForm.elements[name].checked = !!data.policy?.[name];
      policyForm.elements.max_privilege.value = data.policy?.max_privilege || "user";
      editor.querySelector('#monitorPolicyConfirm').checked = false;
      policyForm.hidden = false; text(policyMessage, () => data.id ? t(`当前版本：${data.id}`, `Current version: ${data.id}`) : t("尚未配置；请检查全部规则后保存。", "Not configured; review every rule before saving."));
    } catch (error) { text(policyMessage, () => t(`策略读取失败：${error.message}`, `Policy read failed: ${error.message}`)); }
    finally { busy = false; controls(); }
  };
  policyForm.onsubmit = async event => {
    event.preventDefault();
    if (busy || !healthy || !current || current.id !== policyAuditId || !editor.querySelector('#monitorPolicyConfirm').checked) return;
    const policy = {};
    for (const name of Object.keys(lists)) policy[name] = [...new Set(policyForm.elements[name].value.split("\n").map(s => s.trim()).filter(Boolean))];
    for (const name of Object.keys(flags)) policy[name] = policyForm.elements[name].checked;
    policy.max_privilege = policyForm.elements.max_privilege.value;
    busy = true; controls();
    try {
      const data = await request(`monitor/${encodeURIComponent(policyAuditId)}/policy`, "POST", {policy, expected_id: policyVersion, confirmed: true});
      policyVersion = data.id;
      text(policyMessage, () => t("策略已保存，仅对后续新入库事件生效；未启动主动扫描。", "Policy saved for newly ingested events only; no active scan was started."));
    } catch (error) {
      policyAuditId = null;
      text(policyMessage, () => t(`保存未确认：${error.message}。请重新读取策略后检查确认。`, `Save unconfirmed: ${error.message}. Read the policy again and review it.`));
    } finally { editor.querySelector('#monitorPolicyConfirm').checked = false; busy = false; controls(); }
  };
  const visible = () => page.classList.contains("active") && !document.hidden;
  document.addEventListener("fieldwork:languagechange", () => {
    for (const [node, value] of copy) if (node.isConnected) node.textContent = value();
    for (const input of fields.querySelectorAll("textarea")) input.placeholder = t("每行一项", "One entry per line");
    controls();
  });
  new MutationObserver(() => { if (visible()) load(); }).observe(page, {attributes: true, attributeFilter: ["class"]});
  document.addEventListener("visibilitychange", () => { if (visible()) load(); });
  setInterval(() => { if (visible()) load(); }, 10000);
  if (visible()) load();
})();
