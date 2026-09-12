(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'onboardingDialog';
  dialog.className = 'onboarding-dialog';
  dialog.innerHTML = `<div class="onboarding-shell"><aside><span class="brand-mark">F</span><p>FIRST RUN</p><h2>启动前，先确认环境真实可用。</h2><ol><li>本机运行时</li><li>模型与浏览器</li><li>Traditional 工具链</li><li>Web3 工具链</li><li>数据与恢复</li></ol></aside><main><div class="onboarding-head"><div><p class="eyebrow">ENVIRONMENT PROOF</p><h2>Fieldwork 启动环境检查</h2><p>逐项验证工具、模型、浏览器和本地数据。所有自检都在本机完成，不会访问外部测试目标。</p></div><span id="onboardingVersion"></span></div><div id="onboardingChecks" class="onboarding-checks"><div class="onboarding-loading">正在检查本机运行环境…</div></div><section id="onboardingVerdict" class="onboarding-verdict"></section><p id="onboardingRepairMessage" class="onboarding-repair-message" role="status"></p><div class="onboarding-actions"><button id="onboardingSettings" class="quiet-button">查看设置</button><button id="onboardingRepair" class="quiet-button">一键修复并重新检查</button><button id="onboardingRetest" class="quiet-button">运行本机自检</button><button id="onboardingContinue" class="primary-action compact" disabled><span>进入工作台</span><b>→</b></button></div></main></div>`;
  document.body.append(dialog);

  const completedKey = 'fieldwork-onboarding-0.31.1';
  const shouldOpen = !localStorage.getItem(completedKey) || new URLSearchParams(location.search).get('onboarding') === '1';
  const mark = ok => `<i class="onboarding-mark ${ok ? 'ok' : 'bad'}">${ok ? '✓' : '!'}</i>`;

  function render(result) {
    $('#onboardingVersion').textContent = `v${result.version.app_version} · Schema ${result.version.schema_version}`;
    $('#onboardingChecks').innerHTML = result.checks.map(item => `<article>${mark(item.ok)}<div><b>${esc(item.label)}</b><p>${esc(item.detail)}</p></div><span>${item.ok ? 'READY' : 'BLOCKED'}</span></article>`).join('');
    const verdict = $('#onboardingVerdict');
    verdict.className = `onboarding-verdict ${result.ready ? 'ready' : 'blocked'}`;
    verdict.innerHTML = result.ready ? `<small>FINAL CHECK</small><h3>可以开始真实任务</h3><p>${result.self_tested ? 'Traditional 与 Web3 本地测试项目均已真实运行通过。' : '核心环境已就绪；建议再运行一次测试项目自检。'}</p>` : `<small>需要处理 ${result.blockers.length} 项</small><h3>暂不建议启动真实任务</h3><p>${result.blockers.map(item => esc(item.label)).join('、')}</p>`;
    $('#onboardingContinue').disabled = !result.ready;
    $('#onboardingRepair').hidden = result.ready;
  }

  async function load(runTests = false) {
    $('#onboardingRetest').disabled = true;
    $('#onboardingChecks').setAttribute('aria-busy', 'true');
    try { render(await api(runTests ? '/api/v1/onboarding/self-test' : '/api/v1/onboarding/status', {method: runTests ? 'POST' : 'GET'})); }
    catch (error) { $('#onboardingChecks').innerHTML = `<div class="onboarding-loading">诊断服务失败：${esc(error.message)}</div>`; }
    finally { $('#onboardingRetest').disabled = false; $('#onboardingChecks').removeAttribute('aria-busy'); }
  }

  $('#onboardingRetest').onclick = () => load(true);
  $('#onboardingRepair').onclick = async () => {
    const button=$('#onboardingRepair'),message=$('#onboardingRepairMessage');
    button.disabled=true;message.textContent='正在刷新工具路径与运行时证据…';
    try { const result=await api('/api/v1/onboarding/repair',{method:'POST'});render(result);message.textContent=result.ready?'修复完成：所有必需项已就绪，现在可以进入 Fieldwork。':`修复后仍有 ${result.blockers.length} 项需处理，请打开设置查看具体条件。`; }
    catch(error){message.textContent=`修复失败：${error.message}`;} finally {button.disabled=false;}
  };
  $('#onboardingSettings').onclick = () => { dialog.close(); go('settings'); };
  $('#onboardingContinue').onclick = () => { localStorage.setItem(completedKey, new Date().toISOString()); dialog.close(); };
  if (shouldOpen) { dialog.showModal(); load(false); }
})();
