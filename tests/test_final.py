import asyncio
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
import final_core
import reporting
import web3_lab
import web3_analysis
import capability_registry
import traditional_tools
import traditional_runtime
import native_agent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SRC_ENABLE_SYNTHETIC_DEMO", "1")
    database = tmp_path / "final-test.db"
    monkeypatch.setattr(app, "DB", database)
    monkeypatch.setattr(final_core, "DB", database)
    monkeypatch.setattr(reporting, "EXPORTS", tmp_path / "exports")
    monkeypatch.setattr(web3_analysis, "ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(traditional_tools, "ARTIFACT_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(traditional_runtime, "ARTIFACT_ROOT", tmp_path / "artifacts")
    with TestClient(app.app) as test_client:
        yield test_client


def create_ready(client, mode="traditional", target="https://example.test"):
    response = client.post("/api/v1/engagements", json={
        "name": "Acceptance target", "target": target, "mode": mode,
        "scope": {}, "policy": {},
    })
    assert response.status_code == 201
    engagement = response.json()
    confirmed = client.post(f"/api/v1/engagements/{engagement['id']}/confirm")
    assert confirmed.status_code == 200
    return confirmed.json()


def test_target_resolution_and_immutable_scope(client):
    target = client.post("/api/v1/targets/resolve", json={"target": "Example.TEST/path", "mode": "traditional"})
    assert target.status_code == 200
    assert target.json()["normalized_target"] == "https://example.test"
    engagement = create_ready(client)
    with sqlite3.connect(final_core.DB) as db:
        scope = db.execute("SELECT version,confirmed_at FROM scope_snapshots WHERE id=?", (engagement["current_scope_snapshot_id"],)).fetchone()
    assert scope[0] == 1 and scope[1]


def test_bulk_archive_recent_projects_preserves_records(client):
    first = create_ready(client, target="https://one.example.test")
    second = create_ready(client, target="https://two.example.test")
    response = client.post("/api/v1/engagements/bulk-archive?mode=traditional")
    assert response.status_code == 200
    assert response.json() == {"mode": "traditional", "archived": 2, "evidence_preserved": True}
    assert client.get("/api/v1/engagements?mode=traditional").json() == []
    assert client.get(f"/api/v1/engagements/{first['id']}").status_code == 200
    assert client.get(f"/api/v1/engagements/{second['id']}").status_code == 200


def test_traditional_local_and_git_repository_targets_keep_repository_identity(client, tmp_path):
    local = client.post("/api/v1/targets/resolve", json={"target": str(tmp_path), "mode": "traditional"})
    assert local.status_code == 200 and local.json()["target_type"] == "repository"
    assert local.json()["normalized_target"] == str(tmp_path.resolve())
    remote = client.post("/api/v1/targets/resolve", json={"target": "https://github.com/acme/project.git", "mode": "traditional"})
    assert remote.status_code == 200 and remote.json()["target_type"] == "repository"
    assert remote.json()["normalized_target"].endswith("/acme/project.git")


def test_identity_workspace_crud_and_role_matrix_never_echoes_credentials(client):
    engagement = create_ready(client, target="https://roles.test")
    first = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Tenant A admin", "role": "admin", "tenant": "tenant-a",
        "credential_ref": "keychain://fieldwork/tenant-a-admin",
        "auth_type": "keychain_reference", "session_status": "ready",
    })
    second = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Tenant B viewer", "role": "viewer", "tenant": "tenant-b",
        "auth_type": "cookie", "session_status": "needs_login",
    })
    assert first.status_code == second.status_code == 201
    assert first.json()["credential_configured"] is True
    assert "credential_ref" not in first.json()

    matrix = client.get(f"/api/v1/engagements/{engagement['id']}/role-matrix").json()
    assert len(matrix["identities"]) == 2
    assert len(matrix["pairs"]) == 1
    assert {matrix["pairs"][0]["left_id"], matrix["pairs"][0]["right_id"]} == {first.json()["id"], second.json()["id"]}
    assert matrix["pairs"][0] | {"left_id": "", "right_id": ""} == {
        "left_id": "", "right_id": "", "cross_role": True, "cross_tenant": True, "ready": False,
    }
    assert matrix["ready_pairs"] == 0

    updated = client.patch(f"/api/v1/identities/{second.json()['id']}", json={"session_status": "ready"})
    assert updated.status_code == 200 and updated.json()["last_validated_at"]
    assert client.get(f"/api/v1/engagements/{engagement['id']}/role-matrix").json()["ready_pairs"] == 1
    assert client.delete(f"/api/v1/identities/{second.json()['id']}").status_code == 200
    assert len(client.get(f"/api/v1/engagements/{engagement['id']}/identities").json()) == 1

    secret = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Unsafe", "role": "admin", "credential_ref": "password=plain-text",
    })
    assert secret.status_code == 422


def test_execution_plan_exposes_real_tools_budgets_degradation_and_scope_blockers(client, monkeypatch):
    monkeypatch.setattr(final_core, "capability_inventory", lambda: [
        {"id": "subfinder", "available": True, "configured": True, "ready": True, "version": "v1"},
        {"id": "httpx", "available": True, "configured": True, "ready": True, "version": "v2"},
        {"id": "katana", "available": False, "configured": False, "ready": False},
        {"id": "nuclei", "available": True, "configured": True, "ready": True, "version": "v3"},
    ])
    monkeypatch.setattr(native_agent, "readiness", lambda: {
        "available": True, "configured": False, "ready": False, "browser": "system_chrome",
    })
    draft = client.post("/api/v1/engagements", json={
        "name": "Plan target", "target": "https://plan.test", "mode": "traditional",
        "policy": {"max_requests": 77, "max_runtime_minutes": 12},
    }).json()
    blocked = client.post(f"/api/v1/engagements/{draft['id']}/execution-plan", json={"include_native_agent": True})
    assert blocked.status_code == 200 and blocked.json()["ready"] is False
    assert {item["id"] for item in blocked.json()["blockers"]} == {"scope"}
    client.post(f"/api/v1/engagements/{draft['id']}/confirm")
    plan = client.post(f"/api/v1/engagements/{draft['id']}/execution-plan", json={"include_native_agent": True}).json()
    assert plan["ready"] is True
    assert plan["budget"]["requests"] == 77 and plan["budget"]["runtime_minutes"] == 12
    assert [item["id"] for item in plan["tools"]] == ["subfinder", "httpx", "katana", "nuclei", "native-agent"]
    assert {item["id"] for item in plan["warnings"]} >= {"katana", "native-agent", "role_coverage"}
    assert "production_write" in plan["denied_actions"]


def test_task_center_reports_remaining_stages_and_actionable_next_step(client):
    ready = create_ready(client, target="https://task-center.test")
    run = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"execution_mode": "demo"})
    assert run.status_code == 202
    center = client.get("/api/v1/task-center?mode=traditional")
    assert center.status_code == 200
    item = next(value for value in center.json()["items"] if value["id"] == run.json()["id"])
    assert item["status"] in {"queued", "running", "completed"}
    assert isinstance(item["remaining_stages"], list)
    assert item["next_action"]
    assert center.json()["stall_timeout_seconds"] == 180


