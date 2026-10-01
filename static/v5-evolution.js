(() => {
  "use strict";

  window.createFieldworkEvolution = ({state, esc, list, request, post, toast}) => {
    const $ = selector => document.querySelector(selector);
    const host = $("#evolutionContent");
    const selector = (id, label) => `<option value="${esc(id)}">${esc(label)}</option>`;
    const empty = (title, detail) => `<div class="empty-state"><b>${esc(title)}</b><p>${esc(detail)}</p></div>`;
    const api = "/api/v1/evolution";
    let engagementId = "", campaignId = "", groupId = "", populationId = "", transferSourceId = "";
    let campaigns = [], groups = [], populations = [], transfers = [], tasks = [], claims = [], workerStatus = null;
    let requestToken = 0, busy = false, pendingAction = null, seedKey = crypto.randomUUID(), transferKey = crypto.randomUUID();

    function message(value, error = false) {
      const element = $("#evolutionStatus");
      element.textContent = value;
      element.dataset.error = String(error);
    }

    function setOptions(element, items, current, placeholder, label) {
      element.innerHTML = selector("", placeholder) + items.map(item => selector(item.id, label(item))).join("");
      element.value = current;
    }

    function linkedGroup(node) {
      const attrs = node.attributes || {};
      if (attrs.group_id) return attrs.group_id;
      return tasks.find(task => task.id === attrs.producer_task_id)?.group_id || "";
    }

    function visiblePopulations() { return populations.filter(item => item.group_id === groupId); }
    function chosenPopulation() { return visiblePopulations().find(item => item.id === populationId) || null; }
    function relevantTasks(population) {
      return tasks.filter(task => task.role === "evolver" && task.context_capsule?.evolution_population_id === population?.id);
    }
    function canRunOrRecover(items) {
      // The server decides lease expiry. A restarted worker may leave only
      // running tasks, so the recovery entry must remain reachable.
      return items.some(task => ["queued", "leased", "running"].includes(task.status));
    }
    function groupClaims(id) {
      return claims.filter(node => node.node_type === "claim" && linkedGroup(node) === id
        && !["retired", "archived", "invalidated"].includes(node.status));
    }

    function renderPopulation(population) {
      if (!population) return `<article class="panel">${empty("暂无研究群体", "为当前研究组选择至少两个有来源的 Claim，建立首代评估。")}</article>`;
      const latest = visiblePopulations()[0]?.id === population.id;
      const running = relevantTasks(population);
      const allSucceeded = running.length >= population.selection_count && running.every(task => task.status === "succeeded");
      const canAdvance = population.current && latest && !busy;
      const canCollect = population.current && latest && allSucceeded && !busy;
      const canTick = population.current && canRunOrRecover(running)
        && workerStatus?.evolver?.local_provider_ready === true && !busy;
      const rows = list(population.variants).map(variant => {
        const claim = claims.find(item => item.id === variant.claim_node_id);
        const signals = variant.signals || {};
        return `<tr><td><strong>${esc(claim?.title || variant.claim_node_id)}</strong><small class="mono">${esc(variant.claim_node_id)}</small></td>
          <td>${esc(variant.rank)}</td><td>${esc(Number(variant.score).toFixed(3))}</td>
          <td>${variant.selected ? "保留" : "未选择"}</td>
          <td>${esc(signals.support_count ?? 0)} / ${esc(signals.counterevidence_count ?? 0)}</td>
          <td><small>${esc(list(variant.parent_variant_ids).join(" · ") || "初代 / 重新播种")}</small></td></tr>`;
      }).join("");
      return `<article class="panel"><div class="evolution-panel-head"><div><small>SELECTED POPULATION</small><h2>第 ${esc(population.generation)} 代 · ${esc(population.group_id)}</h2></div>
        <span class="state ${population.current ? "ready" : "failed"}">${population.current ? "当前" : "过期"}</span></div>
        <div class="evolution-meta"><span>${list(population.variants).length} 候选</span><span>${esc(population.selection_count)} 保留</span><span>${running.length} Evolver 任务</span></div>
        <p class="evolution-note">启发式分数只用于研究调度，不是成立概率、影响证明或独立复验。</p>
        <div class="evolution-actions"><button type="button" data-evolution-action="advance" ${canAdvance ? "" : "disabled"}>生成下一轮任务</button>
          <button type="button" data-evolution-action="tick" ${canTick ? "" : "disabled"}>运行或恢复本代 Worker · 2 项</button>
          <button type="button" data-evolution-action="collect" ${canCollect ? "" : "disabled"}>汇集下一代</button></div>
        ${!population.current ? '<p class="evolution-note">父代、图谱或 Scope/Policy 已变化；不可继续推进，请重新播种新的快照。</p>' : ""}
        ${running.some(task => task.status === "failed") ? '<p class="evolution-note">部分 Evolver 任务失败，需在任务控制面处理；此处不会跳过失败任务。</p>' : ""}
        <div class="evolution-variants"><table><thead><tr><th>Claim</th><th>Rank</th><th>Score</th><th>Selection</th><th>Evidence / Counter</th><th>Parent lineage</th></tr></thead><tbody>${rows}</tbody></table></div></article>`;
    }

    function renderTasks(population) {
      const items = relevantTasks(population);
      return `<article class="panel"><div class="evolution-panel-head"><div><small>STRUCTURED WORKERS</small><h2>本代 Evolver 任务</h2></div></div>
        ${items.length ? `<div class="evolution-task-list">${items.map(task => `<div><span>${esc(task.context_capsule?.evolution_mode || "evolver")}<small class="mono">${esc(task.id)}</small></span><span class="state ${esc(task.status)}">${esc(task.status)}</span></div>`).join("")}</div>`
          : empty("尚无 Evolver 任务", "选择当前最新一代后，显式生成受预算约束的任务。")}
        <p class="evolution-note">本地 Provider 不就绪时任务保持排队；模型输出必须通过结构化合同。</p></article>`;
    }

    function renderForms() {
      const available = groupClaims(groupId);
      const sources = groups.filter(group => group.id !== groupId && group.status === "active");
      if (!sources.some(group => group.id === transferSourceId)) transferSourceId = sources[0]?.id || "";
      const transferable = groupClaims(transferSourceId);
      const maxSelection = Math.min(16, available.length);
      return `<article class="panel"><div class="evolution-panel-head"><div><small>START / RESEED</small><h2>建立研究群体</h2></div></div>
        <p class="evolution-note">选择同组 2–32 个已有 Claim。若上一代过期，此操作以新快照重新播种；不回写旧代评分。</p>
        <form id="evolutionSeedForm" class="evolution-form"><fieldset><legend>组内 Claim</legend>
          ${available.length ? available.slice(0, 32).map(node => `<label class="choice"><input type="checkbox" name="claim" value="${esc(node.id)}"><span>${esc(node.title)}<small class="mono">${esc(node.id)}</small></span></label>`).join("")
            : '<p class="evolution-note">当前组没有可用 Claim。先在 Research Graph 中记录带组归属的研究 Claim。</p>'}
          ${available.length > 32 ? '<p class="evolution-note">仅显示前 32 个候选，更多 Claim 请通过 API 管理。</p>' : ""}</fieldset>
          <label>保留数量<input name="selection" type="number" min="1" max="${maxSelection || 1}" value="${Math.min(2, maxSelection || 1)}"></label>
          <button type="submit" ${available.length >= 2 && !busy ? "" : "disabled"}>评估并建立新一代</button></form></article>
        <article class="panel"><div class="evolution-panel-head"><div><small>CROSS-POLLINATION</small><h2>跨组传递精简胶囊</h2></div></div>
          <p class="evolution-note">仅传递 Claim 要点、Evidence/Counterevidence 引用和开放问题，不复制聊天或原始证据正文。</p>
          <form id="evolutionTransferForm" class="evolution-form"><label>来源组<select id="evolutionSourceGroup" name="source" required>${selector("", "选择来源组")}${sources.map(group => selector(group.id, group.role + " · " + group.id)).join("")}</select></label>
            <label>来源 Claim<select name="claim" required>${selector("", "选择 Claim")}${transferable.map(node => selector(node.id, node.title)).join("")}</select></label>
            <button type="submit" ${transferable.length && !busy ? "" : "disabled"}>创建目标组 Specialist 任务</button></form>
          <div class="evolution-transfer-list">${transfers.length ? transfers.slice(0, 12).map(item => `<div><strong>${esc(item.capsule?.key_idea || item.source_claim_id)}</strong><small>${esc(item.source_group_id)} → ${esc(item.target_group_id)} · ${item.current ? "当前" : "已过期"}</small></div>`).join("")
            : '<p class="evolution-note">当前 Campaign 尚无跨组传递记录。</p>'}</div></article>`;
    }

    function render() {
      const freshness = $("#evolutionFreshness");
      if (!campaignId || !groupId) {
        host.innerHTML = empty("选择研究 Campaign 与 Group", "本页仅读取真实持久记录；空状态不代表已验证或已运行。");
        freshness.textContent = "等待选择"; freshness.className = "state"; return;
      }
      const visible = visiblePopulations();
      if (!visible.some(item => item.id === populationId)) populationId = visible[0]?.id || "";
      const population = chosenPopulation();
      freshness.textContent = population ? population.current ? "当前快照" : "快照过期" : "尚无群体";
      freshness.className = `state ${population?.current ? "ready" : population ? "failed" : ""}`;
      host.innerHTML = `<div class="evolution-main"><article class="panel"><div class="evolution-panel-head"><div><small>GENERATION HISTORY</small><h2>代际记录</h2></div></div>
          ${visible.length ? `<div class="evolution-list">${visible.map(item => `<button type="button" class="${item.id === populationId ? "active" : ""}" data-evolution-population="${esc(item.id)}"><strong>第 ${esc(item.generation)} 代 · ${item.current ? "当前" : "过期"}</strong><small>${esc(item.id)} · ${list(item.variants).length} 候选 / ${esc(item.selection_count)} 保留</small></button>`).join("")}</div>`
            : empty("暂无代际记录", "建立首代研究群体后，排名与父代谱系会显示在这里。")}</article>
          ${renderPopulation(population)}${renderTasks(population)}</div><div class="evolution-side">${renderForms()}</div>`;
      const source = $("#evolutionSourceGroup"); if (source) source.value = transferSourceId;
    }

    async function loadData() {
      const token = ++requestToken;
      if (!campaignId) { groups = []; populations = []; transfers = []; tasks = []; claims = []; render(); return; }
      message("正在读取当前 Campaign 的持久演化记录…");
      try {
        const id = encodeURIComponent(campaignId);
        const [groupData, populationData, transferData, taskData, graphData, statusData] = await Promise.all([
          request(`/api/v1/orchestration/groups?campaign_id=${id}`),
          request(`${api}/populations?campaign_id=${id}&limit=500`),
          request(`${api}/transfers?campaign_id=${id}&limit=500`),
          request(`/api/v1/orchestration/tasks?campaign_id=${id}&limit=500`),
          request(`/api/v1/research/campaigns/${id}/graph?limit=1000`),
          request("/api/v1/workers/status"),
        ]);
        if (token !== requestToken) return;
        groups = list(groupData).filter(item => item.status === "active" || item.id === groupId);
        populations = list(populationData); transfers = list(transferData); tasks = list(taskData);
        claims = list(graphData.nodes); workerStatus = statusData;
        if (!groups.some(item => item.id === groupId)) groupId = groups[0]?.id || "";
        setOptions($("#evolutionGroup"), groups, groupId, "选择 Group", item => `${item.role} · ${item.id}`);
        render();
        message(graphData.page?.has_more ? "图谱超过 1000 节点；此页只显示首批 Claim，更多请用 API。" : "已读取当前 Campaign 的真实演化数据。");
      } catch (error) {
        if (token !== requestToken) return;
        host.innerHTML = empty("演化数据读取失败", error.message || "请刷新后重试。");
        $("#evolutionFreshness").textContent = "读取失败";
        message(`读取失败：${error.message}`, true);
      }
    }

    async function loadCampaigns() {
      const token = ++requestToken;
      campaigns = []; groups = []; populations = []; transfers = []; tasks = []; claims = [];
      setOptions($("#evolutionCampaign"), [], "", "选择 Campaign", item => item.name || item.id);
      setOptions($("#evolutionGroup"), [], "", "选择 Group", item => item.role || item.id);
      render();
      if (!engagementId) return;
      message("正在读取研究 Campaign…");
      try {
        const rows = await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`);
        if (token !== requestToken) return;
        campaigns = list(rows);
        if (!campaigns.some(item => item.id === campaignId)) campaignId = campaigns[0]?.id || "";
        setOptions($("#evolutionCampaign"), campaigns, campaignId, "选择 Campaign", item => item.name || item.id);
        await loadData();
      } catch (error) { if (token === requestToken) message(`Campaign 读取失败：${error.message}`, true); }
    }

    async function load() {
      const available = state.engagements || [];
      if (!available.some(item => item.id === engagementId)) {
        engagementId = available[0]?.id || ""; campaignId = ""; groupId = ""; populationId = "";
      }
      setOptions($("#evolutionEngagement"), available, engagementId, "选择项目", item => item.name || item.normalized_target || item.id);
      if (!available.length) { message("当前研究域没有可选项目。", false); render(); return; }
      await loadCampaigns();
    }

    function confirmAction(kind) {
      const population = chosenPopulation();
      if (!population || !population.current || visiblePopulations()[0]?.id !== population.id || busy) return;
      const ownTasks = relevantTasks(population);
      if (kind === "tick" && (!workerStatus?.evolver?.local_provider_ready || !canRunOrRecover(ownTasks))) return;
      if (kind === "collect" && (!ownTasks.length || ownTasks.some(task => task.status !== "succeeded"))) return;
      pendingAction = {kind, populationId: population.id};
      const titles = {advance: "生成本代 Evolver 任务", tick: "运行本代本地 Worker", collect: "汇集下一代"};
      const summaries = {
        advance: "将为已选父代创建受组预算约束的 mutation/combine 任务；不会立即调用模型或生成 Finding。",
        tick: "检查过期租约并运行本代最多 2 个可领取任务。有效租约不会被抢占；调用已配置的本地模型，输出仍需后端合同校验。",
        collect: "将保留已选父代与成功的 draft 提案重新评估为下一代；不会创建 Verified Finding。",
      };
      $("#evolutionActionTitle").textContent = titles[kind];
      $("#evolutionActionSummary").textContent = `${summaries[kind]} Population：${population.id}`;
      $("#evolutionActionMessage").textContent = "";
      $("#evolutionActionDialog").showModal();
    }

    async function submitAction(event) {
      event.preventDefault();
      if (!pendingAction || busy) return;
      const action = pendingAction;
      const button = $("#evolutionActionCommit"); button.disabled = true; busy = true;
      try {
        const id = encodeURIComponent(action.populationId);
        const url = action.kind === "tick" ? `/api/v1/workers/local/tick?limit=2&population_id=${id}`
          : `${api}/populations/${id}/${action.kind === "advance" ? "advance" : "collect"}`;
        const result = await post(url, {});
        $("#evolutionActionDialog").close(); pendingAction = null;
        toast(action.kind === "advance" ? `已创建 ${result.created} 个任务${result.omitted_combinations ? `；候选容量限制，未创建 ${result.omitted_combinations} 个组合` : ""}` : action.kind === "collect" ? "下一代已汇集" : "本代 Worker tick 已完成");
        if (action.kind === "collect") populationId = result.id;
        await loadData();
      } catch (error) { $("#evolutionActionMessage").textContent = `操作失败：${error.message}`; }
      finally { busy = false; button.disabled = false; render(); }
    }

    $("#evolutionEngagement").addEventListener("change", event => {
      engagementId = event.target.value; campaignId = ""; groupId = ""; populationId = ""; loadCampaigns();
    });
    $("#evolutionCampaign").addEventListener("change", event => {
      campaignId = event.target.value; groupId = ""; populationId = ""; loadData();
    });
    $("#evolutionGroup").addEventListener("change", event => { groupId = event.target.value; populationId = ""; render(); });
    $("#evolutionRefresh").addEventListener("click", () => load());
    host.addEventListener("change", event => {
      if (event.target.id === "evolutionSourceGroup") { transferSourceId = event.target.value; render(); }
    });
    host.addEventListener("click", event => {
      const choice = event.target.closest("[data-evolution-population]");
      if (choice) { populationId = choice.dataset.evolutionPopulation; render(); return; }
      const action = event.target.closest("[data-evolution-action]");
      if (action) confirmAction(action.dataset.evolutionAction);
    });
    host.addEventListener("submit", async event => {
      if (!campaignId || !groupId || busy) return;
      if (event.target.id === "evolutionSeedForm") {
        event.preventDefault();
        const form = event.target;
        const ids = [...form.querySelectorAll('input[name="claim"]:checked')].map(input => input.value);
        const count = Number(form.elements.selection.value);
        if (ids.length < 2 || ids.length > 32 || count < 1 || count > Math.min(ids.length, 16)) {
          message("请选择 2–32 个 Claim，并将保留数量设在有效范围内。", true); return;
        }
        busy = true; message("正在建立不可变评估快照…");
        try {
          const result = await post(`${api}/populations`, {campaign_id: campaignId, group_id: groupId,
            idempotency_key: seedKey, selection_count: count,
            variants: ids.map(claim_node_id => ({claim_node_id}))});
          seedKey = crypto.randomUUID(); populationId = result.id; toast("研究群体已建立；尚未启动模型任务"); await loadData();
        } catch (error) { message(`建立失败：${error.message}`, true); }
        finally { busy = false; render(); }
      } else if (event.target.id === "evolutionTransferForm") {
        event.preventDefault();
        const source = event.target.elements.source.value, claim = event.target.elements.claim.value;
        if (!source || !claim || source === groupId) { message("请选择不同的来源组与有效 Claim。", true); return; }
        busy = true; message("正在传递精简 Context Capsule…");
        try {
          await post(`${api}/transfers`, {campaign_id: campaignId, source_group_id: source,
            target_group_id: groupId, source_claim_id: claim, idempotency_key: transferKey});
          transferKey = crypto.randomUUID(); toast("已建立目标组 Specialist 任务；尚未独立验证"); await loadData();
        } catch (error) { message(`传递失败：${error.message}`, true); }
        finally { busy = false; render(); }
      }
    });
    $("#evolutionActionForm").addEventListener("submit", submitAction);
    $("#evolutionActionCancel").addEventListener("click", () => $("#evolutionActionDialog").close());
    $("#evolutionActionBack").addEventListener("click", () => $("#evolutionActionDialog").close());
    return {load};
  };
})();
