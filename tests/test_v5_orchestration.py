from concurrent.futures import ThreadPoolExecutor

import final_core
from tests.test_final import client, create_ready


def campaign(client, target="https://orchestration.example.test"):
    engagement = create_ready(client, target=target)
    response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Orchestration acceptance", "objective": "Coordinate bounded research",
    })
    assert response.status_code == 201, response.text
    return response.json()


def group(client, campaign_id, budget=None):
    response = client.post("/api/v1/orchestration/groups", json={
        "campaign_id": campaign_id, "role": "coordinator", "objective": "Bounded research",
        "budget": budget or {},
    })
    assert response.status_code == 201, response.text
    return response.json()


def task(client, campaign_id, key, *, group_id=None, priority=0, grants=None, budget=None,
         max_attempts=2, route=None):
    response = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": campaign_id, "group_id": group_id, "role": "researcher",
        "objective": f"Investigate {key}", "idempotency_key": key, "priority": priority,
        "tool_grants": grants or [], "budget": budget or {}, "max_attempts": max_attempts,
        "route_requirement": route or {},
    })
    assert response.status_code == 201, response.text
    return response.json()


def runner(client, runner_id="runner-a", *, capabilities=None, concurrency=1, labels=None):
    response = client.put(f"/api/v1/runners/{runner_id}", json={
        "id": runner_id, "name": runner_id, "kind": "worker",
        "capabilities": capabilities or [], "labels": labels or {},
        "max_concurrency": concurrency,
    })
    assert response.status_code == 200, response.text
    return response.json()


def lease(client, runner_id="runner-a", seconds=60):
    return client.post("/api/v1/orchestration/lease", json={
        "runner_id": runner_id, "lease_seconds": seconds,
    })


def test_task_idempotency_replays_exact_request_and_rejects_changed_payload(client):
    current = campaign(client)
    created = task(client, current["id"], "stable-key", priority=2)
    replay = task(client, current["id"], "stable-key", priority=2)
    assert replay["id"] == created["id"]
    changed = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": current["id"], "role": "researcher", "objective": "Changed",
        "idempotency_key": "stable-key", "priority": 2,
    })
    assert changed.status_code == 409


def test_operator_status_returns_real_tasks_groups_runners_and_counts(client):
    current = campaign(client)
    current_group = group(client, current["id"])
    created = task(client, current["id"], "operator-snapshot", group_id=current_group["id"])
    registered = runner(client, capabilities=["http"], concurrency=2)

    response = client.get("/api/v1/orchestration/status")
    assert response.status_code == 200
    value = response.json()
    assert any(item["id"] == created["id"] for item in value["agents"])
    assert any(item["id"] == current_group["id"] for item in value["groups"])
    assert any(item["id"] == registered["id"] for item in value["runners"])
    assert value["counts"]["tasks"] >= 1
    assert value["counts"]["active_tasks"] >= 1
    assert value["counts"]["online_runners"] >= 1


def test_operator_snapshot_does_not_recover_expired_leases(client):
    current = campaign(client)
    created = task(client, current["id"], "read-only-snapshot")
    runner(client)
    lease(client)
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (created["id"],))
        before = db.execute("SELECT COUNT(*) FROM v5_events").fetchone()[0]
        original = dict(db.execute("SELECT * FROM agent_tasks WHERE id=?", (created["id"],)).fetchone())
    for _ in range(2):
        response = client.get("/api/v1/orchestration/status")
        assert response.status_code == 200
        item = next(item for item in response.json()["agents"] if item["id"] == created["id"])
        assert item["status"] == original["status"]
        assert item["lease_owner"] == "runner-a"
    with final_core.connect() as db:
        assert dict(db.execute("SELECT * FROM agent_tasks WHERE id=?", (created["id"],)).fetchone()) == original
        assert db.execute("SELECT COUNT(*) FROM v5_events").fetchone()[0] == before
    assert client.post("/api/v1/orchestration/recover").json()["requeued"] == 1


def test_priority_capability_route_and_runner_concurrency(client):
    current = campaign(client)
    task(client, current["id"], "low", priority=1)
    task(client, current["id"], "missing-capability", priority=100, grants=["browser"])
    high = task(client, current["id"], "high", priority=20, grants=["http"])
    runner(client, capabilities=["http"], concurrency=1)
    first = lease(client).json()["task"]
    assert first["id"] == high["id"] and first["attempt"] == 1
    assert lease(client).json() == {"task": None, "reason": "runner_at_capacity"}
    heartbeat = client.post(f"/api/v1/orchestration/tasks/{high['id']}/heartbeat", json={
        "runner_id": "runner-a", "lease_seconds": 120,
    })
    assert heartbeat.status_code == 200 and heartbeat.json()["lease_owner"] == "runner-a"
    wrong = client.post(f"/api/v1/orchestration/tasks/{high['id']}/complete", json={
        "runner_id": "runner-b", "outcome": "succeeded",
    })
    assert wrong.status_code == 409


