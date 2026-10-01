"""Bounded, checkpointed V5 research ticks; execution remains with authorized runners."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/api/v1/continuous-research", tags=["V5 Continuous Research"])


def _core():
    import final_core
    return final_core


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _load(value: str | None, default: object) -> object:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


class CampaignPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    min_interval_minutes: int = Field(default=60, ge=1, le=10080)
    idle_only: bool = True
    local_first: bool = True
    cloud_escalation: bool = False
    daily_budget_micros: int = Field(default=0, ge=0)
    max_tasks_per_tick: int = Field(default=4, ge=1, le=50)
    max_cloud_tasks_per_tick: int = Field(default=0, ge=0, le=50)
    sensitive_local_only: bool = True


class TickRequest(BaseModel):
    trigger: Literal[
        "manual", "scheduled", "new_commit", "new_advisory", "new_observation",
        "runner_recovered", "verification_failed", "user_material",
    ] = "manual"


def _state(row: sqlite3.Row | None) -> dict:
    if not row:
        return {"configured": False, "policy": CampaignPolicy().model_dump(), "last_checkpoint_id": None,
                "last_tick_at": None, "next_tick_at": None, "last_result": None}
    return {"configured": True, "policy": _load(row["policy_json"], {}),
            "last_checkpoint_id": row["last_checkpoint_id"], "last_tick_at": row["last_tick_at"],
            "next_tick_at": row["next_tick_at"], "last_result": _load(row["last_result_json"], None)}


def _campaign_scope(db: sqlite3.Connection, campaign_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT c.*,e.status AS engagement_status,e.mode,e.current_scope_snapshot_id,"
        "e.current_policy_id,s.confirmed_at FROM research_campaigns c "
        "JOIN engagements_v2 e ON e.id=c.engagement_id "
        "LEFT JOIN scope_snapshots s ON s.id=e.current_scope_snapshot_id "
        "WHERE c.id=?", (campaign_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "research campaign not found")
    return row


@router.get("/campaigns/{campaign_id}")
def get_state(campaign_id: str):
    with _core().connect() as db:
        _campaign_scope(db, campaign_id)
        row = db.execute("SELECT * FROM continuous_research_state WHERE campaign_id=?", (campaign_id,)).fetchone()
    return {"campaign_id": campaign_id, **_state(row)}


@router.put("/campaigns/{campaign_id}")
def configure(campaign_id: str, policy: CampaignPolicy):
    if not policy.local_first:
        raise HTTPException(422, "continuous research requires local-first routing")
    if policy.max_cloud_tasks_per_tick > policy.max_tasks_per_tick:
        raise HTTPException(422, "cloud task cap exceeds total task cap")
    if policy.cloud_escalation:
        raise HTTPException(422, "cloud escalation is not yet available for continuous research")
    now = _now()
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = _campaign_scope(db, campaign_id)
        if policy.enabled and (campaign["status"] != "active" or campaign["engagement_status"] == "archived"
                               or not campaign["confirmed_at"] or not campaign["current_policy_id"]):
            raise HTTPException(409, "active campaign and confirmed current scope/policy required")
        prior = db.execute("SELECT enabled,last_checkpoint_id,last_tick_at,next_tick_at,last_result_json FROM continuous_research_state WHERE campaign_id=?", (campaign_id,)).fetchone()
        next_at = (prior["next_tick_at"] if prior and prior["enabled"] and prior["next_tick_at"]
                   else now.isoformat()) if policy.enabled else None
        db.execute(
            "INSERT INTO continuous_research_state VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(campaign_id) DO UPDATE SET enabled=excluded.enabled,policy_json=excluded.policy_json,"
            "next_tick_at=excluded.next_tick_at",
            (campaign_id, int(policy.enabled), _dump(policy.model_dump()),
             prior["last_checkpoint_id"] if prior else None, prior["last_tick_at"] if prior else None,
             next_at, prior["last_result_json"] if prior else "null"),
        )
        if not policy.enabled:
            for task in db.execute(
                "SELECT id,context_capsule_json FROM agent_tasks WHERE campaign_id=? "
                "AND status IN ('queued','paused','leased','running')", (campaign_id,),
            ).fetchall():
                if _load(task["context_capsule_json"], {}).get("continuous_research"):
                    db.execute(
                        "UPDATE agent_tasks SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,"
                        "updated_at=? WHERE id=?", (now.isoformat(), task["id"]),
                    )
    return get_state(campaign_id)


def _graph_snapshot(db: sqlite3.Connection, campaign_id: str) -> tuple[str, dict]:
    nodes = db.execute(
        "SELECT id,node_type,status,updated_at,created_at FROM research_nodes WHERE campaign_id=? ORDER BY id",
        (campaign_id,),
    ).fetchall()
    edges = db.execute(
        "SELECT id,source_id,target_id,relation_type,created_at FROM research_edges WHERE campaign_id=? ORDER BY id",
        (campaign_id,),
    ).fetchall()
    digest = hashlib.sha256(_dump({"nodes": [tuple(row) for row in nodes],
                                   "edges": [tuple(row) for row in edges]}).encode()).hexdigest()
    counts = {kind: sum(row["node_type"] == kind for row in nodes)
              for kind in ("observation", "claim", "evidence", "counterevidence", "canonical_result")}
    return digest, {"counts": counts, "nodes": nodes, "edges": edges}


def _enqueue(db: sqlite3.Connection, campaign_id: str, kind: str, ref: str, scope_id: str,
             policy_id: str, now: str, fingerprint: str | None = None) -> bool:
    key = hashlib.sha256(f"continuous\0{campaign_id}\0{scope_id}\0{policy_id}\0{kind}\0{fingerprint or ref}".encode()).hexdigest()
    if db.execute("SELECT 1 FROM agent_tasks WHERE idempotency_key=?", (key,)).fetchone():
        return False
    task_id = _uid("atask")
    objective = {"triage_observation": "Triage a new observation and identify evidence gaps",
                 "challenge_claim": "Challenge a claim against newly conflicting evidence",
                 "verification_review": "Prepare independent verification request for a claim"}[kind]
    db.execute(
        "INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, campaign_id, None, None, kind, objective,
         _dump({"source_node_id": ref, "scope_snapshot_id": scope_id, "policy_id": policy_id,
                "continuous_research": True, "external_target_actions_authorized": False}),
         "[]", _dump({"labels": {"location": "local"}}),
         _dump({"max_tokens": 8000, "max_cost_micros": 0}), 0, "queued", 0, 2, key,
         None, None, None, None, None, now, now),
    )
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
        ("continuous_research", campaign_id, task_id, "task.enqueued", _dump({"kind": kind, "source_node_id": ref}), now),
    )
    return True


def tick(campaign_id: str, trigger: str = "manual") -> dict:
    now_dt = _now()
    now = now_dt.isoformat()
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        campaign = _campaign_scope(db, campaign_id)
        state = db.execute("SELECT * FROM continuous_research_state WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not state or not state["enabled"]:
            raise HTTPException(409, "continuous research is not enabled")
        policy = CampaignPolicy.model_validate(_load(state["policy_json"], {}))
        if campaign["status"] != "active" or campaign["engagement_status"] == "archived" or not campaign["confirmed_at"]:
            raise HTTPException(409, "campaign or confirmed scope is no longer active")
        if not db.execute("SELECT 1 FROM execution_policies WHERE id=? AND engagement_id=?",
                          (campaign["current_policy_id"], campaign["engagement_id"])).fetchone():
            raise HTTPException(409, "current execution policy is missing")
        if state["next_tick_at"] and now_dt < datetime.fromisoformat(state["next_tick_at"]):
            return {"campaign_id": campaign_id, "status": "not_due", "next_tick_at": state["next_tick_at"]}
        stale_tasks = db.execute(
            "SELECT id,context_capsule_json FROM agent_tasks WHERE campaign_id=? "
            "AND status IN ('queued','paused','leased','running')", (campaign_id,),
        ).fetchall()
        for task in stale_tasks:
            capsule = _load(task["context_capsule_json"], {})
            if capsule.get("continuous_research") and (
                capsule.get("scope_snapshot_id") != campaign["current_scope_snapshot_id"]
                or capsule.get("policy_id") != campaign["current_policy_id"]
            ):
                db.execute(
                    "UPDATE agent_tasks SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL,"
                    "updated_at=? WHERE id=?", (now, task["id"]),
                )
                db.execute(
                    "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
                    "VALUES(?,?,?,?,?,?)", ("continuous_research", campaign_id, task["id"],
                                       "task.scope_invalidated", "{}", now),
                )
        if policy.idle_only and db.execute(
            "SELECT 1 FROM agent_tasks WHERE campaign_id=? AND status IN ('queued','leased','running') LIMIT 1",
            (campaign_id,),
        ).fetchone():
            db.execute("UPDATE continuous_research_state SET next_tick_at=? WHERE campaign_id=?",
                       ((now_dt + timedelta(minutes=policy.min_interval_minutes)).isoformat(), campaign_id))
            return {"campaign_id": campaign_id, "status": "busy"}
        spent = db.execute(
            "SELECT COALESCE(SUM(cost_micros),0) FROM runtime_usage WHERE campaign_id=? AND created_at>=?",
            (campaign_id, now_dt.date().isoformat()),
        ).fetchone()[0]
        if policy.daily_budget_micros and spent >= policy.daily_budget_micros:
            db.execute("UPDATE continuous_research_state SET next_tick_at=? WHERE campaign_id=?",
                       ((now_dt + timedelta(minutes=policy.min_interval_minutes)).isoformat(), campaign_id))
            return {"campaign_id": campaign_id, "status": "budget_exhausted", "spent_micros": spent}
        digest, snapshot = _graph_snapshot(db, campaign_id)
        previous = db.execute("SELECT * FROM research_checkpoints WHERE id=? AND campaign_id=?",
                              (state["last_checkpoint_id"], campaign_id)).fetchone() if state["last_checkpoint_id"] else None
        prior = _load(previous["summary_json"], {}) if previous else {}
        changed = not previous or previous["graph_digest"] != digest
        tasks = []
        candidates = []
        nodes = snapshot["nodes"]
        node_by_id = {row["id"]: row for row in nodes}
        # Revisit unqueued gaps on later ticks: a per-tick cap must not drop backlog.
        for row in nodes:
            if row["node_type"] == "observation":
                candidates.append(("triage_observation", row["id"], row["id"]))
        for edge in snapshot["edges"]:
            if edge["relation_type"] == "contradicts":
                target = node_by_id.get(edge["target_id"])
                if target and target["node_type"] == "claim":
                    candidates.append(("challenge_claim", target["id"], edge["id"]))
        for row in nodes:
            if row["node_type"] != "claim" or row["status"] in {"retired", "archived", "invalidated", "legacy_verified_claim"}:
                continue
            has_evidence = any(edge["target_id"] == row["id"] and edge["relation_type"] == "supports"
                               for edge in snapshot["edges"])
            has_receipt = db.execute(
                "SELECT 1 FROM verification_receipts_v5 WHERE campaign_id=? AND claim_node_id=? LIMIT 1",
                (campaign_id, row["id"]),
            ).fetchone()
            if has_evidence and not has_receipt:
                candidates.append(("verification_review", row["id"], row["id"]))
        for kind, ref, fingerprint in candidates:
            if len(tasks) >= policy.max_tasks_per_tick:
                break
            if _enqueue(db, campaign_id, kind, ref, campaign["current_scope_snapshot_id"],
                        campaign["current_policy_id"], now, fingerprint):
                tasks.append({"kind": kind, "source_node_id": ref})
        counts = snapshot["counts"]
        observation_ids = {row["id"] for row in nodes if row["node_type"] == "observation"}
        claim_ids = {row["id"] for row in nodes if row["node_type"] == "claim"}
        claim_statuses = {row["id"]: row["status"] for row in nodes if row["node_type"] == "claim"}
        previous_observation_ids = set(prior.get("observation_ids", []))
        previous_claim_ids = set(prior.get("claim_ids", []))
        previous_claim_statuses = prior.get("claim_statuses", {})
        previous_usage = int(prior.get("usage_cost_micros", 0))
        total_usage = db.execute("SELECT COALESCE(SUM(cost_micros),0) FROM runtime_usage WHERE campaign_id=?",
                                 (campaign_id,)).fetchone()[0]
        result = {"campaign_id": campaign_id, "status": "checkpointed", "trigger": trigger,
                  "new_observations": len(observation_ids - previous_observation_ids),
                  "new_claims": len(claim_ids - previous_claim_ids),
                  "challenged_claims": sum(task["kind"] == "challenge_claim" for task in tasks),
                  "retired_claims": sum(status in {"retired", "archived", "invalidated"}
                                        and previous_claim_statuses.get(ref) not in {"retired", "archived", "invalidated"}
                                        for ref, status in claim_statuses.items()),
                  "verification_jobs": sum(task["kind"] == "verification_review" for task in tasks),
                  "cloud_escalations": 0, "cost_usd": max(0, total_usage - previous_usage) / 1_000_000,
                  "tasks_enqueued": tasks, "graph_changed": changed}
        checkpoint_id = _uid("rcheckpoint")
        db.execute("INSERT INTO research_checkpoints VALUES(?,?,?,?,?,?)",
                   (checkpoint_id, campaign_id, None, digest,
                    _dump({"counts": counts, "observation_ids": sorted(observation_ids),
                           "claim_ids": sorted(claim_ids), "claim_statuses": claim_statuses,
                           "usage_cost_micros": total_usage, "trigger": trigger, "tasks": tasks,
                           "scope_snapshot_id": campaign["current_scope_snapshot_id"],
                           "policy_id": campaign["current_policy_id"]}), now))
        result["checkpoint_id"] = checkpoint_id
        db.execute(
            "UPDATE continuous_research_state SET last_checkpoint_id=?,last_tick_at=?,next_tick_at=?,last_result_json=? "
            "WHERE campaign_id=?",
            (checkpoint_id, now, (now_dt + timedelta(minutes=policy.min_interval_minutes)).isoformat(),
             _dump(result), campaign_id),
        )
        db.execute(
            "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            ("continuous_research", campaign_id, checkpoint_id, "tick.checkpointed",
             _dump({"trigger": trigger, "task_count": len(tasks), "graph_changed": changed}), now),
        )
    return result


@router.post("/campaigns/{campaign_id}/tick")
def tick_campaign(campaign_id: str, body: TickRequest):
    return tick(campaign_id, body.trigger)


def run_due(limit: int = 10) -> list[dict]:
    with _core().connect() as db:
        due = [row["campaign_id"] for row in db.execute(
            "SELECT campaign_id FROM continuous_research_state WHERE enabled=1 AND next_tick_at<=? "
            "ORDER BY next_tick_at,campaign_id LIMIT ?", (_now().isoformat(), limit),
        )]
    outcomes = []
    for campaign_id in due:
        try:
            outcomes.append(tick(campaign_id, "scheduled"))
        except HTTPException as error:
            outcomes.append({"campaign_id": campaign_id, "status": "blocked", "reason": str(error.detail)})
    return outcomes


@router.post("/due/tick")
def tick_due():
    return {"processed": run_due()}
