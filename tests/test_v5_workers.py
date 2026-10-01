import final_core
import v5_workers
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tests.test_final import client, create_ready
from tests.test_v5_verification import receipt_body


def setup_campaign(client):
    engagement = create_ready(client)
    response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Structured workers", "objective": "Challenge and synthesize bounded claims",
    })
    assert response.status_code == 201, response.text
    return engagement, response.json()["id"]


def node(client, cid, kind, title, attributes=None):
    response = client.post("/api/v1/research/nodes", json={
        "campaign_id": cid, "node_type": kind, "title": title, "attributes": attributes or {},
    })
    assert response.status_code == 201, response.text
    return response.json()


def worker_task(client, cid, role, claim_ids, evidence_ids=None, counter_ids=None, key=None):
    response = client.post("/api/v1/workers/tasks", json={
        "campaign_id": cid, "role": role, "claim_ids": claim_ids,
        "evidence_ids": evidence_ids or [], "counterevidence_ids": counter_ids or [],
        "objective": f"Review {role} source claims", "idempotency_key": key or role,
    })
    assert response.status_code == 201, response.text
    return response.json()


def runner(client, rid, roles, *, kind="worker", location="local"):
    response = client.put(f"/api/v1/runners/{rid}", json={
        "id": rid, "name": rid, "kind": kind, "labels": {"location": location},
        "capabilities": roles,
    })
    assert response.status_code == 200, response.text
    return response.json()


def lease(client, rid):
    response = client.post("/api/v1/orchestration/lease", json={"runner_id": rid, "lease_seconds": 300})
    assert response.status_code == 200, response.text
    return response.json()["task"]


def test_critic_structured_output_requires_existing_counterevidence_and_preserves_questions(client):
    engagement, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "Authorization boundary may fail", {"scope": "fixture target"})
    counter = node(client, cid, "counterevidence", "Negative control showed isolation")
    task = worker_task(client, cid, "critic", [claim["id"]], counter_ids=[counter["id"]])
    status = client.get("/api/v1/workers/status").json()
    assert status["critic"]["structured_contract"] is True and status["critic"]["local_executor"] is True
    assert status["critic"]["local_provider_ready"] is False
    assert status["verifier"]["process_isolation_attested"] is False
    assert task["context_capsule"]["relevant_claim_ids"] == [claim["id"]]
    assert task["context_capsule"]["counterevidence_ids"] == [counter["id"]]
    assert task["context_capsule"]["scope_snapshot_id"] == engagement["current_scope_snapshot_id"]
    replay = worker_task(client, cid, "critic", [claim["id"]], counter_ids=[counter["id"]])
    assert replay["id"] == task["id"]
    runner(client, "critic-cloud", ["structured_critic"], location="cloud")
    assert lease(client, "critic-cloud") is None
    runner(client, "critic-local", ["structured_critic"])
    leased = lease(client, "critic-local")
    assert leased["id"] == task["id"]
    assert client.post(f"/api/v1/orchestration/tasks/{task['id']}/complete", json={
        "runner_id": "critic-local", "outcome": "succeeded", "result": {"conclusion": "challenged"},
    }).status_code == 409
    assert client.post(f"/api/v1/workers/tasks/{task['id']}/result", json={
        "runner_id": "critic-local", "output": {"claim_node_id": claim["id"],
            "weaknesses": ["The negative control needs independent review"],
            "counterevidence_ids": [], "conclusion": "challenged"},
    }).status_code == 409
    response = client.post(f"/api/v1/workers/tasks/{task['id']}/result", json={
        "runner_id": "critic-local", "output": {"claim_node_id": claim["id"],
            "weaknesses": ["The negative control needs independent review"],
            "counterevidence_ids": [counter["id"]], "conclusion": "challenged"},
        "input_tokens": 30, "output_tokens": 15,
    })
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["counterevidence_ids"] == [counter["id"]] and len(result["open_question_ids"]) == 1
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    assert any(edge["source_id"] == counter["id"] and edge["target_id"] == claim["id"]
               and edge["relation_type"] == "contradicts" for edge in graph["edges"])
    assert any(n["node_type"] == "open_question" and n["attributes"]["not_evidence"] for n in graph["nodes"])
    assert not any(n["node_type"] == "canonical_result" for n in graph["nodes"])
    assert client.get(f"/api/v1/orchestration/tasks/{task['id']}").json()["status"] == "succeeded"


