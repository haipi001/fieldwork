(() => {
  'use strict';
  window.createFieldworkIntelImport=({request,post,esc,onImported})=>{
    const host=document.createElement('article');host.id='intelligenceImport';host.className='panel';host.hidden=true;host.style.overflowWrap='anywhere';
    document.querySelector('#hypothesesContent').after(host);
    const t=(zh,en)=>document.documentElement.lang.startsWith('en')?en:zh;
    const copy=(zh,en)=>`<span data-intel-zh="${esc(zh)}" data-intel-en="${esc(en)}">${esc(t(zh,en))}</span>`;
    host.innerHTML=`<h2>${copy('导入情报来源','Import intelligence source')}</h2><p>${copy('记录保存在本机全局情报库。新修订可能使旧匹配过期，历史记录会保留。来源和许可证是操作员声明，App 尚未验证发布者或使用条款；导入不执行扫描、模型或复验。','Records enter the local global intelligence store. New revisions may make older matches stale; history is preserved. Source and license are operator declarations; publisher and terms are not verified. Import does not run scans, models, or verification.')}</p><form id="intelImportForm" class="form-stack"><label>${copy('适配格式','Adapter')}<select id="intelImportAdapter"><option value="osv">OSV</option><option value="normalized">Normalized</option></select></label><label>${copy('来源名称','Source name')}<input id="intelImportName" required maxlength="120"></label><label>${copy('许可证或使用条款声明','License or terms statement')}<input id="intelImportLicense" required maxlength="200"></label><label>${copy('来源 HTTPS 地址（可选；不会访问）','Source HTTPS URL (optional; not fetched)')}<input id="intelImportUrl" type="url" maxlength="2048"></label><label>${copy('读取本地 JSON 文件（最多 2 MB）','Read a local JSON file (up to 2 MB)')}<input id="intelImportFile" type="file" accept=".json,application/json"></label><label>${copy('JSON：单条公告或最多 100 条的数组','JSON: one advisory or an array of up to 100')}<textarea id="intelImportJson" required maxlength="2000000" rows="8" spellcheck="false"></textarea></label><button>${copy('预览规范化结果','Preview normalized records')}</button></form><div id="intelImportPreview"></div><p id="intelImportStatus" role="status"></p>`;
    const $=id=>host.querySelector(id),form=$('#intelImportForm');let campaign='',generation=0,busy=false,preview=null;
    let statusValue='';
    function drawStatus(){$('#intelImportStatus').textContent=typeof statusValue==='function'?statusValue():statusValue}
    function status(value){statusValue=value;drawStatus()}
    function renderPreview(){
      $('#intelImportPreview').innerHTML=preview?`<p>${t('规范化记录','Normalized records')}: ${preview.received} · ${esc(preview.source)}</p><ul>${preview.records.map(x=>`<li>${esc(x.external_id)} · ${x.affected_packages} · ${esc(x.record_sha256)}</li>`).join('')}</ul><p>${t('预览不写入数据。来源条款尚未验证。','Preview does not write records. Source terms remain unverified.')}</p><label class="check-line"><input id="intelImportAccepted" type="checkbox" ${busy?'disabled':''}>${t('确认上述来源声明，仅将这些记录导入本机情报库','Confirm these source declarations and import these records into the local intelligence store only')}</label><button id="intelImportCommit" type="button" ${busy?'disabled':''}>${t('确认导入','Confirm import')}</button>`:'';
      const commit=$('#intelImportCommit');if(commit)commit.onclick=()=>runImport();
    }
    function body(){
      const text=$('#intelImportJson').value;if(new TextEncoder().encode(text).length>2000000)throw new Error(t('JSON 超过 2 MB。','JSON exceeds 2 MB.'));
      let value;try{value=JSON.parse(text)}catch{throw new Error(t('JSON 无法解析。','JSON could not be parsed.'))}
      const records=Array.isArray(value)?value:[value];if(!records.length||records.length>100||records.some(x=>!x||Array.isArray(x)||typeof x!=='object'))throw new Error(t('需要 1–100 条公告对象。','Expected 1–100 advisory objects.'));
      return {adapter:$('#intelImportAdapter').value,source_name:$('#intelImportName').value.trim(),license:$('#intelImportLicense').value.trim(),source_url:$('#intelImportUrl').value.trim()||null,records};
    }
    function lock(value){busy=value;form.querySelectorAll('input,textarea,select,button').forEach(x=>x.disabled=value);renderPreview()}
    function invalidate(){generation++;preview=null;renderPreview();status('')}
    form.addEventListener('input',invalidate);form.addEventListener('change',event=>{if(event.target.id!=='intelImportFile')invalidate()});
    $('#intelImportFile').onchange=async event=>{
      invalidate();const token=generation,file=event.target.files[0];if(!file)return;
      if(file.size>2000000){status(()=>t('文件超过 2 MB。','File exceeds 2 MB.'));return}
      try{const text=await file.text();if(token===generation){$('#intelImportJson').value=text;status(()=>t('文件已读取，请核对后预览。','File loaded. Review it before previewing.'))}}catch{if(token===generation)status(()=>t('文件读取失败。','File could not be read.'))}
    };
    form.onsubmit=async event=>{
      event.preventDefault();if(busy||!form.reportValidity())return;const token=generation;preview=null;lock(true);status(()=>t('正在预览…','Preparing preview…'));
      try{const value=await post('/api/v1/intelligence/records/preview',body());if(token===generation){preview=value;status(()=>t('预览完成，尚未导入。','Preview ready; records have not been imported.'))}}catch(error){if(token===generation)status(error.message)}finally{if(token===generation)lock(false)}
    };
    async function runImport(){
      if(busy||!preview||!$('#intelImportAccepted').checked||!form.reportValidity())return;
      const token=generation,hash=preview.preview_hash;lock(true);status(()=>t('正在导入…','Importing…'));
      try{const value=await post('/api/v1/intelligence/records/import-reviewed',{...body(),preview_hash:hash});if(token===generation){preview=null;status(()=>t('导入完成：新增 ','Import complete: inserted ')+value.inserted+t('，重复 ','; duplicates ')+value.duplicates);onImported()}}catch(error){if(token===generation)status(error.message)}finally{if(token===generation)lock(false)}
    }
    document.addEventListener('fieldwork:languagechange',()=>{host.querySelectorAll('[data-intel-zh]').forEach(x=>x.textContent=t(x.dataset.intelZh,x.dataset.intelEn));renderPreview();drawStatus()});
    return {update(id){if(campaign===id)return;campaign=id||'';generation++;preview=null;host.hidden=!campaign;lock(false);status('')}};
  };
})();
