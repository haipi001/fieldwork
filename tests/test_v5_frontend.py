from fastapi.testclient import TestClient

import app as application


def test_v5_workspace_serves_real_control_plane_shell():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    response = client.get("/v5")

    assert response.status_code == 200
    for view in (
        "campaign", "graph", "hypotheses", "agents", "evolution", "tasks", "runners",
        "tools", "sentinel", "events", "incidents", "evidence",
        "verification", "findings", "reports", "runtime", "extensions",
        "settings",
    ):
        assert f'id="{view}"' in response.text
    assert "/static/v5.css" in response.text
    assert "/static/v5.js" in response.text
    assert "/static/v5-evolution.js" in response.text
    assert "/static/v5-evolution.css" in response.text
    assert 'id="languageToggle"' in response.text
    assert 'id="mobileCommand"' in response.text
    assert 'role="dialog" aria-modal="true" aria-labelledby="inspectorTitle"' in response.text
    script = client.get("/static/v5.js").text
    assert '$("#content").inert=true;$("#sidebar").inert=true' in script
    assert '$("#content").inert=false;$("#sidebar").inert=false' in script
    for settings_group in ("GENERAL", "APPEARANCE", "WORKSPACE", "PRIVACY &amp; DATA", "SECURITY", "SECRETS", "MCP / API", "STORAGE", "BACKUP", "UPDATES"):
        assert f"<small>{settings_group}</small>" in response.text
    assert '<details class="panel settings-advanced">' in response.text
    assert 'id="findingsPager"' in response.text


def test_v5_evolution_controls_are_real_and_require_explicit_confirmation():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5-evolution.js")
    assert script.status_code == 200
    for control in ("evolutionEngagement", "evolutionCampaign", "evolutionGroup",
                    "evolutionContent", "evolutionActionDialog", "evolutionActionCommit"):
        assert f'id="{control}"' in html
    assert "window.createFieldworkEvolution" in script.text
    assert "population_id=" in script.text
    assert 'const api = "/api/v1/evolution"' in script.text
    assert "`${api}/populations" in script.text
    assert "`${api}/transfers" in script.text
    assert '$("#evolutionActionDialog").showModal()' in script.text
    assert "0.810" not in script.text


def test_v5_workspace_is_the_default_home_and_classic_routes_remain_available():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")

    assert 'id="campaign"' in client.get("/").text
    assert 'id="analysisForm"' in client.get("/new").text


def test_v5_findings_show_only_verified_data_and_read_real_detail():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="findingDetail" aria-live="polite"' in html
    assert 'data-finding-open=' in script
    assert '/api/v1/findings/${encodeURIComponent(id)}' in script
    assert '/api/v1/findings/${encodeURIComponent(id)}/lifecycle' in script
    assert 'filter(x=>x.status==="verified")' in script
    assert 'x.status!=="verified"' in script
    assert 'Receipt ID 的存在也不代表当前有效' in html
    assert 'id="findingRetestDialog"' in html
    assert 'id="findingRetestRun"' in html
    assert '/api/v1/findings/${encodeURIComponent(id)}/proof-capsule' in script
    assert '/api/v1/findings/${encodeURIComponent(id)}/retest-plans' in script
    assert 'data-finding-evidence=' in script
    assert 'data-finding-proof=' in script
    assert 'data-finding-retest=' in script


def test_v5_report_export_requires_backend_proof_preflight():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="reportProofState"' in html
    assert 'proof.finding_id!==id||!proof.sha256' in script
    assert '证明包校验未通过' in script
    assert '材料包暂不能导出' in script
    assert '导出时后端仍会重新校验' in script


def test_v5_assets_are_served_and_do_not_embed_mock_metrics():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js")
    stylesheet = client.get("/static/v5.css")

    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert "/api/v1/engagements" in script.text
    assert "/api/v1/task-center" in script.text
    assert "/api/v1/findings" in script.text
    assert "10,000" not in script.text
    assert "function page(" in script.text
    assert "fieldwork-v5-language" in script.text
    assert "aria-current" in script.text
    assert "function openCommandView(id)" in script.text
    assert "Candidate 不会在此页显示" in script.text
    assert "99.9%" not in script.text
    assert "prefers-reduced-motion" in stylesheet.text
    assert ":focus-visible" in stylesheet.text


def test_v5_provider_configuration_uses_backend_secret_contract():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="providerForm"' in html
    assert 'id="providerKey" type="password"' in html
    assert 'id="providerConfirm" type="checkbox" required' in html
    assert 'autocomplete="new-password"' in html
    assert '"/api/v1/traditional/provider"' in script
    assert 'method:"PUT"' in script
    assert 'secret.value=""' in script
    assert "fieldwork-v5-runtime-display-mode" not in script


