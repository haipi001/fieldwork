import final_core
import v5_workers
import json
import threading
import pytest
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


def test_run_bound_structured_model_uses_v6_gateway(client, monkeypatch):
    engagement, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "Run-bound critique needs a negative control")
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "structured-gateway-run", engagement["id"], "traditional",
            engagement["current_scope_snapshot_id"], engagement["current_policy_id"],
            "running", "target", 0, None, None, None, None, now,
        ))
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "structured-gateway-model", "llama_cpp", "Gateway fixture", "http://127.0.0.1:9009",
            "fixture-model", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    created = client.post("/api/v1/workers/tasks", json={
        "campaign_id": cid, "run_id": "structured-gateway-run", "role": "critic",
        "claim_ids": [claim["id"]], "objective": "Review a run-bound claim",
        "idempotency_key": "structured-gateway", "max_tokens": 8000,
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    monkeypatch.setattr(v5_workers, "_local_model_output", lambda *_: (
        {"claim_node_id": claim["id"], "weaknesses": ["Check the negative control"],
         "counterevidence_ids": [], "conclusion": "inconclusive"}, 10, 12, 25,
    ))
    response = client.post("/api/v1/workers/local/tick?limit=1")
    assert response.status_code == 200, response.text
    assert response.json()["completed"][0]["status"] == "succeeded"
    with final_core.connect() as db:
        binding = db.execute("SELECT b.*,c.state FROM model_gateway_bindings_v6 b "
            "JOIN runtime_calls c ON c.id=b.call_id WHERE c.task_id=?", (task_id,)).fetchone()
        assert binding["principal_id"] == "fieldwork:structured-local-worker"
        assert binding["agent_id"] == "fieldwork:structured-local-agent-v1"
        assert binding["state"] == "settled"
        assert db.execute("SELECT COUNT(*) FROM model_gateway_starts_v6 WHERE call_id=?",
                          (binding["call_id"],)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6 WHERE action_id=?",
                          (binding["call_id"],)).fetchone()[0] == 1
    mismatch = client.post("/api/v1/workers/tasks", json={
        "campaign_id": cid, "role": "critic", "claim_ids": [claim["id"]],
        "objective": "Review a run-bound claim", "idempotency_key": "structured-gateway",
        "max_tokens": 8000,
    })
    assert mismatch.status_code == 409


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
    with final_core.connect() as db:
        usage = db.execute("SELECT * FROM runtime_usage WHERE task_id=?", (task["id"],)).fetchone()
        assert usage["input_tokens"] == 10 and usage["output_tokens"] == 10
        assert usage["provider_id"] == "fixture-local-model"
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
        task = {"role": "critic", "objective": "Critique a bounded claim", "budget_json": '{"max_tokens":4000}'}
        output, input_tokens, output_tokens, runtime_ms = v5_workers._local_model_output(
            provider, task, [{"id": "claim-fixture", "node_type": "claim", "title": "Fixture claim"}],
        )
        assert output["conclusion"] == "inconclusive"
        assert (input_tokens, output_tokens) == (11, 7) and runtime_ms >= 0
        assert seen[0][0] == "/v1/chat/completions"
        assert 0 < seen[0][1]["max_tokens"] < 4000
        evolver = {"role": "evolver", "objective": "Mutate one selected parent",
                   "budget_json": '{"max_tokens":4000}',
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


def test_worker_runtime_budget_is_bound_and_checked_before_graph_write(client):
    _, cid = setup_campaign(client)
    claim = node(client, cid, "claim", "Bounded runtime review claim")
    body = {"campaign_id": cid, "role": "critic", "claim_ids": [claim["id"]],
            "objective": "Review within runtime budget", "idempotency_key": "runtime-limit",
            "max_runtime_ms": 100}
    created = client.post('/api/v1/workers/tasks', json=body)
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['budget']['max_runtime_ms'] == 100
    assert client.post('/api/v1/workers/tasks', json=body).json()['id'] == task['id']
    assert client.post('/api/v1/workers/tasks', json={**body, 'max_runtime_ms': 101}).status_code == 409
    assert client.post('/api/v1/workers/tasks', json={**body, 'max_runtime_ms': True}).status_code == 422
    runner(client, 'runtime-worker', ['structured_critic'])
    leased = lease(client, 'runtime-worker')
    before = client.get(f'/api/v1/research/campaigns/{cid}/graph').json()
    result = client.post(f"/api/v1/workers/tasks/{task['id']}/result", json={
        'runner_id': 'runtime-worker', 'lease_attempt': leased['attempt'], 'runtime_ms': 101,
        'output': {'claim_node_id': claim['id'], 'weaknesses': ['Needs further investigation'],
                   'counterevidence_ids': [], 'conclusion': 'inconclusive'}})
    assert result.status_code == 409 and 'runtime budget' in result.text
    assert client.get(f'/api/v1/research/campaigns/{cid}/graph').json() == before
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM agent_task_usage WHERE task_id=?', (task['id'],)).fetchone()[0] == 0


def test_local_model_redirect_does_not_contact_destination():
    import pytest
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            hits.append(self.path)
            self.send_response(307)
            self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/redirected')
            self.send_header('Content-Length', '0')
            self.end_headers()
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ValueError, match='redirects are forbidden'):
            v5_workers._local_model_output(
                {'kind': 'ollama', 'base_url': f'http://127.0.0.1:{server.server_port}', 'model': 'fixture'},
                {'role': 'critic', 'objective': 'Bounded review', 'budget_json': '{"max_tokens":4000}'}, [])
        assert hits == ['/api/chat']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_ollama_transport_passes_output_limit_and_runtime_timeout(monkeypatch):
    seen = {}
    import v5_model_transport
    def request(url, payload, timeout_ms, check_current):
        seen.update(payload=payload, timeout=timeout_ms / 1000)
        check_current()
        return json.dumps({'message': {'content': '{}'}, 'prompt_eval_count': 3, 'eval_count': 2}).encode()
    monkeypatch.setattr(v5_model_transport, 'request_model', request)
    output, prompt, completion, _ = v5_workers._local_model_output(
        {'kind': 'ollama', 'base_url': 'http://127.0.0.1:11434', 'model': 'fixture'},
        {'role': 'critic', 'objective': 'Review bounded claim',
         'budget_json': '{"max_tokens":4000,"max_runtime_ms":250}'}, [])
    assert 0 < seen['payload']['options']['num_predict'] < 4000
    assert seen['timeout'] == .25
    assert (output, prompt, completion) == ({}, 3, 2)


def test_profile_bound_worker_uses_actual_http_and_records_route_usage(client):
    _, cid = setup_campaign(client)
    claim = node(client, cid, 'claim', 'Profile-specific bounded review')
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            raw = json.dumps({'choices': [{'message': {'content': json.dumps({
                'claim_node_id': claim['id'], 'weaknesses': [],
                'counterevidence_ids': [], 'conclusion': 'inconclusive'})}}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # The first registered healthy provider is intentionally unreachable.
        for pid, port in [('wrong-first', 1), ('profile-selected', server.server_port)]:
            with final_core.connect() as db:
                now = final_core.utcnow()
                db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                    pid, 'llama_cpp', pid, f'http://127.0.0.1:{port}', 'fixture-model', None,
                    1, '{"location":"local"}', 'healthy', now, now, now))
        response = client.put('/api/v1/runtime/config', json={
            'name': 'Bounded worker profile', 'mode': 'offline',
            'config': {'local_provider_ids': ['profile-selected'], 'max_tokens_per_call': 2000}})
        assert response.status_code == 201, response.text
        profile = response.json()['id']
        task_response = client.post('/api/v1/workers/tasks', json={
            'campaign_id': cid, 'role': 'critic', 'claim_ids': [claim['id']],
            'objective': 'Use the selected runtime profile', 'idempotency_key': 'profile-worker',
            'runtime_profile_id': profile})
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()
        tick = client.post('/api/v1/workers/local/tick?limit=1')
        assert tick.status_code == 200, tick.text
        assert tick.json()['completed'][0]['status'] == 'succeeded', tick.text
        assert len(calls) == 1 and 0 < calls[0]['max_tokens'] < 2000
        with final_core.connect() as db:
            decision = db.execute('SELECT * FROM runtime_route_decisions WHERE task_id=?', (task['id'],)).fetchone()
            usage = db.execute('SELECT * FROM runtime_usage WHERE task_id=?', (task['id'],)).fetchone()
            assert decision['profile_id'] == profile and decision['provider_id'] == 'profile-selected'
            assert json.loads(decision['request_json'])['provider_configuration_sha256']
            assert usage['input_tokens'] == 11 and usage['output_tokens'] == 7
        override = client.post('/api/v1/runtime/routes', json={
            'task_type': 'critic', 'task_id': task['id'], 'profile_id': 'different-profile'})
        assert override.status_code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize('change', ['cancel', 'disabled', 'model', 'credential'])
def test_cancel_api_interrupts_active_model_transport_without_result(client, monkeypatch, tmp_path, change):
    import time
    _, cid = setup_campaign(client)
    claim = node(client, cid, 'claim', 'Cancelable bounded model review')
    task = worker_task(client, cid, 'critic', [claim['id']], key='cancel-model-transport')
    requested = threading.Event()
    disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Length', '10000')
            self.end_headers()
            requested.set()
            try:
                for _ in range(200):
                    self.wfile.write(b' ')
                    self.wfile.flush()
                    time.sleep(.025)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    results = []
    ticking = threading.Thread(target=lambda: results.append(client.post('/api/v1/workers/local/tick?limit=1')),
                               daemon=True)
    try:
        with final_core.connect() as db:
            now = final_core.utcnow()
            db.execute('INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                'cancel-model', 'llama_cpp', 'Cancel fixture', f'http://127.0.0.1:{server.server_port}',
                'fixture-model', None, 1, '{"location":"local"}', 'healthy', now, now, now))
        ticking.start()
        assert requested.wait(3)
        start = time.monotonic()
        if change == 'cancel':
            changed = client.post(f"/api/v1/orchestration/tasks/{task['id']}/cancel")
        else:
            import runtime_secrets
            monkeypatch.setattr(runtime_secrets, 'secret_root', lambda: tmp_path / 'secrets')
            fields = {'disabled': {'enabled': False}, 'model': {'model': 'changed-model'},
                      'credential': {'api_key': 'fixture-rotated-credential'}}
            changed = client.patch('/api/v1/runtime/providers/cancel-model', json=fields[change])
        assert changed.status_code == 200, changed.text
        ticking.join(timeout=2)
        assert not ticking.is_alive() and time.monotonic() - start < 2
        assert results[0].status_code == 200
        assert disconnected.wait(1)
        state = client.get(f"/api/v1/orchestration/tasks/{task['id']}").json()
        assert state['status'] == ('cancelled' if change == 'cancel' else 'paused') and state['result'] is None
        with final_core.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM runtime_usage WHERE task_id=?', (task['id'],)).fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM agent_task_usage WHERE task_id=?', (task['id'],)).fetchone()[0] == 0
            assert db.execute('SELECT state FROM runtime_calls WHERE task_id=?', (task['id'],)).fetchone()[0] == 'unknown'
            assert db.execute('SELECT COUNT(*) FROM research_nodes WHERE campaign_id=?', (cid,)).fetchone()[0] == 1
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)
        ticking.join(timeout=2)
