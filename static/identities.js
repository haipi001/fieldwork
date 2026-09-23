(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'identityDialog';
  dialog.className = 'identity-dialog';
  dialog.innerHTML = `<div class="identity-shell"><header><div><h2>登录测试账号</h2><p>依次登录两个不同的测试账号。会话安全保存在钥匙串中，页面中的账号归属信息会作为验证材料，完成后自动检查并继续等待中的任务。</p></div><button class="close-button" aria-label="关闭">×</button></header><div class="identity-quick"><button id="quickIdentityLogin" class="primary-action compact" type="button">登录测试账号</button><p id="quickIdentityHelp">账号位置自动准备，无需填写技术参数。</p></div><section id="identityMatrix" class="identity-matrix"></section><section id="sessionCapturePanel" class="session-capture-panel" hidden><div><p class="eyebrow">测试账号登录</p><h3 id="sessionCaptureTitle">登录测试账号</h3><p id="sessionCaptureHelp"></p></div><form id="sessionCaptureForm"><details><summary>登录设置</summary><label>登录地址<input id="sessionLoginUrl" type="url" required></label><label>请求上限<input id="sessionMaxRequests" type="number" min="20" max="1000" value="200" required></label></details><div class="session-capture-actions"><button class="quiet-button" type="submit">打开隔离 Chrome</button><button id="finishSessionCapture" class="primary-action compact" type="button" hidden><span>完成登录</span><b>→</b></button><button id="cancelSessionCapture" class="text-action danger-text" type="button" hidden>取消采集</button></div></form><p id="sessionCaptureState" class="form-message"></p></section><details class="identity-advanced"><summary>高级账号配置</summary><form id="identityForm" class="identity-form"><input type="hidden" id="identityEngagement"><label>账号名称<input id="identityLabel" required placeholder="例如：Tenant A 普通用户"></label><label>角色<input id="identityRole" required placeholder="guest / user / admin"></label><label>租户<input id="identityTenant" placeholder="tenant-a"></label><label>认证类型<select id="identityAuth"><option value="none">未配置</option><option value="keychain_reference">macOS Keychain 引用</option></select></label><label>凭据引用<input id="identityCredentialRef" placeholder="keychain://fieldwork/account-a"><small>只填写引用名称，不要粘贴真实凭据。</small></label><label>会话状态<select id="identityStatus"><option value="needs_login">需要登录</option><option value="ready">已验证可用</option><option value="expired">已失效</option><option value="disabled">停用</option></select></label><label class="wide">备注<input id="identityNotes" placeholder="测试权限、账号限制或刷新说明"></label><button class="primary-action compact" type="submit"><span>添加测试身份</span><b>→</b></button></form></details></div>`;
  document.body.append(dialog);
  let engagementId = null, engagement = null, captureId = null, captureIdentityId = null, busy = false, researchRunId = null;

  async function render() {
    const matrix = await api(`/api/v1/engagements/${engagementId}/role-matrix`);
    const authAllowed = Boolean(engagement?.confirmed_at && engagement?.scope?.allow_authentication);
    $('#quickIdentityLogin').disabled = !authAllowed || busy || Boolean(captureId);
    $('#quickIdentityHelp').textContent = authAllowed ? '依次登录两个不同账号；已有账号可直接刷新会话。' : '此项目未允许账号登录，请在新项目的授权扩展中启用测试账号登录。';
    const labels = {ready:'会话已保存', needs_login:'等待登录', expired:'需要重新登录', disabled:'已停用'};
    $('#identityMatrix').innerHTML = matrix.identities.map(item => `<article><div><strong>${esc(item.label)}</strong><p>${esc(labels[item.session_status] || '等待登录')}</p></div><div class="identity-row-actions"><button class="quiet-button" data-capture-identity="${esc(item.id)}" data-capture-label="${esc(item.label)}" ${authAllowed && !captureId && !busy ? '' : 'disabled'}>${item.credential_configured?'重新登录':'登录'}</button></div><details><summary>高级</summary><p>${esc(item.role)} · ${esc(item.tenant || '无租户')}</p><select aria-label="会话状态" data-identity-status="${esc(item.id)}"><option value="ready" ${item.session_status==='ready'?'selected':''}>已验证可用</option><option value="needs_login" ${item.session_status==='needs_login'?'selected':''}>需要登录</option><option value="expired" ${item.session_status==='expired'?'selected':''}>已失效</option><option value="disabled" ${item.session_status==='disabled'?'selected':''}>停用</option></select><button class="text-action danger-text" data-delete-identity="${esc(item.id)}">删除</button></details></article>`).join('');
    $$('[data-identity-status]').forEach(select => select.onchange = async () => { await api(`/api/v1/identities/${select.dataset.identityStatus}`, {method:'PATCH', body:JSON.stringify({session_status:select.value})}); await render(); });
    $$('[data-delete-identity]').forEach(button => button.onclick = async () => { if (!confirm('删除这个测试身份？不会删除外部账号。')) return; await api(`/api/v1/identities/${button.dataset.deleteIdentity}`, {method:'DELETE'}); await render(); });
    $$('[data-capture-identity]').forEach(button => button.onclick = () => beginLogin(button.dataset.captureIdentity, button.dataset.captureLabel));
  }

  function openCapture(identityId, label) {
    captureIdentityId = identityId;
    $('#sessionCaptureTitle').textContent = `采集 ${label} 的登录态`;
    $('#sessionLoginUrl').value = engagement.normalized_target;
    $('#sessionCaptureHelp').textContent = `允许域名：${[new URL(engagement.normalized_target).hostname, ...(engagement.scope.auth_allowed_hosts || [])].join('、')}。跳转到其他域名的请求会被阻止。`;
    $('#sessionCaptureState').textContent = '点击后会打开一个不使用日常资料的独立 Chrome 窗口。';
    $('#sessionCapturePanel').hidden = false;
    $('#sessionCapturePanel').scrollIntoView({behavior:'smooth', block:'center'});
  }

  async function beginLogin(identityId, label) {
    if (busy || captureId) return;
    openCapture(identityId, label);
    await startCapture();
  }

  async function startCapture() {
    if (busy || captureId || !captureIdentityId) return;
    busy = true;
    $('#sessionCaptureForm button[type="submit"]').disabled = true;
    try {
      await render();
      const result = await api(`/api/v1/identities/${captureIdentityId}/session-captures`, {method:'POST', body:JSON.stringify({run_id:researchRunId,login_url:$('#sessionLoginUrl').value.trim(),max_requests:Number($('#sessionMaxRequests').value)})});
      captureId = result.id;
      $('#sessionCaptureState').textContent = '请在 Chrome 中登录并打开要检查的功能页面，然后返回点击“完成登录”。';
      $('#finishSessionCapture').hidden = false;
      $('#cancelSessionCapture').hidden = false;
    } catch (error) { $('#sessionCaptureState').textContent = error.message; }
    finally { busy = false; $('#sessionCaptureForm button[type="submit"]').disabled = Boolean(captureId); await render(); }
  }

  $('#quickIdentityLogin').onclick = async () => {
    if (busy || captureId) return;
    busy = true;
    $('#quickIdentityLogin').disabled = true;
    let identity;
    try {
      const result = await api(`/api/v1/engagements/${engagementId}/quick-identities`, {method:'POST'});
      identity = result.identities.find(item => item.session_status !== 'ready' || (item.expires_at && Date.parse(item.expires_at) <= Date.now()));
      if (!identity) toast('两个账号已有会话；需要切换时，点击对应账号的重新登录。');
    } catch (error) { toast(error.message); }
    finally { busy = false; await render(); }
    if (identity) await beginLogin(identity.id, identity.label);
  };

  async function cancelCapture(silent=false) {
    if (captureId) { try { await api(`/api/v1/session-captures/${captureId}`, {method:'DELETE'}); } catch (error) { if (!silent) toast(error.message); } }
    captureId=null; captureIdentityId=null; $('#sessionCaptureForm button[type="submit"]').disabled=false; $('#sessionCapturePanel').hidden=true; $('#finishSessionCapture').hidden=true; $('#cancelSessionCapture').hidden=true;
  }

  window.openIdentityWorkspace = async (id,runId=null) => { researchRunId=runId; engagementId=id; $('#identityEngagement').value=id; dialog.showModal(); try { engagement=await api(`/api/v1/engagements/${id}`); await render(); } catch (error) { toast(error.message); } };
  dialog.querySelector('.close-button').onclick = async () => { if (busy) return; await cancelCapture(true); dialog.close(); };
  dialog.addEventListener('cancel', async event => { event.preventDefault(); if (busy) return; await cancelCapture(true); dialog.close(); });
  $('#sessionCaptureForm').onsubmit = async event => { event.preventDefault(); await startCapture(); };
  $('#finishSessionCapture').onclick = async () => { if (busy || !captureId) return; busy=true; $('#finishSessionCapture').disabled=true; try { const result=await api(`/api/v1/session-captures/${captureId}/complete`, {method:'POST'}); captureId=null; $('#sessionCaptureState').textContent=result.resume_status === 'retry_needed' ? '登录已保存，自动续接暂未成功，请返回任务点击继续处理。' : result.resumed_jobs?.length ? '登录已保存，等待中的任务已自动重新检查。' : '登录已保存。请继续登录另一个测试账号，或返回查看任务。'; if(result.imported_responses) $('#sessionCaptureState').textContent += ` 已自动整理 ${result.imported_responses} 项验证材料。`; $('#finishSessionCapture').hidden=true; $('#cancelSessionCapture').hidden=true; $('#sessionCaptureForm button[type="submit"]').disabled=false; await render(); toast('登录已保存'); } catch (error) { toast(error.message); } finally { busy=false; $('#finishSessionCapture').disabled=false; await render(); } };
  $('#cancelSessionCapture').onclick = async () => { if (busy) return; await cancelCapture(); await render(); $('#sessionCaptureForm button[type="submit"]').disabled=false; toast('登录态采集已取消'); };
  $('#identityForm').onsubmit = async event => { event.preventDefault(); try { await api(`/api/v1/engagements/${engagementId}/identities`, {method:'POST', body:JSON.stringify({label:$('#identityLabel').value.trim(),role:$('#identityRole').value.trim(),tenant:$('#identityTenant').value.trim()||null,auth_type:$('#identityAuth').value,credential_ref:$('#identityCredentialRef').value.trim()||null,session_status:$('#identityStatus').value,notes:$('#identityNotes').value.trim()})}); event.target.reset(); await render(); toast('测试身份已加入角色矩阵'); } catch (error) { toast(error.message); } };
})();
