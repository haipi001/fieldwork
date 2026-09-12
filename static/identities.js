(() => {
  const dialog = document.createElement('dialog');
  dialog.id = 'identityDialog';
  dialog.className = 'identity-dialog';
  dialog.innerHTML = `<div class="identity-shell"><header><div><p class="eyebrow">ROLE & TENANT MATRIX</p><h2>测试账号工作台</h2><p>会话通过隔离的可见 Chrome 采集，只写入 macOS Keychain；Fieldwork 不读取日常浏览器资料。</p></div><button class="close-button" aria-label="关闭">×</button></header><section id="identityMatrix" class="identity-matrix"></section><section id="sessionCapturePanel" class="session-capture-panel" hidden><div><p class="eyebrow">VISIBLE SESSION CAPTURE</p><h3 id="sessionCaptureTitle">采集测试会话</h3><p id="sessionCaptureHelp"></p></div><form id="sessionCaptureForm"><label>登录地址<input id="sessionLoginUrl" type="url" required></label><label>请求上限<input id="sessionMaxRequests" type="number" min="20" max="1000" value="200" required></label><div class="session-capture-actions"><button class="quiet-button" type="submit">打开隔离 Chrome</button><button id="finishSessionCapture" class="primary-action compact" type="button" hidden><span>完成采集</span><b>→</b></button><button id="cancelSessionCapture" class="text-action danger-text" type="button" hidden>取消采集</button></div></form><p id="sessionCaptureState" class="form-message"></p></section><form id="identityForm" class="identity-form"><input type="hidden" id="identityEngagement"><label>账号名称<input id="identityLabel" required placeholder="例如：Tenant A 普通用户"></label><label>角色<input id="identityRole" required placeholder="guest / user / admin"></label><label>租户<input id="identityTenant" placeholder="tenant-a"></label><label>认证类型<select id="identityAuth"><option value="none">未配置</option><option value="keychain_reference">macOS Keychain 引用</option></select></label><label>凭据引用<input id="identityCredentialRef" placeholder="keychain://fieldwork/account-a"><small>只填写引用名称，不要粘贴真实凭据。</small></label><label>会话状态<select id="identityStatus"><option value="needs_login">需要登录</option><option value="ready">已验证可用</option><option value="expired">已失效</option><option value="disabled">停用</option></select></label><label class="wide">备注<input id="identityNotes" placeholder="测试权限、账号限制或刷新说明"></label><button class="primary-action compact" type="submit"><span>添加测试身份</span><b>→</b></button></form></div>`;
  document.body.append(dialog);
  let engagementId = null, engagement = null, captureId = null, captureIdentityId = null;

  async function render() {
    const matrix = await api(`/api/v1/engagements/${engagementId}/role-matrix`);
    const authAllowed = Boolean(engagement?.confirmed_at && engagement?.scope?.allow_authentication);
    $('#identityMatrix').innerHTML = matrix.identities.length ? `<div class="identity-summary"><b>${matrix.identities.length} 个身份</b><span>${matrix.ready_pairs}/${matrix.pairs.length} 个角色组合可测试</span></div>${matrix.identities.map(item => `<article><div><strong>${esc(item.label)}</strong><p>${esc(item.role)} · ${esc(item.tenant || '无租户')} · ${esc(item.auth_type)}</p><small>${item.credential_configured ? '会话凭据已安全配置' : '尚未采集会话'}</small></div><select data-identity-status="${esc(item.id)}"><option value="ready" ${item.session_status==='ready'?'selected':''}>已验证可用</option><option value="needs_login" ${item.session_status==='needs_login'?'selected':''}>需要登录</option><option value="expired" ${item.session_status==='expired'?'selected':''}>已失效</option><option value="disabled" ${item.session_status==='disabled'?'selected':''}>停用</option></select><div class="identity-row-actions"><button class="quiet-button" data-capture-identity="${esc(item.id)}" data-capture-label="${esc(item.label)}" ${authAllowed?'':'disabled'}>${item.credential_configured?'刷新会话':'采集登录态'}</button><button class="text-action danger-text" data-delete-identity="${esc(item.id)}">删除</button></div></article>`).join('')}<p class="identity-scope-note">${authAllowed?'登录流程已写入冻结 Scope；仅允许目标主域和授权附加域名。':'当前 Scope 未允许测试账号登录。请新建项目并在授权扩展中开启“允许测试账号登录”。'}</p>` : '<div class="empty-state">还没有测试身份。至少添加两个不同角色或租户，才能进行权限差异测试。</div>';
    $$('[data-identity-status]').forEach(select => select.onchange = async () => { await api(`/api/v1/identities/${select.dataset.identityStatus}`, {method:'PATCH', body:JSON.stringify({session_status:select.value})}); await render(); });
    $$('[data-delete-identity]').forEach(button => button.onclick = async () => { if (!confirm('删除这个测试身份？不会删除外部账号。')) return; await api(`/api/v1/identities/${button.dataset.deleteIdentity}`, {method:'DELETE'}); await render(); });
    $$('[data-capture-identity]').forEach(button => button.onclick = () => openCapture(button.dataset.captureIdentity, button.dataset.captureLabel));
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

  async function cancelCapture(silent=false) {
    if (captureId) { try { await api(`/api/v1/session-captures/${captureId}`, {method:'DELETE'}); } catch (error) { if (!silent) toast(error.message); } }
    captureId=null; captureIdentityId=null; $('#sessionCapturePanel').hidden=true; $('#finishSessionCapture').hidden=true; $('#cancelSessionCapture').hidden=true;
  }

  window.openIdentityWorkspace = async id => { engagementId=id; $('#identityEngagement').value=id; dialog.showModal(); try { engagement=await api(`/api/v1/engagements/${id}`); await render(); } catch (error) { toast(error.message); } };
  dialog.querySelector('.close-button').onclick = async () => { await cancelCapture(true); dialog.close(); };
  $('#sessionCaptureForm').onsubmit = async event => { event.preventDefault(); try { const result=await api(`/api/v1/identities/${captureIdentityId}/session-captures`, {method:'POST', body:JSON.stringify({login_url:$('#sessionLoginUrl').value.trim(),max_requests:Number($('#sessionMaxRequests').value)})}); captureId=result.id; $('#sessionCaptureState').textContent='隔离 Chrome 已打开。请完成登录，然后回到这里点击“完成采集”。'; $('#finishSessionCapture').hidden=false; $('#cancelSessionCapture').hidden=false; event.submitter.disabled=true; } catch (error) { toast(error.message); } };
  $('#finishSessionCapture').onclick = async () => { try { const result=await api(`/api/v1/session-captures/${captureId}/complete`, {method:'POST'}); captureId=null; $('#sessionCaptureState').textContent=`已安全保存 ${result.cookie_count} 个会话项，覆盖 ${result.domain_count} 个域名；未写入本地数据库。`; $('#finishSessionCapture').hidden=true; $('#cancelSessionCapture').hidden=true; $('#sessionCaptureForm button[type="submit"]').disabled=false; await render(); toast('测试会话已写入 macOS Keychain'); } catch (error) { toast(error.message); } };
  $('#cancelSessionCapture').onclick = async () => { await cancelCapture(); $('#sessionCaptureForm button[type="submit"]').disabled=false; toast('登录态采集已取消'); };
  $('#identityForm').onsubmit = async event => { event.preventDefault(); try { await api(`/api/v1/engagements/${engagementId}/identities`, {method:'POST', body:JSON.stringify({label:$('#identityLabel').value.trim(),role:$('#identityRole').value.trim(),tenant:$('#identityTenant').value.trim()||null,auth_type:$('#identityAuth').value,credential_ref:$('#identityCredentialRef').value.trim()||null,session_status:$('#identityStatus').value,notes:$('#identityNotes').value.trim()})}); event.target.reset(); await render(); toast('测试身份已加入角色矩阵'); } catch (error) { toast(error.message); } };
})();
