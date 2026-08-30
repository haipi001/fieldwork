(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'onboardingDialog';
  dialog.className = 'onboarding-dialog';
  dialog.innerHTML = `<div class="onboarding-shell"><aside><span class="brand-mark">F</span><p>FIRST RUN</p><h2>启动前，先证明环境真实可用。</h2><ol><li>运行时</li><li>模型与浏览器</li><li>Traditional</li><li>Web3</li><li>本地恢复</li></ol></aside><main><div class="onboarding-head"><div><p class="eyebrow">ENVIRONMENT PROOF</p><h2>Fieldwork 首次启动诊断</h2><p>系统只根据真实探测给出结论。测试项目不会访问外部目标。</p></div><span id="onboardingVersion"></span></div><div id="onboardingChecks" class="onboarding-checks"><div class="onboarding-loading">正在检查本机运行环境…</div></div><section id="onboardingVerdict" class="onboarding-verdict"></section><div class="onboarding-actions"><button id="onboardingSettings" class="quiet-button">打开设置</button><button id="onboardingRetest" class="quiet-button">运行测试项目自检</button><button id="onboardingContinue" class="primary-action compact" disabled><span>进入 Fieldwork</span><b>→</b></button></div></main></div>`;
  document.body.append(dialog);

  const completedKey = 'fieldwork-onboarding-0.13.0';
  const shouldOpen = !localStorage.getItem(completedKey) || new URLSearchParams(location.search).get('onboarding') === '1';
  const mark = ok => `<i class="onboarding-mark ${ok ? 'ok' : 'bad'}">${ok ? '✓' : '!'}</i>`;

  function render(result) {
    $('#onboardingVersion').textContent = `v${result.version.app_version} · Schema ${result.version.schema_version}`;
    $('#onboardingChecks').innerHTML = result.checks.map(item => `<article>${mark(item.ok)}<div><b>${esc(item.label)}</b><p>${esc(item.detail)}</p></div><span>${item.ok ? 'READY' : 'BLOCKED'}</span></article>`).join('');
    const verdict = $('#onboardingVerdict');
    verdict.className = `onboarding-verdict ${result.ready ? 'ready' : 'blocked'}`;
    verdict.innerHTML = result.ready ? `<small>FINAL CHECK</small><h3>可以开始真实任务</h3><p>${result.self_tested ? 'Traditional 与 Web3 本地测试项目均已真实运行通过。' : '核心环境已就绪；建议再运行一次测试项目自检。'}</p>` : `<small>需要处理 ${result.blockers.length} 项</small><h3>暂不建议启动真实任务</h3><p>${result.blockers.map(item => esc(item.label)).join('、')}</p>`;
    $('#onboardingContinue').disabled = !result.ready;
  }

  async function load(runTests = false) {
    $('#onboardingRetest').disabled = true;
    $('#onboardingChecks').setAttribute('aria-busy', 'true');
    try { render(await api(runTests ? '/api/v1/onboarding/self-test' : '/api/v1/onboarding/status', {method: runTests ? 'POST' : 'GET'})); }
    catch (error) { $('#onboardingChecks').innerHTML = `<div class="onboarding-loading">诊断服务失败：${esc(error.message)}</div>`; }
    finally { $('#onboardingRetest').disabled = false; $('#onboardingChecks').removeAttribute('aria-busy'); }
  }

  $('#onboardingRetest').onclick = () => load(true);
  $('#onboardingSettings').onclick = () => { dialog.close(); go('settings'); };
  $('#onboardingContinue').onclick = () => { localStorage.setItem(completedKey, new Date().toISOString()); dialog.close(); };
  if (shouldOpen) { dialog.showModal(); load(false); }
})();
