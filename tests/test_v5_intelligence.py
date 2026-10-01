import json
from pathlib import Path

import capability_registry
import final_core
import traditional_tools
import v5_verification
from tests.test_final import client, create_ready
from tests.test_v5_local_verifier import _server
from tests.test_v5_verification import register_verifier, receipt_body


def campaign(client, target="https://intel.example.test"):
    engagement = create_ready(client, target=target)
    response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Intelligence fixture", "objective": "Assess source-backed advisories",
    })
    assert response.status_code == 201, response.text
    return engagement, response.json()["id"]


def import_osv(client):
    response = client.post("/api/v1/intelligence/records", json={
        "adapter": "osv", "source_name": "fixture-feed", "license": "fixture-only",
        "source_url": "https://advisories.example.test/export",
        "records": [{
            "id": "OSV-TEST-1", "published": "2026-09-01T00:00:00Z",
            "summary": "Untrusted advisory body must not become a finding",
            "affected": [{"package": {"ecosystem": "PyPI", "name": "example-pkg"},
                          "versions": ["1.2.3"],
                          "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "1.2.4"}]}]}],
        }],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_source_adapter_fingerprint_applicability_and_no_auto_finding(client):
    engagement, campaign_id = campaign(client)
    foreign, foreign_campaign = campaign(client, "https://foreign-intel.example.test")
    assert {item["kind"] for item in client.get("/api/v1/intelligence/adapters").json()["items"]} == {"osv", "normalized"}
    first = import_osv(client)
    assert first["inserted"] == 1 and first["duplicates"] == 0
    assert import_osv(client)["inserted"] == 0
    result = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [
            {"ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3"},
            {"ecosystem": "PyPI", "name": "example-pkg", "version": "9.9.9"},
            {"ecosystem": "npm", "name": "example-pkg", "version": "1.2.3"},
        ],
    })
    assert result.status_code == 200, result.text
    matches = result.json()["matches"]
    assert len(matches) == 2
    assert {match["applicability"] for match in matches} == {"verification_required", "uncertain"}
    exact = next(match for match in matches if match["fingerprint"]["version"] == "1.2.3")
    assert exact["rationale"]["automated_reason"] == "exact_affected_version_listed"
    assert exact["fingerprint"]["trust"] == "operator_attested"
    again = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3"}],
    }).json()
    assert again["matches"][0]["id"] == exact["id"]
    assert client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["total"] == 2
    assert client.get(f"/api/v1/intelligence/matches?campaign_id={foreign_campaign}").json()["total"] == 0
    graph = client.get(f"/api/v1/research/campaigns/{campaign_id}/graph").json()
    assert {node["node_type"] for node in graph["nodes"]} == {"hypothesis", "observation"}
    assert all(node["attributes"]["candidate_only"] for node in graph["nodes"] if node["node_type"] == "hypothesis")
    assert "Untrusted advisory body" not in json.dumps(graph)
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM candidate_findings WHERE engagement_id=?", (engagement["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM canonical_findings WHERE engagement_id=?", (engagement["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM intel_matches WHERE campaign_id=?", (foreign_campaign,)).fetchone()[0] == 0


def test_manual_assessment_stays_hypothesis_and_verification_requires_receipt(client):
    _, campaign_id = campaign(client)
    import_osv(client)
    match = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3"}],
    }).json()["matches"][0]
    assessed = client.post(f"/api/v1/intelligence/matches/{match['id']}/assess", json={
        "applicability": "applicable", "rationale": "The operator confirms this package inventory applies.",
    })
    assert assessed.status_code == 200 and assessed.json()["applicability"] == "applicable"
    graph = client.get(f"/api/v1/research/campaigns/{campaign_id}/graph").json()
    assert any(node["node_type"] == "hypothesis" and node["status"] == "applicable" for node in graph["nodes"])
    claim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "claim", "title": "Potential vulnerable deployment",
    }).json()
    assert client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
        "claim_node_id": claim["id"], "receipt_id": "missing-receipt",
    }).status_code == 409
    edge = client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": claim["id"],
        "target_id": match["research_node_id"], "relation_type": "derived_from",
    })
    assert edge.status_code == 201
    assert client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
        "claim_node_id": claim["id"], "receipt_id": "missing-receipt",
    }).status_code == 404
    assert client.post(f"/api/v1/intelligence/matches/{match['id']}/assess", json={
        "applicability": "verified", "rationale": "Cannot self-certify applicability.",
    }).status_code == 422
    assert client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["items"][0]["applicability"] == "applicable"


