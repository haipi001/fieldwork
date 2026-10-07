"""Passive ActionIntent capture. A proposal never authorizes execution."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

import final_core

router = APIRouter(prefix="/api/v1/v6", tags=["V6 Control Plane"])
LOCAL_SESSION_PRINCIPAL = "fieldwork:local-session"
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ActionIntentProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=3, max_length=80)
    operation: str = Field(min_length=1, max_length=80)
    resource: str = Field(min_length=1, max_length=256)
    arguments_hash: str | None = None
    side_effect: bool
    risk_hint: float | None = Field(default=None, ge=0, le=100)
    reason_summary: Literal["research", "verification", "investigation", "maintenance"]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _view(row: sqlite3.Row) -> dict:
    value = dict(row)
    value["side_effect"] = bool(value["side_effect"])
    return value


def propose_intent(db: sqlite3.Connection, proposal: ActionIntentProposal) -> dict:
    if not IDENTIFIER_RE.fullmatch(proposal.agent_id):
        raise ValueError("agent_id must be an opaque identifier")
    if not CAPABILITY_RE.fullmatch(proposal.capability):
        raise ValueError("invalid capability name")
    if not RESOURCE_RE.fullmatch(proposal.resource):
        raise ValueError("resource must be a metadata-only identifier without query or credentials")
    if not re.fullmatch(r"[a-z][a-z0-9_.-]*", proposal.operation):
        raise ValueError("invalid operation name")
    if proposal.arguments_hash is not None and not SHA256_RE.fullmatch(proposal.arguments_hash):
        raise ValueError("arguments_hash must be lowercase SHA256")
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("""
        SELECT t.id task_id,t.campaign_id,t.run_id,t.status task_status,
               c.status campaign_status,e.status engagement_status,
               e.current_scope_snapshot_id,e.current_policy_id,
               r.status run_status,r.scope_snapshot_id run_scope_id,r.policy_id run_policy_id,
               s.rules scope_rules,s.confirmed_at,p.policy policy_json
        FROM agent_tasks t
        JOIN research_campaigns c ON c.id=t.campaign_id
        JOIN engagements_v2 e ON e.id=c.engagement_id
        JOIN analysis_runs r ON r.id=t.run_id AND r.engagement_id=e.id
        JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id AND s.engagement_id=e.id
        JOIN execution_policies p ON p.id=e.current_policy_id AND p.engagement_id=e.id
        WHERE t.id=?
    """, (proposal.task_id,)).fetchone()
    if not row or not row["run_id"]:
        raise ValueError("intent requires a task with a valid Run")
    if (row["task_status"] not in {"queued", "leased", "running"}
            or row["campaign_status"] != "active" or row["engagement_status"] != "ready"
            or row["run_status"] not in {"queued", "running"} or not row["confirmed_at"]):
        raise ValueError("intent context is not active and confirmed")
    if (row["current_scope_snapshot_id"] != row["run_scope_id"]
            or row["current_policy_id"] != row["run_policy_id"]):
        raise ValueError("intent scope or policy is stale")
    intent_id = f"intent-{uuid.uuid4().hex}"
    created_at = datetime.now(timezone.utc).isoformat()
    db.execute("""INSERT INTO action_intents_v6 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        intent_id, row["campaign_id"], row["run_id"], row["task_id"],
        proposal.agent_id, LOCAL_SESSION_PRINCIPAL, "authenticated_local_session_agent_claimed",
        proposal.capability, proposal.operation, proposal.resource, proposal.arguments_hash,
        proposal.risk_hint, proposal.reason_summary, int(proposal.side_effect),
        row["current_scope_snapshot_id"], _sha256(row["scope_rules"]),
        row["current_policy_id"], _sha256(row["policy_json"]), "proposed", created_at,
    ))
    return _view(db.execute("SELECT * FROM action_intents_v6 WHERE id=?", (intent_id,)).fetchone())


@router.post("/action-intents", status_code=201)
def create_action_intent(body: ActionIntentProposal):
    try:
        with final_core.connect() as db:
            return propose_intent(db, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks/{task_id}/action-intents")
def task_action_intents(task_id: str):
    with final_core.connect() as db:
        rows = db.execute("SELECT * FROM action_intents_v6 WHERE task_id=? ORDER BY created_at,id", (task_id,))
        return {"intents": [_view(row) for row in rows]}
