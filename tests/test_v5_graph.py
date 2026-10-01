import json

import capability_registry
import final_core
import traditional_tools
import web3_analysis
from v5_graph import project_web3_run_facts
from tests.test_final import client, create_ready


def campaign(client, target="https://graph.example.test"):
    engagement = create_ready(client, target=target)
    created = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Graph acceptance",
        "objective": "Build an evidence-linked research graph",
    })
    assert created.status_code == 201, created.text
    return engagement, created.json()


def node(client, campaign_id, kind, title, **values):
    response = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": kind, "title": title, **values,
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_node_edge_crud_pagination_and_campaign_isolation(client):
    _, first = campaign(client)
    _, second = campaign(client, "https://other-graph.example.test")
    question = node(client, first["id"], "question", "Which object boundary applies?")
    claim = node(client, first["id"], "claim", "Object may cross tenant boundary", confidence=.4)
    other = node(client, second["id"], "claim", "Other campaign claim")
    edge = client.post("/api/v1/research/edges", json={
        "campaign_id": first["id"], "source_id": question["id"], "target_id": claim["id"],
        "relation_type": "decomposes_to", "attributes": {"reason": "fixture"},
    })
    assert edge.status_code == 201 and edge.json()["attributes"]["reason"] == "fixture"
    duplicate = client.post("/api/v1/research/edges", json={
        "campaign_id": first["id"], "source_id": question["id"], "target_id": claim["id"],
        "relation_type": "decomposes_to", "attributes": {},
    })
    assert duplicate.status_code == 201 and duplicate.json()["id"] == edge.json()["id"]
    rejected = client.post("/api/v1/research/edges", json={
        "campaign_id": first["id"], "source_id": question["id"], "target_id": other["id"],
        "relation_type": "related_to",
    })
    assert rejected.status_code == 409
    updated = client.patch(f"/api/v1/research/nodes/{claim['id']}", json={
        "status": "testing", "confidence": .55, "attributes": {"review": "required"},
    })
    assert updated.status_code == 200 and updated.json()["status"] == "testing"
    assert updated.json()["attributes"] == {"review": "required"}
    edge_updated = client.patch(f"/api/v1/research/edges/{edge.json()['id']}", json={"attributes": {"rank": 1}})
    assert edge_updated.json()["attributes"] == {"rank": 1}
    page = client.get(f"/api/v1/research/campaigns/{first['id']}/graph?limit=1&offset=0").json()
    assert page["page"] == {"limit": 1, "offset": 0, "total_nodes": 2, "total_edges": 1, "has_more": True}
    assert len(page["nodes"]) == 1
    assert client.delete(f"/api/v1/research/nodes/{question['id']}").status_code == 409
    assert client.delete(f"/api/v1/research/edges/{edge.json()['id']}").status_code == 204
    assert client.delete(f"/api/v1/research/nodes/{question['id']}").status_code == 204
    assert client.get(f"/api/v1/research/nodes/{question['id']}").status_code == 404


def test_canonical_result_requires_receipt_bound_to_same_claim_and_campaign(client):
    _, first = campaign(client)
    claim = node(client, first["id"], "claim", "Verified claim candidate")
    payload = {
        "campaign_id": first["id"], "node_type": "canonical_result", "title": "Canonical result",
        "source_ref": claim["id"], "attributes": {"verification_receipt_id": "receipt-fixture"},
    }
    assert client.post("/api/v1/research/nodes", json=payload).status_code == 409
    with final_core.connect() as db:
        db.execute("INSERT INTO verification_receipts_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "receipt-fixture", first["id"], claim["id"], "verifier-a", "runner-a", "{}", "{}", "{}",
            '{"outcome":"verified"}', "[]", "[]", "fixture-sha", final_core.utcnow(),
        ))
    # A legacy-looking row without an immutable request/task binding is not a V5 receipt.
    assert client.post("/api/v1/research/nodes", json=payload).status_code == 409


