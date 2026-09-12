(() => {
  const drawer = document.createElement('dialog');
  drawer.className = 'candidate-review-drawer';
  drawer.setAttribute('aria-labelledby', 'candidateReviewTitle');
  drawer.innerHTML = `<header><div><p class="eyebrow">CANDIDATE REVIEW</p><h2 id="candidateReviewTitle">候选审阅</h2></div><button type="button" class="close-button" aria-label="关闭候选详情">×</button></header><div id="candidateReviewBody" aria-live="polite"></div>`;
  document.body.append(drawer);
  const content = drawer.querySelector('#candidateReviewBody');
  drawer.querySelector('.close-button').onclick = () => drawer.close();
  let request = 0;
  drawer.addEventListener('close', () => { request += 1; });

  window.openCandidateDetail = async id => {
    const current = ++request;
    content.innerHTML = '<div class="empty-state">正在读取候选证据…</div>';
    if (!drawer.open) drawer.showModal();
    try {
      const detail = await api(`/api/v1/candidates/${encodeURIComponent(id)}`);
      if (current !== request || !drawer.open) return;
      const c = detail.candidate;
      const next = detail.next_action;
      content.innerHTML = `<section class="candidate-review-intro"><span class="state-badge">${esc(detail.verification_level)}</span><h3>${esc(c.title)}</h3><p>${esc(c.target)}</p><p>${esc(c.hypothesis)}</p>${detail.needs_triage ? '<p class="candidate-review-note">这是一条旧版宽泛候选。请先分辨普通信息观察和具体安全问题。</p>' : ''}</section>
        ${next ? `<section class="candidate-next-panel"><p class="eyebrow">当前下一步</p><h3>${esc(next.label)}</h3><p>${esc(next.reason)}</p><h4>需要准备</h4><ul>${next.materials.map(item=>`<li>${esc(item)}</li>`).join('')}</ul>${['http_workbench','web3_property'].includes(next.method) && next.stage !== 'needs_evidence' ? '<button type="button" class="primary-action compact" data-go-verification>前往复验配置</button>' : next.stage === 'triage' ? '<button type="button" class="quiet-button" data-go-triage>填写分诊依据</button>' : ''}</section>` : ''}
        <section><h3>处理流程</h3><ol>${detail.next_steps.map(step => `<li>${esc(step)}</li>`).join('')}</ol><p class="candidate-review-note">${esc(detail.save_behavior)}</p></section>
        <section class="candidate-triage"><h3>先做分诊</h3><p>分诊不会生成已验证结论。普通观察和重复项会离开当前候选列表，但保留在历史排除记录中。</p><label>判断依据<textarea id="candidateTriageReason" placeholder="说明它违反了什么安全边界，或为什么只是普通信息" minlength="3"></textarea></label><label>重复于<select id="candidateDuplicateOf"><option value="">选择另一条候选</option>${(detail.peer_candidates||[]).map(item=>`<option value="${esc(item.id)}">${esc(item.title)} · ${esc(item.target)}</option>`).join('')}</select></label><div class="triage-actions"><button type="button" class="quiet-button" data-triage="security_hypothesis">安全假设</button><button type="button" class="quiet-button" data-triage="needs_evidence">缺少证据</button><button type="button" class="quiet-button" data-triage="not_security">普通观察</button><button type="button" class="text-button" data-triage="duplicate">重复项</button></div><p id="candidateTriageError" class="inline-error" role="alert"></p></section>
        <section><h3>来源证据 <small>${detail.evidence.length} 项</small></h3>${detail.evidence.length ? detail.evidence.map(e => `<article class="candidate-evidence"><div><strong>${esc(e.source_capability || e.evidence_type)}</strong><span class="state-badge">${esc(e.polarity)}</span></div><p>${esc(e.observation_summary || e.summary)}</p><small>${esc(e.subject || '')}</small><small>Observation · ${esc(e.observation_id || '未关联')}<br>Artifact · ${esc(e.artifact_id || '仅有观察摘要')}</small></article>`).join('') : '<p>尚无可读取的证据，先补齐来源再验证。</p>'}</section>
        <section><h3>验证记录</h3>${detail.attempts.length ? detail.attempts.map(a => `<article class="candidate-evidence"><strong>${esc(a.oracle)}</strong><p>${esc(a.status)} · ${a.attempts} 轮 · ${esc(a.completed_at || a.started_at)}</p></article>`).join('') : '<p>这条候选还没有独立验证记录。</p>'}</section>
        <section id="candidateHttpVerification"></section><div id="candidateWeb3Verification"></div><div class="candidate-review-actions"><button class="quiet-button" id="candidateReviewBack">返回结果</button>${detail.available_method === 'http_workbench' ? '<button class="primary-action compact" id="candidateReviewWorkbench">打开 HTTP 工作台</button>' : ['web3_property','web3_finalize'].includes(detail.available_method) ? `<button class="${detail.available_method==='web3_property'?'primary-action compact':'quiet-button'}" id="candidateReviewProperty">${detail.available_method==='web3_property'?'两轮属性复测':'再次属性复测'}</button>` : '<span>此类问题仍需配置专用验证方法。</span>'}</div><p id="candidateReviewError" class="inline-error" role="alert"></p>`;
      if (detail.available_method === 'http_workbench') window.mountHttpVerification?.(content.querySelector('#candidateHttpVerification'), c, detail.verification_jobs || []);
      if (detail.available_method === 'web3_finalize') window.mountWeb3Verification?.(content.querySelector('#candidateWeb3Verification'), detail);
      content.querySelectorAll('[data-triage]').forEach(button => button.onclick = async () => {
        const reason = content.querySelector('#candidateTriageReason').value.trim();
        const duplicateOf = content.querySelector('#candidateDuplicateOf').value;
        const errorBox = content.querySelector('#candidateTriageError');
        if (reason.length < 3) {errorBox.textContent='请先填写判断依据。';return;}
        if (button.dataset.triage === 'duplicate' && !duplicateOf) {errorBox.textContent='请选择重复的原始候选。';return;}
        errorBox.textContent=''; button.disabled=true;
        try {
          const result = await api(`/api/v1/candidates/${encodeURIComponent(id)}/triage`,{method:'POST',body:JSON.stringify({disposition:button.dataset.triage,reason,duplicate_of:duplicateOf||null})});
          toast(result.status==='graveyard'?'已移出待验证列表并保留历史':'候选分诊已更新');
          drawer.close();
          if(state.activeRun?.id===c.run_id) await selectRun(c.run_id);
          if(result.status!=='graveyard') await window.openCandidateDetail(id);
        } catch(error) {errorBox.textContent=error.message;button.disabled=false;}
      });
      content.querySelector('[data-go-verification]')?.addEventListener('click', () => {
        const section = detail.available_method === 'http_workbench' ? content.querySelector('#candidateHttpVerification') : detail.available_method === 'web3_finalize' ? content.querySelector('#candidateWeb3Verification') : content.querySelector('#candidateReviewProperty');
        const disclosure = section?.querySelector('details');
        if (disclosure) disclosure.open = true;
        section?.scrollIntoView({block:'center', behavior:'smooth'});
      });
      content.querySelector('[data-go-triage]')?.addEventListener('click', () => content.querySelector('#candidateTriageReason').focus());
      content.querySelector('#candidateReviewBack').onclick = () => drawer.close();
      const workbench = content.querySelector('#candidateReviewWorkbench');
      if (workbench) workbench.onclick = async () => {
        workbench.disabled = true;
        try {
          await selectRun(c.run_id);
          drawer.close(); go('run');
          if (/^https?:\/\//i.test(c.target)) $('#httpUrl').value = c.target;
          $('#httpWorkbench').scrollIntoView({block:'start'});
        } catch (error) {
          content.querySelector('#candidateReviewError').textContent = error.message;
          workbench.disabled = false;
        }
      };
      const property = content.querySelector('#candidateReviewProperty');
      if (property) property.onclick = async () => {
        drawer.close();
        await replayWeb3Property(id);
        await window.openCandidateDetail(id);
      };
    } catch (error) {
      if (current !== request) return;
      content.innerHTML = `<p class="inline-error">读取失败：${esc(error.message)}</p><button class="quiet-button" id="candidateReviewRetry">重新读取</button>`;
      content.querySelector('#candidateReviewRetry').onclick = () => window.openCandidateDetail(id);
    }
  };
})();
