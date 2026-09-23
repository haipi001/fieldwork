(() => {
  const lines = value => value.split('\n').map(item=>item.trim()).filter(Boolean);
  const listDelta = (label, value) => {
    if (!value) return '';
    const parts = [];
    if (value.added && (Array.isArray(value.added) ? value.added.length : Object.keys(value.added).length)) parts.push(`${label}新增：${Array.isArray(value.added) ? value.added.join('、') : Object.entries(value.added).map(([key,severity])=>`${key}(${severity})`).join('、')}`);
    if (value.removed && (Array.isArray(value.removed) ? value.removed.length : Object.keys(value.removed).length)) parts.push(`${label}移除：${Array.isArray(value.removed) ? value.removed.join('、') : Object.keys(value.removed).join('、')}`);
    return parts.map(item=>`<li>${esc(item)}</li>`).join('');
  };
  const renderDiff = diff => {
    if (!diff?.has_changes) return '<li>与上一版本的安全约束一致。</li>';
    const changes = [
      diff.rules_text_changed ? '<li>规则正文已改变</li>' : '',
      listDelta('范围资产', diff.scope_assets), listDelta('影响类别', diff.impact_categories),
      ...(diff.impact_categories?.changed || []).map(item=>`<li>${esc(`影响级别：${item.category} ${item.from} → ${item.to}`)}</li>`),
      listDelta('已知问题来源', diff.known_issue_sources), listDelta('审计来源', diff.previous_audit_sources),
      diff.poc_policy ? `<li>${esc(`PoC 政策：${diff.poc_policy.from} → ${diff.poc_policy.to}`)}</li>` : '',
      diff.valid_until ? `<li>${esc(`有效期：${diff.valid_until.from} → ${diff.valid_until.to}`)}</li>` : '',
      diff.source_uri ? '<li>规则来源已改变</li>' : '',
    ].filter(Boolean).join('');
    return changes || '<li>记录到规则元数据变化。</li>';
  };

  window.mountWeb3Verification = (host, detail) => {
    const candidate = detail.candidate;
    const programs = detail.program_rules || [];
    const eligiblePrograms = programs.filter(item=>item.is_latest_program_rules && item.authorization_status==='confirmed');
    const pending = programs.find(item=>item.is_latest_program_rules && item.authorization_status==='pending_reauthorization');
    const defaultDate = new Date(Date.now() + 30 * 86400000).toISOString().slice(0,10);
    const ruleHistory = programs.length ? `<div class="rule-version-list">${programs.map(item=>`
      <div class="candidate-review-note"><strong>v${item.version} · ${esc(item.platform)}</strong>
      <span class="status-pill ${item.authorization_status==='confirmed' ? 'success' : 'warning'}">${item.authorization_status==='confirmed' ? '已授权' : '待重新授权'}</span>
      <small>${item.is_latest_program_rules ? '当前版本' : '历史版本'} · 有效至 ${esc(item.valid_until || '未记录')}</small></div>`).join('')}</div>` : '';
    const authorizationPanel = pending ? `<section class="candidate-review-note rule-change-review">
      <strong>v${pending.version} 规则有变化，正式验证已暂停</strong>
      <p>审阅下面的差异；确认后只有这个最新版本可用于生成正式结果。</p>
      <ul>${renderDiff(pending.diff)}</ul>
      <form data-authorize data-snapshot="${esc(pending.id)}">
        <label>授权说明<textarea name="note" minlength="3" required placeholder="记录已核对的规则来源、范围与影响变化"></textarea></label>
        <button type="submit" class="primary-action compact">确认新规则授权</button><p data-message class="inline-error" role="status"></p>
      </form>
    </section>` : '';
    const finalizePanel = eligiblePrograms.length ? `<p>严重性由最新规则内的影响类别映射，不能在候选上自行提高。</p>
      <form data-finalize>
        <label>项目规则版本<select name="program">${eligiblePrograms.map(item=>`<option value="${esc(item.id)}">v${item.version} · ${esc(item.platform)} · 有效至 ${esc(item.valid_until)}</option>`).join('')}</select></label>
        <label>规则内影响类别<select name="impactCategory"></select></label>
        <label>可复核的影响说明<textarea name="impact" minlength="10" required placeholder="说明属性违反会影响的状态、资产或用户；不要填写尚未证明的金额"></textarea></label>
        <label>根因<textarea name="rootCause" minlength="3" required placeholder="定位错误的状态转换或约束"></textarea></label>
        <div class="verification-fields"><label>弱点分类<input name="weakness" required value="CWE-682"></label><label>代码位置<input name="location" required placeholder="src/Vault.sol:withdraw"></label></div>
        <label>利用可行性<input name="feasibility" placeholder="所需调用条件与限制"></label><label>已证明的风险金额<input name="funds" placeholder="未证明时留空"></label>
        <button type="submit" class="primary-action compact">绑定规则并生成正式结果</button><p data-message class="inline-error" role="status"></p>
      </form>` : `<div class="candidate-review-note">${programs.length ? '最新规则尚未重新授权，完成上方差异审阅后才能生成正式结果。' : '尚无项目规则快照。先导入并审阅规则正文、范围、影响分类和排除来源。'}</div>`;
    const importPanel = `<details class="rule-import" ${programs.length ? '' : 'open'}><summary>${programs.length ? '导入新规则版本' : '导入首个规则版本'}</summary>
      <form data-import>
        <label>规则来源<input name="source" required placeholder="项目规则页面或本地文件说明"></label>
        <label>规则正文<textarea name="rules" minlength="20" required placeholder="粘贴本次审阅所依据的项目规则正文"></textarea></label>
        <label>有效期<input name="validUntil" type="date" required value="${defaultDate}"></label>
        <label>影响类别与严重性（JSON）<textarea name="impacts" required spellcheck="false" placeholder='{"loss_of_funds":"critical","temporary_freeze":"high"}'></textarea></label>
        <label>已知问题核查来源（每行一个）<textarea name="known" required placeholder="规则页的 Known issues 区段或独立来源"></textarea></label>
        <label>历史审计核查来源（每行一个）<textarea name="audits" required placeholder="审计报告链接或本地文件说明"></textarea></label>
        <label>PoC 政策<select name="poc"><option value="allowed">明确允许</option><option value="restricted">受限制</option><option value="forbidden">禁止</option></select></label>
        <button type="submit" class="quiet-button">保存不可变规则快照</button><p data-message class="inline-error" role="status"></p>
      </form></details>`;
    host.innerHTML = `<section class="web3-verification"><h3>规则与影响资格</h3>${ruleHistory}${authorizationPanel}${finalizePanel}${importPanel}</section>`;

    const authorizeForm = host.querySelector('[data-authorize]');
    if (authorizeForm) authorizeForm.onsubmit = async event => {
      event.preventDefault();
      const message=authorizeForm.querySelector('[data-message]'),button=authorizeForm.querySelector('button');
      message.textContent='正在记录授权…';button.disabled=true;
      try {
        const form=new FormData(authorizeForm);
        await api(`/api/v1/program-snapshots/${authorizeForm.dataset.snapshot}/confirm-authorization`,{method:'POST',body:JSON.stringify({confirmation:'CONFIRM_RULE_CHANGE',note:form.get('note').trim()})});
        toast('新规则差异已审阅并授权'); await window.openCandidateDetail(candidate.id);
      } catch(error) {message.textContent=error.message;button.disabled=false;}
    };

    const importForm = host.querySelector('[data-import]');
    importForm.onsubmit = async event => {
      event.preventDefault();
      const message = importForm.querySelector('[data-message]'), button = importForm.querySelector('button');
      message.textContent=''; button.disabled=true;
      try {
        const form = new FormData(importForm), impacts = JSON.parse(form.get('impacts'));
        const result = await api('/api/v1/program-rules/import',{method:'POST',body:JSON.stringify({engagement_id:candidate.engagement_id,platform:'immunefi',source_uri:form.get('source').trim(),rules_text:form.get('rules'),valid_until:`${form.get('validUntil')}T23:59:59Z`,scope_assets:[detail.engagement_target],impact_categories:impacts,known_issue_sources:lines(form.get('known')),previous_audit_sources:lines(form.get('audits')),poc_policy:form.get('poc')})});
        toast(result.authorization_status==='pending_reauthorization' ? `项目规则 v${result.version} 已保存，请审阅差异` : `项目规则 v${result.version} 已保存并确认`);
        await window.openCandidateDetail(candidate.id);
      } catch(error) {message.textContent=error.message;button.disabled=false;}
    };

    const finalizeForm = host.querySelector('[data-finalize]');
    if (!finalizeForm) return;
    const programSelect = finalizeForm.elements.namedItem('program'), impactSelect = finalizeForm.elements.namedItem('impactCategory');
    const refreshImpacts = () => {
      const selected = eligiblePrograms.find(item=>item.id===programSelect.value);
      impactSelect.innerHTML = Object.entries(selected?.impact_categories||{}).map(([key,severity])=>`<option value="${esc(key)}">${esc(key)} · ${esc(severity)}</option>`).join('');
    };
    programSelect.onchange=refreshImpacts; refreshImpacts();
    finalizeForm.onsubmit = async event => {
      event.preventDefault();
      const message=finalizeForm.querySelector('[data-message]'),button=finalizeForm.querySelector('button');
      message.textContent='正在核对机器证据、规则版本和影响类别…';button.disabled=true;
      try {
        const form=new FormData(finalizeForm);
        await api(`/api/v1/web3/candidates/${candidate.id}/finalize-property`,{method:'POST',body:JSON.stringify({program_snapshot_id:form.get('program'),impact_category:form.get('impactCategory'),impact_description:form.get('impact').trim(),root_cause:form.get('rootCause').trim(),weakness:form.get('weakness').trim(),location:form.get('location').trim(),feasibility:form.get('feasibility').trim()||null,funds_at_risk:form.get('funds').trim()||null})});
        toast('正式结果已生成；经济影响仍按证据边界显示'); host.closest('dialog')?.close();
        if(state.activeRun?.id===candidate.run_id) await selectRun(candidate.run_id); go('reports');
      } catch(error) {message.textContent=error.message;button.disabled=false;}
    };
  };
})();
