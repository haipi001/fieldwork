"""Cross-pollination and research population acceptance boundaries."""
import final_core
import v5_workers
from tests.test_final import client, create_ready


def _campaign(client):
    engagement = create_ready(client)
    result = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Evolution fixture", "objective": "Compare bounded research hypotheses",
    })
    assert result.status_code == 201, result.text
    return engagement, result.json()["id"]


def _group(client, campaign_id, role="specialist", budget=None):
    result = client.post("/api/v1/orchestration/groups", json={
        "campaign_id": campaign_id, "role": role, "objective": "Review claims",
        "budget": budget or {},
    })
    assert result.status_code == 201, result.text
    return result.json()


def _node(client, campaign_id, kind, title, attrs=None):
    result = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": kind, "title": title,
        "attributes": attrs or {},
    })
    assert result.status_code == 201, result.text
    return result.json()


def _edge(client, campaign_id, source, target, relation):
    result = client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": source, "target_id": target,
        "relation_type": relation,
    })
    assert result.status_code == 201, result.text
    return result.json()


def test_bounded_capsule_transfer_creates_local_task_and_preserves_counterevidence(client):
    _, cid = _campaign(client)
    source, target = _group(client, cid), _group(client, cid)
    claim = _node(client, cid, "claim", "A bounded authorization hypothesis",
                  {"group_id": source["id"], "secret": "do-not-transfer"})
    evidence = _node(client, cid, "evidence", "Supporting observation")
    counter = _node(client, cid, "counterevidence", "Counterexample")
    question = _node(client, cid, "open_question", "Does the control hold?")
    _edge(client, cid, evidence["id"], claim["id"], "supports")
    _edge(client, cid, counter["id"], claim["id"], "contradicts")
    _edge(client, cid, question["id"], claim["id"], "related_to")
    payload = {"campaign_id": cid, "source_group_id": source["id"],
               "target_group_id": target["id"], "source_claim_id": claim["id"],
               "idempotency_key": "first-transfer"}
    response = client.post("/api/v1/evolution/transfers", json=payload)
    assert response.status_code == 201, response.text
    transfer = response.json()
    assert transfer["current"] is True
    capsule = transfer["capsule"]
    assert capsule["claim_id"] == claim["id"]
    assert capsule["evidence_refs"] == [evidence["id"]]
    assert capsule["counterevidence_refs"] == [counter["id"]]
    assert capsule["weaknesses"][0]["id"] == question["id"]
    assert "do-not-transfer" not in str(transfer)
    assert client.post("/api/v1/evolution/transfers", json=payload).json()["id"] == transfer["id"]
    assert client.post("/api/v1/evolution/transfers", json={**payload,
           "idempotency_key": "equivalent-transfer"}).json()["id"] == transfer["id"]
    task = client.get(f"/api/v1/orchestration/tasks/{transfer['target_task_id']}").json()
    assert task["group_id"] == target["id"] and task["role"] == "specialist"
    assert task["tool_grants"] == [] and task["context_capsule"]["transfer_id"] == transfer["id"]
    runner = client.put("/api/v1/runners/capsule-local", json={
        "id": "capsule-local", "name": "Capsule local", "kind": "worker",
        "labels": {"location": "local"},
    })
    assert runner.status_code == 200
    leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "capsule-local"})
    assert leased.status_code == 200 and leased.json()["task"]["id"] == task["id"]
    completed = client.post(f"/api/v1/orchestration/tasks/{task['id']}/complete", json={
        "runner_id": "capsule-local", "outcome": "succeeded",
        "result": {"assessment": "needs independent verification"},
    })
    assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
    assert client.get(f"/api/v1/evolution/transfers?campaign_id={cid}").json()["items"][0]["id"] == transfer["id"]
    assert not any(node["node_type"] == "canonical_result" for node in
                   client.get(f"/api/v1/research/campaigns/{cid}/graph").json()["nodes"])


