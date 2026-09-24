import asyncio
import base64
import io
import json
import sqlite3
import threading
import time
import zipfile
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
import benchmarking
import traditional_tools
import traditional_runtime
import native_agent
import session_capture


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SRC_ENABLE_SYNTHETIC_DEMO", "1")
    database = tmp_path / "final-test.db"
    monkeypatch.setattr(app, "DB", database)
    monkeypatch.setattr(final_core, "DB", database)
    monkeypatch.setattr(final_core, "LOCAL_DATA_ROOT", tmp_path)
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


def test_engagement_batch_is_atomic_and_idempotent(client, monkeypatch):
    items = [{"name": "First local target", "target": "https://one.example.test", "mode": "traditional"},
             {"name": "Second local target", "target": "https://two.example.test", "mode": "traditional"}]
    payload = {"request_id": "batch-atomic-001", "items": items}
    original = final_core._insert_engagement
    calls = 0
    def fail_second(db, body, resolved):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated database failure")
        return original(db, body, resolved)
    monkeypatch.setattr(final_core, "_insert_engagement", fail_second)
    with pytest.raises(RuntimeError):
        client.post("/api/v1/engagements/batch", json=payload)
    assert client.get("/api/v1/engagements?mode=traditional").json() == []
    monkeypatch.setattr(final_core, "_insert_engagement", original)
    first = client.post("/api/v1/engagements/batch", json=payload)
    assert first.status_code == 201 and first.json()["deduplicated"] is False
    assert len(first.json()["engagements"]) == 2
    again = client.post("/api/v1/engagements/batch", json=payload)
    assert again.status_code == 201 and again.json()["deduplicated"] is True
    assert [item["id"] for item in again.json()["engagements"]] == [item["id"] for item in first.json()["engagements"]]
    changed = client.post("/api/v1/engagements/batch", json={**payload, "items": items[:1]})
    assert changed.status_code == 409
    assert len(client.get("/api/v1/engagements?mode=traditional").json()) == 2


def test_engagement_batch_validates_all_targets_before_insert(client):
    result = client.post("/api/v1/engagements/batch", json={"request_id": "batch-validate-001", "items": [
        {"name": "Valid target", "target": "https://one.example.test", "mode": "traditional"},
        {"name": "Invalid target", "target": "https://", "mode": "traditional"},
    ]})
    assert result.status_code == 422
    assert client.get("/api/v1/engagements?mode=traditional").json() == []


@pytest.mark.parametrize("kind,document", [
    ("openapi", {"openapi": "3.1.0", "servers": [{"url": "https://api.example.test/v1"}],
                 "paths": {"/orders": {"get": {}, "post": {}}, "/me": {"get": {}}}}),
    ("postman", {"info": {"name": "API"}, "item": [{"name": "Orders", "request": {
        "method": "GET", "header": [{"key": "Authorization", "value": "Bearer SECRET"}],
        "url": {"raw": "https://api.example.test/orders?token=SECRET"}}}]}),
    ("har", {"log": {"entries": [{"request": {"method": "GET",
        "url": "https://api.example.test/orders?api_key=SECRET", "headers": [{"name": "Cookie", "value": "SECRET"}]}},
        {"request": {"method": "GET", "url": "https://cdn.third.test/app.js"}}]}}),
])
def test_target_package_preview_and_apply_only_persists_safe_surface(client, kind, document):
    content = json.dumps(document)
    preview = client.post("/api/v1/target-packages/preview", json={"kind": "auto", "content": content})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["kind"] == kind and data["root_target"] == "https://api.example.test"
    assert "SECRET" not in json.dumps(data) and all("?" not in item["url"] for item in data["endpoints"])
    applied = client.post("/api/v1/target-packages/apply", json={
        "kind": kind, "content": content, "name": f"Imported {kind}",
        "root_target": "https://api.example.test",
    })
    assert applied.status_code == 201, applied.text
    engagement = applied.json()["engagement"]
    assert engagement["status"] == "draft" and engagement["confirmed_at"] is None
    assert engagement["scope"]["import_kind"] == kind
    assert engagement["scope"]["import_source_sha256"] == data["source_sha256"]
    assert all(item["origin"] == "https://api.example.test" for item in engagement["scope"]["imported_surface"])
    assert "SECRET" not in json.dumps(engagement)


def test_target_package_preserves_safe_request_semantics_without_values(client):
    document = {"openapi": "3.1.0", "servers": [{"url": "https://api.example.test"}],
                "security": [{"session": []}], "paths": {"/orders/{orderId}": {
                    "parameters": [{"name": "orderId", "in": "path", "required": True, "schema": {"type": "string", "example": "SECRET"}}],
                    "post": {"parameters": [{"name": "dry_run", "in": "query", "schema": {"type": "boolean", "default": "SECRET"}}],
                             "requestBody": {"content": {"application/json": {"schema": {"type": "object", "properties": {
                                 "quantity": {"type": "integer", "example": "SECRET"}}}}}}}}}}
    content = json.dumps(document)
    preview = client.post("/api/v1/target-packages/preview", json={"content": content}).json()
    endpoint = preview["endpoints"][0]
    assert endpoint["parameters"] == [
        {"name": "orderId", "in": "path", "type": "string", "required": True},
        {"name": "dry_run", "in": "query", "type": "boolean", "required": False},
    ]
    assert endpoint["body_fields"] == [{"name": "quantity", "type": "integer"}]
    assert endpoint["auth_required"] is True
    assert "SECRET" not in json.dumps(preview)
    applied = client.post("/api/v1/target-packages/apply", json={"content": content, "name": "Semantic API"}).json()
    assert applied["engagement"]["scope"]["imported_surface"][0]["body_fields"] == endpoint["body_fields"]
    assert "SECRET" not in json.dumps(applied)


@pytest.mark.parametrize("kind,document", [
    ("postman", {"info": {"name": "API"}, "item": [{"request": {"method": "POST",
        "url": {"raw": "https://api.example.test/orders?token=SECRET", "query": [{"key": "dry_run", "value": "SECRET"}]},
        "body": {"mode": "urlencoded", "urlencoded": [{"key": "quantity", "value": "SECRET"}]},
        "header": [{"key": "Authorization", "value": "SECRET"}]}}]}),
    ("har", {"log": {"entries": [{"request": {"method": "POST", "url": "https://api.example.test/orders?token=SECRET",
        "queryString": [{"name": "dry_run", "value": "SECRET"}], "postData": {"params": [{"name": "quantity", "value": "SECRET"}]},
        "headers": [{"name": "Cookie", "value": "SECRET"}]}}]}}),
])
def test_imported_postman_and_har_keep_structure_not_secrets(client, kind, document):
    preview = client.post("/api/v1/target-packages/preview", json={"kind": kind, "content": json.dumps(document)}).json()
    endpoint = preview["endpoints"][0]
    assert endpoint["parameters"] == [{"name": "dry_run", "in": "query", "type": "unknown", "required": False}]
    assert endpoint["body_fields"] == [{"name": "quantity", "type": "unknown"}]
    assert endpoint["auth_required"] is True
    assert "SECRET" not in json.dumps(preview)


def test_target_package_skips_malformed_url_and_schema_type(client):
    document = {"openapi": "3.1.0", "servers": [{"url": "https://api.example.test"}],
                "paths": {"/safe": {"get": {"parameters": [{"name": "page", "in": "query", "schema": {"type": ["string", "null"]}}]}}}}
    response = client.post("/api/v1/target-packages/preview", json={"content": json.dumps(document)})
    assert response.status_code == 200
    assert response.json()["endpoints"][0]["parameters"][0]["type"] == "unknown"
    malformed = {"log": {"entries": [{"request": {"method": "GET", "url": "http://[invalid"}},
                                      {"request": {"method": "GET", "url": "https://api.example.test/safe"}}]}}
    response = client.post("/api/v1/target-packages/preview", json={"content": json.dumps(malformed)})
    assert response.status_code == 200
    assert response.json()["counts"]["endpoints"] == 1


def test_imported_semantics_reach_traditional_run_details_without_claiming_tested(client):
    document = {"openapi": "3.1.0", "servers": [{"url": "https://api.example.test"}],
                "paths": {"/orders/{orderId}": {"get": {"parameters": [
                    {"name": "orderId", "in": "path", "schema": {"type": "string"}}]}}}}
    created = client.post("/api/v1/target-packages/apply", json={
        "content": json.dumps(document), "name": "Imported research target"}).json()["engagement"]
    client.post(f"/api/v1/engagements/{created['id']}/confirm")
    run = client.post(f"/api/v1/engagements/{created['id']}/start", json={"execution_mode": "demo"}).json()
    details = client.get(f"/api/v1/runs/{run['id']}/details").json()
    assert details["imported_surface"][0]["parameters"][0]["name"] == "orderId"
    imported = next(item for item in details["test_items"] if item["id"] == "imported-api")
    assert imported["status"] == "not_tested"
    assert imported["observation_count"] == 0
    assert "尚须独立测试" in imported["result"]


def test_source_zip_preview_and_apply_extracts_only_safe_source(client):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("project/src/Vault.sol", "contract Vault { function value() external pure returns(uint){return 1;} }")
        package.writestr("project/foundry.toml", "[profile.default]\nsrc = 'src'\n")
        package.writestr("project/image.png", b"not-source")
    payload = {
        "kind": "source_zip", "encoding": "base64", "mode": "web3", "filename": "vault.zip",
        "content": base64.b64encode(archive.getvalue()).decode(),
    }
    preview = client.post("/api/v1/target-packages/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"] == {"files": 2, "bytes": 104, "ignored": 1}
    assert {item["path"] for item in preview.json()["files"]} == {"project/src/Vault.sol", "project/foundry.toml"}
    applied = client.post("/api/v1/target-packages/apply", json={**payload, "name": "Imported Vault"})
    assert applied.status_code == 201, applied.text
    engagement = applied.json()["engagement"]
    repository = Path(engagement["normalized_target"])
    assert engagement["mode"] == "web3" and engagement["target_type"] == "repository"
    assert engagement["status"] == "draft" and engagement["confirmed_at"] is None
    assert (repository / "project/src/Vault.sol").is_file()
    assert not (repository / "project/image.png").exists()
    assert engagement["scope"]["import_source_sha256"] == preview.json()["source_sha256"]


def test_source_zip_rejects_path_traversal(client):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.py", "print('unsafe')")
    response = client.post("/api/v1/target-packages/preview", json={
        "kind": "source_zip", "encoding": "base64", "mode": "traditional", "filename": "unsafe.zip",
        "content": base64.b64encode(archive.getvalue()).decode(),
    })
    assert response.status_code == 422 and "不安全路径" in response.text


def test_cidr_import_collapses_ranges_and_authorizes_only_member_ips(client):
    payload = {"kind": "cidr", "content": "192.0.2.0/25\n192.0.2.128/25\n2001:db8::/126", "mode": "traditional"}
    preview = client.post("/api/v1/target-packages/preview", json=payload)
    assert preview.status_code == 200, preview.text
    assert preview.json()["counts"] == {"ranges": 2, "addresses": 260}
    assert [item["cidr"] for item in preview.json()["ranges"]] == ["192.0.2.0/24", "2001:db8::/126"]
    applied = client.post("/api/v1/target-packages/apply", json={**payload, "name": "Authorized lab network"})
    assert applied.status_code == 201
    engagement = applied.json()["engagement"]
    assert engagement["target_type"] == "cidr" and engagement["status"] == "draft"
    client.post(f"/api/v1/engagements/{engagement['id']}/confirm")
    inside = client.post("/api/v1/policy/check", json={
        "engagement_id": engagement["id"], "target": "https://192.0.2.42/status", "action": "read"})
    outside = client.post("/api/v1/policy/check", json={
        "engagement_id": engagement["id"], "target": "https://198.51.100.42/status", "action": "read"})
    assert inside.json()["allowed"] is True
    assert outside.json() == {"allowed": False, "reason": "out_of_scope", "target": "https://198.51.100.42"}


def test_target_package_rejects_unknown_or_changed_root(client):
    unknown = client.post("/api/v1/target-packages/preview", json={"kind": "auto", "content": '{"hello":"world"}'})
    assert unknown.status_code == 422
    content = json.dumps({"openapi": "3.0.0", "servers": [{"url": "https://api.example.test"}],
                          "paths": {"/health": {"get": {}}}})
    changed = client.post("/api/v1/target-packages/apply", json={
        "kind": "openapi", "content": content, "name": "Changed root", "root_target": "https://evil.test",
    })
    assert changed.status_code == 422 and "来源列表" in changed.json()["detail"]


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


def test_project_asset_graph_links_root_target_to_observed_assets(client):
    engagement = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    first = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "surface.route", "subject": "https://example.test/api/orders/42",
        "summary": "Observed authorized API route", "source_capability": "katana", "confidence": .8,
    })
    second = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "surface.route", "subject": "https://example.test/api/orders/42",
        "summary": "Route confirmed by browser", "source_capability": "native-agent", "confidence": .9,
    })
    outside = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "surface.route", "subject": "https://cdn.example.test/app.js",
        "summary": "Observed a subdomain that was not authorized", "source_capability": "katana", "confidence": .7,
    })
    assert first.status_code == 201 and second.status_code == 201 and outside.status_code == 201
    graph = client.get(f"/api/v1/engagements/{engagement['id']}/asset-graph")
    assert graph.status_code == 200
    payload = graph.json()
    assert payload["counts"]["nodes"] == 3 and payload["counts"]["edges"] == 2
    assert payload["counts"]["by_type"] == {"target": 1, "surface.route": 2}
    root = next(node for node in payload["nodes"] if node["id"] == payload["root_id"])
    route = next(node for node in payload["nodes"] if node["label"].endswith("/api/orders/42"))
    outside_route = next(node for node in payload["nodes"] if node["label"].startswith("https://cdn."))
    assert root["attributes"]["root"] is True and root["label"] == engagement["normalized_target"]
    assert route["attributes"]["source"] == "native-agent" and route["attributes"]["confidence"] == .9
    assert route["attributes"]["scope_status"] == "in_scope"
    assert outside_route["attributes"]["scope_status"] == "outside_root"
    edge = next(item for item in payload["edges"] if item["target_id"] == route["id"])
    assert edge["source_id"] == root["id"]
    assert edge["evidence_ids"] == [first.json()["id"], second.json()["id"]]
    assert "未观察到" in payload["boundary"]
    with final_core.connect() as db:
        db.execute("DELETE FROM relationships WHERE engagement_id=?", (engagement["id"],))
        db.execute("DELETE FROM entities WHERE engagement_id=? AND entity_type!='target'", (engagement["id"],))
    legacy_graph = client.get(f"/api/v1/engagements/{engagement['id']}/asset-graph").json()
    assert legacy_graph["counts"]["nodes"] == 3 and legacy_graph["counts"]["edges"] == 2
    assert any(node["attributes"].get("derived_from_observation") for node in legacy_graph["nodes"])
    assert next(node for node in legacy_graph["nodes"] if node["label"].startswith("https://cdn."))["attributes"]["scope_status"] == "outside_root"


def test_oracle_catalog_discloses_supported_and_human_review_boundaries(client):
    response = client.get("/api/v1/verification-oracles")
    assert response.status_code == 200
    items = {item["id"]: item for item in response.json()}
    assert items["http-authorization-read-v2"]["promotes_finding"] is True
    assert items["http-authorization-read-v2"]["portable"] == "recorded_assertion"
    assert items["forge-property-replay-v1"]["portable"] == "isolated_local_execution"
    assert items["unregistered"]["level"] == "human_review_only"
    assert items["unregistered"]["promotes_finding"] is False
    assert all(item["positive"] and item["negative"] and item["requires"] for item in items.values())


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


