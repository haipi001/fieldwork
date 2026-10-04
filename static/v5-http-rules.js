/* Business expectations are explicit project inputs reviewed before Scope freezes. */
(() => {
  const t=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const host=document.createElement('section');host.id='httpReadRules';host.className='http-rule-editor';
  document.querySelector('#scopeSummary').after(host);
  let project=null,rules=[],authentication=false,dirty=false,busy=false,message='',refresh=null,epoch=0;
  const editable=()=>project?.mode==='traditional'&&project.status==='draft'&&!project.confirmed_at;
  const accessLabel=value=>({owner_only:t('仅所有者可读','Owner only'),allowlist:t('所有者和共享名单可读','Owner and sharing list'),public:t('公开可读','Public read')})[value]||value;
  function markDirty(){dirty=true;message='';document.querySelector('#scopeForm [type=submit]').disabled=true;}
  function render(){
    host.hidden=!project||project.mode!=='traditional';if(host.hidden)return;
    const locked=!editable()||busy;
    host.innerHTML=`<h3>${t('对象读取业务规则','Object read business rules')}</h3><p>${t('依据项目业务规范填写精确对象 URL。对象的 owner_id 字段本身不能证明禁止共享。','Use the project specification for each exact object URL. An owner_id field alone does not establish a sharing restriction.')}</p><label class="check-line"><input id="ruleAuthentication" type="checkbox" ${authentication?'checked':''} ${locked?'disabled':''}>${t('授权使用此范围内的测试账号登录与身份检查','Authorize test account login and identity checks within this scope')}</label>${rules.map((rule,i)=>`<fieldset data-rule="${i}" ${locked?'disabled':''}><legend>${t('规则','Rule')} ${i+1}</legend><label>${t('精确对象 URL','Exact object URL')}<input data-field="target" type="url" maxlength="2048" value="${esc(rule.target)}" placeholder="https://authorized.example/api/object/42"></label><label>${t('预期读取权限','Expected read permission')}<select data-field="access"><option value="" disabled ${!rule.access?'selected':''}>${t('选择业务权限','Select permission')}</option>${['owner_only','allowlist','public'].map(value=>`<option value="${value}" ${rule.access===value?'selected':''}>${esc(accessLabel(value))}</option>`).join('')}</select></label><label>${t('共享主体，每行一个','Sharing principals, one per line')}<textarea data-field="allowed_principals" rows="2" ${rule.access!=='allowlist'?'disabled':''}>${esc((rule.allowed_principals||[]).join('\n'))}</textarea></label><label>${t('业务依据','Business rule source')}<textarea data-field="source" rows="2" maxlength="2000" placeholder="${t('填写业务文档、规范章节或项目方确认','Business document, specification section or project confirmation')}">${esc(rule.source)}</textarea></label>${editable()?`<button type="button" data-remove="${i}" ${busy?'disabled':''}>${t('移除此规则','Remove rule')}</button>`:''}</fieldset>`).join('')||`<p>${t('尚未配置业务规则；对象读取复验不能进入正式确认。','No business rules configured. Object read replay cannot proceed to formal confirmation.')}</p>`}${editable()?`<div class="rule-actions"><button type="button" id="ruleAdd" ${busy||rules.length>=100?'disabled':''}>${t('添加对象规则','Add object rule')}</button><button type="button" id="ruleSave" ${busy||!dirty?'disabled':''}>${busy?t('保存中…','Saving…'):t('保存草稿规则','Save draft rules')}</button></div>`:`<p class="trust-note">${t('此范围已冻结。修改业务规则需要新建研究草稿；现有运行与证据保留原绑定。','This scope is frozen. Rule changes require a new research draft; existing runs and evidence retain their original bindings.')}</p>`}<p id="ruleMessage" role="status">${esc(message|| (dirty?t('规则尚未保存。保存并重新审阅后才能冻结 Scope。','Unsaved rules. Save and review before freezing scope.'):''))}</p>`;
    host.querySelector('#ruleAuthentication').onchange=e=>{authentication=e.target.checked;markDirty();render();};
    host.querySelectorAll('[data-field]').forEach(input=>input.addEventListener('input',()=>{
      const rule=rules[Number(input.closest('[data-rule]').dataset.rule)],field=input.dataset.field;
      rule[field]=field==='allowed_principals'?input.value.split('\n').map(x=>x.trim()).filter(Boolean):input.value;
      if(field==='access'&&input.value!=='allowlist')rule.allowed_principals=[];
      markDirty();if(field==='access')render();else{host.querySelector('#ruleSave').disabled=false;host.querySelector('#ruleMessage').textContent=t('规则尚未保存。保存并重新审阅后才能冻结 Scope。','Unsaved rules. Save and review before freezing scope.');}
    }));
    host.querySelectorAll('[data-remove]').forEach(button=>button.onclick=()=>{rules.splice(Number(button.dataset.remove),1);markDirty();render();});
    host.querySelector('#ruleAdd')?.addEventListener('click',()=>{rules.push({target:'',access:'',source:'',allowed_principals:[]});markDirty();render();host.querySelector('[data-rule]:last-of-type input').focus();});
    host.querySelector('#ruleSave')?.addEventListener('click',save);
    if(editable())document.querySelector('#scopeForm [type=submit]').disabled=dirty||busy;
  }
  async function save(){
    if(busy||!editable())return;
    if(rules.some(rule=>!rule.target.trim()||!rule.access||rule.source.trim().length<3)){
      message=t('请补齐每条规则的对象 URL、权限和业务依据。','Complete the URL, permission and source for each rule.');render();return;
    }
    busy=true;const token=epoch,id=project.id;render();
    try{
      const response=await fetch(`/api/v1/engagements/${encodeURIComponent(id)}/http-read-rules`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope_snapshot_id:project.current_scope_snapshot_id,rules,allow_authentication:authentication})});
      const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:t('规则保存失败，请检查输入。','Rules could not be saved. Check inputs.'));
      if(token!==epoch)return;dirty=false;busy=false;await refresh(id);
    }catch(error){if(token===epoch)message=error.message;}finally{if(token===epoch){busy=false;render();}}
  }
  window.FieldworkHttpRules={review(value,onSaved){epoch++;project=value;rules=Array.isArray(value.scope?.http_object_read_rules)?value.scope.http_object_read_rules.filter(x=>x&&typeof x==='object').map(x=>({...x,allowed_principals:Array.isArray(x.allowed_principals)?[...x.allowed_principals]:[]})):[];authentication=Boolean(value.scope?.allow_authentication);dirty=false;busy=false;message='';refresh=onSaved;render();},pending(){return editable()&&(dirty||busy);}};
  document.addEventListener('fieldwork:languagechange',render);
})();