def test_scope_required_and_run_checkpoints(client):
    draft = client.post("/api/v1/engagements", json={"name": "Draft target", "target": "https://draft.test", "mode": "traditional"}).json()
    denied = client.post(f"/api/v1/engagements/{draft['id']}/start")
    assert denied.status_code == 409
    ready = create_ready(client, target="https://ready.test")
    started = client.post(f"/api/v1/engagements/{ready['id']}/start")
    assert started.status_code == 202 and started.json()["synthetic"] is True
    run_id = started.json()["id"]
    import time
    time.sleep(2.5)
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert len([e for e in run["events"] if e["kind"] == "stage.completed"]) == 8
    assert all(e["payload"].get("synthetic") for e in run["events"] if e["kind"] == "stage.completed")


def test_multiple_runs_can_execute_concurrently_and_have_zero_finding_report(client):
    first = create_ready(client, target="https://parallel-one.test")
    second = create_ready(client, target="https://parallel-two.test")
    run_one = client.post(f"/api/v1/engagements/{first['id']}/start")
    run_two = client.post(f"/api/v1/engagements/{second['id']}/start")
    assert run_one.status_code == 202 and run_two.status_code == 202
    report = client.get(f"/api/v1/runs/{run_one.json()['id']}/summary-report")
    assert report.status_code == 200
    assert report.json()["counts"]["verified"] == 0
    assert report.json()["counts"]["tests"] == 5
    assert len(report.json()["tests"]) == 5
    assert "实际测试与反馈" in report.json()["content"]
    assert "未形成已验证漏洞" in report.json()["content"]
    details = client.get(f"/api/v1/runs/{run_one.json()['id']}/details")
    assert details.status_code == 200
    assert [item["id"] for item in details.json()["test_items"]] == ["subfinder", "httpx", "katana", "nuclei", "native-agent"]
    assert len(details.json()["stages"]) == 8


def test_bulk_archive_run_candidates_preserves_evidence_and_report(client):
    engagement = create_ready(client, target="https://candidate-cleanup.test")
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "http.route", "subject": "GET /admin", "summary": "Route observed",
        "source_capability": "katana", "confidence": .6,
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Admin route hypothesis", "category": "exposure",
        "target": "https://candidate-cleanup.test/admin", "hypothesis": "Needs authorization replay",
        "observation_ids": [observation["id"]],
    })
    assert candidate.status_code == 201
    assert client.post(f"/api/v1/runs/{run_id}/findings/archive-candidates").status_code == 409
    with sqlite3.connect(final_core.DB) as db:
        db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
    cleared = client.post(f"/api/v1/runs/{run_id}/findings/archive-candidates")
    assert cleared.status_code == 200
    assert cleared.json()["archived"] == 1 and cleared.json()["evidence_preserved"] is True
    assert client.get(f"/api/v1/findings?run_id={run_id}").json()["candidates"] == []
    details = client.get(f"/api/v1/runs/{run_id}/details").json()
    assert any(item["id"] == observation["id"] for item in details["observations"])
    report = client.get(f"/api/v1/runs/{run_id}/summary-report")
    assert report.status_code == 200 and "实际测试与反馈" in report.json()["content"]


def test_production_mode_rejects_public_demo_execution(client, monkeypatch):
    ready = create_ready(client, target="https://production-only.test")
    monkeypatch.delenv("SRC_ENABLE_SYNTHETIC_DEMO")
    rejected = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"execution_mode": "demo"})
    assert rejected.status_code == 422
    assert "生产模式不提供演示执行" in rejected.json()["detail"]


def test_projects_and_findings_are_editable_and_archived_without_erasing_run(client):
    engagement = create_ready(client, target="https://editable.test")
    renamed = client.patch(f"/api/v1/engagements/{engagement['id']}", json={"name": "Renamed project"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "Renamed project"
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    import time
    time.sleep(.1)
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "header", "subject": "GET /", "summary": "Header candidate",
        "source_capability": "http", "confidence": .7,
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Old title", "category": "configuration", "target": "https://editable.test",
        "hypothesis": "Old hypothesis", "observation_ids": [observation["id"]],
    }).json()
    changed = client.patch(f"/api/v1/findings/{candidate['id']}", json={"title": "Reviewed title", "hypothesis": "Reviewed hypothesis"})
    assert changed.status_code == 200 and changed.json()["title"] == "Reviewed title"
    assert client.delete(f"/api/v1/findings/{candidate['id']}").status_code == 200
    assert client.get(f"/api/v1/findings?run_id={run_id}").json()["candidates"] == []
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200


def test_tool_output_normalizes_to_observation(client):
    ready = create_ready(client, target="https://observe.test")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "http.response", "subject": "GET /health",
        "summary": "HTTP 200 with version header", "source_capability": "http",
        "confidence": .8,
    })
    assert observation.status_code == 201
    with sqlite3.connect(final_core.DB) as db:
        assert db.execute("SELECT COUNT(*) FROM observations WHERE run_id=?", (run_id,)).fetchone()[0] == 1


def test_cross_observation_correlation_creates_candidate_not_finding(client):
    ready = create_ready(client, target="https://correlate.test")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    for source in ("http", "browser"):
        response = client.post(f"/api/v1/runs/{run_id}/observations", json={
            "observation_type": "authorization", "subject": "https://correlate.test/api/orders/42",
            "summary": f"Role boundary difference from {source}", "source_capability": source, "confidence": .8,
        })
        assert response.status_code == 201
    correlated = client.post(f"/api/v1/runs/{run_id}/correlate").json()
    assert correlated["count"] == 1 and correlated["created"][0]["status"] == "candidate"
    findings = client.get("/api/v1/findings?mode=traditional").json()
    assert not findings["verified"]


def test_web3_network_guard_and_program_versions(client):
    engagement = create_ready(client, "web3", "0x1111111111111111111111111111111111111111")
    first = client.post("/api/v1/program-snapshots", json={"engagement_id": engagement["id"], "rules": {"poc": "local-fork-only"}})
    second = client.post("/api/v1/program-snapshots", json={"engagement_id": engagement["id"], "rules": {"poc": "updated"}})
    assert first.json()["version"] == 1 and second.json()["version"] == 2
    for network in ("production", "public_testnet"):
        result = client.post("/api/v1/web3/execution/check", json={"engagement_id": engagement["id"], "network_class": network, "action": "write"}).json()
        assert result["allowed"] is False
    local = client.post("/api/v1/web3/execution/check", json={"engagement_id": engagement["id"], "network_class": "local_fork", "action": "write"}).json()
    assert local["allowed"] is True and local["requires_real_private_key"] is False
    key = client.post("/api/v1/web3/execution/check", json={"engagement_id": engagement["id"], "network_class": "local_fork", "action": "sign", "uses_real_private_key": True}).json()
    assert key["allowed"] is False