def test_visible_session_capture_is_scope_gated_and_keychain_only(client, monkeypatch):
    denied = create_ready(client, target="https://login-denied.test")
    denied_identity = client.post(f"/api/v1/engagements/{denied['id']}/identities", json={
        "label": "Denied user", "role": "user", "auth_type": "none", "session_status": "needs_login",
    }).json()
    blocked = client.post(f"/api/v1/identities/{denied_identity['id']}/session-captures", json={
        "login_url": "https://login-denied.test/login",
    })
    assert blocked.status_code == 409 and "未显式允许" in blocked.json()["detail"]

    created = client.post("/api/v1/engagements", json={
        "name": "Login capture fixture", "target": "https://app.login.test", "mode": "traditional",
        "scope": {"allow_authentication": True, "auth_allowed_hosts": ["sso.login.test"]}, "policy": {},
    }).json()
    engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    identity = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Tenant A", "role": "user", "tenant": "a", "auth_type": "none", "session_status": "needs_login",
    }).json()
    outside = client.post(f"/api/v1/identities/{identity['id']}/session-captures", json={
        "login_url": "https://outside-login.test/login",
    })
    assert outside.status_code == 409 and "auth_allowed_hosts" in outside.json()["detail"]

    started_args = {}
    monkeypatch.setattr(session_capture, "start", lambda identity_id, login_url, allowed_hosts, max_requests: (
        started_args.update({"identity_id": identity_id, "login_url": login_url, "allowed_hosts": allowed_hosts, "max_requests": max_requests})
        or {"id": "capture-test", "identity_id": identity_id, "status": "browser_open", "login_url": login_url}
    ))
    monkeypatch.setattr(session_capture, "status", lambda capture_id: {
        "id": capture_id, "identity_id": identity["id"], "status": "browser_open",
    })
    monkeypatch.setattr(session_capture, "complete", lambda capture_id: {
        "headers": {"Cookie": "session=secret-cookie-value"}, "cookie_count": 1, "domain_count": 1,
        "requests_seen": 8, "requests_blocked": 2,
    })
    stored = {}
    monkeypatch.setattr(session_capture, "store_keychain", lambda identity_id, headers: (
        stored.update({"identity_id": identity_id, "headers": headers}) or f"keychain://fieldwork-session/{identity_id}"
    ))
    started = client.post(f"/api/v1/identities/{identity['id']}/session-captures", json={
        "login_url": "https://sso.login.test/start", "max_requests": 250,
    })
    assert started.status_code == 201 and started.json()["status"] == "browser_open"
    assert started_args["allowed_hosts"] == ["app.login.test", "sso.login.test"]
    completed = client.post("/api/v1/session-captures/capture-test/complete")
    assert completed.status_code == 200 and completed.json()["credential_configured"] is True
    assert completed.json()["requests_blocked"] == 2
    assert "secret-cookie-value" not in completed.text
    assert stored["headers"]["Cookie"] == "session=secret-cookie-value"
    refreshed = client.get(f"/api/v1/engagements/{engagement['id']}/identities").json()[0]
    assert refreshed["session_status"] == "ready" and refreshed["auth_type"] == "keychain_reference"
    assert refreshed["credential_configured"] is True and "credential_ref" not in refreshed
    with sqlite3.connect(final_core.DB) as db:
        row = db.execute("SELECT credential_ref FROM identities WHERE id=?", (identity["id"],)).fetchone()
    assert row[0] == f"keychain://fieldwork-session/{identity['id']}"
    assert "secret-cookie-value" not in row[0]


def test_long_running_business_logic_campaign_builds_memory_and_test_matrix(client):
    engagement = create_ready(client, target="https://logic.test")
    for label, role, tenant in (("Tenant A user", "user", "tenant-a"), ("Tenant B admin", "admin", "tenant-b")):
        created = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": label, "role": role, "tenant": tenant, "auth_type": "keychain_reference",
            "credential_ref": f"keychain://fieldwork/{tenant}-{role}", "session_status": "ready",
        })
        assert created.status_code == 201
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Order authorization deep research",
        "objective": "Continuously test order ownership, transitions and replay resistance",
        "strategy": "business_logic", "max_iterations": 30, "horizon_days": 90, "coverage_target": .9,
    })
    assert campaign.status_code == 201
    campaign_id = campaign.json()["id"]
    workflow = client.post(f"/api/v1/campaigns/{campaign_id}/workflows", json={
        "name": "Read order detail", "objective": "Compare object ownership decisions across tenants",
        "preconditions": ["Two ready identities from different tenants"],
        "steps": [{"name": "Open order", "method": "GET", "url": "https://logic.test/api/orders/42", "actor_role": "user", "state_before": "authenticated", "expected_transition": "No state change", "replay_safe": True}],
        "invariants": ["A user must never read an order owned by another tenant"], "risk_class": "read_only",
    })
    assert workflow.status_code == 201
    plan = client.post(f"/api/v1/campaigns/{campaign_id}/iterations/plan")
    assert plan.status_code == 201
    kinds = {item["kind"] for item in plan.json()["tests"]}
    assert {"baseline", "cross_identity_replay", "duplicate_replay"} <= kinds
    assert plan.json()["identity_count"] == 2 and plan.json()["blocked"] == []

    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "http.authorization", "subject": "GET /api/orders/42",
        "summary": "Cross-tenant replay returned a distinct object", "source_capability": "http-state-replay", "confidence": .8,
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Order ownership hypothesis", "category": "CWE-639", "target": "https://logic.test/api/orders/42",
        "hypothesis": "Order ownership may not be enforced across tenants", "observation_ids": [observation["id"]],
    })
    assert candidate.status_code == 201
    synced = client.post(f"/api/v1/campaigns/{campaign_id}/sync-memory")
    assert synced.status_code == 200 and synced.json()["hypotheses_created"] == 1
    synced_again = client.post(f"/api/v1/campaigns/{campaign_id}/sync-memory")
    assert synced_again.json()["hypotheses_created"] == 0 and synced_again.json()["hypotheses_linked"] == 1
    detail = client.get(f"/api/v1/campaigns/{campaign_id}").json()
    assert detail["workflow_count"] == 1 and detail["hypothesis_count"] == 1
    assert detail["hypotheses"][0]["status"] == "open_proof_gap"


def test_campaign_schedule_persists_prioritizes_hypotheses_and_claims_once(client):
    from concurrent.futures import ThreadPoolExecutor

    engagement = create_ready(client, target="https://schedule.test")
    client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Scheduled reader", "role": "user", "tenant": "tenant-a",
        "auth_type": "none", "session_status": "ready",
    })
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Persistent scheduler", "objective": "Continue prioritized business logic research after application restarts",
        "max_iterations": 20,
    }).json()
    workflows = []
    for suffix in ("high", "low"):
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": f"Read {suffix} object", "objective": f"Compare authorization for {suffix} priority object",
            "steps": [{"name": "Read", "method": "GET", "url": f"https://schedule.test/api/{suffix}", "actor_role": "user"}],
            "invariants": ["Cross-tenant access must be rejected"], "risk_class": "read_only",
        })
        assert workflow.status_code == 201, workflow.text
        workflows.append(workflow.json()["id"])
    low = client.post(f"/api/v1/campaigns/{campaign['id']}/hypotheses", json={
        "workflow_id": workflows[1], "category": "authorization", "statement": "Low priority object may cross tenant boundary",
        "priority": 20, "next_action": "Retest after high priority hypotheses",
    }).json()
    high = client.post(f"/api/v1/campaigns/{campaign['id']}/hypotheses", json={
        "workflow_id": workflows[0], "category": "authorization", "statement": "High priority object may cross tenant boundary",
        "priority": 95, "next_action": "Retest first",
    }).json()
    configured = client.put(f"/api/v1/campaigns/{campaign['id']}/schedule", json={
        "cadence_minutes": 15, "execution_mode": "plan_only", "start_immediately": True,
    })
    assert configured.status_code == 200 and configured.json()["enabled"] is True
    first = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now")
    assert first.status_code == 200 and first.json()["status"] == "planned"
    assert first.json()["hypothesis_id"] == high["id"]
    detail = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
    assert detail["iterations"][0]["plan"]["strategy"] == "scheduled_directed_retest"
    assert detail["schedule"]["last_planned_at"] and "lease_token" not in detail["schedule"]

    paused = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/pause")
    assert paused.status_code == 200 and paused.json()["enabled"] is False
    assert client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now").status_code == 409
    # Simulate completion and process restart. The durable schedule and next
    # lower-priority hypothesis must survive, while two workers claim it once.
    with sqlite3.connect(final_core.DB) as db:
        db.execute("UPDATE campaign_iterations SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), first.json()["iteration_id"]))
        db.execute("UPDATE research_campaigns SET iterations_completed=iterations_completed+1 WHERE id=?", (campaign["id"],))
    final_core.init_final_db()
    resumed = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/resume")
    assert resumed.status_code == 200 and resumed.json()["enabled"] is True
    now = final_core.utcnow()
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: final_core.run_due_campaign_schedules(now, campaign["id"], 1), range(2)))
    claimed = [item for batch in outcomes for item in batch]
    assert len(claimed) == 1 and claimed[0]["status"] == "planned"
    assert claimed[0]["hypothesis_id"] == low["id"]
    with sqlite3.connect(final_core.DB) as db:
        assert db.execute("SELECT COUNT(*) FROM campaign_iterations WHERE campaign_id=?", (campaign["id"],)).fetchone()[0] == 2
        second_id = db.execute("SELECT id FROM campaign_iterations WHERE campaign_id=? ORDER BY sequence DESC LIMIT 1", (campaign["id"],)).fetchone()[0]
        db.execute("UPDATE campaign_iterations SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), second_id))
        db.execute("UPDATE research_campaigns SET iterations_completed=2,max_iterations=2 WHERE id=?", (campaign["id"],))
        db.execute("UPDATE campaign_schedules SET next_run_at=? WHERE campaign_id=?", (now, campaign["id"]))
    exhausted = final_core.run_due_campaign_schedules(now, campaign["id"], 1)
    assert exhausted[0]["status"] == "failed" and "最大研究轮次" in exhausted[0]["error"]
    exhausted_schedule = client.get(f"/api/v1/campaigns/{campaign['id']}").json()["schedule"]
    assert exhausted_schedule["failure_count"] == 1 and exhausted_schedule["lease_expires_at"] is None


def test_campaign_schedule_only_auto_executes_explicit_read_only_plan(client, monkeypatch):
    state = {"requests": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"] += 1
            body = json.dumps({"object": "order-42", "owner": "tenant-a"}, sort_keys=True).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Scheduled read-only fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "environment_class": "local_fixture"},
            "policy": {"max_requests_per_second": 100, "max_requests": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Reader", "role": "user", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Read-only background campaign", "objective": "Safely continue bounded read-only coverage while the app is running",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Read fixture order", "objective": "Observe a stable authorized order without changing state",
            "steps": [{"name": "Read", "method": "GET", "url": f"{target}/orders/42", "actor_role": "user", "replay_safe": True}],
            "invariants": ["Read must not mutate state"], "risk_class": "read_only",
        })
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        invalid = client.put(f"/api/v1/campaigns/{campaign['id']}/schedule", json={
            "cadence_minutes": 15, "execution_mode": "read_only_execute", "preferred_run_id": "run-outside",
        })
        assert invalid.status_code == 422
        configured = client.put(f"/api/v1/campaigns/{campaign['id']}/schedule", json={
            "cadence_minutes": 15, "execution_mode": "read_only_execute", "preferred_run_id": run_id,
            "max_tests": 10, "start_immediately": True,
        })
        assert configured.status_code == 200
        outcome = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now")
        assert outcome.status_code == 200, outcome.text
        assert outcome.json()["status"] == "completed" and outcome.json()["execution"] == "read_only"
        assert outcome.json()["tested"] >= 1 and state["requests"] >= 1
        detail = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
        assert detail["iterations"][0]["status"] == "completed" and detail["iterations_completed"] == 1
        hypothesis = client.post(f"/api/v1/campaigns/{campaign['id']}/hypotheses", json={
            "workflow_id": workflow.json()["id"], "category": "authorization",
            "statement": "Scheduled order access needs repeated cross-identity proof", "priority": 90,
        })
        assert hypothesis.status_code == 201
        second = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now").json()
        third = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now").json()
        assert second["status"] == third["status"] == "completed"
        trend = client.get(f"/api/v1/campaigns/{campaign['id']}/trend").json()
        assert trend["summary"]["measured_iterations"] == 3 and trend["summary"]["plateau_streak"] == 2
        assert trend["summary"]["recommendation"] == "prioritize_directed_retests"
        assert trend["iterations"][0]["new_test_signature_count"] > 0
        assert trend["iterations"][1]["new_test_signature_count"] == trend["iterations"][2]["new_test_signature_count"] == 0
        assert "signature_hashes" not in trend["iterations"][0]
        monkeypatch.setattr(final_core, "execute_campaign_iteration", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fixture execution failed")))
        failed = client.post(f"/api/v1/campaigns/{campaign['id']}/schedule/run-now")
        assert failed.status_code == 200 and failed.json()["status"] == "failed"
        after_failure = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
        assert after_failure["iterations"][0]["status"] == "failed"
        assert after_failure["schedule"]["failure_count"] == 1 and "fixture execution failed" in after_failure["schedule"]["last_error"]
    finally:
        server.shutdown(); server.server_close()


def test_business_workflow_rejects_out_of_scope_and_read_only_post(client):
    engagement = create_ready(client, target="https://workflow-scope.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Scope-safe logic research", "objective": "Test workflows without leaving the frozen scope",
    }).json()
    rejected = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
        "name": "Unsafe checkout", "objective": "Must be rejected before becoming a test plan",
        "steps": [{"name": "Submit", "method": "POST", "url": "https://outside.test/checkout"}],
        "invariants": ["No out-of-scope request"], "risk_class": "read_only",
    })
    assert rejected.status_code == 409
    blockers = rejected.json()["detail"]["blocked_steps"]
    assert {item["reason"] for item in blockers} == {"out_of_scope", "non_read_method_requires_reversible_or_state_changing_risk_class"}


def test_concurrency_probe_requires_explicit_isolated_scope(client):
    engagement = create_ready(client, target="https://concurrency-scope.test")
    client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
        "label": "Research user", "role": "user", "tenant": "tenant-a",
        "auth_type": "none", "session_status": "ready",
    })
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Concurrency guard", "objective": "Prove concurrency remains opt-in and isolated",
    }).json()
    workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
        "name": "Read sequence", "objective": "Test a declared dependency without state changes",
        "steps": [
            {"name": "Discover", "method": "GET", "url": "https://concurrency-scope.test/session", "actor_role": "user"},
            {"name": "Read", "method": "GET", "url": "https://concurrency-scope.test/object", "actor_role": "user", "requires_steps": [1], "concurrency_safe": True},
        ],
        "invariants": ["Dependent reads require their declared prerequisite"], "risk_class": "read_only",
    })
    assert workflow.status_code == 201
    plan = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
    assert "sequence_violation" in {item["kind"] for item in plan["tests"]}
    assert {item["reason"] for item in plan["blocked"]} == {"concurrency_test_requires_isolated_scope"}


def test_logic_benchmark_fails_closed_on_missed_signal_or_proof_gate_bypass():
    manifest = json.loads((app.ROOT / "benchmarks" / "logic-v1.json").read_text())
    score = benchmarking.score_logic_benchmark(manifest, [
        {"kind": "concurrent_step", "stable": True},
    ], verified_findings=1)
    assert score["passed"] is False
    assert score["metrics"]["positive_recall"] == 0
    assert score["metrics"]["negative_control_failure_rate"] == 0
    assert score["proof_gate_passed"] is False


def test_logic_v2_benchmark_scores_accounting_and_collection_controls():
    manifest = json.loads((app.ROOT / "benchmarks" / "logic-v2.json").read_text())
    results = [
        {"kind": "cross_identity_sequence", "decision": "suspicious_success", "hypothesis_id": "hyp-a"},
        {"kind": "sequence_violation", "decision": "suspicious_success", "hypothesis_id": "hyp-b"},
        {"kind": "workflow_sequence", "invariant_failures": 1, "failed_invariant_kinds": ["json_sum_equals"], "hypothesis_id": "hyp-c"},
        {"kind": "concurrent_step", "stable": True},
        {"kind": "workflow_sequence", "invariant_failures": 0, "failed_invariant_kinds": []},
    ]
    score = benchmarking.score_logic_benchmark(manifest, results, verified_findings=0)
    assert score["passed"] is True
    assert score["metrics"] == {"positive_recall": 1.0, "negative_control_failure_rate": 0.0, "verified_findings": 0}


