/* Read-only projection of persisted research links for the selected engagement. */
window.loadFieldworkResearchGraph = async function({engagementId,mode,findings,request}) {
  const nodes=[],edges=[],seen=new Set(),notes=[];
  const addNode=(id,label,entity_type,attributes={})=>{if(!id||seen.has(id))return;seen.add(id);nodes.push({id,label:label||id,entity_type,attributes})};
  const addEdge=(source_id,target_id,relation_type,source)=>{if(seen.has(source_id)&&seen.has(target_id))edges.push({id:`${source_id}:${target_id}:${relation_type}`,source_id,target_id,relation_type,source})};
  const root=`engagement:${engagementId}`;
  addNode(root,engagementId,"target",{source:"Engagement"});
  const campaigns=await request(`/api/v1/engagements/${encodeURIComponent(engagementId)}/campaigns`);
  if(!Array.isArray(campaigns))throw new Error("Campaign 列表格式无效");
  const details=[];
  for(let i=0;i<campaigns.length;i+=8)details.push(...await Promise.all(campaigns.slice(i,i+8).map(x=>request(`/api/v1/campaigns/${encodeURIComponent(x.id)}`))));
  for(const detail of details){
    if(detail.engagement_id!==engagementId){notes.push(`忽略不属于当前项目的 Campaign ${detail.id}`);continue}
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
    const graph=await request(`/api/v1/research/campaigns/${encodeURIComponent(detail.id)}/graph`);
    for(const node of graph.nodes||[]){
      addNode(node.id,node.title||node.id,node.node_type||"research_node",{
        ...(node.attributes||{}),source:node.source_type||"research_nodes",source_ref:node.source_ref,
        status:node.node_type==="canonical_result"&&node.canonical_trust?.current!==true?"stale_verification":node.status,
        canonical_trust:node.canonical_trust||null,
        run_id:node.run_id,confidence:node.confidence,campaign_id:detail.id,
        body:node.body,created_at:node.created_at
      });
      addEdge(campaignId,node.id,"contains_research_node","research_nodes.campaign_id");
    }
    for(const edge of graph.edges||[])addEdge(edge.source_id,edge.target_id,edge.relation_type,"research_edges");
    if(graph.page?.has_more)notes.push(`Campaign ${detail.id} 图谱节点超过当前 200 条读取上限`);
  }
  if(!findings)notes.push("Findings 数据不可用：候选与可信结果可能缺失");
  const candidates=(findings?.candidates||[]).filter(x=>x.engagement_id===engagementId);
  const verified=(findings?.verified||[]).filter(x=>x.engagement_id===engagementId&&x.status==="verified");
  for(const candidate of candidates){
    const id=`candidate:${candidate.id}`;
    addNode(id,candidate.title||candidate.id,"candidate",{source:"candidate_findings",status:candidate.status,run_id:candidate.run_id,id:candidate.id});
    if(!edges.some(x=>x.target_id===id))addEdge(root,id,"engagement_candidate","candidate.engagement_id");
    for(const evidence of candidate.evidence_ids||[]){const evidenceId=`evidence:${evidence}`;addNode(evidenceId,evidence,"evidence_reference",{source:"candidate.evidence_ids",verified:false});addEdge(id,evidenceId,"cites_evidence","candidate.evidence_ids")}
  }
  for(const finding of verified){
    const id=`finding:${finding.id}`,candidateId=`candidate:${finding.candidate_id}`;
    addNode(id,finding.title||finding.id,"canonical_result",{source:"canonical_findings",status:finding.status,severity:finding.severity,id:finding.id});
    if(seen.has(candidateId))addEdge(candidateId,id,"verified_promotion","canonical_findings.candidate_id");
    else addEdge(root,id,"verified_result","canonical_findings.engagement_id");
  }
  if(!campaigns.length&&!candidates.length&&!verified.length)notes.push("当前项目没有研究假设、候选或可信结果");
  return {engagement_id:engagementId,mode,root_id:root,nodes,edges,counts:{nodes:nodes.length,edges:edges.length},notes,boundary:"连线仅来自持久化 ID 关系；Evidence 引用不自动代表独立验证。"};
};