def test_report_renderer_never_invents_missing_fields():
    model = {
        "id": "finding-1", "mode": "traditional", "title": "Verified issue",
        "affected_asset": "https://example.test", "location": None, "summary": None,
        "root_cause": None, "weakness": None, "severity": "medium",
        "scope": {"snapshot_id": "scope-1", "in_scope": True},
        "reproduction": {"steps": None, "poc_artifact_ids": None},
        "impact": {"description": None}, "verification": {}, "eligibility": {},
        "evidence_ids": [], "evidence": [], "remediation": None, "platform_custom": {},
    }
    content, check, _ = reporting.render(model, "generic_src")
    assert check["ready"] is False
    assert "MISSING_REQUIRED_FIELD" in content
    assert "reproduction.steps" in check["missing_required"]


def test_redaction_covers_context_and_export():
    value = "Authorization: Bearer super-secret Cookie: sid=abc password=hunter2 private_key=0xdead"
    clean = reporting.redact(value)
    assert "super-secret" not in clean and "hunter2" not in clean and "0xdead" not in clean
    assert clean.count("[REDACTED]") == 4


def test_all_report_adapters_and_structured_export_consistency():
    model = {
        "id": "finding-golden", "mode": "traditional", "title": "Authorization bypass",
        "affected_asset": "https://golden.test", "location": "GET /api/items/{id}",
        "summary": "Cross-role access reproduced", "root_cause": "Missing owner check",
        "weakness": {"id": "CWE-639", "platform_taxonomy": "P4"}, "severity": "high",
        "scope": {"snapshot_id": "scope-golden", "in_scope": True},
        "reproduction": {"steps": ["Login as role B", "Request role A item"], "expected": "403", "actual": "200", "poc_artifact_ids": ["poc-1"]},
        "impact": {"description": "Unauthorized data access", "feasibility": "reproduced"},
        "verification": {"attempts": 2}, "eligibility": {}, "evidence_ids": ["evidence-1"],
        "evidence": [{"id": "evidence-1", "type": "http", "summary": "redacted exchange", "artifact_id": "artifact-1"}],
        "remediation": "Enforce ownership", "platform_custom": {"testing_ip": "127.0.0.1"},
        "program_snapshot": "program-1", "impact_in_scope": True, "known_issue_check": True,
        "previous_audit_check": True, "primacy_mode": "human-reviewed", "poc_rule_check": True,
    }
    for platform in ("generic_src", "cn_src_generic", "hackerone", "bugcrowd", "intigriti", "immunefi"):
        content, check, _ = reporting.render(model, platform)
        assert check["ready"] is True, (platform, check)
        assert model["title"] in content and model["affected_asset"] in content
    markdown, _, _ = reporting.render(model, "markdown")
    structured, _, _ = reporting.render(model, "json")
    assert model["title"] in markdown
    assert json.loads(structured)["report"]["title"] == model["title"]
    sarif, _, _ = reporting.render(model, "sarif")
    assert json.loads(sarif)["properties"]["applicable"] is True
    web3_model = {**model, "mode": "web3"}
    web3_sarif, _, _ = reporting.render(web3_model, "sarif")
    assert json.loads(web3_sarif)["runs"][0]["results"] == []


def test_candidate_requires_real_oracle_then_compiles_report(client):
    engagement = create_ready(client, target="https://verify.test")
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "http.authorization", "subject": "GET /api/orders/42",
        "summary": "Second role received a distinct tenant object", "source_capability": "browser-http",
        "confidence": .9,
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Cross-tenant order access", "category": "CWE-639",
        "target": "https://verify.test/api/orders/42", "hypothesis": "Object ownership is not enforced",
        "observation_ids": [observation["id"]],
    }).json()
    proof = {
        "oracle": "deterministic-demo-oracle", "attempts": 2, "reproduced": True,
        "counterevidence_checked": True, "counterevidence_summary": "Owner role still works",
        "severity": "high", "impact_description": "Cross-tenant read access",
        "steps": ["Authenticate as role B", "Request role A object"],
        "expected": "403", "actual": "200", "root_cause": "Missing ownership check",
        "weakness": "CWE-639", "location": "GET /api/orders/{id}", "poc_artifact_ids": ["poc-local-1"],
    }
    blocked = client.post(f"/api/v1/candidates/{candidate['id']}/verify", json=proof)
    assert blocked.json()["status"] == "human_review"
    proof["oracle"] = "http-state-replay-v1"
    verified = client.post(f"/api/v1/candidates/{candidate['id']}/verify", json=proof)
    assert verified.status_code == 200 and verified.json()["status"] == "verified"
    finding_id = verified.json()["id"]
    preview = client.post(f"/api/v1/findings/{finding_id}/reports/hackerone/preview")
    assert preview.status_code == 200
    assert preview.json()["completeness"]["ready"] is True
    package = client.post(f"/api/v1/findings/{finding_id}/reports/hackerone/export")
    assert package.status_code == 202
    download = client.get(package.json()["download_url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    capsule = client.get(f"/api/v1/findings/{finding_id}/proof-capsule")
    assert capsule.status_code == 200
    assert capsule.json()["portable"] is True and len(capsule.json()["sha256"]) == 64


def test_policy_denies_out_of_scope_destructive_and_third_party(client):
    engagement = create_ready(client, target="https://scope.test")
    base = {"engagement_id": engagement["id"], "target": "https://scope.test", "action": "read"}
    assert client.post("/api/v1/policy/check", json=base).json()["allowed"] is True
    assert client.post("/api/v1/policy/check", json={**base, "target": "https://other.test"}).json()["reason"] == "out_of_scope"
    assert client.post("/api/v1/policy/check", json={**base, "destructive": True}).json()["reason"] == "destructive_action_not_approved"
    assert client.post("/api/v1/policy/check", json={**base, "third_party_active": True}).json()["reason"] == "third_party_active_test_denied"
    assert client.post("/api/v1/policy/check", json={**base, "third_party_passive": True}).json()["reason"] == "third_party_passive_query_denied"


def test_subdomain_scope_requires_explicit_same_scheme_and_port_opt_in(client):
    base = create_ready(client, target="https://scope.test")
    denied = client.post("/api/v1/policy/check", json={"engagement_id": base["id"], "target": "https://api.scope.test", "action": "read"}).json()
    assert denied["allowed"] is False
    created = client.post("/api/v1/engagements", json={
        "name": "Wildcard program", "target": "https://scope.test", "mode": "traditional",
        "scope": {"allow_subdomains": True},
    }).json()
    allowed = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    assert client.post("/api/v1/policy/check", json={"engagement_id": allowed["id"], "target": "https://api.scope.test", "action": "read"}).json()["allowed"] is True
    assert client.post("/api/v1/policy/check", json={"engagement_id": allowed["id"], "target": "http://api.scope.test", "action": "read"}).json()["allowed"] is False


def test_rate_limit_and_atomic_request_budget(client):
    ready = create_ready(client, target="https://rate.test")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    first = client.post(f"/api/v1/runs/{run_id}/requests/authorize", json={"target": "https://rate.test", "action": "read"}).json()
    second = client.post(f"/api/v1/runs/{run_id}/requests/authorize", json={"target": "https://rate.test", "action": "read"}).json()
    assert first["allowed"] is True
    assert second["allowed"] is False and second["reason"] == "rate_limit"
    budget = client.get(f"/api/v1/runs/{run_id}/budget").json()
    assert budget["requests_used"] == 1


def test_resume_skips_existing_checkpoint(client):
    ready = create_ready(client, target="https://resume.test")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    import time
    time.sleep(.42)
    paused = client.post(f"/api/v1/runs/{run_id}/pause")
    assert paused.status_code == 200
    done_before = len([x for x in paused.json()["events"] if x["kind"] == "stage.completed"])
    resumed = client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    time.sleep(1.8)
    finished = client.get(f"/api/v1/runs/{run_id}").json()
    stages_done = [x["stage"] for x in finished["events"] if x["kind"] == "stage.completed"]
    assert finished["status"] == "completed"
    assert len(stages_done) == 8 and len(set(stages_done)) == 8
    assert done_before >= 1


@pytest.mark.skipif(not web3_lab.binary("anvil"), reason="Anvil optional capability not installed")
def test_real_anvil_local_fork_mutation(client):
    engagement = create_ready(client, "web3", "0x2222222222222222222222222222222222222222")
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    fork = client.post("/api/v1/web3/forks", json={"engagement_id": engagement["id"], "chain_id": 31337})
    assert fork.status_code == 201
    assert fork.json()["network_class"] == "local_fork"
    fork_id = fork.json()["id"]
    mutation = client.post(f"/api/v1/web3/forks/{fork_id}/mutate", json={
        "run_id": run_id,
        "address": "0x0000000000000000000000000000000000000001",
        "balance_wei": 987654321,
    })
    assert mutation.status_code == 200
    assert mutation.json()["after_wei"] == 987654321
    assert mutation.json()["broadcast"] is False
    stopped = client.post(f"/api/v1/web3/forks/{fork_id}/stop")
    assert stopped.json()["status"] == "stopped"


def test_web3_chain_real_run_requires_source_and_https_fork(client):
    engagement = create_ready(client, "web3", "0x2222222222222222222222222222222222222222")
    missing = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "real"})
    assert missing.status_code == 422 and "source" in missing.json()["detail"]
    fixture = app.ROOT / "fixtures" / "web3-vault"
    insecure = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={
        "execution_mode": "real", "source_path": str(fixture), "fork_rpc_url": "http://rpc.example.test",
    })
    assert insecure.status_code == 422 and "HTTPS" in insecure.json()["detail"]


