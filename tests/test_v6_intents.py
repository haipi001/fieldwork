import sqlite3

import final_core
import lifecycle
from v6_schema import V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION
from tests.test_final import client, create_ready


def _setup(client):
    engagement = create_ready(client, target="https://intent.example.test")
    campaign_response = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Intent test", "objective": "Passive proposal only",
    })
    assert campaign_response.status_code == 201, campaign_response.text
    campaign = campaign_response.json()
    with final_core.connect() as db:
        owner = db.execute("SELECT current_scope_snapshot_id,current_policy_id FROM engagements_v2 WHERE id=?",
                           (engagement["id"],)).fetchone()
        db.execute("INSERT INTO analysis_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "run-intent", engagement["id"], "traditional", owner[0], owner[1],
            "queued", "target", 0, None, None, None, None, final_core.utcnow(),
        ))
    task_response = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": campaign["id"], "run_id": "run-intent", "role": "researcher",
        "objective": "Propose read", "idempotency_key": "v6-intent-task",
    })
    assert task_response.status_code == 201, task_response.text
    return engagement, task_response.json()


def _body(task_id):
    return {"task_id": task_id, "agent_id": "claimed-agent", "capability": "network.request",
            "operation": "read", "resource": "https://intent.example.test/object",
            "side_effect": False, "reason_summary": "research", "arguments_hash": "a" * 64}


def test_action_intent_is_passive_bound_and_immutable(client):
    _, task = _setup(client)
    with final_core.connect() as db:
        before = db.execute("SELECT COUNT(*) FROM v5_events").fetchone()[0]
        calls_before = db.execute("SELECT COUNT(*) FROM runtime_calls").fetchone()[0]
    response = client.post("/api/v1/v6/action-intents", json=_body(task["id"]))
    assert response.status_code == 201, response.text
    intent = response.json()
    assert intent["status"] == "proposed"
    assert intent["principal_id"] == "fieldwork:local-session"
    assert intent["principal_binding"] == "authenticated_local_session_agent_claimed"
    assert len(intent["scope_sha256"]) == len(intent["policy_sha256"]) == 64
    assert intent["side_effect"] is False
    assert client.get(f"/api/v1/v6/tasks/{task['id']}/action-intents").json()["intents"] == [intent]
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM v5_events").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM runtime_calls").fetchone()[0] == calls_before
        try:
            db.execute("UPDATE action_intents_v6 SET status='proposed' WHERE id=?", (intent["id"],))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("intent must be immutable")


def test_intent_rejects_stale_context_missing_run_and_secret_like_resource(client):
    engagement, task = _setup(client)
    body = _body(task["id"])
    assert client.post("/api/v1/v6/action-intents", json={**body, "resource":
        "https://user:password@intent.example.test/path?api_key=x"}).status_code == 409
    assert client.post("/api/v1/v6/action-intents", json={**body, "agent_id":
        "agent\nsecret"}).status_code == 409
    no_run = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": task["campaign_id"], "role": "researcher", "objective": "No run",
        "idempotency_key": "v6-no-run-task",
    }).json()
    assert client.post("/api/v1/v6/action-intents", json=_body(no_run["id"])).status_code == 409
    with final_core.connect() as db:
        db.execute("UPDATE engagements_v2 SET current_policy_id='new-policy' WHERE id=?", (engagement["id"],))
    assert client.post("/api/v1/v6/action-intents", json=body).status_code == 409
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM action_intents_v6").fetchone()[0] == 0


def test_v6_additive_migration_backup_and_restore(tmp_path):
    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE preserved(id TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO preserved VALUES('one','historical-evidence')")
        db.execute("PRAGMA user_version=28")
    result = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)
    assert result["previous_schema"] == 28 and result["target_schema"] == SCHEMA_VERSION
    backup = result["backup"]
    assert backup and lifecycle.database_integrity(tmp_path / "backups" / backup["database"])
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM preserved WHERE id='one'").fetchone()[0] == "historical-evidence"
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
    recovered = tmp_path / "recovered.db"
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as source, sqlite3.connect(recovered) as target:
        source.backup(target)
    with sqlite3.connect(recovered) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 28
        assert db.execute("SELECT value FROM preserved WHERE id='one'").fetchone()[0] == "historical-evidence"
