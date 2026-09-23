/* Compiler graph is a research aid, not verification evidence. */
(() => {
  const base = loadWeb3Discovery;
  loadWeb3Discovery = async function () {
    const runId = state.activeRun?.id;
    await base();
    if (!runId || state.activeRun?.id !== runId || state.activeRun?.mode !== 'web3') return;
    const panel = document.querySelector('#web3DiscoveryContent');
    try {
      const data = await api(`/api/v1/web3/runs/${encodeURIComponent(runId)}/discovery`);
      if (state.activeRun?.id !== runId || state.mode !== 'web3' || !data.ready) return;
      panel.querySelector('[data-compiler-analysis]')?.remove();
      const analysis = data.compiler_analysis || {status: 'unavailable', units: []};
      const section = document.createElement('section');
      section.className = 'web3-semantic-paths';
      section.dataset.compilerAnalysis = 'true';
      const units = analysis.units || [];
      const entries = units.flatMap(unit => unit.entrypoints || []);
      entries.sort((a,b) => Number(b.requires_interaction_review) - Number(a.requires_interaction_review));
      section.innerHTML = `<div><p class="eyebrow">COMPILER CALL GRAPH</p><h3>编译器调用分析</h3><p>${analysis.status === 'ready' ? `${units.length} 个源码一致的编译单元 · ${entries.length} 个声明入口` : '尚无匹配当前源码的编译器分析；重新运行源码分析后生成。'}</p></div><div>${entries.slice(0,100).map(entry => `<article><span>${entry.requires_interaction_review ? '优先复核' : '调用路径'}</span><div><b>${esc(entry.label)}</b><p>状态写入：${esc(entry.writes.join(' · ') || '未解析到')}</p><p>内部函数 / 修饰器：${esc(entry.reachable_functions.join(' → ') || '无')}</p><p>外部调用声明：${esc(entry.external_calls.join(' · ') || '无')}</p>${entry.unresolved.length ? `<small>仍需解析：${esc(entry.unresolved.map(item => `${item.target} (${item.reason})`).join(' · '))}</small>` : ''}</div></article>`).join('')}</div><p class="web3-discovery-boundary">${esc(analysis.boundary || '当前记录没有编译器调用图。')}${entries.length > 100 ? ' 本页显示前 100 个入口，完整图保存在源码证据中。' : ''}</p>`;
      panel.append(section);
    } catch (error) {
      if (state.activeRun?.id === runId) {
        const message = document.createElement('p');
        message.textContent = `编译器调用分析不可用：${error.message}`;
        panel.append(message);
      }
    }
  };
})();