def test_logic_v3_benchmark_scores_nested_projection_controls():
    manifest = json.loads((app.ROOT / "benchmarks" / "logic-v3.json").read_text())
    results = [
        {"kind": "cross_identity_sequence", "decision": "suspicious_success", "hypothesis_id": "hyp-a"},
        {"kind": "sequence_violation", "decision": "suspicious_success", "hypothesis_id": "hyp-b"},
        {"kind": "workflow_sequence", "invariant_failures": 1, "failed_invariant_kinds": ["json_sum_equals"], "hypothesis_id": "hyp-c"},
        {"kind": "workflow_sequence", "invariant_failures": 1, "failed_invariant_kinds": ["json_filtered_sum_equals"], "hypothesis_id": "hyp-d"},
        {"kind": "concurrent_step", "stable": True},
        {"kind": "workflow_sequence", "invariant_failures": 0, "failed_invariant_kinds": []},
    ]
    score = benchmarking.score_logic_benchmark(manifest, results, verified_findings=0)
    assert score["passed"] is True and score["metrics"]["positive_recall"] == 1.0


def test_logic_v4_multi_application_benchmark_enforces_each_application_and_proof_gate():
    manifest = json.loads((app.ROOT / "benchmarks" / "logic-v4.json").read_text())
    results = [
        {"benchmark_app": "commerce", "kind": "cross_identity_sequence", "decision": "suspicious_success", "hypothesis_id": "hyp-commerce-owner"},
        {"benchmark_app": "commerce", "kind": "sequence_violation", "decision": "suspicious_success", "hypothesis_id": "hyp-commerce-sequence"},
        {"benchmark_app": "commerce", "kind": "concurrent_step", "stable": True},
        {"benchmark_app": "fintech", "kind": "workflow_sequence", "invariant_failures": 1, "failed_invariant_kinds": ["json_sum_equals"], "hypothesis_id": "hyp-fintech-ledger"},
        {"benchmark_app": "fintech", "kind": "workflow_sequence", "invariant_failures": 0},
        {"benchmark_app": "marketplace", "kind": "workflow_sequence", "invariant_failures": 1, "failed_invariant_kinds": ["json_filtered_sum_equals"], "hypothesis_id": "hyp-marketplace-settlement"},
        {"benchmark_app": "marketplace", "kind": "reversible_transition", "status": "rollback_failed", "snapshot_restored": True, "hypothesis_id": "hyp-marketplace-orphan"},
        {"benchmark_app": "marketplace", "kind": "reversible_transition", "rollback_proven": True},
        {"benchmark_app": "saas", "kind": "cross_identity_sequence", "decision": "suspicious_success", "hypothesis_id": "hyp-saas-role"},
        {"benchmark_app": "saas", "kind": "workflow_sequence", "status": "blocked", "reason": "workflow_dependency_invariants_failed"},
    ]
    score = benchmarking.score_logic_benchmark(manifest, results, verified_findings=0)
    assert score["passed"] is True and score["application_gate_passed"] is True
    assert set(score["applications"]) == {"commerce", "fintech", "marketplace", "saas"}
    test_source = (app.ROOT / "tests" / "test_final.py").read_text()
    assert all(f"def {test_name}(" in test_source for names in manifest["fixture_tests"].values() for test_name in names)
    missing_fintech = benchmarking.score_logic_benchmark(manifest, [item for item in results if item.get("hypothesis_id") != "hyp-fintech-ledger"], 0)
    assert missing_fintech["passed"] is False and missing_fintech["applications"]["fintech"]["positive_recall"] == 0.0
    proof_failure = benchmarking.score_logic_benchmark(manifest, results, verified_findings=1)
    assert proof_failure["passed"] is False and proof_failure["proof_gate_passed"] is False


def test_reversible_business_transition_requires_isolation_confirmation_and_proves_rollback(client):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    state = {"exists": False, "orphan_jobs": 0}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, value: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(value)

        def do_GET(self):
            if self.path == "/jobs":
                self._reply(json.dumps({"count": state["orphan_jobs"]}, sort_keys=True).encode())
            else:
                self._reply(b'{"exists":true}' if state["exists"] else b'{"exists":false}')

        def do_POST(self):
            state["exists"] = True
            state["orphan_jobs"] = 1
            self._reply(b'{"created":true}')

        def do_DELETE(self):
            state["exists"] = False
            state["orphan_jobs"] = 0
            self._reply(b'{"deleted":true}')

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        created = client.post("/api/v1/engagements", json={
            "name": "Reversible fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "allow_reversible_state_change": True, "environment_class": "local_fixture"},
            "policy": {"allow_state_change": True, "max_requests_per_second": 100, "max_requests": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
        identity = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Fixture operator", "role": "operator", "auth_type": "none", "session_status": "ready",
        }).json()
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Lifecycle campaign", "objective": "Prove create and cancel preserve the initial business state",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Create then cancel", "objective": "Exercise a reversible local business transition",
            "steps": [{
                "name": "Create object", "method": "POST", "url": f"{target}/objects", "actor_role": "operator",
                "body": "{}", "snapshot_url": f"{target}/objects/current", "expected_transition": "absent -> present",
                "compensation_method": "DELETE", "compensation_url": f"{target}/objects/current", "replay_safe": False,
                "rollback_probe_urls": [f"{target}/jobs"],
            }],
            "invariants": ["The fixture must return to its initial state after every test"], "risk_class": "reversible",
        })
        assert workflow.status_code == 201
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        transition = next(item for item in iteration["tests"] if item["kind"] == "reversible_transition")
        assert transition["snapshot_url"].endswith("/objects/current")
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={
            "run_id": run_id, "max_tests": 5, "confirm_reversible_state_change": True,
        })
        assert executed.status_code == 200
        result = next(item for item in executed.json()["results"] if item["kind"] == "reversible_transition")
        assert result["status"] == "observed" and result["rollback_proven"] is True
        assert len(result["exchange_ids"]) == 7 and state == {"exists": False, "orphan_jobs": 0}
        assert result["snapshot_restored"] is True and result["rollback_probe_results"][0]["matched"] is True
        journals = client.get(f"/api/v1/campaigns/{campaign['id']}/state-change-journals").json()
        assert journals[0]["id"] == result["journal_id"] and journals[0]["state"] == "restored" and journals[0]["rollback_probe_count"] == 1
        # Simulate an app interruption after a mutation was attempted. The
        # persisted journal must support compensation-only recovery.
        state["exists"] = True
        state["orphan_jobs"] = 1
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE state_change_journal SET state='mutation_attempted' WHERE id=?", (result["journal_id"],))
        unconfirmed = client.post(f"/api/v1/state-change-journals/{result['journal_id']}/recover", json={})
        assert unconfirmed.status_code == 409
        recovered = client.post(f"/api/v1/state-change-journals/{result['journal_id']}/recover", json={"confirm_compensation": True})
        assert recovered.status_code == 200 and recovered.json()["rollback_proven"] is True
        assert recovered.json()["state"] == "restored" and state == {"exists": False, "orphan_jobs": 0}
        assert recovered.json()["rollback_probe_results"][0]["matched"] is True
    finally:
        server.shutdown()
        server.server_close()


def test_multistep_reversible_transaction_rolls_back_in_reverse_order(client):
    state, jobs, events = {"a": False, "b": False}, {"a": False, "b": False}, []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload):
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            key = self.path.rsplit("/", 1)[-1]
            self.reply({"exists": (jobs if self.path.startswith("/jobs/") else state)[key]})

        def do_POST(self):
            key = self.path.rsplit("/", 1)[-1]
            state[key] = True; jobs[key] = True; events.append(f"create:{key}"); self.reply({"created": key})

        def do_DELETE(self):
            key = self.path.rsplit("/", 1)[-1]
            state[key] = False; jobs[key] = False; events.append(f"delete:{key}"); self.reply({"deleted": key})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Transaction fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "allow_reversible_state_change": True, "environment_class": "local_fixture"},
            "policy": {"allow_state_change": True, "max_requests_per_second": 100, "max_requests": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Fixture operator", "role": "operator", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Transactional lifecycle", "objective": "Prove a two-action flow unwinds as a compensation stack",
        }).json()
        steps = [{
            "name": f"Create {key}", "method": "POST", "url": f"{target}/{key}", "snapshot_url": f"{target}/{key}",
            "compensation_method": "DELETE", "compensation_url": f"{target}/{key}", "actor_role": "operator",
            "rollback_probe_urls": [f"{target}/jobs/{key}"],
            "state_before": "absent", "expected_transition": "present then absent", "replay_safe": False,
        } for key in ("a", "b")]
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Create two related objects", "objective": "Rollback B before A", "steps": steps,
            "invariants": ["Both objects return to absent"], "risk_class": "reversible",
        })
        assert workflow.status_code == 201, workflow.text
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        assert [item["kind"] for item in iteration["tests"]] == ["reversible_transaction"]
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        response = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={
            "run_id": run_id, "max_tests": 5, "confirm_reversible_state_change": True,
        })
        assert response.status_code == 200, response.text
        result = response.json()["results"][0]
        assert result["rollback_proven"] is True and result["rollback_order"] == [2, 1]
        assert events == ["create:a", "create:b", "delete:b", "delete:a"]
        assert state == {"a": False, "b": False} and jobs == {"a": False, "b": False}
        assert all(item["rollback_probe_results"][0]["matched"] for item in result["step_results"])
        journals = client.get(f"/api/v1/campaigns/{campaign['id']}/state-change-journals").json()
        assert len(journals) == 2 and {item["state"] for item in journals} == {"restored"}
    finally:
        server.shutdown(); server.server_close()


def test_cross_workflow_reversible_transaction_uses_global_compensation_stack(client):
    state = {"order": False, "reservation": False, "job": False}
    events = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload):
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            key = self.path.rsplit("/", 1)[-1]
            self.reply({"exists": state[key]})

        def do_POST(self):
            key = self.path.rsplit("/", 1)[-1]
            state[key] = True; events.append(f"create:{key}"); self.reply({"created": key})

        def do_DELETE(self):
            key = self.path.rsplit("/", 1)[-1]
            state[key] = False; events.append(f"delete:{key}"); self.reply({"deleted": key})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Cross workflow fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "allow_reversible_state_change": True, "environment_class": "local_fixture"},
            "policy": {"allow_state_change": True, "max_requests_per_second": 100, "max_requests": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Fixture operator", "role": "operator", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Order lifecycle", "objective": "Prove a three-workflow business transaction unwinds globally",
        }).json()
        workflow_ids = []
        for key in ("order", "reservation", "job"):
            response = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
                "name": f"Create {key}", "objective": f"Create and compensate {key}",
                "steps": [{
                    "name": f"Create {key}", "method": "POST", "url": f"{target}/{key}",
                    "snapshot_url": f"{target}/{key}", "compensation_method": "DELETE",
                    "compensation_url": f"{target}/{key}", "actor_role": "operator",
                    "state_before": "absent", "expected_transition": "present then absent", "replay_safe": False,
                }],
                "invariants": [f"{key} returns to absent"], "risk_class": "reversible",
                "depends_on_workflow_ids": workflow_ids[-1:],
            })
            assert response.status_code == 201, response.text
            workflow_ids.append(response.json()["id"])
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        assert len(iteration["tests"]) == 1
        transaction = iteration["tests"][0]
        assert transaction["kind"] == "cross_workflow_reversible_transaction"
        assert transaction["workflow_ids"] == workflow_ids
        assert transaction["depends_on_workflow_ids"] == []
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={
            "run_id": run_id, "max_tests": 5, "confirm_reversible_state_change": True,
        })
        assert executed.status_code == 200, executed.text
        result = executed.json()["results"][0]
        assert result["rollback_proven"] is True and result["workflow_ids"] == workflow_ids
        assert events == ["create:order", "create:reservation", "create:job", "delete:job", "delete:reservation", "delete:order"]
        assert state == {"order": False, "reservation": False, "job": False}
        assert [item["source_workflow_id"] for item in result["step_results"]] == list(reversed(workflow_ids))
    finally:
        server.shutdown(); server.server_close()


def test_rollback_probe_detects_orphan_resource_and_blocks_future_mutations(client):
    state = {"exists": False, "orphan_jobs": 0}

    class Handler(BaseHTTPRequestHandler):
        def reply(self, payload):
            body = json.dumps(payload, sort_keys=True).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            self.reply({"count": state["orphan_jobs"]} if self.path == "/jobs" else {"exists": state["exists"]})

        def do_POST(self):
            state.update(exists=True, orphan_jobs=1); self.reply({"created": True})

        def do_DELETE(self):
            # Deliberately restore the primary object but leak the related job.
            state["exists"] = False; self.reply({"deleted": True})

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Orphan fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "allow_reversible_state_change": True, "environment_class": "local_fixture"},
            "policy": {"allow_state_change": True, "max_requests_per_second": 100, "max_requests": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Operator", "role": "operator", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Leak campaign", "objective": "Detect related resources left after compensation",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Create with side effect", "objective": "Prove primary and related resource rollback",
            "steps": [{"name": "Create", "method": "POST", "url": f"{target}/objects", "actor_role": "operator",
                       "snapshot_url": f"{target}/objects/current", "compensation_method": "DELETE",
                       "compensation_url": f"{target}/objects/current", "rollback_probe_urls": [f"{target}/jobs"],
                       "expected_transition": "all state returns to baseline", "replay_safe": False}],
            "invariants": ["No orphan job remains"], "risk_class": "reversible",
        })
        assert workflow.status_code == 201, workflow.text
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={
            "run_id": run_id, "max_tests": 5, "confirm_reversible_state_change": True,
        }).json()
        result = executed["results"][0]
        assert result["status"] == "rollback_failed" and result["snapshot_restored"] is True
        assert result["rollback_probe_results"][0]["matched"] is False and result["hypothesis_id"]
        assert state == {"exists": False, "orphan_jobs": 1}
        next_iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        blocked = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{next_iteration['id']}/execute", json={
            "run_id": run_id, "max_tests": 5, "confirm_reversible_state_change": True,
        }).json()["results"][0]
        assert blocked["status"] == "blocked" and blocked["reason"].startswith("pending_state_recovery:")
    finally:
        server.shutdown(); server.server_close()


def test_multistep_workflow_extracts_transient_values_and_evaluates_invariants(client, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    secret = "fixture-secret-never-persist"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            tenant = self.headers.get("X-Test-Tenant", "tenant-a")
            if self.path == "/session":
                body = json.dumps({"order_id": "order-42" if tenant == "tenant-a" else "order-99", "secret_token": secret}).encode()
            elif self.path == "/orders/order-42":
                body = b'{"id":"order-42","owner":"tenant-a","visible":true}'
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Dynamic workflow fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True, "allow_concurrency_testing": True, "environment_class": "local_fixture"},
            "policy": {"max_requests_per_second": 100, "max_requests": 100, "max_concurrency": 3},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        first_identity = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Tenant A", "role": "user", "tenant": "tenant-a", "auth_type": "none", "session_status": "ready",
        }).json()
        second_identity = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Tenant B", "role": "user", "tenant": "tenant-b", "auth_type": "none", "session_status": "ready",
        }).json()
        identity_headers = {first_identity["id"]: {"X-Test-Tenant": "tenant-a"}, second_identity["id"]: {"X-Test-Tenant": "tenant-b"}}
        monkeypatch.setattr(traditional_runtime, "resolve_identity_headers", lambda identity_id: identity_headers[identity_id])
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Dynamic object campaign", "objective": "Carry runtime object identifiers across an authorized workflow",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Discover then fetch order", "objective": "Extract an object identifier and prove tenant ownership",
            "steps": [
                {"name": "Discover", "method": "GET", "url": f"{target}/session", "actor_role": "user", "extract": {"order_id": "/order_id", "ephemeral_secret": "/secret_token"}},
                {"name": "Fetch", "method": "GET", "url": f"{target}/orders/{{{{order_id}}}}", "actor_role": "user", "requires_steps": [1], "concurrency_safe": True, "concurrency_replays": 3},
            ],
            "invariants": ["The fetched object must belong to the active tenant"],
            "executable_invariants": [
                {"name": "Both requests succeed", "kind": "status_in", "step": 2, "expected_statuses": [200]},
                {"name": "Object id follows discovery", "kind": "json_equals", "step": 2, "pointer": "/id", "expected_template": "{{order_id}}"},
                {"name": "Tenant owns object", "kind": "json_equals", "step": 2, "pointer": "/owner", "expected_template": "{{identity_tenant}}"},
                {"name": "Responses are distinct", "kind": "body_differs_step", "step": 2, "other_step": 1},
            ], "risk_class": "read_only",
        })
        assert workflow.status_code == 201
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        assert [item["kind"] for item in iteration["tests"]] == ["workflow_sequence", "cross_identity_sequence", "sequence_violation", "concurrent_step"]
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 10})
        assert executed.status_code == 200
        sequence = next(item for item in executed.json()["results"] if item["kind"] == "workflow_sequence")
        assert "invariants" in sequence, executed.json()
        assert all(item["passed"] for item in sequence["invariants"])
        assert sequence["extracted_variables"] == ["ephemeral_secret", "order_id"]
        assert secret not in json.dumps(sequence)
        cross = next(item for item in executed.json()["results"] if item["kind"] == "cross_identity_sequence")
        assert cross["decision"] == "suspicious_success" and cross["hypothesis_id"]
        assert cross["carried_variables"] == ["ephemeral_secret", "order_id"] and secret not in json.dumps(cross)
        sequence_bypass = next(item for item in executed.json()["results"] if item["kind"] == "sequence_violation")
        assert sequence_bypass["decision"] == "suspicious_success" and sequence_bypass["hypothesis_id"]
        concurrent = next(item for item in executed.json()["results"] if item["kind"] == "concurrent_step")
        assert concurrent["stable"] is True and concurrent["workers"] == 3
        manifest = json.loads((app.ROOT / "benchmarks" / "logic-v1.json").read_text())
        benchmark = benchmarking.score_logic_benchmark(manifest, executed.json()["results"], verified_findings=0)
        assert benchmark["passed"] is True
        assert benchmark["metrics"] == {
            "positive_recall": 1.0, "negative_control_failure_rate": 0.0, "verified_findings": 0,
        }
        assert benchmark["proof_gate_passed"] is True
        with sqlite3.connect(final_core.DB) as db:
            previews = " ".join(row[0] for row in db.execute("SELECT response_body_preview FROM http_exchanges WHERE run_id=?", (run_id,)))
            evidence = " ".join(row[0] for row in db.execute("SELECT summary FROM evidence_v2 WHERE run_id=?", (run_id,)))
        assert secret not in previews and "[REDACTED]" in previews
        assert secret not in evidence
    finally:
        server.shutdown()
        server.server_close()