def test_transfer_rejects_unattributed_claim_and_stale_scope(client):
    engagement, cid = _campaign(client)
    source, target = _group(client, cid), _group(client, cid)
    claim = _node(client, cid, "claim", "Unattributed hypothesis")
    payload = {"campaign_id": cid, "source_group_id": source["id"],
               "target_group_id": target["id"], "source_claim_id": claim["id"],
               "idempotency_key": "unattributed"}
    assert client.post("/api/v1/evolution/transfers", json=payload).status_code == 409
    attributed = _node(client, cid, "claim", "Attributable hypothesis", {"group_id": source["id"]})
    payload["source_claim_id"] = attributed["id"]
    response = client.post("/api/v1/evolution/transfers", json=payload)
    assert response.status_code == 201, response.text
    transfer = response.json()
    new_counter = _node(client, cid, "counterevidence", "New counterexample")
    _edge(client, cid, new_counter["id"], attributed["id"], "contradicts")
    assert client.get(f"/api/v1/evolution/transfers/{transfer['id']}").json()["current"] is False
    renewed = client.post("/api/v1/evolution/transfers", json={**payload,
                          "idempotency_key": "renewed-after-graph-change"})
    assert renewed.status_code == 201 and renewed.json()["id"] != transfer["id"]
    assert renewed.json()["current"] is True
    alternate = create_ready(client, target="https://changed-scope.example.test")
    with final_core.connect() as db:
        db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?",
                   (alternate["current_scope_snapshot_id"], engagement["id"]))
    assert client.get(f"/api/v1/evolution/transfers/{transfer['id']}").json()["current"] is False
    runner = client.put("/api/v1/runners/transfer-local", json={
        "id": "transfer-local", "name": "Transfer local", "kind": "worker",
        "labels": {"location": "local"},
    })
    assert runner.status_code == 200
    leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "transfer-local"})
    assert leased.status_code == 200 and leased.json()["task"] is None


def test_population_rank_select_lineage_and_stale_graph(client):
    _, cid = _campaign(client)
    group = _group(client, cid)
    weak = _node(client, cid, "claim", "Weak hypothesis", {"group_id": group["id"]})
    strong = _node(client, cid, "claim", "Testable supported hypothesis",
                   {"group_id": group["id"], "verification_contract_sha256": "a" * 64})
    evidence = _node(client, cid, "evidence", "First-party observation")
    _edge(client, cid, evidence["id"], strong["id"], "supports")
    initial = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 1,
        "idempotency_key": "initial-ranked",
        "variants": [{"claim_node_id": weak["id"]}, {"claim_node_id": strong["id"]}],
    })
    assert initial.status_code == 201, initial.text
    population = initial.json()
    assert population["generation"] == 0 and population["current"] is True
    assert population["variants"][0]["claim_node_id"] == strong["id"]
    assert population["variants"][0]["selected"] == 1
    assert population["variants"][1]["selected"] == 0
    assert "no verification" in population["selection_basis"]
    rejected = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "parent_population_id": population["id"],
        "idempotency_key": "reject-unselected",
        "variants": [{"claim_node_id": weak["id"],
                      "parent_variant_ids": [population["variants"][1]["id"]]},
                     {"claim_node_id": strong["id"],
                      "parent_variant_ids": [population["variants"][0]["id"]]}],
    })
    assert rejected.status_code == 409
    child_a = _node(client, cid, "claim", "Refined testable hypothesis", {"group_id": group["id"]})
    child_b = _node(client, cid, "claim", "Alternative refined hypothesis", {"group_id": group["id"]})
    _edge(client, cid, child_a["id"], strong["id"], "derived_from")
    _edge(client, cid, child_b["id"], strong["id"], "derived_from")
    parent_id = population["variants"][0]["id"]
    next_payload = {"campaign_id": cid, "group_id": group["id"],
                    "parent_population_id": population["id"], "selection_count": 1,
                    "idempotency_key": "second-ranked",
                    "variants": [{"claim_node_id": child_a["id"], "parent_variant_ids": [parent_id]},
                                 {"claim_node_id": child_b["id"], "parent_variant_ids": [parent_id]}]}
    next_result = client.post("/api/v1/evolution/populations", json=next_payload)
    assert next_result.status_code == 201, next_result.text
    next_population = next_result.json()
    assert next_population["generation"] == 1 and next_population["current"] is True
    assert all(variant["parent_variant_ids"] == [parent_id] for variant in next_population["variants"])
    assert client.post("/api/v1/evolution/populations", json=next_payload).json()["id"] == next_population["id"]
    assert len(client.get(f"/api/v1/evolution/populations?campaign_id={cid}").json()["items"]) == 2
    new_counter = _node(client, cid, "counterevidence", "New contradiction")
    _edge(client, cid, new_counter["id"], strong["id"], "contradicts")
    assert client.get(f"/api/v1/evolution/populations/{population['id']}").json()["current"] is False
    assert client.get(f"/api/v1/evolution/populations/{next_population['id']}").json()["current"] is False
    reseed = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "idempotency_key": "reseed-after-stale-parent",
        "selection_count": 1,
        "variants": [{"claim_node_id": child_a["id"]}, {"claim_node_id": child_b["id"]}],
    })
    assert reseed.status_code == 201, reseed.text
    assert reseed.json()["generation"] == 2 and reseed.json()["parent_population_id"] is None
    assert reseed.json()["current"] is True
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    assert not any(item["node_type"] == "canonical_result" for item in graph["nodes"])


