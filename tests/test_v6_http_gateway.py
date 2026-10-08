import hashlib
import sqlite3

import pytest

import final_core
import lifecycle
import native_agent
from tests.test_final import client
from tests.test_http_business_boundary import object_server
from tests.test_v5_native_discovery import prepare
from v6_http_gateway import authorize_native_browser_read, finish_native_browser_read
from v6_runtime_bridge import list_events
from v6_schema import V6_SCHEMA_STATEMENTS, V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION


def test_native_browser_read_has_one_use_policy_event_and_receipt(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    url = "https://native-browser.example.test/page?fixture=redacted"
    action_id = authorize_native_browser_read("native-discovery-run", engagement["id"], url)
    body = b"fixture browser response"
    finish_native_browser_read(action_id, response={
        "status": 200, "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
    })
    with final_core.connect() as db:
        execution = db.execute("SELECT * FROM http_gateway_executions_v6 WHERE action_id=?", (action_id,)).fetchone()
        intent = db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (execution["intent_id"],)).fetchone()
        grant = db.execute("SELECT * FROM capability_grants_v6 WHERE id=?", (execution["grant_id"],)).fetchone()
        receipt = db.execute("SELECT * FROM http_gateway_receipts_v6 WHERE action_id=?", (action_id,)).fetchone()
        assert intent["resource"] == "https://native-browser.example.test/page"
        assert "fixture=redacted" not in str(dict(intent))
        assert intent["principal_id"] == "fieldwork:native-browser-worker"
        assert grant["max_uses"] == 1 and grant["approval_state"] == "active"
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6 WHERE action_id=?", (action_id,)).fetchone()[0] == 1
        assert db.execute("SELECT decision FROM policy_decisions_v6 WHERE id=?",
                          (execution["policy_decision_id"],)).fetchone()[0] == "allow_with_limit"
        assert receipt["status"] == "completed" and receipt["body_sha256"] == hashlib.sha256(body).hexdigest()
        assert db.execute("SELECT status FROM agent_tasks WHERE id=?", (execution["task_id"],)).fetchone()[0] == "succeeded"
        events = [event for event in list_events(db, campaign_id=db.execute(
            "SELECT campaign_id FROM agent_tasks WHERE id=?", (execution["task_id"],)).fetchone()[0])
            if event["metadata"]["legacy_entity_id"] == action_id]
        assert [event["event_type"] for event in events] == [
            "tool.execution.started", "tool.execution.completed",
        ]
        assert all(event["task_id"] == execution["task_id"] and event["run_id"] == "native-discovery-run"
                   for event in events)
    with pytest.raises(ValueError, match="already settled"):
        finish_native_browser_read(action_id, response={
            "status": 200, "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
        })


def test_native_browser_request_uses_pinned_transport_and_v6_receipt(client, object_server):
    origin, calls = object_server
    engagement = prepare(client, origin, None)
    with final_core.connect() as db:
        run = dict(db.execute("SELECT * FROM analysis_runs WHERE id='native-discovery-run'").fetchone())
    response = native_agent._pinned_browser_get(run, final_core.get_engagement(engagement["id"]),
                                                origin + "/object", lambda: None)
    assert response["status"] == 403 and response["process_execution"]["network_connect_denied"] is True
    assert calls == [("/object", None)]
    with final_core.connect() as db:
        assert db.execute("SELECT requests_used FROM run_budgets_v2 WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
        receipt = db.execute("SELECT r.*,x.task_id FROM http_gateway_receipts_v6 r "
                             "JOIN http_gateway_executions_v6 x ON x.action_id=r.action_id").fetchone()
        assert receipt["status"] == "completed" and receipt["http_status"] == 403
        assert receipt["body_sha256"] == response["body_sha256"]
        assert db.execute("SELECT status FROM agent_tasks WHERE id=?", (receipt["task_id"],)).fetchone()[0] == "succeeded"


def test_native_browser_cancellation_before_transport_records_failure_without_network(client, object_server):
    origin, calls = object_server
    engagement = prepare(client, origin, None)
    with final_core.connect() as db:
        run = dict(db.execute("SELECT * FROM analysis_runs WHERE id='native-discovery-run'").fetchone())
    checks = 0
    def cancelled():
        nonlocal checks
        checks += 1
        if checks > 1:
            raise ValueError("native_agent_authority_changed")
    with pytest.raises(ValueError, match="native_agent_authority_changed"):
        native_agent._pinned_browser_get(run, final_core.get_engagement(engagement["id"]),
                                         origin + "/object", cancelled)
    assert calls == []
    with final_core.connect() as db:
        receipt = db.execute("SELECT status,error_type FROM http_gateway_receipts_v6").fetchone()
        assert tuple(receipt) == ("failed", "ValueError")
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 1


def test_native_browser_gateway_rejects_stale_run_and_sensitive_resource(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    with pytest.raises(ValueError, match="embedded credentials"):
        authorize_native_browser_read("native-discovery-run", engagement["id"],
                                      "https://user:secret@native-browser.example.test/")
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='completed' WHERE id='native-discovery-run'")
    with pytest.raises(ValueError, match="Run authority is stale"):
        authorize_native_browser_read("native-discovery-run", engagement["id"],
                                      "https://native-browser.example.test/")
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM http_gateway_executions_v6").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 0


def test_native_browser_gateway_denial_is_audited_without_start_or_grant_use(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    with pytest.raises(ValueError, match="denied by V6 policy"):
        authorize_native_browser_read("native-discovery-run", engagement["id"],
                                      "https://outside.example.test/page")
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM http_gateway_executions_v6").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM capability_uses_v6").fetchone()[0] == 0
        assert db.execute("SELECT decision FROM policy_decisions_v6").fetchone()[0] == "deny"
        assert db.execute("SELECT status FROM agent_tasks").fetchone()[0] == "failed"


def test_native_browser_gateway_schema_upgrade_keeps_prior_records(tmp_path):
    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        db.execute(V6_SCHEMA_STATEMENTS[1])
        db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "old-intent", "campaign", "run", "task", "agent", "principal", "claimed", "network.request",
            "read", "https://example.test", None, None, "research", 0,
            "scope", "a" * 64, "policy", "b" * 64, "proposed", "2026-10-08T00:00:00+00:00",
        ))
        db.execute("PRAGMA user_version=32")
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)["backup"]
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
        assert db.execute("SELECT resource FROM action_intents_v6 WHERE id='old-intent'").fetchone()[0] == "https://example.test"
        assert db.execute("SELECT COUNT(*) FROM http_gateway_executions_v6").fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 32