def test_declarative_branch_and_bounded_read_loop_execute_without_scripts(client):
    state = {"polls": 0, "guest_hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/profile":
                body = b'{"tier":"premium"}'
            elif self.path == "/premium":
                body = b'{"feature":"enabled"}'
            elif self.path == "/guest":
                state["guest_hits"] += 1
                body = b'{"feature":"guest"}'
            elif self.path == "/poll":
                state["polls"] += 1
                body = json.dumps({"ready": state["polls"] >= 3}).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Branch and loop fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests": 50, "max_requests_per_second": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Fixture user", "role": "user", "tenant": "tenant-a", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Conditional state campaign", "objective": "Follow a declared branch and wait for bounded convergence",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Feature branch and completion polling", "objective": "Execute only the matching tier and prove bounded readiness",
            "steps": [
                {"name": "Read tier", "method": "GET", "url": f"{target}/profile", "actor_role": "user", "extract": {"tier": "/tier"}},
                {"name": "Premium branch", "method": "GET", "url": f"{target}/premium", "actor_role": "user", "when_variable": "tier", "when_operator": "equals", "when_value": "premium"},
                {"name": "Guest branch", "method": "GET", "url": f"{target}/guest", "actor_role": "user", "when_variable": "tier", "when_operator": "equals", "when_value": "guest"},
                {"name": "Wait for readiness", "method": "GET", "url": f"{target}/poll", "actor_role": "user", "max_repeats": 4, "repeat_until_pointer": "/ready", "repeat_until_value": True},
            ],
            "invariants": ["Only the matching tier branch executes", "Readiness converges within four reads"],
            "executable_invariants": [
                {"name": "Premium feature loads", "kind": "status_in", "step": 2, "expected_statuses": [200]},
                {"name": "Skipped guest branch is not applicable", "kind": "status_in", "step": 3, "expected_statuses": [200]},
                {"name": "Readiness is true", "kind": "json_equals", "step": 4, "pointer": "/ready", "expected": True},
            ], "risk_class": "read_only",
        })
        assert workflow.status_code == 201, workflow.text
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        assert [item["kind"] for item in iteration["tests"]] == ["workflow_sequence"]
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 5})
        assert executed.status_code == 200, executed.text
        result = executed.json()["results"][0]
        assert result["control_flow"] == [
            {"step": 1, "skipped": False, "condition": "unconditional", "attempts": 1, "until_satisfied": None},
            {"step": 2, "skipped": False, "condition": "tier_matched", "attempts": 1, "until_satisfied": None},
            {"step": 3, "skipped": True, "condition": "tier_different", "attempts": 0, "until_satisfied": None},
            {"step": 4, "skipped": False, "condition": "unconditional", "attempts": 3, "until_satisfied": True},
        ]
        assert state["guest_hits"] == 0
        assert all(item["passed"] for item in result["invariants"])
        assert "hypothesis_id" not in result
    finally:
        server.shutdown()
        server.server_close()


def test_numeric_and_collection_invariants_create_candidate_without_promoting_finding(client):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {
                "/before": {"balance": "100.00", "line_items": ["60.00", "40.00"], "owners": ["alice", "bob"], "total": "100.00"},
                "/after": {"balance": "80.00", "line_items": ["60.00", "30.00"], "owners": ["alice", "bob"], "total": "100.00"},
                "/healthy": {"balance": "100.00", "line_items": ["60.00", "40.00"], "owners": ["alice", "bob"], "total": "100.00"},
            }.get(self.path)
            if payload is None:
                self.send_response(404); self.end_headers(); return
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Accounting fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests": 100, "max_requests_per_second": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Auditor", "role": "auditor", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Accounting invariants", "objective": "Prove balances, line items and owners remain internally consistent",
        }).json()
        broken = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Accounting drift", "objective": "Detect a business accounting mismatch after a declared transition",
            "steps": [
                {"name": "Before", "method": "GET", "url": f"{target}/before", "actor_role": "auditor"},
                {"name": "After", "method": "GET", "url": f"{target}/after", "actor_role": "auditor"},
            ],
            "invariants": ["Balance delta is expected and line items still sum to total"],
            "executable_invariants": [
                {"name": "Balance decreases by twenty", "kind": "json_numeric_delta_equals", "step": 2, "pointer": "/balance", "other_step": 1, "other_pointer": "/balance", "expected": -20, "tolerance": 0.001},
                {"name": "Line items conserve total", "kind": "json_sum_equals", "step": 2, "pointer": "/line_items", "other_step": 2, "other_pointer": "/total", "tolerance": 0.001},
                {"name": "Owners remain unique", "kind": "json_collection_unique", "step": 2, "pointer": "/owners"},
                {"name": "Alice remains an owner", "kind": "json_collection_contains", "step": 2, "pointer": "/owners", "expected": "alice"},
            ], "risk_class": "read_only",
        })
        healthy = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Healthy accounting control", "objective": "Retain a negative control for invariant false positives",
            "steps": [
                {"name": "Read baseline", "method": "GET", "url": f"{target}/healthy", "actor_role": "auditor"},
                {"name": "Read control", "method": "GET", "url": f"{target}/healthy", "actor_role": "auditor"},
            ],
            "invariants": ["Healthy accounting passes every machine invariant"],
            "executable_invariants": [
                {"name": "Balance is at least total", "kind": "json_number_compare", "step": 2, "pointer": "/balance", "operator": "gte", "expected": 100},
                {"name": "Two owners", "kind": "json_collection_size_compare", "step": 2, "pointer": "/owners", "operator": "eq", "expected": 2},
                {"name": "No duplicate owner", "kind": "json_collection_unique", "step": 2, "pointer": "/owners"},
                {"name": "No forbidden owner", "kind": "json_collection_not_contains", "step": 2, "pointer": "/owners", "expected": "mallory"},
                {"name": "Items sum to total", "kind": "json_sum_equals", "step": 2, "pointer": "/line_items", "other_step": 2, "other_pointer": "/total", "tolerance": 0.001},
            ], "risk_class": "read_only",
        })
        assert broken.status_code == healthy.status_code == 201
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        response = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 10})
        assert response.status_code == 200, response.text
        payload = response.json()
        by_workflow = {item["workflow_id"]: item for item in payload["results"] if item["kind"] == "workflow_sequence"}
        broken_result, healthy_result = by_workflow[broken.json()["id"]], by_workflow[healthy.json()["id"]]
        assert broken_result["invariant_failures"] == 1
        assert broken_result["failed_invariant_kinds"] == ["json_sum_equals"] and broken_result["hypothesis_id"]
        assert len(payload["candidate_ids"]) == 1
        assert broken_result["candidate_id"] == payload["candidate_ids"][0]
        assert healthy_result["invariant_failures"] == 0 and "hypothesis_id" not in healthy_result
        findings = client.get(f"/api/v1/findings?run_id={run_id}").json()
        assert findings["verified"] == []
        assert [item["id"] for item in findings["candidates"]] == payload["candidate_ids"]
        campaign_state = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
        assert campaign_state["candidate_links"][0]["candidate_id"] == payload["candidate_ids"][0]
        assert campaign_state["candidate_links"][0]["hypothesis_id"] == broken_result["hypothesis_id"]
        with sqlite3.connect(final_core.DB) as db:
            assert db.execute("SELECT COUNT(*) FROM canonical_findings").fetchone()[0] == 0
    finally:
        server.shutdown(); server.server_close()


def test_business_workflow_templates_apply_through_scope_validation(client):
    engagement = create_ready(client, target="https://templates.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Template campaign", "objective": "Build a bounded workflow from a reviewed template",
    }).json()
    catalog = client.get("/api/v1/business-workflow-templates")
    assert catalog.status_code == 200
    assert {item["id"] for item in catalog.json()} == {
        "object_ownership", "sequence_enforcement", "ledger_consistency", "entitlement_boundary",
    }
    applied = client.post(
        f"/api/v1/campaigns/{campaign['id']}/workflow-templates/sequence_enforcement/apply",
        json={"variables": {
            "prerequisite_url": "https://templates.test/checkout/prepare",
            "dependent_url": "https://templates.test/checkout/confirm",
            "actor_role": "buyer",
        }},
    )
    assert applied.status_code == 201, applied.text
    assert applied.json()["template_id"] == "sequence_enforcement"
    assert applied.json()["steps"][1]["requires_steps"] == [1]
    out_of_scope = client.post(
        f"/api/v1/campaigns/{campaign['id']}/workflow-templates/object_ownership/apply",
        json={"variables": {"resource_url": "https://outside.test/orders/42", "owner_role": "owner"}},
    )
    assert out_of_scope.status_code == 409
    assert client.post(
        f"/api/v1/campaigns/{campaign['id']}/workflow-templates/unknown/apply", json={"variables": {}},
    ).status_code == 404


def test_complex_invariant_declarations_fail_closed(client):
    engagement = create_ready(client, target="https://invariant-guard.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Invariant guard", "objective": "Reject incomplete numeric and collection declarations",
    }).json()
    response = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
        "name": "Invalid invariants", "objective": "Must fail before any request can execute",
        "steps": [{"name": "Read", "method": "GET", "url": "https://invariant-guard.test/state"}],
        "invariants": ["Declarations are complete"],
        "executable_invariants": [
            {"name": "Missing operator", "kind": "json_number_compare", "step": 1, "pointer": "/amount", "expected": 1},
            {"name": "Missing member", "kind": "json_collection_contains", "step": 1, "pointer": "/owners"},
            {"name": "Missing comparison", "kind": "json_numeric_delta_equals", "step": 1, "pointer": "/amount"},
            {"name": "Missing item pointer", "kind": "json_project_unique", "step": 1, "pointer": "/transfers"},
            {"name": "Missing filter", "kind": "json_filtered_sum_equals", "step": 1, "pointer": "/transfers", "item_pointer": "/amount", "expected": 10},
        ], "risk_class": "read_only",
    })
    assert response.status_code == 409
    reasons = {item["reason"] for item in response.json()["detail"]["blocked_steps"]}
    assert {"numeric_comparison_requires_operator", "collection_membership_requires_expected_value", "numeric_delta_requires_other_step_and_pointer", "numeric_delta_requires_expected_value", "collection_projection_requires_item_pointer", "filtered_sum_requires_filter_pointer_and_value"} <= reasons


def test_nested_collection_projection_detects_filtered_accounting_drift(client):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            healthy = {
                "total": "100.00", "transfers": [
                    {"id": "t1", "status": "posted", "amount": "60.00", "tenant": "tenant-a"},
                    {"id": "t2", "status": "posted", "amount": "40.00", "tenant": "tenant-a"},
                    {"id": "t3", "status": "void", "amount": "999.00", "tenant": "tenant-a"},
                ],
            }
            broken = {**healthy, "transfers": [
                {"id": "t1", "status": "posted", "amount": "60.00", "tenant": "tenant-a"},
                {"id": "t2", "status": "posted", "amount": "50.00", "tenant": "tenant-a"},
                {"id": "t3", "status": "void", "amount": "999.00", "tenant": "tenant-a"},
            ]}
            payload = healthy if self.path.startswith("/healthy") else broken if self.path.startswith("/broken") else None
            if payload is None:
                self.send_response(404); self.end_headers(); return
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Projection fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests": 100, "max_requests_per_second": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Tenant auditor", "role": "auditor", "tenant": "tenant-a", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Nested transfer campaign", "objective": "Prove posted transfer sums and nested tenant membership",
        }).json()

        def create_workflow(prefix):
            return client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
                "name": f"{prefix} transfer ledger", "objective": "Project nested transfer fields without executing expressions",
                "steps": [
                    {"name": "Read ledger", "method": "GET", "url": f"{target}/{prefix}/ledger", "actor_role": "auditor"},
                    {"name": "Read confirmation", "method": "GET", "url": f"{target}/{prefix}/confirmation", "actor_role": "auditor"},
                ], "invariants": ["Posted transfers conserve total and all entries belong to the active tenant"],
                "executable_invariants": [
                    {"name": "Posted transfers sum to total", "kind": "json_filtered_sum_equals", "step": 2, "pointer": "/transfers", "item_pointer": "/amount", "filter_pointer": "/status", "filter_expected": "posted", "other_step": 2, "other_pointer": "/total", "tolerance": 0.001},
                    {"name": "Transfer ids are unique", "kind": "json_project_unique", "step": 2, "pointer": "/transfers", "item_pointer": "/id"},
                    {"name": "Every transfer stays in tenant", "kind": "json_all_items_equal", "step": 2, "pointer": "/transfers", "item_pointer": "/tenant", "expected_template": "{{identity_tenant}}"},
                    {"name": "Posted status exists", "kind": "json_project_contains", "step": 2, "pointer": "/transfers", "item_pointer": "/status", "expected": "posted"},
                    {"name": "No failed status", "kind": "json_project_not_contains", "step": 2, "pointer": "/transfers", "item_pointer": "/status", "expected": "failed"},
                    {"name": "At least one void entry is retained", "kind": "json_any_item_equals", "step": 2, "pointer": "/transfers", "item_pointer": "/status", "expected": "void"},
                ], "risk_class": "read_only",
            })

        healthy, broken = create_workflow("healthy"), create_workflow("broken")
        assert healthy.status_code == broken.status_code == 201
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        response = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 10})
        assert response.status_code == 200, response.text
        sequences = {item["workflow_id"]: item for item in response.json()["results"] if item["kind"] == "workflow_sequence"}
        healthy_result, broken_result = sequences[healthy.json()["id"]], sequences[broken.json()["id"]]
        assert healthy_result["invariant_failures"] == 0 and "hypothesis_id" not in healthy_result
        assert broken_result["invariant_failures"] == 1
        assert broken_result["failed_invariant_kinds"] == ["json_filtered_sum_equals"] and broken_result["hypothesis_id"]
        with sqlite3.connect(final_core.DB) as db:
            assert db.execute("SELECT COUNT(*) FROM canonical_findings").fetchone()[0] == 0
    finally:
        server.shutdown(); server.server_close()


