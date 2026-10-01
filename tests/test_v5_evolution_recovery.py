"""Durable evolution across process loss, lease retry, and group limits."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import final_core
import v5_orchestration
import v5_workers
from tests.test_final import client
from tests.test_v5_evolution import _campaign, _group, _node


def seed(client, budget=None, selection=2):
    _, cid = _campaign(client)
    group = _group(client, cid, budget=budget)
    claims = [_node(client, cid, "claim", f"Initial bounded research idea {index}",
                    {"group_id": group["id"], "scope": "fixture scope"})
              for index in range(max(2, selection))]
    response = client.post("/api/v1/evolution/populations", json={
        "campaign_id": cid, "group_id": group["id"], "selection_count": selection,
        "idempotency_key": "durable-evolution-seed",
        "variants": [{"claim_node_id": claim["id"]} for claim in claims],
    })
    assert response.status_code == 201, response.text
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "recovery-fixture-model", "llama_cpp", "Recovery fixture", "http://127.0.0.1:9010",
            "fixture-model", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    return cid, group, response.json()


def proposal(task):
    capsule = json.loads(task["context_capsule_json"])
    return {"mode": capsule["evolution_mode"], "parent_variant_ids": capsule["parent_variant_ids"],
            "statement": f"Bounded draft from task {task['id']}", "scope": "fixture scope",
            "evidence_ids": capsule["relevant_evidence_ids"],
            "counterevidence_ids": capsule["counterevidence_ids"],
            "limitations": ["Fixture proposal requires independent validation"],
            "open_questions": ["Which negative control would refute this draft?"]}


def advance(client, population):
    response = client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("budget", [{"max_concurrency": 0}, {"max_cost_micros": 0}])
def test_local_evolver_obeys_group_limits_before_model_call(client, monkeypatch, budget):
    _, _, population = seed(client, budget=budget)
    task_ids = advance(client, population)["task_ids"]
    calls = []
    monkeypatch.setattr(v5_workers, "_local_model_output", lambda *args: calls.append(args))
    result = client.post(f"/api/v1/workers/local/tick?population_id={population['id']}").json()
    assert result["completed"] == [] and calls == []
    assert all(client.get(f"/api/v1/orchestration/tasks/{tid}").json()["attempt"] == 0 for tid in task_ids)


def test_local_evolver_waits_for_another_runner_at_group_capacity(client, monkeypatch):
    _, _, population = seed(client, budget={"max_concurrency": 1})
    advance(client, population)
    assert client.put("/api/v1/runners/group-capacity-fixture", json={
        "id": "group-capacity-fixture", "name": "External group fixture", "kind": "worker",
        "labels": {"location": "local"}, "capabilities": ["structured_evolver"],
    }).status_code == 200
    leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "group-capacity-fixture"}).json()["task"]
    assert leased
    monkeypatch.setattr(v5_workers, "_local_model_output", lambda _provider, task, _nodes: (proposal(task), 10, 12, 20))
    url = f"/api/v1/workers/local/tick?population_id={population['id']}"
    assert client.post(url).json()["completed"] == []
    with final_core.connect() as db:
        task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (leased["id"],)).fetchone()
    response = client.post(f"/api/v1/evolution/tasks/{leased['id']}/result", json={
        "runner_id": "group-capacity-fixture", "lease_attempt": leased["attempt"], "output": proposal(task),
    })
    assert response.status_code == 200, response.text
    resumed = client.post(url).json()
    assert len(resumed["completed"]) == 2 and all(item["status"] == "succeeded" for item in resumed["completed"])


@pytest.mark.parametrize("late_failure", [False, True])
def test_old_local_attempt_cannot_complete_or_requeue_new_lease(client, monkeypatch, late_failure):
    _, _, population = seed(client, selection=1)
    task_id = advance(client, population)["task_ids"][0]
    replacement = []

    def interrupted_model(_provider, task, _nodes):
        with final_core.connect() as db:
            db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
        assert v5_orchestration.recover_expired_leases()["requeued"] == 1
        with final_core.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            replacement.append(v5_workers._claim_local_task(db, population["id"]))
        assert replacement[0]["attempt"] == 2
        if late_failure:
            raise TimeoutError("old model request finished after a replacement lease")
        return proposal(task), 10, 12, 20

    monkeypatch.setattr(v5_workers, "_local_model_output", interrupted_model)
    result = client.post(f"/api/v1/workers/local/tick?limit=1&population_id={population['id']}").json()
    current = client.get(f"/api/v1/orchestration/tasks/{task_id}").json()
    assert result["completed"][0]["status"] == "failed_attempt", result
    assert current["status"] == "running" and current["attempt"] == 2, current
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_task_usage WHERE task_id=?", (task_id,)).fetchone()[0] == 0
    base = {"runner_id": v5_workers.LOCAL_RUNNER_ID, "output": proposal(replacement[0])}
    # An unversioned retry result is ambiguous; callers must echo the leased attempt.
    assert client.post(f"/api/v1/evolution/tasks/{task_id}/result", json=base).status_code == 409
    accepted = client.post(f"/api/v1/evolution/tasks/{task_id}/result", json={**base, "lease_attempt": 2})
    assert accepted.status_code == 200, accepted.text


CHILD_WORKER = """
import json, os, sys
from pathlib import Path
import final_core, v5_workers
from tests.test_v5_evolution_recovery import proposal
final_core.DB = Path(sys.argv[1])
population_id, mode = sys.argv[2:]
if mode == 'crash':
    with final_core.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        task = v5_workers._claim_local_task(db, population_id)
    print(json.dumps({'task_id': task['id'], 'runner_id': task['lease_owner']}), flush=True)
    os._exit(17)
