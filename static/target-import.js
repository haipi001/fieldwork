(() => {
  const button = document.querySelector('#openTargetImport');
  const dialog = document.querySelector('#targetImportDialog');
  if (!button || !dialog) return;
  button.textContent = '导入接口 / 源码 ZIP / CIDR';
  dialog.querySelector('.dialog-top>div').innerHTML = '<p class="eyebrow">LOCAL TARGET IMPORT</p><h2>导入目标资料</h2><p>先在本机安全解析并显示边界，再建立待确认项目。源码、网络范围和接口记录使用各自的验证规则。</p>';
  dialog.querySelector('#targetImportForm').innerHTML = '<label>资料类型<select id="targetImportType"><option value="auto">自动识别接口包</option><option value="openapi">OpenAPI</option><option value="postman">Postman Collection</option><option value="har">HAR</option><option value="source_zip">源码 ZIP</option><option value="cidr">CIDR 文本</option></select></label><label>选择文件<input id="targetImportFile" type="file" accept=".json,.yaml,.yml,.har,.zip,.txt,.cidr,application/json,text/yaml,application/zip,text/plain" required></label><label>项目名称<input id="targetImportName" required minlength="2" placeholder="例如：订单 API / Vault 源码 / 实验网段"></label><button type="submit" class="primary-action compact"><span>安全预览</span><b>→</b></button>';
  let packagePayload = null;
  const fileAsBase64 = file => new Promise((resolve,reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('无法读取本地文件'));
    reader.onload = () => resolve(String(reader.result).split(',',2)[1] || '');
    reader.readAsDataURL(file);
  });
  const summary = preview => {
    if (preview.kind === 'source_zip') return `${preview.counts.files} 个源码文件 · ${Math.ceil(preview.counts.bytes/1024)} KB · 忽略 ${preview.counts.ignored}`;
    if (preview.kind === 'cidr') return `${preview.counts.ranges} 个网段 · ${preview.counts.addresses.toLocaleString()} 个地址`;
    return `${preview.counts.endpoints} 个端点 · ${preview.counts.origins} 个来源`;
  };
  const previewBody = preview => {
    if (preview.kind === 'source_zip') {
      const languages = Object.entries(preview.languages).map(([kind,count])=>`${kind} ${count}`).join(' · ');
      return `<div class="source-import-list"><b>${esc(languages)}</b>${preview.files.slice(0,12).map(item=>`<span>${esc(item.path)}<small>${item.bytes} B · ${esc(item.sha256.slice(0,12))}</small></span>`).join('')}${preview.files.length>12?`<small>另有 ${preview.files.length-12} 个文件</small>`:''}</div>`;
    }
    if (preview.kind === 'cidr') return `<fieldset><legend>将冻结以下授权网段</legend>${preview.ranges.map(item=>`<label><span><b>${esc(item.cidr)}</b><small>IPv${item.ip_version} · ${item.addresses.toLocaleString()} 个地址</small></span></label>`).join('')}</fieldset>`;
    const endpoints=(preview.endpoints||[]).slice(0,10).map(item=>{
      const parameters=(item.parameters||[]).map(param=>`${param.in}:${param.name} (${param.type})`).join(' · ');
      const fields=(item.body_fields||[]).map(field=>`${field.name} (${field.type})`).join(' · ');
      return `<article class="import-endpoint"><b>${esc(item.method)} ${esc(item.url)}</b><small>${item.auth_required?'需要身份 · ':''}${esc(parameters||'无已知参数')}${fields?`<br>Body：${esc(fields)}`:''}</small></article>`;
    }).join('');
    return `<fieldset><legend>选择冻结根目标</legend>${preview.origins.map((item,index)=>`<label><input type="radio" name="importRoot" value="${esc(item.origin)}" ${index===0?'checked':''}><span><b>${esc(item.origin)}</b><small>${item.requests} 个请求${item.default_in_scope?' · 默认进入 Scope':' · 默认排除'}</small></span></label>`).join('')}</fieldset><div class="import-endpoints"><h3>接口结构预览</h3><p>只显示名称与类型，不包含实际请求值；导入不代表已测试。</p>${endpoints}${preview.endpoints.length>10?`<small>另有 ${preview.endpoints.length-10} 个端点将在项目中保留</small>`:''}</div>`;
  };
  button.onclick = () => {
    dialog.querySelector('#targetImportPreview').hidden = true;
    dialog.querySelector('#targetImportError').textContent = '';
    dialog.showModal();
  };
  dialog.querySelector('#targetImportForm').onsubmit = async event => {
    event.preventDefault();
    const submit=dialog.querySelector('#targetImportForm button[type="submit"]');
    if(submit.disabled)return;
    submit.disabled=true;
    const file = dialog.querySelector('#targetImportFile').files[0];
    const selected = dialog.querySelector('#targetImportType').value;
    if (!file){submit.disabled=false;return}
    try {
      const zip = selected === 'source_zip' || (selected === 'auto' && file.name.toLowerCase().endsWith('.zip'));
      packagePayload = {kind:zip?'source_zip':selected,content:zip?await fileAsBase64(file):await file.text(),encoding:zip?'base64':'text',mode:state.mode,filename:file.name};
      const preview = await api('/api/v1/target-packages/preview',{method:'POST',body:JSON.stringify(packagePayload)});
      packagePayload.kind = preview.kind;
      const panel = dialog.querySelector('#targetImportPreview');
      panel.hidden = false;
      panel.innerHTML = `<div class="import-summary"><strong>${esc(preview.kind.toUpperCase())}</strong><span>${esc(summary(preview))}</span></div>${previewBody(preview)}<p>${esc(preview.boundary)}</p><button type="button" id="applyTargetImport" class="primary-action compact"><span>建立草稿并审阅 Scope</span><b>→</b></button>`;
      panel.querySelector('#applyTargetImport').onclick = async () => {
        const apply=panel.querySelector('#applyTargetImport');
        if(apply.disabled)return;
        apply.disabled=true;
        try {
          const root = dialog.querySelector('input[name="importRoot"]:checked')?.value || null;
          const result = await api('/api/v1/target-packages/apply',{method:'POST',body:JSON.stringify({...packagePayload,name:dialog.querySelector('#targetImportName').value.trim(),root_target:root})});
          dialog.close(); state.pending=[result.engagement];
          const amount = result.import.files ?? result.import.ranges ?? result.import.in_scope_endpoints;
          document.querySelector('#scopeState').textContent=`已导入 ${amount} 项 · 等待确认`;
          showScope(state.pending); await refresh(); toast('目标资料已安全导入，确认 Scope 后才能运行');
        } catch(error) { dialog.querySelector('#targetImportError').textContent=`${error.message}。若连接中断，请先核对最近项目再重试，避免重复创建。`; }
        finally {apply.disabled=false}
      };
    } catch(error) { dialog.querySelector('#targetImportError').textContent=error.message; }
    finally {submit.disabled=false}
  };
})();