def test_cross_workflow_dependencies_import_transient_values_and_fail_closed(client):
    state = {"good_dependent_hits": 0, "blocked_dependent_hits": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            routes = {
                "/good/session": {"case_id": "case-42", "ready": True},
                "/good/context": {"ready": True},
                "/good/cases/case-42": {"id": "case-42", "owner": "tenant-a"},
                "/good/proof": {"ok": True},
                "/bad/session": {"case_id": "case-secret", "ready": False},
                "/bad/context": {"ready": False},
                "/bad/cases/case-secret": {"id": "case-secret"},
                "/bad/proof": {"ok": True},
            }
            payload = routes.get(self.path)
            if payload is None:
                self.send_response(404); self.end_headers(); return
            if self.path == "/good/cases/case-42": state["good_dependent_hits"] += 1
            if self.path == "/bad/cases/case-secret": state["blocked_dependent_hits"] += 1
            body = json.dumps(payload).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    target = f"http://127.0.0.1:{server.server_port}"
    try:
        draft = client.post("/api/v1/engagements", json={
            "name": "Cross workflow fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests": 100, "max_requests_per_second": 100},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{draft['id']}/confirm").json()
        client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
            "label": "Tenant A", "role": "user", "tenant": "tenant-a", "auth_type": "none", "session_status": "ready",
        })
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Cross workflow campaign", "objective": "Carry transient case context only after prerequisite invariants pass",
        }).json()

        def create_source(prefix, expected_ready):
            return client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
                "name": f"{prefix} discovery", "objective": "Discover a transient case id and establish prerequisite state",
                "steps": [
                    {"name": "Session", "method": "GET", "url": f"{target}/{prefix}/session", "actor_role": "user", "extract": {"case_id": "/case_id"}},
                    {"name": "Context", "method": "GET", "url": f"{target}/{prefix}/context", "actor_role": "user"},
                ], "invariants": ["The prerequisite context is ready"],
                "executable_invariants": [{"name": "Context ready", "kind": "json_equals", "step": 2, "pointer": "/ready", "expected": expected_ready}],
                "risk_class": "read_only",
            })

        good_source = create_source("good", True)
        bad_source = create_source("bad", True)
        assert good_source.status_code == bad_source.status_code == 201

        def create_dependent(prefix, source_id):
            return client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
                "name": f"{prefix} dependent", "objective": "Use a prerequisite case id without persisting its raw value",
                "depends_on_workflow_ids": [source_id], "import_variables": {"upstream_case_id": f"{source_id}.case_id"},
                "steps": [
                    {"name": "Read imported case", "method": "GET", "url": f"{target}/{prefix}/cases/{{{{upstream_case_id}}}}", "actor_role": "user"},
                    {"name": "Read proof", "method": "GET", "url": f"{target}/{prefix}/proof", "actor_role": "user"},
                ], "invariants": ["Imported case is reachable only after prerequisite success"],
                "executable_invariants": [{"name": "Imported id matches", "kind": "json_equals", "step": 1, "pointer": "/id", "expected_template": "{{upstream_case_id}}"}],
                "risk_class": "read_only",
            })

        good_dependent = create_dependent("good", good_source.json()["id"])
        bad_dependent = create_dependent("bad", bad_source.json()["id"])
        assert good_dependent.status_code == bad_dependent.status_code == 201
        invalid_import = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Invalid import", "objective": "Must fail before planning when an upstream variable was never exported",
            "depends_on_workflow_ids": [good_source.json()["id"]],
            "import_variables": {"missing": f"{good_source.json()['id']}.not_exported"},
            "steps": [
                {"name": "Read", "method": "GET", "url": f"{target}/good/cases/{{{{missing}}}}"},
                {"name": "Proof", "method": "GET", "url": f"{target}/good/proof"},
            ], "invariants": ["Imports must be declared by the upstream workflow"], "risk_class": "read_only",
        })
        assert invalid_import.status_code == 409
        assert any(item["reason"].startswith("imported_variable_not_exported") for item in invalid_import.json()["detail"]["blocked_steps"])
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        sequence_order = [item["workflow_id"] for item in iteration["tests"] if item["kind"] == "workflow_sequence"]
        assert sequence_order.index(good_source.json()["id"]) < sequence_order.index(good_dependent.json()["id"])
        assert sequence_order.index(bad_source.json()["id"]) < sequence_order.index(bad_dependent.json()["id"])
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        response = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 20})
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        good = next(item for item in results if item["kind"] == "workflow_sequence" and item["workflow_id"] == good_dependent.json()["id"])
        blocked = next(item for item in results if item["kind"] == "workflow_sequence" and item["workflow_id"] == bad_dependent.json()["id"])
        assert good["invariant_failures"] == 0 and good["imported_variables"] == ["upstream_case_id"]
        assert good["dependency_workflow_ids"] == [good_source.json()["id"]]
        assert blocked["status"] == "blocked" and blocked["reason"] == "workflow_dependency_invariants_failed"
        assert state == {"good_dependent_hits": 1, "blocked_dependent_hits": 0}
        serialized = json.dumps(results)
        assert "case-secret" not in serialized and "case-42" not in serialized
    finally:
        server.shutdown(); server.server_close()


def test_branch_and_loop_declarations_fail_closed(client):
    engagement = create_ready(client, target="https://branch-guard.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Branch guard", "objective": "Reject undeclared control flow and mutating loops",
    }).json()
    rejected = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
        "name": "Invalid control flow", "objective": "Must fail before any request can run",
        "steps": [
            {"name": "Unknown branch", "method": "GET", "url": "https://branch-guard.test/a", "when_variable": "missing", "when_operator": "exists"},
            {"name": "Mutating loop", "method": "POST", "url": "https://branch-guard.test/b", "max_repeats": 2},
        ],
        "invariants": ["No undeclared branch or write loop"], "risk_class": "state_changing",
    })
    assert rejected.status_code == 409
    reasons = {item["reason"] for item in rejected.json()["detail"]["blocked_steps"]}
    assert {"branch_variable_not_available", "bounded_loop_only_allows_read_methods"} <= reasons
def test_oast_probe_is_scope_gated_correlated_and_redacted(client):
    denied_engagement = create_ready(client, target="https://oast-denied.test")
    denied_campaign = client.post(f"/api/v1/engagements/{denied_engagement['id']}/campaigns", json={
        "name": "Denied OAST", "objective": "Prove OAST remains opt-in for every frozen scope",
    }).json()
    denied_run = client.post(f"/api/v1/engagements/{denied_engagement['id']}/start", json={"execution_mode": "demo"}).json()
    denied = client.post(f"/api/v1/campaigns/{denied_campaign['id']}/oast-probes", json={"run_id": denied_run["id"]})
    assert denied.status_code == 409 and "未显式允许" in denied.json()["detail"]

    created = client.post("/api/v1/engagements", json={
        "name": "OAST fixture", "target": "https://oast.test", "mode": "traditional",
        "scope": {"allow_oast": True, "oast_allowed_hosts": ["callbacks.example.test"]},
        "policy": {},
    }).json()
    engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Async callback campaign", "objective": "Correlate delayed callbacks without treating silence as safety",
    }).json()
    hypothesis = client.post(f"/api/v1/campaigns/{campaign['id']}/hypotheses", json={
        "category": "blind_ssrf", "statement": "An asynchronous worker may fetch an attacker-controlled callback URL",
        "priority": 90,
    }).json()
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
    remote_denied = client.post(f"/api/v1/campaigns/{campaign['id']}/oast-probes", json={
        "run_id": run_id, "callback_base": "https://unapproved.example.test/callback",
    })
    assert remote_denied.status_code == 409 and "oast_allowed_hosts" in remote_denied.json()["detail"]
    probe = client.post(f"/api/v1/campaigns/{campaign['id']}/oast-probes", json={
        "run_id": run_id, "hypothesis_id": hypothesis["id"],
        "callback_base": "http://127.0.0.1:8000/api/v1/oast/callback", "expires_minutes": 30,
    })
    assert probe.status_code == 201
    probe_data = probe.json()
    assert probe_data["status"] == "pending" and probe_data["local_only"] is True
    token = probe_data["callback_url"].rsplit("/", 1)[1]
    with sqlite3.connect(final_core.DB) as db:
        stored = db.execute("SELECT token_sha256 FROM oast_probes WHERE id=?", (probe_data["id"],)).fetchone()[0]
        assert token not in stored and len(stored) == 64
    callback = client.post(
        f"/api/v1/oast/callback/{token}?trace=controlled",
        headers={"Authorization": "Bearer should-never-persist", "Cookie": "session=should-never-persist"},
        content="worker reached controlled callback",
    )
    assert callback.status_code == 200 and callback.json()["received"] is True
    probes = client.get(f"/api/v1/campaigns/{campaign['id']}/oast-probes").json()
    saved = next(item for item in probes if item["id"] == probe_data["id"])
    assert saved["status"] == "observed" and saved["event_count"] == 1
    assert saved["callback_url"] is None
    assert saved["events"][0]["headers"]["authorization"] == "[REDACTED]"
    assert saved["events"][0]["headers"]["cookie"] == "[REDACTED]"
    assert "should-never-persist" not in json.dumps(saved)
    details = client.get(f"/api/v1/runs/{run_id}/details").json()
    observation = next(item for item in details["observations"] if item["source_capability"] == "oast-callback")
    assert observation["observation_type"] == "oast_callback"
    coverage = client.get(f"/api/v1/runs/{run_id}/coverage").json()["coverage"]
    assert next(item for item in coverage if item["surface_key"] == f"oast:{probe_data['id']}")["state"] == "tested"
    refreshed = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
    linked = next(item for item in refreshed["hypotheses"] if item["id"] == hypothesis["id"])
    assert linked["status"] == "open_proof_gap" and linked["evidence_ids"]


def test_expired_oast_probe_keeps_coverage_not_tested(client):
    created = client.post("/api/v1/engagements", json={
        "name": "Expired OAST", "target": "https://expired-oast.test", "mode": "traditional",
        "scope": {"allow_oast": True}, "policy": {},
    }).json()
    engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Expiry campaign", "objective": "Keep absent callbacks explicitly untested after expiry",
    }).json()
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
    probe = client.post(f"/api/v1/campaigns/{campaign['id']}/oast-probes", json={"run_id": run_id}).json()
    token = probe["callback_url"].rsplit("/", 1)[1]
    with sqlite3.connect(final_core.DB) as db:
        db.execute("UPDATE oast_probes SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", probe["id"]))
    expired = client.get(f"/api/v1/campaigns/{campaign['id']}/oast-probes").json()[0]
    assert expired["status"] == "expired"
    callback = client.get(f"/api/v1/oast/callback/{token}")
    assert callback.status_code == 410
    coverage = client.get(f"/api/v1/runs/{run_id}/coverage").json()["coverage"]
    assert next(item for item in coverage if item["surface_key"] == f"oast:{probe['id']}")["state"] == "not_tested"


