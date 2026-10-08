"""Deterministic V6 policy overlay; decisions do not invoke execution adapters."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Query

import final_core
from v6_capabilities import _intent_current, capability_matches

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])
RULE_ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")


@dataclass(frozen=True)
class PolicyFacts:
    identity_valid: bool
    scope_current: bool
    legacy_guard_allowed: bool | None
    capability_eligible: bool
    budget_ok: bool | None
    side_effect: bool
    external: bool
    resource_class: str = "ordinary"
    environment: str = "unknown"
    capability: str = ""
    risk_hint: float | None = None
    classifier_risk: float | None = None
    bounded: bool = True


def evaluate_policy(facts: PolicyFacts) -> tuple[str, list[str]]:
    """Return a placement-neutral decision and stable rule IDs."""
    if not facts.identity_valid:
        return "deny", ["identity.required"]
    if not facts.scope_current:
        return "deny", ["scope.current_required"]
    if facts.legacy_guard_allowed is not True:
        return "deny", ["legacy_guard.denied" if facts.legacy_guard_allowed is False else "legacy_guard.unknown"]
    if facts.resource_class == "credential":
        return "deny", ["credential.direct_access"]
    if facts.capability == "web3.sign_tx" and facts.environment == "production":
        return "deny", ["prod_wallet_sign"]
    if facts.capability in {"scope.modify", "policy.modify"}:
        return "deny", ["agent_control_change"]
    if not facts.capability_eligible:
        return "deny", ["capability.required"]
    if facts.budget_ok is not True:
        return "deny", ["budget.blocked" if facts.budget_ok is False else "budget.unknown"]
    scores = [value for value in (facts.risk_hint, facts.classifier_risk) if value is not None]
    if any(not math.isfinite(value) or value < 0 or value > 100 for value in scores):
        return "deny", ["risk.invalid"]
    risk = max(scores, default=0)
    if risk >= 80:
        return "quarantine", ["risk.quarantine"]
    if facts.side_effect and facts.external:
        return "require_approval", ["external.state_change"]
    if risk >= 50:
        return "require_approval", ["risk.approval"]
    return ("allow_with_limit", ["bounded.allow"]) if facts.bounded else ("allow", ["safe.allow"])


def record_policy_decision(db: sqlite3.Connection, *, intent_id: str, grant_id: str | None,
                           principal_id: str, legacy_guard_allowed: bool | None,
                           legacy_rule_id: str, budget_ok: bool | None,
                           resource_class: str = "ordinary", environment: str = "unknown",
                           external: bool = False, classifier_risk: float | None = None) -> dict:
    """Persist one immutable decision in the caller's transaction.

    The caller must supply the existing domain guard and budget result from a
    trusted execution boundary. No public endpoint accepts those claims.
    """
    if not db.in_transaction:
        raise ValueError("policy decision requires a caller transaction")
    if not RULE_ID.fullmatch(legacy_rule_id):
        raise ValueError("invalid legacy rule id")
    if legacy_guard_allowed not in (True, False, None) or budget_ok not in (True, False, None):
        raise ValueError("legacy guard and budget facts must be boolean or unknown")
    intent = db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (intent_id,)).fetchone()
    if not intent:
        raise ValueError("intent not found")
    eligible = False
    if grant_id:
        eligible, _ = capability_matches(db, intent_id, grant_id, principal_id=principal_id)
    facts = PolicyFacts(
        identity_valid=principal_id == intent["principal_id"],
        scope_current=_intent_current(db, intent),
        legacy_guard_allowed=legacy_guard_allowed,
        capability_eligible=eligible,
        budget_ok=budget_ok,
        side_effect=bool(intent["side_effect"]), external=external,
        resource_class=resource_class, environment=environment,
        capability=intent["capability"], risk_hint=intent["risk_hint"],
        classifier_risk=classifier_risk,
    )
    decision, reasons = evaluate_policy(facts)
    decision_id = f"policy-{uuid.uuid4().hex}"
    created_at = datetime.now(timezone.utc).isoformat()
    legacy_guard = {"allowed": legacy_guard_allowed, "rule_id": legacy_rule_id}
    db.execute("INSERT INTO policy_decisions_v6 VALUES(?,?,?,?,?,?,?,?,?,?)", (
        decision_id, intent_id, grant_id, principal_id, decision,
        json.dumps(reasons, separators=(",", ":")),
        json.dumps(legacy_guard, sort_keys=True, separators=(",", ":")),
        intent["scope_sha256"], intent["policy_sha256"], created_at,
    ))
    db.execute("INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
               "VALUES(?,?,?,?,?,?)", (
                   "control", intent["campaign_id"], decision_id, "policy.decided",
                   json.dumps({"intent_id": intent_id, "decision": decision, "reasons": reasons},
                              sort_keys=True, separators=(",", ":")), created_at,
               ))
    return {"id": decision_id, "intent_id": intent_id, "grant_id": grant_id,
            "principal_id": principal_id, "decision": decision, "reasons": reasons,
            "legacy_guard": legacy_guard, "scope_sha256": intent["scope_sha256"],
            "policy_sha256": intent["policy_sha256"], "created_at": created_at}


@router.get("/action-intents/{intent_id}/policy-decisions")
def list_policy_decisions(intent_id: str):
    with final_core.connect() as db:
        rows = db.execute("SELECT * FROM policy_decisions_v6 WHERE intent_id=? ORDER BY created_at,id",
                          (intent_id,)).fetchall()
    decisions = []
    for row in rows:
        value = dict(row)
        value["reasons"] = json.loads(value.pop("reasons_json"))
        value["legacy_guard"] = json.loads(value.pop("legacy_guard_json"))
        decisions.append(value)
    return {"decisions": decisions}


@router.get('/policy-decisions')
def policy_decision_page(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    with final_core.connect() as db:
        rows = db.execute('SELECT d.*,a.task_id,a.run_id,a.campaign_id,a.agent_id,a.capability,a.operation,a.resource '
            'FROM policy_decisions_v6 d JOIN action_intents_v6 a ON a.id=d.intent_id '
            'ORDER BY d.created_at DESC,d.id LIMIT ? OFFSET ?', (limit + 1, offset)).fetchall()
    items = []
    for row in rows[:limit]:
        item = dict(row)
        item['reasons'] = json.loads(item.pop('reasons_json'))
        item['legacy_guard'] = json.loads(item.pop('legacy_guard_json'))
        items.append(item)
    return {'decisions': items, 'has_more': len(rows) > limit, 'offset': offset}
