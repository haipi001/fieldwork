import sqlite3

import final_core
import lifecycle
from tests.test_final import client
from tests.test_v6_capabilities import _grant, _intent
from tests.test_v6_intents import _setup
from v6_policy import PolicyFacts, evaluate_policy, record_policy_decision
from v6_runtime_bridge import list_events
from v6_schema import V6_SCHEMA_VERSION, apply_v6_schema
from version import SCHEMA_VERSION


def _facts(**changes):
    values = dict(identity_valid=True, scope_current=True, legacy_guard_allowed=True,
                  capability_eligible=True, budget_ok=True, side_effect=False, external=False,
                  capability="network.request")
    values.update(changes)
    return PolicyFacts(**values)


def test_deterministic_precedence_and_advisory_can_only_tighten():
    assert evaluate_policy(_facts())[0] == "allow_with_limit"
    assert evaluate_policy(_facts(classifier_risk=65))[0] == "require_approval"
    assert evaluate_policy(_facts(classifier_risk=85))[0] == "quarantine"
    assert evaluate_policy(_facts(scope_current=False, classifier_risk=85)) == ("deny", ["scope.current_required"])
    assert evaluate_policy(_facts(legacy_guard_allowed=False, classifier_risk=85)) == ("deny", ["legacy_guard.denied"])
    assert evaluate_policy(_facts(legacy_guard_allowed=None))[0] == "deny"
    assert evaluate_policy(_facts(capability_eligible=False))[0] == "deny"
    assert evaluate_policy(_facts(budget_ok=None))[0] == "deny"
    assert evaluate_policy(_facts(resource_class="credential", classifier_risk=85)) == (
        "deny", ["credential.direct_access"])
    assert evaluate_policy(_facts(capability="web3.sign_tx", environment="production"))[0] == "deny"
    assert evaluate_policy(_facts(side_effect=True, external=True))[0] == "require_approval"


def test_persisted_decision_is_audited_immutable_and_does_not_execute(client):
    _, task = _setup(client)
    intent = _intent(client, task["id"])
    grant = _grant(client, intent["id"])
    with final_core.connect() as db:
        before_calls = db.execute("SELECT COUNT(*) FROM runtime_calls").fetchone()[0]
        db.execute("BEGIN IMMEDIATE")
        decision = record_policy_decision(
            db, intent_id=intent["id"], grant_id=grant["id"],
            principal_id=intent["principal_id"], legacy_guard_allowed=True,
            legacy_rule_id="traditional.read_guard", budget_ok=True,
        )
    assert decision["decision"] == "allow_with_limit"
    listed = client.get(f"/api/v1/v6/action-intents/{intent['id']}/policy-decisions")
    assert listed.status_code == 200 and listed.json()["decisions"] == [decision]
    with final_core.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_calls").fetchone()[0] == before_calls
        event = db.execute("SELECT event_type,payload_json FROM v5_events WHERE entity_id=?",
                           (decision["id"],)).fetchone()
        assert event["event_type"] == "policy.decided"
        assert "api_key" not in event["payload_json"]
        normalized = next(item for item in list_events(db, limit=500)
                          if item["event_type"] == "policy.decided")
        assert normalized["task_id"] == task["id"]
        try:
            db.execute("DELETE FROM policy_decisions_v6 WHERE id=?", (decision["id"],))
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("policy decision must be immutable")


def test_missing_grant_wrong_principal_and_unknown_guard_fail_closed(client):
    _, task = _setup(client)
    intent = _intent(client, task["id"])
    grant = _grant(client, intent["id"])
    with final_core.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        missing = record_policy_decision(db, intent_id=intent["id"], grant_id=None,
                                         principal_id=intent["principal_id"], legacy_guard_allowed=True,
                                         legacy_rule_id="traditional.read_guard", budget_ok=True)
        wrong = record_policy_decision(db, intent_id=intent["id"], grant_id=grant["id"],
                                       principal_id="other-principal", legacy_guard_allowed=True,
                                       legacy_rule_id="traditional.read_guard", budget_ok=True)
        unknown = record_policy_decision(db, intent_id=intent["id"], grant_id=grant["id"],
                                         principal_id=intent["principal_id"], legacy_guard_allowed=None,
                                         legacy_rule_id="traditional.read_guard", budget_ok=True)
    assert missing["reasons"] == ["capability.required"]
    assert wrong["reasons"] == ["identity.required"]
    assert unknown["reasons"] == ["legacy_guard.unknown"]


def test_policy_schema_upgrade_from_30_preserves_backup(tmp_path):
    database = tmp_path / "fieldwork.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE preserved(id TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO preserved VALUES('one','prior-proof')")
        db.execute("PRAGMA user_version=30")
    backup = lifecycle.prepare_database_upgrade(database, tmp_path / "backups", tmp_path)["backup"]
    apply_v6_schema(database)
    lifecycle.finalize_database_version(database)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT value FROM v6_schema_meta WHERE key='schema_version'").fetchone()[0] == str(V6_SCHEMA_VERSION)
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "prior-proof"
    with sqlite3.connect(tmp_path / "backups" / backup["database"]) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA user_version").fetchone()[0] == 30
        assert db.execute("SELECT value FROM preserved").fetchone()[0] == "prior-proof"
