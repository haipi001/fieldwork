import sqlite3
from datetime import datetime, timedelta, timezone

import final_core
import lifecycle
from tests.test_final import client
from tests.test_v6_intents import _body, _setup
from v6_capabilities import capability_matches
from v6_schema import V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION


def _intent(client, task_id, **changes):
    response = client.post("/api/v1/v6/action-intents", json={**_body(task_id), **changes})
    assert response.status_code == 201, response.text
    return response.json()


def _grant(client, intent_id, **changes):
    response = client.post(f"/api/v1/v6/action-intents/{intent_id}/capability-grants",
                           json={"ttl_seconds": 120, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def test_grant_is_exact_principal_bound_expiring_and_revocable(client):
    _, task = _setup(client)
    intent = _intent(client, task["id"])
    grant = _grant(client, intent["id"])
    assert grant["approval_state"] == "active"
    assert grant["resource_pattern"] == intent["resource"]
    with final_core.connect() as db:
        assert capability_matches(db, intent["id"], grant["id"],
                                  principal_id=intent["principal_id"]) == (True, "eligible_only")
        assert capability_matches(db, intent["id"], grant["id"],
                                  principal_id="different-principal") == (False, "wrong_principal")
        later = datetime.now(timezone.utc) + timedelta(hours=1)
        assert capability_matches(db, intent["id"], grant["id"],
                                  principal_id=intent["principal_id"], at=later) == (False, "expired")
        try:
            db.execute("UPDATE capability_grants_v6 SET approval_state='active' WHERE id=?", (grant["id"],))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("grant must be immutable")
    revoked = client.post(f"/api/v1/v6/capability-grants/{grant['id']}/revoke",
                          json={"reason_code": "operator_revoked"})
    assert revoked.status_code == 200
    assert client.post(f"/api/v1/v6/capability-grants/{grant['id']}/revoke",
                       json={"reason_code": "compromised"}).json() == revoked.json()
    with final_core.connect() as db:
        assert capability_matches(db, intent["id"], grant["id"],
                                  principal_id=intent["principal_id"]) == (False, "revoked")
        assert db.execute("SELECT COUNT(*) FROM runtime_calls").fetchone()[0] == 0


def test_legacy_tool_grants_and_runner_advertisement_never_widen_grant(client):
    _, task = _setup(client)
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET tool_grants_json='[\"email.send\",\"network.request\"]' WHERE id=?",
                   (task["id"],))
        db.execute("INSERT INTO runner_registry_v5 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            "advertising-runner", "Advertiser", "worker", "online", '["email.send"]', '{}',
            1, 0, None, '{}', final_core.utcnow(), final_core.utcnow(),
        ))
    send_intent = _intent(client, task["id"], capability="email.send", operation="send",
                          resource="mail:recipient", side_effect=True)
    send_grant = _grant(client, send_intent["id"])
    assert send_grant["approval_state"] == "pending_approval"
    read_intent = _intent(client, task["id"], capability="network.request", operation="read")
    read_grant = _grant(client, read_intent["id"])
    with final_core.connect() as db:
        assert capability_matches(db, send_intent["id"], send_grant["id"],
                                  principal_id=send_intent["principal_id"]) == (False, "scope_or_domain_guard")
        assert capability_matches(db, send_intent["id"], read_grant["id"],
                                  principal_id=send_intent["principal_id"]) == (False, "wrong_intent")
        assert capability_matches(db, read_intent["id"], read_grant["id"],
                                  principal_id=read_intent["principal_id"]) == (True, "eligible_only")


def test_wildcard_pending_one_shot_exhaustion_and_stale_scope(client):
    engagement, task = _setup(client)
    intent = _intent(client, task["id"])
    wildcard = _grant(client, intent["id"], resource_pattern="https://intent.example.test/*")
    assert wildcard["approval_state"] == "pending_approval"
    exact = _grant(client, intent["id"], max_uses=1)
    with final_core.connect() as db:
        db.execute("INSERT INTO capability_uses_v6 VALUES(?,?,?,?,?)", (
            "use-1", exact["id"], intent["id"], "action-1", final_core.utcnow(),
        ))
        assert capability_matches(db, intent["id"], exact["id"],
                                  principal_id=intent["principal_id"]) == (False, "usage_exhausted")
        db.execute("UPDATE engagements_v2 SET current_scope_snapshot_id='new-scope' WHERE id=?",
                   (engagement["id"],))
    with final_core.connect() as db:
        assert capability_matches(db, intent["id"], wildcard["id"],
                                  principal_id=intent["principal_id"]) == (False, "stale_context")
    rejected = client.post(f"/api/v1/v6/action-intents/{intent['id']}/capability-grants",
                           json={"ttl_seconds": 120})
    assert rejected.status_code == 409


def test_out_of_scope_read_stays_pending_even_with_matching_legacy_grant(client):
    _, task = _setup(client)
    with final_core.connect() as db:
        db.execute("UPDATE agent_tasks SET tool_grants_json='[\"network.request\"]' WHERE id=?", (task["id"],))
    intent = _intent(client, task["id"], resource="https://outside.example.test/object")
    grant = _grant(client, intent["id"])
    assert grant["approval_state"] == "pending_approval"
    with final_core.connect() as db:
        assert capability_matches(db, intent["id"], grant["id"],
                                  principal_id=intent["principal_id"]) == (False, "scope_or_domain_guard")


def test_v6_grant_migration_preserves_schema_29_and_recovery(tmp_path):
    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE preserved(id TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO preserved VALUES('one','old-row')")
        db.execute("PRAGMA user_version=29")
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)["backup"]
    assert backup and lifecycle.database_integrity(tmp_path / "backups" / backup["database"])
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "old-row"
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 29
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "old-row"