def test_v5_hypotheses_uses_research_campaign_contract_without_fake_confidence():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    for control in ("hypothesisEngagement", "hypothesisCampaign", "createResearchCampaign", "createHypothesis", "researchCampaignForm", "hypothesisForm"):
        assert f'id="{control}"' in html
    assert "/api/v1/engagements/${encodeURIComponent(id)}/campaigns" in script
    assert "/api/v1/campaigns/${encodeURIComponent(id)}/hypotheses" in script
    assert "优先级不等于成立概率" in script
    assert "Verified 必须由独立复验流程判定" in script


def test_v5_graph_modes_use_persisted_asset_and_research_relationships():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="graphSearch"' in html
    assert 'id="graphCounts"' in html
    assert 'id="graphMode"' in html
    assert '<option value="research">研究关系</option>' in html
    assert '<option value="evidence">证据引用</option>' in html
    assert "/api/v1/engagements/${encodeURIComponent(id)}/asset-graph" in script
    assert "window.loadFieldworkResearchGraph" in script
    graph_script = client.get("/static/v5-research-graph.js").text
    assert "/api/v1/research/campaigns/${encodeURIComponent(id)}/graph/page" in graph_script
    assert "关系仅来自持久化 Observation" in script


def test_v5_monitor_surfaces_use_real_agent_audit_without_promoting_signals():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="sentinelAudit"' in html
    assert 'id="incidentAudit"' in html
    assert "/api/v1/agent-audit/audits" in script
    assert "/api/v1/sentinel/status" not in script
    assert "/api/v1/incidents" not in script
    assert ".filter(x=>!x.demo)" in script
    assert "Candidate 只是调查线索，不是 Incident 或 Verified Finding" in script


def test_v5_verification_detail_reads_attempts_without_claiming_receipt_validity():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="verificationCandidate"' in html
    assert 'id="verificationDetail" aria-live="polite"' in html
    assert "/api/v1/candidates/${encodeURIComponent(id)}" in script
    assert 'x.status==="machine_receipt"' in script
    assert "机器回执记录的存在不证明当前仍有效" in script
    assert "失败、未复现、结论不明均不等于" in script


def test_v5_capability_readiness_is_not_equated_with_installed_executable():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text

    assert 'x.ready===true?"ready"' in script
    assert 'x.available===true?"installed / not ready"' in script
    assert "已安装、可执行、已配置、沙箱就绪与可安全执行是不同状态" in script
    assert 'function renderExtensions()' in script
    assert '"/api/v1/extensions"' not in script
    assert "安装、更新、权限、信任与 drift 不可管理" in script


def test_v5_typography_keeps_dense_workspace_readable():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    css = client.get("/static/v5-workflows.css").text
    html = client.get("/v5").text

    assert "body{font-size:13.5px;line-height:1.55}" in css
    assert ".table-wrap td{font-size:12px}" in css
    assert ".nav-item b{font-size:12.5px" in css
    assert "Priority、Lease 与 Retry 尚待后端接入" in html


def test_v5_language_toggle_translates_shell_and_preserves_preference():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="settingsLanguageToggle"' in html
    assert "function applyLanguage(notify=true)" in script
    assert 'document.documentElement.lang=state.language==="en"?"en":"zh-CN"' in script
    assert "FieldworkStaticLocale?.apply()" in script
    assert "fieldwork:languagechange" in script
    assert "applyLanguage(false);setView(" in script
    locale = client.get("/static/v5-locale.js").text
    assert "NodeFilter.SHOW_TEXT" in locale
    assert "MutationObserver" not in locale  # Never translate dynamically supplied evidence.


def test_v5_report_preview_tracks_source_and_backend_completeness():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="reportQuality" class="report-quality" aria-live="polite"' in html
    assert "function resetReportPreview()" in script
    assert '$("#reportFinding").onchange=resetReportPreview' in script
    assert '$("#reportPlatform").onchange=resetReportPreview' in script
    assert "token!==state.reportToken" in script
    assert "missing_required" in script
    assert "missing_recommended" in script
    assert 'result.status==="draft_incomplete"' in script


def test_v5_overview_distinguishes_unavailable_from_empty_data():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text

    assert "const campaignsOk=state.remote.engagements?.ok,tasksOk=state.remote.tasks?.ok,findingsOk=state.remote.findings?.ok" in script
    assert 'localizedText($("#metricTasks"),()=>tasksOk?active.length:"–")' in script
    assert 'localizedText($("#metricVerified"),()=>findingsOk?verified.length:"–")' in script
    assert 'localizedText($("#attentionCount"),()=>partial?t("部分数据","Partial data")' in script
    assert "无法确认待办总量" in script
    assert 'localizedText($("#trustRatio"),()=>findingsOk?candidates.length:"–")' in script


