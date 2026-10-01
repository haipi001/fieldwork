import json
import sqlite3

import final_core
import v5_graph
from tests.test_final import client, create_ready


def campaign(client, target="https://verification.example.test"):
    engagement = create_ready(client, target=target)
    response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Verification acceptance", "objective": "Verify claims independently",
    })
    assert response.status_code == 201, response.text
    return response.json()


def graph_node(client, campaign_id, node_type, title, **values):
    response = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": node_type, "title": title, **values,
    })
    assert response.status_code == 201, response.text
    return response.json()


def register_verifier(client, runner_id="verifier-runner", **metadata):
    response = client.put(f"/api/v1/runners/{runner_id}", json={
        "id": runner_id, "name": runner_id, "kind": "isolated-verifier",
        "capabilities": ["independent_verification"], "max_concurrency": 1,
        "metadata": metadata,
    })
    assert response.status_code == 200, response.text
    return response.json()


def request(client, campaign_id, claim_id, evidence_ids, replay=None):
    response = client.post(f"/api/v1/verification/claims/{claim_id}", json={
        "campaign_id": campaign_id, "evidence_ids": evidence_ids,
        "replay_contract": replay or {"type": "http_replay", "method": "GET", "path": "/objects/1"},
    })
    assert response.status_code == 202, response.text
    return response.json()


def lease(client, runner_id="verifier-runner"):
    response = client.post("/api/v1/orchestration/lease", json={
        "runner_id": runner_id, "lease_seconds": 300,
    })
    assert response.status_code == 200, response.text
    return response.json()["task"]


def receipt_body(runner_id="verifier-runner", classification="positive", status="verified", **changes):
    value = {
        "runner_id": runner_id,
        "environment": {"os": "darwin", "sandbox": "fresh-profile", "network": "bounded"},
        "result": {
            "status": status, "classification": classification, "summary": "Independent replay completed",
            "preconditions_valid": classification not in {"invalid_precondition", "interrupted"},
            "counterevidence_checked": classification not in {"invalid_precondition", "interrupted"},
            "interrupted": classification == "interrupted", "attempts": 2,
            "oracle": {"kind": "ownership_difference", "negative_control": "matched"},
        },
        "evidence_ids": changes.pop("evidence_ids", []), "limitations": changes.pop("limitations", []),
    }
    value.update(changes)
    return value


def verification_fixture(client, *, claim_attributes=None):
    current = campaign(client)
    claim = graph_node(client, current["id"], "claim", "Object boundary is bypassed",
                       attributes=claim_attributes or {})
    evidence = graph_node(client, current["id"], "evidence", "Replay response digest", body="redacted evidence")
    requested = request(client, current["id"], claim["id"], [evidence["id"]])
    register_verifier(client, verifier_id="verifier-identity-a")
    task = lease(client)
    assert task["id"] == requested["request"]["verifier_task_id"]
    return current, claim, evidence, requested, task


def test_self_attested_positive_receipt_is_task_bound_hashed_but_not_promotable(client):
    current, claim, evidence, _, task = verification_fixture(client)
    bypass = client.post(f"/api/v1/orchestration/tasks/{task['id']}/complete", json={
        "runner_id": "verifier-runner", "outcome": "succeeded", "result": {"status": "verified"},
    })
    assert bypass.status_code == 409
    body = receipt_body(evidence_ids=[evidence["id"]])
    issued = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=body)
    assert issued.status_code == 201, issued.text
    value = issued.json()
    assert value["result"]["status"] == "verified"
    assert value["verifier_id"] == "verifier-identity-a"
    assert value["integrity"] == {"valid": True, "algorithm": "sha256", "current_inputs_match": True,
                                  "process_isolation_observed": False, "promotion_eligible": False,
                                  "promotion_domain": "canonical_result"}
    replayed_ack = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=body)
    assert replayed_ack.status_code == 201 and replayed_ack.json()["id"] == value["id"]
    canonical = client.post("/api/v1/research/nodes", json={
        "campaign_id": current["id"], "node_type": "canonical_result", "title": "Verified boundary bypass",
        "source_ref": claim["id"], "attributes": {
            "verification_receipt_id": value["id"], "verification_outcome": "verified",
        },
    })
    assert canonical.status_code == 409 and "supervisor-observed" in canonical.text
    listed = client.get(f"/api/v1/verification/receipts?campaign_id={current['id']}&limit=1").json()
    assert listed["page"]["total"] == 1 and listed["items"][0]["receipt_sha256"] == value["receipt_sha256"]
    with final_core.connect() as db:
        historical_id, _ = v5_graph._bridge_node(
            db, current["id"], source_type="legacy_fixture", source_ref=claim["id"],
            node_type="canonical_result", title="Historical self-attested result", body="",
            status="verified", attributes={"verification_receipt_id": value["id"],
                                            "verification_outcome": "verified"},
        )
    historical = client.get(f"/api/v1/research/nodes/{historical_id}")
    assert historical.status_code == 200
    assert historical.json()["canonical_trust"]["current"] is False


def test_repaired_and_healthy_negative_receipts_are_refuted_not_auto_promoted(client):
    for index, classification in enumerate(("repaired_negative", "healthy_negative")):
        current = campaign(client, f"https://negative-{index}.example.test")
        claim = graph_node(client, current["id"], "claim", f"Claim {index}")
        evidence = graph_node(client, current["id"], "counterevidence", f"Negative control {index}")
        requested = request(client, current["id"], claim["id"], [evidence["id"]])
        register_verifier(client, f"negative-runner-{index}")
        task = lease(client, f"negative-runner-{index}")
        body = receipt_body(f"negative-runner-{index}", classification, "refuted", evidence_ids=[evidence["id"]])
        receipt = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=body)
        assert receipt.status_code == 201 and receipt.json()["result"]["classification"] == classification
        with final_core.connect() as db:
            assert not db.execute("SELECT 1 FROM research_nodes WHERE node_type='canonical_result' AND campaign_id=?", (current["id"],)).fetchone()
        if classification == "repaired_negative":
            canonical = client.post("/api/v1/research/nodes", json={
                "campaign_id": current["id"], "node_type": "canonical_result", "title": "Repair verified",
                "source_ref": claim["id"], "attributes": {
                    "verification_receipt_id": receipt.json()["id"], "verification_outcome": "refuted",
                },
            })
            assert canonical.status_code == 409 and "supervisor-observed" in canonical.text


