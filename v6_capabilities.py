"""Bounded V6 capability grants; eligibility is not execution authorization."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import final_core
from v6_intents import LOCAL_SESSION_PRINCIPAL, RESOURCE_RE, _sha256

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])
SAFE_READ_CAPABILITIES = {"browser.navigate", "network.request"}
SAFE_READ_OPERATIONS = {"read", "get", "navigate"}


class GrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int = Field(ge=1, le=3600)
    max_uses: int = Field(default=1, ge=1, le=100)
    resource_pattern: str | None = Field(default=None, min_length=1, max_length=257)


class RevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: Literal["operator_revoked", "scope_changed", "policy_changed", "compromised"]


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def _pattern_valid(pattern: str) -> bool:
    if pattern.endswith("/*"):
        return bool(RESOURCE_RE.fullmatch(pattern[:-1]))
    return bool(RESOURCE_RE.fullmatch(pattern))


def _resource_matches(pattern: str, resource: str) -> bool:
    if pattern.endswith("/*"):
        return resource.startswith(pattern[:-1]) and len(resource) > len(pattern) - 1
    return pattern == resource


def _intent_current(db: sqlite3.Connection, intent: sqlite3.Row) -> bool:
    row = db.execute("""
        SELECT t.status task_status,t.run_id,t.context_capsule_json,c.status campaign_status,
               e.status engagement_status,e.current_scope_snapshot_id,e.current_policy_id,
               r.status run_status,r.scope_snapshot_id run_scope_id,r.policy_id run_policy_id,
               s.confirmed_at,s.rules,p.policy
        FROM agent_tasks t
        JOIN research_campaigns c ON c.id=t.campaign_id
        JOIN engagements_v2 e ON e.id=c.engagement_id
        JOIN analysis_runs r ON r.id=t.run_id AND r.engagement_id=e.id
        JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
        JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id
        WHERE t.id=? AND t.campaign_id=?
    """, (intent["task_id"], intent["campaign_id"])).fetchone()
    try:
        team_plan = bool(row and json.loads(row["context_capsule_json"]).get("team_plan"))
    except (TypeError, ValueError, AttributeError):
        team_plan = False
    return bool(row and row["run_id"] == intent["run_id"]
                and row["task_status"] in {"queued", "leased", "running"}
                and row["campaign_status"] == "active" and row["engagement_status"] == "ready"
                and row["run_status"] in ({"queued", "running", "paused", "completed"} if team_plan else {"queued", "running"})
                and row["confirmed_at"]
                and row["current_scope_snapshot_id"] == row["run_scope_id"] == intent["scope_snapshot_id"]
                and row["current_policy_id"] == row["run_policy_id"] == intent["policy_id"]
                and _sha256(row["rules"]) == intent["scope_sha256"]
                and _sha256(row["policy"]) == intent["policy_sha256"])


def _scope_allows_safe_read(db: sqlite3.Connection, intent: sqlite3.Row) -> bool:
    if intent["capability"] not in SAFE_READ_CAPABILITIES or intent["side_effect"]:
        return False
    row = db.execute("SELECT s.rules,p.policy FROM scope_snapshots s JOIN execution_policies p "
                     "ON p.engagement_id=s.engagement_id WHERE s.id=? AND p.id=?",
                     (intent["scope_snapshot_id"], intent["policy_id"])).fetchone()
    if not row:
        return False
    try:
        scope, policy = json.loads(row["rules"]), json.loads(row["policy"])
    except (TypeError, ValueError):
        return False
    actions = scope.get("allowed_actions") if isinstance(scope, dict) else None
    allowed_targets = scope.get("allowed_targets") if isinstance(scope, dict) else None
    denied_targets = scope.get("denied_targets") if isinstance(scope, dict) else None
    if (not isinstance(scope, dict) or not isinstance(policy, dict)
            or not isinstance(actions, list) or "read" not in actions
            or not isinstance(allowed_targets, list) or not isinstance(denied_targets, list)
            or policy.get("network_mode") not in {"scoped", "read_only"}):
        return False
    parsed = urlparse(intent["resource"])
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or any(segment in {".", ".."} for segment in parsed.path.split("/"))):
        return False
    target = intent["resource"]
    allowed = any(isinstance(item, str) and (target == item or target.startswith(item.rstrip("/") + "/"))
                  for item in allowed_targets)
    denied = any(isinstance(item, str) and (target == item or target.startswith(item.rstrip("/") + "/"))
                 for item in denied_targets)
    return allowed and not denied


def _model_route_current(db: sqlite3.Connection, intent: sqlite3.Row) -> bool:
    if (intent["capability"] != "model.call" or intent["operation"] != "call"
            or intent["side_effect"] or not intent["resource"].startswith("route:")):
        return False
    decision_id = intent["resource"][len("route:"):]
    route = db.execute("SELECT * FROM runtime_route_decisions WHERE id=? AND task_id=? AND campaign_id=?",
                       (decision_id, intent["task_id"], intent["campaign_id"])).fetchone()
    return bool(route and route["status"] == "selected" and route["provider_id"])


def _domain_guard_eligible(db: sqlite3.Connection, intent: sqlite3.Row) -> bool:
    if intent["capability"] == "model.call":
        return _model_route_current(db, intent)
    return _scope_allows_safe_read(db, intent)


def _view(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["constraints"] = json.loads(value.pop("constraints_json"))
    return value


def issue_grant(db: sqlite3.Connection, intent_id: str, request: GrantRequest) -> dict:
    db.execute("BEGIN IMMEDIATE")
    intent = db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (intent_id,)).fetchone()
    if not intent or not _intent_current(db, intent):
        raise ValueError("intent context is missing or stale")
    pattern = request.resource_pattern or intent["resource"]
    if not _pattern_valid(pattern) or not _resource_matches(pattern, intent["resource"]):
        raise ValueError("resource pattern does not safely match the intent")
    active = bool(pattern == intent["resource"] and not intent["side_effect"]
                  and intent["principal_id"] == LOCAL_SESSION_PRINCIPAL
                  and intent["capability"] in SAFE_READ_CAPABILITIES
                  and intent["operation"] in SAFE_READ_OPERATIONS
                  and _domain_guard_eligible(db, intent))
    now = datetime.now(timezone.utc)
    grant_id = f"grant-{uuid.uuid4().hex}"
    constraints = {"operation": intent["operation"], "side_effect": bool(intent["side_effect"])}
    db.execute("INSERT INTO capability_grants_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        grant_id, intent_id, intent["principal_id"], intent["capability"], intent["operation"], pattern,
        intent["campaign_id"], intent["run_id"], intent["task_id"], intent["scope_snapshot_id"],
        intent["scope_sha256"], intent["policy_id"], intent["policy_sha256"],
        json.dumps(constraints, sort_keys=True, separators=(",", ":")), request.max_uses,
        "active" if active else "pending_approval", LOCAL_SESSION_PRINCIPAL,
        now.isoformat(), (now + timedelta(seconds=request.ttl_seconds)).isoformat(),
    ))
    return _view(db.execute("SELECT * FROM capability_grants_v6 WHERE id=?", (grant_id,)).fetchone())


def capability_matches(db: sqlite3.Connection, intent_id: str, grant_id: str,
                       *, principal_id: str, at: datetime | None = None) -> tuple[bool, str]:
    intent = db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (intent_id,)).fetchone()
    grant = db.execute("SELECT * FROM capability_grants_v6 WHERE id=?", (grant_id,)).fetchone()
    if not intent or not grant:
        return False, "missing_intent_or_grant"
    if grant["intent_id"] != intent_id:
        return False, "wrong_intent"
    if principal_id != intent["principal_id"] or principal_id != grant["principal_id"]:
        return False, "wrong_principal"
    if not _intent_current(db, intent):
        return False, "stale_context"
    if not _domain_guard_eligible(db, intent):
        return False, "scope_or_domain_guard"
    if grant["approval_state"] != "active":
        return False, "approval_required"
    now = at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.astimezone(timezone.utc) >= _utc(grant["expires_at"]):
        return False, "expired"
    if db.execute("SELECT 1 FROM capability_revocations_v6 WHERE grant_id=?", (grant_id,)).fetchone():
        return False, "revoked"
    for key in ("principal_id", "capability", "operation", "campaign_id", "run_id", "task_id",
                "scope_snapshot_id", "scope_sha256", "policy_id", "policy_sha256"):
        if grant[key] != intent[key]:
            return False, f"mismatched_{key}"
    if not _resource_matches(grant["resource_pattern"], intent["resource"]):
        return False, "resource_mismatch"
    constraints = json.loads(grant["constraints_json"])
    if constraints != {"operation": intent["operation"], "side_effect": bool(intent["side_effect"])}:
        return False, "constraint_mismatch"
    used = db.execute("SELECT COUNT(*) FROM capability_uses_v6 WHERE grant_id=?", (grant_id,)).fetchone()[0]
    if used >= grant["max_uses"]:
        return False, "usage_exhausted"
    return True, "eligible_only"


def revoke_grant(db: sqlite3.Connection, grant_id: str, reason_code: str) -> dict:
    db.execute("BEGIN IMMEDIATE")
    if not db.execute("SELECT 1 FROM capability_grants_v6 WHERE id=?", (grant_id,)).fetchone():
        raise ValueError("grant not found")
    now = datetime.now(timezone.utc).isoformat()
    db.execute("INSERT OR IGNORE INTO capability_revocations_v6 VALUES(?,?,?,?)",
               (grant_id, LOCAL_SESSION_PRINCIPAL, now, reason_code))
    return dict(db.execute("SELECT * FROM capability_revocations_v6 WHERE grant_id=?", (grant_id,)).fetchone())


@router.post("/action-intents/{intent_id}/capability-grants", status_code=201)
def create_capability_grant(intent_id: str, body: GrantRequest):
    try:
        with final_core.connect() as db:
            return issue_grant(db, intent_id, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/action-intents/{intent_id}/capability-grants")
def list_capability_grants(intent_id: str):
    with final_core.connect() as db:
        rows = db.execute("SELECT * FROM capability_grants_v6 WHERE intent_id=? ORDER BY issued_at,id", (intent_id,))
        return {"grants": [_view(row) for row in rows]}


@router.post("/capability-grants/{grant_id}/revoke")
def revoke_capability_grant(grant_id: str, body: RevokeRequest):
    try:
        with final_core.connect() as db:
            return revoke_grant(db, grant_id, body.reason_code)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
