import hashlib
import json
import sqlite3

import pytest
from fastapi import HTTPException

import final_core
import lifecycle
import native_agent
import traditional_runtime as http
from tests.test_final import client
from tests.test_http_business_boundary import object_server
from tests.test_v5_native_discovery import prepare
from v6_http_gateway import authorize_native_browser_read, finish_http_read, link_completed_reads_to_artifact
from v6_runtime_bridge import list_events
from v6_schema import V6_SCHEMA_STATEMENTS, V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION


def test_native_browser_read_has_one_use_policy_event_and_receipt(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    url = "https://native-browser.example.test/page?fixture=redacted"
    action_id = authorize_native_browser_read("native-discovery-run", engagement["id"], url)
    body = b"fixture browser response"
    finish_http_read(action_id, response={
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
        finish_http_read(action_id, response={
            "status": 200, "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
        })


def test_read_artifact_lineage_rejects_cross_run_and_is_immutable(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    action_id = authorize_native_browser_read("native-discovery-run", engagement["id"],
                                               "https://native-browser.example.test/page")
    finish_http_read(action_id, response={"status": 200, "body_sha256": "a" * 64, "body_bytes": 1})
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            "artifact-other", "another-run", "test", "fixture://other", "a" * 64,
            "application/json", 1, final_core.utcnow()))
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            "artifact-own", "native-discovery-run", "test", "fixture://own", "a" * 64,
            "application/json", 1, final_core.utcnow()))
        with pytest.raises(ValueError, match="does not belong"):
            link_completed_reads_to_artifact(db, action_ids=[action_id],
                                             artifact_id="artifact-other", run_id="native-discovery-run")
        with pytest.raises(ValueError, match="conflicts with its gateway receipt"):
            link_completed_reads_to_artifact(db, action_ids=[action_id],
                                             artifact_id="artifact-own", run_id="native-discovery-run",
                                             response={"status": 404, "body_sha256": "a" * 64, "body_bytes": 1})
        link_completed_reads_to_artifact(db, action_ids=[action_id],
                                         artifact_id="artifact-own", run_id="native-discovery-run")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("DELETE FROM runtime_artifact_links_v6 WHERE action_id=?", (action_id,))


def test_exchange_lineage_failure_rolls_back_exchange_and_artifact(client):
    prepare(client, "https://native-browser.example.test", None)
    run = final_core.get_run("native-discovery-run")
    with pytest.raises(ValueError, match="not a completed request"):
        http._record_exchange(run, http.ReplayRequest(url="https://native-browser.example.test/page"),
                              {"status": 200, "headers": {}, "body_preview": "fixture",
                               "body_sha256": "a" * 64, "body_bytes": 1}, None, "manual",
                              gateway_action_id="missing-action")
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM http_exchanges").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    assert not list(http.ARTIFACT_ROOT.glob("artifact-*.json"))


