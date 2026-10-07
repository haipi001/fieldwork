import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

import final_core
import lifecycle
import v5_research_worker as research
import v5_native_discovery as native_discovery
import v5_orchestration as orchestration
import v5_runtime as runtime
from tests.test_final import client
from tests.test_v5_research_worker import create, team
from tests.test_v5_native_discovery import prepare as prepare_native
from v5_runtime_calls import fail_call, reserve_call, start_call
from v6_schema import V6_SCHEMA_STATEMENTS, V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION


def _reserved_research_call(client):
    _, body = team(client, count=1, with_evidence=False)
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "gateway-model", "llama_cpp", "Gateway fixture", "http://127.0.0.1:1",
            "fixture", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    group_id = create(client, body)
    with final_core.connect() as db:
        now = final_core.utcnow()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            research.RUNNER_ID, "Built-in research", "research-worker", "online",
            '["recorded_graph_research"]', '{"location":"local"}', 32, 1,
            now, '{"builtin":true}', now, now,
        ))
        db.execute("UPDATE agent_tasks SET status='running',attempt=1,lease_owner=?,"
                   "lease_expires_at=?,heartbeat_at=? WHERE group_id=?",
                   (research.RUNNER_ID, expires, now, group_id))
        task = db.execute("SELECT * FROM agent_tasks WHERE group_id=?", (group_id,)).fetchone()
    route = research._route(task)
    call = reserve_call(route["id"], task["id"], research.RUNNER_ID, 1)
    with final_core.connect() as db:
        binding = db.execute("SELECT * FROM model_gateway_bindings_v6 WHERE call_id=?", (call["id"],)).fetchone()
        assert binding is not None
    return call, dict(binding), task


def test_revocation_between_reservation_and_start_prevents_dispatch(client):
    call, binding, _ = _reserved_research_call(client)
    response = client.post(f"/api/v1/v6/capability-grants/{binding['grant_id']}/revoke",
                           json={"reason_code": "operator_revoked"})
    assert response.status_code == 200
    with pytest.raises(HTTPException, match="model gateway denied dispatch"):
        start_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT state FROM runtime_calls WHERE id=?", (call["id"],)).fetchone()[0] == "released"
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_gateway_starts_v6").fetchone()[0] == 0
        decision = db.execute("SELECT decision,reasons_json FROM policy_decisions_v6").fetchone()
        assert decision["decision"] == "deny" and "capability.required" in json.loads(decision["reasons_json"])


def test_scope_change_between_reservation_and_start_prevents_dispatch(client):
    call, binding, task = _reserved_research_call(client)
    with final_core.connect() as db:
        db.execute("UPDATE engagements_v2 SET current_policy_id='changed-policy' WHERE id="
                   "(SELECT engagement_id FROM research_campaigns WHERE id=?)", (task["campaign_id"],))
    with pytest.raises(HTTPException, match="worker scope or policy changed"):
        start_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT state FROM runtime_calls WHERE id=?", (call["id"],)).fetchone()[0] == "reserved"
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_gateway_starts_v6").fetchone()[0] == 0
    fail_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT state FROM runtime_calls WHERE id=?", (call["id"],)).fetchone()[0] == "released"


def test_model_start_consumes_one_grant_in_the_calling_transaction(client):
    call, binding, _ = _reserved_research_call(client)
    start_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT state FROM runtime_calls WHERE id=?", (call["id"],)).fetchone()[0] == "calling"
        use = db.execute("SELECT grant_id,intent_id,action_id FROM capability_uses_v6").fetchone()
        assert tuple(use) == (binding["grant_id"], binding["intent_id"], call["id"])
        start = db.execute("SELECT policy_decision_id,grant_use_id FROM model_gateway_starts_v6 "
                           "WHERE call_id=?", (call["id"],)).fetchone()
        assert start and db.execute("SELECT decision FROM policy_decisions_v6 WHERE id=?",
                                    (start["policy_decision_id"],)).fetchone()[0] == "allow_with_limit"
    with pytest.raises(HTTPException, match="cannot be started or replayed"):
        start_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM model_gateway_starts_v6").fetchone()[0] == 1
    fail_call(call["id"])
    with final_core.connect() as db:
        assert db.execute("SELECT state FROM runtime_calls WHERE id=?", (call["id"],)).fetchone()[0] == "unknown"


