(() => {
  const filters = [['all','全部'],['supported','有复验入口'],['impact','补影响与规则'],['needs_evidence','缺少证据'],['triage','待分诊'],['manual','专用验证']];
  let selected = 'all', query = '';
  const list = document.querySelector('#candidateFindings');
  const toolbar = document.createElement('section');
  toolbar.className = 'candidate-queue-toolbar';
  toolbar.setAttribute('aria-label','候选处理队列');
  toolbar.innerHTML = `<div class="candidate-queue-heading"><div><h3>从线索走到可验证的问题</h3><p>先分诊，再复验。分类只是处理建议，不会自动更改记录。</p></div><label class="candidate-search">搜索候选<input type="search" placeholder="标题、目标或分类" aria-label="搜索候选"></label></div><div class="candidate-queue-filters" role="group" aria-label="按下一步筛选">${filters.map(([id,label])=>`<button type="button" data-filter="${id}" aria-pressed="${id==='all'}"><span>${label}</span><b>0</b></button>`).join('')}</div><p class="candidate-filter-summary" role="status"></p>`;
  list.before(toolbar);
  window.filterCandidateQueue = candidates => candidates.filter(item => (selected === 'all' || item.next_action?.stage === selected) && (!query || [item.title,item.target,item.category,item.hypothesis].join(' ').toLowerCase().includes(query)));
  window.renderCandidateQueue = candidates => {
    toolbar.querySelectorAll('[data-filter]').forEach(button => {
      const key = button.dataset.filter;
      button.setAttribute('aria-pressed',String(selected === key));
      button.querySelector('b').textContent = key === 'all' ? candidates.length : candidates.filter(c => c.next_action?.stage === key).length;
    });
    toolbar.querySelector('.candidate-filter-summary').textContent = `显示 ${window.filterCandidateQueue(candidates).length} / ${candidates.length} 条 · ${state.activeRun ? '仅当前运行' : '当前工作域'}`;
  };
  toolbar.querySelector('input').oninput = event => {query = event.target.value.trim().toLowerCase(); renderFindings();};
  toolbar.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => {selected = button.dataset.filter; renderFindings();});
  renderFindings();
})();