v5_workers._local_model_output = lambda provider, task, nodes: (proposal(task), 10, 12, 20)
print(json.dumps(v5_workers.local_worker_tick(limit=8, population_id=population_id)))
"""


def fresh_process(population_id, mode="tick"):
    result = subprocess.run([sys.executable, "-c", CHILD_WORKER, str(final_core.DB), population_id, mode],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                            timeout=30, env={**os.environ, "FIELDWORK_DISABLE_AGENT_MONITOR": "1",
                                             "FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER": "1"})
    assert result.returncode == (17 if mode == "crash" else 0), result.stderr
    return json.loads(result.stdout)


def test_process_crash_before_lease_expiry_recovers_on_later_local_tick(client):
    _, _, population = seed(client, selection=1)
    task_id = advance(client, population)["task_ids"][0]
    crashed = fresh_process(population["id"], "crash")
    assert crashed["task_id"] == task_id
    # A fresh process may start while the old lease is still valid; don't steal it.
    assert fresh_process(population["id"])["completed"] == []
    with final_core.connect() as db:
        row = db.execute("SELECT status,attempt FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        assert (row["status"], row["attempt"]) == ("running", 1)
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
    result = fresh_process(population["id"])
    assert len(result["completed"]) == 1 and result["completed"][0]["status"] == "succeeded", result
    assert fresh_process(population["id"])["completed"] == []
    with final_core.connect() as db:
        assert db.execute("SELECT active_jobs FROM runner_registry_v5 WHERE id=?", (crashed["runner_id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM agent_task_usage WHERE task_id=?", (task_id,)).fetchone()[0] == 1


def test_repeated_process_crashes_exhaust_attempts_without_creating_claims(client):
    cid, _, population = seed(client, selection=1)
    task_id = advance(client, population)["task_ids"][0]
    for attempt in (1, 2):
        fresh_process(population["id"], "crash")
        with final_core.connect() as db:
            assert db.execute("SELECT attempt FROM agent_tasks WHERE id=?", (task_id,)).fetchone()[0] == attempt
            db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task_id,))
        if attempt == 1:
            assert v5_orchestration.recover_expired_leases()["requeued"] == 1
    assert fresh_process(population["id"])["completed"] == []
    assert fresh_process(population["id"])["completed"] == []
    failed = client.get(f"/api/v1/orchestration/tasks/{task_id}").json()
    assert failed["status"] == "failed" and failed["attempt"] == 2
    assert failed["error"]["code"] == "lease_exhausted"
    assert client.post(f"/api/v1/evolution/populations/{population['id']}/collect").status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=?", (cid,)).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM agent_task_usage WHERE task_id=?", (task_id,)).fetchone()[0] == 0


def test_six_generations_across_processes_stop_atomically_at_group_task_budget(client):
    cid, group, population = seed(client, budget={"max_tasks": 18})
    for generation in range(6):
        scheduled = advance(client, population)
        assert scheduled["created"] == 3
        assert advance(client, population)["created"] == 0
        outcome = fresh_process(population["id"])
        assert len(outcome["completed"]) == 3 and all(item["status"] == "succeeded" for item in outcome["completed"]), outcome
        response = client.post(f"/api/v1/evolution/populations/{population['id']}/collect")
        assert response.status_code == 200, response.text
        child = response.json()
        assert child["generation"] == generation + 1 and child["current"] is True
        assert child["parent_population_id"] == population["id"]
        selected = {item["id"] for item in population["variants"] if item["selected"]}
        assert all(set(item["parent_variant_ids"]) <= selected for item in child["variants"])
        assert client.post(f"/api/v1/evolution/populations/{population['id']}/collect").json()["id"] == child["id"]
        population = child
    exhausted = client.post(f"/api/v1/evolution/populations/{population['id']}/advance")
    assert exhausted.status_code == 409 and "budget exhausted" in exhausted.text
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM agent_tasks WHERE group_id=?", (group["id"],)).fetchone()[0] == 18
        assert db.execute("SELECT COUNT(*) FROM research_populations_v5 WHERE campaign_id=?", (cid,)).fetchone()[0] == 7
        assert db.execute("SELECT COUNT(*) FROM research_nodes WHERE campaign_id=? AND node_type='canonical_result'", (cid,)).fetchone()[0] == 0


@pytest.mark.parametrize("selection", [12, 16])
def test_maximum_selected_population_can_collect_within_candidate_limit(client, monkeypatch, selection):
    _, _, population = seed(client, selection=selection)
    scheduled = advance(client, population)
    expected_tasks = 32 - selection
    assert scheduled["created"] == expected_tasks
    assert scheduled["omitted_combinations"] == 3 * selection - 33
    monkeypatch.setattr(v5_workers, "_local_model_output", lambda _provider, task, _nodes: (proposal(task), 10, 12, 20))
    completed = []
    for _ in range((expected_tasks + 7) // 8):
        result = client.post(f"/api/v1/workers/local/tick?limit=8&population_id={population['id']}").json()
        completed.extend(result["completed"])
    assert len(completed) == expected_tasks and all(item["status"] == "succeeded" for item in completed)
    response = client.post(f"/api/v1/evolution/populations/{population['id']}/collect")
    assert response.status_code == 200, response.text
    assert len(response.json()["variants"]) == 32 and response.json()["selection_count"] == selection
