"""First-party research model gateway over the existing runtime call ledger."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from v6_capabilities import _intent_current, capability_matches
from v6_intents import _sha256
from v6_policy import record_policy_decision

RESEARCH_PRINCIPAL = "fieldwork:research-worker"
RESEARCH_AGENT = "fieldwork:research-agent-v1"


def _load_object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def bind_research_call(db: sqlite3.Connection, *, call_id: str, task: sqlite3.Row,
                       route: sqlite3.Row, runner_id: str) -> None:
    """Capture trusted research intent/grant with the original call reservation."""
    if not db.in_transaction:
        raise ValueError("gateway binding requires the reservation transaction")
    capsule = _load_object(task["context_capsule_json"])
    runner = db.execute("SELECT kind,metadata_json FROM runner_registry_v5 WHERE id=?", (runner_id,)).fetchone()
    call = db.execute("SELECT * FROM runtime_calls WHERE id=?", (call_id,)).fetchone()
    if (not capsule.get("team_plan") or not task["run_id"] or not runner
            or runner["kind"] != "research-worker" or not runner_id.startswith("builtin-research-")
            or _load_object(runner["metadata_json"]).get("builtin") is not True
            or not call or call["task_id"] != task["id"] or call["runner_id"] != runner_id
            or route["id"] != call["decision_id"] or route["status"] != "selected"):
        raise ValueError("research model gateway identity or reservation is invalid")
    from v5_orchestration import _continuous_scope_current
    if not _continuous_scope_current(db, task):
        raise ValueError("research model scope or input context is stale")
    authority = db.execute("""
        SELECT e.current_scope_snapshot_id,e.current_policy_id,s.rules,p.policy,
               r.scope_snapshot_id run_scope_id,r.policy_id run_policy_id
        FROM research_campaigns c JOIN engagements_v2 e ON e.id=c.engagement_id
        JOIN analysis_runs r ON r.id=? AND r.engagement_id=e.id
        JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
        JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id
        WHERE c.id=?
    """, (task["run_id"], task["campaign_id"])).fetchone()
    if (not authority or authority["current_scope_snapshot_id"] != authority["run_scope_id"]
            or authority["current_policy_id"] != authority["run_policy_id"]
            or capsule.get("scope_snapshot_id") != authority["current_scope_snapshot_id"]
            or capsule.get("policy_id") != authority["current_policy_id"]):
        raise ValueError("research model Run scope or policy mismatch")
    now = datetime.now(timezone.utc).isoformat()
    intent_id, grant_id = f"intent-{uuid.uuid4().hex}", f"grant-{uuid.uuid4().hex}"
    arguments_hash = hashlib.sha256((route["id"] + "|" + route["request_json"]).encode()).hexdigest()
    db.execute("INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        intent_id, task["campaign_id"], task["run_id"], task["id"], RESEARCH_AGENT,
        RESEARCH_PRINCIPAL, "first_party_research_worker_code_path", "model.call", "call",
        f"route:{route['id']}", arguments_hash, None, "research", 0,
        authority["current_scope_snapshot_id"], _sha256(authority["rules"]),
        authority["current_policy_id"], _sha256(authority["policy"]), "proposed", now,
    ))
    intent = db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (intent_id,)).fetchone()
    if not _intent_current(db, intent):
        raise ValueError("research model intent context is stale")
    constraints = json.dumps({"operation": "call", "side_effect": False}, sort_keys=True, separators=(",", ":"))
    db.execute("INSERT INTO capability_grants_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        grant_id, intent_id, RESEARCH_PRINCIPAL, "model.call", "call", intent["resource"],
        task["campaign_id"], task["run_id"], task["id"], intent["scope_snapshot_id"],
        intent["scope_sha256"], intent["policy_id"], intent["policy_sha256"],
        constraints, 1, "active", "fieldwork:control-plane", now, call["deadline_at"],
    ))
    eligible, reason = capability_matches(db, intent_id, grant_id, principal_id=RESEARCH_PRINCIPAL)
    if not eligible:
        raise ValueError(f"research model grant rejected: {reason}")
    db.execute("INSERT INTO model_gateway_bindings_v6 VALUES(?,?,?,?,?,?,?,?)", (
        call_id, intent_id, grant_id, RESEARCH_PRINCIPAL, RESEARCH_AGENT,
        runner_id, route["id"], now,
    ))


def authorize_research_start(db: sqlite3.Connection, call: sqlite3.Row,
                             task: sqlite3.Row) -> tuple[bool, str]:
    """Decide and consume the grant before the call enters `calling`."""
    binding = db.execute("SELECT * FROM model_gateway_bindings_v6 WHERE call_id=?", (call["id"],)).fetchone()
    if not binding:
        return False, "model gateway binding missing"
    if (binding["runner_id"] != call["runner_id"] or binding["route_decision_id"] != call["decision_id"]
            or binding["principal_id"] != RESEARCH_PRINCIPAL or binding["agent_id"] != RESEARCH_AGENT):
        return False, "model gateway binding mismatch"
    from v5_orchestration import _continuous_scope_current
    legacy_allowed = _continuous_scope_current(db, task)
    result = record_policy_decision(
        db, intent_id=binding["intent_id"], grant_id=binding["grant_id"],
        principal_id=RESEARCH_PRINCIPAL, legacy_guard_allowed=legacy_allowed,
        legacy_rule_id="v5.research_model_guard", budget_ok=True,
    )
    if result["decision"] not in {"allow", "allow_with_limit"}:
        return False, result["reasons"][0]
    use_id = f"grant-use-{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT INTO capability_uses_v6 VALUES(?,?,?,?,?)", (
        use_id, binding["grant_id"], binding["intent_id"], call["id"], now,
    ))
    db.execute("INSERT INTO model_gateway_starts_v6 VALUES(?,?,?,?)", (
        call["id"], result["id"], use_id, now,
    ))
    return True, result["id"]
