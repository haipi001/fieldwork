from concurrent.futures import ThreadPoolExecutor

import final_core
from tests.test_final import client
from tests.test_v5_orchestration import campaign, group, task, runner, lease


def test_group_reserves_running_allowances_and_releases_unused_cost(client):
    cid = campaign(client)["id"]
    gid = group(client, cid, {"max_cost_micros": 10, "max_concurrency": 8})["id"]
    for index in range(8):
        task(client, cid, f"reserve-{index}", group_id=gid, budget={"max_cost_micros": 6})
    runner(client, concurrency=8)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(pool.map(lambda _: lease(client).json()["task"], range(8)))
    active = [item for item in claimed if item]
    assert len(active) == 1
    assert client.post(f"/api/v1/orchestration/tasks/{active[0]['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "succeeded", "usage": {"cost_micros": 4},
    }).status_code == 200
    second = lease(client).json()["task"]
    assert second is not None  # Remaining 6 fits exactly after settling 4.
    assert lease(client).json()["task"] is None
    assert client.post(f"/api/v1/orchestration/tasks/{second['id']}/complete", json={
        "runner_id": "runner-a", "outcome": "succeeded", "usage": {"cost_micros": 6},
    }).status_code == 200
    assert lease(client).json()["task"] is None
    assert client.get(f"/api/v1/orchestration/groups/{gid}").json()["usage"]["cost_micros"] == 10


def test_capped_group_cannot_lease_unbounded_task(client):
    cid = campaign(client)["id"]
    gid = group(client, cid, {"max_cost_micros": 10})["id"]
    task(client, cid, "unbounded", group_id=gid)
    runner(client)
    assert lease(client).json()["task"] is None


def test_retry_preserves_spend_and_rejects_old_completion_heartbeat_checkpoint(client):
    cid = campaign(client)["id"]
    gid = group(client, cid, {"max_cost_micros": 10})["id"]
    created = task(client, cid, "retry-budget", group_id=gid, budget={"max_cost_micros": 10})
    runner(client)
    first = lease(client).json()["task"]
    base = f"/api/v1/orchestration/tasks/{created['id']}"
    assert client.post(base + "/complete", json={"runner_id": "runner-a", "outcome": "failed",
        "usage": {"cost_micros": 6, "input_tokens": 10, "runtime_ms": 5}}).status_code == 200
    assert client.post(base + "/retry").status_code == 200
    second = lease(client).json()["task"]
    assert second["attempt"] == first["attempt"] + 1
    for action, body in (("complete", {"outcome": "succeeded"}), ("heartbeat", {}), ("checkpoints", {"checkpoint": {"late": True}})):
        for attempt in (None, first["attempt"]):
            assert client.post(base + "/" + action, json={"runner_id": "runner-a", "lease_attempt": attempt, **body}).status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_task_checkpoints WHERE task_id=?", (created["id"],)).fetchone()[0] == 0
    completed = client.post(base + "/complete", json={"runner_id": "runner-a", "lease_attempt": second["attempt"],
        "outcome": "succeeded", "usage": {"cost_micros": 5, "output_tokens": 8, "runtime_ms": 6}}).json()
    assert completed["status"] == "budget_exhausted"
    assert completed["usage"] == {"cost_micros": 11, "input_tokens": 10, "output_tokens": 8, "runtime_ms": 11}
    assert client.get(f"/api/v1/orchestration/groups/{gid}").json()["usage"]["cost_micros"] == 11
