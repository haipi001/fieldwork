(() => {
  const roles = {baseline:'所有者读取',attack:'另一身份读取',negative_control:'未登录负对照',baseline_identity:'所有者身份检查',attack_identity:'另一身份检查'};
  window.mountHttpVerification = (host, candidate, existingJobs = []) => {
    if (!/^https?:\/\//i.test(candidate.target)) return;
    host.innerHTML = `<details class="http-verification"><summary>配置对象读取复验</summary><p>适用于 JSON API 的对象归属检查。准备两个已登录身份、身份查询接口（如 /me），以及响应中表示用户和对象所有者的字段。</p><form autocomplete="off">
      <label>对象 URL<input name="target" type="url" required value="${esc(candidate.target)}" readonly></label>
      <label>身份查询 URL<input name="identity" type="url" required placeholder="https://目标域名/api/me"></label>
      <label>所有者请求头（JSON）<textarea name="ownerHeaders" required spellcheck="false" placeholder='{"Authorization":"Bearer …"}'></textarea></label>
      <label>另一身份请求头（JSON）<textarea name="otherHeaders" required spellcheck="false" placeholder='{"Authorization":"Bearer …"}'></textarea></label>
      <div class="verification-fields"><label>身份响应的用户字段<input name="principal" required placeholder="id"></label><label>对象响应的所有者字段<input name="owner" required placeholder="owner_id"></label></div>
      <p class="candidate-review-note">字段支持点分路径，例如 data.owner.id。凭据只用于本次请求，不保存为计划；关闭前请清除输入。</p>
      <label>影响说明<textarea name="impact" required minlength="3" placeholder="另一身份能读取哪些不属于它的数据"></textarea></label>
      <label>根因判断<input name="cause" required minlength="3" placeholder="缺少对象所有权校验"></label>
      <div class="verification-fields"><label>严重性<select name="severity"><option value="medium">中</option><option value="high">高</option><option value="low">低</option></select></label><label>弱点分类<input name="weakness" required value="CWE-639"></label></div>
      <button class="quiet-button" type="submit">预览验证计划</button><button class="quiet-button" type="button" data-clear>清除凭据</button>
      </form><div data-plan aria-live="polite"></div><p data-error class="inline-error" role="alert"></p></details>`;
    const form = host.querySelector('form'), plan = host.querySelector('[data-plan]'), error = host.querySelector('[data-error]');
    let prepared = null, active = true, revision = 0;
    const field = name => form.elements.namedItem(name);
    const clear = () => {field('ownerHeaders').value = ''; field('otherHeaders').value = ''; prepared = null; plan.innerHTML = '';};
    host.querySelector('[data-clear]').onclick = clear;
    // Closing the parent dialog clears plaintext credentials and retained request data.
    host.closest('dialog')?.addEventListener('close', () => {active=false;clear();}, {once:true});
    form.addEventListener('input', () => {revision++;prepared = null; plan.innerHTML = '';});
    function headers(name) {
      const value = JSON.parse(field(name).value);
      if (!value || Array.isArray(value) || typeof value !== 'object' || Object.values(value).some(v => typeof v !== 'string')) throw new Error('请求头必须为字符串键值组成的 JSON 对象');
      return value;
    }
    const labels = {queued:'等待执行',running:'正在执行',cancelling:'正在取消',completed:'复验完成',failed:'执行失败',cancelled:'已取消',interrupted:'进程重启中断'};
    function enableForm() {[...form.elements].forEach(element => element.disabled = false);}
    function renderResult(job) {
      if (!active) return;
      if (['queued','running','cancelling'].includes(job.status)) {
        const percent = Math.round((job.progress || 0) * 100);
        plan.innerHTML = `<article class="candidate-evidence verification-progress"><div><h3>${esc(labels[job.status] || job.status)}</h3><span>${percent}%</span></div><progress max="1" value="${job.progress || 0}">${percent}%</progress><p>${esc(job.phase)}</p><small>${job.completed_requests}/${job.total_requests} 个请求已完成 · ${esc(job.id)}</small><button class="quiet-button" type="button" data-cancel ${job.status==='cancelling'?'disabled':''}>取消复验</button></article>`;
        plan.querySelector('[data-cancel]').onclick = async () => {
          try {await api(`/api/v1/verification-jobs/${job.id}/cancel`,{method:'POST'});} catch(e) {error.textContent=e.message;}
        };
        return;
      }
      enableForm();
      const result = job.result;
      if (job.status !== 'completed' || !result) {
        plan.innerHTML = `<article class="candidate-evidence"><h3>${esc(labels[job.status] || job.status)}</h3><p>${esc(job.error || job.phase || '任务没有产生验证结果')}</p><small>${esc(job.id)}</small></article>`;
        return;
      }
      const verified = result.verification.status === 'verified';
      plan.innerHTML = `<article class="candidate-evidence"><h3>${verified?'已通过两轮读取边界复验':'未建立漏洞证明'}</h3><p>${verified?'机器收据已生成，可继续审阅影响与报告。':'候选保留供复核。查看未通过的断言，调整身份、字段或假设。'}</p>${(result.replay.semantic_checks||[]).map((round,i)=>`<p>第 ${i+1} 轮：${round.passed?'通过':'未通过'}<br><small>${Object.entries(round.checks).filter(([,v])=>!v).map(([k])=>esc(k)).join(' · ')}</small></p>`).join('')}<small>证据：${esc(result.artifact_id)} · 任务：${esc(job.id)}</small></article>`;
      if(state.activeRun?.id===candidate.run_id) selectRun(candidate.run_id).catch(()=>{});
    }
    async function monitor(jobId) {
      if (!active) return;
      try {
        const job = await api(`/api/v1/verification-jobs/${jobId}`);
        renderResult(job);
        if (active && ['queued','running','cancelling'].includes(job.status)) setTimeout(()=>monitor(jobId),700);
      } catch(e) {if(active) error.textContent=e.message;}
    }
    form.onsubmit = async event => {
      event.preventDefault(); const current=revision; error.textContent = ''; prepared = null;
      const button = form.querySelector('[type=submit]'); button.disabled = true;
      try {
        const owner = headers('ownerHeaders'), other = headers('otherHeaders'), target = field('target').value;
        const payload = {candidate_id:candidate.id,baseline:{url:target,headers:owner},attack:{url:target,headers:other},negative_control:{url:target},
          authorization:{baseline_identity:{url:field('identity').value,headers:owner},attack_identity:{url:field('identity').value,headers:other},principal_field:field('principal').value.trim(),owner_field:field('owner').value.trim()},
          severity:field('severity').value,impact_description:field('impact').value.trim(),root_cause:field('cause').value.trim(),weakness:field('weakness').value.trim(),location:target};
        const preview = await api(`/api/v1/traditional/runs/${candidate.run_id}/http-replay/plan`,{method:'POST',body:JSON.stringify(payload)});
        if (!active || current !== revision) return;
        prepared = payload;
        plan.innerHTML = `<article class="candidate-evidence"><h3>执行前检查</h3><p>${preview.rounds} 轮 · 共 ${preview.request_count} 个请求 · 上限 ${preview.max_requests_per_second} 请求/秒</p><ol>${preview.requests.map(r=>`<li>${esc(roles[r.role]||r.role)} · ${esc(r.method)}<br><small>${esc(r.url)}</small></li>`).join('')}</ol><p>${esc(preview.limitation)}</p><p>预览尚未发送目标请求。执行会使用当前授权范围和剩余预算。</p><button class="primary-action compact" data-execute>执行两轮复验</button></article>`;
        plan.querySelector('[data-execute]').onclick = async () => {
          if (!prepared) return;
          const payload = prepared; prepared = null;
          [...form.elements].forEach(e=>e.disabled=true);
          plan.innerHTML = '<p role="status">正在创建后台复验任务…</p>';
          try {
            const job = await api(`/api/v1/traditional/runs/${candidate.run_id}/http-replay/jobs`,{method:'POST',body:JSON.stringify(payload)});
            field('ownerHeaders').value=''; field('otherHeaders').value='';
            renderResult(job); monitor(job.id);
          } catch (e) {error.textContent = e.message; plan.innerHTML = '<p>任务未创建，请检查失败原因后重新预览。</p>';enableForm();}
        };
      } catch(e) {error.textContent = e.message; plan.innerHTML = '';}
      finally {button.disabled=false;}
    };
    const recent = existingJobs.find(job=>['queued','running','cancelling'].includes(job.status)) || existingJobs.find(job=>['failed','cancelled','interrupted'].includes(job.status));
    if (recent) monitor(recent.id);
  };
})();