def test_native_browser_request_uses_pinned_transport_and_v6_receipt(client, object_server, monkeypatch, tmp_path):
    origin, calls = object_server
    monkeypatch.setattr(native_agent, "WORKSPACE_ROOT", tmp_path / "native")
    engagement = prepare(client, origin, None)
    with final_core.connect() as db:
        run = dict(db.execute("SELECT * FROM analysis_runs WHERE id='native-discovery-run'").fetchone())
    action_ids = []
    response = native_agent._pinned_browser_get(run, final_core.get_engagement(engagement["id"]),
                                                origin + "/object", lambda: None, action_ids)
    assert response["status"] == 403 and response["process_execution"]["network_connect_denied"] is True
    assert calls == [("/object", None)]
    assert len(action_ids) == 1
    artifact_id, observation_id = native_agent._persist_browser_artifact(
        run, origin + "/object", {"status": response["status"], "title": "Fixture"}, action_ids)
    with final_core.connect() as db:
        assert db.execute("SELECT requests_used FROM run_budgets_v2 WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
        receipt = db.execute("SELECT r.*,x.task_id FROM http_gateway_receipts_v6 r "
                             "JOIN http_gateway_executions_v6 x ON x.action_id=r.action_id").fetchone()
        assert receipt["status"] == "completed" and receipt["http_status"] == 403
        assert receipt["body_sha256"] == response["body_sha256"]
        assert db.execute("SELECT status FROM agent_tasks WHERE id=?", (receipt["task_id"],)).fetchone()[0] == "succeeded"
        assert db.execute("SELECT raw_ref FROM observations WHERE id=?", (observation_id,)).fetchone()[0] == artifact_id
        assert db.execute("SELECT artifact_id FROM runtime_artifact_links_v6 WHERE action_id=?",
                          (action_ids[0],)).fetchone()[0] == artifact_id
        completed = [event for event in list_events(db) if event["event_type"] == "tool.execution.completed"]
        assert len(completed) == 1 and completed[0]["artifact_ids"] == [artifact_id]


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


def test_run_containment_revokes_grants_and_blocks_new_read_gateway(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    action = authorize_native_browser_read('native-discovery-run', engagement['id'],
                                           'https://native-browser.example.test/')
    finish_http_read(action, response={'status': 200, 'body_sha256': 'a' * 64, 'body_bytes': 1})
    response = client.post('/api/v1/v6/runs/native-discovery-run/contain', json={'reason_code': 'compromised'})
    assert response.status_code == 200 and response.json()['revoked_grants'] == 1
    assert client.post('/api/v1/v6/runs/native-discovery-run/contain',
                       json={'reason_code': 'compromised'}).json() == response.json()
    with pytest.raises(ValueError, match='denied by V6 policy'):
        authorize_native_browser_read('native-discovery-run', engagement['id'],
                                      'https://native-browser.example.test/')
    with final_core.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM http_gateway_executions_v6').fetchone()[0] == 1


def test_body_bearing_get_is_rejected_before_budget_or_gateway(client):
    engagement = prepare(client, "https://native-browser.example.test", None)
    with pytest.raises(HTTPException, match="body-bearing GET"):
        http.create_http_exchange("native-discovery-run", http.ExchangeRequestInput(
            url="https://native-browser.example.test/", method="GET", body="fixture"))
    with final_core.connect() as db:
        assert db.execute("SELECT requests_used FROM run_budgets_v2 WHERE run_id='native-discovery-run'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM http_gateway_executions_v6").fetchone()[0] == 0


def test_manual_exchange_and_replay_each_receive_read_receipt(client, object_server):
    origin, calls = object_server
    prepare(client, origin, None)
    original = http.create_http_exchange("native-discovery-run", http.ExchangeRequestInput(url=origin + "/object"))
    replay = http.replay_http_exchange(original["id"], http.ExchangeReplayInput())
    assert original["response_status"] == replay["response_status"] == 403
    assert calls == [("/object", None), ("/object", None)]
    with final_core.connect() as db:
        rows = db.execute("SELECT t.context_capsule_json,r.status FROM http_gateway_executions_v6 e "
                          "JOIN agent_tasks t ON t.id=e.task_id "
                          "JOIN http_gateway_receipts_v6 r ON r.action_id=e.action_id "
                          "ORDER BY e.started_at,e.action_id").fetchall()
        assert sorted(json.loads(row["context_capsule_json"])["source"] for row in rows) == ["manual", "replay"]
        assert all(row["status"] == "completed" for row in rows)
        links = db.execute("SELECT a.id,a.uri,a.sha256,o.id observation_id FROM runtime_artifact_links_v6 l "
                           "JOIN artifacts a ON a.id=l.artifact_id "
                           "JOIN observations o ON o.raw_ref=a.id "
                           "WHERE a.kind='http.exchange_metadata'").fetchall()
        assert len(links) == 2 and len({row['id'] for row in links}) == 2
        from pathlib import Path
        for row in links:
            data = Path(row['uri']).read_bytes()
            assert hashlib.sha256(data).hexdigest() == row['sha256']
            assert json.loads(data)['http_status'] == 403
            assert origin.encode() not in data
        events = [event for event in list_events(db, limit=500)
                  if event['event_type'] == 'tool.execution.completed']
        assert len(events) == 2 and all(len(event['artifact_ids']) == 1 for event in events)


def test_gateway_lineage_schema_upgrade_keeps_prior_records(tmp_path):
    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        for statement in V6_SCHEMA_STATEMENTS:
            if "runtime_artifact_links_v6" not in statement:
                db.execute(statement)
        db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "old-intent", "campaign", "run", "task", "agent", "principal", "claimed", "network.request",
            "read", "https://example.test", None, None, "research", 0,
            "scope", "a" * 64, "policy", "b" * 64, "proposed", "2026-10-08T00:00:00+00:00",
        ))
        db.execute("PRAGMA user_version=33")
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)["backup"]
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
        assert db.execute("SELECT resource FROM action_intents_v6 WHERE id='old-intent'").fetchone()[0] == "https://example.test"
        assert db.execute("SELECT COUNT(*) FROM http_gateway_executions_v6").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM runtime_artifact_links_v6").fetchone()[0] == 0
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 33