def test_population_rejects_unbacked_lineage_and_is_immutable(client):
    _, cid = _campaign(client)
    group = _group(client, cid)
    first = _node(client, cid, "claim", "First idea", {"group_id": group["id"]})
    second = _node(client, cid, "claim", "Second idea", {"group_id": group["id"]})
    initial = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 1,
        "idempotency_key": "first-lineage",
        "variants": [{"claim_node_id": first["id"]}, {"claim_node_id": second["id"]}],
    })
    assert initial.status_code == 201, initial.text
    population = initial.json()
    parent = population["variants"][0]
    child_a = _node(client, cid, "claim", "Novel child", {"group_id": group["id"]})
    child_b = _node(client, cid, "claim", "Another child", {"group_id": group["id"]})
    payload = {"campaign_id": cid, "group_id": group["id"],
               "parent_population_id": population["id"], "selection_count": 1,
               "idempotency_key": "second-lineage",
               "variants": [{"claim_node_id": child_a["id"], "parent_variant_ids": [parent["id"]]},
                            {"claim_node_id": child_b["id"], "parent_variant_ids": [parent["id"]]}]}
    assert client.post("/api/v1/evolution/populations", json=payload).status_code == 409
    _edge(client, cid, child_a["id"], parent["claim_node_id"], "derived_from")
    _edge(client, cid, child_b["id"], parent["claim_node_id"], "derived_from")
    created = client.post("/api/v1/evolution/populations", json=payload)
    assert created.status_code == 201, created.text
    changed = client.post("/api/v1/evolution/populations", json={**payload, "selection_count": 2})
    assert changed.status_code == 409
    with final_core.connect() as db:
        try:
            db.execute("UPDATE research_variants_v5 SET score=1 WHERE id=?", (created.json()["variants"][0]["id"],))
        except Exception as error:
            assert "immutable" in str(error)
        else:
            raise AssertionError("evaluated research variant must be immutable")


