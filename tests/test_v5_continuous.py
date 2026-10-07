import final_core
from tests.test_final import client, create_ready


def setup_campaign(client):
    engagement = create_ready(client)
    response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Continuous fixture", "objective": "Track evidence deltas safely",
    })
    assert response.status_code == 201, response.text
    return engagement, response.json()["id"]


def add_node(client, campaign_id, node_type, title):
    response = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": node_type, "title": title,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def configure(client, campaign_id, **values):
    response = client.put(f"/api/v1/continuous-research/campaigns/{campaign_id}", json={
        "enabled": True, "min_interval_minutes": 60, "max_tasks_per_tick": 2, **values,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_policy_versions_are_bound_and_old_tasks_cancel_on_change(client):
    _, campaign_id = setup_campaign(client)
    add_node(client, campaign_id, "observation", "Policy revision signal")
    first = configure(client, campaign_id)
    assert first["configuration_revision"] == 1
    ticked = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert len(ticked["tasks_enqueued"]) == 1
    same = configure(client, campaign_id)
    assert same["configuration_revision"] == 1
    with final_core.connect() as db:
        assert db.execute("SELECT status FROM agent_tasks WHERE campaign_id=?", (campaign_id,)).fetchone()[0] == "queued"
    second = configure(client, campaign_id, max_tasks_per_tick=3)
    assert second["configuration_revision"] == 2
    assert second["configuration_sha256"] != first["configuration_sha256"]
    with final_core.connect() as db:
        assert db.execute("SELECT status FROM agent_tasks WHERE campaign_id=?", (campaign_id,)).fetchone()[0] == "cancelled"
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    renewed = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert len(renewed["tasks_enqueued"]) == 1
    history = client.get(f"/api/v1/continuous-research/campaigns/{campaign_id}/history").json()["items"]
    assert [item["revision"] for item in history] == [2, 1]
    assert history[1]["sha256"] == first["configuration_sha256"]


def test_bounded_tick_checkpoint_delta_local_routing_and_idempotency(client):
    engagement, campaign_id = setup_campaign(client)
    observation = add_node(client, campaign_id, "observation", "Signal requiring triage")
    claim = add_node(client, campaign_id, "claim", "Claim requiring challenge")
    evidence = add_node(client, campaign_id, "evidence", "Supporting evidence")
    counter = add_node(client, campaign_id, "counterevidence", "Conflicting evidence")
    for source, relation in ((evidence, "supports"), (counter, "contradicts")):
        response = client.post("/api/v1/research/edges", json={
            "campaign_id": campaign_id, "source_id": source, "target_id": claim,
            "relation_type": relation,
        })
        assert response.status_code == 201, response.text
    assert client.get(f"/api/v1/continuous-research/campaigns/{campaign_id}").json()["configured"] is False
    assert client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).status_code == 409
    configure(client, campaign_id, idle_only=False)
    first = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert first["status"] == "checkpointed" and first["new_observations"] == 1
    assert len(first["tasks_enqueued"]) == 2
    assert {task["kind"] for task in first["tasks_enqueued"]} == {"triage_observation", "challenge_claim"}
    assert first["verification_jobs"] == 0 and first["cloud_escalations"] == 0
    assert client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()["status"] == "not_due"
    with final_core.connect() as db:
        tasks = db.execute("SELECT * FROM agent_tasks WHERE campaign_id=?", (campaign_id,)).fetchall()
        assert len(tasks) == 2
        for task in tasks:
            assert '"location":"local"' in task["route_requirement_json"]
            assert engagement["current_scope_snapshot_id"] in task["context_capsule_json"]
            assert '"max_cost_micros":0' in task["budget_json"]
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    second = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert second["new_observations"] == 0 and not second["graph_changed"]
    assert second["verification_jobs"] == 1 and second["checkpoint_id"] != first["checkpoint_id"]
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE campaign_id=?", (campaign_id,)).fetchone()[0] == 3
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    third = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert third["tasks_enqueued"] == []


def test_budget_idle_and_scope_change_block_continuous_leases(client):
    engagement, campaign_id = setup_campaign(client)
    add_node(client, campaign_id, "observation", "Bounded signal")
    configure(client, campaign_id, daily_budget_micros=100)
    with final_core.connect() as db:
        db.execute("INSERT INTO runtime_usage(campaign_id,task_id,provider_id,route,input_tokens,output_tokens,cost_micros,created_at) VALUES(?,?,?,?,?,?,?,?)",
                   (campaign_id, None, None, "cloud", 0, 0, 100, final_core.utcnow()))
    blocked = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert blocked["status"] == "budget_exhausted"
    configure(client, campaign_id, daily_budget_micros=200)
    with final_core.connect() as db:
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    first = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert len(first["tasks_enqueued"]) == 1
    with final_core.connect() as db:
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    assert client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()["status"] == "busy"
    runner = client.put("/api/v1/runners/continuous-local", json={
        "id": "continuous-local", "name": "Local research worker", "labels": {"location": "local"},
    })
    assert runner.status_code == 200, runner.text
    changed_scope_id = "changed-continuous-scope"
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO scope_snapshots VALUES(?,?,?,?,?,?,?,?)", (
            changed_scope_id, engagement["id"], 2, "traditional", "{}", "fixture", now, now,
        ))
        db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?", (changed_scope_id, engagement["id"]))
        db.execute("UPDATE continuous_research_state SET next_tick_at='2000-01-01T00:00:00+00:00' WHERE campaign_id=?", (campaign_id,))
    lease = client.post("/api/v1/orchestration/lease", json={"runner_id": "continuous-local"})
    assert lease.status_code == 200 and lease.json()["task"] is None
    with final_core.connect() as db:
        old_task_id = db.execute("SELECT id FROM agent_tasks WHERE campaign_id=? ORDER BY created_at LIMIT 1", (campaign_id,)).fetchone()[0]
    assert client.post("/api/v1/runtime/routes", json={
        "task_id": old_task_id, "campaign_id": campaign_id, "task_type": "hypothesis_exploration",
        "sensitivity": "public", "mode": "cloud", "budget_remaining_micros": 1_000_000,
    }).status_code == 409
    assert client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).status_code == 409
    configure(client, campaign_id, daily_budget_micros=200)
    renewed = client.post(f"/api/v1/continuous-research/campaigns/{campaign_id}/tick", json={}).json()
    assert renewed["status"] == "checkpointed" and len(renewed["tasks_enqueued"]) == 1
    with final_core.connect() as db:
        statuses = [row["status"] for row in db.execute(
            "SELECT status FROM agent_tasks WHERE campaign_id=? ORDER BY created_at,id", (campaign_id,),
        )]
    assert statuses.count("cancelled") == 1 and statuses.count("queued") == 1
    disabled = client.put(f"/api/v1/continuous-research/campaigns/{campaign_id}", json={"enabled": False})
    assert disabled.status_code == 200
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE campaign_id=? AND status='queued'", (campaign_id,)).fetchone()[0] == 0