def test_v5_orchestration_views_keep_agent_and_runner_roles_distinct():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text
    team_script = client.get("/static/v5-team.js").text
    css = client.get("/static/v5-workflows.css").text

    assert 'window.createFieldworkTeam' in team_script
    assert 'table(["Runner","Kind / Labels","Capabilities","Status / Heartbeat","Active / Limit","Updated"],runnerRows)' in script
    assert "逻辑 AgentTask" in team_script
    assert "这里不使用 Run 或 Runner 数量填充 Agent 清单" in team_script
    assert "在线状态和 Heartbeat 仅按后端持久化字段展示" in script
    assert ".cell-subline" in css


def test_v5_global_connection_alarm_excludes_only_planned_404_routes():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text

    assert "error.status=r.status" in script
    assert 'const plannedMissing=new Set([])' in script
    assert "plannedMissing.has(k)&&state.remote[k].error?.status===404" in script
    assert '$("#connectionBanner").hidden=failures.length===0' in script
    assert 'failureLabels[k]' in script


def test_v5_control_plane_views_use_v5_runtime_and_receipt_registries():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text

    assert 'verification:"/api/v1/verification/receipts"' in script
    assert 'runtimeConfig:"/api/v1/runtime/config"' in script
    assert 'runtimeUsage:"/api/v1/runtime/usage"' in script
    assert "function ensureRuntimeRegistry()" in script
    assert "Receipt Registry" in script
    assert "integrity?.current_inputs_match===true" in script
    assert "integrity?.promotion_eligible===true" in script
    assert "仅记录 · 未证明独立执行" in script
    assert "包版本适用性可确认" in script
    graph_script = client.get("/static/v5-research-graph.js").text
    assert 'node.canonical_trust?.current!==true?"stale_verification"' in graph_script
    assert "路由决策不会被当作已执行调用" in script


def test_v5_domain_selector_filters_real_research_data_and_resets_stale_state():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    for domain in ("traditional", "web3", "agent_audit"):
        assert f'<option value="{domain}">' in html
    assert 'const endpointFor=(key,domain)' in script
    assert '`${endpoints[key]}?mode=${encodeURIComponent(domain)}`' in script
    assert 'domain:storedDomain==="web3"?"web3":"traditional"' in script
    assert 'if(token!==state.loadToken||domain!==state.domain)return' in script
    assert 'state.researchLoadToken++;state.graphLoadToken++;state.runLoadToken++;state.verificationLoadToken++' in script
    assert '$("#domainSelect").onchange=e=>selectDomain(e.target.value)' in script
    assert '$("#researchMode").value=state.domain' in script


def test_v5_attention_panel_has_direct_queue_routes_and_natural_height():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    css = client.get("/static/v5-workflows.css").text

    assert 'class="panel attention-panel"' in html
    assert '<footer class="attention-actions"><button data-open-view="tasks">任务队列</button><button data-open-view="verification">复核队列</button></footer>' in html
    assert ".overview-grid{align-items:start}" in css
    assert "@media(min-width:1181px){.overview-grid{grid-template-columns:minmax(0,1.2fr) minmax(380px,.8fr)}}" in css


def test_v5_desktop_sidebar_rail_has_real_layout_and_accessible_navigation():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    script = client.get("/static/v5.js").text
    css = client.get("/static/v5-workflows.css").text

    assert 'function setRail(collapsed)' in script
    assert 'localStorage.setItem("fieldwork-v5-rail",String(collapsed))' in script
    assert 'button.setAttribute("aria-label",labels[button.dataset.view])' in script
    assert 'button.setAttribute("aria-expanded",String(!collapsed))' in script
    assert 'body.rail{--sidebar-width:72px}' in css
    assert 'body.rail .nav-item{grid-template-columns:1fr;text-align:center}' in css


def test_v5_mobile_navigation_can_close_and_return_keyboard_focus():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text
    css = client.get("/static/v5-workflows.css").text

    assert 'id="mobileNavClose"' in html
    assert 'id="mobileNavBackdrop" class="mobile-nav-backdrop" hidden' in html
    assert 'aria-controls="sidebar" aria-expanded="false"' in html
    assert 'function openMobileNavigation()' in script
    assert 'function closeMobileNavigation(restoreFocus=true)' in script
    assert '$("#content").inert=true' in script
    assert '$("#content").inert=false' in script
    assert 'if(e.key==="Tab"&&$("#sidebar").classList.contains("open"))' in script
    assert '.mobile-nav-backdrop:not([hidden])' in css


def test_v5_command_palette_has_listbox_semantics_and_chinese_aliases():
    client = TestClient(application.app, base_url="http://127.0.0.1:8000")
    html = client.get("/v5").text
    script = client.get("/static/v5.js").text

    assert 'id="commandPalette" aria-labelledby="commandPaletteTitle"' in html
    assert 'id="commandInput" role="combobox"' in html
    assert 'id="commandResults" role="listbox"' in html
    assert 'role="option" data-command=' in script
    assert 'const commandAliases=' in script
    assert 'aria-activedescendant' in script
