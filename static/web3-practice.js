(() => {
  const panel = document.createElement('section');
  panel.className = 'truth-panel';
  panel.style.marginTop = '24px';
  panel.innerHTML = '<p class="eyebrow">LOCAL ECONOMIC REGRESSION</p><h2>Web3 本地实战校验</h2><p>账本一致性、提款权限、份额舍入：运行漏洞版与修复版，两轮独立 seed 校验，保留经济断言与可复现源码。</p><button type="button" class="quiet-button" data-practice-run>运行正反对照</button><div data-practice-result role="status" aria-live="polite"></div><small>本地 EVM 夹具 · 无链上交易 · 不生成真实项目漏洞结论</small>';
  document.querySelector('[data-workspace="new"]').append(panel);
  const sync = () => { panel.hidden = state.mode !== 'web3'; };
  const original = setMode;
  setMode = function (mode) { const result = original(mode); sync(); return result; };
  sync();
  const button = panel.querySelector('[data-practice-run]');
  const result = panel.querySelector('[data-practice-result]');
  button.onclick = async () => {
    button.disabled = true;
    result.textContent = '正在本地编译并执行两轮校验…';
    try {
      const report = await api('/api/v1/web3/practice/run', {method:'POST'});
      const measured = (report.rounds[0]?.tests || []).filter(test => test.name.startsWith('test_Inflation'));
      const metrics = measured.map(test => `<p>${test.name.includes('Fixed') ? '修复版' : '漏洞版'}实测：本金 ${esc(test.metrics.attacker_capital_wei || '未记录')} wei · 收益 ${esc(test.metrics.attacker_gain_wei || '未记录')} wei · 用户损失 ${esc(test.metrics.victim_loss_wei || '未记录')} wei</p>`).join('');
      const tested = report.rounds.reduce((n,r) => n + (r.tests?.length || 0), 0);
      result.innerHTML = `<h3>${report.status === 'passed' ? '正反对照全部通过' : '校验未通过'}</h3><p>${report.rounds.length} 轮 · ${tested} 次测试结果。通过表示已知漏洞与修复行为均符合夹具断言。</p>${report.rounds.map((round,i) => `<p>第 ${i+1} 轮：${round.passed ? '通过' : '失败'}${round.error ? ` · ${esc(round.error)}` : ''}${Object.entries(round.checks || {}).filter(([,v])=>!v).map(([k])=>` · ${esc(k)}`).join('')}</p>`).join('')}${metrics}<small>固定样本为本地 EVM 观测值；收益未扣 gas，不代表真实项目获利。</small><a href="/api/v1/web3/practice/results/${encodeURIComponent(report.id)}/download">下载证据与复现源码 ZIP</a>`;
    } catch (error) { result.textContent = `未完成：${error.message}`; }
    finally { button.disabled = false; }
  };
})();