def test_web3_deployment_alignment_binds_bytecode_block_chain_and_program_snapshot(client, monkeypatch):
    address = "0x3333333333333333333333333333333333333333"
    engagement = create_ready(client, "web3", address)
    fixture = app.ROOT / "fixtures" / "web3-vault"
    artifact = json.loads((fixture / "out" / "Vault.sol" / "Vault.json").read_text())
    runtime = artifact["deployedBytecode"]["object"]

    class RpcHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            results = {
                "eth_chainId": "0x7a69",
                "eth_blockNumber": "0x2a",
                "eth_getCode": runtime,
                "eth_getStorageAt": "0x" + "00" * 32,
            }
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": results[body["method"]]}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), RpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(web3_analysis, "run_forge_build", lambda _root: {"status": "compiled"})
    rpc_url = f"http://127.0.0.1:{server.server_port}"
    try:
        response = client.post("/api/v1/web3/deployment-alignments", json={
            "engagement_id": engagement["id"], "source_path": str(fixture), "rpc_url": rpc_url,
            "contract_address": address, "artifact_contract": "Vault", "expected_chain_id": 31337,
        })
    finally:
        server.shutdown(); server.server_close()
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "aligned" and value["runtime_bytecode_match"] is True
    assert value["chain_id"] == 31337 and value["block_number"] == 42
    assert value["artifact_contract"] == "Vault" and value["compiler_version"].startswith("0.8.24")
    assert "rpc" not in json.dumps(value).lower()
    snapshots = client.get(f"/api/v1/web3/engagements/{engagement['id']}/deployment-alignments").json()
    assert snapshots[0]["id"] == value["program_snapshot_id"]
    persisted = json.dumps(snapshots)
    assert rpc_url not in persisted and runtime not in persisted
    plan = client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan", json={
        "execution_mode": "real", "source_path": str(fixture), "fork_rpc_url": "https://rpc.example.test",
        "fork_block_number": 42, "chain_id": 31337,
    })
    assert plan.status_code == 200
    assert "deployment_alignment" not in {item["id"] for item in plan.json()["blockers"]}


def test_web3_deployment_alignment_blocks_bytecode_or_chain_mismatch(client, monkeypatch):
    address = "0x4444444444444444444444444444444444444444"
    engagement = create_ready(client, "web3", address)
    fixture = app.ROOT / "fixtures" / "web3-vault"

    class RpcHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            results = {"eth_chainId": "0x1", "eth_blockNumber": "0x64", "eth_getCode": "0x6001600055", "eth_getStorageAt": "0x" + "00" * 32}
            payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": results[body["method"]]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), RpcHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    monkeypatch.setattr(web3_analysis, "run_forge_build", lambda _root: {"status": "compiled"})
    try:
        response = client.post("/api/v1/web3/deployment-alignments", json={
            "engagement_id": engagement["id"], "source_path": str(fixture),
            "rpc_url": f"http://127.0.0.1:{server.server_port}", "contract_address": address,
            "artifact_contract": "Vault", "expected_chain_id": 31337,
        })
    finally:
        server.shutdown(); server.server_close()
    assert response.status_code == 201
    value = response.json()
    assert value["status"] == "blocked" and value["runtime_bytecode_match"] is False
    assert {item.split(":")[0] for item in value["blockers"]} == {"chain_id_mismatch", "runtime_bytecode_mismatch"}
    plan = client.post(f"/api/v1/engagements/{engagement['id']}/execution-plan", json={
        "execution_mode": "real", "source_path": str(fixture), "fork_rpc_url": "https://rpc.example.test",
    }).json()
    assert "deployment_alignment" in {item["id"] for item in plan["blockers"]}


@pytest.mark.skipif(not web3_lab.binary("forge"), reason="Forge optional capability not installed")
def test_foundry_compile_and_protocol_model(client):
    engagement = create_ready(client, "web3", "0x3333333333333333333333333333333333333333")
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    fixture = app.ROOT / "fixtures" / "web3-vault"
    result = client.post("/api/v1/web3/source/inspect", json={
        "engagement_id": engagement["id"], "run_id": run_id, "source_path": str(fixture),
        "source_commit": "fixture-commit", "deployed_address": "0x3333333333333333333333333333333333333333",
    })
    assert result.status_code == 200
    data = result.json()
    assert data["framework"] == "foundry"
    assert data["compile"]["status"] == "compiled"
    assert data["fuzz"]["status"] == "passed" and data["fuzz"]["runs"] == 64
    assert data["compiler_model"] and data["compiler_model"][0]["abi"]
    assert data["observation_id"].startswith("obs-")
    assert data["model"]["contracts"][0]["name"] == "Vault"
    assert any(x["category"] == "accounting" for x in data["model"]["invariants"])
    adapter = client.post("/api/v1/web3/tools/forge/run", json={
        "engagement_id": engagement["id"], "run_id": run_id,
        "source_path": str(fixture), "args": ["--version"],
    })
    assert adapter.status_code == 200
    assert adapter.json()["tool_result"]["status"] == "completed"
    assert adapter.json()["observation_id"].startswith("obs-")


