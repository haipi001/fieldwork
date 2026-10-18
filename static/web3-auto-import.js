(() => {
  const $ = s => document.querySelector(s);
  let campaign = null, current = null, generation = 0, context = {};
  const usable = new Set(['source_ready','chain_unchecked','ready_for_research','aligned','alignment_blocked','compile_failed']);
  const terminal = new Set(['source_ready','chain_unchecked','ready_for_research','aligned','alignment_blocked','compile_failed','unverified_source','cancelled','interrupted','failed']);
  const text = (zh,en) => localStorage.getItem('fieldwork-v5-language') === 'en' ? en : zh;
  async function api(path, body) {
    const response = await fetch('/api/v1/web3/contract-imports' + path, {method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail==='string'?data.detail:text('导入请求失败','Import request failed'));
    return data;
  }
  const phases = {
    queued:['排队','Queued'], importing_source:['获取公开源码','Fetching public sources'],source_ready:['源码已获取','Sources ready'],
    compile_running:['正在隔离编译','Compiling in isolation'],compiled:['编译成功','Compiled'],chain_unchecked:['已编译；尚未检查链上部署','Compiled; chain unchecked'],
    alignment_running:['只读部署对齐','Comparing deployment'],aligned:['完整字节码匹配','Exact bytecode match'],ready_for_research:['产物已绑定；执行仍需确认','Attached; execution needs confirmation'],
    alignment_blocked:['部署对齐被阻止，保留只读材料','Alignment blocked; read-only materials retained'],compile_failed:['编译失败，保留只读材料','Compilation failed; read-only materials retained'],
    unverified_source:['未获取已验证源码','Verified source unavailable'],cancelled:['已取消','Cancelled'],interrupted:['重启中断，可重试','Interrupted by restart; retry available'],failed:['导入失败','Import failed']
  };
  function render(value) {
    const p=phases[value.status]||[value.status,value.status];
    $('#contractImportStatus').textContent = text(...p)+(value.contract_name?' · '+value.contract_name:'')+(value.file_count?' · '+value.file_count+text(' 个文件',' files'):'')+(value.compiler_version?' · solc '+value.compiler_version:'')+(value.elapsed_seconds!=null?' · '+value.elapsed_seconds+'s':'')+(value.error?' · '+value.error.message:'');
    // Render remote strings as text, never HTML or instructions.
    $('#contractImportOpen').disabled = !value.source_path || !value.finished_at;
    $('#contractImportLog').textContent = JSON.stringify({status:value.status,provider:value.provider,chain_id:value.chain_id,address:value.address,proxy:value.proxy,settings:value.settings,compile:value.compile,alignment:value.alignment,error:value.error,events:value.events},null,2);
  }
  function validateRpc() {
    const value=$('#executionRpc').value.trim();
    if(value && /(?:etherscan\.io|arbiscan\.io|basescan\.org|bscscan\.com|polygonscan\.com|snowtrace\.io|\/address\/)/i.test(value)) {
      const message=text('这是浏览器页面，请输入 Ethereum JSON-RPC HTTPS URL','This is an explorer page. Enter an Ethereum JSON-RPC HTTPS URL.');
      $('#executionRpc').setCustomValidity(message);throw new Error(message);
    }
    $('#executionRpc').setCustomValidity('');
  }
  async function start(refresh=false, restored=null) {
    if(!campaign)return;
    const mine=++generation, id=campaign.id;
    $('#contractImportStart').disabled=true;$('#contractImportRefresh').disabled=true;
    try {
      validateRpc();
      const chain=Number($('#contractImportChain').value)||null;
      const rpc=$('#executionRpc').value.trim();
      let value=restored||await api('',{engagement_id:id,address:$('#contractImportAddress').value.trim(),chain_id:chain,mode:rpc?'align':'auto',rpc_url:rpc||null,refresh});
      while(mine===generation && campaign?.id===id) {
        current=value;render(value);
        if(terminal.has(value.status) && value.finished_at) {
          context={};
          if(usable.has(value.status) && value.source_path) {
            const attached=await api('/'+encodeURIComponent(value.import_id)+'/attach',{engagement_id:id});
            if(mine!==generation||campaign?.id!==id)return;
            $('#executionSource').value=attached.source_path;
            context={contract_import_id:attached.import_id,chain_id:attached.chain_id,...(attached.fork_block_number!=null?{fork_block_number:attached.fork_block_number}:{})};
            $('#executionSource').dispatchEvent(new Event('input',{bubbles:true}));
            await window.FieldworkRefreshExecution?.();
          }else{await window.FieldworkRefreshExecution?.();}
          break;
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
        if(mine!==generation)return;
        value=await api('/'+encodeURIComponent(value.import_id));
      }
    }catch(error){if(mine===generation)$('#contractImportStatus').textContent=error.message}
    finally{if(mine===generation){$('#contractImportStart').disabled=false;$('#contractImportRefresh').disabled=false}}
  }
  $('#contractImportReadOnly').onchange=()=>window.FieldworkRefreshExecution?.();
  $('#contractImportStart').onclick=()=>start();
  $('#contractImportRefresh').onclick=()=>start(true);
  $('#contractImportOpen').onclick=async()=>{if(current){try{await api('/'+encodeURIComponent(current.import_id)+'/open',{});}catch(error){$('#contractImportStatus').textContent=error.message}}};
  $('#contractImportCancel').onclick=async()=>{if(current){try{await api('/'+encodeURIComponent(current.import_id)+'/cancel',{});}catch(error){$('#contractImportStatus').textContent=error.message}}};
  $('#executionRpc').addEventListener('input',()=>{try{validateRpc()}catch(error){$('#executionMessage').textContent=error.message}});
  $('#executionDialog').addEventListener('close',()=>{generation++;$('#contractImportStart').disabled=false;$('#contractImportRefresh').disabled=false});
  window.FieldworkContractImport={
    async open(value){generation++;campaign=value;current=null;context={};$('#contractImportReadOnly').checked=true;$('#contractImportPanel').hidden=value.mode!=='web3'||value.target_type!=='contract';$('#contractImportAddress').value=value.raw_target||value.normalized_target||'';$('#contractImportChain').value=value.chain_id||'';$('#contractImportStatus').textContent=text('一次点击获取源码并编译；填写 RPC 后自动进行只读对齐','One click fetches and compiles sources; configure RPC for read-only alignment');$('#contractImportLog').textContent='';$('#contractImportOpen').disabled=true;$('#contractImportStart').disabled=false;$('#contractImportRefresh').disabled=false;
      if(value.mode!=='web3'||value.target_type!=='contract')return;
      const mine=generation;
      try{
        const latest=await api('/engagements/'+encodeURIComponent(value.id)+'/latest');
        if(mine!==generation||campaign?.id!==value.id)return;
        const saved=latest.import;
        if(saved && saved.status!=='interrupted'){await start(false,saved)}
        else{await start()}
      }catch(error){if(mine===generation)$('#contractImportStatus').textContent=error.message}
    },
    executionContext:()=>campaign?.mode==='web3'&&campaign?.target_type==='contract'?{...context,...($('#contractImportReadOnly').checked?{execution_mode:'read_only'}:{})}:{},validateRpc
  };
})();
