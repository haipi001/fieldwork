"""Verify persisted-ID graph links and project isolation in the browser."""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless=True)
    page = browser.new_page()
    page.goto("http://127.0.0.1:8011/v5")
    result = page.evaluate("""async () => {
      const urls=[];
      const request=async url=>{
        urls.push(url);
        if(url.endsWith('/engagements/eg-a/campaigns'))return [{id:'camp-a'}];
        if(url.endsWith('/campaigns/camp-a'))return {
          id:'camp-a',engagement_id:'eg-a',name:'Authentication',status:'active',
          hypotheses:[{id:'hyp-a',campaign_id:'camp-a',statement:'Missing authorization',evidence_ids:['ev-1'],counterevidence_ids:['ev-2']}],
          candidate_links:[{candidate_id:'cand-1',title:'Authorization candidate',status:'candidate'}]
        };
        if(url.endsWith('/research/campaigns/camp-a/graph/page'))return {campaign_id:'camp-a',nodes:[],edges:[],page:{has_more:false}};
        throw Error('Unexpected '+url);
      };
      const graph=await window.loadFieldworkResearchGraph({engagementId:'eg-a',mode:'traditional',findings:{
        candidates:[{id:'cand-1',engagement_id:'eg-a',title:'Authorization candidate',evidence_ids:['ev-1']},{id:'foreign',engagement_id:'eg-b',evidence_ids:['ev-3']}],
        verified:[{id:'find-1',candidate_id:'cand-1',engagement_id:'eg-a',status:'verified',title:'Verified authorization issue'},
                  {id:'find-foreign',engagement_id:'eg-b',status:'verified'}]
      },request});
      return {nodes:graph.nodes.map(x=>x.id),edges:graph.edges.map(x=>[x.source_id,x.target_id,x.relation_type]),urls};
    }""")
    assert result["nodes"] == ["engagement:eg-a", "campaign:camp-a", "hypothesis:hyp-a", "evidence:ev-1", "evidence:ev-2", "candidate:cand-1", "finding:find-1"]
    assert ["hypothesis:hyp-a", "evidence:ev-2", "cites_counterevidence"] in result["edges"]
    assert ["candidate:cand-1", "finding:find-1", "verified_promotion"] in result["edges"]
    assert len(result["urls"]) == 3
    browser.close()
    print("Research graph persisted relations, evidence polarity and engagement isolation passed")