def test_synthesizer_creates_draft_claim_and_verifier_requires_isolated_runner(client):
    _, cid = setup_campaign(client)
    first = node(client, cid, "claim", "First compatible claim", {"scope": "fixture target"})
    second = node(client, cid, "claim", "Second compatible claim", {"scope": "fixture target"})
    evidence = node(client, cid, "evidence", "Independent observation proof")
    counter = node(client, cid, "counterevidence", "Bounded negative check")
    task = worker_task(client, cid, "synthesizer", [first["id"], second["id"]],
                       evidence_ids=[evidence["id"]], counter_ids=[counter["id"]])
    runner(client, "synth-local", ["structured_synthesizer"])
    assert lease(client, "synth-local")["id"] == task["id"]
    submitted = client.post(f"/api/v1/workers/tasks/{task['id']}/result", json={
        "runner_id": "synth-local", "output": {
            "statement": "These two claims describe one bounded condition",
            "scope": "fixture target", "source_claim_ids": [first["id"], second["id"]],
            "evidence_ids": [evidence["id"]], "counterevidence_ids": [counter["id"]],
            "limitations": ["Only the fixture context was considered"],
        },
    })
    assert submitted.status_code == 200, submitted.text
    claim_id = submitted.json()["result"]["claim_node_id"]
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    claim = next(item for item in graph["nodes"] if item["id"] == claim_id)
    assert claim["status"] == "draft" and claim["attributes"]["producer_runner_ref"] == "synth-local"
    assert any(edge["source_id"] == evidence["id"] and edge["target_id"] == claim_id
               and edge["relation_type"] == "supports" for edge in graph["edges"])
    assert any(edge["source_id"] == counter["id"] and edge["target_id"] == claim_id
               and edge["relation_type"] == "contradicts" for edge in graph["edges"])
    request = client.post(f"/api/v1/verification/claims/{claim_id}", json={
        "campaign_id": cid, "evidence_ids": [evidence["id"], counter["id"]],
        "replay_contract": {"type": "local_fixture_replay"},
    })
    assert request.status_code == 202, request.text
    runner(client, "fake-verifier", ["independent_verification"], kind="worker")
    assert lease(client, "fake-verifier") is None
    runner(client, "isolated-verifier", ["independent_verification"], kind="isolated-verifier")
    leased = lease(client, "isolated-verifier")
    assert leased["id"] == request.json()["request"]["verifier_task_id"]
    assert client.post(f"/api/v1/verification/tasks/{leased['id']}/receipt", json=receipt_body(
        "isolated-verifier", evidence_ids=[evidence["id"], counter["id"]],
    )).status_code == 201
    assert not any(n["node_type"] == "canonical_result" for n in client.get(
        f"/api/v1/research/campaigns/{cid}/graph").json()["nodes"])


def test_worker_rejects_cross_campaign_refs_and_scope_rotation(client):
    engagement, cid = setup_campaign(client)
    _, foreign = setup_campaign(client)
    claim = node(client, cid, "claim", "Local claim")
    other = node(client, foreign, "counterevidence", "Foreign counterevidence")
    response = client.post("/api/v1/workers/tasks", json={
        "campaign_id": cid, "role": "critic", "claim_ids": [claim["id"]],
        "counterevidence_ids": [other["id"]], "objective": "Cross-project reference check",
        "idempotency_key": "cross-project",
    })
    assert response.status_code == 409
    task = worker_task(client, cid, "critic", [claim["id"]], key="rotating-scope")
    runner(client, "scope-critic", ["structured_critic"])
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO scope_snapshots VALUES(?,?,?,?,?,?,?,?)", (
            "worker-new-scope", engagement["id"], 2, "traditional", "{}", "fixture", now, now,
        ))
        db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?",
                   ("worker-new-scope", engagement["id"]))
    assert lease(client, "scope-critic") is None
    assert client.post("/api/v1/runtime/routes", json={
        "task_id": task["id"], "task_type": "critique", "sensitivity": "public",
        "mode": "cloud", "budget_remaining_micros": 100,
    }).status_code == 409


def test_structured_worker_rejects_stale_inputs_and_generic_role_bypass(client):
    _, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "Original statement")
    assert client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": cid, "role": "critic", "objective": "Bypass structured contract",
        "idempotency_key": "role-bypass",
    }).status_code == 422
    task = worker_task(client, cid, "critic", [claim["id"]], key="stale-critic")
    runner(client, "stale-critic-runner", ["structured_critic"])
    assert lease(client, "stale-critic-runner")["id"] == task["id"]
    assert client.patch(f"/api/v1/research/nodes/{claim['id']}", json={
        "title": "Changed statement after planning",
    }).status_code == 200
    response = client.post(f"/api/v1/workers/tasks/{task['id']}/result", json={
        "runner_id": "stale-critic-runner", "output": {"claim_node_id": claim["id"],
            "weaknesses": [], "counterevidence_ids": [], "conclusion": "inconclusive"},
    })
    assert response.status_code == 409 and "input graph changed" in response.text