def test_capability_registry_is_truthful_and_versioned(client):
    capabilities = client.get("/api/v1/capabilities").json()
    ids = {item["id"] for item in capabilities}
    assert {"strix", "pentest-ai", "nuclei", "semgrep", "forge", "anvil", "slither", "echidna", "medusa"} <= ids
    forge = next(item for item in capabilities if item["id"] == "forge")
    strix = next(item for item in capabilities if item["id"] == "strix")
    assert "configured" in strix
    assert isinstance(strix["sandbox_ready"], bool)
    assert strix["ready"] is (strix["available"] and strix["configured"] and strix["sandbox_ready"])
    assert strix["requires_docker"] is True and strix["execution_backend"] == "docker_optional"
    assert forge["requires_docker"] is False and forge["execution_backend"] == "local_native"
    assert forge["available"] is bool(web3_lab.binary("forge"))
    if forge["available"]:
        assert "forge" in forge["version"].lower()
    plan = client.post("/api/v1/router/plan", json={"mode": "web3", "task": "fuzz", "model_budget_usd": 0}).json()
    assert "forge" in plan["selected_capabilities"]
    assert plan["model_route"] == "none" and plan["policy"] == "local_deterministic_first"

    readiness = client.get("/api/v1/runtime/readiness").json()
    assert readiness["backend"] == "local_native" and readiness["docker_required"] is False
    assert readiness["required_core"] == 14
    assert set(readiness["optional_docker_agents"]) == {"strix", "shannon"}
    assert readiness["native_agent"]["requires_docker"] is False
    assert readiness["native_agent"]["execution_backend"] == "local_native"


def test_native_agent_is_truthful_and_never_required_for_deterministic_run(client, monkeypatch):
    import native_agent
    monkeypatch.setattr(native_agent, "CHROME", Path("/definitely/missing/chrome"))
    status = native_agent.readiness()
    assert status["available"] is False and status["ready"] is False
    assert status["requires_docker"] is False


def test_native_agent_rejects_out_of_scope_navigation_before_browser(client, monkeypatch):
    import native_agent
    created = client.post("/api/v1/engagements", json={
        "name": "Native agent scope", "target": "https://example.test", "mode": "traditional",
    }).json()
    ready = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"include_native_agent": False}).json()["id"]
    with final_core.connect() as db:
        run = dict(db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone())
    engagement = final_core.get_engagement(ready["id"])
    with pytest.raises(ValueError, match="navigation_denied:out_of_scope"):
        native_agent._observe_page(run, engagement, "https://outside.example/path")


def test_native_agent_hypotheses_remain_candidates(client):
    import native_agent
    created = client.post("/api/v1/engagements", json={
        "name": "Native agent candidate", "target": "https://example.test", "mode": "traditional",
    }).json()
    ready = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"include_native_agent": False}).json()["id"]
    with final_core.connect() as db:
        run = dict(db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone())
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run_id, ready["id"], "traditional", "browser.page_observation",
            "https://example.test/account", "Observed account page", .7, "native-agent-browser", None, final_core.utcnow(),
        ))
    ids = native_agent._record_hypotheses(run, [{
        "title": "Possible authorization boundary", "category": "authorization",
        "target": "https://example.test/account", "summary": "Requires independent replay with a negative control",
    }], [observation_id])
    assert len(ids) == 1
    with final_core.connect() as db:
        candidate = db.execute("SELECT status FROM candidate_findings WHERE id=?", (ids[0],)).fetchone()
        verified = db.execute("SELECT 1 FROM canonical_findings WHERE candidate_id=?", (ids[0],)).fetchone()
    assert candidate["status"] == "candidate" and verified is None


def test_cache_clear_only_removes_regenerable_directories(client, monkeypatch, tmp_path):
    data_root = tmp_path / "maintenance-data"
    monkeypatch.setattr(final_core, "LOCAL_DATA_ROOT", data_root)
    workspace = data_root / "agent_workspaces" / "run-test"
    repository = data_root / "repositories" / "run-test"
    artifact = data_root / "artifacts"
    workspace.mkdir(parents=True); repository.mkdir(parents=True); artifact.mkdir(parents=True)
    (workspace / "page.json").write_text("cache")
    (repository / "source.txt").write_text("cache")
    (artifact / "evidence.json").write_text("keep")
    denied = client.post("/api/v1/maintenance/cache/clear", json={"confirmation": "wrong"})
    assert denied.status_code == 422
    result = client.post("/api/v1/maintenance/cache/clear", json={"confirmation": "CLEAR_CACHE"})
    assert result.status_code == 200 and result.json()["removed_files"] == 2
    assert not workspace.exists() and not repository.exists()
    assert (artifact / "evidence.json").is_file()


def test_recent_records_clear_creates_recovery_backup(client, monkeypatch, tmp_path):
    data_root = tmp_path / "maintenance-data"
    monkeypatch.setattr(final_core, "LOCAL_DATA_ROOT", data_root)
    created = create_ready(client, target="https://clear-records.test")
    assert created["id"]
    result = client.request("DELETE", "/api/v1/maintenance/recent-records", json={"confirmation": "CLEAR_RECENT_RECORDS"})
    assert result.status_code == 200, result.text
    backup = Path(result.json()["backup"])
    assert backup.is_file() and backup.stat().st_size > 0
    assert client.get("/api/v1/engagements").json() == []


def test_httpx_detection_skips_python_cli_name_collision(monkeypatch, tmp_path):
    python_httpx = tmp_path / "python-httpx"
    pd_httpx = tmp_path / "pd-httpx"
    python_httpx.touch(); pd_httpx.touch()
    monkeypatch.setattr(capability_registry, "executable_candidates", lambda _: [str(python_httpx), str(pd_httpx)])
    class Result:
        returncode = 0
        stderr = ""
        def __init__(self, stdout): self.stdout = stdout
    monkeypatch.setattr(capability_registry.subprocess, "run", lambda argv, **_: Result(
        "The httpx Python HTTP client" if argv[0] == str(python_httpx) else "[INF] Current Version: v1.10.0\nprojectdiscovery.io"
    ))
    detected = capability_registry.detect("httpx")
    assert detected.available is True and detected.executable == str(pd_httpx)
    assert detected.version == "[INF] Current Version: v1.10.0"


