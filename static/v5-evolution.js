(() => {
  "use strict";

  window.createFieldworkEvolution = ({state, esc, list, request, post, toast}) => {
    const localeBindings = window.FieldworkLocale.bindings();
    const localizedText = localeBindings.text;
    const $ = selector => document.querySelector(selector);
    const t = (zh, en) => document.documentElement.lang.startsWith("en") ? en : zh;
    const statusLabel = value => ({queued:t("排队中","Queued"), leased:t("已领取","Leased"), running:t("运行中","Running"), succeeded:t("已成功","Succeeded"), failed:t("失败","Failed"), cancelled:t("已取消","Cancelled")}[value] || value);
    const modeLabel = value => ({mutation:t("变异","Mutation"), combine:t("组合","Combine"), evolver:t("演化器","Evolver")}[value] || value);
    const host = $("#evolutionContent");
    const selector = (id, label) => `<option value="${esc(id)}">${esc(label)}</option>`;
    const empty = (title, detail) => `<div class="empty-state"><b>${esc(title)}</b><p>${esc(detail)}</p></div>`;
    const api = "/api/v1/evolution";
    let engagementId = "", campaignId = "", groupId = "", populationId = "", transferSourceId = "";
    let campaigns = [], groups = [], populations = [], transfers = [], tasks = [], claims = [], workerStatus = null;
    let taskCursor = "", loadingTasks = false, loadError = null;
    let requestToken = 0, busy = false, pendingAction = null, seedKey = crypto.randomUUID(), transferKey = crypto.randomUUID();

    function message(value, error = false) {
      const element = $("#evolutionStatus");
      localizedText(element,typeof value === "function" ? value : () => value);
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
      if (!population) return `<article class="panel">${empty(t("暂无研究群体","No research population yet"), t("为当前研究组选择至少两个有来源的 Claim，建立首代评估。","Select at least two sourced claims in this group to evaluate the first generation."))}</article>`;
      const latest = visiblePopulations()[0]?.id === population.id;
      const running = relevantTasks(population);
      const allSucceeded = running.length >= population.selection_count && running.every(task => task.status === "succeeded");
      const canAdvance = population.current && latest && !busy;
      const canCollect = population.current && latest && allSucceeded && !taskCursor && !busy;
      const canTick = population.current && canRunOrRecover(running)
        && workerStatus?.evolver?.local_provider_ready === true && !busy;
      const rows = list(population.variants).map(variant => {
        const claim = claims.find(item => item.id === variant.claim_node_id);
        const signals = variant.signals || {};
        return `<tr><td><strong>${esc(claim?.title || variant.claim_node_id)}</strong><small class="mono">${esc(variant.claim_node_id)}</small></td>
          <td>${esc(variant.rank)}</td><td>${esc(Number(variant.score).toFixed(3))}</td>
          <td>${variant.selected ? t("保留","Retained") : t("未选择","Not selected")}</td>
          <td>${esc(signals.support_count ?? 0)} / ${esc(signals.counterevidence_count ?? 0)}</td>
          <td><small>${esc(list(variant.parent_variant_ids).join(" · ") || t("初代 / 重新播种","Initial generation / Reseeded"))}</small></td></tr>`;
      }).join("");
      return `<article class="panel"><div class="evolution-panel-head"><div><small>${t("已选研究群体","SELECTED POPULATION")}</small><h2>${t(`第 ${esc(population.generation)} 代`,`Generation ${esc(population.generation)}`)} · ${esc(population.group_id)}</h2></div>
        <span class="state ${population.current ? "ready" : "failed"}">${population.current ? t("当前","Current") : t("过期","Stale")}</span></div>
        <div class="evolution-meta"><span>${list(population.variants).length} ${t("候选","candidates")}</span><span>${esc(population.selection_count)} ${t("保留","retained")}</span><span>${running.length} ${t("演化任务","evolver tasks")}</span></div>
        <p class="evolution-note">${t("启发式分数只用于研究调度，不是成立概率、影响证明或独立复验。","Heuristic scores guide research scheduling; they are not probabilities of validity, impact evidence or independent verification.")}</p>
        <div class="evolution-actions"><button type="button" data-evolution-action="advance" ${canAdvance ? "" : "disabled"}>${t("生成下一轮任务","Create next-round tasks")}</button>
          <button type="button" data-evolution-action="tick" ${canTick ? "" : "disabled"}>${t("运行或恢复本代 Worker · 最多 2 项","Run or recover workers · Up to 2 tasks")}</button>
          <button type="button" data-evolution-action="collect" ${canCollect ? "" : "disabled"}>${t("汇集下一代","Collect next generation")}</button></div>
        ${!population.current ? `<p class="evolution-note">${t("父代、图谱或授权范围/策略已变化；不可继续推进，请重新播种新的快照。","Parents, the graph or scope/policy changed. Advancement is blocked; reseed with a new snapshot.")}</p>` : ""}
        ${running.some(task => task.status === "failed") ? `<p class="evolution-note">${t("部分演化任务失败，需在任务控制面处理；此处不会跳过失败任务。","Some evolver tasks failed. Resolve them in task controls; failed tasks are not skipped here.")}</p>` : ""}
        <div class="evolution-variants"><table><thead><tr><th>${t("研究主张","Claim")}</th><th>${t("排名","Rank")}</th><th>${t("评分","Score")}</th><th>${t("筛选结果","Selection")}</th><th>${t("证据 / 反证","Evidence / Counterevidence")}</th><th>${t("父代谱系","Parent lineage")}</th></tr></thead><tbody>${rows}</tbody></table></div></article>`;
    }

    function renderTasks(population) {
      const items = relevantTasks(population);
      return `<article class="panel"><div class="evolution-panel-head"><div><small>${t("结构化执行器","STRUCTURED WORKERS")}</small><h2>${t("本代演化任务","This generation's evolver tasks")}</h2></div></div>
        ${items.length ? `<div class="evolution-task-list">${items.map(task => `<div><span>${esc(modeLabel(task.context_capsule?.evolution_mode || "evolver"))}<small class="mono">${esc(task.id)}</small></span><span class="state ${esc(task.status)}">${esc(statusLabel(task.status))}</span></div>`).join("")}</div>`
          : empty(t("尚无演化任务","No evolver tasks yet"), t("选择当前最新一代后，显式生成受预算约束的任务。","Select the latest current generation, then explicitly create budget-constrained tasks."))}
        ${taskCursor ? `<p class="evolution-note">${t(`当前已读取 ${tasks.length} 个 Campaign 任务，列表尚不完整。加载其余记录后可确认本代汇集状态。`,`${tasks.length} campaign tasks loaded; the list is incomplete. Load the remaining records to confirm collection readiness.`)}</p><button type="button" data-evolution-more-tasks ${loadingTasks ? "disabled" : ""}>${loadingTasks ? t("正在加载…","Loading…") : t("继续加载任务 · 最多 500 项","Load more tasks · Up to 500")}</button>` : ""}
        <p class="evolution-note">${t("本地模型服务未就绪时任务保持排队；模型输出必须通过结构化合同校验。","Tasks stay queued until the local provider is ready; model output must pass the structured contract.")}</p></article>`;
    }

    function renderForms() {
      const available = groupClaims(groupId);
      const sources = groups.filter(group => group.id !== groupId && group.status === "active");
      if (!sources.some(group => group.id === transferSourceId)) transferSourceId = sources[0]?.id || "";
      const transferable = groupClaims(transferSourceId);
      const maxSelection = Math.min(16, available.length);
      return `<article class="panel"><div class="evolution-panel-head"><div><small>${t("启动 / 重新播种","START / RESEED")}</small><h2>${t("建立研究群体","Create research population")}</h2></div></div>
        <p class="evolution-note">${t("选择同组 2–32 个已有 Claim。若上一代过期，此操作以新快照重新播种；不回写旧代评分。","Select 2–32 existing claims from the same group. If the previous generation is stale, reseed from a new snapshot without rewriting historical scores.")}</p>
        <form id="evolutionSeedForm" class="evolution-form"><fieldset><legend>${t("组内 Claim","Claims in this group")}</legend>
          ${available.length ? available.slice(0, 32).map(node => `<label class="choice"><input type="checkbox" name="claim" value="${esc(node.id)}"><span>${esc(node.title)}<small class="mono">${esc(node.id)}</small></span></label>`).join("")
            : t("<p class=\"evolution-note\">当前组没有可用 Claim。先在 Research Graph 中记录带组归属的研究 Claim。</p>","<p class=\"evolution-note\">No claims available in this group. First record research claims with group ownership in the research graph.</p>")}
          ${available.length > 32 ? t("<p class=\"evolution-note\">仅显示前 32 个候选，更多 Claim 请通过 API 管理。</p>","<p class=\"evolution-note\">Only the first 32 candidates are shown. Manage additional claims through the API.</p>") : ""}</fieldset>
          <label>${t("保留数量","Number to retain")}<input name="selection" type="number" min="1" max="${maxSelection || 1}" value="${Math.min(2, maxSelection || 1)}"></label>
          <button type="submit" ${available.length >= 2 && !busy ? "" : "disabled"}>${t("评估并建立新一代","Evaluate and create generation")}</button></form></article>
        <article class="panel"><div class="evolution-panel-head"><div><small>${t("跨组传递","CROSS-POLLINATION")}</small><h2>${t("跨组传递精简胶囊","Transfer a compact capsule across groups")}</h2></div></div>
          <p class="evolution-note">${t("仅传递 Claim 要点、Evidence/Counterevidence 引用和开放问题，不复制聊天或原始证据正文。","Transfer claim summaries, evidence and counterevidence references, and open questions only—not chat or raw evidence.")}</p>
          <form id="evolutionTransferForm" class="evolution-form"><label>${t("来源组","Source group")}<select id="evolutionSourceGroup" name="source" required>${selector("", t("选择来源组","Select source group"))}${sources.map(group => selector(group.id, group.role + " · " + group.id)).join("")}</select></label>
            <label>${t("来源 Claim","Source claim")}<select name="claim" required>${selector("", t("选择 Claim","Select claim"))}${transferable.map(node => selector(node.id, node.title)).join("")}</select></label>
            <button type="submit" ${transferable.length && !busy ? "" : "disabled"}>${t("创建目标组 Specialist 任务","Create target-group specialist task")}</button></form>
          <div class="evolution-transfer-list">${transfers.length ? transfers.slice(0, 12).map(item => `<div><strong>${esc(item.capsule?.key_idea || item.source_claim_id)}</strong><small>${esc(item.source_group_id)} → ${esc(item.target_group_id)} · ${item.current ? t("当前","Current") : t("已过期","Stale")}</small></div>`).join("")
            : t("<p class=\"evolution-note\">当前 Campaign 尚无跨组传递记录。</p>","<p class=\"evolution-note\">No cross-group transfers for this campaign.</p>")}</div></article>`;
    }

    function render() {
      const freshness = $("#evolutionFreshness");
      if (loadError) {
        host.innerHTML = empty(t("演化数据读取失败", "Could not load evolution data"), loadError.message || t("请刷新后重试。", "Refresh to retry."));
        localizedText(freshness, () => t("读取失败", "Read failed"));
        return;
      }
      if (!campaignId || !groupId) {
        host.innerHTML = empty(t("选择研究 Campaign 与 Group","Select a research campaign and group"), t("本页仅读取真实持久记录；空状态不代表已验证或已运行。","This page displays persisted records only. An empty view does not establish verification or execution."));
        localizedText(freshness,()=>t("等待选择","Awaiting selection")); freshness.className = "state"; return;
      }
      const visible = visiblePopulations();
      if (!visible.some(item => item.id === populationId)) populationId = visible[0]?.id || "";
      const population = chosenPopulation();
      localizedText(freshness,()=>population ? population.current ? t("当前快照","Current snapshot") : t("快照过期","Stale snapshot") : t("尚无群体","No population yet"));
      freshness.className = `state ${population?.current ? "ready" : population ? "failed" : ""}`;
      host.innerHTML = `<div class="evolution-main"><article class="panel"><div class="evolution-panel-head"><div><small>${t("代际历史","GENERATION HISTORY")}</small><h2>${t("代际记录","Generation history")}</h2></div></div>
          ${visible.length ? `<div class="evolution-list">${visible.map(item => `<button type="button" class="${item.id === populationId ? "active" : ""}" data-evolution-population="${esc(item.id)}"><strong>${t("第 ","Generation ")}${esc(item.generation)}${t(" 代 · "," · ")}${item.current ? t("当前","Current") : t("过期","Stale")}</strong><small>${esc(item.id)} · ${list(item.variants).length}${t(" 候选 / "," candidates / ")}${esc(item.selection_count)}${t(" 保留"," retained")}</small></button>`).join("")}</div>`
            : empty(t("暂无代际记录","No generation history"), t("建立首代研究群体后，排名与父代谱系会显示在这里。","Create the initial population to view rankings and parent lineage."))}</article>
          ${renderPopulation(population)}${renderTasks(population)}</div><div class="evolution-side">${renderForms()}</div>`;
      const source = $("#evolutionSourceGroup"); if (source) source.value = transferSourceId;
    }

    async function loadData() {
      loadError = null;
      const token = ++requestToken;
      taskCursor = ""; loadingTasks = false;
      if (!campaignId) { groups = []; populations = []; transfers = []; tasks = []; claims = []; render(); return; }
      message(()=>(t("正在读取当前 Campaign 的持久演化记录…","Loading persisted evolution records for this campaign…")));
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
        taskCursor = taskData.page?.next_cursor || "";
        claims = list(graphData.nodes); workerStatus = statusData;
        if (!groups.some(item => item.id === groupId)) groupId = groups[0]?.id || "";
        setOptions($("#evolutionGroup"), groups, groupId, t("选择 Group","Select group"), item => `${item.role} · ${item.id}`);
        render();
        message(()=>(graphData.page?.has_more ? t("图谱超过 1000 节点；此页只显示首批 Claim，更多请用 API。","The graph exceeds 1,000 nodes. Only the first batch of claims is shown; use the API for more.") : t("已读取当前 Campaign 的真实演化数据。","Loaded this campaign's recorded evolution data.")));
      } catch (error) {
        if (token !== requestToken) return;
        loadError = error;
        host.innerHTML = empty(t("演化数据读取失败","Could not load evolution data"), error.message || t("请刷新后重试。","Refresh to retry."));
        localizedText($("#evolutionFreshness"),()=>t("读取失败","Read failed"));
        message(()=>(`${t("读取失败：","Read failed: ")}${error.message}`), true);
      }
    }

    async function loadMoreTasks() {
      if (!taskCursor || loadingTasks || !campaignId) return;
      const token = requestToken, id = campaignId, cursor = taskCursor;
      loadingTasks = true; render();
      try {
        const data = await request(`/api/v1/orchestration/tasks?campaign_id=${encodeURIComponent(id)}&limit=500&cursor=${encodeURIComponent(cursor)}`);
        if (token !== requestToken || id !== campaignId) return;
        tasks = [...new Map([...tasks, ...list(data)].map(task => [task.id, task])).values()];
        taskCursor = data.page?.next_cursor || "";
        message(()=>(taskCursor ? `${t("已读取 ","Loaded ")}${tasks.length}${t(" 个任务，可继续加载。"," tasks; more are available.")}` : `${t("任务列表已读取完毕，共 ","All tasks loaded: ")}${tasks.length}${t(" 项。"," items.")}`));
      } catch (error) { if (token === requestToken) message(()=>(`${t("任务分页读取失败：","Task page read failed: ")}${error.message}`), true); }
      finally { if (token === requestToken) { loadingTasks = false; render(); } }
    }

    async function loadCampaigns() {
      const token = ++requestToken;
      campaigns = []; groups = []; populations = []; transfers = []; tasks = []; claims = [];
      taskCursor = ""; loadingTasks = false;
      setOptions($("#evolutionCampaign"), [], "", t("选择 Campaign","Select campaign"), item => item.name || item.id);
      setOptions($("#evolutionGroup"), [], "", t("选择 Group","Select group"), item => item.role || item.id);
      render();
      if (!engagementId) return;
      message(()=>(t("正在读取研究 Campaign…","Loading research campaigns…")));
      try {
        const rows = await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`);
        if (token !== requestToken) return;
        campaigns = list(rows);
        if (!campaigns.some(item => item.id === campaignId)) campaignId = campaigns[0]?.id || "";
        setOptions($("#evolutionCampaign"), campaigns, campaignId, t("选择 Campaign","Select campaign"), item => item.name || item.id);
        await loadData();
      } catch (error) { if (token === requestToken) message(()=>(`${t("Campaign 读取失败：","Campaign read failed: ")}${error.message}`), true); }
    }

    async function load() {
      const available = state.engagements || [];
      if (!available.some(item => item.id === engagementId)) {
        engagementId = available[0]?.id || ""; campaignId = ""; groupId = ""; populationId = "";
      }
      setOptions($("#evolutionEngagement"), available, engagementId, t("选择项目","Select project"), item => item.name || item.normalized_target || item.id);
      if (!available.length) { message(()=>(t("当前研究域没有可选项目。","No projects in the current research domain.")), false); render(); return; }
      await loadCampaigns();
    }

    function confirmAction(kind) {
      const population = chosenPopulation();
      if (!population || !population.current || visiblePopulations()[0]?.id !== population.id || busy) return;
      const ownTasks = relevantTasks(population);
      if (kind === "tick" && (!workerStatus?.evolver?.local_provider_ready || !canRunOrRecover(ownTasks))) return;
      if (kind === "collect" && (taskCursor || !ownTasks.length || ownTasks.some(task => task.status !== "succeeded"))) return;
      pendingAction = {kind, populationId: population.id};
      const titles = () => ({advance: t("生成本代 Evolver 任务","Create evolver tasks for this generation"), tick: t("运行本代本地 Worker","Run local worker for this generation"), collect: t("汇集下一代","Collect next generation")});
      const summaries = () => ({
        advance: t("将为已选父代创建受组预算约束的 mutation/combine 任务；不会立即调用模型或生成 Finding。","Create mutation/combination tasks for selected parents within group budgets. This does not immediately call a model or produce findings."),
        tick: t("检查过期租约并运行本代最多 2 个可领取任务。有效租约不会被抢占；调用已配置的本地模型，输出仍需后端合同校验。","Check expired leases and run up to two eligible tasks for this generation. Valid leases are not preempted. Uses the configured local model; outputs still require backend contract validation."),
        collect: t("将保留已选父代与成功的 draft 提案重新评估为下一代；不会创建 Verified Finding。","Re-evaluate retained parents and successful draft proposals for the next generation. This does not create verified findings."),
      });
      localizedText($("#evolutionActionTitle"),()=>titles()[kind]);
      localizedText($("#evolutionActionSummary"),()=>`${summaries()[kind]} Population：${population.id}`);
      localizedText($("#evolutionActionMessage"),()=>"");
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
        toast(action.kind === "advance" ? `${t("已创建 ","Created ")}${result.created}${t(" 个任务"," tasks")}${result.omitted_combinations ? `${t("；候选容量限制，未创建 ","; candidate capacity limit prevented creation of ")}${result.omitted_combinations}${t(" 个组合"," combinations")}` : ""}` : action.kind === "collect" ? t("下一代已汇集","Next generation collected") : t("本代 Worker tick 已完成","Worker tick completed for this generation"));
        if (action.kind === "collect") populationId = result.id;
        await loadData();
      } catch (error) { localizedText($("#evolutionActionMessage"),()=>`${t("操作失败：","Action failed: ")}${error.message}`); }
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
      if (event.target.closest("[data-evolution-more-tasks]")) { loadMoreTasks(); return; }
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
          message(()=>(t("请选择 2–32 个 Claim，并将保留数量设在有效范围内。","Select 2–32 claims and a valid number to retain.")), true); return;
        }
        busy = true; message(()=>(t("正在建立不可变评估快照…","Creating an immutable evaluation snapshot…")));
        try {
          const result = await post(`${api}/populations`, {campaign_id: campaignId, group_id: groupId,
            idempotency_key: seedKey, selection_count: count,
            variants: ids.map(claim_node_id => ({claim_node_id}))});
          seedKey = crypto.randomUUID(); populationId = result.id; toast(t("研究群体已建立；尚未启动模型任务","Population created; model tasks have not started")); await loadData();
        } catch (error) { message(()=>(`${t("建立失败：","Creation failed: ")}${error.message}`), true); }
        finally { busy = false; render(); }
      } else if (event.target.id === "evolutionTransferForm") {
        event.preventDefault();
        const source = event.target.elements.source.value, claim = event.target.elements.claim.value;
        if (!source || !claim || source === groupId) { message(()=>(t("请选择不同的来源组与有效 Claim。","Select a different source group and a valid claim.")), true); return; }
        busy = true; message(()=>(t("正在传递精简 Context Capsule…","Transferring a compact context capsule…")));
        try {
          await post(`${api}/transfers`, {campaign_id: campaignId, source_group_id: source,
            target_group_id: groupId, source_claim_id: claim, idempotency_key: transferKey});
          transferKey = crypto.randomUUID(); toast(t("已建立目标组 Specialist 任务；尚未独立验证","Target-group specialist task created; not independently verified")); await loadData();
        } catch (error) { message(()=>(`${t("传递失败：","Transfer failed: ")}${error.message}`), true); }
        finally { busy = false; render(); }
      }
    });
    $("#evolutionActionForm").addEventListener("submit", submitAction);
    $("#evolutionActionCancel").addEventListener("click", () => $("#evolutionActionDialog").close());
    $("#evolutionActionBack").addEventListener("click", () => $("#evolutionActionDialog").close());
    document.addEventListener("fieldwork:languagechange", () => {
      window.FieldworkLocale.preserve(document.querySelector("#evolution"), () => {
        setOptions($("#evolutionEngagement"), state.engagements || [], engagementId, t("选择项目", "Select project"), item => item.name || item.normalized_target || item.id);
        setOptions($("#evolutionCampaign"), campaigns, campaignId, t("选择 Campaign", "Select campaign"), item => item.name || item.id);
        setOptions($("#evolutionGroup"), groups, groupId, t("选择 Group", "Select group"), item => `${item.role} · ${item.id}`);
        render();
        localeBindings.apply();
      });
    });
    return {load};
  };
})();