def test_fingerprint_source_is_scoped_and_import_rejects_credential_url(client):
    _, campaign_id = campaign(client)
    other, _ = campaign(client, "https://other-intel.example.test")
    bad = client.post("/api/v1/intelligence/records", json={
        "adapter": "normalized", "source_name": "fixture", "license": "fixture-only",
        "source_url": "https://advisories.example.test/export?token=secret",
        "records": [{"id": "ADV-1", "affected": [{"ecosystem": "PyPI", "name": "example-pkg", "versions": ["1.0"]}]}],
    })
    assert bad.status_code == 422
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "foreign-intel-run", other["id"], "traditional", other["current_scope_snapshot_id"],
            other["current_policy_id"], "completed", "report", 0, now, None, None, now, now,
        ))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            "foreign-intel-artifact", "foreign-intel-run", "sbom", "/private/foreign.json",
            "test-sha", "application/json", 1, now,
        ))
    response = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg",
                                               "version": "1.0", "source_kind": "artifact",
                                               "source_ref": "foreign-intel-artifact"}],
    })
    assert response.status_code == 409


def test_verified_applicability_rejects_unobserved_runner_receipt_even_with_advisory_source(client):
    _, campaign_id = campaign(client)
    import_osv(client)
    match = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3"}],
    }).json()["matches"][0]
    advisory_id = match["rationale"]["advisory_node_id"]
    claim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "claim", "title": "Package applicability claim",
    }).json()
    evidence = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "evidence", "title": "Inventory proof",
    }).json()
    edge = client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": claim["id"],
        "target_id": match["research_node_id"], "relation_type": "derived_from",
    })
    assert edge.status_code == 201
    requested = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": campaign_id, "evidence_ids": [advisory_id, evidence["id"]],
        "replay_contract": {"type": "local_inventory_review", "source": "fixture"},
    })
    assert requested.status_code == 202, requested.text
    register_verifier(client, "intel-verifier", verifier_id="independent-intel-verifier")
    task = client.post("/api/v1/orchestration/lease", json={"runner_id": "intel-verifier", "lease_seconds": 300}).json()["task"]
    assert task["id"] == requested.json()["request"]["verifier_task_id"]
    issued = client.post(f"/api/v1/verification/tasks/{task['id']}/receipt", json=receipt_body(
        "intel-verifier", evidence_ids=[advisory_id, evidence["id"]],
    ))
    assert issued.status_code == 201, issued.text
    verified = client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
        "claim_node_id": claim["id"], "receipt_id": issued.json()["id"],
    })
    assert verified.status_code == 409 and "supervisor-observed" in verified.text
    current = client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["items"]
    assert next(item for item in current if item["id"] == match["id"])["applicability"] != "verified"
    graph = client.get(f"/api/v1/research/campaigns/{campaign_id}/graph").json()
    assert not any(node["node_type"] == "verification" for node in graph["nodes"])
    assert not any(edge["relation_type"] == "verified_by" for edge in graph["edges"])
    assert not any(node["node_type"] == "canonical_result" for node in graph["nodes"])
    receipt = client.get(f"/api/v1/verification/receipts/{issued.json()['id']}").json()
    assert receipt["integrity"]["current_inputs_match"] is True