def test_traditional_adapter_commands_are_bounded_and_argv_only(tmp_path):
    assert traditional_tools.command_for("subfinder", "https://Example.test/app")[:2] == ["-d", "example.test"]
    assert traditional_tools.command_for("httpx", "https://example.test")[:2] == ["-u", "https://example.test"]
    assert "-d" in traditional_tools.command_for("katana", "https://example.test")
    nuclei_args = traditional_tools.command_for("nuclei", "https://example.test")
    assert "critical" in nuclei_args[nuclei_args.index("-severity") + 1]
    assert "-etags" in nuclei_args and ("-t" in nuclei_args or "-tags" in nuclei_args)
    assert traditional_tools.command_for("katana", "https://example.test", scan_profile="quick")[traditional_tools.command_for("katana", "https://example.test", scan_profile="quick").index("-d") + 1] == "1"
    assert traditional_tools.command_for("semgrep", "https://example.test", str(tmp_path))[0] == "scan"
    strix_args = traditional_tools.command_for("strix", "https://example.test", scan_profile="quick", model_budget_usd=.5)
    assert strix_args[:4] == ["-n", "-t", "https://example.test", "--scan-mode"]
    assert strix_args[strix_args.index("--max-budget") + 1] == "0.5"
    with pytest.raises(ValueError):
        traditional_tools.command_for("trivy", "https://example.test")


def test_semgrep_adapter_prefers_repository_local_rules(tmp_path):
    config = tmp_path / ".semgrep.yml"
    config.write_text("rules: []")
    args = traditional_tools.command_for("semgrep", "https://example.test", str(tmp_path))
    assert args[args.index("--config") + 1] == str(config)


def test_traditional_tool_parsers_normalize_all_supported_outputs():
    samples = {
        "subfinder": '{"host":"api.example.test"}',
        "httpx": '{"url":"https://api.example.test","status_code":200,"title":"API"}',
        "katana": '{"request":{"endpoint":"https://example.test/api"}}',
        "nuclei": '{"template-id":"header","matched-at":"https://example.test","info":{"name":"Header","severity":"low"}}',
        "semgrep": '{"results":[{"check_id":"python.rule","path":"app.py","start":{"line":4},"extra":{"message":"match"}}]}',
        "gitleaks": '[{"RuleID":"generic","File":".env","StartLine":1,"Secret":"must-not-persist"}]',
        "trivy": '{"Results":[{"Target":"requirements.txt","Vulnerabilities":[{"VulnerabilityID":"CVE-TEST","Severity":"HIGH"}]}]}',
    }
    for capability, output in samples.items():
        observations = traditional_tools.parse_output(capability, output)
        assert observations, capability
        assert all(item["observation_type"] and item["subject"] for item in observations)
        assert "must-not-persist" not in json.dumps(observations)


def test_scanner_structured_data_drops_raw_http_and_credentials():
    output = json.dumps({
        "matched-at": "https://example.test", "request": "Authorization: Bearer hidden",
        "response": "Set-Cookie: session=hidden", "info": {"name": "Header", "severity": "low"},
    })
    observations = traditional_tools.parse_output("nuclei", output)
    serialized = json.dumps(observations)
    assert "Bearer hidden" not in serialized and "session=hidden" not in serialized


def test_strix_artifacts_normalize_candidates_without_raw_http(tmp_path):
    run = tmp_path / "run-1"; run.mkdir()
    (run / "vulnerabilities.json").write_text(json.dumps({"vulnerabilities": [{
        "title": "Authorization bypass", "target": "https://example.test/api", "severity": "high",
        "request": "Authorization: Bearer hidden", "response": "secret body",
    }]}))
    observations = traditional_tools.parse_strix_artifacts(run)
    assert observations[0]["observation_type"] == "agent.candidate_finding"
    assert "Bearer hidden" not in json.dumps(observations) and "secret body" not in json.dumps(observations)


def test_strix_execution_fails_closed_before_tool_launch(client, monkeypatch):
    created = client.post("/api/v1/engagements", json={
        "name": "Strix fail closed", "target": "https://example.test", "mode": "traditional",
    }).json()
    ready = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    launched = False

    def forbidden_launch(*args, **kwargs):
        nonlocal launched
        launched = True
        raise AssertionError("Strix must not launch without all local prerequisites")

    monkeypatch.setattr(capability_registry, "execute", forbidden_launch)
    monkeypatch.setattr(traditional_tools, "strix_configured", lambda: False)
    response = client.post(f"/api/v1/traditional/runs/{run_id}/tools/run", json={"capability": "strix"})
    assert response.status_code == 409 and response.json()["detail"] == "strix_provider_not_configured"
    assert launched is False

    monkeypatch.setattr(traditional_tools, "strix_configured", lambda: True)
    monkeypatch.setattr(traditional_tools, "strix_sandbox_ready", lambda: False)
    response = client.post(f"/api/v1/traditional/runs/{run_id}/tools/run", json={"capability": "strix"})
    assert response.status_code == 409 and response.json()["detail"] == "strix_sandbox_not_ready"
    assert launched is False


def test_strix_provider_api_stores_secret_only_in_mode_600_file(client, monkeypatch, tmp_path):
    config = tmp_path / ".strix" / "cli-config.json"
    monkeypatch.setattr(traditional_tools, "strix_config_path", lambda: config)
    response = client.put("/api/v1/traditional/provider", json={
        "base_url": "https://relay.example.test/v1/",
        "model": "relay-model",
        "api_key": "test-secret-key",
    })
    assert response.status_code == 200, response.text
    assert "test-secret-key" not in response.text
    assert response.json() == {
        "configured": True, "base_url": "https://relay.example.test/v1",
        "model": "openai/relay-model", "has_api_key": True,
    }
    assert config.stat().st_mode & 0o777 == 0o600
    assert json.loads(config.read_text())["env"]["LLM_API_KEY"] == "test-secret-key"
    status = client.get("/api/v1/traditional/provider")
    assert status.status_code == 200 and status.json()["has_api_key"] is True
    assert "test-secret-key" not in status.text
    denied = client.put("/api/v1/traditional/provider", json={
        "base_url": "http://relay.example.test/v1", "model": "m", "api_key": "12345678",
    })
    assert denied.status_code == 422


def test_clean_static_scans_are_normalized_as_clean_not_unparsed():
    semgrep = traditional_tools.parse_output("semgrep", '{"results":[],"errors":[]}')
    trivy = traditional_tools.parse_output("trivy", '{"SchemaVersion":2,"ArtifactName":"fixture","ArtifactType":"filesystem"}')
    gitleaks = traditional_tools.parse_output("gitleaks", "", "INF no leaks found")
    assert semgrep[0]["observation_type"] == "code.static_scan_clean"
    assert trivy[0]["observation_type"] == "code.supply_chain_scan_clean"
    assert gitleaks[0]["observation_type"] == "code.secret_scan_clean"


