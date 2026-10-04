import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import final_core
import v5_verification
from tests.test_final import client, create_ready
from tests.test_v5_verification import graph_node, receipt_body, register_verifier


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200 if self.path == "/positive" else 403 if self.path == "/negative" else 404
        self.send_response(status)
        self.end_headers()
        self.wfile.write(b"fixture")

    def log_message(self, *_args):
        pass


def _server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _request(client, base, *, contract=None):
    engagement = create_ready(client, target=base)
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Local verifier fixture", "objective": "Check a scoped status relationship",
    }).json()
    replay = contract or {"type": "loopback_http_status_v1", "url": f"{base}/positive",
                          "negative_control_url": f"{base}/negative", "expected_status": 200,
                          "negative_control_status": 403}
    claim = graph_node(client, campaign["id"], "claim", "Positive endpoint returns 200 while control returns 403",
                       attributes={"claim_kind": "http_status_relationship",
                                   "verification_contract_sha256": v5_verification._sha(replay)})
    evidence = graph_node(client, campaign["id"], "evidence", "Pre-replay status observation")
    response = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": campaign["id"], "evidence_ids": [evidence["id"]], "replay_contract": replay,
    })
    assert response.status_code == 202, response.text
    return response.json()["request"], evidence


def test_local_verifier_replays_in_distinct_observed_process(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request, _ = _request(client, base)
        tick = client.post("/api/v1/verification/local/tick")
        assert tick.status_code == 200, tick.text
        assert tick.json()["completed"][0]["status"] == "succeeded"
        receipt_id = tick.json()["completed"][0]["receipt_id"]
        receipt = client.get(f"/api/v1/verification/receipts/{receipt_id}").json()
        assert receipt["verifier_task_id"] == request["verifier_task_id"]
        assert receipt["result"]["status"] == "verified"
        assert receipt["result"]["oracle"]["positive_statuses"] == [200, 200]
        assert receipt["result"]["oracle"]["negative_control_statuses"] == [403, 403]
        assert receipt["environment"]["provenance"] == "supervisor_observed_process"
        proof = receipt["environment"]["process_isolation"]
        assert proof["child_pid"] != proof["parent_pid"]
        assert proof["exit_code"] == 0 and len(proof["script_sha256"]) == 64
        assert proof["sandbox"] == "macos-seatbelt"
        assert len(proof["sandbox_profile_sha256"]) == 64
        assert proof["file_read_denied"] is True
        assert receipt["integrity"]["valid"] is True
        assert receipt["integrity"]["process_isolation_observed"] is True
        assert receipt["integrity"]["promotion_eligible"] is True
        canonical = client.post("/api/v1/research/nodes", json={
            "campaign_id": request["campaign_id"], "node_type": "canonical_result",
            "title": "Observed local HTTP status relationship", "source_ref": request["claim_node_id"],
            "attributes": {"verification_receipt_id": receipt_id, "verification_outcome": "verified"},
        })
        assert canonical.status_code == 201, canonical.text
        assert canonical.json()["canonical_trust"]["current"] is True
        assert client.post("/api/v1/orchestration/lease", json={
            "runner_id": v5_verification.LOCAL_VERIFIER_ID,
        }).status_code == 409
        assert client.put(f"/api/v1/runners/{v5_verification.LOCAL_VERIFIER_ID}", json={
            "id": v5_verification.LOCAL_VERIFIER_ID, "name": "spoof", "kind": "isolated-verifier",
        }).status_code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_local_verifier_rejects_out_of_scope_and_non_loopback_contract(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _request(client, base, contract={"type": "loopback_http_status_v1",
            "url": "http://192.0.2.1/positive", "negative_control_url": "http://192.0.2.1/negative",
            "expected_status": 200, "negative_control_status": 403})
        tick = client.post("/api/v1/verification/local/tick")
        assert tick.status_code == 200 and tick.json()["status"] == "idle"
        assert tick.json()["completed"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_external_runner_receipt_is_not_process_attested(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request, evidence = _request(client, base)
        register_verifier(client, "external-verifier")
        leased = client.post("/api/v1/orchestration/lease", json={"runner_id": "external-verifier"}).json()["task"]
        assert leased["id"] == request["verifier_task_id"]
        response = client.post(f"/api/v1/verification/tasks/{leased['id']}/receipt", json=receipt_body(
            "external-verifier", evidence_ids=[evidence["id"]]))
        assert response.status_code == 201, response.text
        environment = response.json()["environment"]
        assert environment["provenance"] == "runner_attested"
        assert "process_isolation" not in environment
        assert response.json()["integrity"]["process_isolation_observed"] is False
        assert response.json()["integrity"]["promotion_eligible"] is False
        forged = client.post(f"/api/v1/verification/tasks/{leased['id']}/receipt", json=receipt_body(
            v5_verification.LOCAL_VERIFIER_ID, evidence_ids=[evidence["id"]]))
        assert forged.status_code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_process_observation_requires_os_sandbox_and_denied_capabilities():
    proof = {"observed_by": "fieldwork_local_supervisor", "exit_code": 0,
             "parent_pid": 1, "child_pid": 2, "sandbox": "macos-seatbelt",
             "sandbox_profile_sha256": "a" * 64, "file_read_denied": True,
             "network_denied": True}
    payload = {"environment": {"provenance": "supervisor_observed_process",
                               "oracle": "package_applicability_v1", "process_isolation": proof}}
    assert v5_verification._process_observed(payload) is True
    proof["network_denied"] = False
    assert v5_verification._process_observed(payload) is False
    proof["network_denied"] = True
    proof["file_read_denied"] = False
    assert v5_verification._process_observed(payload) is False
    proof["file_read_denied"] = True
    proof.pop("sandbox")
    assert v5_verification._process_observed(payload) is False


def test_local_verifier_fails_closed_without_os_sandbox(monkeypatch):
    monkeypatch.setattr(v5_verification.sys, "platform", "unsupported")
    with pytest.raises(RuntimeError, match="requires the macOS sandbox"):
        v5_verification._run_local_verifier({"type": "package_applicability_v1"})


def test_verifier_lease_and_receipt_reject_scope_rotation(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request, evidence = _request(client, base)
        register_verifier(client, "scope-verifier")
        task = client.post("/api/v1/orchestration/lease", json={"runner_id": "scope-verifier"}).json()["task"]
        assert task["id"] == request["verifier_task_id"]
        alternate = create_ready(client, target="https://rotated.example.test")
        with final_core.connect() as db:
            campaign = db.execute("SELECT engagement_id FROM research_campaigns WHERE id=?",
                                  (request["campaign_id"],)).fetchone()
            db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?",
                       (alternate["current_scope_snapshot_id"], campaign["engagement_id"]))
        denied = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=receipt_body(
            "scope-verifier", evidence_ids=[evidence["id"]]))
        assert denied.status_code == 409 and "scope" in denied.text
    finally:
        server.shutdown()
        server.server_close()


def test_local_verifier_negative_control_failure_is_inconclusive(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _request(client, base, contract={"type": "loopback_http_status_v1", "url": f"{base}/positive",
            "negative_control_url": f"{base}/negative", "expected_status": 200,
            "negative_control_status": 404})
        tick = client.post("/api/v1/verification/local/tick")
        assert tick.status_code == 200, tick.text
        receipt = client.get("/api/v1/verification/receipts/" + tick.json()["completed"][0]["receipt_id"]).json()
        assert receipt["result"]["status"] == "inconclusive"
        assert receipt["result"]["classification"] == "invalid_precondition"
        assert receipt["integrity"]["process_isolation_observed"] is True
        assert receipt["integrity"]["promotion_eligible"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_local_verifier_child_failure_requeues_without_receipt(client, monkeypatch):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request, _ = _request(client, base)
        monkeypatch.setattr(v5_verification, "_run_local_verifier", lambda _contract, **kwargs: (_ for _ in ()).throw(TimeoutError()))
        tick = client.post("/api/v1/verification/local/tick")
        assert tick.status_code == 200
        assert tick.json()["completed"][0]["status"] == "failed_attempt"
        task = client.get(f"/api/v1/orchestration/tasks/{request['verifier_task_id']}").json()
        assert task["status"] == "queued"
        receipts = client.get(f"/api/v1/verification/receipts?campaign_id={request['campaign_id']}").json()
        assert receipts["page"]["total"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_observed_receipt_loses_promotion_eligibility_after_scope_rotation(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        request, _ = _request(client, base)
        tick = client.post("/api/v1/verification/local/tick")
        receipt_id = tick.json()["completed"][0]["receipt_id"]
        assert client.get(f"/api/v1/verification/receipts/{receipt_id}").json()["integrity"]["current_inputs_match"]
        canonical = client.post("/api/v1/research/nodes", json={
            "campaign_id": request["campaign_id"], "node_type": "canonical_result",
            "title": "Previously current relation", "source_ref": request["claim_node_id"],
            "attributes": {"verification_receipt_id": receipt_id, "verification_outcome": "verified"},
        })
        assert canonical.status_code == 201 and canonical.json()["canonical_trust"]["current"] is True
        alternate = create_ready(client, target="https://new-scope.example.test")
        with final_core.connect() as db:
            engagement_id = db.execute("SELECT engagement_id FROM research_campaigns WHERE id=?",
                                       (request["campaign_id"],)).fetchone()["engagement_id"]
            db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?",
                       (alternate["current_scope_snapshot_id"], engagement_id))
        receipt = client.get(f"/api/v1/verification/receipts/{receipt_id}").json()
        assert receipt["integrity"]["current_inputs_match"] is False
        assert receipt["integrity"]["promotion_eligible"] is False
        stale_node = client.get(f"/api/v1/research/nodes/{canonical.json()['id']}").json()
        assert stale_node["canonical_trust"]["current"] is False
        graph = client.get(f"/api/v1/research/campaigns/{request['campaign_id']}/graph").json()
        assert next(node for node in graph["nodes"] if node["id"] == canonical.json()["id"])["canonical_trust"]["current"] is False
        denied = client.post("/api/v1/research/nodes", json={
            "campaign_id": request["campaign_id"], "node_type": "canonical_result",
            "title": "Stale status relation", "source_ref": request["claim_node_id"],
            "attributes": {"verification_receipt_id": receipt_id, "verification_outcome": "verified"},
        })
        assert denied.status_code == 409
    finally:
        server.shutdown()
        server.server_close()


def test_waiting_real_sandbox_child_is_killed_and_reaped_on_cancel(monkeypatch, tmp_path):
    import time
    script = tmp_path / 'waiting-verifier.py'
    script.write_text('import sys, time\nsys.stdin.buffer.read()\ntime.sleep(30)\n')
    monkeypatch.setattr(v5_verification, 'CHILD_SCRIPT', script)
    stopped = threading.Event()
    processes, timers = [], []
    original = v5_verification.subprocess.Popen
    def observe_process(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        timer = threading.Timer(.35, stopped.set)
        timer.start()
        timers.append(timer)
        return process
    monkeypatch.setattr(v5_verification.subprocess, 'Popen', observe_process)
    def current():
        if stopped.is_set():
            raise v5_verification.LocalVerificationStopped('cancelled')
    started = time.monotonic()
    try:
        with pytest.raises(v5_verification.LocalVerificationStopped, match='cancelled'):
            v5_verification._run_local_verifier({'type': 'package_applicability_v1'}, check_current=current)
    finally:
        for timer in timers:
            timer.cancel()
            timer.join()
    assert time.monotonic() - started < 3
    assert len(processes) == 1 and processes[0].poll() is not None
    assert processes[0].returncode < 0