def test_pause_resume_cancel_and_group_propagation(client):
    current = campaign(client)
    current_group = group(client, current["id"])
    first = task(client, current["id"], "pausable", group_id=current_group["id"])
    assert client.post(f"/api/v1/orchestration/groups/{current_group['id']}/pause").status_code == 200
    assert client.get(f"/api/v1/orchestration/tasks/{first['id']}").json()["status"] == "paused"
    assert client.post(f"/api/v1/orchestration/groups/{current_group['id']}/resume").status_code == 200
    assert client.get(f"/api/v1/orchestration/tasks/{first['id']}").json()["status"] == "queued"
    assert client.post(f"/api/v1/orchestration/tasks/{first['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/v1/orchestration/tasks/{first['id']}/resume").status_code == 409


def test_expired_lease_recovers_then_fails_after_final_attempt(client):
    current = campaign(client)
    created = task(client, current["id"], "crash-recovery", max_attempts=1)
    runner(client)
    assert lease(client).json()["task"]["id"] == created["id"]
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (created["id"],))
    recovered = client.post("/api/v1/orchestration/recover")
    assert recovered.json() == {"requeued": 0, "failed": 1}
    value = client.get(f"/api/v1/orchestration/tasks/{created['id']}").json()
    assert value["status"] == "failed" and value["error"]["code"] == "lease_exhausted"
    assert client.get("/api/v1/runners/runner-a").json()["active_jobs"] == 0


def test_checkpoint_completion_and_task_budget_are_persistent(client):
    current = campaign(client)
    created = task(client, current["id"], "budgeted", budget={"max_tokens": 10})
    runner(client)
    lease(client)
    checkpoint = client.post(f"/api/v1/orchestration/tasks/{created['id']}/checkpoints", json={
        "runner_id": "runner-a", "checkpoint": {"cursor": 7},
    })
    assert checkpoint.status_code == 201
    completed = client.post(f"/api/v1/orchestration/tasks/{created['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "succeeded", "result": {"claim": "candidate only"},
        "usage": {"input_tokens": 8, "output_tokens": 5, "cost_micros": 12, "runtime_ms": 40},
    })
    assert completed.status_code == 200
    value = completed.json()
    assert value["status"] == "budget_exhausted" and value["usage"]["cost_micros"] == 12
    with final_core.connect() as db:
        assert db.execute("SELECT checkpoint_json FROM agent_task_checkpoints WHERE task_id=?", (created["id"],)).fetchone()[0] == '{"cursor":7}'
        assert not db.execute("SELECT 1 FROM research_nodes WHERE source_ref=?", (created["id"],)).fetchone()


def test_group_task_and_cost_budgets_gate_creation_and_future_leases(client):
    current = campaign(client)
    capped = group(client, current["id"], {"max_tasks": 1, "max_cost_micros": 10})
    first = task(client, current["id"], "only", group_id=capped["id"])
    denied = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": current["id"], "group_id": capped["id"], "role": "researcher",
        "objective": "Second", "idempotency_key": "second",
    })
    assert denied.status_code == 409
    runner(client)
    lease(client)
    completed = client.post(f"/api/v1/orchestration/tasks/{first['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "succeeded", "usage": {"cost_micros": 10},
    })
    assert completed.status_code == 200
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET status='queued' WHERE id=?", (first["id"],))
    assert lease(client).json() == {"task": None, "reason": "no_eligible_task"}


def test_atomic_lease_has_one_winner_for_one_task(client):
    current = campaign(client)
    created = task(client, current["id"], "one-winner")
    runner(client, "runner-a")
    runner(client, "runner-b")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda rid: lease(client, rid).json(), ["runner-a", "runner-b"]))
    winners = [item["task"] for item in results if item["task"]]
    assert len(winners) == 1 and winners[0]["id"] == created["id"]


def test_route_matching_failure_retry_and_cancelled_owner_guard(client):
    current = campaign(client)
    routed = task(client, current["id"], "darwin-only", route={"labels": {"os": "darwin"}})
    runner(client, labels={"os": "linux"})
    assert lease(client).json() == {"task": None, "reason": "no_eligible_task"}
    runner(client, labels={"os": "darwin"})
    assert lease(client).json()["task"]["id"] == routed["id"]
    failed = client.post(f"/api/v1/orchestration/tasks/{routed['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "failed", "error": {"code": "transient"},
    })
    assert failed.json()["status"] == "failed"
    assert client.post(f"/api/v1/orchestration/tasks/{routed['id']}/retry").json()["status"] == "queued"
    lease(client)
    assert client.post(f"/api/v1/orchestration/tasks/{routed['id']}/cancel").json()["status"] == "cancelled"
    rejected = client.post(f"/api/v1/orchestration/tasks/{routed['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "succeeded",
    })
    assert rejected.status_code == 409
