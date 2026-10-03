"""Eligible tasks must remain reachable behind stale queued work."""
import json

import pytest

import final_core
import v5_workers
from tests.test_final import client
from tests.test_v5_evolution_recovery import advance, seed


@pytest.mark.parametrize("executor", ["local", "general"])
def test_scheduler_finds_live_work_after_one_thousand_stale_tasks(client, executor):
    _, _, population = seed(client)
    live_ids = advance(client, population)["task_ids"]
    with final_core.connect() as db:
        original = dict(db.execute("SELECT * FROM agent_tasks WHERE id=?", (live_ids[0],)).fetchone())
        capsule = json.loads(original["context_capsule_json"])
        capsule["scope_snapshot_id"] = "previous-scope-snapshot"
        for index in range(1000):
            stale = {**original, "id": f"stale-queue-{index:04d}", "priority": 100,
                     "idempotency_key": f"stale-key-{index:04d}", "context_capsule_json": json.dumps(capsule)}
            db.execute(f"INSERT INTO agent_tasks ({','.join(stale)}) VALUES ({','.join('?' for _ in stale)})", tuple(stale.values()))
    if executor == "local":
        with final_core.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            leased = v5_workers._claim_local_task(db, population["id"])
            assert leased is not None and leased["id"] in live_ids
            assert v5_workers._claim_local_task(db, population["id"]) is None
    else:
        assert client.put("/api/v1/runners/scale-queue", json={
            "id": "scale-queue", "name": "Scale queue fixture", "kind": "worker",
            "capabilities": ["structured_evolver"], "labels": {"location": "local"},
            "max_concurrency": 1,
        }).status_code == 200
        leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "scale-queue"}).json()["task"]
        assert leased is not None and leased["id"] in live_ids
        assert client.post("/api/v1/orchestration/lease", json={"runner_id": "scale-queue"}).json()["task"] is None
    with final_core.connect() as db:
        assert db.execute("SELECT SUM(attempt) FROM agent_tasks WHERE id LIKE 'stale-queue-%'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE status='running'").fetchone()[0] == 1