def test_campaign_iteration_executes_guarded_cross_identity_and_duplicate_replay(client, monkeypatch):
    class LogicHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            tenant = self.headers.get("X-Test-Tenant", "anonymous")
            body = json.dumps({"tenant": tenant, "object": 42}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), LogicHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        target = f"http://127.0.0.1:{server.server_port}"
        created = client.post("/api/v1/engagements", json={
            "name": "Local logic fixture", "target": target, "mode": "traditional",
            "scope": {"allow_private_ips": True}, "policy": {"max_requests_per_second": 100, "max_requests": 1000},
        }).json()
        engagement = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
        identity_ids = []
        for label, role, tenant in (("A user", "user", "tenant-a"), ("B admin", "admin", "tenant-b")):
            identity = client.post(f"/api/v1/engagements/{engagement['id']}/identities", json={
                "label": label, "role": role, "tenant": tenant, "auth_type": "keychain_reference",
                "credential_ref": f"keychain://fieldwork/{tenant}", "session_status": "ready",
            }).json()
            identity_ids.append(identity["id"])
        header_map = {identity_ids[0]: {"X-Test-Tenant": "tenant-a"}, identity_ids[1]: {"X-Test-Tenant": "tenant-b"}}
        monkeypatch.setattr(traditional_runtime, "resolve_identity_headers", lambda identity_id: header_map[identity_id])
        campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
            "name": "Object boundary campaign", "objective": "Continuously compare tenant object authorization decisions",
        }).json()
        workflow = client.post(f"/api/v1/campaigns/{campaign['id']}/workflows", json={
            "name": "Order detail", "objective": "Compare the same object through distinct tenant sessions",
            "steps": [{"name": "Read object", "method": "GET", "url": f"{target}/api/orders/42", "actor_role": "user", "replay_safe": True}],
            "invariants": ["A tenant must not receive another tenant's object"], "risk_class": "read_only",
        })
        assert workflow.status_code == 201
        iteration = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/plan").json()
        run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        with sqlite3.connect(final_core.DB) as db:
            db.execute("UPDATE analysis_runs SET status='completed',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
        executed = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{iteration['id']}/execute", json={"run_id": run_id, "max_tests": 10})
        assert executed.status_code == 200
        result = executed.json()
        assert result["tested"] == 3 and result["blocked"] == 0
        cross = next(item for item in result["results"] if item["kind"] == "cross_identity_replay")
        assert cross["same_response"] is False and cross["hypothesis_id"]
        details = client.get(f"/api/v1/runs/{run_id}/details").json()
        assert len([item for item in details["observations"] if item["source_capability"] == "campaign-logic-runner"]) == 3
        coverage = client.get(f"/api/v1/runs/{run_id}/coverage").json()["coverage"]
        assert any(item["surface_key"].startswith(f"campaign:{campaign['id']}") and item["state"] == "tested" for item in coverage)
        campaign_detail = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
        assert campaign_detail["iterations_completed"] == 1
        hypothesis = next(item for item in campaign_detail["hypotheses"] if item["category"] == "access_control_differential")
        updated = client.patch(f"/api/v1/campaigns/{campaign['id']}/hypotheses/{hypothesis['id']}", json={
            "priority": 95, "next_action": "Repeat with an owner and a non-owner negative control",
        })
        assert updated.status_code == 200 and updated.json()["priority"] == 95
        retest = client.post(f"/api/v1/campaigns/{campaign['id']}/hypotheses/{hypothesis['id']}/retest-plan")
        assert retest.status_code == 201 and retest.json()["strategy"] == "directed_retest"
        assert {item["workflow_id"] for item in retest.json()["tests"]} == {workflow.json()["id"]}
        rerun = client.post(f"/api/v1/campaigns/{campaign['id']}/iterations/{retest.json()['id']}/execute", json={"run_id": run_id, "max_tests": 10})
        assert rerun.status_code == 200 and rerun.json()["tested"] == 3
        after = client.get(f"/api/v1/campaigns/{campaign['id']}").json()
        hypothesis_after = next(item for item in after["hypotheses"] if item["id"] == hypothesis["id"])
        assert hypothesis_after["status"] == "open_proof_gap" and hypothesis_after["attempts"] >= 2
        rejected = client.patch(f"/api/v1/campaigns/{campaign['id']}/hypotheses/{hypothesis['id']}", json={"status": "rejected"})
        assert rejected.status_code == 409 and "反证" in rejected.json()["detail"]
        counterevidence_id = final_core.uid("evidence")
        with sqlite3.connect(final_core.DB) as db:
            observation_id = db.execute("SELECT id FROM observations WHERE run_id=? LIMIT 1", (run_id,)).fetchone()[0]
            db.execute("INSERT INTO evidence_v2 VALUES(?,?,?,?,?,?,?,?)", (
                counterevidence_id, observation_id, run_id, "negative_control",
                "Owner and non-owner returned the same authorization decision under a controlled negative test",
                None, "counter", final_core.utcnow(),
            ))
        rejected_with_proof = client.patch(f"/api/v1/campaigns/{campaign['id']}/hypotheses/{hypothesis['id']}", json={
            "status": "rejected", "counterevidence_ids": [counterevidence_id],
            "next_action": "Retain the negative control and reopen only when the workflow changes",
        })
        assert rejected_with_proof.status_code == 200
        assert rejected_with_proof.json()["status"] == "rejected"
        assert rejected_with_proof.json()["counterevidence_ids"] == [counterevidence_id]
    finally:
        server.shutdown(); server.server_close()

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


def test_task_and_coverage_clear_only_hide_ui_records(client):
    ready = create_ready(client, target="https://display-cleanup.test")
    run = client.post(f"/api/v1/engagements/{ready['id']}/start", json={"execution_mode": "demo"}).json()
    assert client.post(f"/api/v1/runs/{run['id']}/stop").status_code == 200
    timestamp = final_core.utcnow()
    with sqlite3.connect(final_core.DB) as db:
        db.execute("INSERT OR REPLACE INTO coverage_v2 VALUES(?,?,?,?,?,?,?)", (
            "coverage-cleanup", run["id"], "business:authorization", "tested",
            "authorized fixture response", "[]", timestamp,
        ))
    task_clear = client.request("DELETE", "/api/v1/task-center?mode=traditional", json={"confirmation": "CLEAR_TERMINAL_TASKS"})
    assert task_clear.status_code == 200 and task_clear.json()["hidden"] >= 1
    assert all(item["id"] != run["id"] for item in client.get("/api/v1/task-center?mode=traditional").json()["items"])
    assert client.get(f"/api/v1/runs/{run['id']}").status_code == 200

    before = client.get(f"/api/v1/runs/{run['id']}/coverage").json()["coverage"]
    assert [item["id"] for item in before] == ["coverage-cleanup"]
    coverage_clear = client.request("DELETE", f"/api/v1/runs/{run['id']}/coverage-display", json={"confirmation": "CLEAR_COVERAGE_DISPLAY"})
    assert coverage_clear.status_code == 200 and coverage_clear.json()["report_preserved"] is True
    assert client.get(f"/api/v1/runs/{run['id']}/coverage").json()["coverage"] == []
    with sqlite3.connect(final_core.DB) as db:
        assert db.execute("SELECT COUNT(*) FROM coverage_v2 WHERE id='coverage-cleanup'").fetchone()[0] == 1


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


def test_program_rules_import_is_versioned_scoped_and_expiring(client):
    engagement = create_ready(client, "web3", "0x1111111111111111111111111111111111111111")
    base = {
        "engagement_id": engagement["id"], "platform": "immunefi",
        "source_uri": "https://program.example/rules",
        "rules_text": "Authorized program rules revision September 2026.",
        "valid_until": "2030-01-01T00:00:00Z",
        "scope_assets": [engagement["normalized_target"]],
        "impact_categories": {"loss_of_funds": "critical"},
        "known_issue_sources": ["https://program.example/known-issues"],
        "previous_audit_sources": ["https://program.example/audits"],
        "poc_policy": "allowed",
    }
    first = client.post("/api/v1/program-rules/import", json=base)
    second = client.post("/api/v1/program-rules/import", json={**base, "rules_text": base["rules_text"] + " Updated."})
    assert first.status_code == 201 and second.status_code == 201
    assert (first.json()["version"], second.json()["version"]) == (1, 2)
    assert first.json()["authorization_status"] == "confirmed"
    assert second.json()["authorization_status"] == "pending_reauthorization"
    assert second.json()["diff"]["rules_text_changed"] is True
    snapshots = client.get(f"/api/v1/engagements/{engagement['id']}/program-snapshots").json()
    assert [item["version"] for item in snapshots] == [2, 1]
    assert snapshots[0]["rules"]["rules_sha256"] != snapshots[1]["rules"]["rules_sha256"]
    assert snapshots[0]["authorization_status"] == "pending_reauthorization"
    assert snapshots[0]["is_latest_program_rules"] is True
    assert snapshots[1]["is_latest_program_rules"] is False
    with sqlite3.connect(final_core.DB) as db:
        db.row_factory = sqlite3.Row
        latest_row = db.execute("SELECT * FROM program_snapshots WHERE id=?", (second.json()["id"],)).fetchone()
        old_row = db.execute("SELECT * FROM program_snapshots WHERE id=?", (first.json()["id"],)).fetchone()
        original_rules = latest_row["rules"]
    with pytest.raises(Exception, match="重新授权"):
        final_core.validated_program_rules(latest_row, engagement, "loss_of_funds")
    bad_confirmation = client.post(
        f"/api/v1/program-snapshots/{second.json()['id']}/confirm-authorization",
        json={"confirmation": "YES", "note": "reviewed"},
    )
    assert bad_confirmation.status_code == 422
    confirmed = client.post(
        f"/api/v1/program-snapshots/{second.json()['id']}/confirm-authorization",
        json={"confirmation": "CONFIRM_RULE_CHANGE", "note": "范围与影响映射已重新核对"},
    )
    assert confirmed.status_code == 200
    with sqlite3.connect(final_core.DB) as db:
        db.row_factory = sqlite3.Row
        latest_row = db.execute("SELECT * FROM program_snapshots WHERE id=?", (second.json()["id"],)).fetchone()
        assert latest_row["rules"] == original_rules
    assert final_core.validated_program_rules(latest_row, engagement, "loss_of_funds")["severity"] == "critical"
    with pytest.raises(Exception, match="更新的项目规则"):
        final_core.validated_program_rules(old_row, engagement, "loss_of_funds")
    wrong_scope = client.post("/api/v1/program-rules/import", json={
        **base, "scope_assets": ["0x2222222222222222222222222222222222222222"]})
    expired = client.post("/api/v1/program-rules/import", json={
        **base, "valid_until": "2000-01-01T00:00:00Z"})
    assert wrong_scope.status_code == 422 and expired.status_code == 422


def test_identical_program_rule_version_remains_authorized(client):
    engagement = create_ready(client, "web3", "0x1111111111111111111111111111111111111111")
    payload = {
        "engagement_id": engagement["id"], "platform": "immunefi",
        "source_uri": "https://program.example/rules",
        "rules_text": "Authorized program rules revision September 2026.",
        "valid_until": "2030-01-01T00:00:00Z",
        "scope_assets": [engagement["normalized_target"]],
        "impact_categories": {"loss_of_funds": "critical"},
        "known_issue_sources": ["https://program.example/known-issues"],
        "previous_audit_sources": ["https://program.example/audits"],
        "poc_policy": "allowed",
    }
    assert client.post("/api/v1/program-rules/import", json=payload).json()["authorization_status"] == "confirmed"
    repeated = client.post("/api/v1/program-rules/import", json=payload)
    assert repeated.status_code == 201
    assert repeated.json()["authorization_status"] == "confirmed"
    assert repeated.json()["diff"]["has_changes"] is False


def test_web3_source_discovery_catalogs_entrypoints_and_review_hypotheses(client, tmp_path, monkeypatch):
    source = tmp_path / "src"
    source.mkdir()
    (source / "RiskyVault.sol").write_text("""
pragma solidity ^0.8.24;
contract RiskyVault {
    uint256 public price;
    function setPrice(uint256 next) external { price = next; }
    function execute(address target, bytes calldata data) external payable {
        target.delegatecall(data);
    }
    function quote() external view returns (uint256) { return block.timestamp * price; }
}
""")
    model = web3_analysis.source_model(tmp_path)
    assert model["summary"] == {
        "contracts": 1, "entrypoints": 3, "state_changing_entrypoints": 2,
        "state_variables": 1, "call_edges": 1, "risk_paths": 0,
        "risk_primitives": 2, "hypotheses": 3,
    }
    assert {item["name"] for item in model["entrypoints"]} == {"setPrice", "execute", "quote"}
    assert {item["subject"] for item in model["hypotheses"]} == {"delegatecall", "block.timestamp", "price/oracle dependency"}
    assert model["state_variables"] == [{"contract": "RiskyVault", "name": "price", "source": "src/RiskyVault.sol"}]
    assert next(item for item in model["entrypoints"] if item["name"] == "setPrice")["writes"] == ["price"]
    assert next(item for item in model["entrypoints"] if item["name"] == "quote")["reads"] == ["price"]
    assert model["call_graph"] == [{"source": "RiskyVault.execute", "target": "target.delegatecall", "type": "external_call"}]

    engagement = create_ready(client, "web3", str(tmp_path))
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
    monkeypatch.setattr(web3_analysis, "run_forge_build", lambda _root: {"status": "not_run"})
    inspected = client.post("/api/v1/web3/source/inspect", json={
        "engagement_id": engagement["id"], "run_id": run_id, "source_path": str(tmp_path),
    })
    assert inspected.status_code == 200, inspected.text
    assert len(inspected.json()["hypothesis_observation_ids"]) == 3
    discovery = client.get(f"/api/v1/web3/runs/{run_id}/discovery")
    assert discovery.status_code == 200
    payload = discovery.json()
    assert payload["ready"] is True and payload["summary"]["entrypoints"] == 3
    assert payload["hypotheses"][0]["severity"] == "critical"
    assert payload["state_variables"][0]["name"] == "price" and payload["call_graph"]


def test_web3_semantic_model_prioritizes_state_and_external_interaction(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "Treasury.sol").write_text("""
pragma solidity ^0.8.24;
interface IERC20 { function transfer(address,uint256) external returns (bool); }
contract Treasury {
    mapping(address => uint256) public balances;
    uint256 public totalAssets;
    IERC20 public token;
    function withdraw(uint256 amount) external {
        balances[msg.sender] -= amount;
        totalAssets -= amount;
        token.transfer(msg.sender, amount);
    }
}
""")
    model = web3_analysis.source_model(tmp_path)
    withdraw = next(item for item in model["entrypoints"] if item["name"] == "withdraw")
    assert set(withdraw["writes"]) == {"balances", "totalAssets"}
    assert withdraw["external_calls"] == ["token.transfer"]
    assert withdraw["risk_score"] >= 70
    assert model["risk_paths"] == [{
        "category": "state_external_interaction", "severity": "high",
        "entrypoint": "Treasury.withdraw(uint256)", "writes": ["balances", "totalAssets"],
        "calls": ["token.transfer"], "guards": [], "source": "src/Treasury.sol",
    }]
    assert any(item["category"] == "state_external_interaction" for item in model["hypotheses"])


@pytest.mark.skipif(not web3_lab.binary("forge"), reason="Forge optional capability not installed")
def test_web3_buggy_fixture_produces_normalized_property_counterexample(client):
    fixture = app.ROOT / "fixtures" / "web3-buggy-vault"
    result = web3_analysis.run_forge_tests(fixture)
    assert result["status"] == "failed" and result["failed_tests"] == 1
    failed = result["tests"][0]
    assert failed["name"] == "testFuzz_TotalAssetsReturnsToZero(uint96)"
    assert failed["reason"] == "totalAssets drift after full withdrawal"
    assert failed["counterexample"] and failed["counterexample"]["Single"]["args"]

    engagement = create_ready(client, "web3", str(fixture))
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
    inspected = client.post("/api/v1/web3/source/inspect", json={
        "engagement_id": engagement["id"], "run_id": run_id, "source_path": str(fixture),
    })
    assert inspected.status_code == 200, inspected.text
    payload = inspected.json()
    assert payload["fuzz"]["failed_tests"] == 1 and payload["property_observation_ids"]
    with final_core.connect() as db:
        observation = db.execute(
            "SELECT * FROM observations WHERE id=?", (payload["property_observation_ids"][0],),
        ).fetchone()
        invariant = db.execute(
            "SELECT * FROM invariant_registry WHERE engagement_id=? AND status='violated'", (engagement["id"],),
        ).fetchone()
        candidate = db.execute(
            "SELECT * FROM candidate_findings WHERE run_id=? AND category='web3_property_violation'", (run_id,),
        ).fetchone()
    assert observation["observation_type"] == "web3.property.counterexample"
    assert "totalAssets drift" in observation["summary"]
    assert invariant["statement"] == failed["name"]
    assert candidate["status"] == "candidate"
    assert final_core.load(candidate["evidence_ids"], [])

    replay = client.post(
        f"/api/v1/web3/candidates/{candidate['id']}/property-replay/jobs", json={"rounds": 2},
    )
    assert replay.status_code == 202, replay.text
    for _ in range(300):
        job = client.get(f"/api/v1/verification-jobs/{replay.json()['id']}").json()
        if job["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            break
        time.sleep(.02)
    assert job["status"] == "completed", job
    assert job["completed_requests"] == 2 and job["progress"] == 1
    replay_payload = job["result"]
    assert replay_payload["status"] == "reproduced"
    assert replay_payload["stable_failure"] is True
    assert len(replay_payload["rounds"]) == 2
    assert {item["reason"] for item in replay_payload["rounds"]} == {"totalAssets drift after full withdrawal"}
    assert all(item["counterexample_sha256"] for item in replay_payload["rounds"])
    with final_core.connect() as db:
        updated_candidate = db.execute("SELECT * FROM candidate_findings WHERE id=?", (candidate["id"],)).fetchone()
        attempt = db.execute(
            "SELECT * FROM verification_attempts WHERE id=?", (replay_payload["verification_attempt_id"],),
        ).fetchone()
        replay_observations = db.execute(
            "SELECT * FROM observations WHERE run_id=? AND observation_type='web3.property.replay'", (run_id,),
        ).fetchall()
        canonical = db.execute("SELECT * FROM canonical_findings WHERE candidate_id=?", (candidate["id"],)).fetchone()
    assert updated_candidate["status"] == "reproduced"
    assert len(final_core.load(updated_candidate["evidence_ids"], [])) == 3
    assert attempt["status"] == "reproduced" and attempt["attempts"] == 2
    assert final_core.load(attempt["result"], {})["stable_failure"] is True
    assert len(replay_observations) == 2
    assert canonical is None  # Replay stability never bypasses impact and counterevidence gates.

    rules = client.post("/api/v1/program-rules/import", json={
        "engagement_id": engagement["id"], "platform": "immunefi",
        "source_uri": "https://program.example/rules",
        "rules_text": "Bug bounty rules explicitly covering accounting invariant failures and local proof of concept.",
        "valid_until": "2030-01-01T00:00:00Z",
        "scope_assets": [engagement["normalized_target"]],
        "impact_categories": {"accounting_invariant_break": "high"},
        "known_issue_sources": ["https://program.example/known"],
        "previous_audit_sources": ["https://program.example/audits"],
        "poc_policy": "allowed",
    })
    assert rules.status_code == 201, rules.text
    finalized = client.post(f"/api/v1/web3/candidates/{candidate['id']}/finalize-property", json={
        "program_snapshot_id": rules.json()["id"],
        "impact_category": "accounting_invariant_break",
        "impact_description": "A full withdrawal leaves totalAssets inconsistent with user balances.",
        "root_cause": "withdraw omits a decrement of totalAssets", "weakness": "CWE-682",
        "location": "src/BuggyVault.sol:withdraw",
        "feasibility": "Reproduced with arbitrary positive deposits",
    })
    assert finalized.status_code == 200, finalized.text
    finalized_payload = finalized.json()
    assert finalized_payload["status"] == "verified" and finalized_payload["receipt_id"].startswith("receipt-")
    assert finalized_payload["impact_demonstrated"] is False
    canonical = client.get(f"/api/v1/findings/{finalized_payload['finding']['id']}").json()
    assert canonical["verification"]["oracle"] == "forge-property-replay-v1"
    assert canonical["verification"]["receipt_id"] == finalized_payload["receipt_id"]
    assert canonical["impact"]["demonstrated"] is False
    exported = client.post(f"/api/v1/findings/{canonical['id']}/reports/immunefi/export")
    assert exported.status_code == 202, exported.text
    with final_core.connect() as db:
        bundle_path = db.execute(
            "SELECT export_path FROM submission_packages_v2 WHERE id=?", (exported.json()["id"],),
        ).fetchone()[0]
    portable = reporting.replay_bundle(Path(bundle_path), execute=True)
    assert portable["integrity_ok"] is True and portable["recorded_replay_verified"] is True
    assert portable["replay_kind"] == "forge_property_replay_v1"
    assert portable["replay_performed"] is True and len(portable["execution"]["rounds"]) == 2
    import subprocess
    import sys
    second_process = subprocess.run(
        [sys.executable, str(app.ROOT / "scripts" / "verify_proof_bundle.py"), bundle_path, "--replay"],
        cwd=app.ROOT, capture_output=True, text=True, timeout=30,
    )
    assert second_process.returncode == 0, second_process.stderr
    separate_result = json.loads(second_process.stdout)
    assert separate_result["replay_performed"] is True
    assert separate_result["recorded_replay_verified"] is True


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
    assert "sid=abc" not in clean and "[REDACTED]" in clean


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
    assert verified.status_code == 409
    assert "服务端真实复验收据" in verified.json()["detail"]
    assert client.get(f"/api/v1/findings?run_id={run_id}").json()["verified"] == []


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


@pytest.mark.skipif(not web3_lab.binary("forge"), reason="Forge optional capability not installed")
def test_web3_deployment_alignment_binds_bytecode_block_chain_and_program_snapshot(client, monkeypatch):
    address = "0x3333333333333333333333333333333333333333"
    engagement = create_ready(client, "web3", address)
    fixture = app.ROOT / "fixtures" / "web3-vault"
    assert web3_analysis.run_forge_build(fixture)["status"] == "compiled"
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


@pytest.mark.skipif(not web3_lab.binary("forge"), reason="Forge optional capability not installed")
def test_web3_deployment_alignment_blocks_bytecode_or_chain_mismatch(client, monkeypatch):
    address = "0x4444444444444444444444444444444444444444"
    engagement = create_ready(client, "web3", address)
    fixture = app.ROOT / "fixtures" / "web3-vault"
    assert web3_analysis.run_forge_build(fixture)["status"] == "compiled"

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
        "security_boundary": "Only the account owner may read private profile fields",
        "expected_behavior": "Other identities must receive an access denial",
        "observed_behavior": "Account page appears reachable; protection requires validation",
        "impact_hypothesis": "Potential private profile access by a different identity",
        "verification_method": "Compare owner and non-owner requests and a negative control",
        "observation_ids": [observation_id],
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
    assert verified.status_code == 409
    assert "服务端真实复验收据" in verified.json()["detail"]
    assert client.get(f"/api/v1/findings?run_id={run_id}").json()["verified"] == []


def test_real_http_replay_oracle_with_negative_control(client):
    class Handler(BaseHTTPRequestHandler):
        fixed = False
        def do_GET(self):
            role = self.headers.get("X-Role")
            if not role:
                self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"unauthenticated"}')
            elif self.path == "/me":
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({"id": "tenant-a" if role == "owner" else "tenant-b"}).encode())
            elif self.path == "/api/object/42":
                if role == "other" and Handler.fixed:
                    self.send_response(403); self.end_headers(); self.wfile.write(b'{"error":"forbidden"}')
                    return
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
        payload = {
            "candidate_id": candidate["id"],
            "baseline": {"url": f"http://127.0.0.1:{port}/api/object/42", "headers": {"X-Role": "owner"}},
            "attack": {"url": f"http://127.0.0.1:{port}/api/object/42", "headers": {"X-Role": "other"}},
            "negative_control": {"url": f"http://127.0.0.1:{port}/api/object/42"},
            "authorization": {
                "baseline_identity": {"url": f"http://127.0.0.1:{port}/me", "headers": {"X-Role": "owner"}},
                "attack_identity": {"url": f"http://127.0.0.1:{port}/me", "headers": {"X-Role": "other"}},
                "principal_field": "id", "owner_field": "owner"},
            "severity": "high", "impact_description": "Cross-role object disclosure",
            "root_cause": "Missing ownership check", "weakness": "CWE-639", "location": "GET /api/object/{id}",
        }
        preview = client.post(endpoint + '/plan', json=payload)
        assert preview.status_code == 200 and preview.json()['request_count'] == 10
        assert preview.json()['sends_requests'] is False
        assert 'headers' not in json.dumps(preview.json())
        # Old response-difference recipe remains review-only, never machine proof.
        legacy = client.post(endpoint, json={k:v for k,v in payload.items() if k != 'authorization'})
        assert legacy.status_code == 200
        assert legacy.json()['replay']['differential_match'] is True
        assert legacy.json()['verification']['status'] == 'human_review'
        queued = client.post(endpoint + '/jobs', json=payload)
        assert queued.status_code == 202, queued.text
        assert queued.json()['total_requests'] == 10
        for _ in range(200):
            job = client.get(f"/api/v1/traditional/http-replay/jobs/{queued.json()['id']}").json()
            if job['status'] not in {'queued','running','cancelling'}:
                break
            time.sleep(.01)
        assert job['status'] == 'completed', job
        assert job['progress'] == 1
        data = job['result']
        assert data["replay"]["reproduced"] is True and data["replay"]["stable"] is True
        assert data["verification"]["status"] == "verified"
        assert all(check['passed'] for check in data['replay']['semantic_checks'])
        assert '_transient_body' not in json.dumps(data)
        finding_id = data['verification']['id']
        finding = client.get(f'/api/v1/findings/{finding_id}').json()
        receipt_id = finding['verification']['receipt_id']
        assert receipt_id.startswith('receipt-')
        positive_id = next(item['id'] for item in finding['evidence'] if item['polarity'] != 'counter')
        counter_id = next(item['id'] for item in finding['evidence'] if item['polarity'] == 'counter')
        report_fields = {
            'summary': 'A second authorized identity can read the owner object through the same endpoint.',
            'prerequisites': ['Two authorized test identities', 'An object owned by the baseline identity'],
            'impact_description': 'A user can disclose another user object when its identifier is known.',
            'affected_users': 'Users whose object identifiers are known to another authenticated user.',
            'impact_conditions': 'The attacker must be authenticated and know or obtain an object identifier.',
            'impact_evidence_ids': [positive_id],
            'counterevidence_summary': 'The unauthenticated request was rejected while the second authenticated identity succeeded.',
            'counterevidence_ids': [counter_id],
            'remediation': 'Enforce owner identity in the object query and retain this cross-identity regression test.',
            'platform_custom': {'testing_ip': 'Provided separately to the program'},
        }
        invalid_report = client.put(f'/api/v1/findings/{finding_id}/report-fields', json={
            **report_fields, 'counterevidence_ids': [positive_id],
        })
        assert invalid_report.status_code == 409
        saved_report = client.put(f'/api/v1/findings/{finding_id}/report-fields', json=report_fields)
        assert saved_report.status_code == 200, saved_report.text
        assert saved_report.json()['receipt_preserved'] is True
        assert saved_report.json()['report_fields']['impact']['evidence_ids'] == [positive_id]
        finding = client.get(f'/api/v1/findings/{finding_id}').json()
        assert finding['verification']['receipt_id'] == receipt_id
        preview = client.post(f'/api/v1/findings/{finding_id}/reports/hackerone/preview')
        assert preview.status_code == 200 and preview.json()['completeness']['ready']
        assert not ({'prerequisites', 'impact.affected_users', 'impact.conditions', 'impact.evidence_ids',
                     'counterevidence.summary', 'counterevidence.evidence_ids', 'remediation'}
                    & set(preview.json()['completeness']['missing_recommended']))
        assert '## Counterevidence / Negative Control' in preview.json()['content']
        assert '## Remediation' in preview.json()['content']
        assert counter_id in preview.json()['content'] and positive_id in preview.json()['content']
        package = client.post(f'/api/v1/findings/{finding_id}/reports/hackerone/export')
        assert package.status_code == 202
        assert package.json()['manifest']['proof_replay'] == {
            'schema': 'fieldwork-replay-contract/1', 'mode': 'recorded_assertion',
            'kind': 'http_authorization_read_v2', 'active_execution_supported': False,
        }
        assert client.get(package.json()['download_url']).status_code == 200
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(client.get(package.json()['download_url']).content)) as bundle:
            proof_path = f"proof/evidence/{data['artifact_id']}.json"
            assert proof_path in bundle.namelist()
            actual_proof = json.loads(bundle.read(proof_path))
            assert len(actual_proof['rounds']) == 2
            assert actual_proof['stable'] is True
            assert 'proof/environment.json' in bundle.namelist()
            assert 'proof/steps.md' in bundle.namelist()
            assert 'proof/expected.json' in bundle.namelist()
            assert 'proof/replay-contract.json' in bundle.namelist()
        with final_core.connect() as db:
            bundle_path = db.execute(
                "SELECT export_path FROM submission_packages_v2 WHERE id=?", (package.json()['id'],),
            ).fetchone()[0]
        replayed = reporting.replay_bundle(Path(bundle_path))
        assert replayed['recorded_replay_verified'] is True
        assert replayed['replay_kind'] == 'http_authorization_read_v2'
        capsule = client.get(f'/api/v1/findings/{finding_id}/proof-capsule').json()
        assert capsule['portable'] is True
        assert capsule['portability_status'] == 'recorded_assertion_ready'
        assert capsule['replay']['active_execution_supported'] is False
        claimed = client.patch(f'/api/v1/findings/{finding_id}/lifecycle', json={
            'status': 'fix_claimed', 'remediation': 'commit fixed123 enforces owner identity',
            'note': 'Deployed to the local regression fixture',
        })
        assert claimed.status_code == 200 and claimed.json()['status'] == 'fix_claimed'
        retest_run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
        planned = client.post(f'/api/v1/findings/{finding_id}/retest-plans', json={
            'run_id': retest_run, 'note': 'Repeat the original object boundary against fixed123',
        })
        assert planned.status_code == 201 and planned.json()['candidate_id'].startswith('candidate-')
        Handler.fixed = True
        fixed_payload = {**payload, 'candidate_id': planned.json()['candidate_id']}
        fixed = client.post(f'/api/v1/traditional/runs/{retest_run}/http-replay', json=fixed_payload)
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()['verification']['status'] == 'verified_fixed'
        fixed_lifecycle = client.get(f'/api/v1/findings/{finding_id}/lifecycle').json()
        assert fixed_lifecycle['status'] == 'verified_fixed'
        assert fixed_lifecycle['retests'][0]['status'] == 'fixed'
        assert fixed_lifecycle['retests'][0]['result']['receipt_id'].startswith('receipt-fixed-')
        with final_core.connect() as db:
            negative = db.execute(
                "SELECT * FROM verification_attempts WHERE candidate_id=? AND status='machine_negative_receipt'",
                (planned.json()['candidate_id'],),
            ).fetchone()
        assert negative is not None and final_core.load(negative['result'], {})['schema'] == 'fix-verification-receipt/1'
        (traditional_runtime.ARTIFACT_ROOT/f"{data['artifact_id']}.json").write_text('tampered')
        rejected = client.post(f'/api/v1/findings/{finding_id}/reports/hackerone/export')
        assert rejected.status_code == 409 and '哈希' in rejected.json()['detail']
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


