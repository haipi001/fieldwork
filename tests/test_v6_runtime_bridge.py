import sqlite3

import pytest

from v6_runtime_bridge import list_agent_identities, list_events, list_model_call_snapshots, list_runners
from tests.test_final import client


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE agent_tasks(id TEXT,campaign_id TEXT,run_id TEXT,group_id TEXT,role TEXT,lease_owner TEXT);
        CREATE TABLE runtime_route_decisions(id TEXT,task_id TEXT,campaign_id TEXT);
        CREATE TABLE v5_events(id INTEGER PRIMARY KEY,topic TEXT,campaign_id TEXT,entity_id TEXT,
                               event_type TEXT,payload_json TEXT,created_at TEXT);
        CREATE TABLE runner_registry_v5(id TEXT,kind TEXT,capabilities_json TEXT,labels_json TEXT,
                                        max_concurrency INTEGER,status TEXT,heartbeat_at TEXT);
        CREATE TABLE agent_registry_v5(id TEXT,model TEXT,runtime TEXT);
        CREATE TABLE runtime_calls(id TEXT,decision_id TEXT,task_id TEXT,attempt INTEGER,runner_id TEXT,
            campaign_id TEXT,group_id TEXT,profile_id TEXT,provider_id TEXT,reserved_tokens INTEGER,
            reserved_cost_micros INTEGER,max_runtime_ms INTEGER,state TEXT,created_at TEXT,updated_at TEXT);
    """)
    yield connection
    connection.close()


def test_event_bridge_correlates_task_route_and_redacts_legacy_payload(db):
    db.execute("INSERT INTO agent_tasks VALUES('task-1','campaign-1','run-1','group-1','researcher',NULL)")
    db.execute("INSERT INTO runtime_route_decisions VALUES('route-1','task-1','campaign-1')")
    db.executemany("INSERT INTO v5_events VALUES(?,?,?,?,?,?,?)", [
        (1, "orchestration", "campaign-1", "task-1", "task.leased",
         '{"runner_id":"local-1","api_key":"never-expose"}', "2026-10-08T00:00:00Z"),
        (2, "runtime", "campaign-1", "route-1", "route.decided",
         '{"provider_id":"provider-1","secret":"never-expose"}', "2026-10-08T00:00:01Z"),
    ])
    events = list_events(db)
    assert [event["event_id"] for event in events] == ["v5:1", "v5:2"]
    assert events[0]["task_id"] == events[1]["task_id"] == "task-1"
    assert events[0]["group_id"] == events[1]["group_id"] == "group-1"
    assert events[0]["runner_id"] == "local-1"
    assert events[0]["principal_id"] is None and events[0]["signature_ref"] is None
    assert "never-expose" not in str(events)
    assert [event["event_id"] for event in list_events(db, after_id=1)] == ["v5:2"]


def test_event_bridge_rejects_conflicting_ownership(db):
    db.execute("INSERT INTO agent_tasks VALUES('task-1','campaign-1',NULL,NULL,'researcher',NULL)")
    db.execute("INSERT INTO v5_events VALUES(1,'orchestration','campaign-2','task-1',"
               "'task.leased','{}','2026-10-08T00:00:00Z')")
    with pytest.raises(ValueError, match="campaign conflicts"):
        list_events(db)


def test_runner_view_does_not_treat_advertisement_as_authorization(db):
    db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?)", (
        "local-1", "local-structured", '["model.call","model.call","http.read"]',
        '{"location":"local","secret_ref":"hidden"}', 2, "online", None,
    ))
    runner = list_runners(db)[0]
    assert runner["kind"] == "local"
    assert runner["capabilities"] == ["http.read", "model.call"]
    assert runner["attestation"] is None
    assert "hidden" not in str(runner)
    db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?)", (
        "worker-1", "worker", '[]', '{}', 1, "online", None,
    ))
    assert {item["kind"] for item in list_runners(db)} == {"local"}
    for kind in ("research-worker", "native-discovery", "isolated-verifier"):
        db.execute("UPDATE runner_registry_v5 SET kind=? WHERE id='worker-1'", (kind,))
        assert {item["kind"] for item in list_runners(db)} == {"local"}
    db.execute("UPDATE runner_registry_v5 SET kind='unsupported' WHERE id='worker-1'")
    with pytest.raises(ValueError, match="placement kind is unknown"):
        list_runners(db)


def test_call_snapshot_and_agent_identity_do_not_invent_provenance(db):
    db.execute("INSERT INTO agent_tasks VALUES('task-1','campaign-1','run-1','group-1','researcher',NULL)")
    db.execute("INSERT INTO runtime_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "call-1", "route-1", "task-1", 1, "runner-1", "campaign-1", "group-1",
        "profile-1", "provider-1", 100, 1000, 5000, "unknown", "t1", "t2",
    ))
    db.execute("INSERT INTO agent_registry_v5 VALUES('agent-observed','model-x','local')")
    db.execute("INSERT INTO v5_events VALUES(1,'runtime','campaign-1','call-1','call.reserved',"
               "'{\"task_id\":\"task-1\",\"token\":\"hidden\"}','t1')")
    call = list_model_call_snapshots(db, task_id="task-1")[0]
    event = list_events(db)[0]
    identity = list_agent_identities(db)[0]
    assert call["state"] == "unknown" and call["runner_id"] == "runner-1"
    assert call["agent_id"] is None and call["principal_id"] is None
    assert event["task_id"] == "task-1" and event["runner_id"] == "runner-1"
    assert "hidden" not in str(event)
    assert identity["agent_id"] == "agent-observed" and identity["attestation"] is None
    db.execute("UPDATE runtime_calls SET campaign_id='other' WHERE id='call-1'")
    with pytest.raises(ValueError, match="ownership conflicts"):
        list_model_call_snapshots(db, task_id="task-1")


def test_read_only_api_paginates_and_never_returns_raw_event_payload(client):
    import final_core

    with final_core.connect() as connection:
        connection.execute("INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
                           "VALUES(?,?,?,?,?,?)", ("runtime", "campaign-1", "route-1", "route.decided",
                           '{"api_key":"hidden-value"}', "2026-10-08T00:00:00Z"))
        connection.commit()
        event_id = connection.execute("SELECT max(id) FROM v5_events").fetchone()[0]
    response = client.get("/api/v1/v6/campaigns/campaign-1/runtime-events", params={"limit": 1})
    assert response.status_code == 200
    events = response.json()["events"]
    assert len(events) == 1 and events[0]["event_id"] == f"v5:{event_id}"
    assert "hidden-value" not in response.text
    assert client.get("/api/v1/v6/campaigns/campaign-2/runtime-events").json()["events"] == []
    assert client.get("/api/v1/v6/campaigns/campaign-1/runtime-events", params={"limit": 501}).status_code == 422