def test_intelligence_match_rejects_observed_but_unrelated_http_oracle(client):
    server = _server()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        _, campaign_id = campaign(client, base)
        import_osv(client)
        match = client.post("/api/v1/intelligence/fingerprint", json={
            "campaign_id": campaign_id,
            "packages": [{"ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3"}],
        }).json()["matches"][0]
        contract = {"type": "loopback_http_status_v1", "url": f"{base}/positive",
                    "negative_control_url": f"{base}/negative", "expected_status": 200,
                    "negative_control_status": 403}
        claim = client.post("/api/v1/research/nodes", json={
            "campaign_id": campaign_id, "node_type": "claim", "title": "HTTP status relation",
            "attributes": {"claim_kind": "http_status_relationship",
                           "verification_contract_sha256": v5_verification._sha(contract)},
        }).json()
        evidence = client.post("/api/v1/research/nodes", json={
            "campaign_id": campaign_id, "node_type": "evidence", "title": "Local status observation",
        }).json()
        assert client.post("/api/v1/research/edges", json={
            "campaign_id": campaign_id, "source_id": claim["id"],
            "target_id": match["research_node_id"], "relation_type": "derived_from",
        }).status_code == 201
        requested = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
            "campaign_id": campaign_id,
            "evidence_ids": [match["rationale"]["advisory_node_id"], evidence["id"]],
            "replay_contract": contract,
        })
        assert requested.status_code == 202, requested.text
        tick = client.post("/api/v1/verification/local/tick")
        assert tick.status_code == 200, tick.text
        receipt_id = tick.json()["completed"][0]["receipt_id"]
        rejected = client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
            "claim_node_id": claim["id"], "receipt_id": receipt_id,
        })
        assert rejected.status_code == 409 and "oracle" in rejected.text
    finally:
        server.shutdown()
        server.server_close()


def _trivy_inventory_artifact(client, engagement, campaign_id, *, version="1.2.3", synthetic=False):
    now = final_core.utcnow()
    with final_core.connect() as db:
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "intel-trivy-run", engagement["id"], "traditional",
            engagement["current_scope_snapshot_id"], engagement["current_policy_id"],
            "completed", "report", int(synthetic), now, None, None, now, now,
        ))
    stdout = json.dumps({"Results": [{"Target": "requirements.txt", "Vulnerabilities": [{
        "VulnerabilityID": "OSV-TEST-1", "PkgName": "example-pkg",
        "InstalledVersion": version,
        "PkgIdentifier": {"PURL": f"pkg:pypi/example-pkg@{version}"},
        "Severity": "HIGH",
    }]}]})
    result = traditional_tools._persist_result(
        {"id": "intel-trivy-run", "engagement_id": engagement["id"], "mode": "traditional"},
        "trivy", capability_registry.ToolResultEnvelope(
            "trivy", "completed", 0, stdout, "", synthetic,
        ), traditional_tools.parse_output("trivy", stdout), [],
    )
    graph = client.get(f"/api/v1/research/campaigns/{campaign_id}/graph").json()
    artifact_node = next(node for node in graph["nodes"] if node["node_type"] == "artifact"
                         and node["source_ref"] == result["artifact_id"])
    return result["artifact_id"], artifact_node["id"]