def test_traditional_tool_result_projects_observation_and_artifact_to_active_campaign_only(client):
    engagement, current = campaign(client)
    _, foreign = campaign(client, "https://foreign-graph.example.test")
    paused = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Paused research", "objective": "Do not receive new observations",
    }).json()
    timestamp = final_core.utcnow()
    with final_core.connect() as db:
        db.execute("UPDATE research_campaigns SET status='paused' WHERE id=?", (paused["id"],))
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "traditional-live-run", engagement["id"], "traditional", engagement["current_scope_snapshot_id"],
            engagement["current_policy_id"], "completed", "report", 0, timestamp, None, None, timestamp, timestamp,
        ))
    result = traditional_tools._persist_result(
        {"id": "traditional-live-run", "engagement_id": engagement["id"], "mode": "traditional"},
        "nuclei", capability_registry.ToolResultEnvelope(
            "nuclei", "completed", 0, "", "", False,
        ),
        [{"observation_type": "scanner.template_match", "subject": "https://graph.example.test/path",
          "summary": "Template signal", "confidence": .7, "structured": {"rule": "fixture"}}], [],
    )
    graph = client.get(f"/api/v1/research/campaigns/{current['id']}/graph").json()
    types = {node["node_type"] for node in graph["nodes"]}
    assert types == {"observation", "artifact"}
    observation = next(node for node in graph["nodes"] if node["node_type"] == "observation")
    artifact = next(node for node in graph["nodes"] if node["node_type"] == "artifact")
    assert observation["source_ref"] == result["observation_ids"][0]
    assert observation["status"] == "observed" and observation["source_type"] == "observation"
    assert artifact["source_ref"] == result["artifact_id"]
    assert artifact["attributes"]["content_copied"] is False
    assert graph["edges"][0]["source_id"] == observation["id"]
    assert graph["edges"][0]["target_id"] == artifact["id"]
    assert client.get(f"/api/v1/research/campaigns/{foreign['id']}/graph").json()["nodes"] == []
    assert client.get(f"/api/v1/research/campaigns/{paused['id']}/graph").json()["nodes"] == []
    client.post(f"/api/v1/research/campaigns/{current['id']}/bridge")
    bridged = client.get(f"/api/v1/research/campaigns/{current['id']}/graph").json()
    assert sum(node["source_ref"] == result["observation_ids"][0] for node in bridged["nodes"]) == 1
    assert sum(node["source_ref"] == result["artifact_id"] for node in bridged["nodes"]) == 1
    assert client.post(f"/api/v1/research/campaigns/{current['id']}/bridge").json()["created"] == {"nodes": 0, "edges": 0}


def test_web3_projection_preserves_deployment_context_and_evidence_polarity(client):
    engagement = create_ready(client, "web3", "0x3333333333333333333333333333333333333333")
    current = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Web3 analysis", "objective": "Trace compiler and Forge evidence",
    }).json()
    foreign = create_ready(client, "web3", "0x4444444444444444444444444444444444444444")
    other = client.post(f"/api/v1/engagements/{foreign['id']}/campaigns", json={
        "name": "Other contract", "objective": "Isolated deployment",
    }).json()
    now = final_core.utcnow()
    with final_core.connect() as db:
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "web3-projection-run", engagement["id"], "web3", engagement["current_scope_snapshot_id"],
            engagement["current_policy_id"], "completed", "report", 0, now, None, None, now, now,
        ))
        db.execute("INSERT INTO program_snapshots VALUES(?,?,?,?,?,?,?)", (
            "alignment-graph", engagement["id"], 1, "immunefi",
            json.dumps({"kind": "deployment_alignment", "chain_id": 31337, "block_number": 42,
                        "contract_address": engagement["normalized_target"], "chain_runtime_sha256": "abc123",
                        "rpc_url": "https://secret-rpc.invalid"}), "/private/source", now,
        ))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            "web3-graph-artifact", "web3-projection-run", "web3.source_model", "/private/artifact.json",
            "sha-fixture", "application/json", 1, now,
        ))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            "web3-graph-observation", "web3-projection-run", engagement["id"], "web3",
            "web3.property.counterexample", "VaultInvariant", "Forge counterexample", .95,
            "forge-test", "web3-graph-artifact", now,
        ))
        for eid, polarity in (("web3-support", "supporting"), ("web3-counter", "counter")):
            db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                eid, "web3-graph-observation", "web3-projection-run", "forge_counterexample",
                f"{polarity} evidence", "web3-graph-artifact", polarity, now,
            ))
        first = project_web3_run_facts(db, engagement["id"], "web3-projection-run",
                                       "web3-graph-artifact", ["web3-graph-observation"])
        second = project_web3_run_facts(db, engagement["id"], "web3-projection-run",
                                        "web3-graph-artifact", ["web3-graph-observation"])
    assert first == {"nodes": 4, "edges": 5}
    assert second == {"nodes": 0, "edges": 0}
    graph = client.get(f"/api/v1/research/campaigns/{current['id']}/graph").json()
    assert {n["node_type"] for n in graph["nodes"]} == {"artifact", "observation", "evidence", "counterevidence"}
    assert all(n["attributes"]["deployment_context"]["chain_id"] == 31337 for n in graph["nodes"])
    assert all(n["attributes"]["deployment_context"]["block_number"] == 42 for n in graph["nodes"])
    assert "secret-rpc" not in json.dumps(graph) and "/private/" not in json.dumps(graph)
    assert client.get(f"/api/v1/research/campaigns/{other['id']}/graph").json()["nodes"] == []


