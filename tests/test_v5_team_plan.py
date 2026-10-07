"""Research teams are atomic, authorization-bound plans, not fabricated agents."""
import final_core
import pytest
import v5_orchestration
from tests.test_final import client, create_ready


def _setup(client):
    engagement = create_ready(client, target="https://team.example.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Team research", "objective": "Review actual evidence",
    }).json()
    run_id = "team-real-run"
    with final_core.connect() as db:
        db.execute(
            "INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, engagement["id"], "traditional", engagement["current_scope_snapshot_id"],
             engagement["current_policy_id"], "paused", "target", 0,
             None, None, None, None, final_core.utcnow()),
        )
    return engagement, campaign, run_id


def _body(campaign, run_id, count):
    return {
        "campaign_id": campaign["id"], "run_id": run_id,
        "objective": "Investigate the selected run evidence",
        "roles": [{"role": "researcher", "count": count}],
        "max_concurrency": min(count, 2), "max_cost_micros": count * 1000,
    }


def test_team_preview_is_read_only_and_commit_is_atomic_idempotent(client):
    engagement, campaign, run_id = _setup(client)
    for created_groups, count in enumerate((1, 2, 8)):
        body = _body(campaign, run_id, count)
        preview = client.post("/api/v1/orchestration/groups/team/preview", json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()["task_count"] == count
        with final_core.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM research_groups").fetchone()[0] == created_groups
        payload = {**body, "preview_hash": preview.json()["preview_hash"],
                   "idempotency_key": f"team-count-{count}"}
        created = client.post("/api/v1/orchestration/groups/team", json=payload)
        assert created.status_code == 201, created.text
        value = created.json()
        assert value["deduplicated"] is False
        assert len(value["tasks"]) == count
        assert value["group"]["budget"]["max_tasks"] == count
        assert sum(task["budget"]["max_cost_micros"] for task in value["tasks"]) == count * 1000
        assert all(task["status"] == "queued" and task["tool_grants"] == []
                   and task["context_capsule"]["scope_snapshot_id"] == engagement["current_scope_snapshot_id"]
                   and task["route_requirement"]["kind"] == "research-worker"
                   for task in value["tasks"])
        repeated = client.post("/api/v1/orchestration/groups/team", json=payload)
        assert repeated.status_code == 201 and repeated.json()["deduplicated"] is True
        assert repeated.json()["group"]["id"] == value["group"]["id"]
        assert len(repeated.json()["tasks"]) == count
        changed = client.post("/api/v1/orchestration/groups/team", json={
            **payload, "objective": "Investigate a different evidence set",
        })
        assert changed.status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_groups").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 11


def test_team_rejects_unconfirmed_scope_and_stale_preview(client):
    draft = client.post("/api/v1/engagements", json={
        "name": "Draft", "target": "https://draft.example.test", "mode": "traditional",
        "scope": {}, "policy": {},
    }).json()
    draft_campaign = client.post(f"/api/v1/engagements/{draft['id']}/campaigns", json={
        "name": "Draft campaign", "objective": "No authority yet",
    }).json()
    with final_core.connect() as db:
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   ("draft-run", draft["id"], "traditional", draft["current_scope_snapshot_id"],
                    draft["current_policy_id"], "queued", "target", 0,
                    None, None, None, None, final_core.utcnow()))
    assert client.post("/api/v1/orchestration/groups/team/preview",
                       json=_body(draft_campaign, "draft-run", 2)).status_code == 409

    _, campaign, run_id = _setup(client)
    body = _body(campaign, run_id, 2)
    preview = client.post("/api/v1/orchestration/groups/team/preview", json=body).json()
    assert client.post("/api/v1/orchestration/groups/team", json={
        **body, "preview_hash": "0" * 64, "idempotency_key": "stale-preview",
    }).status_code == 409
    assert client.post("/api/v1/orchestration/groups/team", json={
        **body, "preview_hash": preview["preview_hash"], "idempotency_key": "current-preview",
    }).status_code == 201
    with final_core.connect() as db:
        db.execute("UPDATE engagements_v2 SET current_policy_id=? WHERE id=(SELECT engagement_id FROM research_campaigns WHERE id=?)",
                   ("changed-policy", campaign["id"]))
    assert client.post("/api/v1/orchestration/groups/team", json={
        **body, "preview_hash": preview["preview_hash"], "idempotency_key": "current-preview",
    }).status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_groups WHERE campaign_id=?",
                          (campaign["id"],)).fetchone()[0] == 1