def test_artifact_backed_exact_package_version_is_independently_verified(client):
    engagement, campaign_id = campaign(client)
    import_osv(client)
    artifact_id, artifact_node_id = _trivy_inventory_artifact(client, engagement, campaign_id)
    match = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg",
            "version": "1.2.3", "source_kind": "artifact", "source_ref": artifact_id}],
    }).json()["matches"][0]
    contract = {"type": "package_applicability_v1", "match_id": match["id"],
                "artifact_id": artifact_id}
    claim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "claim", "title": "Scanned source package matches exact advisory version",
        "attributes": {"claim_kind": "package_applicability",
                       "verification_contract_sha256": v5_verification._sha(contract)},
    }).json()
    assert client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": claim["id"],
        "target_id": match["research_node_id"], "relation_type": "derived_from",
    }).status_code == 201
    advisory_id = match["rationale"]["advisory_node_id"]
    request = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": campaign_id, "evidence_ids": [advisory_id, artifact_node_id],
        "replay_contract": contract,
    })
    assert request.status_code == 202, request.text
    tick = client.post("/api/v1/verification/local/tick")
    assert tick.status_code == 200 and tick.json()["completed"][0]["status"] == "succeeded", tick.text
    receipt_id = tick.json()["completed"][0]["receipt_id"]
    receipt = client.get(f"/api/v1/verification/receipts/{receipt_id}").json()
    assert receipt["result"]["status"] == "verified"
    assert receipt["result"]["oracle"]["kind"] == "package_applicability_v1"
    assert receipt["environment"]["process_isolation"]["sandbox"] == "macos-seatbelt"
    assert receipt["environment"]["process_isolation"]["file_read_denied"] is True
    assert receipt["environment"]["process_isolation"]["network_denied"] is True
    assert receipt["integrity"]["promotion_eligible"] is True
    assert receipt["integrity"]["promotion_domain"] == "applicability_only"
    overclaim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "canonical_result",
        "title": "Must not become a verified vulnerability", "source_ref": claim["id"],
        "attributes": {"verification_receipt_id": receipt_id, "verification_outcome": "verified"},
    })
    assert overclaim.status_code == 409 and "applicability" in overclaim.text
    verified = client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
        "claim_node_id": claim["id"], "receipt_id": receipt_id,
    })
    assert verified.status_code == 200, verified.text
    assert verified.json()["applicability"] == "verified" and verified.json()["current"] is True
    assert verified.json()["rationale"]["verification_scope"] == "source_artifact_exact_package_version_only"
    with final_core.connect() as db:
        artifact_path = Path(db.execute("SELECT uri FROM artifacts WHERE id=?", (artifact_id,)).fetchone()["uri"])
    artifact_path.write_text('{"tampered":true}')
    stale = client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["items"][0]
    assert stale["current"] is False
    integrity = client.get(f"/api/v1/verification/receipts/{receipt_id}").json()["integrity"]
    assert integrity["current_inputs_match"] is False and integrity["promotion_eligible"] is False


def test_synthetic_trivy_artifact_never_enters_local_package_verifier(client):
    engagement, campaign_id = campaign(client)
    import_osv(client)
    artifact_id, artifact_node_id = _trivy_inventory_artifact(
        client, engagement, campaign_id, synthetic=True,
    )
    match = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg",
            "version": "1.2.3", "source_kind": "artifact", "source_ref": artifact_id}],
    }).json()["matches"][0]
    contract = {"type": "package_applicability_v1", "match_id": match["id"],
                "artifact_id": artifact_id}
    claim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "claim", "title": "Synthetic source must not verify",
        "attributes": {"claim_kind": "package_applicability",
                       "verification_contract_sha256": v5_verification._sha(contract)},
    }).json()
    assert client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": claim["id"],
        "target_id": match["research_node_id"], "relation_type": "derived_from",
    }).status_code == 201
    queued = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": campaign_id,
        "evidence_ids": [match["rationale"]["advisory_node_id"], artifact_node_id],
        "replay_contract": contract,
    })
    assert queued.status_code == 202
    tick = client.post("/api/v1/verification/local/tick")
    assert tick.status_code == 200 and tick.json()["status"] == "idle"
    assert client.get(f"/api/v1/orchestration/tasks/{queued.json()['request']['verifier_task_id']}").json()["status"] == "queued"