def test_real_traditional_tool_endpoint_persists_artifact_and_observations(client, monkeypatch):
    created = client.post("/api/v1/engagements", json={
        "name": "Local adapter", "target": "http://127.0.0.1:8765", "mode": "traditional",
        "scope": {"allow_private_ips": True},
    }).json()
    ready = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    envelope = capability_registry.ToolResultEnvelope(
        "nuclei", "completed", 0,
        '{"template-id":"fixture","matched-at":"http://127.0.0.1:8765","info":{"name":"Fixture match","severity":"medium"}}', "",
    )
    monkeypatch.setattr(capability_registry, "execute", lambda *args, **kwargs: envelope)
    result = client.post(f"/api/v1/traditional/runs/{run_id}/tools/run", json={"capability": "nuclei"})
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["tool_result"]["status"] == "completed" and data["observation_count"] == 1
    with sqlite3.connect(final_core.DB) as db:
        assert db.execute("SELECT source_capability FROM observations WHERE id=?", (data["observation_ids"][0],)).fetchone()[0] == "nuclei"
        assert db.execute("SELECT kind FROM artifacts WHERE id=?", (data["artifact_id"],)).fetchone()[0] == "traditional.tool.nuclei"


def test_traditional_scanners_reuse_private_ip_network_guard(client, monkeypatch):
    ready = create_ready(client, target="http://127.0.0.1:8765")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    monkeypatch.setattr(capability_registry, "execute", lambda *args, **kwargs: pytest.fail("blocked target must not launch scanner"))
    response = client.post(f"/api/v1/traditional/runs/{run_id}/tools/run", json={"capability": "nuclei"})
    assert response.status_code == 409 and "private_or_special_ip_denied" in response.json()["detail"]


def test_real_mode_starts_non_synthetic_toolchain(client, monkeypatch):
    ready = create_ready(client, target="https://real-mode.test")
    monkeypatch.setattr(capability_registry, "inventory", lambda: [
        {"id": name, "available": False} for name in traditional_tools.TRADITIONAL_CAPABILITIES
    ])
    started = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"execution_mode": "real", "include_recon": True})
    assert started.status_code == 202 and started.json()["synthetic"] is False
    import time
    deadline = time.monotonic() + 3
    while True:
        run = client.get(f"/api/v1/runs/{started.json()['id']}").json()
        if any(event["kind"] == "run.completed" for event in run["events"]):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(.05)
    assert run["synthetic"] is False
    assert any(event["kind"] == "capability.degraded" for event in run["events"])
    assert any(event["kind"] == "run.completed" and event["payload"].get("synthetic") is False for event in run["events"])
    coverage = client.get(f"/api/v1/runs/{started.json()['id']}/coverage").json()
    assert coverage["coverage"] and all(item["state"] == "not_tested" for item in coverage["coverage"])


def test_real_toolchain_resume_uses_saved_plan_and_skips_tool_checkpoint(client, monkeypatch):
    ready = create_ready(client, target="https://checkpoint.test")
    monkeypatch.setattr(capability_registry, "inventory", lambda: [
        {"id": name, "available": False} for name in traditional_tools.TRADITIONAL_CAPABILITIES
    ])
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"execution_mode": "real"}).json()["id"]
    import time
    time.sleep(.15)
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='paused',completed_at=NULL WHERE id=?", (run_id,))
        target_hash = traditional_tools.hashlib.sha256("https://checkpoint.test".encode()).hexdigest()[:12]
        db.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?)", ("checkpoint-httpx", run_id, f"tool.httpx.{target_hash}", "{}", final_core.utcnow()))
    monkeypatch.setattr(capability_registry, "inventory", lambda: [
        {"id": name, "available": name == "httpx"} for name in traditional_tools.TRADITIONAL_CAPABILITIES
    ])
    original = traditional_tools.run_capability
    def guarded(*args, **kwargs):
        if args[1] == "httpx":
            raise AssertionError("checkpointed httpx must not be rerun")
        return original(*args, **kwargs)
    monkeypatch.setattr(traditional_tools, "run_capability", guarded)
    resumed = client.post(f"/api/v1/runs/{run_id}/resume")
    assert resumed.status_code == 200
    time.sleep(.2)
    run = client.get(f"/api/v1/runs/{run_id}").json()
    assert run["synthetic"] is False
    assert any(event["kind"] == "tool.skipped_checkpoint" and event["payload"].get("capability") == "httpx" for event in run["events"])


def test_toolchain_passes_discovered_hosts_and_live_urls_downstream(client, monkeypatch):
    ready = create_ready(client, target="https://flow.test")
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()["id"]
    import time
    time.sleep(1.7)
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='queued',synthetic=0,completed_at=NULL WHERE id=?", (run_id,))
        db.execute("DELETE FROM checkpoints WHERE run_id=?", (run_id,))
    monkeypatch.setattr(capability_registry, "inventory", lambda: [
        {"id": name, "available": name in traditional_tools.NETWORK_CAPABILITIES} for name in traditional_tools.TRADITIONAL_CAPABILITIES
    ])
    calls = []
    def fake_run(run, capability, source, timeout, target=None, scan_profile="quick"):
        calls.append((capability, target))
        subjects = {
            "subfinder": ["api.flow.test"],
            "httpx": ["https://live.flow.test"],
            "katana": ["https://live.flow.test/path"],
            "nuclei": ["https://live.flow.test"],
        }[capability]
        return {"capability": capability, "tool_result": {"status": "completed"}, "artifact_id": f"artifact-{capability}", "observation_ids": [], "observation_count": len(subjects), "subjects": subjects}
    monkeypatch.setattr(traditional_tools, "run_capability", fake_run)
    asyncio.run(traditional_tools.execute_toolchain(run_id, traditional_tools.TraditionalToolchainInput()))
    assert ("httpx", "https://api.flow.test") in calls
    assert ("katana", "https://live.flow.test") in calls
    assert ("nuclei", "https://live.flow.test") in calls


def test_web3_verified_finding_requires_program_gates_and_immunefi_ready(client):
    engagement = create_ready(client, "web3", "0x4444444444444444444444444444444444444444")
    program = client.post("/api/v1/program-snapshots", json={
        "engagement_id": engagement["id"], "platform": "immunefi",
        "rules": {"impacts": ["loss_of_funds"], "poc": "local_fork"},
    }).json()
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "web3.invariant", "subject": "Vault.totalAssets",
        "summary": "Local fork state diff violates asset conservation", "source_capability": "forge",
        "confidence": .95,
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Vault accounting invariant violation", "category": "accounting",
        "target": "0x4444444444444444444444444444444444444444",
        "hypothesis": "Withdraw path can desynchronize total assets", "observation_ids": [observation["id"]],
    }).json()
    proof = {
        "oracle": "forge-local-fork-state-diff-v1", "attempts": 2, "reproduced": True,
        "counterevidence_checked": True, "counterevidence_summary": "Control sequence preserves accounting",
        "severity": "critical", "impact_description": "Demonstrated local-fork asset accounting loss",
        "steps": ["Create local fork", "Execute controlled call sequence", "Compare state and balance diff"],
        "expected": "totalAssets remains conserved", "actual": "state diff violates conservation",
        "root_cause": "Accounting update order", "weakness": "CWE-682", "location": "Vault.withdraw",
        "poc_artifact_ids": ["fork-trace-1"], "program_snapshot_id": program["id"],
        "impact_in_scope": True, "known_issue_checked": True, "previous_audit_checked": True,
        "poc_rule_checked": True, "feasibility": "Reproduced on local fork", "funds_at_risk": "fixture only",
    }
    verified = client.post(f"/api/v1/candidates/{candidate['id']}/verify", json=proof)
    assert verified.status_code == 200
    preview = client.post(f"/api/v1/findings/{verified.json()['id']}/reports/immunefi/preview")
    assert preview.status_code == 200
    assert preview.json()["completeness"]["ready"] is True