def test_native_model_start_uses_separate_worker_identity_and_one_use_grant(client):
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "native-gateway-model", "llama_cpp", "Native gateway fixture", "http://127.0.0.1:1",
            "fixture", None, 1, '{"location":"local"}', "healthy", now, now, now,
        ))
    profile = client.put("/api/v1/runtime/config", json={"name": "Native gateway profile", "mode": "local",
        "config": {"local_provider_ids": ["native-gateway-model"]}})
    assert profile.status_code == 201, profile.text
    engagement = prepare_native(client, "https://native-gateway.example.test", profile.json()["id"])
    run_id = "native-discovery-run"
    with final_core.connect() as db:
        now = final_core.utcnow()
        db.execute("INSERT INTO research_campaigns VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "native-gateway-campaign", engagement["id"], "Native gateway", "Model fixture", "active",
            "business_logic", 12, 0, 1, .85, now, now,
        ))
    created = orchestration.create_task(orchestration.TaskCreate(
        campaign_id="native-gateway-campaign", run_id=run_id, role="explorer",
        objective="Choose a bounded read-only navigation", route_requirement={"kind": "native-discovery"},
        idempotency_key="native-gateway-task", max_attempts=1,
        context_capsule={"native_discovery": True, "native_input_hashes": {},
            "scope_snapshot_id": engagement["current_scope_snapshot_id"],
            "policy_id": engagement["current_policy_id"]},
        budget={"max_tokens": 8000, "max_runtime_ms": 45000, "max_cost_micros": 0},
    ))
    with final_core.connect() as db:
        now = final_core.utcnow()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            native_discovery.RUNNER, "Native built-in", "native-discovery", "online",
            '["native_discovery"]', '{"location":"local"}', 1, 1,
            now, '{"builtin":true}', now, now,
        ))
        db.execute("UPDATE agent_tasks SET status='running',attempt=1,lease_owner=?,"
                   "lease_expires_at=?,heartbeat_at=? WHERE id=?",
                   (native_discovery.RUNNER, expires, now, created["id"]))
    route = runtime.route_task(runtime.RouteRequest(task_id=created["id"],
        task_type="native_discovery", profile_id=profile.json()["id"], sensitivity="secret",
        mode="local", estimated_tokens=8000))
    assert route["status"] == "selected"
    call = reserve_call(route["id"], created["id"], native_discovery.RUNNER, 1)
    start_call(call["id"])
    with final_core.connect() as db:
        binding = db.execute("SELECT * FROM model_gateway_bindings_v6 WHERE call_id=?", (call["id"],)).fetchone()
        assert binding["principal_id"] == "fieldwork:native-discovery-worker"
        assert binding["agent_id"] == "fieldwork:native-discovery-agent-v1"
        assert db.execute("SELECT COUNT(*) FROM model_gateway_starts_v6 WHERE call_id=?", (call["id"],)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6 WHERE action_id=?", (call["id"],)).fetchone()[0] == 1
    with pytest.raises(HTTPException, match="cannot be started or replayed"):
        start_call(call["id"])


def test_gateway_schema_upgrade_preserves_prior_intent_rows(tmp_path):
    import sqlite3

    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        db.execute(V6_SCHEMA_STATEMENTS[1])
        db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "old-intent", "campaign", "run", "task", "agent", "principal", "claimed", "network.request",
            "read", "https://example.test", None, None, "research", 0,
            "scope", "a" * 64, "policy", "b" * 64, "proposed", "2026-10-08T00:00:00+00:00",
        ))
        db.execute("PRAGMA user_version=31")
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)["backup"]
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
        assert db.execute("SELECT resource FROM action_intents_v6 WHERE id='old-intent'").fetchone()[0] == "https://example.test"
        assert db.execute("SELECT COUNT(*) FROM model_gateway_bindings_v6").fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 31
        assert db.execute("SELECT resource FROM action_intents_v6 WHERE id='old-intent'").fetchone()[0] == "https://example.test"
