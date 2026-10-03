/* Read-only projection of persisted research links for the selected engagement. */
window.loadFieldworkResearchGraph = async function({engagementId,mode,findings,request}) {
  const t=(zh,en)=>typeof document!=="undefined"&&/^en(?:-|$)/i.test(document.documentElement.lang)?en:zh;
  const nodes=[],edges=new Map(),seen=new Set(),notes=[],pending=[];
  const addNode=(id,label,entity_type,attributes={})=>{if(!id||seen.has(id))return;seen.add(id);nodes.push({id,label:label||id,entity_type,attributes})};
  const addEdge=(source_id,target_id,relation_type,source)=>{const id=`${source_id}:${target_id}:${relation_type}`;edges.set(id,{id,source_id,target_id,relation_type,source})};
  function mergePage(graph,detail){
    if(graph.campaign_id!==detail.id||!Array.isArray(graph.nodes)||!Array.isArray(graph.edges))throw new Error(t("图谱分页响应不属于当前 Campaign 或格式无效","The graph page is invalid or belongs to another research campaign."));
    if(graph.nodes.some(node=>node.campaign_id!==detail.id)||graph.edges.some(edge=>edge.campaign_id!==detail.id))throw new Error(t("图谱记录不属于当前 Campaign","Graph records do not belong to the selected research campaign."));
    if(graph.page?.has_more&&!graph.page.next_cursor)throw new Error(t("图谱缺少下一页游标","The graph response is missing the next-page cursor."));
    const campaignId=`campaign:${detail.id}`;
    for(const node of graph.nodes){
      addNode(node.id,node.title||node.id,node.node_type||"research_node",{
        ...(node.attributes||{}),source:node.source_type||"research_nodes",source_ref:node.source_ref,
        status:node.node_type==="canonical_result"&&node.canonical_trust?.current!==true?"stale_verification":node.status,
        canonical_trust:node.canonical_trust||null,run_id:node.run_id,confidence:node.confidence,
        campaign_id:detail.id,body:node.body,created_at:node.created_at
      });
      addEdge(campaignId,node.id,"contains_research_node","research_nodes.campaign_id");
    }
    for(const edge of graph.edges){
      addEdge(edge.source_id,edge.target_id,edge.relation_type,"research_edges");
    }
  }
  const pageUrl=id=>`/api/v1/research/campaigns/${encodeURIComponent(id)}/graph/page`;
  const visibleEdges=()=>[...edges.values()].filter(edge=>seen.has(edge.source_id)&&seen.has(edge.target_id));
  function result(){
    const visible=visibleEdges(),waiting=edges.size-visible.length;
    // Keep the facts tied to this page snapshot while translating UI copy lazily.
    // localize() also survives object-spread projections used by the evidence view.
    const noteFactories=[...notes,
      ...(pending.length?[()=>t("图谱尚未读完，可继续加载节点与关系。","More graph nodes and relationships are available to load.")]:[]),
      ...(waiting?[()=>t(`${waiting} 条关系正在等待端点节点；刷新可重新读取变化后的图谱。`,`${waiting} relationship${waiting===1?" is":"s are"} waiting for endpoint nodes. Refresh to reload graph changes.`)]:[])];
    const localize=()=>({notes:noteFactories.map(note=>note()),boundary:t("连线仅来自持久化 ID 关系；Evidence 引用不自动代表独立验证。","Edges reflect persisted ID relationships only. Evidence references do not imply independent verification.")});
    return {engagement_id:engagementId,mode,root_id:root,nodes:[...nodes],edges:visible,
      counts:{nodes:nodes.length,edges:visible.length},get notes(){return localize().notes},
      page:{has_more:pending.length>0,pending_edges:waiting},loadMore,
      get boundary(){return localize().boundary},localize};
  }
  let loading=false;
  async function loadMore(){
    if(loading||!pending.length)return result();
    loading=true;
    try{
      const next=pending[0],graph=await request(`${pageUrl(next.detail.id)}?cursor=${encodeURIComponent(next.cursor)}`);
      if(graph.page?.has_more&&graph.page.next_cursor===next.cursor)throw new Error(t("图谱分页游标未前进，请刷新重试","The graph cursor did not advance. Refresh and try again."));
      mergePage(graph,next.detail);
      pending.shift();
      if(graph.page?.has_more)pending.push({detail:next.detail,cursor:graph.page.next_cursor});
      return result();
    }finally{loading=false}
  }
  const root=`engagement:${engagementId}`;
  addNode(root,engagementId,"target",{source:"Engagement"});
  const campaigns=await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`);
  if(!Array.isArray(campaigns))throw new Error(t("Campaign 列表格式无效","The research campaign list is invalid."));
  const details=[];
  for(let i=0;i<campaigns.length;i+=8)details.push(...await Promise.all(campaigns.slice(i,i+8).map(x=>request(`/api/v1/campaigns/${encodeURIComponent(x.id)}`))));
  for(const detail of details){
    if(detail.engagement_id!==engagementId){const campaignId=detail.id;notes.push(()=>t(`忽略不属于当前项目的 Campaign ${campaignId}`,`Ignored research campaign ${campaignId}: it does not belong to the selected project.`));continue}
    const campaignId=`campaign:${detail.id}`;
    addNode(campaignId,detail.name||detail.id,"research_campaign",{source:"Research Campaign",status:detail.status,id:detail.id});
    addEdge(root,campaignId,"contains","campaign.engagement_id");
    for(const hypothesis of detail.hypotheses||[]){
      if(hypothesis.campaign_id!==detail.id)continue;
      const hypothesisId=`hypothesis:${hypothesis.id}`;
      addNode(hypothesisId,hypothesis.statement||hypothesis.id,"hypothesis",{source:"research_hypotheses",status:hypothesis.status,priority:hypothesis.priority,id:hypothesis.id});
      addEdge(campaignId,hypothesisId,"proposes","hypothesis.campaign_id");
      for(const id of hypothesis.evidence_ids||[]){const evidenceId=`evidence:${id}`;addNode(evidenceId,id,"evidence_reference",{source:"hypothesis.evidence_ids",verified:false});addEdge(hypothesisId,evidenceId,"cites_support","hypothesis.evidence_ids")}
      for(const id of hypothesis.counterevidence_ids||[]){const evidenceId=`evidence:${id}`;addNode(evidenceId,id,"counterevidence_reference",{source:"hypothesis.counterevidence_ids",verified:false});addEdge(hypothesisId,evidenceId,"cites_counterevidence","hypothesis.counterevidence_ids")}
    }
    for(const link of detail.candidate_links||[]){
      const candidateId=`candidate:${link.candidate_id}`;
      addNode(candidateId,link.title||link.candidate_id,"candidate",{source:"campaign_candidate_links",status:link.status,id:link.candidate_id});
      addEdge(campaignId,candidateId,"linked_candidate","campaign_candidate_links");
    }
    const graph=await request(pageUrl(detail.id));
    mergePage(graph,detail);
    if(graph.page?.has_more)pending.push({detail,cursor:graph.page.next_cursor});
  }
  if(!findings)notes.push(()=>t("Findings 数据不可用：候选与可信结果可能缺失","Findings data is unavailable; candidates and verified results may be missing."));
  const candidates=(findings?.candidates||[]).filter(x=>x.engagement_id===engagementId);
  const verified=(findings?.verified||[]).filter(x=>x.engagement_id===engagementId&&x.status==="verified");
  for(const candidate of candidates){
    const id=`candidate:${candidate.id}`;
    addNode(id,candidate.title||candidate.id,"candidate",{source:"candidate_findings",status:candidate.status,run_id:candidate.run_id,id:candidate.id});
    if(![...edges.values()].some(x=>x.target_id===id))addEdge(root,id,"engagement_candidate","candidate.engagement_id");
    for(const evidence of candidate.evidence_ids||[]){const evidenceId=`evidence:${evidence}`;addNode(evidenceId,evidence,"evidence_reference",{source:"candidate.evidence_ids",verified:false});addEdge(id,evidenceId,"cites_evidence","candidate.evidence_ids")}
  }
  for(const finding of verified){
    const id=`finding:${finding.id}`,candidateId=`candidate:${finding.candidate_id}`;
    addNode(id,finding.title||finding.id,"canonical_result",{source:"canonical_findings",status:finding.status,severity:finding.severity,id:finding.id});
    if(seen.has(candidateId))addEdge(candidateId,id,"verified_promotion","canonical_findings.candidate_id");
    else addEdge(root,id,"verified_result","canonical_findings.engagement_id");
  }
  if(!campaigns.length&&!candidates.length&&!verified.length)notes.push(()=>t("当前项目没有研究假设、候选或可信结果","The selected project has no research hypotheses, candidates, or verified results."));
  return result();
};