def test_team_budget_authority_and_run_boundaries(client):
    _, campaign, run_id = _setup(client)
    body = _body(campaign, run_id, 2)
    for changes in ({"max_concurrency": 3}, {"max_cost_micros": 1},
                    {"roles": [{"role": "researcher", "count": 100},
                               {"role": "explorer", "count": 1}]},
                    {"roles": [{"role": "researcher", "count": 1}] * 2}):
        assert client.post("/api/v1/orchestration/groups/team/preview",
                           json={**body, **changes}).status_code == 422
    foreign = create_ready(client, target="https://other-team.example.test")
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET engagement_id=? WHERE id=?", (foreign["id"], run_id))
    assert client.post("/api/v1/orchestration/groups/team/preview", json=body).status_code == 409
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET engagement_id=(SELECT engagement_id FROM research_campaigns WHERE id=?),synthetic=1 WHERE id=?",
                   (campaign["id"], run_id))
    assert client.post("/api/v1/orchestration/groups/team/preview", json=body).status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_groups").fetchone()[0] == 0


def test_team_partial_write_rolls_back(client, monkeypatch):
    _, campaign, run_id = _setup(client)
    body = _body(campaign, run_id, 8)
    preview = client.post("/api/v1/orchestration/groups/team/preview", json=body).json()
    original = v5_orchestration._emit
    def fail_after_task(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("injected team write failure")
    monkeypatch.setattr(v5_orchestration, "_emit", fail_after_task)
    with pytest.raises(RuntimeError, match="injected team write failure"):
        client.post("/api/v1/orchestration/groups/team", json={
            **body, "preview_hash": preview["preview_hash"], "idempotency_key": "rollback-plan",
        })
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_groups").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM app_metadata WHERE key='v5-team:rollback-plan'").fetchone()[0] == 0


def test_team_lists_real_usage_and_stale_authority_blocks_lease(client):
    engagement, campaign, run_id = _setup(client)
    body = _body(campaign, run_id, 2)
    preview = client.post("/api/v1/orchestration/groups/team/preview", json=body).json()
    created = client.post("/api/v1/orchestration/groups/team", json={
        **body, "preview_hash": preview["preview_hash"], "idempotency_key": "usage-plan",
    }).json()
    first = created["tasks"][0]
    with final_core.connect() as db:
        db.execute("INSERT INTO agent_task_usage VALUES(?,?,?,?,?,?)",
                   (first["id"], 11, 22, 123, 45, final_core.utcnow()))
    listed = client.get(f"/api/v1/orchestration/tasks?campaign_id={campaign['id']}&limit=1").json()
    all_items = listed["items"]
    if listed["page"]["next_cursor"]:
        all_items += client.get("/api/v1/orchestration/tasks", params={
            "campaign_id": campaign["id"], "limit": 1, "cursor": listed["page"]["next_cursor"],
        }).json()["items"]
    assert next(item for item in all_items if item["id"] == first["id"])["usage"]["cost_micros"] == 123
    groups = client.get(f"/api/v1/orchestration/groups?campaign_id={campaign['id']}").json()["items"]
    assert groups[0]["usage"] == {"task_count": 2, "cost_micros": 123}
    assert client.put("/api/v1/runners/team-runner", json={
        "id": "team-runner", "name": "Team runner", "kind": "research-worker",
        "capabilities": [], "max_concurrency": 2,
    }).status_code == 200
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='cancelled' WHERE id=?", (run_id,))
    assert client.post("/api/v1/orchestration/lease", json={"runner_id": "team-runner"}).json() == {
        "task": None, "reason": "no_eligible_task",
    }
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='paused' WHERE id=?", (run_id,))
    leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "team-runner"}).json()["task"]
    assert leased is not None
    with final_core.connect() as db:
        db.execute("UPDATE engagements_v2 SET current_policy_id='changed-policy' WHERE id=?", (engagement["id"],))
    assert client.post("/api/v1/orchestration/lease", json={"runner_id": "team-runner"}).json() == {
        "task": None, "reason": "no_eligible_task",
    }
    assert client.post(f"/api/v1/orchestration/tasks/{leased['id']}/heartbeat",
                       json={"runner_id": "team-runner"}).status_code == 409