def test_evolver_tasks_preserve_counterevidence_and_collect_next_generation(client):
    _, cid = _campaign(client)
    group = _group(client, cid)
    first = _node(client, cid, "claim", "First scoped research hypothesis",
                  {"group_id": group["id"], "scope": "fixture scope"})
    second = _node(client, cid, "claim", "Second scoped research hypothesis",
                   {"group_id": group["id"], "scope": "fixture scope"})
    counter = _node(client, cid, "counterevidence", "Existing negative control")
    _edge(client, cid, counter["id"], first["id"], "contradicts")
    population = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 2,
        "idempotency_key": "evolver-seed",
        "variants": [{"claim_node_id": first["id"]}, {"claim_node_id": second["id"]}],
    }).json()
    advanced = client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    assert advanced.status_code == 200, advanced.text
    assert len(advanced.json()["task_ids"]) == 3 and advanced.json()["created"] == 3
    replay = client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    assert replay.json()["task_ids"] == advanced.json()["task_ids"] and replay.json()["created"] == 0
    assert client.post(f"/api/v1/evolution/populations/{population['id']}/collect").status_code == 409
    runner = client.put("/api/v1/runners/evolver-local", json={
        "id": "evolver-local", "name": "Evolver local", "kind": "worker",
        "labels": {"location": "local"}, "capabilities": ["structured_evolver"],
    })
    assert runner.status_code == 200
    proposed = []
    for index in range(3):
        leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "evolver-local"})
        assert leased.status_code == 200 and leased.json()["task"] is not None
        task = leased.json()["task"]
        capsule = task["context_capsule"]
        assert task["role"] == "evolver"
        assert client.post(f"/api/v1/orchestration/tasks/{task['id']}/complete", json={
            "runner_id": "evolver-local", "outcome": "succeeded", "result": {},
        }).status_code == 409
        output = {"mode": capsule["evolution_mode"],
                  "parent_variant_ids": capsule["parent_variant_ids"],
                  "statement": f"Novel bounded proposal {index} from selected parents",
                  "scope": "fixture scope", "evidence_ids": [],
                  "counterevidence_ids": capsule["counterevidence_ids"],
                  "limitations": ["Requires independent replay"],
                  "open_questions": ["Which negative control would refute it?"]}
        if counter["id"] in capsule["counterevidence_ids"]:
            denied = client.post(f"/api/v1/evolution/tasks/{task['id']}/result", json={
                "runner_id": "evolver-local", "output": {**output, "counterevidence_ids": []},
            })
            assert denied.status_code == 409
        result = client.post(f"/api/v1/evolution/tasks/{task['id']}/result", json={
            "runner_id": "evolver-local", "output": output,
        })
        assert result.status_code == 200, result.text
        proposed.append(result.json()["result"]["claim_node_id"])
    collected = client.post(f"/api/v1/evolution/populations/{population['id']}/collect")
    assert collected.status_code == 200, collected.text
    next_population = collected.json()
    assert next_population["generation"] == 1 and len(next_population["variants"]) == 5
    assert client.post(f"/api/v1/evolution/populations/{population['id']}/collect").json()["id"] == next_population["id"]
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    drafts = [node for node in graph["nodes"] if node["id"] in proposed]
    assert len(drafts) == 3 and all(node["status"] == "draft" for node in drafts)
    assert not any(node["node_type"] == "canonical_result" for node in graph["nodes"])
    assert any(edge["source_id"] == counter["id"] and edge["target_id"] in proposed
               and edge["relation_type"] == "contradicts" for edge in graph["edges"])