def test_due_tick_requires_explicit_enablement_and_confirmed_scope(client):
    engagement, campaign_id = setup_campaign(client)
    assert client.post("/api/v1/continuous-research/due/tick").json()["processed"] == []
    with final_core.connect() as db:
        db.execute("UPDATE scope_snapshots SET confirmed_at=NULL WHERE id=?", (engagement["current_scope_snapshot_id"],))
    response = client.put(f"/api/v1/continuous-research/campaigns/{campaign_id}", json={"enabled": True})
    assert response.status_code == 409
    with final_core.connect() as db:
        db.execute("UPDATE scope_snapshots SET confirmed_at=? WHERE id=?", (final_core.utcnow(), engagement["current_scope_snapshot_id"]))
    configure(client, campaign_id)
    processed = client.post("/api/v1/continuous-research/due/tick").json()["processed"]
    assert len(processed) == 1 and processed[0]["campaign_id"] == campaign_id
    assert processed[0]["trigger"] == "scheduled" and processed[0]["checkpoint_id"]


def test_policy_rejects_unimplemented_cloud_and_non_local_routing(client):
    _, campaign_id = setup_campaign(client)
    url = f"/api/v1/continuous-research/campaigns/{campaign_id}"
    assert client.put(url, json={"enabled": True, "local_first": False}).status_code == 422
    assert client.put(url, json={
        "enabled": True, "cloud_escalation": True,
        "daily_budget_micros": 1000, "max_cloud_tasks_per_tick": 1,
    }).status_code == 422
    assert client.get(url).json()["configured"] is False