def test_conflicting_scanned_version_is_inconclusive_not_verified(client):
    engagement, campaign_id = campaign(client)
    import_osv(client)
    artifact_id, artifact_node_id = _trivy_inventory_artifact(
        client, engagement, campaign_id, version="9.9.9",
    )
    match = client.post("/api/v1/intelligence/fingerprint", json={
        "campaign_id": campaign_id, "packages": [{"ecosystem": "PyPI", "name": "example-pkg",
            "version": "1.2.3", "source_kind": "artifact", "source_ref": artifact_id}],
    }).json()["matches"][0]
    contract = {"type": "package_applicability_v1", "match_id": match["id"],
                "artifact_id": artifact_id}
    claim = client.post("/api/v1/research/nodes", json={
        "campaign_id": campaign_id, "node_type": "claim", "title": "Potential package applicability",
        "attributes": {"claim_kind": "package_applicability",
                       "verification_contract_sha256": v5_verification._sha(contract)},
    }).json()
    assert client.post("/api/v1/research/edges", json={
        "campaign_id": campaign_id, "source_id": claim["id"],
        "target_id": match["research_node_id"], "relation_type": "derived_from",
    }).status_code == 201
    request = client.post(f"/api/v1/verification/claims/{claim['id']}", json={
        "campaign_id": campaign_id,
        "evidence_ids": [match["rationale"]["advisory_node_id"], artifact_node_id],
        "replay_contract": contract,
    })
    assert request.status_code == 202
    tick = client.post("/api/v1/verification/local/tick")
    assert tick.status_code == 200 and tick.json()["completed"][0]["status"] == "succeeded"
    receipt_id = tick.json()["completed"][0]["receipt_id"]
    receipt = client.get(f"/api/v1/verification/receipts/{receipt_id}").json()
    assert receipt["result"]["status"] == "inconclusive"
    assert receipt["result"]["oracle"]["observed_versions"] == ["9.9.9"]
    assert receipt["integrity"]["promotion_eligible"] is False
    denied = client.post(f"/api/v1/intelligence/matches/{match['id']}/verify", json={
        "claim_node_id": claim["id"], "receipt_id": receipt_id,
    })
    assert denied.status_code == 409


def test_new_advisory_revision_and_scope_change_require_fresh_fingerprint(client):
    engagement, campaign_id = campaign(client)
    import_osv(client)
    payload = {"campaign_id": campaign_id, "packages": [{
        "ecosystem": "PyPI", "name": "example-pkg", "version": "1.2.3",
    }]}
    old_match = client.post("/api/v1/intelligence/fingerprint", json=payload).json()["matches"][0]
    revised = client.post("/api/v1/intelligence/records", json={
        "adapter": "osv", "source_name": "fixture-feed", "license": "fixture-only",
        "records": [{"id": "OSV-TEST-1", "modified": "2026-09-30T00:00:00Z",
                     "affected": [{"package": {"ecosystem": "PyPI", "name": "example-pkg"},
                                   "versions": ["1.2.3", "1.2.4"]}]}],
    })
    assert revised.status_code == 201 and revised.json()["inserted"] == 1
    matches = client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["items"]
    assert len(matches) == 1 and matches[0]["is_latest_record"] is False and matches[0]["current"] is False
    assert client.post(f"/api/v1/intelligence/matches/{old_match['id']}/assess", json={
        "applicability": "applicable", "rationale": "Stale record must not be approved.",
    }).status_code == 409
    fresh = client.post("/api/v1/intelligence/fingerprint", json=payload).json()["matches"][0]
    assert fresh["id"] != old_match["id"] and fresh["is_latest_record"] is True and fresh["current"] is True
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO scope_snapshots VALUES(?,?,?,?,?,?,?,?)", (
            "intel-new-scope", engagement["id"], 2, "traditional", "{}", "fixture", now, now,
        ))
        db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id=? WHERE id=?", (
            "intel-new-scope", engagement["id"],
        ))
    assert client.post(f"/api/v1/intelligence/matches/{fresh['id']}/assess", json={
        "applicability": "applicable", "rationale": "Old scope must not be approved.",
    }).status_code == 409
    latest = client.get(f"/api/v1/intelligence/matches?campaign_id={campaign_id}").json()["items"]
    assert next(item for item in latest if item["id"] == fresh["id"])["current"] is False