def test_observation_is_not_test_coverage_and_success_reason_is_normalized(client):
    engagement = create_ready(client)
    run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run}/observations', json={
        'observation_type':'page.inventory', 'subject':'https://example.test/catalog',
        'summary':'Public catalog', 'source_capability':'native-agent', 'confidence':1,
    }).json()
    traditional_tools.record_coverage(run, 'capability:native-agent', 'tested', 'read_only_completed', [obs['id']])
    assert client.post(f'/api/v1/runs/{run}/stop').status_code == 200
    details = client.get(f'/api/v1/runs/{run}/details').json()
    assert next(x for x in details['test_items'] if x['id']=='native-agent')['status'] == 'completed'
    assert next(x for x in details['coverage'] if x['surface_key']=='https://example.test/catalog')['state'] == 'observed'
    stages = {x['id']:x for x in details['stages']}
    assert stages['verification']['status'] == 'not_tested'
    assert stages['impact']['status'] == 'not_applicable'
    client.post(f'/api/v1/runs/{run}/candidates', json={
        'title':'Boundary hypothesis', 'category':'authorization', 'target':'https://example.test/catalog',
        'hypothesis':'Requires evidence of a protected boundary', 'observation_ids':[obs['id']],
    })
    details = client.get(f'/api/v1/runs/{run}/details').json()
    assert next(x for x in details['stages'] if x['id']=='verification')['status'] == 'human_review'


@pytest.mark.parametrize('mutation', ['missing', 'unknown', 'changed_proof', 'changed_candidate', 'tampered_artifact', 'missing_artifact', 'expired', 'other_candidate'])
def test_verification_receipt_rejects_untrusted_or_stale_proof(client, tmp_path, mutation):
    import hashlib
    import verification_receipts
    engagement = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run_id}/observations', json={
        'observation_type':'http.authorization', 'subject':'https://example.test/item',
        'summary':'Receipt binding fixture', 'source_capability':'http',
    }).json()
    payload = {'title':'Bound proof', 'category':'CWE-639', 'target':'https://example.test/item',
               'hypothesis':'Authorization boundary requires proof', 'observation_ids':[obs['id']]}
    candidate = client.post(f'/api/v1/runs/{run_id}/candidates', json=payload).json()
    artifact_path = tmp_path/'receipt-fixture.json'
    artifact_path.write_text('{"fixture":"receipt binding only"}')
    with final_core.connect() as db:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)', (
            'artifact-receipt-test',run_id,'http.replay',str(artifact_path),hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
            'application/json',1,final_core.utcnow()))
    proof = final_core.VerificationInput(
        oracle='http-state-replay-v1',attempts=2,reproduced=True,counterevidence_checked=True,
        counterevidence_summary='Control rejected',severity='high',impact_description='Protected object read',
        steps=['Owner baseline','Other identity','Control','Repeat'],expected='403',actual='200',
        root_cause='Ownership check missing',weakness='CWE-639',location='GET /item',poc_artifact_ids=['artifact-receipt-test'])
    # Internal issuer is used only to isolate binding checks; real execution is tested above.
    proof.receipt_id = verification_receipts.issue_receipt(candidate['id'], proof)
    if mutation == 'missing': proof.receipt_id = None
    elif mutation == 'unknown': proof.receipt_id = 'receipt-forged'
    elif mutation == 'changed_proof': proof.severity = 'critical'
    elif mutation == 'changed_candidate':
        client.patch(f"/api/v1/findings/{candidate['id']}",json={'hypothesis':'A different claim'})
    elif mutation == 'tampered_artifact': artifact_path.write_text('modified')
    elif mutation == 'missing_artifact': artifact_path.unlink()
    elif mutation == 'expired':
        with final_core.connect() as db:
            row = db.execute('SELECT result FROM verification_attempts WHERE id=?',(proof.receipt_id,)).fetchone()
            value = json.loads(row['result']);value['expires_at']='2000-01-01T00:00:00+00:00'
            db.execute('UPDATE verification_attempts SET result=? WHERE id=?',(json.dumps(value),proof.receipt_id))
    elif mutation == 'other_candidate':
        candidate = client.post(f'/api/v1/runs/{run_id}/candidates',json={**payload,'title':'Other candidate'}).json()
    response = client.post(f"/api/v1/candidates/{candidate['id']}/verify",json=proof.model_dump())
    assert response.status_code == 409, response.text
    assert client.get(f'/api/v1/findings?run_id={run_id}').json()['verified'] == []


def test_web3_receipt_rejects_modified_program_rules(client, tmp_path):
    import hashlib
    import verification_receipts
    engagement = create_ready(client, "web3", "0x1111111111111111111111111111111111111111")
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    obs = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type":"web3.property.counterexample", "subject":"testInvariant(uint256)",
        "summary":"Property failed", "source_capability":"forge-test"}).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title":"Invariant failure", "category":"web3_property_violation", "target":"testInvariant(uint256)",
        "hypothesis":"Registered invariant fails", "observation_ids":[obs["id"]]}).json()
    artifact_path = tmp_path / "web3-replay.json"
    artifact_path.write_text('{"stable_failure":true}')
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            "artifact-web3-receipt", run_id, "web3.property_replay", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow()))
    program = client.post("/api/v1/program-rules/import", json={
        "engagement_id":engagement["id"], "platform":"immunefi", "source_uri":"https://program.example/rules",
        "rules_text":"Reviewed scope and impact policy for this verification test.",
        "valid_until":"2030-01-01T00:00:00Z", "scope_assets":[engagement["normalized_target"]],
        "impact_categories":{"invariant_break":"high"}, "known_issue_sources":["known-ref"],
        "previous_audit_sources":["audit-ref"], "poc_policy":"allowed"}).json()
    proof = final_core.VerificationInput(
        oracle="forge-property-replay-v1", attempts=2, reproduced=True,
        counterevidence_checked=True, counterevidence_summary="Distinct seeds and hashed counterexamples",
        severity="high", impact_description="Accounting invariant breaks after withdrawal",
        steps=["Replay twice"], expected="Invariant holds", actual="Invariant failed twice",
        root_cause="Accounting update missing", weakness="CWE-682", location="Vault.withdraw",
        poc_artifact_ids=["artifact-web3-receipt"], program_snapshot_id=program["id"],
        impact_in_scope=True, known_issue_checked=True, previous_audit_checked=True, poc_rule_checked=True)
    proof.receipt_id = verification_receipts.issue_receipt(candidate["id"], proof)
    with final_core.connect() as db:
        row = db.execute("SELECT rules FROM program_snapshots WHERE id=?", (program["id"],)).fetchone()
        changed = json.loads(row["rules"]); changed["impact_categories"]["invariant_break"] = "critical"
        db.execute("UPDATE program_snapshots SET rules=? WHERE id=?", (json.dumps(changed), program["id"]))
    rejected = client.post(f"/api/v1/candidates/{candidate['id']}/verify", json=proof.model_dump())
    assert rejected.status_code == 409 and "规则快照" in rejected.json()["detail"]