def test_web3_source_inspection_emits_graph_observations_via_api(client, tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "Vault.sol").write_text("pragma solidity ^0.8.24; contract Vault { function ping() external pure returns(uint) { return 1; } }")
    engagement = create_ready(client, "web3", str(tmp_path))
    current = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Compiler review", "objective": "Map source observations",
    }).json()
    run = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()
    monkeypatch.setattr(web3_analysis, "run_forge_build", lambda _root: {"status": "not_run"})
    response = client.post("/api/v1/web3/source/inspect", json={
        "engagement_id": engagement["id"], "run_id": run["id"], "source_path": str(tmp_path),
        "source_commit": "fixture-commit", "deployed_address": "0x3333333333333333333333333333333333333333",
    })
    assert response.status_code == 200, response.text
    graph = client.get(f"/api/v1/research/campaigns/{current['id']}/graph").json()
    observation = next(node for node in graph["nodes"] if node["source_ref"] == response.json()["observation_id"])
    assert observation["node_type"] == "observation" and observation["status"] == "observed"
    assert observation["attributes"]["deployment_context"]["source_commit"] == "fixture-commit"
    assert observation["attributes"]["deployment_context"]["deployed_address"] == "0x3333333333333333333333333333333333333333"
    assert not any(node["node_type"] in {"claim", "canonical_result"} for node in graph["nodes"])


def test_explicit_bridge_is_idempotent_provenance_only_and_never_promotes(client):
    engagement, current = campaign(client)
    timestamp = final_core.utcnow()
    run_id, observation_id, artifact_id, evidence_id = "run-bridge", "obs-bridge", "artifact-bridge", "evidence-bridge"
    candidate_id, hypothesis_id = "candidate-bridge", "hypothesis-bridge"
    with final_core.connect() as db:
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            run_id, engagement["id"], "traditional", engagement["current_scope_snapshot_id"],
            engagement["current_policy_id"], "completed", "report", 0, timestamp, None, None, timestamp, timestamp,
        ))
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run_id, engagement["id"], "traditional", "authorization", "/objects/1",
            "Observed owner mismatch", .8, "fixture", None, timestamp,
        ))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, run_id, "response", "file:///private/secret-response.bin", "abc123",
            "application/octet-stream", 1, timestamp,
        ))
        db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
            evidence_id, observation_id, run_id, "authorization", "Owner mismatch evidence",
            artifact_id, "supporting", timestamp,
        ))
        db.execute("INSERT INTO research_hypotheses VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            hypothesis_id, current["id"], None, "fingerprint", "access_control", "Tenant boundary may fail",
            "testing", 10, json.dumps([evidence_id]), "[]", 1, timestamp, "Run negative control", timestamp, timestamp,
        ))
        db.execute("INSERT INTO candidate_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            candidate_id, run_id, engagement["id"], "traditional", "Cross-tenant object read", "access_control",
            "/objects/1", "Another tenant may read the object", "verified", json.dumps([evidence_id]), timestamp, timestamp,
        ))
        db.execute("INSERT INTO campaign_candidate_links VALUES(?,?,?,?,?,?,?)", (
            "link-bridge", current["id"], "iteration-bridge", "workflow-bridge", hypothesis_id, candidate_id, timestamp,
        ))
        db.execute("INSERT INTO canonical_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "finding-bridge", candidate_id, engagement["id"], "traditional", "Legacy verified finding",
            "access_control", "high", "/objects/1", "Legacy impact", "legacy eligible", "{}",
            json.dumps([evidence_id]), "verified", timestamp, timestamp,
        ))
        db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", (
            "entity-a", engagement["id"], "endpoint", "endpoint-a", "Endpoint A", "{}", timestamp,
        ))
        db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?)", (
            "entity-b", engagement["id"], "object", "object-b", "Object B", "{}", timestamp,
        ))
        db.execute("INSERT INTO relationships VALUES(?,?,?,?,?,?,?)", (
            "relationship-a", engagement["id"], "entity-a", "entity-b", "calls", "[]", timestamp,
        ))
    first = client.post(f"/api/v1/research/campaigns/{current['id']}/bridge")
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["created"]["nodes"] >= 8 and result["automatic_promotion"] is False
    assert result["artifacts_copied"] is False
    second = client.post(f"/api/v1/research/campaigns/{current['id']}/bridge").json()
    assert second["created"] == {"nodes": 0, "edges": 0}
    graph = client.get(f"/api/v1/research/campaigns/{current['id']}/graph?limit=1000").json()
    serialized = json.dumps(graph)
    assert "file:///private/secret-response.bin" not in serialized
    assert not [item for item in graph["nodes"] if item["node_type"] == "canonical_result"]
    legacy = next(item for item in graph["nodes"] if item["source_type"] == "canonical_finding")
    assert legacy["node_type"] == "claim"
    assert legacy["attributes"]["canonical_result_eligible"] is False
    artifact = next(item for item in graph["nodes"] if item["source_type"] == "artifact")
    assert artifact["attributes"]["content_copied"] is False and artifact["source_ref"] == artifact_id