def test_local_evolver_tick_uses_structured_contract_and_stale_parent_blocks_completion(client, monkeypatch):
    _, cid = _campaign(client)
    group = _group(client, cid)
    first = _node(client, cid, "claim", "First idea for local evolution", {"group_id": group["id"]})
    second = _node(client, cid, "claim", "Second idea for local evolution", {"group_id": group["id"]})
    population = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 1,
        "idempotency_key": "local-evolver-seed",
        "variants": [{"claim_node_id": first["id"]}, {"claim_node_id": second["id"]}],
    }).json()
    unrelated = client.post("/api/v1/workers/tasks", json={
        "campaign_id": cid, "role": "critic", "claim_ids": [first["id"]],
        "objective": "Independent critique outside the population", "idempotency_key": "unrelated-critic",
    })
    assert unrelated.status_code == 201, unrelated.text
    task_id = client.post(f"/api/v1/evolution/populations/{population['id']}/advance").json()["task_ids"][0]
    assert client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": cid, "role": "evolver", "objective": "Bypass", "idempotency_key": "bypass-evolver",
    }).status_code == 422
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "evolution-fixture-model", "llama_cpp", "Fixture local model", "http://127.0.0.1:9010",
            "fixture-model", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    def model(_provider, task, _nodes):
        capsule = v5_workers._load(task["context_capsule_json"], {})
        return ({"mode": capsule["evolution_mode"], "parent_variant_ids": capsule["parent_variant_ids"],
                 "statement": "A locally generated bounded draft proposal",
                 "scope": "fixture scope", "evidence_ids": [],
                 "counterevidence_ids": capsule["counterevidence_ids"],
                 "limitations": ["Needs independent verification"],
                 "open_questions": ["What would disprove this proposal?"]}, 10, 12, 20)
    monkeypatch.setattr(v5_workers, "_local_model_output", model)
    tick = client.post(f"/api/v1/workers/local/tick?limit=1&population_id={population['id']}")
    assert tick.status_code == 200 and tick.json()["completed"][0]["status"] == "succeeded", tick.text
    assert client.get(f"/api/v1/orchestration/tasks/{task_id}").json()["status"] == "succeeded"
    assert client.get(f"/api/v1/orchestration/tasks/{unrelated.json()['id']}").json()["status"] == "queued"
    assert client.post(f"/api/v1/evolution/populations/{population['id']}/collect").status_code == 200
    new_counter = _node(client, cid, "counterevidence", "New contradiction after collection")
    selected_claim = population["variants"][0]["claim_node_id"]
    _edge(client, cid, new_counter["id"], selected_claim, "contradicts")
    assert client.post(f"/api/v1/evolution/populations/{population['id']}/advance").status_code == 409


def test_evolver_in_flight_rejects_changed_parent_graph(client):
    _, cid = _campaign(client)
    group = _group(client, cid)
    first = _node(client, cid, "claim", "First mutation parent", {"group_id": group["id"]})
    second = _node(client, cid, "claim", "Second mutation parent", {"group_id": group["id"]})
    population = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 1,
        "idempotency_key": "in-flight-seed",
        "variants": [{"claim_node_id": first["id"]}, {"claim_node_id": second["id"]}],
    }).json()
    client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    client.put("/api/v1/runners/in-flight-evolver", json={
        "id": "in-flight-evolver", "name": "In-flight evolver", "kind": "worker",
        "labels": {"location": "local"}, "capabilities": ["structured_evolver"],
    })
    task = client.post("/api/v1/orchestration/lease", json={"runner_id": "in-flight-evolver"}).json()["task"]
    assert task and task["role"] == "evolver"
    parent_claim = task["context_capsule"]["parent_claim_ids"][0]
    counter = _node(client, cid, "counterevidence", "New negative evidence after lease")
    _edge(client, cid, counter["id"], parent_claim, "contradicts")
    denied = client.post(f"/api/v1/evolution/tasks/{task['id']}/result", json={
        "runner_id": "in-flight-evolver", "output": {
            "mode": "mutate", "parent_variant_ids": task["context_capsule"]["parent_variant_ids"],
            "statement": "Draft that must be rejected after new counterevidence",
            "scope": "fixture scope", "evidence_ids": [], "counterevidence_ids": [],
            "limitations": ["Stale parent"], "open_questions": ["What changed?"],
        },
    })
    assert denied.status_code == 409
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    assert not any(node["attributes"].get("producer_task_id") == task["id"] for node in graph["nodes"])


def test_evolver_advance_respects_group_task_budget_atomically(client):
    _, cid = _campaign(client)
    group = _group(client, cid, budget={"max_tasks": 0})
    first = _node(client, cid, "claim", "Budgeted first idea", {"group_id": group["id"]})
    second = _node(client, cid, "claim", "Budgeted second idea", {"group_id": group["id"]})
    population = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": 2,
        "idempotency_key": "budget-seed",
        "variants": [{"claim_node_id": first["id"]}, {"claim_node_id": second["id"]}],
    }).json()
    denied = client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    assert denied.status_code == 409 and "budget" in denied.text
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=?", (group["id"],)).fetchone()[0] == 0