def test_invalid_precondition_and_interrupted_runs_never_promote(client):
    for index, classification in enumerate(("invalid_precondition", "interrupted")):
        current = campaign(client, f"https://inconclusive-{index}.example.test")
        claim = graph_node(client, current["id"], "claim", f"Inconclusive claim {index}")
        evidence = graph_node(client, current["id"], "observation", f"Incomplete run {index}")
        requested = request(client, current["id"], claim["id"], [evidence["id"]])
        runner_id = f"inconclusive-runner-{index}"
        register_verifier(client, runner_id)
        task = lease(client, runner_id)
        body = receipt_body(runner_id, classification, "inconclusive", evidence_ids=[evidence["id"]])
        receipt = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=body)
        assert receipt.status_code == 201
        promote = client.post("/api/v1/research/nodes", json={
            "campaign_id": current["id"], "node_type": "canonical_result", "title": "Must reject",
            "source_ref": claim["id"], "attributes": {
                "verification_receipt_id": receipt.json()["id"], "verification_outcome": "inconclusive",
            },
        })
        assert promote.status_code == 409


def test_tampered_inputs_and_self_verification_are_rejected(client):
    current, claim, evidence, _, task = verification_fixture(client)
    changed = client.patch(f"/api/v1/research/nodes/{evidence['id']}", json={"body": "changed after request"})
    assert changed.status_code == 200
    rejected = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=receipt_body(
        evidence_ids=[evidence["id"]],
    ))
    assert rejected.status_code == 409 and "changed" in rejected.text

    other = campaign(client, "https://self-verify.example.test")
    self_claim = graph_node(client, other["id"], "claim", "Self-certified claim",
                            attributes={"producer_runner_ref": "producer-runner"})
    self_evidence = graph_node(client, other["id"], "evidence", "Producer evidence")
    requested = request(client, other["id"], self_claim["id"], [self_evidence["id"]])
    register_verifier(client, "producer-runner")
    self_task = lease(client, "producer-runner")
    assert self_task["id"] == requested["request"]["verifier_task_id"]
    self_result = client.post(f"/api/v1/verification/tasks/{self_task['id']}/receipt", json=receipt_body(
        "producer-runner", evidence_ids=[self_evidence["id"]],
    ))
    assert self_result.status_code == 409 and "self-verify" in self_result.text


def test_receipt_rows_are_database_immutable_and_stale_inputs_block_promotion(client):
    current, claim, evidence, _, task = verification_fixture(client)
    value = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=receipt_body(
        evidence_ids=[evidence["id"]],
    )).json()
    with final_core.connect() as db:
        try:
            db.execute("UPDATE verification_receipts_v5 SET result_json='{}' WHERE id=?", (value["id"],))
        except sqlite3.IntegrityError as error:
            assert "immutable" in str(error)
        else:
            raise AssertionError("receipt update unexpectedly succeeded")
        try:
            db.execute("DELETE FROM verification_receipts_v5 WHERE id=?", (value["id"],))
        except sqlite3.IntegrityError as error:
            assert "immutable" in str(error)
        else:
            raise AssertionError("receipt delete unexpectedly succeeded")
    assert client.patch(f"/api/v1/research/nodes/{claim['id']}", json={"body": "claim changed"}).status_code == 409
    assert client.patch(f"/api/v1/research/nodes/{evidence['id']}", json={"body": "evidence changed"}).status_code == 409
    # Direct out-of-band database tampering cannot alter the receipt, but makes
    # its snapshot stale and therefore blocks promotion.
    with final_core.connect() as db:
        db.execute("UPDATE research_nodes SET body='claim changed out of band' WHERE id=?", (claim["id"],))
    read = client.get(f"/api/v1/verification/receipts/{value['id']}")
    assert read.status_code == 200 and read.json()["integrity"]["current_inputs_match"] is False
    promote = client.post("/api/v1/research/nodes", json={
        "campaign_id": current["id"], "node_type": "canonical_result", "title": "Stale result",
        "source_ref": claim["id"], "attributes": {
            "verification_receipt_id": value["id"], "verification_outcome": "verified",
        },
    })
    assert promote.status_code == 409


def test_replay_queues_new_independent_task_and_secret_fields_fail_closed(client):
    current, claim, evidence, requested, task = verification_fixture(client)
    value = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=receipt_body(
        evidence_ids=[evidence["id"]],
    )).json()
    replay = client.post(f"/api/v1/verification/receipts/{value['id']}/replay")
    assert replay.status_code == 202, replay.text
    replayed = replay.json()["request"]
    assert replayed["source_receipt_id"] == value["id"]
    assert replayed["verifier_task_id"] != requested["request"]["verifier_task_id"]
    duplicate = client.post(f"/api/v1/verification/receipts/{value['id']}/replay").json()
    assert duplicate["created"] is False and duplicate["request"]["id"] == replayed["id"]
    denied = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": current["id"], "evidence_ids": [evidence["id"]],
        "replay_contract": {"type": "http_replay", "authorization": "secret"},
    })
    assert denied.status_code == 422