def test_explicit_local_worker_tick_executes_only_structured_tasks(client, monkeypatch):
    _, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "A bounded issue needs critique")
    task = worker_task(client, cid, "critic", [claim["id"]], key="builtin-critic")
    unavailable = client.post("/api/v1/workers/local/tick?limit=1")
    assert unavailable.status_code == 200
    assert unavailable.json()["status"] == "local_provider_unavailable"
    assert client.get(f"/api/v1/orchestration/tasks/{task['id']}").json()["status"] == "queued"
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "fixture-local-model", "llama_cpp", "Fixture local model", "http://127.0.0.1:9009",
            "fixture-model", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    seen = []
    def model(provider, leased_task, nodes):
        seen.append((provider["id"], leased_task["id"], [item["id"] for item in nodes]))
        return ({"claim_node_id": claim["id"], "weaknesses": ["Need a negative control"],
                 "counterevidence_ids": [], "conclusion": "inconclusive"}, 10, 12, 25)
    monkeypatch.setattr(v5_workers, "_local_model_output", model)
    response = client.post("/api/v1/workers/local/tick?limit=1")
    assert response.status_code == 200, response.text
    assert response.json()["completed"][0]["status"] == "succeeded"
    assert seen == [("fixture-local-model", task["id"], [claim["id"]])]
    assert client.get(f"/api/v1/orchestration/tasks/{task['id']}").json()["status"] == "succeeded"
    status = client.get("/api/v1/workers/status").json()
    assert status["critic"]["local_executor"] is True and status["critic"]["local_provider_ready"] is True


def test_local_worker_invalid_output_retries_without_graph_promotion(client, monkeypatch):
    _, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "A bounded issue needs critique")
    task = worker_task(client, cid, "critic", [claim["id"]], key="invalid-local-output")
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "fixture-local-model", "llama_cpp", "Fixture local model", "http://127.0.0.1:9009",
            "fixture-model", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    monkeypatch.setattr(v5_workers, "_local_model_output", lambda *_: (
        {"claim_node_id": claim["id"], "weaknesses": [], "counterevidence_ids": [],
         "conclusion": "challenged"}, 10, 10, 10,
    ))
    result = client.post("/api/v1/workers/local/tick?limit=1").json()
    assert result["completed"][0]["status"] == "failed_attempt"
    state = client.get(f"/api/v1/orchestration/tasks/{task['id']}").json()
    assert state["status"] == "queued" and state["attempt"] == 1
    assert state["error"]["error_type"] == "HTTPException"
    graph = client.get(f"/api/v1/research/campaigns/{cid}/graph").json()
    assert {n["node_type"] for n in graph["nodes"]} == {"claim"}


def test_retried_critic_requires_current_lease_attempt(client):
    _, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "A bounded critique retry fixture")
    task = worker_task(client, cid, "critic", [claim["id"]], key="attempt-fenced-critic")
    runner(client, "retry-critic", ["structured_critic"])
    assert lease(client, "retry-critic")["attempt"] == 1
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (task["id"],))
    assert lease(client, "retry-critic")["attempt"] == 2
    payload = {"runner_id": "retry-critic", "output": {"claim_node_id": claim["id"],
               "weaknesses": [], "counterevidence_ids": [], "conclusion": "inconclusive"}}
    url = f"/api/v1/workers/tasks/{task['id']}/result"
    assert client.post(url, json=payload).status_code == 409
    assert client.post(url, json={**payload, "lease_attempt": 1}).status_code == 409
    assert client.post(url, json={**payload, "lease_attempt": 2}).status_code == 200


def test_local_model_transport_uses_loopback_and_json_contract():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            seen.append((self.path, json.loads(self.rfile.read(length))))
            body = json.dumps({"choices": [{"message": {"content": json.dumps({
                "claim_node_id": "claim-fixture", "weaknesses": [],
                "counterevidence_ids": [], "conclusion": "inconclusive",
            })}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = {"kind": "llama_cpp", "base_url": f"http://127.0.0.1:{server.server_port}",
                    "model": "fixture-model"}
        task = {"role": "critic", "objective": "Critique a bounded claim", "budget_json": '{"max_tokens":100}'}
        output, input_tokens, output_tokens, runtime_ms = v5_workers._local_model_output(
            provider, task, [{"id": "claim-fixture", "node_type": "claim", "title": "Fixture claim"}],
        )
        assert output["conclusion"] == "inconclusive"
        assert (input_tokens, output_tokens) == (11, 7) and runtime_ms >= 0
        assert seen[0][0] == "/v1/chat/completions"
        assert seen[0][1]["max_tokens"] == 100
        evolver = {"role": "evolver", "objective": "Mutate one selected parent",
                   "budget_json": '{"max_tokens":100}',
                   "context_capsule_json": json.dumps({"evolution_mode": "mutate",
                       "parent_variant_ids": ["variant-1"], "relevant_evidence_ids": ["evidence-1"],
                       "counterevidence_ids": ["counter-1"]})}
        v5_workers._local_model_output(provider, evolver, [
            {"id": "claim-1", "node_type": "claim", "title": "Parent claim"},
        ])
        assert "Preserve every required counterevidence ID" in seen[1][1]["messages"][0]["content"]
        assert "variant-1" in seen[1][1]["messages"][1]["content"]
        try:
            v5_workers._local_model_output({**provider, "base_url": "http://example.test:80"}, task, [])
        except ValueError as error:
            assert "loopback" in str(error)
        else:
            raise AssertionError("non-loopback model endpoint was accepted")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