def test_same_root_cause_across_runs_merges_lifecycle_and_reopens_failed_fix(client, tmp_path):
    import hashlib
    import verification_receipts
    engagement = create_ready(client, target="https://lifecycle.example.test")

    def verified_occurrence(run_id, suffix):
        observation = client.post(f"/api/v1/runs/{run_id}/observations", json={
            "observation_type": "http.authorization", "subject": "GET /orders/42",
            "summary": f"Cross-tenant read reproduced {suffix}", "source_capability": "http-oracle",
        }).json()
        candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
            "title": f"Cross-tenant order read {suffix}", "category": "authorization",
            "target": "https://lifecycle.example.test/orders/42",
            "hypothesis": "A different tenant can read the owner order",
            "observation_ids": [observation["id"]],
        }).json()
        artifact_id = f"artifact-lifecycle-{suffix}"
        artifact_path = tmp_path / f"{artifact_id}.json"
        artifact_path.write_text('{"rounds":2,"stable":true}')
        with final_core.connect() as db:
            db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
                artifact_id, run_id, "http.replay", str(artifact_path),
                hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
            ))
        proof = final_core.VerificationInput(
            oracle="http-state-replay-v1", attempts=2, reproduced=True,
            counterevidence_checked=True, counterevidence_summary="Unauthenticated control rejected twice",
            severity="high", impact_description="Another tenant can read a private order",
            steps=["Owner baseline", "Other tenant replay", "Unauthenticated control", "Repeat"],
            expected="403", actual="200", root_cause="Order lookup omits tenant ownership check",
            weakness="CWE-639", location="routes/orders.py:show", poc_artifact_ids=[artifact_id],
        )
        proof.receipt_id = verification_receipts.issue_receipt(candidate["id"], proof)
        response = client.post(f"/api/v1/candidates/{candidate['id']}/verify", json=proof.model_dump())
        assert response.status_code == 200, response.text
        return response.json()

    first_run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    first = verified_occurrence(first_run, "first")
    same_run_plan = client.post(f"/api/v1/findings/{first['id']}/retest-plans", json={
        "run_id": first_run, "note": "This must not reuse the original evidence run",
    })
    assert same_run_plan.status_code == 422 and "新的 Run" in same_run_plan.json()["detail"]
    lifecycle = client.patch(f"/api/v1/findings/{first['id']}/lifecycle", json={
        "status": "fix_claimed", "remediation": "commit abc123 adds tenant_id to the order lookup",
        "note": "Developer supplied a repair build",
    })
    assert lifecycle.status_code == 200 and lifecycle.json()["status"] == "fix_claimed"
    second_run = client.post(f"/api/v1/engagements/{engagement['id']}/start").json()["id"]
    plan = client.post(f"/api/v1/findings/{first['id']}/retest-plans", json={
        "run_id": second_run, "note": "Retest commit abc123 against the original tenant boundary",
    })
    assert plan.status_code == 201
    planned_candidate_id = plan.json()["candidate_id"]
    second = verified_occurrence(second_run, "second")
    assert second["id"] == first["id"] and second["deduplicated"] is True
    assert second["occurrence_kind"] == "reopened" and second["lifecycle_status"] == "reopened"
    detail = client.get(f"/api/v1/findings/{first['id']}/lifecycle").json()
    assert detail["occurrence_count"] == 2 and len(detail["occurrences"]) == 2
    assert detail["retests"][0]["status"] == "reproduced"
    assert detail["retests"][0]["result"]["candidate_id"] == second["candidate_id"]
    with final_core.connect() as db:
        planned_candidate = db.execute(
            "SELECT status FROM candidate_findings WHERE id=?", (planned_candidate_id,),
        ).fetchone()
    assert planned_candidate["status"] == "duplicate"
    run_two_findings = client.get(f"/api/v1/findings?run_id={second_run}").json()["verified"]
    assert len(run_two_findings) == 1 and run_two_findings[0]["id"] == first["id"]
    assert run_two_findings[0]["lifecycle_status"] == "reopened"
    all_findings = client.get("/api/v1/findings").json()["verified"]
    assert len(all_findings) == 1 and all_findings[0]["occurrence_count"] == 2


def test_discovery_correlation_does_not_create_vulnerability_candidates(client):
    ready = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    for kind, source in [('http.service','httpx'),('surface.route','katana'),('browser.page_observation','native-agent')]:
        client.post(f'/api/v1/runs/{run_id}/observations',json={
            'observation_type':kind,'subject':'https://example.test/products',
            'summary':'Public catalog and brand names','source_capability':source,'confidence':1})
    result = client.post(f'/api/v1/runs/{run_id}/correlate').json()
    assert result['created'] == [] and result['discovery_observations'] == 3
    assert client.get(f'/api/v1/findings?run_id={run_id}').json()['candidates'] == []


def test_single_security_detector_signal_is_not_lost(client):
    ready = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    client.post(f'/api/v1/runs/{run_id}/observations',json={
        'observation_type':'code.static_finding','subject':'app.py:32',
        'summary':'User input reaches an unsafe query sink','source_capability':'semgrep','confidence':.8})
    first = client.post(f'/api/v1/runs/{run_id}/correlate').json()
    assert first['count'] == 1 and first['created'][0]['category'] == 'code_review'
    assert client.post(f'/api/v1/runs/{run_id}/correlate').json()['count'] == 0
    assert client.get(f'/api/v1/findings?run_id={run_id}').json()['verified'] == []


def test_agent_inventory_and_unbound_claims_are_not_admitted(client):
    ready = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run_id}/observations',json={
        'observation_type':'browser.page_observation','subject':'https://example.test/products',
        'summary':'Public catalog','source_capability':'native-agent'}).json()
    with final_core.connect() as db:
        run = dict(db.execute('SELECT * FROM analysis_runs WHERE id=?',(run_id,)).fetchone())
    item = {'title':'Brand names','category':'inventory','target':'https://example.test/products','summary':'Visible brands'}
    assert native_agent._record_hypotheses(run,[item],[obs['id']]) == []
    from candidate_quality import REQUIRED_CLAIM_FIELDS
    claim = {**item, 'category':'authorization', **{key:'A bounded security claim' for key in REQUIRED_CLAIM_FIELDS}, 'observation_ids':['obs-unknown']}
    assert native_agent._record_hypotheses(run,[claim],[obs['id']]) == []
    claim['observation_ids']=[obs['id']];claim['target']='https://outside.test/'
    assert native_agent._record_hypotheses(run,[claim],[obs['id']]) == []
    events = client.get(f'/api/v1/runs/{run_id}').json()['events']
    assert sum(e['kind']=='hypothesis.not_admitted' for e in events) == 3


def test_candidate_detail_explains_evidence_without_changing_status(client):
    ready = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run_id}/observations', json={
        'observation_type': 'browser.page_observation', 'subject': 'https://example.test/products',
        'summary': 'Public product catalog', 'source_capability': 'native-agent'}).json()
    candidate = client.post(f'/api/v1/runs/{run_id}/candidates', json={
        'title': 'Legacy catalog hypothesis', 'category': 'correlated_observation',
        'target': 'https://example.test/products', 'hypothesis': 'Requires security triage',
        'observation_ids': [obs['id']]}).json()
    response = client.get(f"/api/v1/candidates/{candidate['id']}")
    assert response.status_code == 200
    detail = response.json()
    assert detail['needs_triage'] is True
    assert detail['next_action']['stage'] == 'triage'
    listed = client.get(f'/api/v1/findings?run_id={run_id}').json()['candidates']
    assert next(item for item in listed if item['id'] == candidate['id'])['next_action'] == detail['next_action']
    assert detail['available_method'] == 'manual_review'
    assert detail['verification_level'] == 'V0 / 缺少安全语义'
    assert detail['evidence'][0]['observation_id'] == obs['id']
    assert detail['evidence'][0]['subject'] == 'https://example.test/products'
    assert detail['attempts'] == []
    assert detail['candidate']['status'] == candidate['status']
    assert '不执行复验' in detail['save_behavior']
    assert client.get('/api/v1/candidates/missing-candidate').status_code == 404
    client.post(f'/api/v1/runs/{run_id}/stop')


def test_candidate_triage_separates_active_work_from_preserved_history(client):
    ready = create_ready(client)
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run_id}/observations', json={
        'observation_type':'http.authorization','subject':'GET /objects/42',
        'summary':'Candidate triage evidence','source_capability':'http'}).json()
    def create(title):
        return client.post(f'/api/v1/runs/{run_id}/candidates', json={
            'title':title,'category':'authorization','target':'https://example.test/objects/42',
            'hypothesis':'Object ownership needs validation','observation_ids':[obs['id']]}).json()
    original, duplicate, pending = create('Original access claim'), create('Repeated access claim'), create('Needs more evidence')
    evidence = client.post(f"/api/v1/candidates/{pending['id']}/triage", json={
        'disposition':'needs_evidence','reason':'Second authenticated identity is missing'})
    assert evidence.status_code == 200 and evidence.json()['status'] == 'needs_evidence'
    invalid = client.post(f"/api/v1/candidates/{duplicate['id']}/triage", json={
        'disposition':'duplicate','reason':'Same object and boundary'})
    assert invalid.status_code == 422
    merged = client.post(f"/api/v1/candidates/{duplicate['id']}/triage", json={
        'disposition':'duplicate','reason':'Same object and boundary','duplicate_of':original['id']})
    assert merged.status_code == 200 and merged.json()['history_preserved'] is True
    visible = client.get(f'/api/v1/findings?run_id={run_id}').json()['candidates']
    assert {item['id'] for item in visible} == {original['id'], pending['id']}
    ledger = client.get(f'/api/v1/runs/{run_id}/coverage').json()['graveyard']
    assert any(item['id'] == merged.json()['graveyard_id'] and 'duplicate_of' in item['reason'] for item in ledger)
    detail = client.get(f"/api/v1/candidates/{original['id']}").json()
    assert detail['available_method'] == 'http_workbench'
    assert duplicate['id'] not in {item['id'] for item in detail['peer_candidates']}
    client.post(f'/api/v1/runs/{run_id}/stop')


def test_authorization_oracle_rejects_normal_and_ambiguous_responses():
    def response(status, body):
        import hashlib
        text = json.dumps(body)
        return {'status': status, '_transient_body': text, 'body_sha256': hashlib.sha256(text.encode()).hexdigest()}
    assertion = traditional_runtime.AuthorizationAssertion(
        baseline_identity={'url':'https://example.test/me'}, attack_identity={'url':'https://example.test/me'},
        principal_field='id', owner_field='owner')
    valid = {'baseline': response(200, {'owner':'a'}), 'attack': response(200, {'owner':'a'}),
             'negative_control': response(401, {}), 'baseline_identity': response(200, {'id':'a'}),
             'attack_identity': response(200, {'id':'b'})}
    assert traditional_runtime.authorization_round(valid, assertion)['passed']
    for name, replacement in [
        ('negative_control',response(200, {'owner':'a'})),  # public object
        ('attack',response(403, {})),  # authorization enforced
        ('attack_identity',response(200, {'id':'a'})),  # same principal
        ('baseline_identity',response(401, {'id':'a'})),  # expired session
        ('baseline',response(200, {'owner':'c'})),  # unbound ownership
        ('attack',response(200, {'owner':'b'})),  # legitimate own object
        ('attack_identity',response(200, {})),  # absent identity
    ]:
        assert not traditional_runtime.authorization_round({**valid,name:replacement}, assertion)['passed']


def test_http_verification_job_can_be_cancelled_without_persisting_credentials(client, monkeypatch):
    created = client.post("/api/v1/engagements", json={
        "name": "Cancelable verification", "target": "http://127.0.0.1:8123", "mode": "traditional",
        "scope": {"allow_private_ips": True}, "policy": {"max_requests_per_second": 200},
    }).json()
    ready = client.post(f"/api/v1/engagements/{created['id']}/confirm").json()
    run_id = client.post(f"/api/v1/engagements/{ready['id']}/start").json()['id']
    obs = client.post(f'/api/v1/runs/{run_id}/observations', json={
        'observation_type':'http.authorization','subject':'GET /object/42',
        'summary':'Authorization signal','source_capability':'http'}).json()
    candidate = client.post(f'/api/v1/runs/{run_id}/candidates', json={
        'title':'Cancelable boundary test','category':'authorization',
        'target':'http://127.0.0.1:8123/object/42','hypothesis':'Different principal may read the object',
        'observation_ids':[obs['id']]}).json()
    entered, release = threading.Event(), threading.Event()

    def delayed_request(_):
        entered.set(); release.wait(2)
        return {'status': 200, 'body_sha256':'fixture', 'body_bytes':2,
                'headers':{}, 'body_preview':'{}', '_transient_body':'{}'}

    monkeypatch.setattr(traditional_runtime, 'request_once', delayed_request)
    payload = {
        'candidate_id':candidate['id'],
        'baseline':{'url':candidate['target'],'headers':{'Authorization':'Bearer owner-secret'}},
        'attack':{'url':candidate['target'],'headers':{'Authorization':'Bearer other-secret'}},
        'negative_control':{'url':candidate['target']},
        'authorization':{
            'baseline_identity':{'url':'http://127.0.0.1:8123/me','headers':{'Authorization':'Bearer owner-secret'}},
            'attack_identity':{'url':'http://127.0.0.1:8123/me','headers':{'Authorization':'Bearer other-secret'}},
            'principal_field':'id','owner_field':'owner'},
        'severity':'medium','impact_description':'Object disclosure','root_cause':'Missing ownership check',
        'weakness':'CWE-639','location':'GET /object/42'}
    started = client.post(f'/api/v1/traditional/runs/{run_id}/http-replay/jobs', json=payload)
    assert started.status_code == 202 and entered.wait(1)
    cancelled = client.post(f"/api/v1/verification-jobs/{started.json()['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()['cancel_requested'] is True
    release.set()
    for _ in range(100):
        job = client.get(f"/api/v1/verification-jobs/{started.json()['id']}").json()
        if job['status'] == 'cancelled':
            break
        time.sleep(.01)
    assert job['status'] == 'cancelled' and job['result'] is None
    assert 'owner-secret' not in json.dumps(job) and 'other-secret' not in json.dumps(job)
    with final_core.connect() as db:
        stored = json.dumps(dict(db.execute('SELECT * FROM verification_jobs WHERE id=?',(job['id'],)).fetchone()))
        assert 'owner-secret' not in stored and 'other-secret' not in stored


def test_web3_property_job_can_be_cancelled_between_forge_rounds(client, monkeypatch, tmp_path):
    root = tmp_path / "foundry-project"
    (root / "src").mkdir(parents=True)
    (root / "foundry.toml").write_text("[profile.default]\nsrc = 'src'\n")
    (root / "src" / "Vault.sol").write_text("pragma solidity ^0.8.24; contract Vault {}")
    engagement = create_ready(client, "web3", str(root))
    run_id = client.post(f"/api/v1/engagements/{engagement['id']}/start", json={"execution_mode": "demo"}).json()["id"]
    client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "web3.compiler_result", "subject": str(root),
        "summary": "Foundry source root", "source_capability": "forge",
    })
    evidence = client.post(f"/api/v1/runs/{run_id}/observations", json={
        "observation_type": "web3.property.counterexample", "subject": "testInvariant(uint256)",
        "summary": "Invariant failed with a concrete counterexample", "source_capability": "forge",
    }).json()
    candidate = client.post(f"/api/v1/runs/{run_id}/candidates", json={
        "title": "Cancelable invariant replay", "category": "web3_property_violation",
        "target": "testInvariant(uint256)", "hypothesis": "Registered invariant fails",
        "observation_ids": [evidence["id"]],
    }).json()
    entered, release = threading.Event(), threading.Event()

    def delayed_replay(_root, target, seed):
        entered.set()
        release.wait(2)
        return {"status": "failed", "seed": seed, "tests": [{
            "name": target, "status": "failed", "reason": "invariant drift",
            "fuzz_runs": 256, "counterexample": {"amount": 1},
        }]}

    monkeypatch.setattr(web3_analysis, "run_forge_property_replay", delayed_replay)
    started = client.post(f"/api/v1/web3/candidates/{candidate['id']}/property-replay/jobs", json={"rounds": 2})
    assert started.status_code == 202 and entered.wait(1)
    cancelled = client.post(f"/api/v1/verification-jobs/{started.json()['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelling"
    release.set()
    for _ in range(100):
        job = client.get(f"/api/v1/verification-jobs/{started.json()['id']}").json()
        if job["status"] == "cancelled":
            break
        time.sleep(.01)
    assert job["status"] == "cancelled" and job["completed_requests"] == 1
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM verification_attempts WHERE candidate_id=?", (candidate["id"],)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=? AND kind='web3.property_replay'", (run_id,)).fetchone()[0] == 0