def test_real_http_replay_oracle_with_negative_control(client):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/object/42":
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(b'{"owner":"tenant-a","id":42}')
            else:
                self.send_response(404); self.end_headers(); self.wfile.write(b'{"error":"not found"}')
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        port = server.server_port
        created = client.post("/api/v1/engagements", json={
            "name": "Local HTTP oracle", "target": f"http://127.0.0.1:{port}", "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests_per_second": 200},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        obs = client.post(f"/api/v1/runs/{run_id}/observations", json={
            "observation_type": "http.authorization", "subject": "GET /api/object/42",
            "summary": "Role B response matched owner response", "source_capability": "http",
        }).json()
        candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
            "title": "Cross-role object read", "category": "CWE-639",
            "target": f"http://127.0.0.1:{port}/api/object/42", "hypothesis": "Ownership is not enforced",
            "observation_ids": [obs["id"]],
        }).json()
        endpoint = f"/api/v1/traditional/runs/{run_id}/http-replay"
        replay = client.post(endpoint, json={
            "candidate_id": candidate["id"],
            "baseline": {"url": f"http://127.0.0.1:{port}/api/object/42", "headers": {"X-Role": "owner"}},
            "attack": {"url": f"http://127.0.0.1:{port}/api/object/42", "headers": {"X-Role": "other"}},
            "negative_control": {"url": f"http://127.0.0.1:{port}/api/object/999", "headers": {"X-Role": "other"}},
            "severity": "high", "impact_description": "Cross-role object disclosure",
            "root_cause": "Missing ownership check", "weakness": "CWE-639", "location": "GET /api/object/{id}",
        })
        assert replay.status_code == 200, replay.text
        data = replay.json()
        assert data["replay"]["reproduced"] is True and data["replay"]["stable"] is True
        assert data["verification"]["status"] == "verified"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_workbench_records_redacts_replays_diffs_and_creates_candidate(client):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = b'{"state":"changed"}' if self.path == "/changed" else b'{"state":"baseline"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session=server-secret")
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        port = server.server_port
        engagement = client.post("/api/v1/engagements", json={
            "name": "HTTP workbench", "target": f"http://127.0.0.1:{port}", "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests_per_second": 200},
        }).json()
        client.post(f"/api/v1/engagements/{engagement['id']}/confirm")
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
        first = client.post(f"/api/v1/traditional/runs/{run_id}/http-exchanges", json={
            "url": f"http://127.0.0.1:{port}/baseline", "method": "GET",
            "headers": {"Authorization": "Bearer super-secret-value", "Cookie": "session=client-secret"},
        })
        assert first.status_code == 201, first.text
        exchange = first.json()
        assert exchange["response_status"] == 200
        assert "super-secret-value" not in json.dumps(exchange) and "client-secret" not in json.dumps(exchange)
        replay = client.post(f"/api/v1/traditional/http-exchanges/{exchange['id']}/replay", json={
            "url": f"http://127.0.0.1:{port}/changed", "headers": {},
        })
        assert replay.status_code == 201, replay.text
        assert replay.json()["diff"]["body_changed"] is True
        assert replay.json()["parent_exchange_id"] == exchange["id"]
        listed = client.get(f"/api/v1/traditional/runs/{run_id}/http-exchanges").json()
        assert len(listed) == 2 and listed[0]["source"] == "replay"
        promoted = client.post(f"/api/v1/traditional/http-exchanges/{replay.json()['id']}/candidate", json={
            "title": "Role boundary differs", "category": "authorization",
            "hypothesis": "The changed response may expose a cross-role object boundary",
        })
        assert promoted.status_code == 201
        assert promoted.json()["candidate"]["status"] == "candidate"
        assert "super-secret-value" not in final_core.DB.read_bytes().decode(errors="ignore")
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_ptai_capsule_replay_requires_integrity_scope_and_live_oracle(client, monkeypatch):
    created = client.post("/api/v1/engagements", json={
        "name": "ptai replay", "target": "http://127.0.0.1:8765", "mode": "traditional",
        "scope": {"allow_private_ips": True}, "policy": {"max_requests_per_second": 100},
    }).json()
    engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "http.reflection", "subject": "http://127.0.0.1:8765/search",
        "summary": "Reflection candidate", "source_capability": "pentest-ai",
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Reflected XSS", "category": "CWE-79", "target": "http://127.0.0.1:8765/search",
        "hypothesis": "Input is reflected without encoding", "observation_ids": [observation["id"]],
    }).json()
    capsule = {
        "capsule_schema_version": 1, "ptai_version": "1.3.1", "created_at": "fixture",
        "finding": {"id": "fixture", "title": "Reflected XSS", "target": "http://127.0.0.1:8765/search?q=x", "severity": "medium", "bug_class": "xss_reflected", "evidence": "secret raw response", "verification_recipe": {"oracle": "http_reflect", "param": "q"}},
        "receipt": {"verdict": "verified", "oracle_kind": "unescaped_reflection", "evidence": {"request": "raw", "response": "raw", "diff": "negative control escaped the canary"}, "replay": {"successes": 3, "attempts": 3}},
    }
    capsule["integrity_sha256"] = traditional_runtime.capsule_integrity(capsule)
    class Result:
        returncode = 0
        stdout = '{"integrity_ok":true,"verdict":"verified","oracle":"unescaped_reflection","replay":"3/3"}'
        stderr = ""
    monkeypatch.setattr(capability_registry, "resolve_executable", lambda name: "/fixture/ptai")
    monkeypatch.setattr(traditional_runtime.subprocess, "run", lambda *args, **kwargs: Result())
    replay = client.post(f"/api/v1/traditional/runs/{run_id}/ptai-replay", json={
        "candidate_id": candidate["id"], "capsule": capsule,
        "impact_description": "Arbitrary script execution in the victim origin",
        "root_cause": "Missing contextual output encoding", "weakness": "CWE-79", "location": "GET /search?q",
    })
    assert replay.status_code == 200, replay.text
    data = replay.json()
    assert data["status"] == "verified" and data["verification"]["status"] == "verified"
    artifact_path = traditional_runtime.ARTIFACT_ROOT / f"{data['artifact_id']}.json"
    artifact_text = artifact_path.read_text()
    assert "secret raw response" not in artifact_text and '"request": "raw"' not in artifact_text
    tampered = {**capsule, "ptai_version": "tampered"}
    rejected = client.post(f"/api/v1/traditional/runs/{run_id}/ptai-replay", json={
        "candidate_id": candidate["id"], "capsule": tampered,
        "impact_description": "impact", "root_cause": "cause", "weakness": "CWE-79", "location": "route",
    })
    assert rejected.status_code == 422
